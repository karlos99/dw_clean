"""Column management utilities and views."""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST

from ..models import DefaultColumnSelection
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from ..column_utils import get_test_columns, get_mark_columns
from ..survey_utils import get_survey_columns
from ..utils import get_user_roles


def get_saved_default_columns():
    """Get saved default columns or fall back to constants."""
    selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
    if selection and selection.columns:
        return selection.columns
    return DEFAULT_COLUMNS


def get_column_expression(col, db_columns, enroll_columns=None, use_enroll=False, use_students=False, use_eht=False):
    """Generate SQL expression for a column based on available tables."""
    if col == 'elem_teacher_name' and use_eht:
        return "eht.elem_teacher_name", "elem_teacher_name"
    if col == 'ltel' and use_students:
        return "COALESCE(s.ltel, sssid.ltel)", "ltel"
    if col == 'ell':
        ell_candidates = []
        if 'ell' in db_columns:
            ell_candidates.append("cs.ell")
        if use_enroll and enroll_columns:
            if 'ell_status' in enroll_columns:
                ell_candidates.append("se.ell_status")
        if ell_candidates:
            return f"COALESCE({', '.join(ell_candidates)})", "ell"
        return None, None
    if col == 'atendace_rate' and 'attendance_rate' in db_columns:
        return "cs.attendance_rate", "atendace_rate"
    if col in {'birth_date', 'us_enter_date', 'fep_date'}:
        if col in db_columns:
            return f"cs.{col}", col
        if use_enroll and enroll_columns and col in enroll_columns:
            return f"se.{col}", col
        return None, None
    if col == 'migrant':
        if col in db_columns:
            return "cs.migrant", "migrant"
        if use_enroll and enroll_columns and col in enroll_columns:
            return "se.migrant", "migrant"
        return None, None
    if col == 'avid':
        return "(cs.add_data::jsonb->>'AVID')", "avid"
    if col == 'avid_type':
        return "(cs.add_data::jsonb->>'AVID_type')", "avid_type"
    if col in db_columns:
        return f"cs.{col}", col
    return None, None


def user_can_manage_defaults(user):
    """Check if user has permission to manage default columns."""
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    user_roles = get_user_roles(user)
    return bool({'Project Admin', 'District Admin'} & user_roles)


@login_required
def manage_default_columns(request):
    """Admin interface for setting default columns shown to users."""
    if not user_can_manage_defaults(request.user):
        return HttpResponseForbidden("You don't have permission to manage default columns.")

    test_structure = get_test_columns()
    default_columns = get_saved_default_columns()

    if request.method == 'POST':
        import json
        columns_json = request.POST.get('columns', '[]')
        try:
            columns = json.loads(columns_json)
            selection, created = DefaultColumnSelection.objects.update_or_create(
                id=1,
                defaults={'columns': columns}
            )
            return render(request, 'dataexploration/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': columns,
                'success': True,
            })
        except Exception as e:
            return render(request, 'dataexploration/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': default_columns,
                'error': str(e),
            })

    context = {
        'column_categories': COLUMN_CATEGORIES,
        'column_definitions': COLUMN_DEFINITIONS,
        'test_structure': test_structure,
        'selected_columns': default_columns,
    }

    return render(request, 'dataexploration/manage_default_columns.html', context)


def load_column_structures(request):
    """Load dynamic column structures for the selector via HTMX."""
    import json
    
    filters_json = request.GET.get('column_filters')
    column_filters = None
    if filters_json:
        try:
            column_filters = json.loads(filters_json)
        except json.JSONDecodeError:
            pass

    # Build school filtering based on user permissions
    where_clauses = []
    params = []
    user = request.user
    
    user_roles = get_user_roles(user)
    unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
    is_unrestricted = user.is_superuser or bool(user_roles & unrestricted_roles)
    
    if hasattr(user, 'active_school') and user.active_school:
        where_clauses.append("cs.school_state_id = %s")
        params.append(user.active_school.state_id)
    else:
        if not is_unrestricted:
            if hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
                where_clauses.append("cs.school_state_id = ANY(%s)")
                params.append(user.schools)
            else:
                where_clauses.append("1=0")

    test_structure = get_test_columns(
        column_filters=column_filters,
        extra_where_clauses=where_clauses,
        extra_params=params
    )
    marks_structure = get_mark_columns(
        column_filters=column_filters,
        extra_where_clauses=where_clauses,
        extra_params=params
    )
    survey_structure = get_survey_columns()
    
    context = {
        'test_structure': test_structure,
        'marks_structure': marks_structure,
        'survey_structure': survey_structure.get('structure', {}),
        'column_definitions': COLUMN_DEFINITIONS,
    }
    
    return render(request, 'dataexploration/partials/column_structures.html', context)
