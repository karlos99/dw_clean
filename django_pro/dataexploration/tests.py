from pathlib import Path
from django.test import SimpleTestCase
from django.template.loader import get_template


class TemplateCompilationTests(SimpleTestCase):
	def test_folder_browser_template_compiles(self):
		# This template is rendered via HTMX at /dataexploration/api/folders/.
		# A small syntax regression (e.g. missing spaces around '==') causes a hard 500.
		get_template("dataexploration/partials/folder_browser.html")

	def test_student_with_tests_list_template_compiles(self):
		get_template("dataexploration/student_with_tests_list.html")

	def test_dataexploration_manage_default_columns_compiles(self):
		get_template("dataexploration/manage_default_columns.html")

	def test_users_manage_default_columns_compiles(self):
		get_template("users/manage_default_columns.html")

	def test_nextyearlist_column_structures_compiles(self):
		get_template("nextyearlist/partials/column_structures.html")

	def test_users_column_structures_compiles(self):
		get_template("users/partials/column_structures.html")

	def test_dataexploration_manage_default_assessment_tree_compiles(self):
		get_template("dataexploration/partials/manage_default_assessment_tree.html")

	def test_users_manage_default_assessment_tree_compiles(self):
		get_template("users/partials/manage_default_assessment_tree.html")


class StudentListConfigTests(SimpleTestCase):
	def test_student_list_savedquery_config_key(self):
		# Student lists are stored as SavedQuery(query_type='student_list')
		# with SSIDs in config['student_ids'].
		config = {"student_ids": ["123", "456"]}
		self.assertEqual(config.get("student_ids"), ["123", "456"])


class StudentListFilterScriptTests(SimpleTestCase):
	def test_student_list_script_exposes_global_load_data(self):
		script_path = Path(__file__).resolve().parent.parent / "templates" / "static" / "js" / "student_tests_app.js"
		self.assertTrue(script_path.exists(), msg=f"Missing script at {script_path}")
		content = script_path.read_text(encoding="utf-8")
		self.assertIn("window.loadData = loadData", content)

	def test_filter_dropdowns_call_window_load_data(self):
		# loadData() is a closure-local const inside student_tests_app.js, so Alpine's
		# inline @change handlers (evaluated in global scope) can only reach it via
		# window.loadData. A bare @change="loadData()" silently no-ops the filter.
		templates_dir = Path(__file__).resolve().parent.parent / "templates"
		for relative_path in (
			"dataexploration/student_with_tests_list.html",
			"nextyearlist/student_list.html",
		):
			content = (templates_dir / relative_path).read_text(encoding="utf-8")
			self.assertNotIn(
				'@change="loadData()"', content,
				msg=f"{relative_path} calls loadData() directly; use window.loadData() so Alpine can resolve it",
			)


from unittest.mock import patch
from dataexploration.school_year_utils import resolve_relative_column_key
from dataexploration.column_utils import test_metadata_matches


class StudentSummaryDedupeTests(SimpleTestCase):
	def test_dedupe_merges_duplicate_results_with_dfp(self):
		from dataexploration.view_modules.student_data_utils import _dedupe_test_records

		base = {
			'school_year': '2025-2026', 'test_name': 'CAASPP', 'subject': 'ELA',
			'test_window': None, 'pl': '2', 'ss': '2499', 'school_code': 'S1',
			'grade': '11', 'school': 'Amistad HS', 'school_short_name': 'Amistad HS',
		}
		rows = [
			{**base, 'other_info': {'Genre': 'ARGU'}},
			{**base, 'other_info': {'dfp': -84.0, 'Genre': 'ARGU'}},
			{**base, 'other_info': {}},
		]
		out = _dedupe_test_records(rows)
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0]['other_info'].get('dfp'), -84.0)
		self.assertEqual(out[0]['other_info'].get('Genre'), 'ARGU')

	def test_dedupe_collapses_school_join_fanout(self):
		from dataexploration.view_modules.student_data_utils import _dedupe_test_records

		base = {
			'school_year': '2025-2026', 'test_name': 'CAASPP', 'subject': 'Math',
			'test_window': None, 'pl': '1', 'ss': '2501', 'school_code': 'S2',
			'grade': '11', 'school': 'Amistad HS',
		}
		rows = [
			{**base, 'school_short_name': 'Amistad HS', 'other_info': {'dfp': -127.0}},
			{**base, 'school_short_name': 'Amistad IS', 'other_info': {}},
			{**base, 'school_short_name': 'Amistad ATP', 'other_info': {}},
		]
		out = _dedupe_test_records(rows)
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0]['other_info'].get('dfp'), -127.0)

	def test_dedupe_preserves_distinct_scores(self):
		from dataexploration.view_modules.student_data_utils import _dedupe_test_records

		base = {
			'school_year': '2025-2026', 'test_name': 'iready', 'subject': 'Math',
			'test_window': 'Spring', 'pl': '2 Grade Levels Below', 'school_code': 'S3',
			'grade': '11', 'school': 'Amistad HS', 'school_short_name': 'Amistad HS',
		}
		rows = [
			{**base, 'ss': '482.0', 'other_info': {'LQMeasure': '610Q'}},
			{**base, 'ss': '485.0', 'other_info': {'LQMeasure': '620Q'}},
			{**base, 'ss': '402.0', 'other_info': {'LQMeasure': '560Q'}},
		]
		out = _dedupe_test_records(rows)
		self.assertEqual(len(out), 3)
		self.assertEqual({r['ss'] for r in out}, {'482.0', '485.0', '402.0'})

	def test_dedupe_empty(self):
		from dataexploration.view_modules.student_data_utils import _dedupe_test_records
		self.assertEqual(_dedupe_test_records([]), [])


class TestMetadataMatchesTests(SimpleTestCase):
	def test_caa_does_not_match_caaspp(self):
		info = {'test_name': 'CAA', 'subject': 'ELA', 'test_window': None}
		self.assertFalse(test_metadata_matches(info, 'CAASPP', 'ELA', ''))

	def test_caaspp_matches_caaspp(self):
		info = {'test_name': 'CAASPP', 'subject': 'ELA', 'test_window': None}
		self.assertTrue(test_metadata_matches(info, 'CAASPP', 'ELA', ''))

	def test_no_window_column_accepts_any_window(self):
		info = {'test_name': 'CAASPP', 'subject': 'ELA', 'test_window': None}
		self.assertTrue(test_metadata_matches(info, 'CAASPP', 'ELA', 'Spring'))
		self.assertTrue(test_metadata_matches(info, 'CAASPP', 'ELA', ''))

	def test_window_must_match_when_specified(self):
		info = {'test_name': 'CAASPP', 'subject': 'ELA', 'test_window': 'Fall'}
		self.assertTrue(test_metadata_matches(info, 'CAASPP', 'ELA', 'Fall'))
		self.assertFalse(test_metadata_matches(info, 'CAASPP', 'ELA', 'Spring'))

	def test_both_empty_windows_match(self):
		info = {'test_name': 'CAA', 'subject': 'Math', 'test_window': ''}
		self.assertTrue(test_metadata_matches(info, 'CAA', 'Math', None))


class ResultsStudentMatchTests(SimpleTestCase):
	def test_recycled_local_id_does_not_attach_wrong_ssid_scores(self):
		from dataexploration.column_utils import assign_result_value_maps, lookup_result_value

		# Result belongs to SSID 3122440885 but shares local_id with another student.
		rows = [
			('20250161', '3122440885', '1'),
			('999', None, '4'),
		]
		by_id, by_ssid = assign_result_value_maps(rows)
		self.assertEqual(by_ssid['3122440885'], '1')
		self.assertNotIn('20250161', by_id)
		self.assertEqual(by_id['999'], '4')

		emily = {'student_id': '20250161', 'ssid': '8442970229'}
		owner = {'student_id': 'other', 'ssid': '3122440885'}
		orphan = {'student_id': '999', 'ssid': 'orphan'}

		self.assertEqual(lookup_result_value(emily, by_id, by_ssid), '—')
		self.assertEqual(lookup_result_value(owner, by_id, by_ssid), '1')
		self.assertEqual(lookup_result_value(orphan, by_id, by_ssid), '4')

	def test_normalize_score_value_blank_sentinels(self):
		from dataexploration.column_utils import normalize_score_value
		for sentinel in ('NaN', 'nan', 'NAN', 'NS', 'ns', 'N/A', 'n/a', 'NA', 'na', 'null', 'None', '', '  '):
			self.assertIsNone(normalize_score_value(sentinel), msg=f'{sentinel!r} should be blank')
		self.assertEqual(normalize_score_value('3'), '3')
		self.assertEqual(normalize_score_value('2461'), '2461')
		self.assertEqual(normalize_score_value('At Benchmark'), 'At Benchmark')
		self.assertEqual(normalize_score_value(0), '0')
		self.assertIsNone(normalize_score_value(None))

	def test_assign_result_value_maps_blank_sentinel_scores(self):
		from dataexploration.column_utils import assign_result_value_maps, lookup_result_value
		rows = [
			('loc1', '3122440885', 'NaN'),
			('loc2', None, 'NS'),
			('loc3', '555', '2'),
		]
		by_id, by_ssid = assign_result_value_maps(rows)
		self.assertEqual(lookup_result_value({'ssid': '3122440885'}, by_id, by_ssid), '—')
		self.assertEqual(lookup_result_value({'student_id': 'loc2', 'ssid': ''}, by_id, by_ssid), '—')
		self.assertEqual(lookup_result_value({'ssid': '555'}, by_id, by_ssid), '2')

	def test_export_cell_uses_blank_marker_consistently(self):
		from dataexploration.column_utils import export_cell
		self.assertEqual(export_cell(None), '—')
		self.assertEqual(export_cell(''), '—')
		self.assertEqual(export_cell('NaN'), '—')
		self.assertEqual(export_cell('NS'), '—')
		self.assertEqual(export_cell('n/a'), '—')
		self.assertEqual(export_cell('3'), '3')
		self.assertEqual(export_cell('2461'), '2461')
		self.assertEqual(export_cell('At Benchmark'), 'At Benchmark')
		self.assertEqual(export_cell(0), '0')
		self.assertEqual(export_cell('—'), '—')

	def test_parse_apall_sort_key(self):
		from dataexploration.column_utils import parse_apall_sort_key
		self.assertEqual(
			parse_apall_sort_key('apall_2024_2025_1_SUBJECT'),
			{'year': '2024-2025', 'index': 1, 'field': 'SUBJECT'},
		)
		self.assertEqual(
			parse_apall_sort_key('apall_2025_2026_3_PL'),
			{'year': '2025-2026', 'index': 3, 'field': 'PL'},
		)
		self.assertIsNone(parse_apall_sort_key('ap_2024_2025_Calc_AB_PL'))
		self.assertIsNone(parse_apall_sort_key('apawards_all_2024_2025'))

	def test_pl_sort_sql_escapes_like_wildcards_for_psycopg(self):
		from dataexploration.column_utils import PL_SORT_SQL
		# Raw '%' would be eaten as a placeholder; must be '%%' in the fragment.
		self.assertIn("LIKE 'novice%%'", PL_SORT_SQL)
		self.assertIn("LIKE '%%ifep%%'", PL_SORT_SQL)
		self.assertNotIn("LIKE 'novice%'", PL_SORT_SQL.replace('%%', ''))

	def test_enrich_students_tests_marks_one_round_trip(self):
		from dataexploration.column_utils import enrich_students_tests_marks
		from unittest.mock import patch, MagicMock

		students = [
			{'ssid': 'A', 'student_id': '1', 'stu_annual_key': 'k1'},
			{'ssid': 'B', 'student_id': '2', 'stu_annual_key': 'k2'},
		]
		test_info = {
			'col_name': 'test_2025_2026_CAASPP_Math_SS',
			'year': '2025-2026', 'test_name': 'CAASPP', 'subject': 'Math',
			'metric': 'ss',
		}
		mark_info = {
			'col_name': 'mark_x', 'year': '2025-2026',
			'subject': 'Math', 'course_name': 'Math 8', 'mark_period': 'Q1',
		}
		test_result = {
			'error': None,
			'rows': [
				{
					'local_student_id': '1', 'ssid': 'A', 'school_year': '2025-2026',
					'test_name': 'CAASPP', 'subject': 'Math', 'test_window': None,
					'ss': '2400', 'pl': '3', 'other_info': None,
				},
			],
		}
		mark_result = {
			'error': None,
			'rows': [
				{
					'stu_annual_key': 'k2', 'school_year': '2025-2026',
					'subject': 'Mathematics', 'course_name': 'Math 8 HP',
					'mark_period': 'Q1', 'mark': 'A',
				},
			],
		}
		exec_mock = MagicMock()
		exec_mock.execute.side_effect = [test_result, mark_result]
		with patch('clickhouse_utils.executor.QueryExecutor', return_value=exec_mock):
			enrich_students_tests_marks(students, [test_info], [mark_info])
		self.assertEqual(exec_mock.execute.call_count, 2)
		self.assertEqual(students[0]['test_2025_2026_CAASPP_Math_SS'], '2400')
		self.assertEqual(students[1]['mark_x'], 'A')
		self.assertEqual(students[1]['test_2025_2026_CAASPP_Math_SS'], '—')

	def test_enrich_other_info_uses_postgres_not_clickhouse(self):
		from dataexploration.column_utils import enrich_students_tests_marks
		from unittest.mock import patch, MagicMock

		students = [{'ssid': '8184048772', 'student_id': '1'}]
		test_info = {
			'col_name': 'test_2025_2026_CAASPP_ELA_OI_WERDEVEELConditionCode_abc123',
			'year': '2025-2026', 'test_name': 'CAASPP', 'subject': 'ELA',
			'metric': 'oi', 'other_info_key': 'WERDEVEELConditionCode',
		}
		pg_rows = [(
			'1', '8184048772', '2025-2026', 'CAASPP', 'ELA', '',
			'2500', '3',
			{'WERDEVEELConditionCode': 'I'},
		)]
		cur = MagicMock()
		cur.fetchall.return_value = pg_rows
		conn = MagicMock()
		conn.cursor.return_value.__enter__.return_value = cur
		exec_mock = MagicMock()
		with patch('clickhouse_utils.executor.QueryExecutor', return_value=exec_mock):
			with patch('dataexploration.column_utils.connections') as connections:
				connections.__getitem__.return_value = conn
				enrich_students_tests_marks(students, [test_info], [])

		exec_mock.execute.assert_not_called()
		self.assertEqual(students[0][test_info['col_name']], 'I')


class SchoolYearUtilsTests(SimpleTestCase):
	@patch("dataexploration.school_year_utils.resolve_school_year")
	def test_resolve_relative_column_key(self, mock_resolve):
		mock_resolve.return_value = "2025-2026"
		res = resolve_relative_column_key("test___sy_this___iready_reading_ss")
		self.assertEqual(res, "test_2025_2026_iready_reading_ss")

	@patch("dataexploration.school_year_utils.resolve_school_year")
	def test_resolve_relative_column_key_no_alias(self, mock_resolve):
		res = resolve_relative_column_key("test_2025_2026_iready_reading_ss")
		self.assertEqual(res, "test_2025_2026_iready_reading_ss")


class JsonListColumnTests(SimpleTestCase):
	def test_json_list_key_for_column(self):
		from dataexploration.view_modules.column_management import (
			json_list_key_for_column,
			get_column_expression,
		)
		self.assertEqual(json_list_key_for_column('race_list'), 'l_race')
		self.assertIsNone(json_list_key_for_column('dsusd_fep'))
		self.assertIsNone(json_list_key_for_column('avid'))

		expr, alias = get_column_expression('dsusd_fep', db_columns=set())
		self.assertEqual(alias, 'dsusd_fep')
		self.assertIn("->>'DSUSD_FEP'", expr)
		self.assertNotIn('array_to_string', expr)


class CsFilterClauseTests(SimpleTestCase):
	def test_dsusd_fep_filter_uses_json_extract_not_physical_column(self):
		from dataexploration.column_utils import _append_cs_filter_clause

		where_clauses = []
		params = []
		_append_cs_filter_clause(where_clauses, params, 'dsusd_fep', ['Y'])

		self.assertEqual(len(where_clauses), 1)
		self.assertIn("->>'DSUSD_FEP'", where_clauses[0])
		self.assertNotIn('cs.dsusd_fep', where_clauses[0])
		self.assertEqual(params, [['Y']])


class ApplyColumnFilterTests(SimpleTestCase):
	def test_skips_context_filters_already_applied_separately(self):
		from dataexploration.view_modules.filter_sql import apply_column_filters

		where_clauses = []
		params = []
		apply_column_filters(
			where_clauses, params,
			{'school_year': ['2026-2027'], 'grade': ['5'], 'status': ['Active'], 'ell': ['Y']},
			{}, {}, set(), {}, False,
		)
		self.assertEqual(len(where_clauses), 1)
		self.assertIn('ell', where_clauses[0])

	def test_mark_column_filter_uses_stu_annual_key_subquery(self):
		from dataexploration.view_modules.filter_sql import apply_column_filters

		where_clauses = []
		params = []
		mark_col_map = {
			'mark_2025_2026_Math_Algebra_Q1': {
				'col_name': 'mark_2025_2026_Math_Algebra_Q1',
				'year': '2025-2026',
				'subject': 'Math',
				'course_name': 'Algebra',
				'mark_period': 'Q1',
			},
		}
		apply_column_filters(
			where_clauses, params,
			{'mark_2025_2026_Math_Algebra_Q1': ['A']},
			{}, mark_col_map, set(), {}, False,
		)
		self.assertEqual(len(where_clauses), 1)
		self.assertIn('student_marks_list', where_clauses[0])
		self.assertIn('stu_annual_key', where_clauses[0])
