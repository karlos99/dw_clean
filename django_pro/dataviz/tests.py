from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from dataviz.models import SavedChart, Dataset
from dataviz.utils.compiler import compile_dataset_to_sql
from dataviz.utils.drill_through import execute_drill_query

User = get_user_model()

class CompilerSqlTests(SimpleTestCase):
    def test_compile_dataset_to_sql_uses_any_for_multi_value_filters(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )

        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'grade')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."grade"'):
            sql, params = compile_dataset_to_sql(
                dataset,
                interactive_filters={'current_student.grade': ['K', '1']},
                user=None,
            )

        self.assertIn('"current_student"."grade" = ANY(%s)', sql)
        self.assertEqual(params[0], 'Active')
        self.assertEqual(params[1], ['K', '1'])

    def test_compile_dataset_to_sql_status_default_active_with_interactive_filters(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'grade')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."grade"'):
            sql, params = compile_dataset_to_sql(
                dataset,
                interactive_filters={'current_student.grade': ['K']},
                user=None,
            )
        self.assertIn('"current_student"."status" = %s', sql)
        self.assertIn('Active', params)

    def test_compile_dataset_to_sql_status_no_implicit_filter_for_dataset_builder(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'grade')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."grade"'):
            sql, params = compile_dataset_to_sql(dataset, interactive_filters={}, user=None)
        self.assertNotIn('"current_student"."status"', sql)

    def test_compile_dataset_to_sql_status_explicit_all(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'grade')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."grade"'):
            sql, params = compile_dataset_to_sql(
                dataset, interactive_filters={}, user=None, status_filter=None,
            )
        self.assertNotIn('"current_student"."status"', sql)

    def test_compile_dataset_to_sql_range_filters(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.atendace_rate'],
            updated_at=None,
        )

        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'atendace_rate'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'atendace_rate')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."atendace_rate"'):
            
            # Test include range
            sql, params = compile_dataset_to_sql(
                dataset,
                interactive_filters={'current_student.atendace_rate': {'gte': 90.0, 'lte': 95.0}},
                user=None,
            )
            self.assertIn('"current_student"."atendace_rate" >= %s', sql)
            self.assertIn('"current_student"."atendace_rate" <= %s', sql)
            self.assertEqual(params[1:], [90.0, 95.0])

            # Test exclude range
            sql, params = compile_dataset_to_sql(
                dataset,
                interactive_filters={'current_student.atendace_rate': {'gte': 90.0, 'lte': 95.0, 'exclude': True}},
                user=None,
            )
            self.assertIn('("current_student"."atendace_rate" < %s OR "current_student"."atendace_rate" > %s OR "current_student"."atendace_rate" IS NULL)', sql)
            self.assertEqual(params[1:], [90.0, 95.0])

    def test_compile_dataset_to_sql_order_by(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade', 'current_student.student_name'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade', 'student_name'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', side_effect=lambda c, t: (c.split('.')[0], c.split('.')[1])), \
             patch('dataviz.utils.compiler.format_column_expr', side_effect=lambda t, c: f'"{t}"."{c}"'):
            sql, params = compile_dataset_to_sql(
                dataset,
                interactive_filters={},
                user=None,
                order_by=[
                    {'column': 'current_student.grade', 'direction': 'asc'},
                    {'column': 'current_student.student_name', 'direction': 'desc'},
                ],
            )
        self.assertIn('ORDER BY "current_student"."grade" ASC, "current_student"."student_name" DESC', sql)

    def test_compile_dataset_to_sql_order_by_defaults_asc(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', side_effect=lambda c, t: (c.split('.')[0], c.split('.')[1])), \
             patch('dataviz.utils.compiler.format_column_expr', side_effect=lambda t, c: f'"{t}"."{c}"'):
            sql, params = compile_dataset_to_sql(
                dataset, interactive_filters={}, user=None,
                order_by=[{'column': 'current_student.grade'}],
            )
        self.assertIn('ORDER BY "current_student"."grade" ASC', sql)

    def test_compile_dataset_to_sql_order_by_none_skipped(self):
        dataset = SimpleNamespace(
            base_table='current_student',
            distinct=False,
            joins=[],
            filters=[],
            columns=['current_student.grade'],
            updated_at=None,
        )
        with patch('dataviz.utils.compiler.get_allowed_dataset_tables', return_value={'current_student'}), \
             patch('dataviz.utils.compiler.get_columns_for_table', return_value={'grade'}), \
             patch('dataviz.utils.compiler.validate_column_identifier', return_value=('current_student', 'grade')), \
             patch('dataviz.utils.compiler.format_column_expr', return_value='"current_student"."grade"'):
            sql, params = compile_dataset_to_sql(dataset, interactive_filters={}, user=None)
        self.assertNotIn('ORDER BY', sql)

    def test_build_where_clause_range_filters(self):
        from dataviz.views.api.query_api import _build_where_clause

        parts, params = _build_where_clause({
            'fep_date': {'gte': '2024-01-01', 'lte': '2024-12-31'},
            'atendace_rate': {'gte': 90, 'lte': 95, 'exclude': True},
        })
        self.assertIn('se.fep_date >= %s', parts)
        self.assertIn('se.fep_date <= %s', parts)
        self.assertTrue(any('cs.atendace_rate < %s' in p for p in parts))
        self.assertTrue(any('cs.atendace_rate > %s' in p for p in parts))
        self.assertEqual(params, ['2024-01-01', '2024-12-31', 90, 95])


class StatusFilterTests(SimpleTestCase):
    def test_normalize_status_default_active(self):
        from dataviz.utils.status_filter import normalize_status_value
        self.assertEqual(normalize_status_value({}), 'Active')
        self.assertEqual(normalize_status_value({'grade': ['K']}), 'Active')

    def test_normalize_status_explicit_all(self):
        from dataviz.utils.status_filter import normalize_status_value
        self.assertIsNone(normalize_status_value({'status': []}))
        self.assertIsNone(normalize_status_value({'status': ''}))
        self.assertIsNone(normalize_status_value({'status': None}))

    def test_normalize_status_explicit_values(self):
        from dataviz.utils.status_filter import normalize_status_value
        self.assertEqual(normalize_status_value({'status': 'Inactive'}), 'Inactive')
        self.assertEqual(normalize_status_value({'status': ['Active', 'Inactive']}), ['Active', 'Inactive'])

    def test_apply_status_filter_sql(self):
        from dataviz.utils.status_filter import apply_status_filter
        where, params = [], []
        apply_status_filter(where, params, {})
        self.assertEqual(where, ['cs.status = %s'])
        self.assertEqual(params, ['Active'])

        where, params = [], []
        apply_status_filter(where, params, {'status': []})
        self.assertEqual(where, [])
        self.assertEqual(params, [])

        where, params = [], []
        apply_status_filter(where, params, {'status': ['Inactive']})
        self.assertEqual(where, ['cs.status = %s'])
        self.assertEqual(params, ['Inactive'])

    def test_dataset_status_value_defaults_active(self):
        from dataviz.utils.status_filter import dataset_status_value
        # Field missing → historical default Active
        self.assertEqual(dataset_status_value(SimpleNamespace()), 'Active')
        # Empty list → all statuses (no restriction)
        self.assertEqual(dataset_status_value(SimpleNamespace(status_filter=[])), None)
        # Single value
        self.assertEqual(dataset_status_value(SimpleNamespace(status_filter=['Inactive'])), 'Inactive')
        # Multiple values
        self.assertEqual(
            dataset_status_value(SimpleNamespace(status_filter=['Active', 'Inactive'])),
            ['Active', 'Inactive'],
        )
        # Explicit None → default Active
        self.assertEqual(dataset_status_value(SimpleNamespace(status_filter=None)), 'Active')

    def test_prepare_interactive_filters_uses_dataset_status_when_no_explicit(self):
        from dataviz.utils.status_filter import prepare_interactive_filters
        ds = SimpleNamespace(
            base_table='current_student', joins=[], columns=['current_student.grade'],
            column_mappings=None, filters=[], status_filter=['Inactive'],
        )
        with patch('dataviz.utils.compiler.resolve_dataset_filters', return_value={}):
            resolved, status_val = prepare_interactive_filters({}, ds, {'current_student'})
        self.assertEqual(status_val, 'Inactive')
        self.assertEqual(resolved, {})

    def test_prepare_interactive_filters_explicit_status_wins(self):
        from dataviz.utils.status_filter import prepare_interactive_filters
        ds = SimpleNamespace(
            base_table='current_student', joins=[], columns=['current_student.grade'],
            column_mappings=None, filters=[], status_filter=['Inactive'],
        )
        with patch('dataviz.utils.compiler.resolve_dataset_filters', return_value={}):
            resolved, status_val = prepare_interactive_filters({'status': ['Active']}, ds, {'current_student'})
        self.assertEqual(status_val, ['Active'])
        self.assertEqual(resolved, {})



class DatasetDrillThroughTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password', is_superuser=True)
        self.dataset = Dataset.objects.create(
            name="Test Dataset",
            base_table="current_student",
            columns=["current_student.ssid", "current_student.student_name", "current_student.grade"],
            owner=self.user
        )
        self.chart = SavedChart.objects.create(
            name="Test Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_DATASET,
            source_id=self.dataset.id,
            owner=self.user
        )

    @patch('dataviz.utils.compiler.compile_dataset_to_sql')
    @patch('dataviz.utils.compiler.execute_compiled_query_aliased')
    @patch('dataviz.utils.data_loader._run_saved_query_sql')
    def test_dataset_drill_through_with_ssids(self, mock_run_sql, mock_execute_query, mock_compile):
        # 1. Mock dataset query result returning ssids
        mock_compile.return_value = ("SELECT current_student.ssid FROM ...", [])
        mock_execute_query.return_value = {
            'columns': ['ssid', 'student_name', 'grade'],
            'rows': [
                {'ssid': '12345', 'student_name': 'Alice In Dataset', 'grade': '5'},
                {'ssid': '67890', 'student_name': 'Bob In Dataset', 'grade': '6'}
            ],
            'row_count': 2
        }

        # 2. Mock _run_saved_query_sql which queries standard student list
        mock_run_sql.return_value = {
            'columns': ['student_id', 'student_name', 'grade', 'school', 'school_year'],
            'rows': [
                {'student_id': 'S1', 'student_name': 'Alice', 'grade': '5', 'school': 'School A', 'school_year': '2025-2026', 'ssid': '12345'},
                {'student_id': 'S2', 'student_name': 'Bob', 'grade': '6', 'school': 'School B', 'school_year': '2025-2026', 'ssid': '67890'}
            ],
            'error': None
        }

        # Run execute_drill_query
        result = execute_drill_query(
            self.chart,
            drill_filters={'current_student.grade': '5'},
            columns=[],
            user=self.user,
            extra_columns=[]
        )

        # Check mocks were called with correct mapped filters
        mock_compile.assert_called_once()
        args, kwargs = mock_compile.call_args
        self.assertEqual(kwargs['interactive_filters'], {'current_student.grade': '5'})

        # Check standard query runner config
        mock_run_sql.assert_called_once()
        run_config = mock_run_sql.call_args[0][0]
        self.assertIn('ssid', run_config['filters'])
        self.assertCountEqual(run_config['filters']['ssid'], ['12345', '67890'])

        # Check columns default to dataset columns + basic columns
        self.assertCountEqual(
            run_config['columns'],
            ['ssid', 'student_name', 'grade', 'student_id', 'school', 'school_year']
        )

        # Result verification
        self.assertEqual(len(result['rows']), 2)
        # Should have student_name merged from demographics
        self.assertEqual(result['rows'][0]['student_name'], 'Alice')
        # Cleaned dataset key should exist
        self.assertEqual(result['rows'][0]['ssid'], '12345')

    def test_build_drill_filter_multiple_x_axis(self):
        from dataviz.utils.drill_through import build_drill_filter
        
        self.chart.config = {
            'x_cols': ['current_student.grade', 'current_student.gender']
        }
        self.chart.save()
        
        params = {
            'x_val': '5 · Male',
            'series_val': 'GroupA'
        }
        
        filters = build_drill_filter(self.chart, params)
        self.assertEqual(filters.get('current_student.grade'), '5')
        self.assertEqual(filters.get('current_student.gender'), 'Male')

    @patch('dataviz.utils.compiler.compile_dataset_to_sql')
    @patch('dataviz.utils.compiler.execute_compiled_query_aliased')
    @patch('dataviz.utils.data_loader._run_saved_query_sql')
    def test_dataset_drill_through_with_multiple_x_axis(self, mock_run_sql, mock_execute_query, mock_compile):
        # 1. Mock dataset query result returning ssids
        mock_compile.return_value = ("SELECT current_student.ssid FROM ...", [])
        mock_execute_query.return_value = {
            'columns': ['ssid'],
            'rows': [{'ssid': '12345'}],
            'row_count': 1
        }

        # 2. Mock _run_saved_query_sql
        mock_run_sql.return_value = {
            'columns': ['student_id', 'student_name', 'grade', 'school', 'school_year'],
            'rows': [{'student_id': 'S1', 'student_name': 'Alice', 'grade': '5', 'school': 'School A', 'school_year': '2025-2026', 'ssid': '12345'}],
            'error': None
        }

        # Update chart config to have multiple x axis columns
        self.chart.config = {
            'x_cols': ['current_student.grade', 'current_student.gender']
        }
        self.chart.save()

        # Run execute_drill_query with x_val containing separator
        result = execute_drill_query(
            self.chart,
            drill_filters={'current_student.grade': '5', 'current_student.gender': 'Male'},
            columns=[],
            user=self.user,
            extra_columns=[]
        )

        # Verify compiler received both filters
        mock_compile.assert_called_once()
        args, kwargs = mock_compile.call_args
        self.assertEqual(kwargs['interactive_filters']['current_student.grade'], '5')
        self.assertEqual(kwargs['interactive_filters']['current_student.gender'], 'Male')

    def test_build_drill_filter_comma_separated_heuristic(self):
        from dataviz.utils.drill_through import build_drill_filter
        
        # Test case where x_col is a comma-separated string
        self.chart.drill_through_filter_map = {
            'x_col': 'tests.test_window,current_student.grade'
        }
        self.chart.config = {}
        self.chart.save()
        
        params = {
            'x_val': 'KN',
            'series_val': 'BOY'
        }
        
        filters = build_drill_filter(self.chart, params)
        self.assertEqual(filters.get('tests.test_window'), 'BOY')
        self.assertEqual(filters.get('current_student.grade'), 'KN')

    def test_build_drill_filter_comma_separated_safety_net(self):
        from dataviz.utils.drill_through import build_drill_filter

        # Test safety net split when x_col list is length 1 with a comma, or any comma key enters filters
        self.chart.drill_through_filter_map = {}
        self.chart.config = {
            'x_col': 'Tests.Test Window,Current Student.Grade'
        }
        self.chart.save()

        # Simulate a click with just 'KN' value
        params = {
            'x_val': 'KN',
            'series_val': ''
        }

        filters = build_drill_filter(self.chart, params)
        # Should have split the key and correctly routed 'KN' to grade column
        self.assertEqual(filters.get('Current Student.Grade'), 'KN')
        self.assertNotIn('Tests.Test Window,Current Student.Grade', filters)
        self.assertIsNone(filters.get('Tests.Test Window'))

    def test_build_drill_filter_composite_non_breaking_spaces(self):
        from dataviz.utils.drill_through import build_drill_filter
        
        self.chart.config = {
            'x_cols': ['tests.test_window', 'current_student.grade']
        }
        self.chart.save()
        
        params = {
            'x_val': 'Fall\xa0·\xa0KN',
            'series_val': 'Mid or Above Grade Level'
        }
        
        filters = build_drill_filter(self.chart, params)
        self.assertEqual(filters.get('tests.test_window'), 'Fall')
        self.assertEqual(filters.get('current_student.grade'), 'KN')

    def test_build_drill_filter_grouped_stacked_series(self):
        from dataviz.utils.drill_through import build_drill_filter
        
        self.chart.config = {
            'x_col': 'current_student.grade',
            'group_col': 'tests.test_window',
            'stack_col': 'results.pl'
        }
        self.chart.save()
        
        params = {
            'x_val': 'KN',
            'series_val': 'Fall — Mid or Above Grade Level'
        }
        
        filters = build_drill_filter(self.chart, params)
        self.assertEqual(filters.get('current_student.grade'), 'KN')
        self.assertEqual(filters.get('tests.test_window'), 'Fall')
        self.assertEqual(filters.get('results.pl'), 'Mid or Above Grade Level')

    def test_relative_year_mapping_update(self):
        from landing.models import ProjectConfig
        from dataviz.models import RelativeYear
        
        # Get/create ProjectConfig and save a new default_school_year
        cfg = ProjectConfig.get()
        cfg.default_school_year = '2026-2027'
        cfg.save()
        
        # Check that RelativeYear mapping was updated
        this_year_mapping = RelativeYear.objects.get(relative_alias='__sy_cur__')
        self.assertEqual(this_year_mapping.school_year, '2026-2027')
        self.assertTrue(this_year_mapping.is_current)
        
        prev_year_mapping = RelativeYear.objects.get(relative_alias='__sy_prev1__')
        self.assertEqual(prev_year_mapping.school_year, '2025-2026')
        
        prev2_year_mapping = RelativeYear.objects.get(relative_alias='__sy_prev2__')
        self.assertEqual(prev2_year_mapping.school_year, '2024-2025')

    def test_resolve_exploration_column_mappings(self):
        from dataviz.utils.resolution import resolve_exploration_column
        
        # Test case 1: Fallback heuristic
        self.assertEqual(resolve_exploration_column(self.dataset, 'current_student.grade'), 'grade')
        self.assertIsNone(resolve_exploration_column(self.dataset, 'tests.test_window'))
        
        # Test case 2: Explicit mapping overrides
        self.dataset.column_mappings = {
            'tests.test_window': 'test_window',
            'custom_table.gr': 'grade'
        }
        self.dataset.save()
        
        self.assertEqual(resolve_exploration_column(self.dataset, 'tests.test_window'), 'test_window')
        self.assertEqual(resolve_exploration_column(self.dataset, 'custom_table.gr'), 'grade')

    @patch('dataviz.utils.compiler.compile_dataset_to_sql')
    @patch('dataviz.utils.compiler.execute_compiled_query_aliased')
    @patch('dataviz.utils.data_loader._run_saved_query_sql')
    def test_dataset_drill_through_with_jsonb_filter_key(self, mock_run_sql, mock_execute_query, mock_compile):
        mock_compile.return_value = ("SELECT current_student.ssid FROM ...", [])
        mock_execute_query.return_value = {
            'columns': ['ssid'],
            'rows': [{'ssid': '12345'}],
            'row_count': 1,
        }
        mock_run_sql.return_value = {
            'columns': ['student_id', 'student_name', 'grade', 'school', 'school_year'],
            'rows': [{'student_id': 'S1', 'student_name': 'Alice', 'grade': '5', 'school': 'School A', 'school_year': '2025-2026', 'ssid': '12345'}],
            'error': None,
        }

        result = execute_drill_query(
            self.chart,
            drill_filters={"current_student.add_data->'AVID'": 'Yes'},
            columns=[],
            user=self.user,
            extra_columns=[],
        )

        mock_compile.assert_called_once()
        self.assertEqual(mock_compile.call_args[1]['interactive_filters'], {"current_student.add_data->'AVID'": 'Yes'})
        self.assertEqual(result['rows'][0]['ssid'], '12345')

    @patch('dataviz.utils.compiler.compile_dataset_to_sql')
    @patch('dataviz.utils.compiler.execute_compiled_query_aliased')
    @patch('dataviz.utils.data_loader._run_saved_query_sql')
    def test_dataset_drill_through_with_custom_column_mapping(self, mock_run_sql, mock_execute_query, mock_compile):
        mock_compile.return_value = ("SELECT current_student.ssid FROM ...", [])
        mock_execute_query.return_value = {
            'columns': ['ssid'],
            'rows': [{'ssid': '12345'}],
            'row_count': 1
        }
        mock_run_sql.return_value = {
            'columns': ['student_id', 'student_name', 'grade', 'school', 'school_year'],
            'rows': [{'student_id': 'S1', 'student_name': 'Alice', 'grade': '5', 'school': 'School A', 'school_year': '2025-2026', 'ssid': '12345'}],
            'error': None
        }

        # Set up custom mapping on the dataset
        self.dataset.joins = [{'table': 'tests', 'join_type': 'left', 'conditions': []}]
        self.dataset.column_mappings = {
            'tests.test_window': 'test_window'
        }
        self.dataset.save()

        # Run execute_drill_query with a mapped filter tests.test_window
        result = execute_drill_query(
            self.chart,
            drill_filters={'tests.test_window': 'Fall'},
            columns=[],
            user=self.user,
            extra_columns=[]
        )

        # Verify compiler received the dataset filter
        mock_compile.assert_called_once()
        self.assertEqual(mock_compile.call_args[1]['interactive_filters'], {'tests.test_window': 'Fall'})

        # Verify that Data Exploration runner received the mapped filter key 'test_window' rather than 'tests.test_window'
        mock_run_sql.assert_called_once()
        run_config = mock_run_sql.call_args[0][0]
        self.assertEqual(run_config['filters'].get('test_window'), 'Fall')

    def test_allowed_dataset_tables_whitelisting(self):
        from dataviz.utils.compiler import get_allowed_dataset_tables, compile_dataset_to_sql, SQLCompilationError
        from landing.models import ProjectConfig
        
        # Default behavior: all tables allowed
        allowed = get_allowed_dataset_tables()
        self.assertIn('current_student', allowed)
        self.assertIn('results', allowed)
        
        # Restrict via ProjectConfig
        cfg = ProjectConfig.get()
        cfg.enabled_dataset_tables = ['current_student']
        cfg.save()
        
        allowed = get_allowed_dataset_tables()
        self.assertIn('current_student', allowed)
        self.assertNotIn('results', allowed)
        
        # Test compile failure when joining non-whitelisted table
        self.dataset.joins = [{'table': 'results', 'join_type': 'left', 'conditions': []}]
        self.dataset.save()
        
        with self.assertRaises(SQLCompilationError) as ctx:
            compile_dataset_to_sql(self.dataset, user=self.user)
        self.assertIn("Joined table 'results' is not whitelisted", str(ctx.exception))
        
        # Clean up config for other tests
        cfg.enabled_dataset_tables = None
        cfg.save()

    @patch('dataexploration.column_utils.get_mark_columns', return_value={})
    @patch('dataexploration.column_utils.get_test_columns', return_value={})
    def test_dataset_builder_view_dynamic_tables_and_cache(self, mock_get_tests, mock_get_marks):
        from django.core.cache import cache
        from landing.models import ProjectConfig
        from django.urls import reverse

        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

        # Clear any schema cache first
        cache.delete('dataviz:schema_json')

        # Request builder view, check context/response
        url = reverse('dataviz:dataset_new')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Ensure schema_json cache was populated
        cached_schema = cache.get('dataviz:schema_json')
        self.assertIsNotNone(cached_schema)
        self.assertIn('current_student', cached_schema)

        # Update ProjectConfig, which should bust the cache
        cfg = ProjectConfig.get()
        cfg.enabled_dataset_tables = ['current_student']
        cfg.save()

        # Cache should now be busted (None)
        self.assertIsNone(cache.get('dataviz:schema_json'))

        # Request again to rebuild cache with restricted table
        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, 200)
        
        cached_schema2 = cache.get('dataviz:schema_json')
        self.assertIsNotNone(cached_schema2)
        self.assertIn('current_student', cached_schema2)
        # 'results' should NOT be in cached_schema2 because it's not whitelisted
        self.assertNotIn('results', cached_schema2)

        # Clean up
        cfg.enabled_dataset_tables = None
        cfg.save()
        cache.delete('dataviz:schema_json')

    def test_dataset_json_column_compilation(self):
        from django.core.cache import cache
        from dataviz.utils.compiler import get_columns_for_table, compile_dataset_to_sql
        from studata.models import Result

        # Create a result record with JSON key-value pairs in other_info
        Result.objects.create(
            test_id="T1",
            test_group_key="TG1",
            ssid="12345",
            local_student_id="L1",
            school_year="2025-2026",
            school_code="S1",
            other_info={"lexile": "450", "quantile": "500"}
        )

        # Clear cache to force dynamic JSON discovery
        cache.delete('dataviz:schema_json')

        # Get columns of results table
        cols = get_columns_for_table('results')
        self.assertIn("other_info->'lexile'", cols)
        self.assertIn("other_info->'quantile'", cols)

        # Build and compile dataset using json column key
        self.dataset.columns = ["results.other_info->'lexile'"]
        self.dataset.joins = [{'table': 'results', 'join_type': 'left', 'conditions': [{'on_left': 'current_student.ssid', 'on_right': 'results.ssid'}]}]
        self.dataset.filters = [{'column': "results.other_info->'lexile'", 'operator': 'exact', 'value': '450'}]
        self.dataset.save()

        sql, params = compile_dataset_to_sql(self.dataset, user=self.user)
        
        # Verify compiled SQL structure
        self.assertIn('"results"."other_info"->>\'lexile\' AS "results.other_info->\'lexile\'"', sql)
        self.assertIn('"results"."other_info"->>\'lexile\' = %s', sql)
        self.assertIn('450', params)

        # Clean up
        Result.objects.filter(test_id="T1").delete()
        cache.delete('dataviz:schema_json')

    def test_dataset_student_add_data_json_column_compilation(self):
        from django.core.cache import cache
        from dataviz.utils.compiler import get_columns_for_table, compile_dataset_to_sql
        from studata.models import CurrentStudent

        # Create a student record using Django ORM
        student = CurrentStudent.objects.create(
            stu_annual_key="annual_key_1",
            ssid="S123",
            student_id="SID123",
            student_name="Charlie",
            school_year="2025-2026",
            grade="5",
            add_data={"AVID": "Yes", "AVID_type": "elective"}
        )

        # Clear cache to force dynamic JSON discovery
        cache.delete('dataviz:schema_json')

        # Get columns of current_student table
        cols = get_columns_for_table('current_student')
        self.assertIn("add_data->'AVID'", cols)
        self.assertIn("add_data->'AVID_type'", cols)

        # Build and compile dataset using json column key
        self.dataset.columns = ["current_student.add_data->'AVID'"]
        self.dataset.filters = [{'column': "current_student.add_data->'AVID'", 'operator': 'exact', 'value': 'Yes'}]
        self.dataset.save()

        sql, params = compile_dataset_to_sql(self.dataset, user=self.user)
        
        # Verify compiled SQL structure
        self.assertIn('"current_student"."add_data"->>\'AVID\' AS "current_student.add_data->\'AVID\'"', sql)
        self.assertIn('"current_student"."add_data"->>\'AVID\' = %s', sql)
        self.assertIn('Yes', params)

        # Clean up
        CurrentStudent.objects.filter(stu_annual_key="annual_key_1").delete()
        cache.delete('dataviz:schema_json')

    def test_chart_force_show_all_axis_labels(self):
        from dataviz.utils.chart_builder import build_option
        
        cfg = {
            'chart_type': 'bar',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'agg': 'count',
            'force_show_all_labels': True
        }
        col_defs = {
            'current_student.grade': 'Grade',
            'current_student.ssid': 'SSID'
        }
        rows = [
            {'current_student.grade': 'K', 'current_student.ssid': 1},
            {'current_student.grade': '1', 'current_student.ssid': 2}
        ]
        
        opt = build_option(rows, col_defs, cfg)
        
        # In vertical bar, xAxis is the category axis
        x_axis = opt.get('xAxis', {})
        if isinstance(x_axis, list):
            x_axis = x_axis[0]
        self.assertEqual(x_axis.get('axisLabel', {}).get('interval'), 0)

    def test_chart_label_font_size(self):
        from dataviz.utils.chart_builder import build_option

        cfg = {
            'chart_type': 'bar',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'agg': 'count',
            'data_labels': True,
            'label_font_size': 14
        }
        col_defs = {
            'current_student.grade': 'Grade',
            'current_student.ssid': 'SSID'
        }
        rows = [
            {'current_student.grade': 'K', 'current_student.ssid': 1},
            {'current_student.grade': '1', 'current_student.ssid': 2}
        ]

        opt = build_option(rows, col_defs, cfg)
        series = opt.get('series', [])
        self.assertTrue(len(series) > 0)
        for s in series:
            self.assertEqual(s.get('label', {}).get('fontSize'), 14)

    @patch('django.core.cache.cache.get', return_value=None)
    @patch('django.core.cache.cache.set')
    @patch('dataviz.views.dataset_views.is_project_admin')
    def test_dataset_filter_options_api_excludes_self_filter(self, mock_is_admin, mock_cache_set, mock_cache_get):
        from dataviz.utils.compiler import compile_dataset_to_sql as original_compile
        mock_is_admin.return_value = True
        
        from django.test import RequestFactory
        from dataviz.views.dataset_views import dataset_filter_options_api
        import json

        rf = RequestFactory()
        request = rf.get('/dataviz/api/datasets/1/filter-options/', {
            'current_filters': json.dumps({'current_student.grade': ['K'], 'current_student.school': ['Lincoln']})
        })
        request.user = self.user

        from unittest.mock import patch as local_patch
        with local_patch('dataviz.utils.compiler.compile_dataset_to_sql') as mock_compile:
            mock_compile.side_effect = original_compile
            
            response = dataset_filter_options_api(request, self.dataset.id)
            self.assertEqual(response.status_code, 200)
            
            # Check that compile_dataset_to_sql was called with the filters
            # For 'current_student.grade', the filter on 'current_student.grade' must be excluded.
            # So interactive_filters should only contain 'current_student.school'
            grade_call_args = None
            for call in mock_compile.call_args_list:
                kwargs = call[1]
                if 'current_student.grade' not in kwargs.get('interactive_filters', {}):
                    grade_call_args = kwargs.get('interactive_filters')

            self.assertIsNotNone(grade_call_args)
            self.assertEqual(grade_call_args, {'current_student.school': ['Lincoln']})

    def test_new_aggregations_and_empty_mappings_resolution(self):
        import pandas as pd
        from dataviz.utils.chart_builder import _agg
        
        # Test 1: pandas-based count_unique and count_occurrence aggregation
        df = pd.DataFrame([
            {'x': 'A', 'y': 10},
            {'x': 'A', 'y': 10},
            {'x': 'A', 'y': 20},
            {'x': 'B', 'y': 30},
        ])
        
        res_unique = _agg(df, 'x', 'y', 'count_unique')
        val_map_u = dict(zip(res_unique['x'], res_unique['_value']))
        self.assertEqual(val_map_u['A'], 2)
        self.assertEqual(val_map_u['B'], 1)
        
        res_occ = _agg(df, 'x', 'y', 'count_occurrence')
        val_map_o = dict(zip(res_occ['x'], res_occ['_value']))
        self.assertEqual(val_map_o['A'], 3)
        self.assertEqual(val_map_o['B'], 1)

        # Test 2: resolve_dataset_filters with empty column_mappings (fallback validation)
        from dataviz.utils.compiler import resolve_dataset_filters
        
        self.dataset.column_mappings = {}
        self.dataset.save()
        
        active_tables = {self.dataset.base_table}
        filters = {'grade': ['12']}
        resolved = resolve_dataset_filters(filters, self.dataset, active_tables)
        self.assertIn('current_student.grade', resolved)
        self.assertEqual(resolved['current_student.grade'], ['12'])

    def test_resolve_dataset_filters_test_name_not_treated_as_dynamic_column(self):
        from dataviz.utils.compiler import resolve_dataset_filters, build_dataset_col_index

        self.dataset.base_table = 'results'
        self.dataset.columns = [
            'tests.test_name',
            'tests.subject',
            'results.pl',
        ]
        self.dataset.joins = [
            {'table': 'tests', 'join_type': 'inner', 'conditions': []},
        ]
        self.dataset.column_mappings = {
            'tests.test_name': 'test_name',
            'tests.subject': 'subject',
            'results.pl': 'pl',
        }
        active_tables = {self.dataset.base_table, 'tests'}
        resolved = resolve_dataset_filters({'test_name': ['CAASPP']}, self.dataset, active_tables)
        self.assertEqual(resolved, {'tests.test_name': ['CAASPP']})
        self.assertIn('test_name', build_dataset_col_index(self.dataset))

    def test_resolve_dataset_filters_prefixed_collision_alias(self):
        from dataviz.utils.compiler import resolve_dataset_filters

        self.dataset.base_table = 'results'
        self.dataset.columns = [
            'current_student.school_year',
            'relative_year.relative_label',
        ]
        self.dataset.joins = [
            {'table': 'current_student', 'join_type': 'inner', 'conditions': []},
            {'table': 'relative_year', 'join_type': 'inner', 'conditions': []},
        ]
        self.dataset.column_mappings = {
            'current_student.school_year': 'current_student_school_year',
            'relative_year.relative_label': 'relative_label',
        }
        active_tables = {self.dataset.base_table, 'current_student', 'relative_year'}
        resolved = resolve_dataset_filters(
            {'current_student_school_year': ['2024-2025', '2025-2026']},
            self.dataset,
            active_tables,
        )
        self.assertEqual(resolved, {'current_student.school_year': ['2024-2025', '2025-2026']})

    def test_resolve_dataset_filters_still_skips_dynamic_test_columns(self):
        from dataviz.utils.compiler import resolve_dataset_filters

        active_tables = {self.dataset.base_table}
        resolved = resolve_dataset_filters(
            {'test_2024_2025_caaspp_ela_pl': ['3', '4']},
            self.dataset,
            active_tables,
        )
        self.assertEqual(resolved, {})

    def test_line_chart_options(self):
        from dataviz.utils.chart_builder import build_option
        col_defs = {
            'current_student.grade': 'Grade',
            'current_student.ssid': 'SSID',
            'current_student.school': 'School'
        }
        rows = [
            {'current_student.grade': 'K', 'current_student.ssid': 1, 'current_student.school': 'A'},
            {'current_student.grade': '1', 'current_student.ssid': 2, 'current_student.school': 'B'}
        ]

        # Standard layout without fill or smooth
        cfg = {
            'chart_type': 'line',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'agg': 'count',
            'line_layout': 'standard',
            'line_fill': False,
            'smooth': False
        }
        opt = build_option(rows, col_defs, cfg)
        series = opt.get('series', [])
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]['type'], 'line')
        self.assertIsNone(series[0].get('areaStyle'))
        self.assertEqual(series[0].get('smooth'), False)
        self.assertIsNone(series[0].get('stack'))

        # Stacked layout with fill and smooth
        cfg_stacked = {
            'chart_type': 'line',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'series_col': 'current_student.school',
            'stack_col': 'current_student.school',
            'agg': 'count',
            'line_layout': 'stacked',
            'line_fill': True,
            'smooth': True
        }
        opt_stacked = build_option(rows, col_defs, cfg_stacked)
        series_stacked = opt_stacked.get('series', [])
        self.assertTrue(len(series_stacked) > 0)
        for s in series_stacked:
            self.assertEqual(s['type'], 'line')
            self.assertEqual(s['stack'], 'total')
            self.assertEqual(s['smooth'], True)
            self.assertIsNotNone(s.get('areaStyle'))

    def test_legacy_line_charts_compat(self):
        from dataviz.utils.chart_builder import build_option
        col_defs = {
            'current_student.grade': 'Grade',
            'current_student.ssid': 'SSID'
        }
        rows = [
            {'current_student.grade': 'K', 'current_student.ssid': 1},
            {'current_student.grade': '1', 'current_student.ssid': 2}
        ]

        # Legacy area
        cfg = {
            'chart_type': 'area',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'agg': 'count'
        }
        opt = build_option(rows, col_defs, cfg)
        s = opt.get('series', [])[0]
        self.assertIsNotNone(s.get('areaStyle'))
        self.assertEqual(s.get('smooth'), False)

        # Legacy areaspline
        cfg_spline = {
            'chart_type': 'areaspline',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'agg': 'count'
        }
        opt_spline = build_option(rows, col_defs, cfg_spline)
        s_spline = opt_spline.get('series', [])[0]
        self.assertIsNotNone(s_spline.get('areaStyle'))
        self.assertEqual(s_spline.get('smooth'), True)

    @patch('dataviz.views.api.chart_api.is_project_admin', return_value=True)
    @patch('dataviz.utils.data_loader.run_saved_query')
    def test_chart_preview_api_returns_columns(self, mock_run_query, mock_is_admin):
        from django.test import RequestFactory
        from dataviz.views.api.chart_api import chart_preview_api
        import json

        rf = RequestFactory()
        request = rf.post('/dataviz/api/charts/preview/', json.dumps({
            'source_type': 'query',
            'saved_query_id': '1',
            'chart_type': 'bar',
            'x_col': 'grade',
            'y_col': 'student_id',
            'agg_func': 'count'
        }), content_type='application/json')
        request.user = self.user

        # Mock the query runner returning some rows with columns
        mock_run_query.return_value = {
            'columns': ['grade', 'student_id'],
            'rows': [
                {'grade': 'K', 'student_id': 1},
                {'grade': '1', 'student_id': 2}
            ]
        }

        response = chart_preview_api(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-query-columns', response.content)
        self.assertIn(b'data-query-columns="[&quot;grade&quot;, &quot;student_id&quot;]"', response.content)


class DrillThroughResultAlignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password', is_superuser=True)
        self.chart = SavedChart.objects.create(
            name="Test Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_SQL,
            source_sql="SELECT ssid, student_name, grade FROM current_student",
            drill_through_columns=['grade', 'student_name'],
            owner=self.user
        )

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_execute_drill_query_column_ordering_and_mapping(self, mock_run_sql):
        mock_run_sql.return_value = {
            'columns': ['ssid', 'student_name', 'grade'],
            'rows': [
                {'ssid': '123', 'student_name': 'Alice', 'grade': '5'},
                {'ssid': '456', 'student_name': 'Bob', 'grade': '6'}
            ]
        }

        # 1. Custom columns: 'grade', 'student_name' (reordered, ssid excluded from visible but kept in row for links)
        result = execute_drill_query(
            self.chart,
            drill_filters={},
            columns=['grade', 'student_name'],
            user=self.user
        )

        self.assertEqual(result['columns'], ['grade', 'student_name'])
        # Check order in rows and preservation of ssid
        self.assertEqual(list(result['rows'][0].keys()), ['grade', 'student_name', 'ssid'])
        self.assertEqual(result['rows'][0]['grade'], '5')
        self.assertEqual(result['rows'][0]['student_name'], 'Alice')
        self.assertEqual(result['rows'][0]['ssid'], '123')

        # 2. Prefix mapping: 'current_student.grade', 'current_student.student_name'
        result_prefixed = execute_drill_query(
            self.chart,
            drill_filters={},
            columns=['current_student.grade', 'current_student.student_name'],
            user=self.user
        )
        self.assertEqual(result_prefixed['columns'], ['current_student.grade', 'current_student.student_name'])
        # The key name in the row should match the input column name exactly
        self.assertEqual(result_prefixed['rows'][0]['current_student.grade'], '5')
        self.assertEqual(result_prefixed['rows'][0]['current_student.student_name'], 'Alice')
        # ssid is still kept
        self.assertEqual(result_prefixed['rows'][0]['ssid'], '123')

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_drill_rows_api_defaults_to_saved_columns(self, mock_run_sql):
        mock_run_sql.return_value = {
            'columns': ['ssid', 'student_name', 'grade'],
            'rows': [
                {'ssid': '123', 'student_name': 'Alice', 'grade': '5'}
            ]
        }
        self.client.force_login(self.user)

        # Case 1: no cols param -> defaults to chart.drill_through_columns (['grade', 'student_name'])
        response = self.client.get(f'/dataviz/api/drill/{self.chart.id}/rows/', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['columns'], ['grade', 'student_name'])

        # Case 2: cols param is explicitly provided -> overrides default
        response = self.client.get(f'/dataviz/api/drill/{self.chart.id}/rows/?cols=student_name', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['columns'], ['student_name'])

        # Case 3: cols param is explicitly empty -> defaults to saved columns
        response = self.client.get(f'/dataviz/api/drill/{self.chart.id}/rows/?cols=', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['columns'], ['grade', 'student_name'])

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_drill_export_csv_api(self, mock_run_sql):
        mock_run_sql.return_value = {
            'columns': ['ssid', 'student_name', 'grade'],
            'rows': [
                {'ssid': '123', 'student_name': 'Alice', 'grade': '5'}
            ]
        }
        self.client.force_login(self.user)

        response = self.client.get(f'/dataviz/api/drill/{self.chart.id}/export-csv/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        
        # Consume the streaming content
        content = b"".join(response.streaming_content).decode('utf-8')
        
        # Assert structure of CSV output
        lines = content.strip().split('\n')
        self.assertEqual(len(lines), 2)
        # Headers should correspond to the default saved columns (grade and student_name, pretty printed)
        self.assertEqual(lines[0], 'Grade,Student Name')
        self.assertEqual(lines[1], '"5","Alice"')


class GetAllAvailableColumnsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser2', password='password')
        self.dataset = Dataset.objects.create(
            name="Test Dataset",
            base_table="current_student",
            columns=["current_student.ssid", "current_student.student_name", "current_student.grade"],
            column_mappings={"results.ss": "scale_score"},
            owner=self.user
        )

    def test_get_all_available_columns_dataset(self):
        from dataviz.utils.drill_through import get_all_available_columns
        chart = SavedChart.objects.create(
            name="Dataset Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_DATASET,
            source_id=self.dataset.id,
            owner=self.user
        )
        cols = get_all_available_columns(chart, self.user)
        # Should include columns from dataset (ssid, student_name, grade), alias mapping (scale_score), and standard student columns
        self.assertIn('ssid', cols)
        self.assertIn('student_name', cols)
        self.assertIn('grade', cols)
        self.assertIn('scale_score', cols)
        self.assertIn('student_id', cols)

    @patch('dataviz.utils.data_loader.run_raw_sql')
    def test_get_all_available_columns_sql(self, mock_run_sql):
        from dataviz.utils.drill_through import get_all_available_columns
        mock_run_sql.return_value = {
            'columns': ['custom_id', 'custom_name'],
            'rows': []
        }
        chart = SavedChart.objects.create(
            name="SQL Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_SQL,
            source_sql="SELECT custom_id, custom_name FROM custom_table",
            owner=self.user
        )
        cols = get_all_available_columns(chart, self.user)
        self.assertEqual(cols, ['custom_id', 'custom_name'])


class RelativeYearFilterOptionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='password', is_staff=True, is_superuser=True)
        from django.core.cache import cache
        cache.clear()
        from landing.models import ProjectConfig
        cfg = ProjectConfig.get()
        cfg.default_school_year = '2025-2026'
        cfg.save()

    @patch('dataviz.views.api.query_api.is_project_admin', return_value=True)
    def test_column_filter_options_api_resolves_relative_keys(self, mock_is_admin):
        from django.test import RequestFactory
        from dataviz.views.api.query_api import column_filter_options_api
        import json
        
        rf = RequestFactory()
        body = {
            'columns': ['grade', 'test___sy_this___iready_reading_ss_PL'],
            'filters': {
                'school_year': '__sy_this__',
            }
        }
        
        from unittest.mock import patch as local_patch
        with local_patch('dataviz.utils.data_loader._build_test_columns_from_db') as mock_build:
            mock_build.return_value = ([], {})
            
            request = rf.post('/dataviz/api/columns/filter-options/', json.dumps(body), content_type='application/json')
            request.user = self.user
            response = column_filter_options_api(request)
            
            self.assertTrue(mock_build.called)
            called_args = mock_build.call_args[0][1]
            # Verify relative year key resolved to concrete year key
            self.assertTrue(any(c.startswith('test_') and not c.startswith('test___sy_') for c in called_args))

    @patch('dataviz.views.api.query_api.is_project_admin', return_value=True)
    def test_query_filter_options_api_resolves_relative_keys(self, mock_is_admin):
        from django.test import RequestFactory
        from dataviz.views.api.query_api import query_filter_options_api
        from dataexploration.models import SavedQuery
        import json
        
        sq = SavedQuery.objects.create(
            name="Test Saved Query",
            config={
                'columns': ['grade', 'test___sy_this___iready_reading_ss_PL'],
                'filters': {'school_year': '__sy_this__'}
            },
            owner=self.user
        )
        
        rf = RequestFactory()
        
        from unittest.mock import patch as local_patch
        with local_patch('dataviz.utils.data_loader._build_test_columns_from_db') as mock_build:
            mock_build.return_value = ([], {})
            
            request = rf.get(f'/dataviz/api/query/{sq.id}/filter-options/')
            request.user = self.user
            response = query_filter_options_api(request, sq.id)
            
            self.assertTrue(mock_build.called)
            called_args = mock_build.call_args[0][1]
            # Verify relative year key resolved to concrete year key
            self.assertTrue(any(c.startswith('test_') and not c.startswith('test___sy_') for c in called_args))


class ProgressGrowthMatrixTests(SimpleTestCase):
    def test_matrix_consolidates_split_pl_across_enrollment_rows(self):
        from dataviz.utils.chart_builder import build_option

        pl_scale = ['1', '2', '3', '4']
        rows = [
            {'ssid': '1', 'before_pl': '2', 'after_pl': ''},
            {'ssid': '1', 'before_pl': '', 'after_pl': '3'},
            {'ssid': '2', 'before_pl': '1', 'after_pl': '3'},
        ]
        cfg = {
            'chart_type': 'progress_growth',
            'pg_before_col': 'before_pl',
            'pg_after_col': 'after_pl',
            'pl_scale': pl_scale,
            'pg_display': 'matrix',
        }
        opt = build_option(rows, {}, cfg)
        counts = {(d['value'][1], d['value'][0]): d['value'][2] for d in opt['series'][0]['data']}
        # y=before idx, x=after idx in ordered ['1','2','3','4']
        self.assertEqual(counts[(1, 2)], 1)   # 2→3: student 1 (PL split across rows)
        self.assertEqual(counts[(0, 2)], 1)   # 1→3: student 2

    def test_matrix_matches_duplicate_result_pl_like_exploration(self):
        from dataviz.utils.chart_builder import (
            build_option, filter_progress_growth_matrix_rows, pg_all_key,
        )

        pl_scale = ['1', '2', '3', '4']
        # Display column shows last-fetched PL=2, but a duplicate result had PL=1.
        rows = [
            {
                'ssid': '1',
                'before_pl': '2',
                'after_pl': '2',
                pg_all_key('before_pl'): ['1', '2'],
                pg_all_key('after_pl'): ['2'],
            },
            {'ssid': '2', 'before_pl': '1', 'after_pl': '2'},
        ]
        matched, _, _ = filter_progress_growth_matrix_rows(
            rows, 'before_pl', 'after_pl', '1', '2',
        )
        self.assertEqual(len(matched), 2)

        cfg = {
            'chart_type': 'progress_growth',
            'pg_before_col': 'before_pl',
            'pg_after_col': 'after_pl',
            'pl_scale': pl_scale,
            'pg_display': 'matrix',
        }
        opt = build_option(rows, {}, cfg)
        counts = {(d['value'][1], d['value'][0]): d['value'][2] for d in opt['series'][0]['data']}
        self.assertEqual(counts[(0, 1)], 2)   # 1→2

    def test_matrix_ignores_blank_x_col_filter(self):
        from dataviz.utils.chart_builder import build_option

        pl_scale = ['1', '2', '3', '4']
        rows = [
            {'ssid': '1', 'before_pl': '1', 'after_pl': '3', 'grade': ''},
            {'ssid': '2', 'before_pl': '1', 'after_pl': '3', 'grade': '5'},
        ]
        cfg = {
            'chart_type': 'progress_growth',
            'pg_before_col': 'before_pl',
            'pg_after_col': 'after_pl',
            'pl_scale': pl_scale,
            'pg_display': 'matrix',
            'x_col': 'grade',
        }
        opt = build_option(rows, {}, cfg)
        counts = {(d['value'][1], d['value'][0]): d['value'][2] for d in opt['series'][0]['data']}
        self.assertEqual(counts[(0, 2)], 2)

    def test_filter_progress_growth_matrix_rows_per_cell(self):
        from dataviz.utils.chart_builder import filter_progress_growth_matrix_rows

        rows = [
            {'ssid': '1', 'before_pl': '4', 'after_pl': '1'},
            {'ssid': '1', 'before_pl': '1', 'after_pl': '3'},
            {'ssid': '2', 'before_pl': '1', 'after_pl': '3'},
        ]

        matched, _, _ = filter_progress_growth_matrix_rows(
            rows, 'before_pl', 'after_pl', '1', '3',
        )
        self.assertEqual(len(matched), 2)
        self.assertEqual(sorted(r['ssid'] for r in matched), ['1', '2'])

    def test_matrix_dedupes_by_any_student_id_column(self):
        from dataviz.utils.chart_builder import filter_progress_growth_matrix_rows

        rows = [
            {'ssid': '', 'student_id': 'A', 'before_pl': '1', 'after_pl': '3'},
            {'ssid': '', 'student_id': 'B', 'before_pl': '1', 'after_pl': '3'},
            {'ssid': 'C', 'student_id': 'C', 'before_pl': '1', 'after_pl': '3'},
        ]
        matched, _, _ = filter_progress_growth_matrix_rows(
            rows, 'before_pl', 'after_pl', '1', '3',
        )
        self.assertEqual(len(matched), 3)

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_matrix_drill_count_matches_chart_cell(self, mock_run_sql):
        from dataviz.utils.drill_through import execute_drill_query

        pl_scale = ['1', '2', '3', '4']
        rows = [
            {'ssid': '1', 'before_pl': '4', 'after_pl': '1'},
            {'ssid': '1', 'before_pl': '1', 'after_pl': '3'},
            {'ssid': '2', 'before_pl': '1', 'after_pl': '3'},
            {'ssid': '3', 'before_pl': '1', 'after_pl': '3'},
        ]
        mock_run_sql.return_value = {'columns': list(rows[0].keys()), 'rows': rows}

        chart = SimpleNamespace(
            pk=58,
            updated_at=SimpleNamespace(timestamp=lambda: 0),
            source_type='sql',
            source_sql='SELECT 1',
            source_id=None,
            source_filters={},
            chart_type='progress_growth',
            config={
                'pg_before_col': 'before_pl',
                'pg_after_col': 'after_pl',
                'pl_scale': pl_scale,
                'pg_display': 'matrix',
            },
            drill_through_columns=['ssid'],
            column_defs={},
        )

        result = execute_drill_query(
            chart,
            drill_filters={'pg_before_val': '1', 'pg_after_val': '3'},
            columns=['ssid'],
            user=SimpleNamespace(is_superuser=True, roles=SimpleNamespace(all=lambda: [])),
        )

        self.assertEqual(result['total_rows'], 3)

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_matrix_drill_consolidates_split_pl_rows(self, mock_run_sql):
        from dataviz.utils.drill_through import execute_drill_query

        pl_scale = ['1', '2', '3', '4']
        rows = [
            {'ssid': '1', 'before_pl': '2', 'after_pl': ''},
            {'ssid': '1', 'before_pl': '', 'after_pl': '3'},
        ]
        mock_run_sql.return_value = {'columns': list(rows[0].keys()), 'rows': rows}

        chart = SimpleNamespace(
            pk=58,
            updated_at=SimpleNamespace(timestamp=lambda: 0),
            source_type='sql',
            source_sql='SELECT 1',
            source_id=None,
            source_filters={},
            chart_type='progress_growth',
            config={
                'pg_before_col': 'before_pl',
                'pg_after_col': 'after_pl',
                'pl_scale': pl_scale,
                'pg_display': 'matrix',
            },
            drill_through_columns=['ssid'],
            column_defs={},
        )

        result = execute_drill_query(
            chart,
            drill_filters={'pg_before_val': '2', 'pg_after_val': '3'},
            columns=['ssid'],
            user=SimpleNamespace(is_superuser=True, roles=SimpleNamespace(all=lambda: [])),
        )

        self.assertEqual(result['total_rows'], 1)


class DateSeriesOptionTests(SimpleTestCase):
    def test_basic_count_line_per_date(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01'},
            {'ssid': '2', 'attendance_date': '2025-09-01'},
            {'ssid': '3', 'attendance_date': '2025-09-02'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'l1', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        series = opt['series'][0]
        self.assertEqual(series['name'], 'Enrolled')
        dates = opt['__category_names__']
        self.assertEqual(dates, ['2025-09-01', '2025-09-02'])
        self.assertEqual(series['data'], [2.0, 1.0])

    def test_calc_line_subtracts_two_lines(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01', 'absent_flag': '1'},
            {'ssid': '2', 'attendance_date': '2025-09-01'},
            {'ssid': '3', 'attendance_date': '2025-09-01'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'enrolled', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absent_flag'},
                {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract', 'line_a': 'enrolled', 'line_b': 'absent'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Enrolled'], [3.0])
        self.assertEqual(by_name['Absent'], [1.0])
        self.assertEqual(by_name['Present'], [2.0])

    def test_missing_future_column_renders_as_zero_not_error(self):
        """A calc line referencing a column/table that doesn't exist yet must
        not break the chart — it should plot as 0 until the data shows up."""
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01'},
            {'ssid': '2', 'attendance_date': '2025-09-01'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'enrolled', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
                # 'absence_status' doesn't exist in the loaded data — table not built yet
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absence_status'},
                {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract', 'line_a': 'enrolled', 'line_b': 'absent'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertFalse(opt.get('__is_error__'))
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Absent'], [0.0])
        self.assertEqual(by_name['Present'], [2.0])  # same as Enrolled since Absent is 0

    def test_date_bucketing_by_week(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01'},  # Monday, ISO week 36
            {'ssid': '2', 'attendance_date': '2025-09-03'},  # same ISO week
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'week',
            'ds_lines': [
                {'id': 'l1', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertEqual(len(opt['__category_names__']), 1)
        self.assertEqual(opt['series'][0]['data'], [2.0])

    def test_missing_lines_or_date_col_returns_message_not_crash(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'attendance_date': '2025-09-01'}]
        opt = build_option(rows, {}, {'chart_type': 'date_series', 'ds_date_col': '', 'ds_lines': []})
        self.assertTrue(opt.get('__is_error__'))

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_drill_count_matches_chart_line_for_date(self, mock_run_sql):
        from dataviz.utils.drill_through import execute_drill_query, build_drill_filter

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01'},
            {'ssid': '2', 'attendance_date': '2025-09-01'},
            {'ssid': '3', 'attendance_date': '2025-09-02'},
        ]
        mock_run_sql.return_value = {'columns': list(rows[0].keys()), 'rows': rows}

        chart = SimpleNamespace(
            pk=99,
            updated_at=SimpleNamespace(timestamp=lambda: 0),
            source_type='sql',
            source_sql='SELECT 1',
            source_id=None,
            source_filters={},
            chart_type='date_series',
            chart_id=99,
            config={
                'ds_date_col': 'attendance_date',
                'ds_date_bucket': 'day',
                'ds_lines': [
                    {'id': 'l1', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
                ],
            },
            drill_through_columns=['ssid'],
            drill_through_filter_map={},
            column_defs={},
        )

        drill_filters = build_drill_filter(chart, {'x_val': '2025-09-01', 'series_val': 'Enrolled'})
        self.assertEqual(drill_filters.get('ds_date_val'), '2025-09-01')
        self.assertEqual(drill_filters.get('ds_value_col_required'), 'ssid')

        result = execute_drill_query(
            chart, drill_filters, columns=['ssid'],
            user=SimpleNamespace(pk=1, is_superuser=True, roles=SimpleNamespace(all=lambda: [])),
        )
        self.assertEqual(result['total_rows'], 2)

    def test_calc_line_click_is_not_filterable_by_column(self):
        from dataviz.utils.drill_through import build_drill_filter

        chart = SimpleNamespace(
            chart_type='date_series',
            config={
                'ds_date_col': 'attendance_date',
                'ds_lines': [
                    {'id': 'enrolled', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
                    {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absence_status'},
                    {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract', 'line_a': 'enrolled', 'line_b': 'absent'},
                ],
            },
            drill_through_filter_map={},
        )
        filters = build_drill_filter(chart, {'x_val': '2025-09-01', 'series_val': 'Present'})
        self.assertEqual(filters.get('ds_date_val'), '2025-09-01')
        self.assertNotIn('ds_value_col_required', filters)


class DateSeriesActiveRangeTests(SimpleTestCase):
    def test_active_range_overlap_counts_by_day(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'enter_date': '2025-09-01', 'leave_date': '2025-09-10'},
            {'ssid': '2', 'enter_date': '2025-09-03', 'leave_date': None},
            {'ssid': '3', 'enter_date': '2025-09-08', 'leave_date': '2025-09-20'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                 'enter_col': 'enter_date', 'leave_col': 'leave_date'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertFalse(opt.get('__is_error__'))
        by_date = dict(zip(opt['__category_names__'], opt['series'][0]['data']))
        self.assertEqual(by_date['2025-09-01'], 1.0)   # only student 1
        self.assertEqual(by_date['2025-09-08'], 3.0)   # all three overlap
        self.assertEqual(by_date['2025-09-11'], 2.0)   # student 1 left (leave=10)
        self.assertEqual(by_date['2025-09-20'], 2.0)   # student 3's leave day still counts

    def test_axis_is_continuous_no_gaps_skipped(self):
        """Only two rows exist (Sep 1 and Sep 5) but every day in between
        must still appear on the axis, zero-filled for event-count lines."""
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'attendance_date': '2025-09-01'},
            {'ssid': '2', 'attendance_date': '2025-09-05'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'l1', 'line_type': 'base', 'label': 'Enrolled', 'agg_func': 'count', 'value_col': 'ssid'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        dates = opt['__category_names__']
        self.assertEqual(dates, ['2025-09-01', '2025-09-02', '2025-09-03', '2025-09-04', '2025-09-05'])
        by_date = dict(zip(dates, opt['series'][0]['data']))
        self.assertEqual(by_date['2025-09-02'], 0.0)
        self.assertEqual(by_date['2025-09-03'], 0.0)

    def test_active_range_month_bucket_overlap_semantics(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            # Overlaps September only briefly (Sep 25-Oct 5) — still counts for Sep under overlap semantics.
            {'ssid': '1', 'enroll_date': '2025-09-25', 'enter_date': '2025-09-25', 'leave_date': '2025-10-05'},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enroll_date',
            'ds_date_bucket': 'month',
            'ds_lines': [
                {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                 'enter_col': 'enter_date', 'leave_col': 'leave_date'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_date = dict(zip(opt['__category_names__'], opt['series'][0]['data']))
        self.assertEqual(by_date['2025-09'], 1.0)
        self.assertEqual(by_date['2025-10'], 1.0)

    def test_missing_enroll_table_column_renders_as_zero(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'attendance_date': '2025-09-01'}]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'attendance_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                 'enter_col': 'enroll_enter_date', 'leave_col': 'enroll_leave_date'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertFalse(opt.get('__is_error__'))
        self.assertEqual(opt['series'][0]['data'], [0.0])

    def test_active_range_no_leave_col_means_never_leaves(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'enter_date': '2025-09-01'}]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                 'enter_col': 'enter_date', 'leave_col': ''},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertTrue(all(v == 1.0 for v in opt['series'][0]['data']))

    def test_calc_line_can_combine_active_range_and_base(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'enroll_date': '2025-09-01', 'enter_date': '2025-09-01', 'leave_date': None, 'absent_flag': '1'},
            {'ssid': '2', 'enroll_date': '2025-09-01', 'enter_date': '2025-09-01', 'leave_date': None, 'absent_flag': None},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enroll_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                 'enter_col': 'enter_date', 'leave_col': 'leave_date'},
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absent_flag'},
                {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract', 'line_a': 'active', 'line_b': 'absent'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Active'], [2.0])
        self.assertEqual(by_name['Absent'], [1.0])
        self.assertEqual(by_name['Present'], [1.0])

    @patch('dataviz.utils.drill_through.run_raw_sql')
    def test_active_range_drill_matches_chart_count(self, mock_run_sql):
        from dataviz.utils.drill_through import execute_drill_query, build_drill_filter

        rows = [
            {'ssid': '1', 'enter_date': '2025-09-01', 'leave_date': '2025-09-10'},
            {'ssid': '2', 'enter_date': '2025-09-03', 'leave_date': None},
            {'ssid': '3', 'enter_date': '2025-09-15', 'leave_date': '2025-09-20'},
        ]
        mock_run_sql.return_value = {'columns': list(rows[0].keys()), 'rows': rows}

        chart = SimpleNamespace(
            pk=101,
            updated_at=SimpleNamespace(timestamp=lambda: 0),
            source_type='sql',
            source_sql='SELECT 1',
            source_id=None,
            source_filters={},
            chart_type='date_series',
            config={
                'ds_date_col': 'enter_date',
                'ds_date_bucket': 'day',
                'ds_lines': [
                    {'id': 'active', 'line_type': 'active_range', 'label': 'Active',
                     'enter_col': 'enter_date', 'leave_col': 'leave_date'},
                ],
            },
            drill_through_columns=['ssid'],
            drill_through_filter_map={},
            column_defs={},
        )

        drill_filters = build_drill_filter(chart, {'x_val': '2025-09-08', 'series_val': 'Active'})
        self.assertEqual(drill_filters.get('ds_active_line_id'), 'active')

        result = execute_drill_query(
            chart, drill_filters, columns=['ssid'],
            user=SimpleNamespace(pk=1, is_superuser=True, roles=SimpleNamespace(all=lambda: [])),
        )
        # Active on Sep 8: student 1 (1-10) and student 2 (3-open) = 2; student 3 (15-20) not yet entered.
        self.assertEqual(result['total_rows'], 2)
        returned_ssids = sorted(r['ssid'] for r in result['rows'])
        self.assertEqual(returned_ssids, ['1', '2'])


class DateSeriesCalcOperatorTests(SimpleTestCase):
    def test_divide_computes_rate(self):
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'enter_date': '2025-09-01', 'absent_date': '2025-09-01'},
            {'ssid': '2', 'enter_date': '2025-09-01', 'absent_date': None},
            {'ssid': '3', 'enter_date': '2025-09-01', 'absent_date': None},
            {'ssid': '4', 'enter_date': '2025-09-01', 'absent_date': None},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'total', 'line_type': 'base', 'label': 'Total', 'agg_func': 'count', 'value_col': 'ssid'},
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absent_date'},
                {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract', 'line_a': 'total', 'line_b': 'absent'},
                {'id': 'rate', 'line_type': 'calc', 'label': 'Attendance Rate', 'op': 'divide',
                 'line_a': 'present', 'line_b': 'total', 'as_percent': True},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Total'], [4.0])
        self.assertEqual(by_name['Absent'], [1.0])
        self.assertEqual(by_name['Present'], [3.0])
        self.assertEqual(by_name['Attendance Rate'], [75.0])  # 3/4 = 75%

    def test_divide_by_zero_is_zero_not_crash(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'enter_date': '2025-09-01'}]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                # 'missing_col' doesn't exist -> Total is always 0 -> divide by zero
                {'id': 'total', 'line_type': 'base', 'label': 'Total', 'agg_func': 'count', 'value_col': 'missing_col'},
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'missing_col2'},
                {'id': 'rate', 'line_type': 'calc', 'label': 'Rate', 'op': 'divide', 'line_a': 'absent', 'line_b': 'total'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertFalse(opt.get('__is_error__'))
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Rate'], [0.0])

    def test_calc_chain_resolves_regardless_of_list_order(self):
        """rate references present, present references total/absent — but
        the list is written in reverse dependency order on purpose."""
        from dataviz.utils.chart_builder import build_option

        rows = [
            {'ssid': '1', 'enter_date': '2025-09-01', 'absent_date': '2025-09-01'},
            {'ssid': '2', 'enter_date': '2025-09-01', 'absent_date': None},
        ]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'rate', 'line_type': 'calc', 'label': 'Rate', 'op': 'divide',
                 'line_a': 'present', 'line_b': 'total', 'as_percent': True},
                {'id': 'present', 'line_type': 'calc', 'label': 'Present', 'op': 'subtract',
                 'line_a': 'total', 'line_b': 'absent'},
                {'id': 'total', 'line_type': 'base', 'label': 'Total', 'agg_func': 'count', 'value_col': 'ssid'},
                {'id': 'absent', 'line_type': 'base', 'label': 'Absent', 'agg_func': 'count', 'value_col': 'absent_date'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['Present'], [1.0])
        self.assertEqual(by_name['Rate'], [50.0])

    def test_dangling_calc_reference_renders_as_zero_not_crash(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'enter_date': '2025-09-01'}]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'rate', 'line_type': 'calc', 'label': 'Rate', 'op': 'divide',
                 'line_a': 'does_not_exist', 'line_b': 'also_missing'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        self.assertFalse(opt.get('__is_error__'))
        self.assertEqual(opt['series'][0]['data'], [0.0])

    def test_multiply_operator(self):
        from dataviz.utils.chart_builder import build_option

        rows = [{'ssid': '1', 'enter_date': '2025-09-01', 'weight': '2'}]
        cfg = {
            'chart_type': 'date_series',
            'ds_date_col': 'enter_date',
            'ds_date_bucket': 'day',
            'ds_lines': [
                {'id': 'a', 'line_type': 'base', 'label': 'A', 'agg_func': 'sum', 'value_col': 'weight'},
                {'id': 'b', 'line_type': 'base', 'label': 'B', 'agg_func': 'count', 'value_col': 'ssid'},
                {'id': 'c', 'line_type': 'calc', 'label': 'C', 'op': 'multiply', 'line_a': 'a', 'line_b': 'b'},
            ],
        }
        opt = build_option(rows, {}, cfg)
        by_name = {s['name']: s['data'] for s in opt['series']}
        self.assertEqual(by_name['C'], [2.0])  # 2 * 1


class ChartRenderCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='password', is_staff=True, is_superuser=True)
        from django.core.cache import cache
        cache.clear()

    @patch('dataviz.utils.data_loader.run_column_query')
    def test_chart_render_cache_isolation_by_filters(self, mock_run_query):
        mock_run_query.return_value = {
            'rows': [{'student_id': '1', 'grade': 'K'}],
            'columns': ['student_id', 'grade']
        }
        
        chart = SavedChart.objects.create(
            name="Test Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_COLUMNS,
            config={'selected_columns': ['student_id', 'grade'], 'chart_type': 'bar'},
            owner=self.user
        )
        
        from django.test import RequestFactory
        from dataviz.views.api.chart_api import chart_render_api
        
        rf = RequestFactory()
        
        # 1. First request - no filters (MISS)
        req1 = rf.get(f'/api/charts/{chart.id}/render/')
        req1.user = self.user
        resp1 = chart_render_api(req1, chart.id)
        self.assertEqual(resp1.get('X-Chart-Cache'), 'MISS')
        
        # 2. Second request - no filters (HIT)
        req2 = rf.get(f'/api/charts/{chart.id}/render/')
        req2.user = self.user
        resp2 = chart_render_api(req2, chart.id)
        self.assertEqual(resp2.get('X-Chart-Cache'), 'HIT')
        
        # 3. Third request - with filter pf_grade=K (MISS)
        req3 = rf.get(f'/api/charts/{chart.id}/render/?pf_grade=K')
        req3.user = self.user
        resp3 = chart_render_api(req3, chart.id)
        self.assertEqual(resp3.get('X-Chart-Cache'), 'MISS')
        
        # 4. Fourth request - with filter pf_grade=K (HIT)
        req4 = rf.get(f'/api/charts/{chart.id}/render/?pf_grade=K')
        req4.user = self.user
        resp4 = chart_render_api(req4, chart.id)
        self.assertEqual(resp4.get('X-Chart-Cache'), 'HIT')
        
        # 5. Fifth request - with different filter pf_grade=1 (MISS)
        req5 = rf.get(f'/api/charts/{chart.id}/render/?pf_grade=1')
        req5.user = self.user
        resp5 = chart_render_api(req5, chart.id)
        self.assertEqual(resp5.get('X-Chart-Cache'), 'MISS')


class ChartAccessControlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='restricted_user', password='password', is_staff=False, is_superuser=False)
        from unittest.mock import MagicMock
        self.user.active_role = MagicMock()
        self.user.active_role.name = 'School Gr View'
        self.user.active_school = MagicMock()
        self.user.active_school.state_id = '12345'
        self.user.schools = ['12345']
        self.user.grade_levels = ['K', '1']

        self.chart = SavedChart.objects.create(
            name="Test Saved Query Chart",
            chart_type="bar",
            source_type=SavedChart.SOURCE_QUERY,
            source_id=999,
            config={'chart_type': 'bar'},
            owner=self.user
        )

    @patch('dataviz.utils.data_loader._run_saved_query_sql')
    @patch('dataexploration.utils.can_user_view_query')
    @patch('dataexploration.models.SavedQuery.objects.get')
    def test_chart_rendering_does_not_apply_user_filters(self, mock_get_query, mock_can_view, mock_run_sql):
        mock_can_view.return_value = True
        from dataexploration.models import SavedQuery
        mock_sq = SavedQuery(id=999, config={'columns': ['student_id', 'grade'], 'filters': {}})
        mock_get_query.return_value = mock_sq

        mock_run_sql.return_value = {
            'rows': [{'student_id': '1', 'grade': 'K'}],
            'columns': ['student_id', 'grade']
        }

        from django.test import RequestFactory
        from dataviz.views.api.chart_api import chart_render_api

        rf = RequestFactory()
        req = rf.get(f'/api/charts/{self.chart.id}/render/')
        req.user = self.user

        resp = chart_render_api(req, self.chart.id)
        self.assertEqual(resp.status_code, 200)

        # Verify that _run_saved_query_sql was called with user=None (unrestricted)
        mock_run_sql.assert_called_once()
        called_args, called_kwargs = mock_run_sql.call_args
        self.assertIsNone(called_kwargs.get('user'))


class DynamicSummaryTableOptionTests(TestCase):
    def test_dynamic_summary_table_calculations_mode(self):
        import pandas as pd
        from dataviz.utils.chart_builder import _dynamic_summary_table_option

        df = pd.DataFrame([
            {'grade': '5', 'ssid': '123', 'scale_score': 300.0},
            {'grade': '5', 'ssid': '456', 'scale_score': 400.0},
            {'grade': '6', 'ssid': '789', 'scale_score': 500.0},
        ])

        cfg = {
            'dyn_col_mode': 'calculations',
            'dyn_calculations': [
                {'agg_func': 'count', 'value_col': 'ssid', 'label': 'My Count', 'decimals': None},
                {'agg_func': 'avg', 'value_col': 'scale_score', 'label': 'My Avg', 'decimals': 1},
            ],
            'dyn_show_row_totals': False,
            'dyn_show_col_totals': False,
        }

        res = _dynamic_summary_table_option(df, row_cols=['grade'], col_cols=[], agg_func='count', value_col='', agg_decimals=None, cfg=cfg, col_defs=None)
        
        self.assertEqual(res['__type__'], 'table')
        self.assertEqual(res['col_headers'], ['My Count', 'My Avg'])
        self.assertEqual(res['rows'], [
            ['5', 2.0, 350.0],
            ['6', 1.0, 500.0],
        ])

    def test_dynamic_summary_table_echarts_mode(self):
        import pandas as pd
        from dataviz.utils.chart_builder import _dynamic_summary_table_option

        df = pd.DataFrame([
            {'grade': '5', 'ssid': '123', 'scale_score': 300.0},
            {'grade': '5', 'ssid': '456', 'scale_score': 400.0},
            {'grade': '6', 'ssid': '789', 'scale_score': 500.0},
        ])

        cfg = {
            'dyn_col_mode': 'calculations',
            'dyn_calculations': [
                {'agg_func': 'count', 'value_col': 'ssid', 'label': 'My Count', 'decimals': None},
            ],
            'dyn_show_row_totals': False,
            'dyn_show_col_totals': False,
            'dyn_display_type': 'stacked_bar',
        }

        res = _dynamic_summary_table_option(df, row_cols=['grade'], col_cols=[], agg_func='count', value_col='', agg_decimals=None, cfg=cfg, col_defs=None)

        self.assertNotIn('__type__', res)
        self.assertEqual(res['xAxis']['data'], ['5', '6'])
        self.assertEqual(res['series'][0]['name'], 'My Count')
        self.assertEqual(res['series'][0]['type'], 'bar')
        self.assertEqual(res['series'][0]['stack'], 'total')
        self.assertEqual(res['series'][0]['data'], [2.0, 1.0])

    def test_dynamic_summary_table_pivot_decimals(self):
        import pandas as pd
        from dataviz.utils.chart_builder import _dynamic_summary_table_option

        df = pd.DataFrame([
            {'grade': '5', 'gender': 'F', 'scale_score': 300.567},
            {'grade': '5', 'gender': 'F', 'scale_score': 400.123},
            {'grade': '6', 'gender': 'M', 'scale_score': 500.000},
        ])

        cfg = {
            'dyn_col_mode': 'pivot',
            'dyn_show_row_totals': True,
            'dyn_show_col_totals': True,
        }

        # Test single column pivot category decimals rounding
        res = _dynamic_summary_table_option(
            df, row_cols=['grade'], col_cols=['gender'], agg_func='avg', value_col='scale_score',
            agg_decimals=1, cfg=cfg, col_defs=None
        )

        self.assertEqual(res['__type__'], 'table')
        # Row 1: '5', F=350.345 -> rounded to 350.3, M=0.0, RowTotal=350.3
        self.assertEqual(res['rows'][0], ['5', 350.3, 0.0, 350.3])
        self.assertEqual(res['total_row'], ['Total', 350.3, 500.0, 850.3])

    def test_summary_table_and_dynamic_table_tooltips(self):
        import pandas as pd
        from dataviz.utils.chart_builder import _summary_table_option, _dynamic_summary_table_option

        df = pd.DataFrame([
            {'grade': '5', 'gender': 'F', 'scale_score': 300.0, 'ssid': '1'},
            {'grade': '5', 'gender': 'F', 'scale_score': 400.0, 'ssid': '1'},
            {'grade': '5', 'gender': 'F', 'scale_score': 500.0, 'ssid': '2'},
            {'grade': '6', 'gender': 'M', 'scale_score': 500.0, 'ssid': '3'},
        ])

        # 1. Summary Table (Values mode, count agg, showing tooltips)
        cfg_st = {
            'st_col_mode': 'values',
            'st_agg_func': 'count',
            'st_value_col': 'scale_score',
            'st_show_row_totals': False,
            'st_show_col_totals': False,
            'st_show_tooltips': True,
        }
        res_st = _summary_table_option(df, row_col='grade', col_col='gender', cfg=cfg_st, col_defs=None)
        # Expected: grade 5, Female count=2 (student 1 counted once, student 2 once)
        # Alt Average of the deduplicated student scores: (300+500)/2 = 400.0
        self.assertEqual(res_st['rows'][0][1], {'val': 2.0, 'tooltip': 'Average: 400.0'})

        # 2. Dynamic Table (Calculations mode, count agg & avg agg, showing tooltips)
        cfg_dyn = {
            'dyn_col_mode': 'calculations',
            'dyn_calculations': [
                {'agg_func': 'count', 'value_col': 'scale_score', 'label': 'My Count'},
                {'agg_func': 'avg', 'value_col': 'scale_score', 'label': 'My Avg', 'decimals': 1},
            ],
            'dyn_show_row_totals': False,
            'dyn_show_col_totals': False,
            'dyn_show_tooltips': True,
        }
        res_dyn = _dynamic_summary_table_option(df, row_cols=['grade'], col_cols=[], agg_func='count', value_col='', agg_decimals=None, cfg=cfg_dyn, col_defs=None)
        # Expected row 1 (grade 5):
        # Column 1 (Count of scale_score): val=2.0 (unique ssid count), Alt Avg = (300+500)/2 = 400.0 -> {'val': 2.0, 'tooltip': 'Average: 400.0'}
        # Column 2 (Avg of scale_score): val=400.0 (all 3 scores averaged), Alt unique count = 2 -> {'val': 400.0, 'tooltip': 'Count: 2'}
        self.assertEqual(res_dyn['rows'][0][1], {'val': 2.0, 'tooltip': 'Average: 400.0'})
        self.assertEqual(res_dyn['rows'][0][2], {'val': 400.0, 'tooltip': 'Count: 2'})


class DatasetExportCSVTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='password', is_superuser=True)

    @patch('dataviz.views.dataset_views.compile_dataset_to_sql')
    @patch('dataviz.views.dataset_views.execute_compiled_query')
    def test_dataset_export_csv_api(self, mock_execute, mock_compile):
        mock_compile.return_value = ("SELECT * FROM ...", [])
        mock_execute.return_value = {
            'columns': ['ssid', 'student_name', 'grade'],
            'rows': [
                {'ssid': '123', 'student_name': 'Alice', 'grade': '5'}
            ]
        }
        self.client.force_login(self.user)

        payload = {
            'base_table': 'current_student',
            'distinct': True,
            'joins': [],
            'filters': [],
            'columns': ['current_student.ssid', 'current_student.student_name', 'current_student.grade']
        }

        response = self.client.post(
            '/dataviz/api/datasets/export-csv/',
            data=payload,
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="dataset_export.csv"', response['Content-Disposition'])

        # Consume the streaming content
        content = b"".join(response.streaming_content).decode('utf-8')
        lines = content.strip().split('\n')
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], '"ssid","student_name","grade"')
        self.assertEqual(lines[1], '"123","Alice","5"')









