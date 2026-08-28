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
    return f"v3:{mtime}"


def _resolve_ssid(ssid: str) -> str:
    """If the provided ssid is actually a student_id, look up the real SSID."""
    if not ssid:
        return ssid
    from studata.models import CurrentStudent
    try:
        # Check if the identifier exists as an SSID
        if CurrentStudent.objects.filter(ssid=ssid).exists():
            return ssid
        # Fallback to student_id lookup
        match = CurrentStudent.objects.filter(student_id=ssid).first()
        if match and match.ssid:
            return match.ssid
    except Exception:
        pass
    return ssid


def _student_summary_cache_keys(ssid: str, user_id=None, embedded: bool = False):
    """Generate cache keys for student summary data."""
    ssid = _resolve_ssid(ssid)
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

    student_info = {
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
        'race_list': None,
        'dsusd_fep': None,
    }

    # Extract JSON add_data fields (AVID, Race List, DSUSD FEP)
    if cs_latest and getattr(cs_latest, 'add_data', None):
        try:
            import json
            add_data = cs_latest.add_data
            if isinstance(add_data, str):
                add_data = json.loads(add_data)
            if isinstance(add_data, dict):
                student_info['avid'] = add_data.get('AVID')
                student_info['avid_type'] = add_data.get('AVID_type')
                l_race = add_data.get('l_race')
                if isinstance(l_race, list):
                    student_info['race_list'] = ', '.join(str(r) for r in l_race if r is not None)
                elif l_race:
                    student_info['race_list'] = str(l_race)
                dsusd_fep = add_data.get('DSUSD_FEP', add_data.get('dsusd_fep'))
                if dsusd_fep is None:
                    student_info['dsusd_fep'] = None
                elif isinstance(dsusd_fep, list):
                    student_info['dsusd_fep'] = ', '.join(str(v) for v in dsusd_fep if v is not None)
                else:
                    student_info['dsusd_fep'] = str(dsusd_fep)
        except (json.JSONDecodeError, TypeError):
            pass

    return student_info


# Fields that identify a single test result for a student. Join-derived context
# (grade, school, school_short_name) is deliberately excluded — the `school`
# table can contain several rows per assessment_school_code and the summary
# should still show one row per test.
_TEST_IDENTITY_FIELDS = ('school_year', 'test_name', 'subject', 'test_window', 'pl', 'ss', 'school_code')


def _merge_other_info(rows_group: list) -> dict:
    """Merge a group's ``other_info`` dicts, keeping the first non-None value per key.

    Duplicate ``results`` rows for the same test/scores differ only in whether
    the additional-info JSON (DFP, writing domains, …) is populated. Unioning
    the dicts preserves every key (e.g. ``dfp``) from the row that carries it.
    """
    merged: dict = {}
    for row in rows_group:
        oi = row.get('other_info')
        if not isinstance(oi, dict):
            continue
        for k, v in oi.items():
            if v is None or k in merged:
                continue
            merged[k] = v
    return merged


def _dedupe_test_records(rows: list) -> list:
    """Collapse duplicate test-result rows for the summary page.

    The ``results`` table often stores the same student/test/scores twice — one
    row whose ``other_info`` carries the DFP/additional-info JSON and one (or
    more) without it — and the ``school`` join can fan a single result out to
    several rows. Rows sharing the same test identity are merged into a single
    record (keeping the first, with the unioned ``other_info``). Rows that
    genuinely differ (e.g. distinct iReady scale scores) are preserved.
    """
    if not rows:
        return rows
    groups: dict = {}
    order: list = []
    for row in rows:
        key = tuple(row.get(k) for k in _TEST_IDENTITY_FIELDS)
        if key in groups:
            groups[key].append(row)
        else:
            groups[key] = [row]
            order.append(key)
    for key in order:
        group = groups[key]
        if len(group) > 1:
            group[0]['other_info'] = _merge_other_info(group)
    return [groups[key][0] for key in order]


def _fetch_test_records_for_ssid(ssid: str):
    """Fetch all test records for a student from the database."""
    from clickhouse_utils.executor import QueryExecutor

    sql = """
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
    """
    result = QueryExecutor(analytics=True).execute(sql, [ssid])
    if result.get('error'):
        return []
    
    import json
    rows = result.get('rows', [])
    for row in rows:
        oi = row.get('other_info')
        if isinstance(oi, str) and oi.strip():
            try:
                row['other_info'] = json.loads(oi)
            except Exception:
                row['other_info'] = {}
        elif not isinstance(oi, dict):
            row['other_info'] = {}
    return _dedupe_test_records(rows)


def _fetch_student_marks_for_ssid(ssid: str):
    """Fetch all marks for a student from the database."""
    return list(StudentMarks.objects.filter(ssid=ssid)
                .order_by('-school_year', 'mark_period', 'course_name')
                .values())


def get_student_info_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get student info with caching."""
    ssid = _resolve_ssid(ssid)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_info'])
    if cached is not None:
        return cached
    student_info = _build_student_info_for_ssid(ssid)
    cache.set(keys['student_info'], student_info, timeout=timeout)
    return student_info


def get_test_records_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get test records with caching."""
    ssid = _resolve_ssid(ssid)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['test_records'])
    if cached is not None:
        return cached
    test_records = _fetch_test_records_for_ssid(ssid)
    cache.set(keys['test_records'], test_records, timeout=timeout)
    return test_records


def get_student_marks_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get student marks with caching."""
    ssid = _resolve_ssid(ssid)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_marks'])
    if cached is not None:
        return cached
    marks = _fetch_student_marks_for_ssid(ssid)
    cache.set(keys['student_marks'], marks, timeout=timeout)
    return marks


def get_student_summary_partial_html_cached(request, ssid: str, timeout: int = 300, embedded: bool = False):
    """Get rendered HTML for student summary with caching."""
    ssid = _resolve_ssid(ssid)
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
