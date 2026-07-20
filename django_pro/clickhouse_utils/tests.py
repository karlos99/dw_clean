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
        self.assertEqual(ch_params['p5'], 'Mathematics')       # ILIKE is not lowered by python if it doesn't contain '%'

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
