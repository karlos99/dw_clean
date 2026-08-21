from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core import signing
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.urls import reverse
import csv
import json

from studata.models import CurrentStudent, School as StudataSchool, TeacherAccess
from landing.utils import get_project_config
from .models import (
    Accommodation,
    AccommodationOption,
    Assessment,
    AssessmentCriteria,
    DesiExportConfiguration,
    StudentAccommodation,
    StudentAssessmentCompletion,
)


BASE_EXPORT_COLUMNS = [
    ('student_name', 'Student Name', 'student'),
    ('student_id', 'Local Student ID', 'student'),
    ('ssid', 'SSID', 'student'),
    ('stu_annual_key', 'Student Annual Key', 'student'),
    ('grade', 'Grade', 'student'),
    ('school_name', 'School', 'student'),
    ('school_year', 'School Year', 'student'),
    ('assessment_name', 'Assessment Name', 'support'),
    ('assessment_subject', 'Assessment Subject', 'support'),
    ('assessment_school_year', 'Assessment School Year', 'support'),
    ('assessment_completed', 'Assessment Completed', 'support'),
]

EXPORT_ENDPOINT_SIGNING_SALT = "desi-export-endpoint"
EXPORT_ENDPOINT_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _is_project_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role and user.active_role.name == 'Project Admin':
        return True
    return user.roles.filter(name='Project Admin').exists() if hasattr(user, 'roles') else False


def _is_teacher(user):
    """Return whether the user's active role is Teacher."""
    if not user.is_authenticated:
        return False
    active_role = getattr(user, 'active_role', None)
    if active_role:
        return active_role.name == 'Teacher'
    return user.roles.filter(name='Teacher').exists() if hasattr(user, 'roles') else False


def _get_student_field_options():
    fields_to_pull = [
        'grade', 'gender', 'race', 'ell', 'sed', 'foster', 
        'homeless', 'sped', 'gate', 'in_504', 'dli'
    ]
    student_field_options = {}
    for field in fields_to_pull:
        vals = CurrentStudent.objects.values_list(field, flat=True).distinct().order_by()
        cleaned_vals = sorted(list(set(v for v in vals if v is not None and str(v).strip() != "")))
        student_field_options[field] = cleaned_vals
    import json
    return json.dumps(student_field_options)


def get_visible_students(user, active_school=None):
    """
    Enforces role-based and school-context data visibility constraints.
    - Teachers can only see students in their schedule classes.
    - Admins see students within their permitted schools (or all if unrestricted).
    """
    # 1. Row-level teacher filter
    if hasattr(user, 'get_accessible_student_keys'):
        accessible_keys = user.get_accessible_student_keys()
        if accessible_keys is not None:
            # If teacher has no students, return empty
            if not accessible_keys:
                return CurrentStudent.objects.none()
            return CurrentStudent.objects.filter(stu_annual_key__in=accessible_keys)

    # 2. School-context filter (if unrestricted role)
    if active_school:
        return CurrentStudent.objects.filter(school_state_id=active_school.state_id)
        
    user_schools = getattr(user, 'schools', None)
    if isinstance(user_schools, list) and user_schools:
        return CurrentStudent.objects.filter(school_state_id__in=user_schools)

    # Superusers / Admins with no school constraints see everything
    return CurrentStudent.objects.all()


def check_criteria(criteria_list, student):
    """
    Dynamically evaluates a list of AssessmentCriteria against a student's current demographics.
    Returns (is_eligible: bool, failed_reasons: list of str).
    """
    failed_reasons = []
    for criterion in criteria_list:
        field_name = criterion.field_name
        
        # Resolve value from student attributes or add_data dict
        if hasattr(student, field_name):
            student_value = getattr(student, field_name)
        elif student.add_data and isinstance(student.add_data, dict) and field_name in student.add_data:
            student_value = student.add_data.get(field_name)
        else:
            student_value = None

        # Normalize values to string for case-insensitive comparison
        stu_val_str = str(student_value).strip().lower() if student_value is not None else ""
        crit_val_str = criterion.value.strip().lower()
        crit_list_vals = [v.strip().lower() for v in criterion.value.split(',') if v.strip()]

        match = False
        op = criterion.operator
        if op == 'is':
            match = (stu_val_str == crit_val_str)
        elif op == 'is_not':
            match = (stu_val_str != crit_val_str)
        elif op == 'in':
            match = (stu_val_str in crit_list_vals)
        elif op == 'not_in':
            match = (stu_val_str not in crit_list_vals)

        if not match:
            # Generate descriptive error message
            display_field = criterion.get_field_name_display()
            display_operator = criterion.get_operator_display()
            current_display = f"'{student_value}'" if student_value is not None else "(Not Set)"
            failed_reasons.append(
                f"{display_field} must be {display_operator} '{criterion.value}' (current value: {current_display})"
            )
            
    return len(failed_reasons) == 0, failed_reasons


def _parse_dependency_fields(post_data):
    dependency_support_id = post_data.get('depends_on_accommodation', '').strip()
    dependency_option_id = post_data.get('depends_on_option', '').strip()

    dep_support_id = int(dependency_support_id) if dependency_support_id.isdigit() else None
    dep_option_id = int(dependency_option_id) if dependency_option_id.isdigit() else None
    return dep_support_id, dep_option_id


def _parse_dependency_assessments(post_data):
    return {int(x) for x in post_data.getlist('dependency_assessments') if x.isdigit()}


def _split_option_values(raw_value):
    """Turn one option field into one or more trimmed comma-separated values."""
    return [value.strip() for value in (raw_value or '').split(',') if value.strip()]


def _validate_dependency(accommodation, dep_support_id, dep_option_id):
    if dep_support_id and dep_option_id:
        try:
            parent_support = Accommodation.objects.get(id=dep_support_id)
        except Accommodation.DoesNotExist:
            return None, None, "Selected dependency support was not found."

        try:
            parent_option = AccommodationOption.objects.get(id=dep_option_id)
        except AccommodationOption.DoesNotExist:
            return None, None, "Selected dependency option was not found."

        if parent_option.accommodation_id != parent_support.id:
            return None, None, "Dependency option must belong to the selected dependency support."

        if accommodation and parent_support.id == accommodation.id:
            return None, None, "A designated support cannot depend on itself."

        return parent_support, parent_option, None

    if dep_support_id or dep_option_id:
        return None, None, "Select both dependency support and dependency option, or leave both blank."

    return None, None, None


def _apply_dependency_rules(selected_option_ids, option_to_accommodation, support_contexts, dependency_rules):
    """
    Returns filtered option IDs where supports hidden in all relevant contexts are removed.
    """
    selected = set(selected_option_ids)

    while True:
        hidden_everywhere_support_ids = set()
        for support_id, rule in dependency_rules.items():
            trigger_option_id = rule['trigger_option_id']
            if trigger_option_id not in selected:
                continue

            support_ast_ids = support_contexts.get(support_id, set())
            if not support_ast_ids:
                continue

            scoped_ast_ids = rule['assessment_ids']
            if not scoped_ast_ids:
                affected_ast_ids = support_ast_ids
            else:
                affected_ast_ids = support_ast_ids.intersection(scoped_ast_ids)

            if affected_ast_ids and affected_ast_ids == support_ast_ids:
                hidden_everywhere_support_ids.add(support_id)

        next_selected = {
            opt_id for opt_id in selected
            if option_to_accommodation.get(opt_id) not in hidden_everywhere_support_ids
        }
        if next_selected == selected:
            return next_selected
        selected = next_selected


def _get_student_list_context(request):
    try:
        default_year = get_project_config().default_school_year
        if not default_year:
            latest_stu = CurrentStudent.objects.order_by('-school_year').first()
            default_year = latest_stu.school_year if latest_stu else "2025-2026"
    except Exception:
        default_year = "2025-2026"
    
    school_year = request.GET.get('school_year', default_year)
    search_query = request.GET.get('search', '').strip()
    grade_filter = request.GET.get('grade', '').strip()
    teacher_filter = request.GET.get('teacher', '').strip()
    
    active_school = getattr(request.user, 'active_school', None)
    school_filter_id = request.GET.get('school', '')
    
    if school_filter_id and not active_school:
        user_schools = getattr(request.user, 'schools', None)
        if not user_schools or school_filter_id in user_schools:
            active_school = (
                StudataSchool.objects
                .filter(state_id=school_filter_id)
                .order_by('school_name', 'dw_schoolkey')
                .first()
            )

    students_qs = get_visible_students(request.user, active_school=active_school)
    students_qs = students_qs.filter(school_year=school_year)

    # Build teacher choices from the students visible in the selected school/year,
    # then optionally narrow the student list to that teacher's students.
    teacher_names = []
    teacher_options_by_filter = {}
    try:
        teacher_access_qs = TeacherAccess.objects.filter(
            school_year=school_year,
            stu_annual_key__in=students_qs.values('stu_annual_key'),
        )
        student_details = {
            row['stu_annual_key']: row
            for row in students_qs.values('stu_annual_key', 'school_state_id', 'grade')
        }
        for access in teacher_access_qs.values('staff_name', 'stu_annual_key'):
            name = (access['staff_name'] or '').strip()
            student_detail = student_details.get(access['stu_annual_key'])
            if not name or not student_detail:
                continue
            school_key = student_detail['school_state_id'] or ''
            grade_key = student_detail['grade'] or ''
            for key in ('::', f'{school_key}::', f'::{grade_key}', f'{school_key}::{grade_key}'):
                teacher_options_by_filter.setdefault(key, set()).add(name)

        teacher_options_by_filter = {
            key: sorted(names, key=str.casefold)
            for key, names in teacher_options_by_filter.items()
        }
        current_school = school_filter_id or getattr(active_school, 'state_id', '') or ''
        teacher_names = teacher_options_by_filter.get(
            f'{current_school}::{grade_filter}',
            teacher_options_by_filter.get(f'{current_school}::', []) if current_school
            else teacher_options_by_filter.get(f'::{grade_filter}', teacher_options_by_filter.get('::', [])),
        )
        if teacher_filter:
            students_qs = students_qs.filter(
                stu_annual_key__in=teacher_access_qs.filter(
                    staff_name__iexact=teacher_filter
                ).values('stu_annual_key')
            )
    except Exception:
        # Some deployments do not have the teacher-access view available.
        teacher_names = []
        if teacher_filter:
            students_qs = students_qs.none()

    if search_query:
        students_qs = students_qs.filter(
            Q(student_name__icontains=search_query) |
            Q(ssid__icontains=search_query) |
            Q(student_id__icontains=search_query)
        )

    if grade_filter:
        students_qs = students_qs.filter(grade=grade_filter)

    paginator = Paginator(students_qs.order_by('student_name'), 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    student_keys = [s.stu_annual_key for s in page_obj]
    
    global_acc_map = {}
    if student_keys:
        acc_records = StudentAccommodation.objects.filter(student_annual_key__in=student_keys).prefetch_related('options__accommodation')
        for rec in acc_records:
            global_acc_map[rec.student_annual_key] = list(rec.options.all())

    completions_map = {}
    if student_keys:
        completions = StudentAssessmentCompletion.objects.filter(
            student_annual_key__in=student_keys,
            is_completed=True
        )
        for c in completions:
            if c.student_annual_key not in completions_map:
                completions_map[c.student_annual_key] = set()
            completions_map[c.student_annual_key].add(c.assessment_id)

    all_year_assessments = list(Assessment.objects.filter(school_year=school_year, is_active=True).prefetch_related('criteria'))

    for student in page_obj:
        eligible_ids = []
        for ast in all_year_assessments:
            eligible, _ = check_criteria(ast.criteria.all(), student)
            if eligible:
                eligible_ids.append(ast.id)
        
        student.eligible_count = len(eligible_ids)
        student_completed_ids = completions_map.get(student.stu_annual_key, set())
        student.completed_count = len(student_completed_ids.intersection(set(eligible_ids)))
        student.assessment_statuses = [
            {
                'assessment': ast,
                'is_completed': ast.id in student_completed_ids,
            }
            for ast in all_year_assessments
            if ast.id in eligible_ids
        ]
        student.global_accommodations = global_acc_map.get(student.stu_annual_key, [])

    grade_base = CurrentStudent.objects.filter(school_year=school_year)
    if school_filter_id:
        grade_base = grade_base.filter(school_state_id=school_filter_id)
    elif active_school:
        grade_base = grade_base.filter(school_state_id=active_school.state_id)
    grades = grade_base.values_list('grade', flat=True).distinct().order_by('grade')
    grades = [g for g in grades if g]

    available_schools = getattr(request.user, 'available_schools', [])
    if not available_schools and request.user.is_superuser:
        available_schools = StudataSchool.objects.all().order_by('school_name')

    if hasattr(available_schools, 'exists'):
        has_multiple_schools = available_schools.count() > 1
    else:
        has_multiple_schools = len(available_schools) > 1

    grade_options_by_school = {}
    school_ids = [s.state_id for s in available_schools if getattr(s, 'state_id', None)] if not hasattr(available_schools, 'values_list') else list(available_schools.values_list('state_id', flat=True))
    for school_id in school_ids:
        grade_options_by_school[school_id] = list(
            CurrentStudent.objects.filter(school_year=school_year, school_state_id=school_id)
            .exclude(grade__isnull=True).exclude(grade='')
            .values_list('grade', flat=True).distinct().order_by('grade')
        )

    school_names = {}
    school_rows = available_schools if hasattr(available_schools, '__iter__') else available_schools
    for school in school_rows:
        school_names[school.state_id] = school.short_name or school.school_name
    for student in page_obj:
        student.school_display = school_names.get(student.school_state_id) or student.school or student.school_state_id

    return {
        'page_obj': page_obj,
        'search_query': search_query,
        'grade_filter': grade_filter,
        'teacher_filter': teacher_filter,
        'teachers': teacher_names,
        'teacher_options_by_filter_json': json.dumps(teacher_options_by_filter),
        'school_filter': school_filter_id,
        'school_year': school_year,
        'grades': grades,
        'schools': available_schools,
        'active_school': active_school,
        'is_project_admin': _is_project_admin(request.user),
        'show_teacher_filter': not _is_teacher(request.user),
        'students_qs_raw': students_qs,
        'has_multiple_schools': has_multiple_schools,
        'grade_options_by_school_json': json.dumps(grade_options_by_school),
    }


@login_required
def student_list(request):
    """Lists students filtered by search, school, and grade, with active accommodation summary and test completion counts."""
    context = _get_student_list_context(request)
    # Automatically redirect to first student in list if available
    first_student = context['students_qs_raw'].order_by('student_name').first()
    if first_student:
        get_params = request.GET.urlencode()
        url = reverse('desi:assign_accommodations', args=[first_student.stu_annual_key])
        if get_params:
            url = f"{url}?{get_params}"
        return redirect(url)
    return render(request, 'desi/student_list.html', context)


@login_required
def admin_dashboard(request):
    """Admin configuration dashboard for assessments and accommodations."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied. You must be a Project Admin.")
        return redirect('landing:index')

    try:
        default_year = get_project_config().default_school_year
    except Exception:
        default_year = "2025-2026"

    assessments = Assessment.objects.all().prefetch_related('criteria').order_by('-school_year', 'name')
    accommodations = Accommodation.objects.all().prefetch_related('options')

    context = {
        'assessments': assessments,
        'accommodations': accommodations,
        'school_year': default_year,
    }
    return render(request, 'desi/admin_dashboard.html', context)


def _build_export_columns(selected_assessments):
    columns = []
    for idx, (key, label, group) in enumerate(BASE_EXPORT_COLUMNS, start=1):
        columns.append({
            'key': key,
            'label': label,
            'group': group,
            'default_order': idx,
            'support_id': None,
        })

    support_map = {}
    for ast in selected_assessments:
        for opt in ast.supported_options.all().select_related('accommodation'):
            support = opt.accommodation
            support_map[support.id] = support

    next_order = len(columns) + 1
    for support in sorted(support_map.values(), key=lambda s: (s.order, s.category, s.name)):
        label = support.name
        if support.code:
            label = f"{support.name} ({support.code})"
        columns.append({
            'key': f"support__{support.id}",
            'label': label,
            'group': 'support',
            'default_order': next_order,
            'support_id': support.id,
        })
        next_order += 1

    return columns


def _parse_export_column_config(post_data, available_columns):
    configured = []
    for col in available_columns:
        key = col['key']
        default_label = col['label']
        group = col['group']
        include = post_data.get(f'include__{key}') == 'on'
        header = post_data.get(f'header__{key}', '').strip() or default_label
        raw_order = post_data.get(f'order__{key}', '').strip()
        try:
            order = int(raw_order) if raw_order else col['default_order']
        except ValueError:
            order = col['default_order']
        configured.append({
            'key': key,
            'label': default_label,
            'group': group,
            'include': include,
            'header': header,
            'order': order,
            'support_id': col.get('support_id'),
        })

    included = [c for c in configured if c['include']]
    order_index = {c['key']: idx for idx, c in enumerate(available_columns)}
    included.sort(key=lambda c: (c['order'], order_index.get(c['key'], 9999)))
    return configured, included


def _build_column_config_from_saved_config(available_columns, saved_column_config):
    saved_map = {c.get('key'): c for c in saved_column_config if isinstance(c, dict) and c.get('key')}
    default_include_keys = {'student_name', 'student_id', 'grade', 'school_name', 'assessment_name', 'assessment_completed'}
    configured = []
    for col in available_columns:
        key = col['key']
        saved = saved_map.get(key, {})
        configured.append({
            'key': key,
            'label': col['label'],
            'group': col['group'],
            'include': saved.get('include', key in default_include_keys or key.startswith('support__')),
            'header': saved.get('header', col['label']),
            'order': saved.get('order', col['default_order']),
            'support_id': col.get('support_id'),
        })
    return configured


def _build_export_config_payload(school_year, selected_assessment_ids, only_with_supports, support_value_field, column_config):
    return {
        'school_year': school_year,
        'assessment_ids': [int(x) for x in selected_assessment_ids if str(x).isdigit()],
        'only_with_supports': bool(only_with_supports),
        'support_value_field': support_value_field,
        'column_config': column_config,
    }


def _generate_export_endpoint_auth_token(user):
    return signing.dumps(
        {"uid": user.id, "purpose": "desi_export"},
        salt=EXPORT_ENDPOINT_SIGNING_SALT,
    )


def _resolve_export_endpoint_user(auth_token):
    if not auth_token:
        return None
    try:
        payload = signing.loads(
            auth_token,
            salt=EXPORT_ENDPOINT_SIGNING_SALT,
            max_age=EXPORT_ENDPOINT_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return None

    if payload.get("purpose") != "desi_export":
        return None

    uid = payload.get("uid")
    if not uid:
        return None

    User = get_user_model()
    return User.objects.filter(id=uid, is_active=True).first()


def _build_export_csv_response(
    user,
    school_year,
    selected_assessments,
    selected_assessment_ids,
    available_columns,
    included_columns,
    only_with_supports,
    support_value_field,
):
    students = list(get_visible_students(user).filter(school_year=school_year).order_by('student_name'))
    student_keys = [s.stu_annual_key for s in students]

    student_option_map = {}
    if student_keys:
        student_acc_records = StudentAccommodation.objects.filter(student_annual_key__in=student_keys).prefetch_related('options__accommodation')
        for rec in student_acc_records:
            student_option_map[rec.student_annual_key] = list(rec.options.all())

    completion_map = {}
    if student_keys and selected_assessment_ids:
        completions = StudentAssessmentCompletion.objects.filter(
            student_annual_key__in=student_keys,
            assessment_id__in=selected_assessment_ids,
            is_completed=True,
        )
        for comp in completions:
            completion_map[(comp.student_annual_key, comp.assessment_id)] = True

    assessment_supports = {}
    for ast in selected_assessments:
        supports = {}
        for opt in ast.supported_options.all().select_related('accommodation'):
            supports.setdefault(opt.accommodation_id, {'support': opt.accommodation, 'options': []})
            supports[opt.accommodation_id]['options'].append(opt)
        assessment_supports[ast.id] = supports

    rows = []
    for student in students:
        selected_options = student_option_map.get(student.stu_annual_key, [])
        selected_option_ids = {o.id for o in selected_options}

        for ast in selected_assessments:
            base_values = {
                'student_name': student.student_name or '',
                'student_id': student.student_id or '',
                'ssid': student.ssid or '',
                'stu_annual_key': student.stu_annual_key or '',
                'grade': student.grade or '',
                'school_name': getattr(student, 'school', None) or student.school_state_id or '',
                'school_year': student.school_year or '',
                'assessment_name': ast.name or '',
                'assessment_subject': ast.subject or '',
                'assessment_school_year': ast.school_year or '',
                'assessment_completed': 'Yes' if completion_map.get((student.stu_annual_key, ast.id), False) else 'No',
            }
            supports = assessment_supports.get(ast.id, {})
            row = dict(base_values)
            has_any_support_selected = False
            for col in available_columns:
                support_id = col.get('support_id')
                if not support_id:
                    continue
                support_entry = supports.get(support_id)
                if not support_entry:
                    row[col['key']] = ''
                    continue
                selected_opt = next((o for o in support_entry['options'] if o.id in selected_option_ids), None)
                if not selected_opt:
                    row[col['key']] = ''
                else:
                    if support_value_field == 'code':
                        row[col['key']] = selected_opt.code or ''
                    else:
                        row[col['key']] = selected_opt.name or ''
                    has_any_support_selected = True
            if only_with_supports and not has_any_support_selected:
                continue
            rows.append(row)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="desi_export_{school_year}.csv"'
    writer = csv.writer(response)
    writer.writerow([c['header'] for c in included_columns])
    for row in rows:
        writer.writerow([row.get(c['key'], '') for c in included_columns])
    return response


@login_required
def export_data(request):
    """Project-admin export tool for DESI student/support data."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied. You must be a Project Admin.")
        return redirect('landing:index')

    request_data = request.POST if request.method == 'POST' else request.GET
    school_year = request_data.get('school_year', '').strip()
    if not school_year:
        try:
            school_year = get_project_config().default_school_year or "2025-2026"
        except Exception:
            school_year = "2025-2026"

    saved_configs = DesiExportConfiguration.objects.filter(owner=request.user).order_by('-updated_at')
    selected_config = None
    config_id = request.GET.get('config_id', '').strip()
    if config_id.isdigit():
        selected_config = saved_configs.filter(id=int(config_id)).first()

    if selected_config:
        saved_config = selected_config.config
        school_year = saved_config.get('school_year', school_year)
        support_value_field = saved_config.get('support_value_field', 'name').strip().lower()
        if support_value_field not in {'name', 'code'}:
            support_value_field = 'name'
        only_with_supports = bool(saved_config.get('only_with_supports', False))
        selected_assessment_ids = {int(x) for x in saved_config.get('assessment_ids', []) if str(x).isdigit()}
        selected_config_id = selected_config.id
    else:
        support_value_field = request_data.get('support_value_field', 'name').strip().lower()
        if support_value_field not in {'name', 'code'}:
            support_value_field = 'name'
        only_with_supports = request.method == 'POST' and request_data.get('only_with_supports') == 'on'
        selected_assessment_ids = {
            int(x) for x in request_data.getlist('assessment_ids') if str(x).isdigit()
        }
        selected_config_id = None

    assessments = list(
        Assessment.objects.filter(school_year=school_year, is_active=True)
        .prefetch_related('supported_options__accommodation')
        .order_by('name')
    )
    if selected_config:
        selected_assessment_ids = {int(x) for x in selected_config.config.get('assessment_ids', []) if str(x).isdigit()}
    elif request.method == 'GET' and not selected_assessment_ids:
        selected_assessment_ids = {a.id for a in assessments}
    if selected_assessment_ids:
        assessments = [a for a in assessments if a.id in selected_assessment_ids]
    selected_assessments = [a for a in assessments if a.id in selected_assessment_ids]
    available_columns = _build_export_columns(selected_assessments)

    if request.method == 'POST':
        column_config, included_columns = _parse_export_column_config(request.POST, available_columns)
        if request.POST.get('save_configuration') == '1' or request.POST.get('save_configuration_name', '').strip():
            config_name = request.POST.get('save_configuration_name', '').strip()
            if not config_name:
                messages.error(request, "Enter a name to save this export configuration.")
            else:
                payload = _build_export_config_payload(
                    school_year,
                    selected_assessment_ids,
                    only_with_supports,
                    support_value_field,
                    column_config,
                )
                DesiExportConfiguration.objects.create(
                    owner=request.user,
                    name=config_name,
                    description=request.POST.get('save_configuration_description', '').strip(),
                    config=payload,
                )
                messages.success(request, f"Export configuration '{config_name}' saved.")
                saved_configs = DesiExportConfiguration.objects.filter(owner=request.user).order_by('-updated_at')
                selected_config = saved_configs.filter(name=config_name).first()
                selected_config_id = selected_config.id if selected_config else None
            context = {
                'school_year': school_year,
                'assessments': assessments,
                'selected_assessment_ids': selected_assessment_ids,
                'column_config': column_config,
                'only_with_supports': only_with_supports,
                'support_value_field': support_value_field,
                'export_endpoint_base_url': request.build_absolute_uri(reverse('desi:export_data_endpoint')),
                'export_endpoint_auth_token': _generate_export_endpoint_auth_token(request.user),
                'saved_configs': saved_configs,
                'selected_config_id': selected_config_id,
            }
            return render(request, 'desi/export_data.html', context)

        if not included_columns:
            messages.error(request, "Select at least one column to export.")
            context = {
                'school_year': school_year,
                'assessments': assessments,
                'selected_assessment_ids': selected_assessment_ids,
                'column_config': column_config,
                'only_with_supports': only_with_supports,
                'support_value_field': support_value_field,
                'export_endpoint_base_url': request.build_absolute_uri(reverse('desi:export_data_endpoint')),
                'export_endpoint_auth_token': _generate_export_endpoint_auth_token(request.user),
                'saved_configs': saved_configs,
                'selected_config_id': selected_config_id,
            }
            return render(request, 'desi/export_data.html', context)

        if not selected_assessments:
            messages.error(request, "Select at least one assessment to export.")
            context = {
                'school_year': school_year,
                'assessments': assessments,
                'selected_assessment_ids': selected_assessment_ids,
                'column_config': column_config,
                'only_with_supports': only_with_supports,
                'support_value_field': support_value_field,
                'export_endpoint_base_url': request.build_absolute_uri(reverse('desi:export_data_endpoint')),
                'export_endpoint_auth_token': _generate_export_endpoint_auth_token(request.user),
                'saved_configs': saved_configs,
                'selected_config_id': selected_config_id,
            }
            return render(request, 'desi/export_data.html', context)
        return _build_export_csv_response(
            request.user,
            school_year,
            selected_assessments,
            selected_assessment_ids,
            available_columns,
            included_columns,
            only_with_supports,
            support_value_field,
        )

    if selected_config:
        column_config = _build_column_config_from_saved_config(available_columns, selected_config.config.get('column_config', []))
    else:
        column_config = []
        for col in available_columns:
            key = col['key']
            label = col['label']
            group = col['group']
            include_by_default = key in {
                'student_name', 'student_id', 'grade', 'school_name',
                'assessment_name', 'assessment_completed'
            } or key.startswith('support__')
            column_config.append({
                'key': key,
                'label': label,
                'group': group,
                'include': include_by_default,
                'header': label,
                'order': col['default_order'],
                'support_id': col.get('support_id'),
            })

    context = {
        'school_year': school_year,
        'assessments': assessments,
        'selected_assessment_ids': selected_assessment_ids,
        'column_config': column_config,
        'only_with_supports': only_with_supports,
        'support_value_field': support_value_field,
        'export_endpoint_base_url': request.build_absolute_uri(reverse('desi:export_data_endpoint')),
        'export_endpoint_auth_token': _generate_export_endpoint_auth_token(request.user),
        'saved_configs': saved_configs,
        'selected_config_id': selected_config_id,
    }
    return render(request, 'desi/export_data.html', context)


def export_data_endpoint(request):
    """
    Token-authenticated GET endpoint for external systems to generate DESI export CSV.
    """
    auth_token = request.GET.get('auth', '').strip()
    token_user = _resolve_export_endpoint_user(auth_token)
    if not token_user:
        return HttpResponseForbidden("Invalid or expired export auth token.")
    if not _is_project_admin(token_user):
        return HttpResponseForbidden("Permission denied.")

    school_year = request.GET.get('school_year', '').strip()
    if not school_year:
        try:
            school_year = get_project_config().default_school_year or "2025-2026"
        except Exception:
            school_year = "2025-2026"

    assessments = list(
        Assessment.objects.filter(school_year=school_year, is_active=True)
        .prefetch_related('supported_options__accommodation')
        .order_by('name')
    )
    selected_assessment_ids = {int(x) for x in request.GET.getlist('assessment_ids') if x.isdigit()}
    selected_assessments = [a for a in assessments if a.id in selected_assessment_ids]
    if not selected_assessments:
        return HttpResponseForbidden("Select at least one assessment.")

    available_columns = _build_export_columns(selected_assessments)
    _column_config, included_columns = _parse_export_column_config(request.GET, available_columns)
    if not included_columns:
        return HttpResponseForbidden("Select at least one column.")

    only_with_supports_raw = request.GET.get('only_with_supports', '').strip().lower()
    only_with_supports = only_with_supports_raw in {'on', '1', 'true', 'yes'}
    support_value_field = request.GET.get('support_value_field', 'name').strip().lower()
    if support_value_field not in {'name', 'code'}:
        support_value_field = 'name'

    return _build_export_csv_response(
        token_user,
        school_year,
        selected_assessments,
        selected_assessment_ids,
        available_columns,
        included_columns,
        only_with_supports,
        support_value_field,
    )


@login_required
def create_accommodation(request):
    """Create a new accommodation question and selectively map to assessments and options."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    if not Assessment.objects.exists():
        messages.error(request, "You must create at least one assessment configuration before you can create designated supports.")
        return redirect('desi:admin_dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        category = request.POST.get('category', '').strip()
        description = request.POST.get('description', '').strip()
        
        video_url = request.POST.get('video_url', '').strip() or None
        order_val = request.POST.get('order', '0').strip() or '0'
        
        bulk_options_text = request.POST.get('bulk_options', '').strip()
        dep_support_id, dep_option_id = _parse_dependency_fields(request.POST)
        dep_assessment_ids = _parse_dependency_assessments(request.POST)

        if not name:
            messages.error(request, "Designated Support name is required.")
            return redirect('desi:create_accommodation')

        if Accommodation.objects.filter(name=name).exists():
            messages.error(request, f"A designated support with the name '{name}' already exists.")
            return redirect('desi:create_accommodation')

        try:
            order = int(order_val)
        except ValueError:
            order = 0

        parent_support, parent_option, dep_error = _validate_dependency(
            accommodation=None,
            dep_support_id=dep_support_id,
            dep_option_id=dep_option_id,
        )
        if dep_error:
            messages.error(request, dep_error)
            return redirect('desi:create_accommodation')

        accommodation = Accommodation.objects.create(
            name=name,
            code=code,
            category=category,
            description=description,
            video_url=video_url,
            order=order,
            depends_on_accommodation=parent_support,
            depends_on_option=parent_option,
        )
        if parent_option and dep_assessment_ids:
            accommodation.dependency_assessments.set(Assessment.objects.filter(id__in=dep_assessment_ids))

        # Collect option names/codes to create
        option_names = request.POST.getlist('option_name[]')
        option_codes = request.POST.getlist('option_code[]')
        master_options = []
        for idx, raw_name in enumerate(option_names):
            opt_code = option_codes[idx].strip() if idx < len(option_codes) else ''
            names = _split_option_values(raw_name)
            for opt_name in names:
                master_options.append((opt_name, opt_code if len(names) == 1 else ''))

        if bulk_options_text:
            for raw_name in [x.strip() for x in bulk_options_text.split(',') if x.strip()]:
                master_options.append((raw_name, ''))

        normalized_names = [name.lower() for name, _ in master_options]
        if len(normalized_names) != len(set(normalized_names)):
            messages.error(request, "Option names must be unique within a designated support.")
            return redirect('desi:create_accommodation')

        master_option_names = {name for name, _ in master_options}
        master_option_codes = {name: code for name, code in master_options if code}

        selected_assessment_ids = request.POST.getlist('supported_assessments')
        selected_assessment_ids = [int(x) for x in selected_assessment_ids if x.isdigit()]

        all_names_to_create = set(master_option_names)
        for ast_id in selected_assessment_ids:
            custom_opts_text = request.POST.get(f'assessment_opts_custom_{ast_id}', '').strip()
            if custom_opts_text:
                all_names_to_create.update([x.strip() for x in custom_opts_text.split(',') if x.strip()])

        if not all_names_to_create:
            all_names_to_create.add("Standard")

        # Create all unique AccommodationOption objects
        for opt_name in all_names_to_create:
            opt, _created = AccommodationOption.objects.get_or_create(
                accommodation=accommodation,
                name=opt_name,
                defaults={'code': master_option_codes.get(opt_name, opt_name[:10].upper().replace(" ", ""))}
            )
            desired_code = master_option_codes.get(opt_name)
            if desired_code is not None and opt.code != desired_code:
                opt.code = desired_code
                opt.save(update_fields=['code'])

        # Clear and map options to selected assessments
        all_options = accommodation.options.all()
        for ast_id in selected_assessment_ids:
            try:
                ast = Assessment.objects.get(id=ast_id)
                custom_opts_text = request.POST.get(f'assessment_opts_custom_{ast_id}', '').strip()
                if custom_opts_text:
                    names_to_add = [x.strip() for x in custom_opts_text.split(',') if x.strip()]
                    opts_to_add = all_options.filter(name__in=names_to_add)
                else:
                    opt_names = request.POST.getlist(f'assessment_opts_{ast_id}')
                    if not opt_names:
                        opts_to_add = all_options.filter(name__in=list(master_option_names))
                    else:
                        opts_to_add = all_options.filter(name__in=opt_names)
                ast.supported_options.add(*opts_to_add)
            except Assessment.DoesNotExist:
                pass

        messages.success(request, f"Designated support '{accommodation.name}' created successfully.")
        return redirect('desi:admin_dashboard')

    assessments = Assessment.objects.filter(is_active=True).order_by('school_year', 'name')
    context = {
        'is_edit': False,
        'assessments': assessments,
        'dependency_supports': Accommodation.objects.all().prefetch_related('options'),
        'selected_dependency_support_id': None,
        'selected_dependency_option_id': None,
        'selected_dependency_assessment_ids': set(),
    }
    return render(request, 'desi/accommodation_form.html', context)


@login_required
def edit_accommodation(request, id):
    """Edit an existing accommodation, options list, and assessment option mappings."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    accommodation = get_object_or_404(Accommodation, id=id)

    if request.method == 'POST':
        accommodation.name = request.POST.get('name', '').strip()
        accommodation.code = request.POST.get('code', '').strip()
        accommodation.category = request.POST.get('category', '').strip()
        accommodation.description = request.POST.get('description', '').strip()
        
        accommodation.video_url = request.POST.get('video_url', '').strip() or None
        
        order_val = request.POST.get('order', '0').strip() or '0'
        try:
            accommodation.order = int(order_val)
        except ValueError:
            accommodation.order = 0

        accommodation.save()

        # Update options
        bulk_options_text = request.POST.get('bulk_options', '').strip()
        dep_support_id, dep_option_id = _parse_dependency_fields(request.POST)
        dep_assessment_ids = _parse_dependency_assessments(request.POST)

        option_ids = request.POST.getlist('option_id[]')
        option_names = request.POST.getlist('option_name[]')
        option_codes = request.POST.getlist('option_code[]')
        posted_options = []
        for idx, raw_name in enumerate(option_names):
            opt_code = option_codes[idx].strip() if idx < len(option_codes) else ''
            opt_id = option_ids[idx].strip() if idx < len(option_ids) else ''
            names = _split_option_values(raw_name)
            for opt_name in names:
                # A single value can safely retain its existing ID/code;
                # comma-expanded values are new options.
                posted_options.append((opt_id if len(names) == 1 else '', opt_name, opt_code if len(names) == 1 else ''))

        if bulk_options_text:
            for raw_name in [x.strip() for x in bulk_options_text.split(',') if x.strip()]:
                posted_options.append(('', raw_name, ''))

        normalized_names = [name.lower() for _id, name, _code in posted_options]
        if len(normalized_names) != len(set(normalized_names)):
            messages.error(request, "Option names must be unique within a designated support.")
            return redirect('desi:edit_accommodation', id=accommodation.id)

        master_option_names = {name for _id, name, _code in posted_options}

        selected_assessment_ids = request.POST.getlist('supported_assessments')
        selected_assessment_ids = [int(x) for x in selected_assessment_ids if x.isdigit()]

        all_names_to_create = set(master_option_names)
        for ast_id in selected_assessment_ids:
            custom_opts_text = request.POST.get(f'assessment_opts_custom_{ast_id}', '').strip()
            if custom_opts_text:
                all_names_to_create.update([x.strip() for x in custom_opts_text.split(',') if x.strip()])

        if not all_names_to_create:
            all_names_to_create.add("Standard")

        # ID-preserving option updates with editable name/code
        existing_opts = {str(opt.id): opt for opt in accommodation.options.all()}
        keep_opt_ids = []
        for posted_id, name, code in posted_options:
            if posted_id and posted_id in existing_opts:
                opt = existing_opts[posted_id]
                if opt.name != name or opt.code != code:
                    opt.name = name
                    opt.code = code
                    opt.save(update_fields=['name', 'code'])
                keep_opt_ids.append(opt.id)
            else:
                new_opt = AccommodationOption.objects.create(
                    accommodation=accommodation,
                    name=name,
                    code=code or name[:10].upper().replace(" ", "")
                )
                keep_opt_ids.append(new_opt.id)

        existing_all_by_name = {opt.name: opt for opt in accommodation.options.all()}
        existing_names = {opt.name for opt in accommodation.options.filter(id__in=keep_opt_ids)}
        for name in all_names_to_create:
            if name in existing_names:
                continue
            existing_opt = existing_all_by_name.get(name)
            if existing_opt:
                keep_opt_ids.append(existing_opt.id)
                existing_names.add(name)
                continue
            new_opt = AccommodationOption.objects.create(
                accommodation=accommodation,
                name=name,
                code=name[:10].upper().replace(" ", "")
            )
            keep_opt_ids.append(new_opt.id)
            existing_names.add(name)

        # Remove any options not in the new set
        accommodation.options.exclude(id__in=keep_opt_ids).delete()

        # Clear old mappings of this accommodation on ALL assessments first
        all_options = accommodation.options.all()
        for ast in Assessment.objects.all():
            ast.supported_options.remove(*all_options)

        # Re-save mapping of options to selected assessments
        all_options_list = list(all_options)
        for ast_id in selected_assessment_ids:
            try:
                ast = Assessment.objects.get(id=ast_id)
                custom_opts_text = request.POST.get(f'assessment_opts_custom_{ast_id}', '').strip()
                if custom_opts_text:
                    names_to_add = [x.strip() for x in custom_opts_text.split(',') if x.strip()]
                    opts_to_add = [o for o in all_options_list if o.name in names_to_add]
                else:
                    opt_names = request.POST.getlist(f'assessment_opts_{ast_id}')
                    if not opt_names:
                        opts_to_add = [o for o in all_options_list if o.name in master_option_names]
                    else:
                        opts_to_add = [o for o in all_options_list if o.name in opt_names]
                ast.supported_options.add(*opts_to_add)
            except Assessment.DoesNotExist:
                pass

        parent_support, parent_option, dep_error = _validate_dependency(
            accommodation=accommodation,
            dep_support_id=dep_support_id,
            dep_option_id=dep_option_id,
        )
        if dep_error:
            messages.error(request, dep_error)
            return redirect('desi:edit_accommodation', id=accommodation.id)

        accommodation.depends_on_accommodation = parent_support
        accommodation.depends_on_option = parent_option
        accommodation.save(update_fields=['depends_on_accommodation', 'depends_on_option', 'updated_at'])
        if parent_option:
            accommodation.dependency_assessments.set(Assessment.objects.filter(id__in=dep_assessment_ids))
        else:
            accommodation.dependency_assessments.clear()

        messages.success(request, f"Designated support '{accommodation.name}' updated successfully.")
        return redirect('desi:admin_dashboard')

    # Load active assessments
    assessments = list(Assessment.objects.filter(is_active=True).order_by('school_year', 'name'))
    
    # Pre-resolve selected assessments and options for mapping in view
    assigned_assessment_opts = {}
    assigned_custom_opts = {}
    
    master_option_names = set(accommodation.options.values_list('name', flat=True))
    
    for ast in assessments:
        ast_opts = list(ast.supported_options.filter(accommodation=accommodation))
        if ast_opts:
            opt_names = [o.name for o in ast_opts]
            is_custom = any(name not in master_option_names for name in opt_names)
            if is_custom:
                assigned_custom_opts[ast.id] = ", ".join(opt_names)
            else:
                assigned_assessment_opts[ast.id] = opt_names

    context = {
        'accommodation': accommodation,
        'options': accommodation.options.all(),
        'assessments': assessments,
        'assigned_assessment_opts': assigned_assessment_opts,
        'assigned_custom_opts': assigned_custom_opts,
        'is_edit': True,
        'dependency_supports': Accommodation.objects.exclude(id=accommodation.id).prefetch_related('options'),
        'selected_dependency_support_id': accommodation.depends_on_accommodation_id,
        'selected_dependency_option_id': accommodation.depends_on_option_id,
        'selected_dependency_assessment_ids': set(accommodation.dependency_assessments.values_list('id', flat=True)),
    }
    return render(request, 'desi/accommodation_form.html', context)


@login_required
def delete_accommodation(request, id):
    """Delete an accommodation configuration."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    accommodation = get_object_or_404(Accommodation, id=id)
    name = accommodation.name
    accommodation.delete()
    messages.success(request, f"Designated support '{name}' deleted successfully.")
    return redirect('desi:admin_dashboard')


@login_required
def create_assessment(request):
    """Create a new assessment configuration with criteria."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    try:
        default_year = get_project_config().default_school_year
    except Exception:
        default_year = "2025-2026"

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        subject = request.POST.get('subject', '').strip()
        school_year = request.POST.get('school_year', default_year).strip()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not name:
            messages.error(request, "Assessment name is required.")
            return redirect('desi:create_assessment')

        if Assessment.objects.filter(name=name, subject=subject, school_year=school_year).exists():
            subject_label = subject or ' no subject'
            messages.error(request, f"An assessment named '{name}' with subject '{subject_label}' already exists for school year {school_year}.")
            return redirect('desi:create_assessment')

        assessment = Assessment.objects.create(
            name=name,
            subject=subject,
            school_year=school_year,
            description=description,
            is_active=is_active
        )

        # Save Criteria
        crit_fields = request.POST.getlist('crit_field[]')
        crit_operators = request.POST.getlist('crit_operator[]')
        crit_values = request.POST.getlist('crit_value[]')

        for field, op, val in zip(crit_fields, crit_operators, crit_values):
            if field and op and val:
                AssessmentCriteria.objects.create(
                    assessment=assessment,
                    field_name=field,
                    operator=op,
                    value=val
                )

        messages.success(request, f"Assessment '{assessment.name}' created successfully.")
        return redirect('desi:admin_dashboard')

    field_choices = AssessmentCriteria.FIELD_CHOICES
    operator_choices = AssessmentCriteria.OPERATOR_CHOICES

    context = {
        'field_choices': field_choices,
        'operator_choices': operator_choices,
        'default_year': default_year,
        'student_field_options_json': _get_student_field_options(),
        'is_edit': False,
    }
    return render(request, 'desi/assessment_form.html', context)


@login_required
def edit_assessment(request, id):
    """Edit an existing assessment and criteria rules."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    assessment = get_object_or_404(Assessment, id=id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        subject = request.POST.get('subject', '').strip()
        school_year = request.POST.get('school_year', assessment.school_year).strip()
        if Assessment.objects.filter(name=name, subject=subject, school_year=school_year).exclude(id=assessment.id).exists():
            messages.error(request, f"An assessment named '{name}' with subject '{subject or 'no subject'}' already exists for school year {school_year}.")
            return redirect('desi:edit_assessment', id=assessment.id)
        assessment.name = name
        assessment.subject = subject
        assessment.school_year = school_year
        assessment.description = request.POST.get('description', '').strip()
        assessment.is_active = request.POST.get('is_active') == 'on'
        assessment.save()

        # Re-save Criteria Rules
        assessment.criteria.all().delete()
        crit_fields = request.POST.getlist('crit_field[]')
        crit_operators = request.POST.getlist('crit_operator[]')
        crit_values = request.POST.getlist('crit_value[]')

        for field, op, val in zip(crit_fields, crit_operators, crit_values):
            if field and op and val:
                AssessmentCriteria.objects.create(
                    assessment=assessment,
                    field_name=field,
                    operator=op,
                    value=val
                )

        messages.success(request, f"Assessment '{assessment.name}' updated successfully.")
        return redirect('desi:admin_dashboard')

    context = {
        'assessment': assessment,
        'criteria': assessment.criteria.all(),
        'field_choices': AssessmentCriteria.FIELD_CHOICES,
        'operator_choices': AssessmentCriteria.OPERATOR_CHOICES,
        'student_field_options_json': _get_student_field_options(),
        'is_edit': True,
    }
    return render(request, 'desi/assessment_form.html', context)


@login_required
def delete_assessment(request, id):
    """Delete an assessment configuration."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    assessment = get_object_or_404(Assessment, id=id)
    name = assessment.name
    assessment.delete()
    messages.success(request, f"Assessment '{name}' deleted successfully.")
    return redirect('desi:admin_dashboard')


@login_required
def clone_assessment(request, id):
    """Clones an assessment and its demographic criteria rules."""
    if not _is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('landing:index')

    src = get_object_or_404(Assessment, id=id)
    cloned = Assessment.objects.create(
        name=f"Copy of {src.name}",
        subject=src.subject,
        school_year=src.school_year,
        description=src.description,
        is_active=False
    )
    cloned.supported_options.set(src.supported_options.all())

    # Clone criteria
    for rule in src.criteria.all():
        AssessmentCriteria.objects.create(
            assessment=cloned,
            field_name=rule.field_name,
            operator=rule.operator,
            value=rule.value
        )

    messages.success(request, f"Assessment cloned to '{cloned.name}'. Please edit the name and activate it.")
    return redirect('desi:admin_dashboard')


def _build_assign_accommodations_context(student, stu_annual_key, eligible_assessments, assessments, failed_rules_map, student_option_ids, selected_support_ids=None):
    """Build workspace context for the DESI accommodation editor."""
    shared_accommodations = []
    selected_support_ids = set(selected_support_ids or [])
    test_specific_accommodations = {ast.id: [] for ast in eligible_assessments}

    # Map: accommodation -> { assessment: options_list }
    acc_ast_opts = {}
    for ast in eligible_assessments:
        opts = list(ast.supported_options.all().select_related('accommodation'))
        for opt in opts:
            acc = opt.accommodation
            if acc not in acc_ast_opts:
                acc_ast_opts[acc] = {}
            if ast not in acc_ast_opts[acc]:
                acc_ast_opts[acc][ast] = []
            acc_ast_opts[acc][ast].append(opt)

    # Sort accommodations
    sorted_accs = sorted(acc_ast_opts.keys(), key=lambda a: (a.order, a.category, a.name))

    for acc in sorted_accs:
        ast_map = acc_ast_opts[acc]
        asts = list(ast_map.keys())
        dependency_scope_ids = set(acc.dependency_assessments.values_list('id', flat=True))

        def dependency_applies(assessment_id):
            if not acc.depends_on_option_id:
                return False
            if not dependency_scope_ids:
                return True
            return assessment_id in dependency_scope_ids

        # Check if options are identical across all supported assessments for this student
        is_identical = True
        if len(asts) > 1:
            first_set = set(o.id for o in list(ast_map.values())[0])
            first_dep_applies = dependency_applies(asts[0].id)
            for ast, opts in ast_map.items():
                if set(o.id for o in opts) != first_set:
                    is_identical = False
                    break
                if dependency_applies(ast.id) != first_dep_applies:
                    is_identical = False
                    break
        else:
            is_identical = False

        representative_opts = list(ast_map.values())[0]
        assigned_opt = None
        for opt in representative_opts:
            if opt.id in student_option_ids:
                assigned_opt = opt
                break
        is_multichoice = len(representative_opts) > 1 or (len(representative_opts) == 1 and representative_opts[0].name != "Standard")

        acc_data = {
            'accommodation': acc,
            'options': representative_opts,
            'is_multichoice': is_multichoice,
            'selected_option': assigned_opt,
            'dependency_option_id': acc.depends_on_option_id if dependency_applies(asts[0].id) else None,
            'selected_direct': acc.id in selected_support_ids,
        }

        if len(asts) > 1 and is_identical:
            acc_data['applies_to'] = asts
            shared_accommodations.append(acc_data)
        else:
            # Add unique configurations to each assessment
            for ast, opts in ast_map.items():
                ast_assigned = None
                for opt in opts:
                    if opt.id in student_option_ids:
                        ast_assigned = opt
                        break
                ast_is_multi = len(opts) > 1 or (len(opts) == 1 and opts[0].name != "Standard")

                test_specific_accommodations[ast.id].append({
                    'accommodation': acc,
                    'options': opts,
                    'is_multichoice': ast_is_multi,
                    'selected_option': ast_assigned,
                    'dependency_option_id': acc.depends_on_option_id if dependency_applies(ast.id) else None,
                    'selected_direct': acc.id in selected_support_ids,
                })

    # Completions status
    completions = StudentAssessmentCompletion.objects.filter(
        student_annual_key=stu_annual_key,
        assessment__in=eligible_assessments,
        is_completed=True
    ).values_list('assessment_id', flat=True)
    completed_assessment_ids = set(completions)

    # Format assessment cards packaging
    assessment_cards = []
    for ast in eligible_assessments:
        assessment_cards.append({
            'assessment': ast,
            'accommodations': test_specific_accommodations[ast.id],
            'is_completed': ast.id in completed_assessment_ids,
            'is_eligible': True,
        })

    # Include warning details for ineligible tests in active school year
    ineligible_cards = []
    for ast in assessments:
        if ast not in eligible_assessments:
            ineligible_cards.append({
                'assessment': ast,
                'is_eligible': False,
                'failed_rules': failed_rules_map.get(ast.id, []),
            })

    return {
        'student': student,
        'shared_accommodations': shared_accommodations,
        'assessment_cards': assessment_cards,
        'ineligible_cards': ineligible_cards,
        'student_option_ids': student_option_ids,
    }


def _student_annual_keys(student):
    """Find this student's annual records across school years."""
    identity_filter = Q(ssid=student.ssid) if student.ssid else Q(student_id=student.student_id)
    if student.student_id:
        identity_filter |= Q(student_id=student.student_id)
    return list(CurrentStudent.objects.filter(identity_filter).values_list('stu_annual_key', flat=True))


def _get_student_accommodation(student):
    """Load supports by stable student identity, carrying them across years."""
    keys = _student_annual_keys(student)
    records = list(StudentAccommodation.objects.filter(
        student_annual_key__in=keys
    ).prefetch_related('options').order_by('-updated_at'))
    if records:
        record = records[0]
        if record.student_annual_key != student.stu_annual_key:
            current, _ = StudentAccommodation.objects.get_or_create(
                student_annual_key=student.stu_annual_key,
                defaults={'updated_by': record.updated_by},
            )
            current.options.set(record.options.all())
            current.selected_accommodations.set(record.selected_accommodations.all())
            return current, keys
        return record, keys
    record, _ = StudentAccommodation.objects.get_or_create(
        student_annual_key=student.stu_annual_key
    )
    return record, keys


@login_required
def assign_accommodations(request, stu_annual_key):
    """Workspace to check eligibility, group accommodations, select options, and mark tests as completed."""
    student = get_object_or_404(CurrentStudent, stu_annual_key=stu_annual_key)

    active_school = getattr(request.user, 'active_school', None)
    visible_students = get_visible_students(request.user, active_school=active_school)
    if not visible_students.filter(stu_annual_key=stu_annual_key).exists():
        return HttpResponseForbidden("You do not have permission to view or manage this student's data.")

    student_acc, student_annual_keys = _get_student_accommodation(student)

    # Pull active assessments
    assessments = Assessment.objects.filter(school_year=student.school_year, is_active=True).prefetch_related(
        'criteria',
        'supported_options__accommodation__depends_on_option',
        'supported_options__accommodation__dependency_assessments',
    )

    # Evaluate dynamic eligibility
    eligible_assessments = []
    failed_rules_map = {}
    for ast in assessments:
        eligible, failed_rules = check_criteria(ast.criteria.all(), student)
        failed_rules_map[ast.id] = failed_rules
        if eligible:
            eligible_assessments.append(ast)

    is_htmx = request.headers.get('HX-Request') == 'true' or request.META.get('HTTP_HX_REQUEST') == 'true'

    if request.method == 'POST':
        # 1. Update Student's global assigned AccommodationOptions
        selected_opt_ids = request.POST.getlist('student_options')
        selected_opt_ids = [int(x) for x in selected_opt_ids if x and str(x).isdigit()]
        selected_support_ids = [int(x) for x in request.POST.getlist('student_supports') if x and str(x).isdigit()]
        selected_options = list(
            AccommodationOption.objects.filter(id__in=selected_opt_ids)
            .select_related('accommodation')
            .prefetch_related('accommodation__dependency_assessments')
        )
        option_to_accommodation = {opt.id: opt.accommodation_id for opt in selected_options}

        dependency_rules = {}
        support_contexts = {}
        for ast in eligible_assessments:
            for opt in ast.supported_options.all():
                support = opt.accommodation
                support_contexts.setdefault(support.id, set()).add(ast.id)
                if support.depends_on_option_id:
                    dependency_rules[support.id] = {
                        'trigger_option_id': support.depends_on_option_id,
                        'assessment_ids': set(support.dependency_assessments.values_list('id', flat=True)),
                    }

        for opt in selected_options:
            support = opt.accommodation
            if support.depends_on_option_id:
                dependency_rules[support.id] = {
                    'trigger_option_id': support.depends_on_option_id,
                    'assessment_ids': set(support.dependency_assessments.values_list('id', flat=True)),
                }

        selected_opt_ids = _apply_dependency_rules(
            selected_opt_ids,
            option_to_accommodation,
            support_contexts,
            dependency_rules,
        )
        student_acc.options.set(AccommodationOption.objects.filter(id__in=selected_opt_ids))
        student_acc.selected_accommodations.set(Accommodation.objects.filter(id__in=selected_support_ids))
        student_acc.updated_by = request.user
        student_acc.save()
        # Keep the student's support choices consistent across annual records.
        StudentAccommodation.objects.filter(
            student_annual_key__in=student_annual_keys
        ).exclude(pk=student_acc.pk).update(updated_by=request.user)
        for annual_record in StudentAccommodation.objects.filter(student_annual_key__in=student_annual_keys).exclude(pk=student_acc.pk):
            annual_record.options.set(selected_opt_ids)
            annual_record.selected_accommodations.set(selected_support_ids)

        # 2. Update Assessment completions status
        completed_ids_list = request.POST.getlist('completed_assessments')
        completed_ids = set(int(x) for x in completed_ids_list if x.isdigit())

        for ast in eligible_assessments:
            is_comp = ast.id in completed_ids
            comp_record, _ = StudentAssessmentCompletion.objects.get_or_create(
                student_annual_key=stu_annual_key,
                assessment=ast
            )
            comp_record.is_completed = is_comp
            comp_record.updated_by = request.user
            comp_record.save()

        messages.success(request, f"Designated supports for {student.student_name} saved successfully.")

        if is_htmx:
            list_context = _get_student_list_context(request)
            context = _build_assign_accommodations_context(
                student,
                stu_annual_key,
                eligible_assessments,
                assessments,
                failed_rules_map,
                selected_opt_ids,
                selected_support_ids,
            )
            list_context.update(context)

            selected_student_row = None
            for list_student in list_context.get('page_obj', []):
                if getattr(list_student, 'stu_annual_key', None) == stu_annual_key:
                    selected_student_row = list_student
                    break

            row_fragment = ''
            if selected_student_row:
                row_context = {
                    'student': selected_student_row,
                    'selected_student_key': stu_annual_key,
                    'request': request,
                    'swap_oob': True,
                    'has_multiple_schools': list_context.get('has_multiple_schools', False),
                }
                row_fragment = render_to_string('desi/partials/student_list_item.html', row_context, request=request)

            workspace_html = render_to_string('desi/partials/workspace_form.html', context, request=request)
            return HttpResponse(f"{workspace_html}{row_fragment}")

        get_params = request.GET.urlencode()
        url = reverse('desi:assign_accommodations', args=[stu_annual_key])
        if get_params:
            url = f"{url}?{get_params}"
        return redirect(url)

    # GET logic: Group accommodations into Shared vs Test-Specific
    student_options = list(student_acc.options.all())
    selected_support_ids = set(student_acc.selected_accommodations.values_list('id', flat=True))
    option_to_accommodation = {opt.id: opt.accommodation_id for opt in student_options}
    dependency_rules = {}
    support_contexts = {}
    for ast in eligible_assessments:
        for opt in ast.supported_options.all():
            support = opt.accommodation
            support_contexts.setdefault(support.id, set()).add(ast.id)
            if support.depends_on_option_id:
                dependency_rules[support.id] = {
                    'trigger_option_id': support.depends_on_option_id,
                    'assessment_ids': set(support.dependency_assessments.values_list('id', flat=True)),
                }

    student_option_ids = _apply_dependency_rules(
        [o.id for o in student_options],
        option_to_accommodation,
        support_contexts,
        dependency_rules,
    )
    if set(o.id for o in student_options) != student_option_ids:
        student_acc.options.set(AccommodationOption.objects.filter(id__in=student_option_ids))
        student_acc.updated_by = request.user
        student_acc.save()

    context = _build_assign_accommodations_context(
        student,
        stu_annual_key,
        eligible_assessments,
        assessments,
        failed_rules_map,
        student_option_ids,
        selected_support_ids,
    )

    if is_htmx:
        workspace_html = render_to_string('desi/partials/workspace_form.html', context, request=request)
        return HttpResponse(workspace_html)

    # Pull list context
    list_context = _get_student_list_context(request)
    list_context.update(context)
    return render(request, 'desi/student_list.html', list_context)
