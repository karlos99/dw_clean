"""PostgreSQL extracts for ClickHouse course-program analytics tables."""

# Resolve catalog entry without OR inside LATERAL (uses PK / small-table lookups).
_CATALOG_RESOLVE_JOINS = """
LEFT JOIN courses_catalog cc_k
    ON NULLIF(BTRIM(s.course_key), '') IS NOT NULL
   AND cc_k.course_key = NULLIF(BTRIM(s.course_key), '')
LEFT JOIN courses_catalog cc_c
    ON cc_k.course_key IS NULL
   AND NULLIF(BTRIM(s.course_code), '') IS NOT NULL
   AND cc_c.course_id = NULLIF(BTRIM(s.course_code), '')
LEFT JOIN courses_catalog cc_s
    ON cc_k.course_key IS NULL
   AND cc_c.course_key IS NULL
   AND NULLIF(BTRIM(s.course_state_equivalence_code), '') IS NOT NULL
   AND cc_s.state_course_code = NULLIF(BTRIM(s.course_state_equivalence_code), '')
LEFT JOIN coursecatalog_catalogplacement pl
    ON pl.catalog_entry_id = COALESCE(cc_k.course_key, cc_c.course_key, cc_s.course_key)
LEFT JOIN coursecatalog_catalogtag tg ON pl.tag_id = tg.id
LEFT JOIN coursecatalog_catalogcategory cat ON pl.category_id = cat.id
LEFT JOIN coursecatalog_catalogdiscipline disc ON pl.discipline_id = disc.id
LEFT JOIN coursecatalog_cataloglevel lv ON pl.level_id = lv.id
LEFT JOIN coursecatalog_cte_pathways cte
    ON NULLIF(BTRIM(s.course_code), '') IS NOT NULL
   AND NULLIF(BTRIM(cte.course_id), '') IS NOT NULL
   AND cte.course_id = NULLIF(BTRIM(s.course_code), '')
"""

# One pass over enrolled schedule → row-level staff + CTE dimensions (sync temp table).
CREATE_MATCHED_TEMP_SQL = f"""
CREATE TEMP TABLE _course_program_matched ON COMMIT PRESERVE ROWS AS
SELECT
    s.stu_annual_key,
    s.school_year,
    tg.name AS staff_tag,
    cat.name AS staff_category,
    disc.name AS staff_discipline,
    lv.name AS staff_level,
    cte.career_cluster AS cte_category,
    cte.cte_pathway AS cte_discipline,
    cte.cte_course_level AS cte_level,
    (cte.course_id IS NOT NULL AND BTRIM(cte.course_id) <> '') AS has_cte
FROM schedule_list s
{_CATALOG_RESOLVE_JOINS}
WHERE s.schedule_status IN ('Active', 'Completed')
  AND s.stu_annual_key IS NOT NULL AND BTRIM(s.stu_annual_key) <> ''
  AND s.school_year IS NOT NULL AND BTRIM(s.school_year) <> ''
"""

MATCHED_TEMP_INDEX_SQL = (
    "CREATE INDEX _course_program_matched_stu_idx "
    "ON _course_program_matched (stu_annual_key, school_year)"
)

COURSE_PROGRAM_AGG_FROM_TEMP = """
SELECT
    stu_annual_key,
    school_year,
    COALESCE(NULLIF(TRIM(BOTH ', ' FROM CONCAT_WS(', ',
        NULLIF(string_agg(DISTINCT staff_tag, ', ' ORDER BY staff_tag)
            FILTER (WHERE staff_tag IS NOT NULL AND staff_tag <> ''), ''),
        CASE WHEN bool_or(has_cte) THEN 'CTE' ELSE NULL END
    )), ''), '') AS course_prog_tag,
    COALESCE(NULLIF(TRIM(BOTH ', ' FROM CONCAT_WS(', ',
        NULLIF(string_agg(DISTINCT staff_category, ', ' ORDER BY staff_category)
            FILTER (WHERE staff_category IS NOT NULL AND staff_category <> ''), ''),
        NULLIF(string_agg(DISTINCT cte_category, ', ' ORDER BY cte_category)
            FILTER (WHERE cte_category IS NOT NULL AND cte_category <> ''), '')
    )), ''), '') AS course_prog_category,
    COALESCE(NULLIF(TRIM(BOTH ', ' FROM CONCAT_WS(', ',
        NULLIF(string_agg(DISTINCT staff_discipline, ', ' ORDER BY staff_discipline)
            FILTER (WHERE staff_discipline IS NOT NULL AND staff_discipline <> ''), ''),
        NULLIF(string_agg(DISTINCT cte_discipline, ', ' ORDER BY cte_discipline)
            FILTER (WHERE cte_discipline IS NOT NULL AND cte_discipline <> ''), '')
    )), ''), '') AS course_prog_discipline,
    COALESCE(NULLIF(TRIM(BOTH ', ' FROM CONCAT_WS(', ',
        NULLIF(string_agg(DISTINCT staff_level, ', ' ORDER BY staff_level)
            FILTER (WHERE staff_level IS NOT NULL AND staff_level <> ''), ''),
        NULLIF(string_agg(DISTINCT cte_level, ', ' ORDER BY cte_level)
            FILTER (WHERE cte_level IS NOT NULL AND cte_level <> ''), '')
    )), ''), '') AS course_prog_level
FROM _course_program_matched
GROUP BY stu_annual_key, school_year
"""

COURSE_PROGRAM_VALUES_FROM_TEMP = """
SELECT DISTINCT stu_annual_key, school_year, 'tag' AS dimension, staff_tag AS value
FROM _course_program_matched
WHERE staff_tag IS NOT NULL AND BTRIM(staff_tag) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'tag', 'CTE'
FROM _course_program_matched
WHERE has_cte
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'category', staff_category
FROM _course_program_matched
WHERE staff_category IS NOT NULL AND BTRIM(staff_category) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'category', cte_category
FROM _course_program_matched
WHERE cte_category IS NOT NULL AND BTRIM(cte_category) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'discipline', staff_discipline
FROM _course_program_matched
WHERE staff_discipline IS NOT NULL AND BTRIM(staff_discipline) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'discipline', cte_discipline
FROM _course_program_matched
WHERE cte_discipline IS NOT NULL AND BTRIM(cte_discipline) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'level', staff_level
FROM _course_program_matched
WHERE staff_level IS NOT NULL AND BTRIM(staff_level) <> ''
UNION ALL
SELECT DISTINCT stu_annual_key, school_year, 'level', cte_level
FROM _course_program_matched
WHERE cte_level IS NOT NULL AND BTRIM(cte_level) <> ''
"""

# Optional helper indexes for catalog resolution (no-op if present).
COURSE_PROGRAM_CATALOG_INDEXES = (
    "CREATE INDEX IF NOT EXISTS courses_catalog_course_id_idx "
    "ON courses_catalog (course_id) WHERE course_id IS NOT NULL AND course_id <> ''",
    "CREATE INDEX IF NOT EXISTS courses_catalog_state_course_code_idx "
    "ON courses_catalog (state_course_code) "
    "WHERE state_course_code IS NOT NULL AND state_course_code <> ''",
)

COURSE_PROGRAM_AGG_COLUMNS = (
    'stu_annual_key',
    'school_year',
    'course_prog_tag',
    'course_prog_category',
    'course_prog_discipline',
    'course_prog_level',
)

COURSE_PROGRAM_VALUES_COLUMNS = (
    'stu_annual_key',
    'school_year',
    'dimension',
    'value',
)

DIMENSION_BY_COLUMN = {
    'course_prog_tag': 'tag',
    'course_prog_category': 'category',
    'course_prog_discipline': 'discipline',
    'course_prog_level': 'level',
}

