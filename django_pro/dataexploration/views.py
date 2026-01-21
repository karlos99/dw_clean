"""Views for data exploration app."""

from django.shortcuts import render
from django.http import JsonResponse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db import connections
from django.core.cache import cache
import json

from .models import StudentWithTests
from studata.models import CurrentStudent
from .column_utils import get_test_columns
from .constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS


def _get_current_student_columns():
    """Fetch current_student columns from the DB (cached)."""
    columns_key = "dataexp:current_student_columns"
    columns = cache.get(columns_key)
    if not columns:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'current_student'"
            )
            columns = [row[0] for row in cursor.fetchall()]
            cache.set(columns_key, columns, 3600)
    return set(columns)


class StudentWithTestsListView(LoginRequiredMixin, ListView):
    """Display list of students with their test results (one row per student)."""
    model = CurrentStudent
    template_name = 'dataexploration/student_with_tests_list.html'
    context_object_name = 'students'
    paginate_by = 50

    def get_context_data(self, **kwargs):
        """Add additional context for filters and column selection."""
        context = super().get_context_data(**kwargs)
        
        # Get dynamic test columns
        test_structure = get_test_columns()
        
        # Get unique values for filters using cache
        school_years_key = "dataexp:school_years"
        school_years = cache.get(school_years_key)
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT school_year FROM current_student WHERE school_year IS NOT NULL ORDER BY school_year DESC")
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set(school_years_key, school_years, 3600)
        
        grades_key = "dataexp:grades"
        grades = cache.get(grades_key)
        if not grades:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT grade FROM current_student WHERE grade IS NOT NULL ORDER BY grade")
                grades = [row[0] for row in cursor.fetchall()]
                cache.set(grades_key, grades, 3600)
        
        context['school_years'] = school_years
        context['grades'] = grades
        context['test_structure'] = test_structure
        context['column_categories'] = COLUMN_CATEGORIES
        context['column_definitions'] = COLUMN_DEFINITIONS
        context['default_columns'] = DEFAULT_COLUMNS
        context['default_columns_json'] = json.dumps(DEFAULT_COLUMNS)
        context['column_definitions_json'] = json.dumps(COLUMN_DEFINITIONS)
        
        # Current filter values
        context['search_query'] = self.request.GET.get('search', '')
        context['selected_school_year'] = self.request.GET.get('school_year', '')
        context['selected_grade'] = self.request.GET.get('grade', '')
        
        return context


def load_students(request):
    """Load student data with dynamic test columns via HTMX."""
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 50))
    
    # Get selected columns
    selected_columns_json = request.GET.get('columns', '')
    if selected_columns_json:
        try:
            selected_columns = json.loads(selected_columns_json)
        except:
            selected_columns = DEFAULT_COLUMNS
    else:
        selected_columns = DEFAULT_COLUMNS
    
    # Get filters
    search = request.GET.get('search', '')
    school_year = request.GET.get('school_year', '')
    grade = request.GET.get('grade', '')
    
    with connections['default'].cursor() as cursor:
        db_columns = _get_current_student_columns()
        where_clauses = ["1=1"]
        params = []
        
        # Apply filters
        if search:
            where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
            search_param = f'%{search}%'
            params.extend([search_param, search_param, search_param])
        
        if school_year:
            where_clauses.append("cs.school_year = %s")
            params.append(school_year)
        
        if grade:
            where_clauses.append("cs.grade = %s")
            params.append(grade)
        
        where_sql = " AND ".join(where_clauses)
        
        # Count total
        count_query = f"""
            SELECT COUNT(DISTINCT cs.ssid)
            FROM current_student cs
            WHERE {where_sql}
        """
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]
        
        # Get paginated results
        offset = (page - 1) * limit
        
        # Parse column requests - separate base columns from test columns
        base_cols = []
        test_cols_info = []
        
        for col in selected_columns:
            if col.startswith('test_'):
                # Parse test column: test_YEAR_TESTNAME_SUBJECT_METRIC
                col_normalized = col.replace('-', '_')
                parts = col_normalized.replace('test_', '').rsplit('_', 1)
                if len(parts) == 2:
                    test_part, metric = parts
                    # Split year from test name and subject
                    test_parts = test_part.split('_', 1)
                    if len(test_parts) >= 2:
                        year_normalized = test_parts[0]
                        year = year_normalized.replace('_', '-')
                        remainder = test_parts[1].rsplit('_', 1)
                        if len(remainder) == 2:
                            test_name = remainder[0].replace('_', ' ')
                            subject = remainder[1].replace('_', ' ')
                            metric_col = 'ss' if metric == 'SS' else 'pl'
                            test_cols_info.append({
                                'col_name': col_normalized,
                                'year': year,
                                'test_name': test_name,
                                'subject': subject,
                                'metric': metric_col
                            })
            elif col in COLUMN_DEFINITIONS:
                base_cols.append(col)
        
        # Always include ssid for joining
        if 'ssid' not in base_cols:
            base_cols.insert(0, 'ssid')

        # Build base column SELECT with safe mapping
        base_col_selects = []
        for col in base_cols:
            if col in db_columns:
                base_col_selects.append(f"cs.{col}")
            elif col == 'atendace_rate' and 'attendance_rate' in db_columns:
                base_col_selects.append("cs.attendance_rate AS atendace_rate")

        if not base_col_selects:
            base_col_selects.append("cs.ssid") if 'ssid' in db_columns else base_col_selects.append("NULL AS ssid")

        base_col_sql = ", ".join(base_col_selects)
        
        # Build query with base columns
        query = f"""
        SELECT {base_col_sql}
        FROM current_student cs
        WHERE {where_sql}
        ORDER BY cs.student_name
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        cursor.execute(query, params)
        
        columns = [desc[0] for desc in cursor.description]
        students = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        student_ids = [s['ssid'] for s in students]
        
        # Fetch test data in bulk for selected test columns
        if test_cols_info and student_ids:
            for test_info in test_cols_info:
                metric_col = test_info['metric']
                test_name_db = test_info['test_name'].replace(' ', '_')
                subject_db = test_info['subject'].replace(' ', '_')
                
                query = f"""
                    SELECT r.local_student_id, r.{metric_col}
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE r.local_student_id = ANY(%s)
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                """
                cursor.execute(query, [student_ids, test_info['year'], 
                                     f"%{test_info['test_name']}%", 
                                     f"%{test_info['subject']}%"])
                
                test_data = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Add test data to each student
                for student in students:
                    student[test_info['col_name']] = test_data.get(student['ssid'], '—')
    
    # Convert students list to JSON for Alpine.js
    students_json = [json.dumps(student) for student in students]
    
    total_pages = (total + limit - 1) // limit

    if request.GET.get('format') == 'json':
        return JsonResponse({
            'students': students,
            'selected_columns': selected_columns,
            'total': total,
            'page': page,
            'total_pages': total_pages,
            'column_definitions': COLUMN_DEFINITIONS,
        })

    context = {
        'students': students,
        'students_json': students_json,
        'selected_columns': selected_columns,
        'total': total,
        'page': page,
        'total_pages': total_pages,
        'column_definitions': COLUMN_DEFINITIONS,
    }

    return render(request, 'dataexploration/partials/student_table.html', context)


def load_column_structures(request):
    """Load dynamic column structures based on current filters via HTMX."""
    # Get column filters if any
    column_filters_json = request.GET.get('column_filters', '')
    column_filters = {}
    if column_filters_json:
        try:
            column_filters = json.loads(column_filters_json)
        except:
            pass
    
    # Get dynamic test columns with filters
    test_structure = get_test_columns(column_filters)
    
    context = {
        'test_structure': test_structure,
        'column_definitions': COLUMN_DEFINITIONS,
    }
    
    return render(request, 'dataexploration/partials/column_structures.html', context)


class StudentWithTestsDetailView(LoginRequiredMixin, DetailView):
    """Detail view for an individual student's test record."""
    model = StudentWithTests
    template_name = 'dataexploration/student_with_tests_detail.html'
    context_object_name = 'student_test'


class StudentTestSummaryView(LoginRequiredMixin, ListView):
    """Summary view showing all tests for a specific student (by SSID)."""
    model = StudentWithTests
    template_name = 'dataexploration/student_test_summary.html'
    context_object_name = 'tests'
    paginate_by = 100

    def get_queryset(self):
        ssid = self.kwargs.get('ssid')
        return StudentWithTests.objects.filter(ssid=ssid).order_by('school_year', 'test_name', 'subject')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ssid = self.kwargs.get('ssid')
        context['ssid'] = ssid
        if self.object_list.exists():
            context['student_name'] = self.object_list.first().full_name
        return context
