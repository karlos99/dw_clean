"""Utilities for fetching and caching student data."""

from django.db import connections
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings
import os

from studata.models import CurrentStudent, StudentEnroll, School, StudentMarks


def _student_summary_cache_version():
    """Include template mtime so edits don't keep serving stale cached HTML."""
    try:
        template_path = settings.BASE_DIR / 'templates' / 'dataexploration' / 'partials' / 'student_test_summary_content.html'
        mtime = int(os.path.getmtime(template_path))
    except Exception:
        mtime = 0
    return f"v2:{mtime}"


def _student_summary_cache_keys(ssid: str, user_id=None, embedded: bool = False):
    """Generate cache keys for student summary data."""
    v = _student_summary_cache_version()
    user_part = f"u{user_id}:" if user_id else ""
    return {
        'student_info': f"dataexp:student_summary:{v}:{user_part}student_info:{ssid}",
        'test_records': f"dataexp:student_summary:{v}:{user_part}test_records:{ssid}",
        'student_marks': f"dataexp:student_summary:{v}:{user_part}student_marks:{ssid}",
        'partial_html': f"dataexp:student_summary:{v}:{user_part}partial_html:{'embedded' if embedded else 'full'}:{ssid}",
    }


def _build_student_info_for_ssid(ssid: str):
    """Build comprehensive student info dictionary from CurrentStudent and StudentEnroll."""
    cs_latest = (
        CurrentStudent.objects.filter(ssid=ssid)
        .order_by('-school_year')
        .first()
    )
    se_latest = (
        StudentEnroll.objects.filter(ssid=ssid)
        .order_by('-school_year', '-enter_date')
        .first()
    )

    school_short_name = None
    school_state_id = getattr(cs_latest, 'school_state_id', None) if cs_latest else None
    if school_state_id:
        school_obj = School.objects.filter(state_id=school_state_id).first()
        school_short_name = getattr(school_obj, 'short_name', None) if school_obj else None

    full_name = None
    if se_latest and (se_latest.first_name or se_latest.last_name or se_latest.middle_name):
        full_name = se_latest.full_name
    elif cs_latest and getattr(cs_latest, 'student_name', None):
        full_name = cs_latest.student_name

    return {
        'full_name': full_name,
        'student_id': getattr(cs_latest, 'student_id', None) if cs_latest else None,
        'ssid': getattr(cs_latest, 'ssid', None) if cs_latest else ssid,
        'grade': getattr(cs_latest, 'grade', None) if cs_latest else None,
        'gender': getattr(cs_latest, 'gender', None) if cs_latest else None,
        'race': getattr(cs_latest, 'race', None) if cs_latest else None,
        'birth_date': getattr(se_latest, 'birth_date', None) if se_latest else None,
        'school': getattr(cs_latest, 'school', None) if cs_latest else None,
        'school_state_id': school_state_id,
        'school_short_name': school_short_name,
        'school_year': getattr(cs_latest, 'school_year', None) if cs_latest else None,
        'status': getattr(cs_latest, 'status', None) if cs_latest else None,
        'atendace_rate': getattr(cs_latest, 'atendace_rate', None) if cs_latest else None,
        'instructional_setting': getattr(se_latest, 'instructional_setting', None) if se_latest else None,
        'ell_status': getattr(se_latest, 'ell_status', None) if se_latest else None,
        'ltel': getattr(cs_latest, 'ltel', None) if cs_latest else None,
        'fep_date': getattr(se_latest, 'fep_date', None) if se_latest else None,
        'us_enter_date': getattr(se_latest, 'us_enter_date', None) if se_latest else None,
        'state_enter_date': getattr(se_latest, 'state_enter_date', None) if se_latest else None,
        'special_program_code': getattr(se_latest, 'special_program_code', None) if se_latest else None,
        'sped': getattr(cs_latest, 'sped', None) if cs_latest else None,
        'in_504': getattr(cs_latest, 'in_504', None) if cs_latest else None,
        'gate': getattr(cs_latest, 'gate', None) if cs_latest else None,
        'sed': getattr(cs_latest, 'sed', None) if cs_latest else None,
        'foster': getattr(cs_latest, 'foster', None) if cs_latest else None,
        'homeless': getattr(cs_latest, 'homeless', None) if cs_latest else None,
        'migrant': getattr(se_latest, 'migrant', None) if se_latest else None,
        'dli': getattr(cs_latest, 'dli', None) if cs_latest else None,
        'suspension_indicator': getattr(cs_latest, 'suspension_indicator', None) if cs_latest else None,
        'avid': None,
        'avid_type': None,
    }

    # Extract AVID info from add_data JSON
    if cs_latest and getattr(cs_latest, 'add_data', None):
        try:
            import json
            add_data = json.loads(cs_latest.add_data)
            student_info['avid'] = add_data.get('AVID')
            student_info['avid_type'] = add_data.get('AVID_type')
        except (json.JSONDecodeError, TypeError):
            pass


def _fetch_test_records_for_ssid(ssid: str):
    """Fetch all test records for a student from the database."""
    with connections['default'].cursor() as cursor:
        cursor.execute(
            """
            SELECT
                r.school_year AS school_year,
                t.test_name AS test_name,
                t.subject AS subject,
                t.test_window AS test_window,
                cs.grade AS grade,
                cs.school AS school,
                cs.school_state_id AS school_state_id,
                sch.short_name AS school_short_name,
                r.pl AS pl,
                r.ss AS ss,
                r.school_code AS school_code,
                r.other_info AS other_info
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            LEFT JOIN current_student cs
                ON cs.ssid = r.ssid AND cs.school_year = r.school_year
            LEFT JOIN school sch
                ON sch.assessment_school_code = r.school_code
            WHERE r.ssid = %s
            ORDER BY r.school_year DESC, t.test_name, t.subject
            """,
            [ssid],
        )
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _fetch_student_marks_for_ssid(ssid: str):
    """Fetch all marks for a student from the database."""
    return list(StudentMarks.objects.filter(ssid=ssid)
                .order_by('-school_year', 'mark_period', 'course_name')
                .values())


def get_student_info_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get student info with caching."""
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_info'])
    if cached is not None:
        return cached
    student_info = _build_student_info_for_ssid(ssid)
    cache.set(keys['student_info'], student_info, timeout=timeout)
    return student_info


def get_test_records_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get test records with caching."""
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['test_records'])
    if cached is not None:
        return cached
    test_records = _fetch_test_records_for_ssid(ssid)
    cache.set(keys['test_records'], test_records, timeout=timeout)
    return test_records


def get_student_marks_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get student marks with caching."""
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_marks'])
    if cached is not None:
        return cached
    marks = _fetch_student_marks_for_ssid(ssid)
    cache.set(keys['student_marks'], marks, timeout=timeout)
    return marks


def get_student_summary_partial_html_cached(request, ssid: str, timeout: int = 300, embedded: bool = False):
    """Get rendered HTML for student summary with caching."""
    user_id = getattr(getattr(request, 'user', None), 'pk', None)
    keys = _student_summary_cache_keys(ssid, user_id=user_id, embedded=embedded)
    cached = cache.get(keys['partial_html'])
    if cached is not None:
        return cached

    context = {
        'ssid': ssid,
        'student_info': get_student_info_cached(ssid, timeout=timeout, user_id=user_id),
        'student_name': get_student_info_cached(ssid, timeout=timeout, user_id=user_id).get('full_name'),
        'test_records': get_test_records_cached(ssid, timeout=timeout, user_id=user_id),
        'student_marks': get_student_marks_cached(ssid, timeout=timeout, user_id=user_id),
        'embedded': embedded,
    }
    html = render_to_string('dataexploration/partials/student_test_summary_content.html', context, request=request)
    cache.set(keys['partial_html'], html, timeout=timeout)
    return html


def get_current_student_columns():
    """Fetch current_student columns from the DB (cached)."""
    columns_key = "dataexp:current_student_columns"
    columns = cache.get(columns_key)
    if not columns:
        with connections['default'].cursor() as cursor:
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'current_student'
                  AND table_schema = 'public'
            """)
            columns = [row[0] for row in cursor.fetchall()]
            cache.set(columns_key, columns, 3600)
    return set(columns)


def get_student_enroll_columns():
    """Fetch student_enroll columns from the DB (cached)."""
    columns_key = "dataexp:student_enroll_columns"
    columns = cache.get(columns_key)
    if not columns:
        with connections['default'].cursor() as cursor:
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'student_enroll'
                  AND table_schema = 'public'
            """)
            columns = [row[0] for row in cursor.fetchall()]
            cache.set(columns_key, columns, 3600)
    return set(columns)
