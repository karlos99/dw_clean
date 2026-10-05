from django.test import SimpleTestCase

from dataexploration.view_modules.column_management import strip_course_program_filters
from dataexploration.catalog_columns import (
    COURSE_PROGRAM_DEFINITIONS,
    apply_course_program_column_filter,
    canonical_course_program_column,
    filters_without_course_program_dimension,
    merge_course_program_columns,
)


class CourseProgramColumnSettingsTests(SimpleTestCase):
    def test_merge_inserts_category_after_performance_when_enabled(self):
        from unittest.mock import patch

        defs = {'grade': 'Grade'}
        cats = {
            'programs': {'title': 'Programs', 'columns': ['grade']},
            'performance': {'title': 'Performance & Behavior', 'columns': ['atendace_rate']},
        }
        with patch('dataexploration.catalog_columns.course_program_columns_enabled', return_value=True):
            merged_defs, merged_cats = merge_course_program_columns(defs, cats)
        self.assertIn('course_prog_tag', merged_defs)
        keys = list(merged_cats.keys())
        self.assertEqual(keys.index('performance') + 1, keys.index('course_programs'))
        self.assertEqual(
            merged_cats['course_programs']['columns'],
            list(COURSE_PROGRAM_DEFINITIONS.keys()),
        )

    def test_merge_skipped_when_disabled(self):
        from unittest.mock import patch

        with patch('dataexploration.catalog_columns.course_program_columns_enabled', return_value=False):
            merged_defs, merged_cats = merge_course_program_columns({'a': 'A'}, {'b': {'title': 'B', 'columns': []}})
        self.assertEqual(merged_defs, {'a': 'A'})
        self.assertNotIn('course_programs', merged_cats)

    def test_legacy_filter_key_maps_to_canonical_dimension(self):
        self.assertEqual(canonical_course_program_column('course_prog_cte_pathway'), 'course_prog_discipline')
        stripped = filters_without_course_program_dimension(
            {'course_prog_cte_pathway': ['X'], 'course_prog_tag': ['CTE']},
            'course_prog_discipline',
        )
        self.assertNotIn('course_prog_cte_pathway', stripped)
        self.assertIn('course_prog_tag', stripped)

    def test_apply_course_program_filter_appends_exists_clause(self):
        from unittest.mock import patch

        where, params = [], []
        with patch('dataexploration.catalog_columns.course_program_columns_enabled', return_value=True):
            apply_course_program_column_filter(where, params, 'course_prog_tag', ['CTE'])
        self.assertEqual(len(where), 1)
        self.assertIn('EXISTS', where[0])
        self.assertIn('schedule_list', where[0])
        self.assertIn("schedule_status IN ('Active', 'Completed')", where[0])

    def test_clickhouse_filter_uses_uncorrelated_tuple_in(self):
        from dataexploration.catalog_columns import (
            _apply_one_course_program_filter_clickhouse,
        )

        where, params = [], []
        _apply_one_course_program_filter_clickhouse(where, params, 'course_prog_tag', ['VAPA'])
        self.assertEqual(len(where), 1)
        self.assertNotIn('EXISTS', where[0])
        self.assertIn('(cs.stu_annual_key, cs.school_year) IN (', where[0])
        self.assertIn('analytics_course_program_values', where[0])
        self.assertEqual(params, ['tag', ['VAPA']])

    def test_clickhouse_blank_filter_uses_uncorrelated_not_in(self):
        from dataexploration.catalog_columns import (
            _apply_one_course_program_filter_clickhouse,
        )

        where, params = [], []
        _apply_one_course_program_filter_clickhouse(where, params, 'course_prog_tag', [''])
        self.assertEqual(len(where), 1)
        self.assertNotIn('EXISTS', where[0])
        self.assertIn('(cs.stu_annual_key, cs.school_year) NOT IN (', where[0])
        self.assertEqual(params, ['tag'])

    def test_strip_course_program_filters_removes_only_course_program_keys(self):
        stripped = strip_course_program_filters({
            'school_year': '2026-2027',
            'grade': '9',
            'course_prog_tag': ['VAPA'],
            'course_prog_cte_pathway': ['X'],
        })
        self.assertEqual(stripped, {'school_year': '2026-2027', 'grade': '9'})

    def test_strip_course_program_filters_preserves_non_dict(self):
        self.assertIsNone(strip_course_program_filters(None))

    def test_enabled_columns_allowlist_includes_course_programs_when_feature_on(self):
        from unittest.mock import MagicMock, patch

        from dataexploration.view_modules.column_management import get_enabled_columns

        mock_cfg = MagicMock()
        mock_cfg.enabled_columns = ['ssid', 'grade']
        mock_cfg.enable_course_program_columns = True
        with patch('landing.utils.get_project_config', return_value=mock_cfg):
            with patch('dataexploration.view_modules.column_management.course_program_columns_enabled', return_value=True):
                _defs, cats = get_enabled_columns()
        self.assertIn('course_programs', cats)
        self.assertTrue(cats['course_programs']['columns'])
