from django.test import SimpleTestCase
from unittest.mock import patch, MagicMock

from dataviz.utils.dataloader_links import (
    build_survey_pivot_subquery,
    expand_dataloader_joins,
    school_year_match_expr,
    suggest_attach_payload,
    survey_link_id,
    LINK_TYPE_AP_RESULTS,
    LINK_TYPE_SURVEY,
)
from dataviz.utils.compiler import (
    compile_dataset_to_sql,
    SQLCompilationError,
    get_dataset_table_registry,
    dataset_table_choices,
)


class DatasetTableRegistryTests(SimpleTestCase):
    def test_registry_includes_clickhouse_analytics_tables(self):
        reg = get_dataset_table_registry()
        for name in (
            'analytics_ap_results',
            'analytics_ap_awards',
            'analytics_sat_results',
            'analytics_survey_responses',
        ):
            self.assertIn(name, reg)

    def test_dataset_table_choices_include_analytics_labels(self):
        keys = {k for k, _ in dataset_table_choices()}
        self.assertIn('analytics_survey_responses', keys)


class SurveyToolboxGroupTests(SimpleTestCase):
    def test_survey_groups_combine_same_type_and_year(self):
        from dataviz.utils.dataloader_links import build_survey_toolbox_groups

        surveys = [
            {
                'link_id': 'A|2025-2026|SPRING|Grades 6-8',
                'survey_type': 'Skills and Competencies',
                'school_year': '2025-2026',
                'window': 'SPRING',
                'grade_range': 'Grades 6-8',
                'questions': [{'key': 'q1'}, {'key': 'q2'}],
            },
            {
                'link_id': 'A|2025-2026|FALL|Grades 3-5',
                'survey_type': 'Skills and Competencies',
                'school_year': '2025-2026',
                'window': 'FALL',
                'grade_range': 'Grades 3-5',
                'questions': [{'key': 'q3'}],
            },
        ]
        groups = build_survey_toolbox_groups(surveys)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['title'], 'Skills and Competencies')
        self.assertEqual(groups[0]['subtitle'], '2025-2026')
        self.assertEqual(len(groups[0]['items']), 2)
        short_labels = ' '.join(i['short_label'] for i in groups[0]['items'])
        self.assertIn('Fall', short_labels)
        self.assertIn('Spring', short_labels)


class DataLoaderLinksSqlTests(SimpleTestCase):
    def test_school_year_match_includes_substring_fallback(self):
        sql = school_year_match_expr('"cs"."school_year"', '"ap"."admin_year"')
        self.assertIn('substring', sql.lower())
        self.assertIn('admin_year', sql)

    @patch('dataviz.utils.dataloader_links.get_survey_columns')
    def test_survey_pivot_uses_maxif_and_filters(self, mock_cols):
        link_id = survey_link_id('Skills and Competencies', '2025-2026', 'SPRING', 'Grades 6-8')
        mock_cols.return_value = {
            'col_meta': {
                'survey_skills_2626_SPR_6_8_Q1': {
                    'survey_type': 'Skills and Competencies',
                    'school_year': '2025-2026',
                    'window': 'SPRING',
                    'grade_range': 'Grades 6-8',
                    'question_order': 0,
                },
            },
            'definitions': {},
        }
        subquery, alias, cols, params = build_survey_pivot_subquery(
            link_id, ['survey_skills_2626_SPR_6_8_Q1'],
        )
        self.assertIn('maxIf', subquery)
        self.assertIn('question_order = 0', subquery)
        self.assertIn('analytics_survey_responses', subquery)
        self.assertIn('survey_type', subquery)
        self.assertEqual(params[0], 'Skills and Competencies')
        self.assertIn('survey_skills_2626_SPR_6_8_Q1', cols)
        self.assertTrue(alias.startswith('dl_survey_'))

    def test_build_table_year_join_ssid_only(self):
        from dataviz.utils.dataloader_links import (
            build_table_year_join_on,
            SCHOOL_YEAR_JOIN_SSID_ONLY,
        )
        self.assertIsNone(
            build_table_year_join_on(
                LINK_TYPE_AP_RESULTS, 'current_student', 'analytics_ap_results',
                SCHOOL_YEAR_JOIN_SSID_ONLY,
            )
        )

    def test_expand_ap_results_adds_year_match_extra_on(self):
        joins = [{
            'kind': 'dataloader',
            'link_type': LINK_TYPE_AP_RESULTS,
            'join_type': 'left',
        }]
        table_joins, raw, virtual, params = expand_dataloader_joins(joins, 'current_student')
        self.assertEqual(len(table_joins), 1)
        self.assertEqual(table_joins[0]['table'], 'analytics_ap_results')
        self.assertIn('_extra_on', table_joins[0])
        self.assertIn('admin_year', table_joins[0]['_extra_on'])
        self.assertEqual(raw, [])
        self.assertEqual(params, [])

    def test_expand_survey_emits_subquery_join(self):
        link_id = survey_link_id('SE', '2025-2026', 'FALL', '')
        with patch('dataviz.utils.dataloader_links.get_survey_columns') as mock_cols:
            mock_cols.return_value = {
                'col_meta': {
                    'survey_se_2626_FAL_ALL_Q2': {
                        'survey_type': 'SE',
                        'school_year': '2025-2026',
                        'window': 'FALL',
                        'grade_range': '',
                        'question_order': 1,
                    },
                },
                'definitions': {},
            }
            joins = [{
                'kind': 'dataloader',
                'link_type': LINK_TYPE_SURVEY,
                'link_id': link_id,
                'columns': ['survey_se_2626_FAL_ALL_Q2'],
                'join_type': 'left',
            }]
            table_joins, raw, virtual, params = expand_dataloader_joins(joins, 'current_student')
        self.assertEqual(table_joins, [])
        self.assertEqual(len(raw), 1)
        self.assertIn('LEFT JOIN', raw[0])
        self.assertIn('maxIf', raw[0])
        self.assertEqual(len(params), 4)
        self.assertTrue(virtual)

    def test_suggest_attach_payload_survey_qualified_columns(self):
        link_id = survey_link_id('SE', '2025-2026', 'FALL', '')
        with patch('dataviz.utils.dataloader_links.get_survey_columns') as mock_cols:
            mock_cols.return_value = {
                'col_meta': {
                    'survey_se_2626_FAL_ALL_Q2': {
                        'survey_type': 'SE',
                        'school_year': '2025-2026',
                        'window': 'FALL',
                        'grade_range': '',
                        'question_order': 1,
                    },
                },
                'definitions': {},
            }
            payload = suggest_attach_payload(
                LINK_TYPE_SURVEY,
                'current_student',
                link_id=link_id,
                selected_columns=['survey_se_2626_FAL_ALL_Q2'],
            )
        self.assertEqual(payload['join']['kind'], 'dataloader')
        self.assertTrue(payload['columns'][0].startswith('dl_survey_'))

    def test_suggest_attach_payload_ap_persists_columns_on_join(self):
        payload = suggest_attach_payload(
            LINK_TYPE_AP_RESULTS,
            'current_student',
            selected_columns=['exam_name', 'exam_grade'],
            school_year_join='ssid_only',
        )
        self.assertEqual(payload['join']['table'], 'analytics_ap_results')
        self.assertEqual(payload['join']['columns'], ['exam_name', 'exam_grade'])
        self.assertEqual(
            payload['columns'],
            ['analytics_ap_results.exam_name', 'analytics_ap_results.exam_grade'],
        )


class DataLoaderCompilerTests(SimpleTestCase):
    @patch('dataviz.utils.compiler.get_allowed_dataset_tables')
    @patch('dataviz.utils.dataloader_links.get_survey_columns')
    def test_compile_dataset_with_survey_link(self, mock_survey, mock_allowed):
        mock_allowed.return_value = {
            'current_student': 'current_student',
            'analytics_survey_responses': 'analytics_survey_responses',
        }
        link_id = survey_link_id('SE', '2025-2026', 'FALL', '')
        col_key = 'survey_se_2626_FAL_ALL_Q2'
        alias = suggest_attach_payload(
            LINK_TYPE_SURVEY, 'current_student', link_id=link_id, selected_columns=[col_key],
        )['virtual_table']
        mock_survey.return_value = {
            'col_meta': {
                col_key: {
                    'survey_type': 'SE',
                    'school_year': '2025-2026',
                    'window': 'FALL',
                    'grade_range': '',
                    'question_order': 1,
                },
            },
            'definitions': {col_key: 'Question two'},
        }

        ds = MagicMock()
        ds.base_table = 'current_student'
        ds.distinct = True
        ds.joins = [{
            'kind': 'dataloader',
            'link_type': LINK_TYPE_SURVEY,
            'link_id': link_id,
            'columns': [col_key],
            'table': alias,
            'join_type': 'left',
        }]
        ds.filters = []
        ds.columns = [f'{alias}.{col_key}', 'current_student.ssid']
        ds.order_by = []
        ds.updated_at = ''

        from dataviz.utils.compiler import _UNSET
        sql, params = compile_dataset_to_sql(ds, user=None, status_filter=_UNSET)
        self.assertIn('maxIf', sql)
        self.assertIn('analytics_survey_responses', sql)
        self.assertEqual(params[:4], ['SE', '2025-2026', 'FALL', ''])

    @patch('dataviz.utils.compiler.get_allowed_dataset_tables')
    def test_compile_rejects_blocked_dataloader_table(self, mock_allowed):
        mock_allowed.return_value = {'current_student': 'current_student'}
        ds = MagicMock()
        ds.base_table = 'current_student'
        ds.joins = [{'kind': 'dataloader', 'link_type': LINK_TYPE_AP_RESULTS}]
        ds.columns = ['current_student.ssid']
        ds.filters = []
        ds.distinct = True
        ds.order_by = []
        ds.updated_at = ''
        with self.assertRaises(SQLCompilationError):
            compile_dataset_to_sql(ds, user=None, status_filter=[])
