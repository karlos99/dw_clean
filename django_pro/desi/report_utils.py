"""
Aggregate DESI completion report for a school, used by the Testing Coordinator
page. Completion is recorded per assessment (StudentAssessmentCompletion); the
report shows, per assessment, how many eligible students are completed/pending.
"""


def build_school_report(school_state_id, school_year):
    """
    Return per-assessment completion summary and per-student status rows for a
    school/year.

    students: list of dicts {student, statuses: [{assessment, eligible, completed}]}
    pending_by_assessment: {assessment_id: [student, ...]} (eligible, not completed)
    """
    from desi.models import Assessment, StudentAssessmentCompletion
    from desi.views import check_criteria, build_effective_assessment_criteria
    from studata.models import CurrentStudent

    students = list(
        CurrentStudent.objects
        .filter(school_year=school_year, school_state_id=school_state_id)
        .order_by('student_name', 'stu_annual_key')
    )
    all_assessments = list(Assessment.objects.prefetch_related('criteria').order_by('name'))
    assessments = [a for a in all_assessments if a.is_active]

    student_keys = [s.stu_annual_key for s in students]
    completed_pairs = set()
    if student_keys and assessments:
        completed_pairs = {
            (rec.student_annual_key, rec.assessment_id)
            for rec in StudentAssessmentCompletion.objects.filter(
                student_annual_key__in=student_keys,
                assessment__in=assessments,
                is_completed=True,
            )
        }

    assessment_stats = {
        a.id: {'assessment': a, 'eligible': 0, 'completed': 0}
        for a in assessments
    }
    pending_by_assessment = {a.id: [] for a in assessments}

    student_statuses = []
    for student in students:
        statuses = []
        for assessment in assessments:
            eligible, _failed = check_criteria(
                build_effective_assessment_criteria(assessment, all_assessments), student
            )
            is_completed = (student.stu_annual_key, assessment.id) in completed_pairs
            statuses.append({
                'assessment': assessment,
                'eligible': eligible,
                'completed': is_completed,
            })
            if eligible:
                assessment_stats[assessment.id]['eligible'] += 1
                if is_completed:
                    assessment_stats[assessment.id]['completed'] += 1
                else:
                    pending_by_assessment[assessment.id].append(student)
        student_statuses.append({'student': student, 'statuses': statuses})

    summary = []
    for stat in assessment_stats.values():
        assessment = stat['assessment']
        eligible = stat['eligible']
        completed = stat['completed']
        summary.append({
            'assessment': assessment,
            'eligible': eligible,
            'completed': completed,
            'pending': eligible - completed,
            'percent': round((completed / eligible) * 100, 1) if eligible else None,
        })

    return {
        'school_state_id': school_state_id,
        'school_year': school_year,
        'assessments': assessments,
        'summary': summary,
        'students': student_statuses,
        'pending_by_assessment': pending_by_assessment,
        'total_students': len(students),
    }


def build_report_rows(report):
    """
    CSV-friendly per-student rows. Columns: SSID, Student, Grade, then one
    column per assessment with Completed / Pending / '' (not eligible).
    """
    rows = []
    assessments = report['assessments']
    for entry in report['students']:
        student = entry['student']
        status_by_id = {s['assessment'].id: s for s in entry['statuses']}
        row = {
            'ssid': student.ssid or '',
            'student_name': student.student_name or '',
            'grade': student.grade or '',
        }
        for assessment in assessments:
            status = status_by_id[assessment.id]
            if not status['eligible']:
                row[assessment.id] = ''
            else:
                row[assessment.id] = 'Completed' if status['completed'] else 'Pending'
        rows.append(row)
    return rows


def teacher_options_by_school(school_state_ids, school_year):
    """Map each school state id to sorted teacher names for that school/year."""
    from studata.models import CurrentStudent, TeacherAccess

    school_state_ids = [sid for sid in school_state_ids if sid]
    if not school_state_ids or not school_year:
        return {sid: [] for sid in school_state_ids}

    key_to_school = dict(
        CurrentStudent.objects.filter(
            school_year=school_year,
            school_state_id__in=school_state_ids,
        ).values_list('stu_annual_key', 'school_state_id')
    )
    by_school = {sid: set() for sid in school_state_ids}
    if not key_to_school:
        return {sid: [] for sid in school_state_ids}

    for staff_name, stu_key in TeacherAccess.objects.filter(
        school_year=school_year,
        stu_annual_key__in=key_to_school.keys(),
    ).values_list('staff_name', 'stu_annual_key'):
        name = (staff_name or '').strip()
        school_id = key_to_school.get(stu_key)
        if name and school_id:
            by_school.setdefault(school_id, set()).add(name)

    return {
        sid: sorted(names, key=str.casefold)
        for sid, names in by_school.items()
    }


def list_school_teachers(school_state_id, school_year):
    """Teacher names (from the teacher access view) with students in the school/year."""
    return teacher_options_by_school([school_state_id], school_year).get(school_state_id, [])


from collections import defaultdict

from django.db import DatabaseError, transaction

from desi.seis_data import seis_support_detail_for_student
from studata.models import TeacherAccess


def teachers_for_students(stu_annual_keys, school_year):
    """Map stu_annual_key -> sorted teacher names from the teacher access view."""
    keys = [k for k in stu_annual_keys if k]
    if not keys:
        return {}

    by_key = defaultdict(set)
    try:
        with transaction.atomic():
            for staff_name, stu_key in TeacherAccess.objects.filter(
                school_year=school_year,
                stu_annual_key__in=keys,
            ).values_list('staff_name', 'stu_annual_key'):
                name = (staff_name or '').strip()
                if name and stu_key:
                    by_key[stu_key].add(name)
    except DatabaseError:
        # In test environments or unmigrated views, table may not exist
        pass

    return {
        key: sorted(names, key=str.casefold)
        for key, names in by_key.items()
    }


def build_support_filter_groups():
    """
    Support filter dropdown: program (CAASPP / ELPAC), then section category (or
    legacy category), then support / option entries. Values are
    ``program:<TAG>``, ``o<option_id>`` or ``a<accommodation_id>``.
    """
    from desi.models import Accommodation, AccommodationCategory

    groups = [{
        'label': 'Program',
        'options': [
            {'value': 'program:CAASPP', 'label': 'CAASPP'},
            {'value': 'program:ELPAC', 'label': 'ELPAC'},
        ],
    }]
    seen_accommodation_ids = set()

    categories = list(AccommodationCategory.objects.order_by('order', 'name'))
    for category in categories:
        accommodations = list(
            Accommodation.objects.filter(section_category=category)
            .prefetch_related('options')
            .order_by('order', 'name')
        )
        options = []
        for acc in accommodations:
            seen_accommodation_ids.add(acc.id)
            opts = list(acc.options.all().order_by('name'))
            if opts:
                for opt in opts:
                    options.append({
                        'value': f'o{opt.id}',
                        'label': f'{acc.name} — {opt.name}',
                    })
            else:
                options.append({'value': f'a{acc.id}', 'label': acc.name})
        if options:
            groups.append({'label': category.name, 'options': options})

    uncategorized = (
        Accommodation.objects.filter(section_category__isnull=True)
        .prefetch_related('options')
        .order_by('category', 'order', 'name')
    )
    by_legacy_category = {}
    for acc in uncategorized:
        if acc.id in seen_accommodation_ids:
            continue
        seen_accommodation_ids.add(acc.id)
        label = (acc.category or '').strip() or 'Other'
        by_legacy_category.setdefault(label, []).append(acc)

    for cat_label in sorted(by_legacy_category.keys(), key=str.casefold):
        options = []
        for acc in by_legacy_category[cat_label]:
            opts = list(acc.options.all().order_by('name'))
            if opts:
                for opt in opts:
                    options.append({
                        'value': f'o{opt.id}',
                        'label': f'{acc.name} — {opt.name}',
                    })
            else:
                options.append({'value': f'a{acc.id}', 'label': acc.name})
        if options:
            groups.append({'label': cat_label, 'options': options})

    return groups


def parse_support_filter_value(raw_value):
    """
    Return (kind, id) for ``program:<TAG>`` / ``o123`` / ``a456``, or
    (None, None) if invalid.
    """
    raw = (raw_value or '').strip()
    if raw.lower().startswith('program:'):
        program = raw.split(':', 1)[1].strip().upper()
        if program:
            return ('program', program)
        return None, None
    if len(raw) < 2:
        return None, None
    prefix, id_part = raw[0], raw[1:]
    if prefix not in ('o', 'a') or not id_part.isdigit():
        return None, None
    return ('option', int(id_part)) if prefix == 'o' else ('accommodation', int(id_part))


def student_has_support_filter(record, seis_detail, filter_kind, filter_id):
    """Whether a student has the selected support/program (DESI entry and/or SEIS)."""
    if not filter_kind or filter_id is None:
        return True

    seis_detail = seis_detail or {}
    seis_option_ids = seis_detail.get('option_ids') or set()
    seis_acc_ids = seis_detail.get('accommodation_ids') or set()

    if filter_kind == 'program':
        tag = str(filter_id).strip().upper()
        if tag in (seis_detail.get('tags') or set()):
            return True
        if record:
            if record.options.filter(accommodation__tag__iexact=tag).exists():
                return True
            if record.selected_accommodations.filter(tag__iexact=tag).exists():
                return True
        return False

    if filter_kind == 'option':
        if filter_id in seis_option_ids:
            return True
        if record and record.options.filter(id=filter_id).exists():
            return True
        return False

    if filter_kind == 'accommodation':
        if filter_id in seis_acc_ids:
            return True
        if record:
            if record.selected_accommodations.filter(id=filter_id).exists():
                return True
            if record.options.filter(accommodation_id=filter_id).exists():
                return True
        return False

    return True


def build_entered_supports_report(school_state_id, school_year, teacher=None, support_filter=''):
    """
    Per-student supports for the coordinator export: DESI-entered options and
    direct supports, plus read-only SEIS-mapped supports (labeled ``(SEIS)``).
    Also includes completion names and last update metadata. Optionally scoped
    to a teacher's roster.
    """
    from desi.models import StudentAccommodation, StudentAssessmentCompletion
    from studata.models import CurrentStudent, TeacherAccess

    filter_kind, filter_id = parse_support_filter_value(support_filter)

    students_qs = CurrentStudent.objects.filter(
        school_year=school_year,
        school_state_id=school_state_id,
    ).order_by('student_name', 'stu_annual_key')

    if teacher:
        roster_keys = set(
            TeacherAccess.objects.filter(
                school_year=school_year,
                staff_name__iexact=teacher,
                stu_annual_key__in=students_qs.values('stu_annual_key'),
            ).values_list('stu_annual_key', flat=True)
        )
        if not roster_keys:
            return {'students': [], 'rows': [], 'total': 0}
        students_qs = students_qs.filter(stu_annual_key__in=roster_keys)

    students = list(students_qs)
    keys = [s.stu_annual_key for s in students]
    teacher_map = teachers_for_students(keys, school_year)

    records = {
        rec.student_annual_key: rec
        for rec in StudentAccommodation.objects.filter(student_annual_key__in=keys)
        .select_related('updated_by')
        .prefetch_related(
            'options__accommodation',
            'options__assessments',
            'selected_accommodations__options__assessments',
        )
    }

    completed_map = {}
    if keys:
        for key, assessment_name in (
            StudentAssessmentCompletion.objects.filter(
                student_annual_key__in=keys,
                is_completed=True,
            ).values_list('student_annual_key', 'assessment__name')
        ):
            completed_map.setdefault(key, []).append(assessment_name)

    # Report columns: (row key, program tag, subject filter). CAASPP supports are
    # split by content area (ELA / Math / Science); a support that applies to
    # several areas is listed under each. ELPAC keeps a single column.
    columns = [
        ('supports_caaspp_ela', 'CAASPP', 'ELA'),
        ('supports_caaspp_math', 'CAASPP', 'Math'),
        ('supports_caaspp_science', 'CAASPP', 'Science'),
        ('supports_elpac', 'ELPAC', None),
    ]
    column_tags = {tag for _key, tag, _subject in columns}

    def _option_subjects(option):
        return {
            (ast.subject or '').strip()
            for ast in option.assessments.all()
            if (ast.subject or '').strip()
        }

    def _direct_support_subjects(acc):
        subjects = set()
        for option in acc.options.all():
            subjects |= _option_subjects(option)
        return subjects

    rows = []
    for student in students:
        record = records.get(student.stu_annual_key)
        supports_by_col = {key: [] for key, _tag, _subject in columns}
        manual_keys_by_col = {key: set() for key, _tag, _subject in columns}

        def _add_support(raw_tag, subjects, label):
            tag = (raw_tag or '').strip().upper()
            label_key = label.casefold()
            subjects = {(s or '').strip() for s in (subjects or set()) if (s or '').strip()}
            for key, col_tag, col_subject in columns:
                if tag != col_tag:
                    continue
                # Unknown subject (no mapping) falls into every column for the tag.
                if col_subject and subjects and col_subject not in subjects:
                    continue
                if label_key in manual_keys_by_col[key]:
                    continue
                supports_by_col[key].append(label)
                manual_keys_by_col[key].add(label_key)

        if record:
            for option in record.options.all():
                prefix = f"{option.accommodation.name}: " if option.accommodation else ''
                _add_support(
                    getattr(option.accommodation, 'tag', '') if option.accommodation else '',
                    _option_subjects(option),
                    f"{prefix}{option.name}",
                )
            for acc in record.selected_accommodations.all():
                _add_support(acc.tag, _direct_support_subjects(acc), acc.name)

        seis_detail = seis_support_detail_for_student(student)
        seis_labels = seis_detail['labels']
        if filter_kind and not student_has_support_filter(record, seis_detail, filter_kind, filter_id):
            continue

        labels_by_tag = seis_detail.get('labels_by_tag') or {}
        label_subjects = seis_detail.get('label_subjects') or {}
        for tag in column_tags:
            for label in labels_by_tag.get(tag, []):
                _add_support(
                    tag,
                    set(label_subjects.get(label) or []),
                    f"{label} (SEIS)",
                )

        combined = []
        seen_combined = set()
        for key, _tag, _subject in columns:
            for label in supports_by_col[key]:
                if label.casefold() not in seen_combined:
                    seen_combined.add(label.casefold())
                    combined.append(label)

        completions = sorted(completed_map.get(student.stu_annual_key, []))
        updater = record.updated_by if record else None
        has_seis = bool(seis_labels)
        teacher_names = teacher_map.get(student.stu_annual_key, [])
        row = {
            'stu_annual_key': student.stu_annual_key,
            'ssid': student.ssid or '',
            'student_name': student.student_name or '',
            'grade': student.grade or '',
            'teachers': ', '.join(teacher_names),
            'supports': '; '.join(combined),
            'has_seis_supports': has_seis,
            'from_seis': 'Yes' if has_seis else 'No',
            'completions': ', '.join(completions),
            'updated_by': (updater.name or updater.email) if updater else '',
            'updated_at': record.updated_at.strftime('%Y-%m-%d %H:%M') if record and record.updated_at else '',
        }
        for key, _tag, _subject in columns:
            row[key] = '; '.join(supports_by_col[key])
        rows.append(row)

    return {'students': students, 'rows': rows, 'total': len(rows)}
