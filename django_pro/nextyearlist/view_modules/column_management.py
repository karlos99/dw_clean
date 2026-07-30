"""Column management utilities and views for Next Year List."""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

from dataexploration.models import DefaultColumnSelection
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from ..column_utils import get_nyl_test_columns, get_nyl_mark_columns
from ..utils import get_user_roles


def get_saved_default_columns():
    """Get saved default columns for NYL, falling back to NYL-specific defaults."""
    # NYL shares DefaultColumnSelection with dataexploration; if a specific NYL
    # record keyed at id=2 exists, use that, otherwise fall back to constants.
    selection = DefaultColumnSelection.objects.filter(id=2).first()
    if selection and selection.columns:
        return selection.columns
    return DEFAULT_COLUMNS


def get_column_expression(col, db_columns, enroll_columns=None, use_enroll=False, use_students=False, use_eht=False):
    """Generate SQL expression for a column including NYL-specific columns."""
    # NYL-specific columns
    if col == 'next_year_school_state_id':
        return 'nyl.next_year_school_state_id', 'next_year_school_state_id'
    if col == 'next_year_school_name':
        return 'nyl.next_year_school_name', 'next_year_school_name'
    if col == 'math_selection':
        return 'mr.math_selection', 'math_selection'

    # ltel is a direct column on current_student in this schema; use cs.ltel
    # to avoid requiring the students table join, which is not available in NYL queries.
    if col == 'ltel' and db_columns and 'ltel' in db_columns:
        return 'cs.ltel', 'ltel'

    # Delegate to dataexploration for all other columns
    from dataexploration.view_modules.column_management import get_column_expression as _base
    return _base(col, db_columns, enroll_columns, use_enroll, use_students, use_eht)


@login_required
def load_column_structures(request):
    """Return dynamic column structures for the column selector via HTMX."""
    import json
    
    filters_json = request.GET.get('column_filters')
    column_filters = None
    if filters_json:
        try:
            column_filters = json.loads(filters_json)
        except json.JSONDecodeError:
            pass

    test_structure = get_nyl_test_columns(user=request.user, column_filters=column_filters)
    marks_structure = get_nyl_mark_columns(user=request.user, column_filters=column_filters)

    context = {
        'test_structure': test_structure,
        'marks_structure': marks_structure,
        'column_definitions': COLUMN_DEFINITIONS,
    }
    return render(request, 'nextyearlist/partials/column_structures.html', context)
