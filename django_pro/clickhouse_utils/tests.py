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
