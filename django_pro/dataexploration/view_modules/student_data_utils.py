"""Utilities for fetching and caching student data."""

from django.db import connections
from django.db.models import Q
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings
import json
import math
import os
import re

from studata.models import CurrentStudent, StudentEnroll, School, StudentMarks


def _student_summary_cache_version():
    """Include template mtimes so edits don't keep serving stale cached HTML."""
    base = settings.BASE_DIR / 'templates' / 'dataexploration' / 'partials'
    mtimes = []
    for name in (
        'student_test_summary_content.html', 'student_details.html',
        'student_orbit_header.html', 'student_requirement_table.html',
    ):
        try:
            mtimes.append(str(int(os.path.getmtime(base / name))))
        except Exception:
            mtimes.append('0')
    # Include the configured summary-section toggles so turning a section on/off
    # takes effect immediately instead of after the cache TTL.
    try:
        from landing.utils import summary_sections_version
        sections = summary_sections_version()
    except Exception:
        sections = ''
    return 'v7:' + ':'.join(mtimes) + ':' + sections


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
        'student_cte_details': f"dataexp:student_summary:{v}:{user_part}student_cte_details:{ssid}",
        'student_details': f"dataexp:student_summary:{v}:{user_part}student_details:{ssid}",
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


_PERIOD_RANK = {
    'P1': 0, 'P2': 1, 'P3': 2, 'P4': 3, 'P5': 4, 'P6': 5,
    'Q1': 10, 'Q2': 11, 'Q3': 12, 'Q4': 13,
    'S1': 20, 'S2': 21, 'SEM1': 20, 'SEM2': 21,
    'T1': 30, 'T2': 31, 'T3': 32,
    'TR1': 40, 'TR2': 41, 'TR3': 42,
    'FINAL': 98,
    'LIVE': 99,
}


def _period_sort_key(period: str):
    p = (period or '').strip()
    rank = _PERIOD_RANK.get(p.upper())
    if rank is None:
        return (80, p.lower())
    return (rank, p.lower())


def _is_progress_period(period):
    """True for in-progress/live report periods that should not be displayed.

    The Grades grid shows only semester/final marks, not the live (current)
    grade or progress-report periods.
    """
    text = str(period or '').strip().lower()
    return text == 'live' or 'progress' in text


def _mark_tone(mark):
    if mark is None or str(mark).strip() == '':
        return ''
    m = str(mark).strip().upper()
    if m in ('A+', 'A', 'A-', '4', 'O'):
        return 'green'
    if m in ('B+', 'B', 'B-', '3', 'S'):
        return 'blue'
    if m in ('C+', 'C', 'C-', '2', 'N'):
        return 'yellow'
    if m in ('D+', 'D', 'D-', 'F', '1', 'U'):
        return 'red'
    return 'slate'


def filter_display_marks(marks: list) -> list:
    """Summary marks: drop live/progress periods so only semester grades show."""
    return [m for m in (marks or []) if not _is_progress_period(m.get('mark_period'))]


def pivot_student_marks(marks: list) -> list:
    """Turn flat mark rows into year → class rows × marking-period columns."""
    if not marks:
        return []
    years = {}
    year_order = []
    for m in marks:
        year = m.get('school_year') or ''
        if year not in years:
            years[year] = {'periods': set(), 'courses': {}}
            year_order.append(year)
        course_key = (m.get('subject') or '', m.get('course_name') or '')
        period = (m.get('mark_period') or '').strip() or '—'
        bucket = years[year]
        course = bucket['courses'].get(course_key)
        if course is None:
            course = {
                'subject': m.get('subject') or '',
                'course_name': m.get('course_name') or '',
                'staff_name': m.get('staff_name') or '',
                'by_period': {},
            }
            bucket['courses'][course_key] = course
        # Show only semester/final grades — the live (in-progress) grade is hidden.
        if _is_progress_period(period):
            if not course['staff_name'] and m.get('staff_name'):
                course['staff_name'] = m.get('staff_name')
            continue
        bucket['periods'].add(period)
        if m.get('staff_name') and (period.lower() == 'live' or not course['staff_name']):
            course['staff_name'] = m.get('staff_name')
        existing = course['by_period'].get(period)
        if existing is None or m.get('mark'):
            course['by_period'][period] = m.get('mark')

    grid = []
    for year in year_order:
        bucket = years[year]
        periods = sorted(bucket['periods'], key=_period_sort_key)
        courses = []
        for course in bucket['courses'].values():
            cells = []
            for p in periods:
                mark = course['by_period'].get(p)
                cells.append({'period': p, 'mark': mark, 'tone': _mark_tone(mark)})
            courses.append({
                'subject': course['subject'],
                'course_name': course['course_name'],
                'staff_name': course['staff_name'],
                'cells': cells,
            })
        grid.append({
            'school_year': year,
            'periods': periods,
            'courses': courses,
        })
    return grid


def _fetch_student_marks_for_ssid(ssid: str):
    """Fetch all marks for a student from live mark_data (not the matview)."""
    from ..column_utils import _fetch_marks_live_from_alt

    ssid = _resolve_ssid(ssid)
    cs = (
        CurrentStudent.objects.filter(ssid=ssid)
        .order_by('-school_year')
        .first()
    )
    student_id = getattr(cs, 'student_id', None) if cs else None
    years = list(
        CurrentStudent.objects.filter(ssid=ssid)
        .values_list('school_year', flat=True)
        .distinct()
    )
    if not years:
        years = list(
            StudentMarks.objects.filter(ssid=ssid)
            .values_list('school_year', flat=True)
            .distinct()
        )
    live_rows = _fetch_marks_live_from_alt([ssid], [student_id] if student_id else [], years or None)
    marks = []
    for row in live_rows:
        marks.append({
            'ssid': row.get('ssid'),
            'student_id': row.get('student_id'),
            'school_year': row.get('school_year'),
            'subject': row.get('subject'),
            'course_name': row.get('course_name'),
            'mark_period': row.get('mark_period'),
            'mark': row.get('mark'),
            'staff_name': row.get('staff_name'),
        })
    def _sort_key(m):
        p = str(m.get('mark_period') or '').strip()
        is_live = 1 if p.lower() == 'live' else 0
        sy = str(m.get('school_year') or '')
        sy_prefix = -int(sy[:4]) if sy[:4].isdigit() else 0
        return (sy_prefix, m.get('subject') or '', m.get('course_name') or '', is_live, p)
    marks.sort(key=_sort_key)
    return marks


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


def _student_year_label(school_year):
    """Normalize '2026-2027' / '2026' to a 4-digit year label."""
    text = str(school_year or '').strip()
    return text[:4] if text[:4].isdigit() else text


def _cte_requirements(raw):
    """Turn ``completer_requirements_json`` into an ordered display list."""
    if not isinstance(raw, dict):
        return []
    out = []
    for key, label in (('concentrator', 'Concentrator'), ('capstone', 'Capstone')):
        node = raw.get(key)
        if not isinstance(node, dict):
            continue
        status = (node.get('status') or '').strip()
        if not status and 'credits_threshold_met' in node:
            status = 'Met' if node.get('credits_threshold_met') else 'Not Met'
        out.append({
            'key': key,
            'label': label,
            'status': status,
            'requirement': (node.get('requirement') or '').strip(),
            'justification': (node.get('justification') or '').strip(),
            'qualifying_courses': (node.get('qualifying_courses') or '').strip(),
            'total_credits': node.get('total_credits_earned'),
            'courses': node.get('course_list') or [],
        })
    return out


def _cte_history_columns(history):
    """Group ``course_history_json`` rows into ordered CTE stage columns."""
    from coursecatalog.cte_pathways import CTE_STAGE_BY_LOWER, CTE_STAGE_ORDER, cte_stage_year

    buckets = {}
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            level = (item.get('cte_level') or '').strip() or 'Other'
            credits = item.get('credits_earned')
            try:
                credits = float(credits) if credits not in (None, '') else None
            except (TypeError, ValueError):
                credits = None
            buckets.setdefault(level, []).append({
                'name': (item.get('course') or '').strip(),
                'year': item.get('year'),
                's1': (item.get('eff_s1') or '').strip(),
                's2': (item.get('eff_s2') or '').strip(),
                'credits': credits,
                'status': (item.get('completion_status') or '').strip(),
            })

    def _level_sort(level):
        canonical = CTE_STAGE_BY_LOWER.get(level.lower())
        if canonical:
            return (CTE_STAGE_ORDER.index(canonical), '')
        return (len(CTE_STAGE_ORDER), level.lower())

    columns = []
    for level in sorted(buckets.keys(), key=_level_sort):
        columns.append({
            'level': level,
            'year': cte_stage_year(level),
            'courses': buckets[level],
        })
    return columns


def _build_cte_pathway(row):
    columns = _cte_history_columns(row.course_history_json)
    total_credits = 0.0
    for col in columns:
        for course in col['courses']:
            total_credits += course['credits'] or 0.0
    status = (row.pathway_status or '').strip()
    return {
        'school_year': (row.school_year or '').strip(),
        'cte_pathway': (row.cte_pathway or '').strip(),
        'pathway_code': (row.pathway_code or '').strip(),
        'pathway_code_name': (row.pathway_code_name or '').strip(),
        'industry_sector': (row.industry_sector or '').strip(),
        'pathway_status': status,
        'is_completer': status.lower() == 'cte completer',
        'columns': columns,
        'requirements': _cte_requirements(row.completer_requirements_json),
        'total_credits': total_credits,
        'course_count': sum(len(c['courses']) for c in columns),
    }


def _fetch_student_cte_details(ssid: str, current_school_year: str = ''):
    """Per-student CTE details: current-year pathways + past completers."""
    from coursecatalog.models import CteDetailEntry

    ssid = _resolve_ssid(ssid)
    cs = CurrentStudent.objects.filter(ssid=ssid).order_by('-school_year').first()
    student_id = getattr(cs, 'student_id', None) if cs else None
    if not current_school_year:
        current_school_year = getattr(cs, 'school_year', '') if cs else ''
    current_label = _student_year_label(current_school_year)

    query = Q(state_student_id=ssid)
    if student_id:
        query |= Q(sis_number=student_id)

    try:
        rows = list(CteDetailEntry.objects.filter(query).order_by('school_year'))
    except Exception:
        return {'current': [], 'past_completers': [], 'has_any': False}
    if not rows:
        return {'current': [], 'past_completers': [], 'has_any': False}

    def _year_num(row):
        try:
            return int(_student_year_label(row.school_year))
        except (TypeError, ValueError):
            return -1

    effective_label = current_label
    if not effective_label:
        effective_label = _student_year_label(max(rows, key=_year_num).school_year)
    current_rows = [r for r in rows if _student_year_label(r.school_year) == effective_label]

    current = sorted(
        (_build_cte_pathway(r) for r in current_rows),
        key=lambda p: p['cte_pathway'].lower(),
    )

    past = []
    for row in rows:
        label = _student_year_label(row.school_year)
        if label == effective_label:
            continue
        status = (row.pathway_status or '').strip()
        if status.lower() != 'cte completer':
            continue
        past.append({
            'school_year': label,
            'cte_pathway': (row.cte_pathway or '').strip(),
            'industry_sector': (row.industry_sector or '').strip(),
            'pathway_status': status,
        })
    past.sort(key=lambda p: (p['school_year'], p['cte_pathway'].lower()))

    return {
        'current': current,
        'past_completers': past,
        'has_any': bool(current or past),
    }


def get_student_cte_details_cached(
    ssid: str, timeout: int = 300, user_id=None, current_school_year: str = '',
):
    """Get the student's CTE details with caching."""
    ssid = _resolve_ssid(ssid)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_cte_details'])
    if cached is not None:
        return cached
    details = _fetch_student_cte_details(ssid, current_school_year=current_school_year)
    cache.set(keys['student_cte_details'], details, timeout=timeout)
    return details


_TERM_LABELS = {
    'SEM1': 'Semester 1', 'SEM01': 'Semester 1', 'S1': 'Semester 1',
    'SEM2': 'Semester 2', 'SEM02': 'Semester 2', 'S2': 'Semester 2',
}
_TERM_SORT = {'SEM1': 0, 'SEM01': 0, 'S1': 0, 'SEM2': 1, 'SEM02': 1, 'S2': 1}


def _to_float(value):
    try:
        if value in (None, ''):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_gpa(value):
    number = _to_float(value)
    return None if number is None else f"{number:.2f}"


def _clean_year(value):
    """Render a graduation year without a trailing decimal (e.g. '2026.0' -> '2026')."""
    return re.sub(r'\.0+$', '', str(value or '').strip())


# Current academic recognition for high-school students, by unweighted GPA.
_ACADEMIC_RECOGNITION = (
    (4.0, 'Summa Cum Laude'),
    (3.75, 'Magna Cum Laude'),
    (3.5, 'Cum Laude'),
)


def _high_school_grade_number(grade):
    """Return 9-12 for a high-school grade, else None."""
    match = re.search(r'\d+', str(grade or ''))
    if not match:
        return None
    number = int(match.group())
    return number if 9 <= number <= 12 else None


def _is_high_school_grade(grade):
    """True for grades 9-12 (accepts '9', '09', 'Grade 11', …)."""
    return _high_school_grade_number(grade) is not None


def _academic_recognition(unweighted_gpa):
    """Academic recognition label from an unweighted GPA, else ''."""
    gpa = _to_float(unweighted_gpa)
    if gpa is None:
        return ''
    for threshold, label in _ACADEMIC_RECOGNITION:
        if gpa >= threshold:
            return label
    return ''


def _normalize_term(term_code):
    code = str(term_code or '').strip()
    return _TERM_LABELS.get(code.upper(), code)


def _term_sort_key(term_code):
    code = str(term_code or '').strip().upper()
    return (_TERM_SORT.get(code, 99), code)


_MARK_RE = re.compile(r'^\s*([A-Za-z])([+-]?)')


def _normalize_mark(mark):
    """Reduce a raw mark to a single letter plus an optional +/-.

    The source appends status suffixes (e.g. ``AW``, ``A-W``, ``FR``, ``PC``);
    only the leading grade letter and its sign are meaningful for display.
    """
    match = _MARK_RE.match(str(mark or '').strip())
    if not match:
        return ''
    return match.group(1).upper() + match.group(2)


# CSU A-G requires a grade of "C" or better (a "C-" counts). Passing marks are
# the ones that earn A-G credit; D/F do not.
_AG_PASS_MARKS = {
    'A+', 'A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-',
    'P', 'CR', 'S', 'O', '3', '4',
}
_AG_FAIL_MARKS = {
    'D+', 'D', 'D-', 'F', 'U', 'NC', 'N', 'WF', 'W', 'I', '1', '2',
}


def _ag_mark_passes(mark):
    """True when a completed course's mark earns A-G credit (C- or better)."""
    text = str(mark or '').strip().upper()
    if not text:
        # No final mark yet (e.g. a scheduled course) — treated as projected.
        return True
    if text in _AG_PASS_MARKS:
        return True
    if text in _AG_FAIL_MARKS:
        return False
    return text[0] in ('A', 'B', 'C')


def _ag_letter(value):
    """First A-G area letter from a catalog value (``GA``/``GO`` → ``G``)."""
    text = str(value or '').strip().upper()
    return text[0] if text and text[0] in 'ABCDEFG' else ''


def _ag_areas(*values):
    areas = []
    for value in values:
        letter = _ag_letter(value)
        if letter and letter not in areas:
            areas.append(letter)
    return sorted(areas)


def _get_course_ag_map():
    """Cached ``course_id`` → A-G letters + subject-area metadata."""
    cache_key = 'dataexp:course_ag_map:v2'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from coursecatalog.models import CourseCatalogEntry

    mapping = {}
    loaded = False
    try:
        rows = CourseCatalogEntry.objects.values(
            'course_id', 'university_area_1', 'university_area_2',
            'college_area_1', 'college_area_2', 'subject_area_1_desc',
        )
        for row in rows:
            cid = (row.get('course_id') or '').strip()
            if not cid:
                continue
            mapping[cid] = {
                'uc': _ag_areas(row.get('university_area_1'), row.get('university_area_2')),
                'csu': _ag_areas(row.get('college_area_1'), row.get('college_area_2')),
                'subject': (row.get('subject_area_1_desc') or '').strip(),
            }
        loaded = True
    except Exception:
        mapping = {}
    if loaded:
        cache.set(cache_key, mapping, 3600)
    return mapping


def _year_sort_key(label):
    text = str(label or '')
    return (0, int(text[:4])) if text[:4].isdigit() else (1, text)


_DIPLOMA_SUBJECT_MAP = {
    'english': 'English',
    'english language arts': 'English',
    'ela': 'English',
    'math': 'Math',
    'mathematics': 'Math',
    'physical education': 'Physical Education',
    'pe': 'Physical Education',
    'world history': 'World History',
    'us history': 'US History',
    'u.s. history': 'US History',
    'united states history': 'US History',
    'american history': 'US History',
    'economics': 'Economics',
    'us government': 'US Government',
    'u.s. government': 'US Government',
    'american government': 'US Government',
    'government': 'US Government',
    'civics': 'Civics',
    'geography': 'Geography',
    'social science': 'Social Studies',
    'social studies': 'Social Studies',
    'history': 'Social Studies',
    'physical science': 'Physical Science',
    'life science': 'Life Science',
    'science': 'Science',
    'biology': 'Life Science',
    'chemistry': 'Physical Science',
    'physics': 'Physical Science',
    'earth science': 'Science',
    'integrated science': 'Science',
    'environmental science': 'Science',
    'anatomy': 'Life Science',
    'physiology': 'Life Science',
    'fine arts': 'Fine Arts/WL/CTE',
    'fine arts/wl/cte': 'Fine Arts/WL/CTE',
    'visual arts': 'Fine Arts/WL/CTE',
    'visual and performing arts': 'Fine Arts/WL/CTE',
    'vapa': 'Fine Arts/WL/CTE',
    'art': 'Fine Arts/WL/CTE',
    'music': 'Fine Arts/WL/CTE',
    'band': 'Fine Arts/WL/CTE',
    'theatre': 'Fine Arts/WL/CTE',
    'theater': 'Fine Arts/WL/CTE',
    'drama': 'Fine Arts/WL/CTE',
    'dance': 'Fine Arts/WL/CTE',
    'world language': 'Fine Arts/WL/CTE',
    'foreign language': 'Fine Arts/WL/CTE',
    'lote': 'Fine Arts/WL/CTE',
    'spanish': 'Fine Arts/WL/CTE',
    'french': 'Fine Arts/WL/CTE',
    'latin': 'Fine Arts/WL/CTE',
    'american sign language': 'Fine Arts/WL/CTE',
    'asl': 'Fine Arts/WL/CTE',
    'cte': 'Fine Arts/WL/CTE',
    'career technical education': 'Fine Arts/WL/CTE',
    'elective': 'Elective',
    'electives': 'Elective',
}

# Fallback: derive a diploma area from a course's A-G letters when the subject
# isn't recognised, so a counted credit always has its course listed.
_AG_LETTER_TO_DIPLOMA_AREA = {
    'A': 'Social Studies',
    'B': 'English',
    'C': 'Math',
    'D': 'Science',
    'E': 'Fine Arts/WL/CTE',
    'F': 'Fine Arts/WL/CTE',
    'G': 'Elective',
}


def _diploma_subject_area(subject):
    return _DIPLOMA_SUBJECT_MAP.get(str(subject or '').strip().lower(), '')


def _diploma_area_for(subject, csu_areas=None):
    """Diploma area for a course: by subject, else by its A-G letters."""
    area = _diploma_subject_area(subject)
    if area:
        return area
    for value in csu_areas or []:
        mapped = _AG_LETTER_TO_DIPLOMA_AREA.get(_ag_letter(value))
        if mapped:
            return mapped
    return ''


# Sentinel values that mean "no leave date".
_LEAVE_SENTINELS = {
    '', 'N', 'NA', 'N/A', 'NULL', 'NONE', '0000-00-00', '0',
    'F', 'FALSE', 'NO', 'NOT SET', 'UNKNOWN',
}


def _schedule_leave_date(item):
    """Return an enrollment leave date from a schedule row, else ''.

    The RPC field name for the leave date is not guaranteed, so any key
    containing "leave", "exit", or "withdraw" is checked.
    """
    if not isinstance(item, dict):
        return ''
    for key, raw in item.items():
        name = str(key or '').strip().lower()
        if not any(token in name for token in ('leave', 'exit', 'withdraw')):
            continue
        value = str(raw or '').strip()
        if value.upper() not in _LEAVE_SENTINELS:
            return value
    return ''


# Values in a status-like field that mean the course was dropped/left.
_DROP_TOKENS = (
    'drop', 'withdraw', 'leave', 'left', 'inactive', 'cancel', 'exit',
    'no longer', 'not enrolled', 'removed', 'unenroll', 'dismiss',
)


def _schedule_status_dropped(item):
    """True when a status-like field marks the row as dropped/withdrawn.

    Scans any key containing "status" or "state" (e.g. schedule_status,
    mark_status, enrollment_status) for a drop/withdraw/leave value.
    """
    if not isinstance(item, dict):
        return False
    for key, raw in item.items():
        name = str(key or '').strip().lower()
        if 'status' not in name and 'state' not in name:
            continue
        text = str(raw or '').strip().lower()
        if text and any(token in text for token in _DROP_TOKENS):
            return True
    return False


def _is_dropped_current_schedule_item(item):
    """True when a current-schedule row should be treated as dropped.

    Covers an explicit dropped/withdrawn status, or an enrollment that ended
    (leave date) without a semester mark.
    """
    if _schedule_status_dropped(item):
        return True
    grade = str(item.get('report_card_grade') or '').strip()
    if not grade or grade.upper() == 'N/A':
        return bool(_schedule_leave_date(item))
    return False


def _dropped_current_schedule_identities(raw):
    """Identities of schedule rows dropped (status or leave), so the full-year
    schedule merge does not re-introduce a stale course.
    """
    if not isinstance(raw, list):
        return set()
    dropped = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not _is_dropped_current_schedule_item(item):
            continue
        dropped |= _course_identity_keys({
            'course_id': item.get('course_id'),
            'course_title': item.get('course_title'),
        })
    return dropped


def _student_current_schedule(raw, ag_map=None):
    """Parse ``student_rpc.current_schedule`` into display rows for the current year."""
    if not isinstance(raw, list):
        return []
    ag_map = ag_map or {}

    items = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = (item.get('course_id') or '').strip()
        meta = ag_map.get(cid) or {}
        areas = _ag_areas(item.get('csu_area_1'), item.get('csu_area_2'))
        if not areas:
            areas = meta.get('csu') or []
        subject = meta.get('subject') or ''
        mark_status = (item.get('mark_status') or '').strip()
        earned = mark_status.lower() == 'final'
        # Skip dropped/withdrawn rows, and enrollments that ended (leave date)
        # without a semester mark, so stale classes don't appear on the schedule.
        if _is_dropped_current_schedule_item(item):
            continue
        items.append({
            'school_year': (item.get('school_year_range') or '').strip() or str(item.get('school_year') or ''),
            'term': _normalize_term(item.get('term_code')),
            'term_code': (item.get('term_code') or '').strip(),
            'period': str(item.get('period') or '').strip(),
            'course_title': (item.get('course_title') or '').strip() or '—',
            'course_id': cid,
            # Scheduled courses are estimates: show their credits, not a mark.
            'mark': '',
            'mark_status': mark_status,
            'earned': earned,
            'credits': _to_float(item.get('credit')),
            'staff_name': (item.get('staff_name') or '').strip(),
            'csu_areas': areas,
            'subject': subject,
            'diploma_area': _diploma_area_for(subject, areas),
            'ag_letter': '',
        })
    items = _dedupe_schedule_rows(items)
    items.sort(key=lambda c: (_term_sort_key(c['term_code']), c['period']))
    return items


def _course_identity_keys(course):
    """Keys identifying the same course regardless of section/period format.

    The RPC and the schedule table use different identifiers and period
    spellings for the same class, so match on the course code *or* the title.
    """
    keys = set()
    code = str(course.get('course_id') or '').strip().lower()
    title = str(course.get('course_title') or '').strip().lower()
    if code:
        keys.add(('code', code))
    if title:
        keys.add(('title', title))
    return keys


def _course_match_key(course):
    """Single key for de-duplicating a course within a term (title, else code)."""
    title = str(course.get('course_title') or '').strip().lower()
    if title:
        return title
    return str(course.get('course_id') or '').strip().lower()


def _current_schedule_score(course):
    """Rank duplicate current-schedule rows; higher wins."""
    return (
        1 if course.get('earned') else 0,
        1 if str(course.get('mark') or '').strip() else 0,
        _to_float(course.get('credits')) or 0.0,
    )


def _dedupe_schedule_rows(items):
    """Collapse the same course appearing twice in the same year + term.

    The RPC can list a class once per enrollment (e.g. a section the student
    left and a section they took). Only one row per course/term is kept —
    preferring a completed row with a mark — so the course is neither listed
    twice nor credited twice.
    """
    best = {}
    order = []
    for course in items:
        key = (_course_match_key(course), course.get('school_year'), course.get('term_code'))
        existing = best.get(key)
        if existing is None:
            best[key] = course
            order.append(key)
        elif _current_schedule_score(course) > _current_schedule_score(existing):
            best[key] = course
    return [best[k] for k in order]


def _merge_projection_schedule(current_schedule, schedule_courses, history_identities=None):
    """Add full-year scheduled courses to the current term for projections.

    Projections should reflect the entire school year, but the RPC
    ``current_schedule`` only carries the active term. A schedule-table course
    that is the same class as one already recorded in the active term or in the
    same year's course history (matching by code or title, ignoring the
    section/period spelling) is skipped, so a course is neither listed nor
    counted twice.
    """
    if not schedule_courses:
        return list(current_schedule)
    history_identities = history_identities or {}
    merged = list(current_schedule)
    seen = set()
    for course in current_schedule:
        seen |= _course_identity_keys(course)
    for course in schedule_courses:
        keys = _course_identity_keys(course)
        if not keys:
            merged.append(course)
            continue
        if keys & seen:
            continue
        history = history_identities.get(course.get('school_year') or '')
        if history and (keys & history):
            continue
        seen |= keys
        merged.append(course)
    return merged


def _scheduled_courses_for_year(ssid: str, school_year: str):
    """All courses on a student's schedule for a school year (the whole year).

    ``student_rpc.current_schedule`` only covers the active term, so the
    ``schedule_list`` table supplies the rest of the year. Credits and A-G
    areas are resolved from the course catalog where possible.
    """
    from studata.models import Schedule
    from coursecatalog.models import CourseCatalogEntry

    school_year = str(school_year or '').strip()
    if not ssid or not school_year:
        return []
    try:
        rows = list(Schedule.objects.filter(ssid=ssid, school_year=school_year))
    except Exception:
        return []
    if not rows:
        return []

    keys = set()
    for row in rows:
        for value in (row.course_key, row.course_code, row.course_state_equivalence_code):
            text = str(value or '').strip()
            if text:
                keys.add(text)

    entries = {}
    if keys:
        try:
            catalog = CourseCatalogEntry.objects.filter(
                Q(course_key__in=keys) | Q(course_id__in=keys) | Q(state_course_code__in=keys)
            )
            for entry in catalog:
                for value in (entry.course_key, entry.course_id, entry.state_course_code):
                    text = str(value or '').strip()
                    if text and text in keys:
                        entries.setdefault(text, entry)
        except Exception:
            entries = {}

    courses = []
    for row in rows:
        if _schedule_status_dropped({'schedule_status': getattr(row, 'schedule_status', '')}):
            continue
        entry = None
        for value in (row.course_key, row.course_code, row.course_state_equivalence_code):
            text = str(value or '').strip()
            if text and text in entries:
                entry = entries[text]
                break
        subject = (getattr(entry, 'subject_area_1_desc', '') or '').strip() if entry else ''
        csu_areas = _ag_areas(entry.college_area_1, entry.college_area_2) if entry else []
        courses.append({
            'school_year': school_year,
            'term': 'Scheduled',
            'term_code': '',
            'period': str(row.period or '').strip(),
            'course_title': (row.course_name or '').strip() or '—',
            'course_id': (row.course_code or row.course_key or '').strip(),
            'mark': '',
            'mark_status': '',
            'earned': False,
            'credits': _to_float(entry.credit) if entry else None,
            'staff_name': (row.staff_name or '').strip(),
            'csu_areas': csu_areas,
            'subject': subject,
            'diploma_area': _diploma_area_for(subject, csu_areas),
            'ag_letter': '',
            'from_schedule': True,
        })
    return courses


def _ag_credit_allocation(course_history, current_schedule, plans):
    """Assign each course to exactly one A-G letter and group credits by year.

    A course tagged with two areas (e.g. ``F/G``) counts toward its first
    letter until that area's requirement is met; later courses then fall to
    the second letter (G). Courses are processed oldest-first, and the
    in-progress current schedule is processed last, so the earliest work
    satisfies the primary subject first.
    """
    capacity = {}
    for plan in plans:
        for row in plan['rows']:
            letter = _ag_letter(row['subject_area'])
            if letter:
                capacity[letter] = row['req_credits']
    remaining = dict(capacity)

    ordered = []
    for year in sorted(course_history, key=lambda y: _year_sort_key(y['school_year'])):
        for term in year['terms']:
            for course in term['courses']:
                ordered.append((year['school_year'], term.get('term', ''), course, False, True))
    for course in current_schedule:
        ordered.append((course['school_year'], course.get('term', ''), course, True, bool(course.get('earned'))))

    allocation = {}
    for year_label, term_label, course, in_progress, earned in ordered:
        course['ag_letter'] = ''
        areas = course.get('csu_areas') or []
        credits = course.get('credits') or 0.0
        if not areas or credits <= 0:
            continue
        # A-G requires a C- or better. A final D/F course is still listed under
        # its area, but it does not earn credit and never counts toward meeting
        # the requirement (it also does not consume the area's capacity).
        earns_credit = (not earned) or _ag_mark_passes(course.get('mark'))
        course['ag_credit'] = earns_credit
        target = next((a for a in areas if remaining.get(a, 0) > 0), areas[-1])
        if earns_credit:
            remaining[target] = remaining.get(target, 0) - credits
        course['ag_letter'] = target
        bucket = allocation.setdefault(target, {}).setdefault(
            year_label, {'school_year': year_label, 'credits': 0.0, 'courses': [], 'in_progress': False})
        if earns_credit:
            bucket['credits'] += credits
        bucket['in_progress'] = bucket['in_progress'] or in_progress
        bucket['courses'].append({
            'name': course['course_title'],
            'mark': course['mark'],
            'credits': credits,
            'earned': earned,
            'credit_ok': earns_credit,
            'term': term_label,
        })

    result = {}
    for letter, years in allocation.items():
        rows = []
        for year_label in sorted(years.keys(), key=_year_sort_key, reverse=True):
            bucket = years[year_label]
            bucket['credits'] = round(bucket['credits'], 2)
            rows.append(bucket)
        result[letter] = rows
    return result


def _subject_courses_by_year(course_history, current_courses, areas):
    """Courses counting toward a diploma area/category, grouped by school year.

    ``areas`` may be a single area name or an iterable of areas (a DSUSD
    category such as Social Studies aggregates several district areas).
    """
    if not areas:
        return []
    area_set = {str(a).strip().lower() for a in (
        areas if isinstance(areas, (list, tuple, set, frozenset)) else (areas,)
    )}
    if not area_set:
        return []

    grouped = {}
    order = []

    def _bucket(year_label):
        if year_label not in grouped:
            grouped[year_label] = {
                'school_year': year_label, 'credits': 0.0, 'courses': [], 'in_progress': False,
            }
            order.append(year_label)
        return grouped[year_label]

    for year in course_history:
        for term in year['terms']:
            for course in term['courses']:
                if str(course.get('diploma_area') or '').strip().lower() not in area_set:
                    continue
                credits = course.get('credits') or 0.0
                bucket = _bucket(year['school_year'])
                bucket['credits'] += credits
                bucket['courses'].append({
                    'name': course['course_title'],
                    'mark': course['mark'],
                    'credits': credits,
                    'earned': True,
                    'term': term['term'],
                })

    for course in current_courses:
        credits = course.get('credits') or 0.0
        bucket = _bucket(course.get('school_year') or '')
        bucket['in_progress'] = True
        bucket['credits'] += credits
        bucket['courses'].append({
            'name': course['course_title'],
            'mark': course.get('mark') or '',
            'credits': credits,
            'earned': bool(course.get('earned')),
            'term': course.get('term') or '',
        })

    result = []
    for year_label in sorted(order, key=_year_sort_key, reverse=True):
        bucket = grouped[year_label]
        bucket['credits'] = round(bucket['credits'], 2)
        result.append(bucket)
    return result


def _apply_prediction(row, current_courses):
    """Add earned, needed, and scheduled current-schedule credits to a row.

    Only current-schedule courses whose semester credits are earned (final
    marks) are counted toward the requirement; the rest are "scheduled".
    """
    earned_current = sum((c.get('credits') or 0.0) for c in current_courses if c.get('earned'))
    scheduled_current = sum((c.get('credits') or 0.0) for c in current_courses if not c.get('earned'))
    req = row['req_credits']
    counted = row['comp_credits'] + earned_current
    projected_total = counted + scheduled_current

    row['in_progress'] = current_courses
    row['earned_current'] = round(earned_current, 2)
    row['scheduled_current'] = round(scheduled_current, 2)
    row['projected_current'] = row['scheduled_current']
    row['counted_credits'] = round(counted, 2)
    row['needed_after'] = round(max(req - counted, 0.0), 2)
    row['projected_total'] = round(projected_total, 2)
    row['pct_earned'] = round(min(counted / req * 100, 100)) if req > 0 else (100 if counted > 0 else 0)
    row['pct_projected'] = (
        round(min(projected_total / req * 100, 100)) - row['pct_earned'] if req > 0 else 0
    )
    if counted >= req:
        row['projection_status'] = 'Met'
    elif projected_total >= req:
        row['projection_status'] = 'Scheduled'
    else:
        row['projection_status'] = 'Not Met'
    return row


# CSU first-time-freshman "a-g" pattern: (area, display label, required years).
# Source: calstate.edu/apply/freshman/getting_into_the_csu/admission-requirements.
# One year of study is equivalent to 10 credits, so the 15-unit pattern maps to
# A=20, B=40, C=30, D=20, E=20, F=10, G=10 credits.
_CSU_AG_PATTERN = (
    ('A', 'A. History and Social Science', 2),
    ('B', 'B. English', 4),
    ('C', 'C. Mathematics', 3),
    ('D', 'D. Laboratory Science', 2),
    ('E', 'E. Language Other Than English', 2),
    ('F', 'F. Visual and Performing Arts', 1),
    ('G', 'G. College Preparatory Elective', 1),
)
_CREDITS_PER_YEAR = 10.0


def _student_ag_plans(raw, course_history, current_schedule):
    """CSU first-time-freshman A-G plan.

    Areas, required years and the minimum 15-unit total follow the CSU
    admission requirements, with one year of study equal to 10 credits. Only
    courses completed with a C- or better earn A-G credit; the earned totals are
    derived from the course allocation so a completed D/F is never counted.
    Returns ``[]`` when the student has no A-G signal.
    """
    by_letter = {}
    for plan in _student_requirement_plans(raw):
        for row in plan['rows']:
            letter = _ag_letter(row['subject_area'])
            if letter and letter not in by_letter:
                by_letter[letter] = row

    has_course_ag = any(
        c.get('csu_areas')
        for year in course_history
        for term in year.get('terms', [])
        for c in term.get('courses', [])
    ) or any(c.get('csu_areas') for c in current_schedule)
    if not by_letter and not has_course_ag:
        return []

    rows = []
    for index, (letter, label, years) in enumerate(_CSU_AG_PATTERN):
        required = round(years * _CREDITS_PER_YEAR, 2)
        source = by_letter.get(letter) or {}
        completed = _to_float(source.get('comp_credits')) or 0.0
        rows.append({
            'subject_area': label,
            'req_credits': required,
            'req_years': years,
            'comp_credits': completed,
            'wip_credits': _to_float(source.get('wip_credits')) or 0.0,
            'needed_credits': max(required - completed, 0.0),
            'met': completed >= required,
            'is_total': False,
            'sort_order': index,
        })

    plan = {
        'key': 'csu-ag',
        'label': 'UC/CSU A-G',
        'display_label': 'UC/CSU A-G',
        'requirement_set': 'CSU first-time freshman · 15 units (1 year = 10 credits)',
        'rows': rows,
    }

    allocation = _ag_credit_allocation(course_history, current_schedule, [plan])
    for row in plan['rows']:
        letter = _ag_letter(row['subject_area'])
        by_year = allocation.get(letter, [])
        row['by_year'] = by_year
        earned_from_history = sum(
            bucket['credits'] for bucket in by_year if not bucket.get('in_progress')
        )
        if has_course_ag:
            # Drive A-G earned credits from the C-or-better course allocation so
            # a completed D/F never counts (even if the district RPC includes it).
            row['comp_credits'] = round(earned_from_history, 2)
        elif earned_from_history > row['comp_credits']:
            # No course-level A-G data; trust the district RPC progress.
            row['comp_credits'] = round(earned_from_history, 2)
        completed = row['comp_credits']
        wip = row['wip_credits']
        required = row['req_credits']
        completed_pct = round(min(completed / required * 100, 100)) if required > 0 else (100 if completed > 0 else 0)
        total_pct = round(min((completed + wip) / required * 100, 100)) if required > 0 else completed_pct
        row['percent'] = completed_pct
        row['pct_completed'] = completed_pct
        row['pct_in_progress'] = max(total_pct - completed_pct, 0)
        row['met'] = completed >= required
        courses = [
            c for c in current_schedule
            if c.get('ag_letter') == letter and (not c.get('earned') or c.get('ag_credit'))
        ]
        _apply_prediction(row, courses)

    plan['totals'] = {
        'comp_credits': round(sum(r['comp_credits'] for r in plan['rows']), 2),
        'req_credits': round(sum(r['req_credits'] for r in plan['rows']), 2),
    }
    plan['row_count'] = len(plan['rows'])
    plan['met_count'] = sum(
        1 for r in plan['rows'] if r.get('projection_status') == 'Met'
    )
    return [plan]


# A-G Progress Benchmarks for CSU (CCGI, March 2025). One unit == one year ==
# 10 credits. Cumulative on-track A-G units by end of grade, plus the specific
# subject-area years expected by then.
_AG_BENCHMARK_UNITS = {9: 4, 10: 7, 11: 11, 12: 15}
_AG_BENCHMARK_AREA_YEARS = {
    'A': {9: 0, 10: 0, 11: 1, 12: 2},
    'B': {9: 1, 10: 2, 11: 3, 12: 4},
    'C': {9: 1, 10: 2, 11: 3, 12: 3},
    'D': {9: 0, 10: 0, 11: 1, 12: 2},
    'E': {9: 0, 10: 0, 11: 1, 12: 2},
    'F': {9: 0, 10: 0, 11: 0, 12: 1},
    'G': {9: 0, 10: 0, 11: 0, 12: 1},
}


def _ag_on_track(ag_plans, grade):
    """A-G on-track status for a high-school grade against the CSU benchmarks.

    Compares the A-G units (and per-area years) a student has earned — plus the
    courses currently on their schedule — with what CaliforniaColleges.edu
    expects by the end of their grade, and reports which areas are behind.
    """
    units_expected = _AG_BENCHMARK_UNITS.get(grade, 15)
    earned_credits = {letter: 0.0 for letter in 'ABCDEFG'}
    projected_credits = {letter: 0.0 for letter in 'ABCDEFG'}
    if ag_plans:
        for row in ag_plans[0]['rows']:
            letter = _ag_letter(row['subject_area'])
            if not letter:
                continue
            earned = _to_float(row.get('comp_credits')) or 0.0
            projected = _to_float(row.get('projected_total'))
            if projected is None:
                projected = earned + (_to_float(row.get('scheduled_current')) or 0.0)
            earned_credits[letter] = earned
            projected_credits[letter] = projected

    areas = []
    lacking = []
    total_earned_years = 0.0
    total_projected_years = 0.0
    for letter, label, _full_years in _CSU_AG_PATTERN:
        expected = _AG_BENCHMARK_AREA_YEARS.get(letter, {}).get(grade, 0)
        earned = round(earned_credits.get(letter, 0.0) / _CREDITS_PER_YEAR, 2)
        # "On track" counts the current schedule too (projected completion).
        projected = round(projected_credits.get(letter, earned_credits.get(letter, 0.0)) / _CREDITS_PER_YEAR, 2)
        total_earned_years += earned
        total_projected_years += projected
        shortfall = round(max(expected - projected, 0.0), 2)
        if shortfall > 0:
            status = 'behind'
        elif projected > expected:
            status = 'ahead'
        else:
            status = 'on_track'
        area = {
            'letter': letter,
            'label': label,
            'expected_years': expected,
            'earned_years': earned,
            'projected_years': projected,
            'shortfall_years': shortfall,
            'status': status,
        }
        areas.append(area)
        if shortfall > 0:
            lacking.append(area)

    total_earned_years = round(total_earned_years, 2)
    total_projected_years = round(total_projected_years, 2)
    units_shortfall = round(max(units_expected - total_projected_years, 0.0), 2)
    return {
        'grade': grade,
        'units_expected': units_expected,
        'units_earned': total_earned_years,
        'units_projected': total_projected_years,
        'units_shortfall': units_shortfall,
        'on_track': not lacking and units_shortfall <= 0,
        'areas': areas,
        'lacking': lacking,
    }


DIPLOMA_ON_TRACK_SUBJECTS = (
    'English', 'Math', 'Social Studies', 'Science',
    'Physical Education', 'Fine Art, CTE, or World Language (LOTE)', 'Electives',
)


def _round_credits(value):
    """Round a credit amount to the nearest 5 (credits come in 5-credit units)."""
    try:
        return int(math.floor(float(value or 0) / 5.0 + 0.5) * 5)
    except (TypeError, ValueError):
        return 0


# Default expected credits per subject per grade (grades 9-12). Subjects can
# start in a later grade (e.g. Social Studies in grade 10); a subject that has
# not started yet does not make a student "behind". Electives fill each year up
# to the pathway's yearly target (220 / 4 = 55).
_DIPLOMA_EXPECTED_PER_YEAR = {
    'English': [10, 10, 10, 10],
    'Math': [10, 10, 10, 0],            # 3 years; must start in grade 9
    'Social Studies': [0, 10, 10, 10],  # starts in grade 10
    'Science': [5, 5, 5, 5],
    'Physical Education': [10, 10, 0, 0],
    'Fine Art, CTE, or World Language (LOTE)': [0, 0, 5, 5],
}


def default_diploma_expected_credits():
    """Expected diploma credits per subject per grade (grades 9-12).

    Reflects when each subject typically starts; electives fill each grade up
    to the standard pathway's yearly target. Used when ProjectConfig has no
    override.
    """
    per_year = {subject: list(years) for subject, years in _DIPLOMA_EXPECTED_PER_YEAR.items()}
    yearly_target = 220.0 / 4  # standard pathway, 55 credits per grade
    electives = []
    for index in range(4):
        fixed = sum(years[index] for years in per_year.values())
        electives.append(int(round(yearly_target - fixed)))
    per_year['Electives'] = electives
    return per_year


def _diploma_on_track(diploma_plans, grade, expected_credits=None):
    """Diploma on-track status by grade using expected credits per subject/year.

    Earned = completed credits; Projected = earned + currently-scheduled credits.
    Expected comes from ProjectConfig (or evenly distributed defaults) and is
    reported in whole 5-credit units.
    """
    plan = None
    for candidate in diploma_plans or []:
        if candidate.get('pathway_credits'):
            plan = candidate
            break
    if plan is None and diploma_plans:
        plan = diploma_plans[0]
    if plan is None:
        return None

    expected_config = expected_credits or {}
    defaults = default_diploma_expected_credits()
    grade_index = max(grade - 9, 0)  # 0..3 for grades 9..12

    total_expected = 0
    total_earned = 0
    total_projected = 0
    areas = []
    lacking = []
    for row in plan['rows']:
        label = row['subject_area']
        weights = expected_config.get(label) or defaults.get(label) or [0, 0, 0, 0]
        weights = [max(0, int(_to_float(w) or 0)) for w in (list(weights) + [0, 0, 0, 0])[:4]]
        weight_sum = sum(weights) or 1
        cumulative = sum(weights[:grade_index + 1])
        future = sum(weights[grade_index + 1:])
        required = _to_float(row.get('req_credits')) or 0.0
        expected = _round_credits(required * cumulative / weight_sum)

        earned = _round_credits(_to_float(row.get('counted_credits')))
        projected = _to_float(row.get('projected_total'))
        if projected is None:
            projected = _to_float(row.get('counted_credits')) or 0.0
        projected = _round_credits(projected)

        total_expected += expected
        total_earned += earned
        total_projected += projected
        if expected <= 0 and future > 0:
            # The subject doesn't start until a later grade — not behind.
            status = 'not_started'
            shortfall = 0
        elif projected < expected:
            # Expected by now (and/or running out of windows) but not earned.
            status = 'behind'
            shortfall = expected - projected
        elif projected > expected:
            status = 'ahead'
            shortfall = 0
        else:
            status = 'on_track'
            shortfall = 0
        area = {
            'label': label,
            'expected_credits': expected,
            'earned_credits': earned,
            'projected_credits': projected,
            'shortfall_credits': shortfall,
            'status': status,
        }
        areas.append(area)
        if shortfall > 0:
            lacking.append(area)

    return {
        'label': plan.get('display_label') or plan.get('label') or 'Diploma',
        'credits_expected': total_expected,
        'credits_earned': total_earned,
        'credits_projected': total_projected,
        'credits_shortfall': sum(a['shortfall_credits'] for a in lacking),
        'on_track': not lacking,
        'areas': areas,
        'lacking': lacking,
    }


def _high_school_track(grade, ag_plans, diploma_plans, expected_credits=None):
    """On-track view (A-G + Diploma) for a high-school grade."""
    ag = _ag_on_track(ag_plans, grade)
    diploma = _diploma_on_track(diploma_plans, grade, expected_credits)
    return {
        'grade': grade,
        'ag': ag,
        'diploma': diploma,
        'lacking_count': len(ag['lacking']) + (len(diploma['lacking']) if diploma else 0),
    }


def _student_course_history(raw, ag_map=None):
    """Group ``student_rpc.course_history`` rows by school year then term.

    The history arrives per section (term), and the same course can appear
    more than once in a term with different marks. Duplicate sections are
    merged into a single course row whose marks are shown together
    (e.g. ``C- / B``) while the credits are counted only once.
    """
    if not isinstance(raw, list):
        return []
    ag_map = ag_map or {}

    years = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        # A row on an enrollment that ended with no mark is stale.
        if _schedule_leave_date(item) and not str(item.get('display_mark') or '').strip():
            continue
        year = item.get('school_year')
        year_label = (item.get('school_year_range') or '').strip() or (str(year) if year else '')
        bucket = years.setdefault(year_label, {
            'school_year': year_label,
            'course_count': 0,
            'credits_earned': 0.0,
            'terms': {},
        })
        term_code = (item.get('term_code') or '').strip()
        term_label = _normalize_term(term_code) or '—'
        term = bucket['terms'].setdefault(term_label, {
            'term': term_label,
            'term_code': term_code,
            'gpa': None,
            'courses': {},
        })
        if term['gpa'] is None:
            term['gpa'] = _to_float(item.get('term_gpa'))

        course_id = (item.get('course_id') or '').strip()
        title = (item.get('course_title') or '').strip() or '—'
        # De-dupe by title within a term, so two enrollments of the same class
        # (e.g. one the student left) collapse into a single row.
        key = title or course_id
        course = term['courses'].get(key)
        if course is None:
            meta = ag_map.get(course_id) or {}
            course_subject = meta.get('subject') or ''
            course_csu = meta.get('csu') or []
            course = {
                'course_title': title,
                'course_id': course_id,
                'marks': [],
                'mark': '',
                'credits': None,
                'attempted': None,
                'uc_areas': meta.get('uc') or [],
                'csu_areas': course_csu,
                'subject': course_subject,
                'diploma_area': _diploma_area_for(course_subject, course_csu),
            }
            term['courses'][key] = course

        mark = _normalize_mark(item.get('display_mark'))
        if mark and mark not in course['marks']:
            course['marks'].append(mark)
        credits = _to_float(item.get('display_completed'))
        if credits is not None:
            course['credits'] = credits if course['credits'] is None else max(course['credits'], credits)
        attempted = _to_float(item.get('display_attempted'))
        if attempted is not None:
            course['attempted'] = attempted if course['attempted'] is None else max(course['attempted'], attempted)

    result = []
    for bucket in years.values():
        terms = []
        for term in bucket['terms'].values():
            courses = list(term['courses'].values())
            for course in courses:
                course['mark'] = ' / '.join(course['marks'])
            term['courses'] = courses
            bucket['course_count'] += len(courses)
            bucket['credits_earned'] += sum((c['credits'] or 0.0) for c in courses)
            terms.append(term)
        bucket['terms'] = sorted(terms, key=lambda t: _term_sort_key(t['term_code']))
        bucket['credits_earned'] = round(bucket['credits_earned'], 2)
        result.append(bucket)

    def _year_key(bucket):
        text = bucket['school_year']
        return text[:4] if text[:4].isdigit() else text

    result.sort(key=_year_key, reverse=True)
    return result


def _requirement_row(item):
    req = _to_float(item.get('req_credits')) or 0.0
    comp = _to_float(item.get('comp_credits')) or 0.0
    wip = _to_float(item.get('wip_credits')) or 0.0
    needed = _to_float(item.get('needed_credits'))
    if needed is None:
        needed = max(req - comp, 0.0)
    subject = (item.get('subject_area') or '').strip() or '—'
    try:
        sort_order = int(item.get('sort_order'))
    except (TypeError, ValueError):
        sort_order = 999
    completed_pct = round(min(comp / req * 100, 100)) if req > 0 else (100 if comp > 0 else 0)
    total_pct = round(min((comp + wip) / req * 100, 100)) if req > 0 else completed_pct
    return {
        'subject_area': subject,
        'req_credits': req,
        'comp_credits': comp,
        'wip_credits': wip,
        'needed_credits': needed,
        'percent': completed_pct,
        'pct_completed': completed_pct,
        'pct_in_progress': max(total_pct - completed_pct, 0),
        'met': needed <= 0,
        'is_total': subject.upper() == 'TOTALS',
        'sort_order': sort_order,
    }


_GRADUATION_PLAN_LABELS = {
    'ds high school': 'DS High School Diploma',
    'alt edu high school diploma': 'Alternative Education Diploma',
    'alt edu high school': 'Alternative Education Diploma',
    'alternative education high school diploma': 'Alternative Education Diploma',
    'district graduation': 'District Diploma',
    'district diploma': 'District Diploma',
}


def _graduation_plan_label(name):
    """Friendlier display name for a raw ``grad_plan`` value."""
    text = str(name or '').strip().rstrip('.')
    return _GRADUATION_PLAN_LABELS.get(text.lower(), text)


def _plan_key(name):
    slug = re.sub(r'[^a-z0-9]+', '-', str(name or '').lower()).strip('-')
    return slug or 'plan'


def _student_requirement_plans(raw):
    """Group requirement rows by diploma/plan (``grad_plan``).

    UC and CSU have a single plan each, but the district diploma offers
    multiple options (DS High School / Alt Ed) that the UI toggles between.
    """
    if not isinstance(raw, list):
        return []

    grouped = {}
    order = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = (
            (item.get('grad_plan') or '').strip().rstrip('.')
            or (item.get('requirement_set') or '').strip()
            or 'Requirements'
        )
        key = _plan_key(label)
        if key not in grouped:
            grouped[key] = {
                'key': key,
                'label': label,
                'display_label': _graduation_plan_label(label),
                'requirement_set': (item.get('requirement_set') or '').strip(),
                'rows': [],
            }
            order.append(key)
        grouped[key]['rows'].append(_requirement_row(item))

    result = []
    for key in order:
        plan = grouped[key]
        plan['rows'].sort(key=lambda r: (r['is_total'], r['sort_order']))
        plan['totals'] = next((r for r in plan['rows'] if r['is_total']), None)
        plan['rows'] = [r for r in plan['rows'] if not r['is_total']]
        result.append(plan)
    return result


# DSUSD diploma pathway subject categories. Each category aggregates the
# district's finer-grained requirement areas. Source:
# https://www.dsusd.us/departments/educational_services/curriculum_instruction_assessment/d_s_u_s_d_diploma_pathways
_DSUSD_DIPLOMA_CATEGORIES = (
    ('English', ('english',)),
    ('Math', ('math', 'mathematics')),
    ('Social Studies', ('social studies', 'world history', 'us history', 'u.s. history',
                        'economics', 'us government', 'u.s. government', 'american government',
                        'civics', 'geography')),
    ('Science', ('science', 'physical science', 'life science', 'biological science',
                 'earth science', 'laboratory science')),
    ('Physical Education', ('physical education', 'pe')),
    ('Fine Art, CTE, or World Language (LOTE)', ('fine art', 'fine arts', 'fine arts/wl/cte',
                                                 'visual and performing arts', 'vapa', 'world language',
                                                 'lote', 'foreign language', 'cte',
                                                 'career technical education')),
    ('Electives', ('elective', 'electives')),
)

# Required credits for the non-elective categories; electives make up the rest
# of the pathway total.
_DSUSD_CATEGORY_REQUIRED = {
    'English': 40.0,
    'Math': 30.0,
    'Social Studies': 30.0,
    'Science': 20.0,
    'Physical Education': 20.0,
    'Fine Art, CTE, or World Language (LOTE)': 10.0,
}

# Raw ``grad_plan`` value (lowercased) -> (display label, total credits).
_DSUSD_DIPLOMA_PATHWAYS = {
    'ds high school': ('DS High School Diploma', 220),
    'ds high school diploma': ('DS High School Diploma', 220),
    'standard': ('DS High School Diploma', 220),
    'alt edu high school diploma': ('Alternative Education Diploma', 200),
    'alt edu high school': ('Alternative Education Diploma', 200),
    'alternative education high school diploma': ('Alternative Education Diploma', 200),
    'alternative education diploma': ('Alternative Education Diploma', 200),
    'continuation': ('Alternative Education Diploma', 200),
}


def _dsusd_diploma_rows(district_rows, total_credits):
    """Aggregate district requirement rows into DSUSD diploma categories."""
    area_to_category = {
        area: label
        for label, areas in _DSUSD_DIPLOMA_CATEGORIES
        for area in areas
    }
    completed = {label: 0.0 for label, _ in _DSUSD_DIPLOMA_CATEGORIES}
    in_progress = {label: 0.0 for label, _ in _DSUSD_DIPLOMA_CATEGORIES}
    for row in district_rows:
        category = area_to_category.get(str(row.get('subject_area') or '').strip().lower())
        if not category:
            continue
        completed[category] += row.get('comp_credits') or 0.0
        in_progress[category] += row.get('wip_credits') or 0.0

    electives_required = max(
        float(total_credits) - sum(_DSUSD_CATEGORY_REQUIRED.values()), 0.0
    )
    rows = []
    for index, (label, areas) in enumerate(_DSUSD_DIPLOMA_CATEGORIES):
        required = electives_required if label == 'Electives' else _DSUSD_CATEGORY_REQUIRED[label]
        comp = round(completed[label], 2)
        wip = round(in_progress[label], 2)
        needed = max(required - comp, 0.0)
        completed_pct = round(min(comp / required * 100, 100)) if required > 0 else (100 if comp > 0 else 0)
        total_pct = round(min((comp + wip) / required * 100, 100)) if required > 0 else completed_pct
        rows.append({
            'subject_area': label,
            'areas': areas,
            'req_credits': required,
            'comp_credits': comp,
            'wip_credits': wip,
            'needed_credits': needed,
            'percent': completed_pct,
            'pct_completed': completed_pct,
            'pct_in_progress': max(total_pct - completed_pct, 0),
            'met': needed <= 0,
            'is_total': False,
            'sort_order': index,
        })
    return rows


def _student_diploma_plans(raw):
    """Diploma options shaped to the DSUSD diploma pathways.

    Recognised DSUSD pathways (DS High School = 220 credits, Alternative
    Education = 200 credits) are rebuilt from the DSUSD subject categories,
    aggregating the district's finer-grained progress. Unknown plans are left
    as reported.
    """
    plans = _student_requirement_plans(raw)
    for plan in plans:
        pathway = _DSUSD_DIPLOMA_PATHWAYS.get(str(plan['label']).strip().lower())
        if not pathway:
            for row in plan['rows']:
                row.setdefault('areas', (row['subject_area'],))
            continue
        display_label, total_credits = pathway
        plan['display_label'] = display_label
        plan['pathway_credits'] = total_credits
        plan['rows'] = _dsusd_diploma_rows(plan['rows'], total_credits)
        plan['totals'] = {
            'comp_credits': round(sum(r['comp_credits'] for r in plan['rows']), 2),
            'req_credits': round(sum(r['req_credits'] for r in plan['rows']), 2),
        }
    return plans


def _build_student_details(row, ag_map=None, schedule_courses=None):
    rank = str(row.class_rank or '').strip()
    size = str(row.class_size or '').strip()
    if rank and size:
        rank_display = f"{rank} of {size}"
    else:
        rank_display = rank or size or ''
    course_history = _student_course_history(row.course_history, ag_map)
    # The current schedule shown on the summary comes straight from the
    # student_rpc.current_schedule column.
    current_schedule = _student_current_schedule(
        getattr(row, 'current_schedule', None), ag_map
    )
    # For projections, drop rows that repeat a course already recorded in the
    # transcript for the same year + term, then fold in the rest of the year's
    # scheduled courses so Diploma/A-G projections cover the whole year.
    history_term_keys = set()
    for year in course_history:
        for term in year['terms']:
            label = (term.get('term') or '').strip().lower()
            for course in term['courses']:
                history_term_keys.add((_course_match_key(course), year['school_year'], label))
    if history_term_keys:
        projection_base = [
            c for c in current_schedule
            if (_course_match_key(c), c.get('school_year'), (c.get('term') or '').strip().lower())
            not in history_term_keys
        ]
    else:
        projection_base = list(current_schedule)
    history_identities = {}
    for year in course_history:
        keys = set()
        for term in year['terms']:
            for course in term['courses']:
                keys |= _course_identity_keys(course)
        history_identities[year['school_year']] = keys
    # Don't let the full-year schedule re-add a course dropped (status/leave).
    dropped_identities = _dropped_current_schedule_identities(
        getattr(row, 'current_schedule', None)
    )
    if dropped_identities and schedule_courses:
        schedule_courses = [
            c for c in schedule_courses
            if not (_course_identity_keys(c) & dropped_identities)
        ]
    projection_schedule = _merge_projection_schedule(
        projection_base, schedule_courses, history_identities
    )
    diploma_plans = _student_diploma_plans(row.graduation_requirements)
    for plan in diploma_plans:
        for req_row in plan['rows']:
            areas = req_row.get('areas') or (req_row['subject_area'],)
            area_set = {str(a).strip().lower() for a in areas}
            courses = [
                c for c in projection_schedule
                if str(c['diploma_area'] or '').strip().lower() in area_set
            ]
            req_row['by_year'] = _subject_courses_by_year(
                course_history, courses, areas
            )
            # Earned credits come from the transcript so they always match the
            # courses listed (the district RPC can over- or under-report, e.g.
            # counting a section the student left).
            history_courses = [
                c
                for year in course_history
                for term in year['terms']
                for c in term['courses']
                if str(c.get('diploma_area') or '').strip().lower() in area_set
            ]
            if history_courses:
                history_completed = round(
                    sum((c.get('credits') or 0.0) for c in history_courses), 2
                )
                req_row['comp_credits'] = history_completed
                req_row['needed_credits'] = round(
                    max(req_row['req_credits'] - history_completed, 0.0), 2
                )
                req_row['met'] = req_row['needed_credits'] <= 0
            else:
                earned_from_history = sum(
                    (c.get('credits') or 0.0)
                    for year in req_row['by_year'] if not year['in_progress']
                    for c in year['courses']
                )
                if earned_from_history > (req_row.get('comp_credits') or 0.0):
                    req_row['comp_credits'] = round(earned_from_history, 2)
                    req_row['needed_credits'] = round(
                        max(req_row['req_credits'] - earned_from_history, 0.0), 2
                    )
                    req_row['met'] = req_row['needed_credits'] <= 0
            _apply_prediction(req_row, courses)
    ag_plans = _student_ag_plans(row.csu_requirements, course_history, projection_schedule)
    for plan in (*diploma_plans, *ag_plans):
        rows = plan['rows']
        plan['row_count'] = len(rows)
        plan['met_count'] = sum(1 for r in rows if r.get('projection_status') == 'Met')
    requirement_tabs = [
        {'key': 'diploma', 'label': 'District Diploma', 'plans': diploma_plans},
        {'key': 'ag', 'label': 'UC/CSU A-G', 'plans': ag_plans},
    ]
    default_plan = ''
    for tab in requirement_tabs:
        if len(tab['plans']) > 1:
            default_plan = tab['plans'][0]['key']
            break
    grade_number = _high_school_grade_number(row.grade)
    on_track = None
    if grade_number in (9, 10, 11):
        try:
            from landing.utils import get_diploma_expected_credits
            expected_credits = get_diploma_expected_credits()
        except Exception:
            expected_credits = {}
        on_track = _high_school_track(
            grade_number, ag_plans, diploma_plans, expected_credits
        )
    return {
        'has_any': True,
        'summary': {
            'student_name': (row.student_name or '').strip(),
            'grade': (row.grade or '').strip(),
            'school_name': (row.school_name or '').strip(),
            'school_year': (row.school_year or '').strip(),
            'counselor_name': (row.counselor_name or '').strip(),
            'weighted_gpa': _format_gpa(row.weighted_gpa),
            'unweighted_gpa': _format_gpa(row.unweighted_gpa),
            'class_rank': rank,
            'class_size': size,
            'rank_display': rank_display,
            'expected_graduation_year': _clean_year(row.expected_graduation_year),
            'graduation_status': (row.graduation_status or '').strip(),
            'is_high_school': _is_high_school_grade(row.grade),
            'academic_recognition': (
                _academic_recognition(row.unweighted_gpa)
                if _is_high_school_grade(row.grade) else ''
            ),
        },
        'course_history': course_history,
        'current_schedule': current_schedule,
        'requirements': {
            'diploma': diploma_plans,
            'ag': ag_plans,
        },
        'requirement_tabs': requirement_tabs,
        'default_plan': default_plan,
        'on_track': on_track,
    }


def _empty_student_details():
    return {
        'has_any': False,
        'summary': {},
        'course_history': [],
        'current_schedule': [],
        'requirements': {'diploma': [], 'ag': []},
        'requirement_tabs': [],
        'default_plan': '',
    }


def _fetch_student_details(ssid: str):
    """Per-student record: GPA/rank summary, course history, and requirement sets."""
    from studata.models import StudentRpc

    ssid = _resolve_ssid(ssid)
    try:
        row = StudentRpc.objects.filter(ssid=ssid).first()
        if row is None:
            cs = CurrentStudent.objects.filter(ssid=ssid).order_by('-school_year').first()
            student_id = getattr(cs, 'student_id', None) if cs else None
            if student_id:
                row = StudentRpc.objects.filter(student_id=student_id).first()
    except Exception:
        return _empty_student_details()
    if row is None:
        return _empty_student_details()
    # The current courses for the diploma/A-G projections come from the
    # student_rpc.current_schedule column (it holds the current-year schedule).
    return _build_student_details(row, ag_map=_get_course_ag_map())


def get_student_details_cached(ssid: str, timeout: int = 300, user_id=None):
    """Get the student's record/transcript details with caching."""
    ssid = _resolve_ssid(ssid)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_details'])
    if cached is not None:
        return cached
    details = _fetch_student_details(ssid)
    cache.set(keys['student_details'], details, timeout=timeout)
    return details


def get_student_summary_partial_html_cached(request, ssid: str, timeout: int = 300, embedded: bool = False):
    """Get rendered HTML for student summary with caching."""
    ssid = _resolve_ssid(ssid)
    user_id = getattr(getattr(request, 'user', None), 'pk', None)
    keys = _student_summary_cache_keys(ssid, user_id=user_id, embedded=embedded)
    cached = cache.get(keys['partial_html'])
    if cached is not None:
        return cached

    marks = filter_display_marks(get_student_marks_cached(ssid, timeout=timeout, user_id=user_id))
    student_info = get_student_info_cached(ssid, timeout=timeout, user_id=user_id)
    try:
        from landing.utils import get_summary_sections
        summary_sections = get_summary_sections()
    except Exception:
        summary_sections = {
            'tests': True, 'grades': True, 'diploma': True, 'ag': True,
            'on_track': True, 'cte': True, 'recognition': True, 'flags': True,
        }
    context = {
        'ssid': ssid,
        'student_info': student_info,
        'student_name': student_info.get('full_name'),
        'test_records': get_test_records_cached(ssid, timeout=timeout, user_id=user_id),
        'student_marks': marks,
        'student_marks_grid': pivot_student_marks(marks),
        'student_cte_details': get_student_cte_details_cached(
            ssid, timeout=timeout, user_id=user_id,
            current_school_year=student_info.get('school_year') or '',
        ),
        'student_details': get_student_details_cached(ssid, timeout=timeout, user_id=user_id),
        'embedded': embedded,
        'summary_sections': summary_sections,
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
