from django.test import SimpleTestCase
from unittest.mock import patch
from clickhouse_utils.translator import translate_pg_to_clickhouse
from clickhouse_utils.executor import QueryExecutor


class AnalyticsRoutingTests(SimpleTestCase):
    def test_analytics_executor_does_not_fallback_when_clickhouse_unavailable(self):
        executor = QueryExecutor(analytics=True)
        with patch.object(QueryExecutor, 'ch_client', new=property(lambda _self: None)):
            result = executor.execute('SELECT 1')

        self.assertIn('ClickHouse analytics service is unavailable', result['error'])

    def test_analytics_executor_does_not_fallback_on_clickhouse_failure(self):
        executor = QueryExecutor(analytics=True)
        with patch.object(QueryExecutor, 'ch_client', new=property(lambda _self: object())):
            with patch.object(executor, '_execute_clickhouse', side_effect=Exception('unsupported syntax')):
                with patch.object(executor, '_execute_postgres') as postgres:
                    result = executor.execute('SELECT 1')

        self.assertEqual(result['error'], 'unsupported syntax')
        postgres.assert_not_called()


class ThreadPoolSizingTests(SimpleTestCase):
    def test_ch_io_pool_stays_under_clickhouse_concurrency_cap(self):
        from clickhouse_utils.thread_pool import get_io_pool
        self.assertEqual(get_io_pool()._max_workers, 8)

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

        self.assertIn('JSONExtractString(r.other_info, %(p0)s)', translated_sql)
        self.assertNotIn('->>', translated_sql)
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
        self.assertIn("JSONExtract(ifNull(cs.add_data, '{}'), 'l_race', 'Array(String)')", translated)
        self.assertIn("hasAny(", translated)
        self.assertIn("[%(p0)s, %(p1)s]", translated)
        self.assertNotIn('__HASANY_ARR__', translated)
        self.assertNotIn('assumeNotNull', translated)
        self.assertNotIn('jsonb_array_elements_text', translated)
        self.assertNotIn('regexp_split_to_array', translated)
        self.assertEqual(ch_params.get('p0'), 'Asian')
        self.assertEqual(ch_params.get('p1'), 'White')

        unnest_sql = f"SELECT DISTINCT v FROM (SELECT {unnest} AS v FROM current_student cs) t"
        unnest_translated, _ = translate_pg_to_clickhouse(unnest_sql, [])
        self.assertIn("arrayJoin(", unnest_translated)
        self.assertNotIn('unnest(', unnest_translated.lower().replace('arrayjoin', ''))

    def test_translate_pg_sort_scalars_and_like_percents(self):
        from dataexploration.column_utils import SS_SORT_SQL, PL_SORT_SQL

        sql = (
            f"SELECT any({SS_SORT_SQL}) AS v FROM analytics_student_results r "
            f"WHERE 1=1 AND ({PL_SORT_SQL}) IS NOT NULL"
        )
        translated, _ = translate_pg_to_clickhouse(sql, [])
        self.assertNotIn('btrim', translated.lower())
        self.assertNotIn('regexp_replace', translated.lower())
        self.assertNotIn('NUMERIC', translated.upper().replace('NULLABLE(FLOAT64)', ''))
        self.assertIn('replaceRegexpAll', translated)
        self.assertIn('AS Nullable(Float64)', translated)
        self.assertIn("LIKE 'novice%'", translated)
        self.assertNotIn('novice%%', translated)

        join_sql = (
            "SELECT cs.ssid FROM current_student cs"
            " LEFT JOIN (SELECT r.ssid AS _k, any(" + SS_SORT_SQL + ") AS _v"
            " FROM analytics_student_results r"
            " WHERE r.school_year = %s GROUP BY r.ssid) _sort_ssid ON _sort_ssid._k = cs.ssid"
            " ORDER BY coalesce(_sort_ssid._v, NULL) ASC, cs.stu_annual_key ASC LIMIT %s"
        )
        translated_join, params = translate_pg_to_clickhouse(join_sql, ['2025-2026', 50])
        self.assertNotIn('WHERE r.ssid = cs.ssid', translated_join)
        self.assertIn('_sort_ssid', translated_join)
        self.assertEqual(params['p0'], '2025-2026')
        self.assertEqual(params['p1'], 50)

    def test_translate_distinct_on_join_exposes_columns(self):
        sql = (
            "SELECT cs.ssid, se.ell FROM current_student cs"
            " LEFT JOIN ("
            " SELECT DISTINCT ON (student_id, school_year) *"
            " FROM student_enroll"
            " ORDER BY student_id, school_year, enter_date DESC NULLS LAST"
            " ) se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
            " LIMIT %s"
        )
        translated, _ = translate_pg_to_clickhouse(sql, [50])
        self.assertIn('QUALIFY', translated)
        self.assertIn('se.ell', translated)
        self.assertNotIn('AS _rn', translated)
        self.assertNotIn('DISTINCT ON', translated.upper())


class AnalyticsReadModelTests(SimpleTestCase):
    def test_schema_includes_analytics_read_models(self):
        from clickhouse_utils.schema import (
            ANALYTICS_TABLE_NAMES,
            CREATE_ANALYTICS_STUDENT_MARKS,
            CREATE_ANALYTICS_STUDENT_PROFILE,
            CREATE_ANALYTICS_STUDENT_RESULTS,
            get_all_ddl,
        )
        ddl = '\n'.join(get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_STUDENT_PROFILE, get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_STUDENT_RESULTS, get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_STUDENT_MARKS, get_all_ddl())
        for name in ANALYTICS_TABLE_NAMES:
            self.assertIn(name, ddl)


class KeysetPaginationTests(SimpleTestCase):
    def test_cursor_roundtrip_and_legacy_payload(self):
        from clickhouse_utils.pagination import (
            decode_cursor, encode_cursor, keyset_clause, cursor_from_row,
        )
        encoded = encode_cursor('student_name', 'Ada', 'sak-1', 'asc')
        decoded = decode_cursor(encoded)
        self.assertEqual(decoded['k'], 'student_name')
        self.assertEqual(decoded['v'], 'Ada')
        self.assertEqual(decoded['sak'], 'sak-1')

        import base64, json
        legacy = base64.urlsafe_b64encode(
            json.dumps({'name': 'Ada', 'sak': 'sak-1'}).encode()
        ).decode()
        legacy_decoded = decode_cursor(legacy)
        self.assertEqual(legacy_decoded['k'], 'student_name')
        self.assertEqual(legacy_decoded['v'], 'Ada')

        clause = keyset_clause('cs.student_name', 'asc')
        self.assertIn('cs.student_name > %s', clause)
        self.assertIn('cs.stu_annual_key > %s', clause)
        self.assertEqual(
            cursor_from_row(
                {'student_name': 'Ada', 'stu_annual_key': 'sak-1'},
                'student_name',
            ),
            encoded,
        )


class IncrementalSyncDecisionTests(SimpleTestCase):
    def test_choose_sync_mode_skips_unchanged_timestamped_tables(self):
        from clickhouse_utils.management.commands.sync_to_clickhouse import (
            choose_sync_mode,
        )
        self.assertEqual(
            choose_sync_mode('student_marks_list', '2026-01-01', 100, 100, 0),
            'skip',
        )
        self.assertEqual(
            choose_sync_mode('student_marks_list', '2026-01-01', 100, 90, 0),
            'full',
        )
        self.assertEqual(
            choose_sync_mode('student_marks_list', '2026-01-01', 100, 100, 5),
            'incremental',
        )
        self.assertEqual(
            choose_sync_mode('student_marks_list', '2026-01-01', 100, 100, 50),
            'full',
        )
        self.assertEqual(
            choose_sync_mode('current_student', None, 100, 100, 0),
            'full',
        )


class RemainingAnalyticsTests(SimpleTestCase):
    def test_schema_includes_ap_survey_and_projections(self):
        from clickhouse_utils.schema import (
            CREATE_ANALYTICS_AP_AWARDS,
            CREATE_ANALYTICS_AP_RESULTS,
            CREATE_ANALYTICS_SURVEY_RESPONSES,
            PROJECTION_DDL,
            get_all_ddl,
        )
        ddl = '\n'.join(get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_AP_RESULTS, get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_AP_AWARDS, get_all_ddl())
        self.assertIn(CREATE_ANALYTICS_SURVEY_RESPONSES, get_all_ddl())
        self.assertIn('analytics_ap_results', ddl)
        self.assertIn('analytics_survey_responses', ddl)
        self.assertTrue(PROJECTION_DDL)
        self.assertIn('ADD PROJECTION', PROJECTION_DDL[0])

    def test_timed_fn_preserves_name_and_sets_request_id(self):
        from clickhouse_utils.timing import current_request_id, timed_fn

        @timed_fn('unit_op')
        def sample():
            return current_request_id()

        self.assertEqual(sample.__name__, 'sample')
        self.assertTrue(sample())

    def test_dataset_sql_paginate_skips_one_per_sak_and_bare_sort(self):
        from types import SimpleNamespace
        from dataviz.utils.drill_through import _can_sql_paginate_drill

        chart = SimpleNamespace(source_type='dataset', chart_type='pie', config={})
        self.assertTrue(_can_sql_paginate_drill(chart, {}, ''))
        chart.config = {'one_row_per_stu_annual_key': True}
        self.assertFalse(_can_sql_paginate_drill(chart, {}, ''))
        chart.config = {}
        self.assertFalse(_can_sql_paginate_drill(chart, {}, 'student_name'))
        self.assertTrue(_can_sql_paginate_drill(chart, {}, 'current_student.student_name'))
