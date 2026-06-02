"""Data query helpers for the mathR app.

Uses raw SQL for the main student query (matching the provided SQL spec) and
ORM for MathRecommendation lookups.  Results are cached in Redis.
"""
from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import connection

from mathr.models import MathRecommendation


# ---------------------------------------------------------------------------
# Current school year detection
# ---------------------------------------------------------------------------

def get_current_school_year() -> str:
    """Return the default school_year from ProjectConfig, falling back to student_enroll."""
    try:
        from landing.utils import get_project_config
        val = get_project_config().default_school_year
        if val:
            return val
    except Exception:
        pass

    cache_key = 'mathr:current_school_year'
    val = cache.get(cache_key)
    if val:
        return val
    with connection.cursor() as cur:
        cur.execute(
            "SELECT school_year FROM student_enroll "
            "ORDER BY school_year DESC LIMIT 1"
        )
        row = cur.fetchone()
    val = row[0] if row else '2025-2026'
    cache.set(cache_key, val, timeout=3600)
    return val


# ---------------------------------------------------------------------------
# Math suggestion options
# ---------------------------------------------------------------------------

BASE_MATH_OPTIONS: list[str] = ['Math I', 'HS Compacted Math']


def get_math_options(
    course_subject: str | None,
    school_name: str | None,
    course_name: str | None,
) -> list[str]:
    """Return the dropdown options for a student's math recommendation.

    Parameters map directly to schedule_list columns:
      - course_subject  → schoolDepartment in the original JS
      - school_name     → schoolName
      - course_name     → currentCourse
    """
    base = list(BASE_MATH_OPTIONS)

    if course_subject == 'Special Education':
        return base + ['Math IA']
    if school_name in ('Indio MS', 'Thomas Jefferson MS'):
        return base + ['Math I HP']
    if course_name == 'MS Compacted Math: Year 2' and school_name != 'Indio MS':
        return base + ['Math II']
    return base


# ---------------------------------------------------------------------------
# Student data query (raw SQL — matches the spec query)
# ---------------------------------------------------------------------------

_STUDENT_SQL = """
SELECT
  se.stu_annual_key,
  se.ssid,
  se.student_id,
  se.last_name,
  se.first_name,
  se.grade,
  sl.school_name,
  sl.state_school_id,
  sl.course_subject,
  sl.course_name,
  sl.period,
  sl.staff_employee_id,
  MAX(CASE WHEN sml.mark_period = 'P1' THEN sml.mark END) AS p1,
  MAX(CASE WHEN sml.mark_period = 'P2' THEN sml.mark END) AS p2,
  MAX(CASE WHEN sml.mark_period = 'P3' THEN sml.mark END) AS p3,
  MAX(CASE WHEN sml.mark_period = 'P4' THEN sml.mark END) AS p4,
  MAX(CASE WHEN sml.mark_period = 'Q1' THEN sml.mark END) AS q1,
  MAX(CASE WHEN sml.mark_period = 'Q2' THEN sml.mark END) AS q2,
  MAX(CASE WHEN sml.mark_period = 'Q3' THEN sml.mark END) AS q3,
  MAX(CASE WHEN sml.mark_period = 'Q4' THEN sml.mark END) AS q4,
  MAX(CASE WHEN t.test_window = 'Fall'   THEN r.pl END) AS iready_pl_fall,
  MAX(CASE WHEN t.test_window = 'Winter' THEN r.pl END) AS iready_pl_winter
FROM student_enroll se
INNER JOIN schedule_list sl
  ON sl.stu_annual_key = se.stu_annual_key
LEFT JOIN student_marks_list sml
  ON se.stu_annual_key = sml.stu_annual_key
  AND (sml.course_name LIKE '%%Math%%' OR sml.subject LIKE '%%Math%%')
LEFT JOIN results r
  ON se.ssid = r.ssid
  AND r.school_year = %s
LEFT JOIN tests t
  ON t.test_group_key = r.test_group_key
  AND t.test_name = 'iready'
  AND t.subject = 'Math'
WHERE (sl.course_name LIKE '%%Math%%' OR sl.course_subject LIKE '%%Math%%')
  AND se.grade = '08'
  AND sl.schedule_status = 'Active'
"""

_GROUP_BY = """
GROUP BY
  se.stu_annual_key, se.ssid, se.student_id, se.last_name,
  se.first_name, se.grade, sl.school_name, sl.state_school_id,
  sl.course_subject, sl.course_name, sl.period, sl.staff_employee_id
ORDER BY se.last_name, se.first_name
"""

_COLUMNS = [
    'stu_annual_key', 'ssid', 'student_id', 'last_name', 'first_name',
    'grade', 'school_name', 'state_school_id', 'course_subject',
    'course_name', 'period', 'staff_employee_id',
    'p1', 'p2', 'p3', 'p4', 'q1', 'q2', 'q3', 'q4',
    'iready_pl_fall', 'iready_pl_winter',
]


def get_math_students(
    school_year: str,
    *,
    staff_employee_id: str | None = None,
    state_school_id: str | None = None,
    period: str | None = None,
) -> list[dict[str, Any]]:
    """Execute the base student query with role-based filters.

    Returns a list of dicts, one per student row.
    """
    cache_parts = ['mathr:students', school_year]
    filters: list[str] = []
    params: list[Any] = [school_year]  # first param for r.school_year

    if staff_employee_id:
        filters.append('AND sl.staff_employee_id = %s')
        params.append(staff_employee_id)
        cache_parts.append(f'staff:{staff_employee_id}')

    if state_school_id:
        filters.append('AND sl.state_school_id = %s')
        params.append(state_school_id)
        cache_parts.append(f'school:{state_school_id}')

    if period:
        filters.append('AND sl.period = %s')
        params.append(period)
        cache_parts.append(f'period:{period}')

    cache_key = ':'.join(cache_parts)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    sql = _STUDENT_SQL + '\n'.join(filters) + _GROUP_BY

    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    result = [dict(zip(_COLUMNS, row)) for row in rows]
    cache.set(cache_key, result, timeout=300)
    return result


# ---------------------------------------------------------------------------
# Teacher periods
# ---------------------------------------------------------------------------

def get_teacher_periods(school_year: str, staff_employee_id: str) -> list[str]:
    """Return distinct periods for a teacher's math schedule."""
    cache_key = f'mathr:periods:{school_year}:{staff_employee_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with connection.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT period FROM schedule_list "
            "WHERE staff_employee_id = %s "
            "AND (course_name LIKE '%%Math%%' OR course_subject LIKE '%%Math%%') "
            "AND schedule_status = 'Active' "
            "ORDER BY period",
            [staff_employee_id],
        )
        rows = cur.fetchall()

    result = [r[0] for r in rows]
    cache.set(cache_key, result, timeout=600)
    return result


def get_school_teachers(school_year: str, state_school_id: str) -> list[dict[str, str]]:
    """Return list of {staff_employee_id, staff_name} for math teachers at a school."""
    cache_key = f'mathr:school_teachers:{school_year}:{state_school_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with connection.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT sl.staff_employee_id, MAX(sl.staff_name) AS staff_name "
            "FROM schedule_list sl "
            "INNER JOIN student_enroll se ON se.stu_annual_key = sl.stu_annual_key "
            "WHERE sl.state_school_id = %s "
            "AND (sl.course_name LIKE '%%Math%%' OR sl.course_subject LIKE '%%Math%%') "
            "AND se.grade = '08' AND sl.schedule_status = 'Active' "
            "GROUP BY sl.staff_employee_id "
            "ORDER BY staff_name",
            [state_school_id],
        )
        rows = cur.fetchall()

    result = [
        {'staff_employee_id': r[0], 'staff_name': r[1] or r[0]}
        for r in rows
    ]
    cache.set(cache_key, result, timeout=600)
    return result


# ---------------------------------------------------------------------------
# Recommendation map (existing selections keyed by stu_annual_key)
# ---------------------------------------------------------------------------

def get_recommendation_map(school_year: str, state_school_id: str | None = None) -> dict[str, str]:
    """Return {stu_annual_key: math_selection} for already-saved recs."""
    qs = MathRecommendation.objects.filter(school_year=school_year)
    if state_school_id:
        qs = qs.filter(state_school_id=state_school_id)
    return dict(qs.values_list('stu_annual_key', 'math_selection'))


# ---------------------------------------------------------------------------
# Completion statistics
# ---------------------------------------------------------------------------

def get_completion_by_school(school_year: str) -> list[dict[str, Any]]:
    """District-level: completion counts per school."""
    cache_key = f'mathr:completion:by_school:{school_year}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with connection.cursor() as cur:
        cur.execute("""
            SELECT
                sl.state_school_id,
                sl.school_name,
                COUNT(DISTINCT se.stu_annual_key) AS total_students,
                COUNT(DISTINCT mr.stu_annual_key) AS completed
            FROM student_enroll se
            INNER JOIN schedule_list sl ON sl.stu_annual_key = se.stu_annual_key
            LEFT JOIN mathr_recommendation mr
                ON mr.stu_annual_key = se.stu_annual_key
                AND mr.school_year = %s
            WHERE (sl.course_name LIKE '%%Math%%' OR sl.course_subject LIKE '%%Math%%')
                AND se.grade = '08'
                AND sl.schedule_status = 'Active'
            GROUP BY sl.state_school_id, sl.school_name
            ORDER BY sl.school_name
        """, [school_year])
        rows = cur.fetchall()

    result = [
        {
            'state_school_id': r[0],
            'school_name': r[1],
            'total': r[2],
            'completed': r[3],
            'pct': round(r[3] / r[2] * 100) if r[2] else 0,
        }
        for r in rows
    ]
    cache.set(cache_key, result, timeout=300)
    return result


def get_completion_by_teacher(school_year: str, state_school_id: str) -> list[dict[str, Any]]:
    """School-level: completion counts per teacher."""
    cache_key = f'mathr:completion:by_teacher:{school_year}:{state_school_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    with connection.cursor() as cur:
        cur.execute("""
            SELECT
                sl.staff_employee_id,
                MAX(sl.staff_name) AS staff_name,
                COUNT(DISTINCT se.stu_annual_key) AS total_students,
                COUNT(DISTINCT mr.stu_annual_key) AS completed
            FROM student_enroll se
            INNER JOIN schedule_list sl ON sl.stu_annual_key = se.stu_annual_key
            LEFT JOIN mathr_recommendation mr
                ON mr.stu_annual_key = se.stu_annual_key
                AND mr.school_year = %s
            WHERE (sl.course_name LIKE '%%Math%%' OR sl.course_subject LIKE '%%Math%%')
                AND se.grade = '08'
                AND sl.schedule_status = 'Active'
                AND sl.state_school_id = %s
            GROUP BY sl.staff_employee_id
            ORDER BY staff_name
        """, [school_year, state_school_id])
        rows = cur.fetchall()

    result = [
        {
            'staff_employee_id': r[0],
            'staff_name': r[1] or r[0],
            'total': r[2],
            'completed': r[3],
            'pct': round(r[3] / r[2] * 100) if r[2] else 0,
        }
        for r in rows
    ]
    cache.set(cache_key, result, timeout=300)
    return result


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------

def invalidate_mathr_caches(school_year: str) -> None:
    """Clear caches after a recommendation is saved."""
    # Completion caches use deterministic prefixes — delete by pattern
    # django-redis supports delete_pattern; fall back to individual keys
    try:
        cache.delete_pattern(f'mathr:completion:*{school_year}*')  # type: ignore[attr-defined]
        cache.delete_pattern(f'mathr:students:{school_year}*')  # type: ignore[attr-defined]
    except AttributeError:
        # Non-redis backend fallback — just let TTL handle it
        pass
