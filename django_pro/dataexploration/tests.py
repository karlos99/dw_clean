from pathlib import Path
from django.test import SimpleTestCase
from django.template.loader import get_template


class ScheduleFilterScopeTests(SimpleTestCase):
	def test_district_admin_elementary_school_uses_elementary_schedule_mode(self):
		from unittest.mock import MagicMock, patch
		from dataexploration.utils import (
			SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
			schedule_filter_ui_mode,
		)

		user = MagicMock()
		with patch('dataexploration.utils.is_unrestricted', return_value=True):
			with patch(
				'dataexploration.utils.single_school_state_id_from_column_filters',
				return_value='ELEM123',
			):
				school = MagicMock()
				school.school_group = 'Elementary School'
				with patch(
					'dataexploration.utils._resolve_schedule_filter_school',
					return_value=school,
				):
					mode = schedule_filter_ui_mode(user, {'school_state_id': ['ELEM123']})
		self.assertEqual(mode, SCHEDULE_FILTER_MODE_SCHOOL_ELEM)

	def test_column_filter_school_overrides_active_school_for_schedule_scope(self):
		from dataexploration.view_modules.schedule_filter_sql import _resolve_scope_state_school_id

		class _School:
			state_id = 'ACTIVE_SCHOOL'

		class _User:
			active_school = _School()

		user = _User()
		column_filters = {'school_state_id': ['FILTER_SCHOOL']}
		self.assertEqual(
			_resolve_scope_state_school_id(user, column_filters),
			'FILTER_SCHOOL',
		)


class TemplateCompilationTests(SimpleTestCase):
	def test_folder_browser_template_compiles(self):
		# This template is rendered via HTMX at /dataexploration/api/folders/.
		# A small syntax regression (e.g. missing spaces around '==') causes a hard 500.
		get_template("dataexploration/partials/folder_browser.html")

	def test_student_with_tests_list_template_compiles(self):
		get_template("dataexploration/student_with_tests_list.html")

	def test_student_test_summary_content_compiles(self):
		get_template("dataexploration/partials/student_test_summary_content.html")

	def test_dataexploration_manage_default_columns_compiles(self):
		get_template("dataexploration/manage_default_columns.html")

	def test_users_manage_default_columns_compiles(self):
		get_template("users/manage_default_columns.html")

	def test_nextyearlist_column_structures_compiles(self):
		get_template("nextyearlist/partials/column_structures.html")

	def test_users_column_structures_compiles(self):
		get_template("users/partials/column_structures.html")

	def test_split_schedule_column_filters(self):
		from dataexploration.view_modules.column_management import split_schedule_column_filters

		base, sched = split_schedule_column_filters({
			'school_year': '2024-2025',
			'grade': '10',
			'schedule_period': ['1', '2'],
			'schedule_subject': 'Math',
		})
		self.assertEqual(base, {'school_year': '2024-2025', 'grade': '10'})
		self.assertEqual(sched['schedule_period'], ['1', '2'])
		self.assertEqual(sched['schedule_subject'], ['Math'])

	def test_dataexploration_column_structures_strips_django_comments(self):
		from django.template.loader import render_to_string

		html = render_to_string(
			"dataexploration/partials/column_structures.html",
			{
				"test_structure": {},
				"marks_structure": {"2024": {"Math": {"Algebra I": {}}}},
				"survey_structure": {},
				"ap_structure": {},
				"sat_structure": {},
				"column_definitions": {},
				"year_labels": {"2024": "2024"},
				"year_to_alias": {},
			},
		)
		self.assertNotIn("{#", html)

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


class StudentMarksGridTests(SimpleTestCase):
	def test_pivot_groups_class_rows_and_period_columns(self):
		from dataexploration.view_modules.student_data_utils import pivot_student_marks

		marks = [
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Q2', 'mark': 'B+', 'staff_name': 'Smith'},
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Q1', 'mark': 'B', 'staff_name': 'Smith'},
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Live', 'mark': 'A-', 'staff_name': 'Smith'},
			{'school_year': '2025-2026', 'subject': 'English', 'course_name': 'ELA 8',
			 'mark_period': 'Q1', 'mark': 'A', 'staff_name': 'Jones'},
			{'school_year': '2024-2025', 'subject': 'Math', 'course_name': 'Math 7',
			 'mark_period': 'Q4', 'mark': 'C', 'staff_name': 'Lee'},
		]
		grid = pivot_student_marks(marks)
		self.assertEqual([y['school_year'] for y in grid], ['2025-2026', '2024-2025'])
		self.assertEqual(grid[0]['periods'], ['Q1', 'Q2', 'Live'])
		self.assertEqual(len(grid[0]['courses']), 2)
		alg = next(c for c in grid[0]['courses'] if c['course_name'] == 'Algebra I')
		self.assertEqual(alg['subject'], 'Math')
		self.assertEqual(alg['staff_name'], 'Smith')
		self.assertEqual([c['mark'] for c in alg['cells']], ['B', 'B+', 'A-'])
		self.assertEqual(alg['cells'][2]['tone'], 'green')

	def test_pivot_empty(self):
		from dataexploration.view_modules.student_data_utils import pivot_student_marks
		self.assertEqual(pivot_student_marks([]), [])


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

	def test_stale_ssid_falls_back_to_local_id(self):
		from dataexploration.column_utils import assign_result_value_maps, lookup_result_value

		# A result whose SSID was corrected keeps the OLD ssid but the local id
		# still identifies the current student, so it must not be orphaned.
		rows = [('20250161', '3122440885', '1')]
		valid_ssids = {'8442970229'}
		by_id, by_ssid = assign_result_value_maps(rows, valid_ssids=valid_ssids)
		self.assertEqual(by_id['20250161'], '1')
		self.assertNotIn('3122440885', by_ssid)
		student = {'student_id': '20250161', 'ssid': '8442970229'}
		self.assertEqual(lookup_result_value(student, by_id, by_ssid), '1')

	def test_valid_ssid_still_wins_over_local_id(self):
		from dataexploration.column_utils import assign_result_value_maps, lookup_result_value

		rows = [('20250161', '3122440885', '1')]
		valid_ssids = {'3122440885', '8442970229'}
		by_id, by_ssid = assign_result_value_maps(rows, valid_ssids=valid_ssids)
		self.assertIn('3122440885', by_ssid)
		self.assertNotIn('20250161', by_id)  # valid ssid must not leak via local id
		self.assertEqual(lookup_result_value({'student_id': '20250161', 'ssid': '8442970229'}, by_id, by_ssid), '—')
		self.assertEqual(lookup_result_value({'ssid': '3122440885'}, by_id, by_ssid), '1')

	def test_nan_ssid_treated_as_blank(self):
		from dataexploration.column_utils import assign_result_value_maps

		rows = [('20250161', 'NaN', '3')]
		by_id, by_ssid = assign_result_value_maps(rows, valid_ssids={'8442970229'})
		self.assertEqual(by_id['20250161'], '3')
		self.assertNotIn('NaN', by_ssid)

	def test_export_csv_cell_uses_empty_blank(self):
		from dataexploration.column_utils import export_csv_cell
		self.assertEqual(export_csv_cell(None), '')
		self.assertEqual(export_csv_cell('NaN'), '')
		self.assertEqual(export_csv_cell('3'), '3')

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
		live_marks = [{
			'ssid': 'B', 'student_id': '2', 'school_year': '2025-2026',
			'subject': 'Mathematics', 'course_name': 'Math 8 HP',
			'mark_period': 'Q1', 'mark': 'A', 'mark_status': 'Final', 'mark_ord': 1,
		}]
		exec_mock = MagicMock()
		exec_mock.execute.return_value = test_result
		with patch('clickhouse_utils.executor.QueryExecutor', return_value=exec_mock):
			with patch('dataexploration.column_utils._fetch_marks_live_from_alt', return_value=live_marks):
				enrich_students_tests_marks(students, [test_info], [mark_info])
		self.assertEqual(exec_mock.execute.call_count, 1)
		self.assertEqual(students[0]['test_2025_2026_CAASPP_Math_SS'], '2400')
		self.assertEqual(students[1]['mark_x'], 'A')
		self.assertEqual(students[1]['test_2025_2026_CAASPP_Math_SS'], '—')

	def test_enrich_marks_falls_back_to_student_id_on_stale_ssid(self):
		from dataexploration.column_utils import enrich_students_tests_marks
		from unittest.mock import patch, MagicMock

		students = [
			{'ssid': '8442970229', 'student_id': '20250161', 'stu_annual_key': 'k1'},
		]
		mark_info = {
			'col_name': 'mark_x', 'year': '2024-2025',
			'subject': 'Math', 'course_name': 'Math 6', 'mark_period': 'Q2',
		}
		# The mark row carries a stale SSID but a correct local student id.
		live_marks = [{
			'ssid': '3122440885', 'student_id': '20250161',
			'school_year': '2024-2025', 'subject': 'Math',
			'course_name': 'Math 6', 'mark_period': 'Q2', 'mark': 'B-',
			'mark_status': 'Final', 'mark_ord': 1,
		}]
		with patch('dataexploration.column_utils._fetch_marks_live_from_alt', return_value=live_marks):
			enrich_students_tests_marks(students, [], [mark_info])
		self.assertEqual(students[0]['mark_x'], 'B-')

	def test_choose_final_mark_over_na_duplicate(self):
		from dataexploration.column_utils import enrich_students_tests_marks
		from unittest.mock import patch

		students = [{'ssid': '5070477262', 'student_id': '20152793'}]
		mark_info = {
			'col_name': 'mark_x', 'year': '2024-2025',
			'subject': 'English', 'course_name': 'Language Arts 7', 'mark_period': 'Q1',
		}
		live_marks = [
			{
				'ssid': '5070477262', 'student_id': '20152793', 'school_year': '2024-2025',
				'subject': 'English', 'course_name': 'Language Arts 7',
				'mark_period': 'Q1', 'mark': 'C-', 'mark_status': 'Current', 'mark_ord': 2,
			},
			{
				'ssid': '5070477262', 'student_id': '20152793', 'school_year': '2024-2025',
				'subject': 'English', 'course_name': 'Language Arts 7',
				'mark_period': 'Q1', 'mark': 'A', 'mark_status': 'Final', 'mark_ord': 1,
			},
		]
		with patch('dataexploration.column_utils._fetch_marks_live_from_alt', return_value=live_marks):
			enrich_students_tests_marks(students, [], [mark_info])
		self.assertEqual(students[0]['mark_x'], 'A')

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


class PictureColumnTests(SimpleTestCase):
	def test_picture_is_first_student_info_column(self):
		from dataexploration.constants import COLUMN_CATEGORIES, COLUMN_DEFINITIONS, VIRTUAL_COLUMNS
		self.assertEqual(COLUMN_CATEGORIES['student_info']['columns'][0], 'picture')
		self.assertEqual(COLUMN_DEFINITIONS['picture'], 'Picture')
		self.assertIn('picture', VIRTUAL_COLUMNS)

	def test_picture_has_no_sql_expression(self):
		from dataexploration.view_modules.column_management import get_column_expression
		expr, alias = get_column_expression('picture', db_columns={'ssid', 'picture'})
		self.assertIsNone(expr)
		self.assertIsNone(alias)

	def test_picture_filter_is_noop(self):
		from dataexploration.column_utils import _append_cs_filter_clause
		where_clauses = []
		params = []
		_append_cs_filter_clause(where_clauses, params, 'picture', ['x'])
		self.assertEqual(where_clauses, [])
		self.assertEqual(params, [])


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


class ClickHouseExecutorTests(SimpleTestCase):
	def test_l_race_sql_routes_to_postgres(self):
		from clickhouse_utils.executor import _sql_uses_pg_add_data_key
		from dataexploration.view_modules.column_management import race_list_overlap_sql

		sql = f"SELECT 1 FROM current_student cs WHERE {race_list_overlap_sql('cs')}"
		self.assertTrue(_sql_uses_pg_add_data_key(sql))


class ApplyColumnFilterTests(SimpleTestCase):
	def test_grade_column_filter_applies_to_cs_grade(self):
		from dataexploration.view_modules.filter_sql import apply_column_filters

		where_clauses = []
		params = []
		apply_column_filters(
			where_clauses, params,
			{'grade': ['03']},
			{}, {}, {'grade'}, {}, False,
		)
		self.assertEqual(len(where_clauses), 1)
		self.assertIn('cs.grade', where_clauses[0])
		self.assertEqual(params, [['03']])

	def test_race_list_filter_uses_l_race_json_overlap(self):
		from dataexploration.view_modules.filter_sql import apply_column_filters

		where_clauses = []
		params = []
		apply_column_filters(
			where_clauses, params,
			{'race_list': ['White', 'Asian']},
			{}, {}, set(), {}, False,
		)
		self.assertEqual(len(where_clauses), 1)
		self.assertIn('l_race', where_clauses[0])
		self.assertIn('&&', where_clauses[0])
		self.assertEqual(params, [['White', 'Asian']])

	def test_mark_column_filter_uses_ssid_subquery(self):
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
		self.assertIn('cs.ssid IN', where_clauses[0])
		self.assertIn('sm.ssid', where_clauses[0])


class SATColumnKeyTests(SimpleTestCase):
	def test_sat_column_key_round_trip(self):
		from dataexploration.column_utils import (
			sat_column_key, parse_sat_column_key, sat_column_db_field,
		)
		score_key = sat_column_key('2025-2026', 'score', field='TOTAL')
		self.assertEqual(score_key, 'sat_2025_2026_TOTAL')
		self.assertEqual(
			parse_sat_column_key(score_key),
			{'year': '2025-2026', 'kind': 'score', 'field': 'TOTAL'},
		)
		self.assertEqual(sat_column_db_field(score_key), 'total_score')

		pct_key = sat_column_key('2025-2026', 'percentile', region='natrep', metric='rw')
		self.assertEqual(pct_key, 'sat_2025_2026_PCT_NATREP_RW')
		parsed = parse_sat_column_key(pct_key)
		self.assertEqual(parsed['kind'], 'percentile')
		self.assertEqual(parsed['region'], 'natrep')
		self.assertEqual(parsed['metric'], 'rw')
		self.assertEqual(sat_column_db_field(pct_key), 'percentile_natrep_rw')

	def test_parse_sat_column_key_rejects_other_keys(self):
		from dataexploration.column_utils import parse_sat_column_key
		self.assertIsNone(parse_sat_column_key('ap_2025_2026_Math_PL'))
		self.assertIsNone(parse_sat_column_key('sat_bogus'))
		self.assertIsNone(parse_sat_column_key('sat_2025_2026_PCT_BOGUS_RW'))


class SurveyDataResolutionTests(SimpleTestCase):
	def test_get_survey_data_for_students_cross_year_and_ssid(self):
		from dataexploration.survey_utils import get_survey_data_for_students, _generate_survey_column_key

		col_key_24 = _generate_survey_column_key('Skills and Competencies', '2024-2025', 'SPRING', 0, 'Grades 6-8')
		col_key_25 = _generate_survey_column_key('Skills and Competencies', '2025-2026', 'FALL', 0, 'Grades 6-8')

		fake_rows = [
			{
				'student_id': '1001',
				'ssid': '9000000001',
				'survey_type': 'Skills and Competencies',
				'school_year': '2024-2025',
				'testing_window': 'SPRING',
				'grade_range': 'Grades 6-8',
				'question_order': 0,
				'value': '4',
			},
			{
				'student_id': '1001',
				'ssid': '9000000001',
				'survey_type': 'Skills and Competencies',
				'school_year': '2025-2026',
				'testing_window': 'FALL',
				'grade_range': 'Grades 6-8',
				'question_order': 0,
				'value': '5',
			},
			{
				'student_id': '1002',
				'ssid': '9000000002',
				'survey_type': 'Skills and Competencies',
				'school_year': '2024-2025',
				'testing_window': 'SPRING',
				'grade_range': 'Grades 6-8',
				'question_order': 0,
				'value': '3',
			},
		]

		with patch('dataviz.utils.data_loader._analytics_rows', return_value=fake_rows):
			data = get_survey_data_for_students(student_ids=['1001'], ssids=['9000000002'])

		# Student 1001 has responses across both 2024-2025 and 2025-2026
		self.assertIn('1001', data)
		self.assertEqual(data['1001'][col_key_24], '4')
		self.assertEqual(data['1001'][col_key_25], '5')

		# SSID 9000000001 also resolves
		self.assertIn('9000000001', data)
		self.assertEqual(data['9000000001'][col_key_24], '4')

		# Student 1002 resolves via SSID
		self.assertIn('9000000002', data)
		self.assertEqual(data['9000000002'][col_key_24], '3')

	def test_get_mark_columns_orders_live_period_at_end(self):
		from dataexploration.column_utils import get_mark_columns
		from unittest.mock import patch, MagicMock

		mock_rows = [
			('2026-2027', 'English', 'English 10', 'P1'),
			('2026-2027', 'English', 'English 10', 'Live'),
			('2026-2027', 'English', 'English 10', 'Q1'),
			('2026-2027', 'English', 'English 10', 'P2'),
		]
		mock_cursor = MagicMock()
		mock_cursor.fetchall.return_value = mock_rows
		mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
		mock_cursor.__exit__ = MagicMock(return_value=False)

		# Force the SQL path (the metadata fast path is exercised separately);
		# this test verifies period ordering from cursor rows.
		with patch('dataexploration.column_utils._build_mark_columns_from_metadata', return_value=None):
			with patch('clickhouse_utils.executor.QueryExecutor.cursor', return_value=mock_cursor):
				with patch('django.core.cache.cache.get', return_value=None):
					with patch('django.core.cache.cache.set'):
						cols = get_mark_columns()

		periods = list(cols['2026-2027']['English']['English 10'].keys())
		self.assertEqual(periods, ['P1', 'P2', 'Q1', 'Live'])
		self.assertEqual(periods[-1], 'Live')

	def test_format_marks_cell_bolds_live(self):
		from dataexploration.templatetags.dataexploration_tags import format_marks_cell

		formatted = format_marks_cell('P1: A, Live: A+', 'markall_2026_2027_Math_1_MARKS')
		self.assertIn('<span class="font-bold text-slate-900">Live</span>:', formatted)

		formatted_single = format_marks_cell('Live', 'mark_2026_2027_Math_Algebra_Live')
		self.assertIn('<span class="font-bold text-slate-900">Live</span>', formatted_single)

		# Non-mark columns are untouched
		self.assertEqual(format_marks_cell('Active', 'status'), 'Active')




class StudentCteDetailsTests(SimpleTestCase):
	def test_student_year_label_normalizes(self):
		from dataexploration.view_modules.student_data_utils import _student_year_label

		self.assertEqual(_student_year_label('2026-2027'), '2026')
		self.assertEqual(_student_year_label('2026'), '2026')
		self.assertEqual(_student_year_label(''), '')

	def test_cte_history_columns_orders_stages_and_credits(self):
		from dataexploration.view_modules.student_data_utils import _cte_history_columns

		columns = _cte_history_columns([
			{'cte_level': 'Capstone', 'course': 'Cap', 'eff_s1': '', 'eff_s2': '', 'credits_earned': 0, 'completion_status': 'In Process'},
			{'cte_level': 'Introduction', 'course': 'Intro', 'eff_s1': 'C-', 'eff_s2': 'A-', 'credits_earned': 15, 'completion_status': 'Completed'},
			{'cte_level': 'Concentration', 'course': 'Conc', 'eff_s1': 'B-', 'eff_s2': 'C+', 'credits_earned': 10, 'completion_status': 'Completed'},
		])
		self.assertEqual([c['level'] for c in columns], ['Introduction', 'Concentration', 'Capstone'])
		self.assertEqual([c['year'] for c in columns], [1, 2, 3])
		self.assertEqual(columns[0]['courses'][0]['s1'], 'C-')
		self.assertEqual(columns[0]['courses'][0]['credits'], 15.0)

	def test_cte_requirements_derive_status_from_threshold(self):
		from dataexploration.view_modules.student_data_utils import _cte_requirements

		reqs = _cte_requirements({
			'concentrator': {'status': 'Met', 'total_credits_earned': 10, 'justification': 'ok'},
			'capstone': {'credits_threshold_met': False, 'justification': 'No capstone course found.'},
		})
		self.assertEqual([r['label'] for r in reqs], ['Concentrator', 'Capstone'])
		self.assertEqual(reqs[0]['status'], 'Met')
		self.assertEqual(reqs[1]['status'], 'Not Met')
		self.assertEqual(_cte_requirements(None), [])

	def test_fetch_student_cte_details_current_and_past_completers(self):
		from unittest.mock import MagicMock, patch

		import dataexploration.view_modules.student_data_utils as mod

		class _Row:
			def __init__(self, **kw):
				self.__dict__.update(kw)

		rows = [
			_Row(
				school_year='2025', cte_pathway='Professional Theater', pathway_status='CTE Completer',
				industry_sector='Arts', pathway_code='', pathway_code_name='',
				course_history_json=[{'cte_level': 'Concentration', 'course': 'Professional Theater I', 'eff_s1': 'A', 'eff_s2': 'B', 'credits_earned': 10, 'completion_status': 'Completed'}],
				completer_requirements_json={'concentrator': {'status': 'Met'}},
			),
			_Row(
				school_year='2026', cte_pathway='Professional Theater', pathway_status='In Progress',
				industry_sector='Arts', pathway_code='', pathway_code_name='',
				course_history_json=[{'cte_level': 'Capstone', 'course': 'Professional Theater II', 'credits_earned': 0, 'completion_status': 'In Process'}],
				completer_requirements_json={'capstone': {'credits_threshold_met': False, 'justification': 'No capstone course found.'}},
			),
		]

		detail_qs = MagicMock()
		detail_qs.order_by.return_value = rows

		with patch.object(mod, '_resolve_ssid', return_value='123'), \
			patch.object(mod, 'CurrentStudent') as cs, \
			patch('coursecatalog.models.CteDetailEntry') as cte_model:
			cs.objects.filter.return_value.order_by.return_value.first.return_value = None
			cte_model.objects.filter.return_value = detail_qs
			out = mod._fetch_student_cte_details('123', current_school_year='2026-2027')

		self.assertTrue(out['has_any'])
		self.assertEqual(len(out['current']), 1)
		self.assertEqual(out['current'][0]['school_year'], '2026')
		self.assertFalse(out['current'][0]['is_completer'])
		self.assertEqual([c['level'] for c in out['current'][0]['columns']], ['Capstone'])
		self.assertEqual(len(out['past_completers']), 1)
		self.assertEqual(out['past_completers'][0]['school_year'], '2025')
		self.assertEqual(out['past_completers'][0]['pathway_status'], 'CTE Completer')

	def test_fetch_student_cte_details_empty_when_no_rows(self):
		from unittest.mock import MagicMock, patch

		import dataexploration.view_modules.student_data_utils as mod

		detail_qs = MagicMock()
		detail_qs.order_by.return_value = []

		with patch.object(mod, '_resolve_ssid', return_value='123'), \
			patch.object(mod, 'CurrentStudent') as cs, \
			patch('coursecatalog.models.CteDetailEntry') as cte_model:
			cs.objects.filter.return_value.order_by.return_value.first.return_value = None
			cte_model.objects.filter.return_value = detail_qs
			out = mod._fetch_student_cte_details('123')

		self.assertEqual(out, {'current': [], 'past_completers': [], 'has_any': False})
