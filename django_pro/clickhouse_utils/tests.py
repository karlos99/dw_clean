from django.test import SimpleTestCase
from clickhouse_utils.translator import translate_pg_to_clickhouse

class SQLTranslatorTests(SimpleTestCase):
    def test_translate_any_placeholder_with_preceding_placeholder(self):
        sql = """
            SELECT r.local_student_id, (r.other_info ->> %s)
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            WHERE r.local_student_id = ANY(%s)
            AND r.school_year = %s
            AND t.test_name ILIKE %s
            AND t.subject ILIKE %s
        """
        params = [
            'some_key',
            ['student_a', 'student_b'],
            '2025-2026',
            '%Smarter Balanced%',
            'Mathematics'
        ]
        translated_sql, ch_params = translate_pg_to_clickhouse(sql, params)

        # Assert correct parameter names mapping
        self.assertIn('%(p0)s', translated_sql)
        self.assertIn('IN (%(p1)s, %(p2)s)', translated_sql)
        self.assertIn('%(p3)s', translated_sql)
        self.assertIn('%(p4)s', translated_sql)
        self.assertIn('%(p5)s', translated_sql)

        self.assertEqual(ch_params['p0'], 'some_key')
        self.assertEqual(ch_params['p1'], 'student_a')
        self.assertEqual(ch_params['p2'], 'student_b')
        self.assertEqual(ch_params['p3'], '2025-2026')
        self.assertEqual(ch_params['p4'], '%smarter balanced%')  # ILIKE is lowered if it contains '%'
        self.assertEqual(ch_params['p5'], 'Mathematics')

    def test_schema_includes_results_with_tests(self):
        from clickhouse_utils.schema import get_all_ddl, CREATE_RESULTS_WITH_TESTS
        ddl = get_all_ddl()
        self.assertIn(CREATE_RESULTS_WITH_TESTS, ddl)
        combined = '\n'.join(ddl)
        self.assertIn('results_with_tests', combined)       # ILIKE is not lowered by python if it doesn't contain '%'

    def test_translate_any_with_empty_collections(self):
        sql = "SELECT * FROM students WHERE ssid = ANY(%s)"
        
        # Test empty list
        sql_list, params_list = translate_pg_to_clickhouse(sql, [[]])
        self.assertIn('IN (NULL)', sql_list)
        self.assertEqual(params_list, {})

        # Test empty tuple
        sql_tuple, params_tuple = translate_pg_to_clickhouse(sql, [()])
        self.assertIn('IN (NULL)', sql_tuple)
        self.assertEqual(params_tuple, {})

        # Test empty set
        sql_set, params_set = translate_pg_to_clickhouse(sql, [set()])
        self.assertIn('IN (NULL)', sql_set)
        self.assertEqual(params_set, {})

    def test_translate_tuple_and_set_parameters_to_list(self):
        sql = "SELECT * FROM students WHERE grade = %s AND school = %s"
        params = [('04', '05'), {'school_a', 'school_b'}]
        
        translated_sql, ch_params = translate_pg_to_clickhouse(sql, params)
        
        # Assert type conversion
        self.assertIsInstance(ch_params['p0'], list)
        self.assertIsInstance(ch_params['p1'], list)
        
        # Verify values
        self.assertEqual(ch_params['p0'], ['04', '05'])
        self.assertEqual(sorted(ch_params['p1']), ['school_a', 'school_b'])

    def test_translate_ilike_string_literal(self):
        sql = "SELECT 1 WHERE t.test_name ILIKE 'ELPAC' AND t.subject ILIKE 'summative'"
        translated_sql, _params = translate_pg_to_clickhouse(sql, [])
        self.assertIn("lower(t.test_name) LIKE lower('ELPAC')", translated_sql)
        self.assertIn("lower(t.subject) LIKE lower('summative')", translated_sql)

    def test_translate_ilike_or_group_does_not_wrap_boolean(self):
        sql = (
            "SELECT cs.student_name FROM current_student cs "
            "WHERE (cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s) "
            "AND cs.school_year = %s LIMIT %s OFFSET %s"
        )
        params = ['%8037020790%', '%8037020790%', '%8037020790%', '2025-2026', 50, 0]
        translated_sql, ch_params = translate_pg_to_clickhouse(sql, params)

        self.assertNotIn('lower((cs.student_name) LIKE', translated_sql)
        self.assertIn('lower(cs.student_name) LIKE LOWER(%(p0)s)', translated_sql)
        self.assertIn('lower(cs.ssid) LIKE LOWER(%(p1)s)', translated_sql)
        self.assertIn('lower(cs.student_id) LIKE LOWER(%(p2)s)', translated_sql)
        self.assertEqual(ch_params['p0'], '%8037020790%')

    def test_translate_race_list_display_and_overlap(self):
        from dataexploration.view_modules.column_management import (
            race_list_display_expr,
            race_list_overlap_sql,
            race_list_unnest_select_expr,
            race_list_blank_sql,
        )
        display = race_list_display_expr('cs')
        overlap = race_list_overlap_sql('cs')
        unnest = race_list_unnest_select_expr('cs')
        blank = race_list_blank_sql('cs')

        sql = f"SELECT {display} AS race_list FROM current_student cs WHERE {overlap} OR {blank}"
        translated, ch_params = translate_pg_to_clickhouse(sql, [['Asian', 'White']])

        self.assertIn("arrayStringConcat(", translated)
        self.assertIn("JSONExtract(cs.add_data, 'l_race', 'Array(String)')", translated)
        self.assertIn("hasAny(", translated)
        self.assertIn("[%(p0)s, %(p1)s]", translated)
        self.assertNotIn('__HASANY_ARR__', translated)
        self.assertNotIn('jsonb_array_elements_text', translated)
        self.assertNotIn('regexp_split_to_array', translated)
        self.assertEqual(ch_params.get('p0'), 'Asian')
        self.assertEqual(ch_params.get('p1'), 'White')

        unnest_sql = f"SELECT DISTINCT v FROM (SELECT {unnest} AS v FROM current_student cs) t"
        unnest_translated, _ = translate_pg_to_clickhouse(unnest_sql, [])
        self.assertIn("arrayJoin(", unnest_translated)
        self.assertNotIn('unnest(', unnest_translated.lower().replace('arrayjoin', ''))
