"""
Survey column utilities for Data Exploration integration.

Provides functions to generate dynamic survey columns from dataLoader survey data.
"""
import hashlib
import json
from django.core.cache import cache
from django.db import connections


def _get_survey_display_name(question_text, max_length=50):
    """Truncate and clean question text for column display."""
    if not question_text:
        return question_text
    text = str(question_text).strip()
    if len(text) > max_length:
        text = text[:max_length-3] + '...'
    return text


def _abbreviate_survey_header(survey_type, school_year, window, grade_range):
    """Build a short header label like 'SC 6-8 Spr 25-26'."""
    _type_map = {
        'skills and competencies': 'SC',
        'support and environment': 'SE',
    }
    type_abbrev = _type_map.get((survey_type or '').lower().strip())
    if not type_abbrev:
        type_abbrev = ''.join(w[0].upper() for w in (survey_type or '').split() if w)

    yr_parts = (school_year or '').split('-')
    if len(yr_parts) == 2 and len(yr_parts[0]) == 4 and len(yr_parts[1]) == 4:
        year_abbrev = f"{yr_parts[0][2:]}-{yr_parts[1][2:]}"
    else:
        year_abbrev = school_year or ''

    _win_map = {'SPRING': 'Spr', 'FALL': 'Fall', 'WINTER': 'Win', 'SUMMER': 'Sum'}
    win_abbrev = _win_map.get((window or '').upper(), window or '')

    gr = (grade_range or '').replace('Grades ', '').replace('Grade ', '').strip()

    parts = [type_abbrev]
    if gr:
        parts.append(gr)
    if win_abbrev:
        parts.append(win_abbrev)
    if year_abbrev:
        parts.append(year_abbrev)
    return ' '.join(parts)


def _generate_survey_column_key(survey_type, school_year, window, question_order, grade_range=''):
    """Generate a unique column key for a survey question."""
    # Create a readable but unique key - grade_range must be included so that
    # surveys sharing the same type/year/window but different grade bands
    # (e.g. Gr 3-5 vs Gr 6-8) produce distinct keys.
    type_short = survey_type.replace(' ', '_').replace('and', '').replace('__', '_')[:20]
    year_short = school_year.replace('-', '')[-4:]  # e.g., 2026
    window_short = window[:3].upper() if window else 'ALL'
    grade_short = (grade_range or '').replace('Grades ', '').replace('Grade ', '').replace(' ', '').replace('-', '_') or 'ALL'
    grade_short = grade_short[:6]  # keep it compact, e.g. "3_5" or "6_8"
    return f"survey_{type_short}_{year_short}_{window_short}_{grade_short}_Q{question_order + 1}"


def get_survey_columns(column_filters=None):
    """
    Get dynamic survey columns from published survey data.
    
    Returns structure:
    {
        school_year: {
            survey_type: {
                'windows': {
                    testing_window: {
                        question_order: {
                            'key': column_key,
                            'label': question_text,
                            'scale_max': 5 or 7,
                        }
                    }
                },
                'grade_range': 'Grades 3-5'
            }
        }
    }
    """
    cache_key = f"survey_columns_v8_{hashlib.md5(json.dumps(column_filters or {}, sort_keys=True).encode()).hexdigest()}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return cached_data
    
    columns = {}
    definitions = {}
    column_colors = {}  # Map column_key -> {'colors': {...}, 'scale_max': int}
    col_meta = {}  # Map column_key -> {survey_type, school_year, window, question_order}
    
    with connections['default'].cursor() as cursor:
        # Get survey definitions and their questions from published imports
        # We check if there's at least one published import for this survey definition
        cursor.execute("""
            SELECT DISTINCT
                sd.id,
                sd.survey_type,
                sd.school_year,
                sd.testing_window,
                sd.grade_range,
                sd.response_colors,
                sq.id as question_id,
                sq.question_text,
                sq."order",
                sq.response_scale_max,
                COALESCE(st.name, sq.domain) AS theme_name
            FROM "dataLoader_surveydefinition" sd
            INNER JOIN "dataLoader_surveyquestion" sq ON sq.survey_definition_id = sd.id
            LEFT JOIN "dataLoader_surveytopic" st ON st.id = sq.topic_id
            INNER JOIN "dataLoader_importlog" il ON il.survey_definition_id = sd.id
            WHERE il.ready_for_exploration = true
              AND il.status = 'COMPLETED'
              AND sd.is_active = true
              AND sq.is_active = true
            ORDER BY sd.school_year DESC, sd.survey_type, sd.testing_window, sq."order"
        """)
        
        rows = cursor.fetchall()
        
        for row in rows:
            (survey_id, survey_type, school_year, window, grade_range, response_colors,
             question_id, question_text, order, scale_max, theme_name) = row
            
            # Initialize nested structure
            # Use composite key so different grade bands of the same survey type
            # don't overwrite each other in the structure dict.
            type_grade_key = f"{survey_type}||{grade_range}" if grade_range else survey_type
            if school_year not in columns:
                columns[school_year] = {}
            if type_grade_key not in columns[school_year]:
                columns[school_year][type_grade_key] = {
                    'windows': {},
                    'grade_range': grade_range or '',
                    'survey_type': survey_type,
                }
            if window not in columns[school_year][type_grade_key]['windows']:
                columns[school_year][type_grade_key]['windows'][window] = {}
            
            # Generate column key - grade_range ensures uniqueness across grade bands
            col_key = _generate_survey_column_key(survey_type, school_year, window, order, grade_range)
            
            # Store response colors and scale for this column
            # response_colors format: {'scale_5': {...}, 'scale_7': {...},
            #                          'questions': {'<order>': {value: color}}}
            question_scale = scale_max or 5
            # psycopg3 may return JSONB columns as strings from raw cursor queries
            if isinstance(response_colors, str):
                try:
                    response_colors = json.loads(response_colors)
                except (json.JSONDecodeError, TypeError):
                    response_colors = {}
            if not isinstance(response_colors, dict):
                response_colors = {}
            # A question can override the survey-wide palette for its own
            # response range; anything not overridden falls back to scale_N.
            q_overrides = (response_colors.get('questions') or {}).get(str(order)) or {}
            effective_colors = {
                'scale_5': {**(response_colors.get('scale_5') or {}), **q_overrides},
                'scale_7': {**(response_colors.get('scale_7') or {}), **q_overrides},
            }
            _short_label = _abbreviate_survey_header(survey_type, school_year, window, grade_range)
            column_colors[col_key] = {
                'colors': effective_colors,
                'scale_max': question_scale,
                'short_label': _short_label,
                'topic': theme_name or '',
            }
            
            # Add question info
            columns[school_year][type_grade_key]['windows'][window][order] = {
                'key': col_key,
                'label': _get_survey_display_name(question_text),
                'full_text': question_text,
                'scale_max': scale_max or 7,
                'domain': theme_name or '',
                'question_id': question_id,
            }
            
            # Store metadata for use in SQL filter building
            col_meta[col_key] = {
                'survey_type': survey_type,
                'school_year': school_year,
                'window': window,
                'grade_range': grade_range,
                'question_order': order,
            }

            # Add to definitions for the data exploration app (full text for column headers)
            definitions[col_key] = question_text
    
    # Build topics_grouped for each survey_data entry so the template can group
    # questions by topic without needing DB access or Python logic in the template.
    for year_key in columns:
        for tg_key in columns[year_key]:
            survey_data = columns[year_key][tg_key]
            topics_grouped = {}
            for window, question_dict in survey_data['windows'].items():
                window_topics = {}
                for order_k in sorted(question_dict.keys()):
                    q = question_dict[order_k]
                    topic = (q.get('domain') or '').strip() or 'Uncategorized'
                    if topic not in window_topics:
                        window_topics[topic] = []
                    window_topics[topic].append(q)
                # Named topics alphabetically, 'Uncategorized' last
                named = sorted(t for t in window_topics if t != 'Uncategorized')
                sorted_topics = {t: window_topics[t] for t in named}
                if 'Uncategorized' in window_topics:
                    sorted_topics['Uncategorized'] = window_topics['Uncategorized']
                topics_grouped[window] = sorted_topics
            survey_data['topics_grouped'] = topics_grouped

    result = {
        'structure': columns,
        'definitions': definitions,
        'colors': column_colors,  # Map column_key -> {value: color}
        'col_meta': col_meta,  # Map column_key -> survey metadata for SQL filtering
    }

    # Cache for 5 minutes
    cache.set(cache_key, result, 300)
    
    return result


def get_survey_data_for_students(student_ids=None, school_year=None, ssids=None):
    """
    Fetch survey response data for a list of students.
    
    Args:
        student_ids: Optional list of student_id values to fetch data for
        school_year: Optional school year filter (if None, all attached survey responses are fetched)
        ssids: Optional list of ssid values to match
    
    Returns:
        Dict mapping student_id / ssid -> {column_key: value}
    """
    clean_ids = [str(s).strip() for s in (student_ids or []) if str(s).strip()]
    clean_ssids = [str(s).strip() for s in (ssids or []) if str(s).strip()]
    if not clean_ids and not clean_ssids:
        return {}

    where_parts = []
    params = []
    if clean_ids:
        where_parts.append("student_id = ANY(%s)")
        params.append(clean_ids)
    if clean_ssids:
        where_parts.append("(ssid != '' AND ssid = ANY(%s))")
        params.append(clean_ssids)

    ch_where = " OR ".join(where_parts)
    year_sql = ""
    if school_year:
        year_sql = " AND school_year = %s"
        params.append(school_year)

    from dataviz.utils.data_loader import _analytics_rows
    rows = _analytics_rows(
        "SELECT student_id, ssid, survey_type, school_year, testing_window, grade_range, "
        "question_order, value FROM analytics_survey_responses "
        f"WHERE ({ch_where}){year_sql}",
        params,
    )
    if rows is None:
        with connections['default'].cursor() as cursor:
            pg_where_parts = []
            pg_params = []
            if clean_ids:
                pg_where_parts.append("sr.student_id = ANY(%s)")
                pg_params.append(clean_ids)
            if clean_ssids:
                pg_where_parts.append("(sr.ssid IS NOT NULL AND sr.ssid != '' AND sr.ssid = ANY(%s))")
                pg_params.append(clean_ssids)

            where_clauses = [f"({' OR '.join(pg_where_parts)})"]
            if school_year:
                where_clauses.append("sd.school_year = %s")
                pg_params.append(school_year)
            where_clauses.append("(il.ready_for_exploration = true OR sd.ready_for_exploration = true)")
            where_clauses.append("il.status = 'COMPLETED'")
            where_clauses.append("sd.is_active = true")
            cursor.execute(f"""
                SELECT
                    sr.student_id,
                    sr.ssid,
                    sd.survey_type,
                    sd.school_year,
                    sd.testing_window,
                    sd.grade_range,
                    sq."order",
                    sr.value
                FROM "dataLoader_surveyresponse" sr
                INNER JOIN "dataLoader_surveyquestion" sq ON sr.question_id = sq.id
                INNER JOIN "dataLoader_surveydefinition" sd ON sq.survey_definition_id = sd.id
                INNER JOIN "dataLoader_importlog" il ON sr.import_log_id = il.id
                WHERE {" AND ".join(where_clauses)}
                ORDER BY sr.student_id, sq."order"
            """, pg_params)
            rows = [
                {
                    'student_id': r[0], 'ssid': r[1], 'survey_type': r[2], 'school_year': r[3],
                    'testing_window': r[4], 'grade_range': r[5],
                    'question_order': r[6], 'value': r[7],
                }
                for r in cursor.fetchall()
            ]

    results = {}
    for row in rows:
        col_key = _generate_survey_column_key(
            row.get('survey_type'), row.get('school_year'), row.get('testing_window'),
            row.get('question_order'), row.get('grade_range') or '',
        )
        val = row.get('value')
        stu_id = str(row.get('student_id') or '').strip()
        ssid_val = str(row.get('ssid') or '').strip()
        if stu_id:
            results.setdefault(stu_id, {})[col_key] = val
        if ssid_val:
            results.setdefault(ssid_val, {})[col_key] = val
    return results


def get_published_survey_summary():
    """
    Get a summary of all published surveys for display.
    
    Returns list of dicts with survey info.
    """
    with connections['default'].cursor() as cursor:
        cursor.execute("""
            SELECT 
                sd.id,
                sd.name,
                sd.survey_type,
                sd.school_year,
                sd.testing_window,
                sd.grade_range,
                COUNT(DISTINCT sq.id) as question_count,
                COUNT(DISTINCT sr.student_id) as student_count,
                sd.published_at
            FROM "dataLoader_surveydefinition" sd
            LEFT JOIN "dataLoader_surveyquestion" sq ON sq.survey_definition_id = sd.id AND sq.is_active = true
            LEFT JOIN "dataLoader_surveyresponse" sr ON sr.question_id = sq.id
            WHERE sd.ready_for_exploration = true
              AND sd.is_active = true
            GROUP BY sd.id, sd.name, sd.survey_type, sd.school_year, 
                     sd.testing_window, sd.grade_range, sd.published_at
            ORDER BY sd.school_year DESC, sd.survey_type
        """)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'id': row[0],
                'name': row[1],
                'survey_type': row[2],
                'school_year': row[3],
                'testing_window': row[4],
                'grade_range': row[5],
                'question_count': row[6],
                'student_count': row[7],
                'published_at': row[8],
            })
        
        return results
