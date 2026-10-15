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

	def test_student_details_compiles(self):
		get_template("dataexploration/partials/student_details.html")

	def test_student_orbit_header_compiles(self):
		get_template("dataexploration/partials/student_orbit_header.html")

	def test_student_orbit_header_respects_summary_sections_disabled(self):
		from django.template.loader import render_to_string
		context = {
			'ssid': '12345',
			'student_info': {
				'ssid': '12345',
				'student_id': '1001',
				'full_name': 'Test Student',
				'ell_status': 'EO',
				'school': 'Test High School',
				'school_short_name': 'Test HS',
				'grade': '10',
				'school_year': '2024-2025',
			},
			'student_details': {
				'has_any': True,
				'summary': {'weighted_gpa': '3.8'},
				'requirements': {
					'diploma': [{'label': 'Standard Diploma', 'rows': [], 'met_count': 10, 'row_count': 10}],
					'ag': [{'label': 'UC/CSU', 'rows': [], 'met_count': 6, 'row_count': 7}],
				},
			},
			'student_cte_details': {
				'has_any': True,
				'current': [{'cte_pathway': 'Health', 'pathway_status': 'In Progress'}],
			},
			'summary_sections': {
				'diploma': False,
				'ag': False,
				'cte': False,
				'recognition': False,
				'flags': False,
			},
		}
		html = render_to_string("dataexploration/partials/student_orbit_header.html", context)
		self.assertNotIn("node === 'diploma'", html)
		self.assertNotIn("node === 'ag'", html)
		self.assertNotIn("node === 'cte'", html)
		self.assertNotIn("UC/CSU A-G", html)
		self.assertNotIn("Diploma Requirements", html)
		self.assertNotIn("CTE Pathways", html)

	def test_student_orbit_header_renders_when_summary_sections_enabled(self):
		from django.template.loader import render_to_string
		context = {
			'ssid': '12345',
			'student_info': {
				'ssid': '12345',
				'student_id': '1001',
				'full_name': 'Test Student',
				'ell_status': 'EO',
				'school': 'Test High School',
				'school_short_name': 'Test HS',
				'grade': '10',
				'school_year': '2024-2025',
			},
			'student_details': {
				'has_any': True,
				'summary': {'weighted_gpa': '3.8'},
				'requirements': {
					'diploma': [{'label': 'Standard Diploma', 'rows': [], 'met_count': 10, 'row_count': 10}],
					'ag': [{'label': 'UC/CSU', 'rows': [], 'met_count': 6, 'row_count': 7}],
				},
			},
			'student_cte_details': {
				'has_any': True,
				'current': [{'cte_pathway': 'Health', 'pathway_status': 'In Progress'}],
			},
			'summary_sections': {
				'diploma': True,
				'ag': True,
				'cte': True,
				'recognition': True,
				'flags': True,
			},
		}
		html = render_to_string("dataexploration/partials/student_orbit_header.html", context)
		self.assertIn("node === 'diploma'", html)
		self.assertIn("node === 'ag'", html)
		self.assertIn("node === 'cte'", html)
		self.assertIn("UC/CSU A-G", html)
		self.assertIn("Diploma Requirements", html)
		self.assertIn("CTE Pathways", html)

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
		# The live (in-progress) grade is hidden; only marking periods show.
		self.assertEqual(grid[0]['periods'], ['Q1', 'Q2'])
		self.assertEqual(len(grid[0]['courses']), 2)
		alg = next(c for c in grid[0]['courses'] if c['course_name'] == 'Algebra I')
		self.assertEqual(alg['subject'], 'Math')
		self.assertEqual(alg['staff_name'], 'Smith')
		self.assertEqual([c['mark'] for c in alg['cells']], ['B', 'B+'])
		self.assertEqual(alg['cells'][1]['tone'], 'blue')

	def test_pivot_hides_live_and_progress_periods(self):
		from dataexploration.view_modules.student_data_utils import pivot_student_marks

		marks = [
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Live', 'mark': 'A-', 'staff_name': 'Smith'},
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Progress', 'mark': 'B', 'staff_name': 'Smith'},
			{'school_year': '2025-2026', 'subject': 'Math', 'course_name': 'Algebra I',
			 'mark_period': 'Semester 1', 'mark': 'B+', 'staff_name': 'Smith'},
		]
		grid = pivot_student_marks(marks)
		self.assertEqual(grid[0]['periods'], ['Semester 1'])
		alg = grid[0]['courses'][0]
		self.assertEqual([c['mark'] for c in alg['cells']], ['B+'])

	def test_pivot_empty(self):
		from dataexploration.view_modules.student_data_utils import pivot_student_marks
		self.assertEqual(pivot_student_marks([]), [])

	def test_filter_display_marks_drops_live_and_progress(self):
		from dataexploration.view_modules.student_data_utils import filter_display_marks

		marks = [
			{'mark_period': 'Live', 'mark': 'A'},
			{'mark_period': 'Progress', 'mark': 'B'},
			{'mark_period': 'Semester 1', 'mark': 'B+'},
			{'mark_period': 'Semester 2', 'mark': 'A-'},
		]
		out = filter_display_marks(marks)
		self.assertEqual([m['mark_period'] for m in out], ['Semester 1', 'Semester 2'])


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


class StudentDetailsTests(SimpleTestCase):
	def _row(self):
		from types import SimpleNamespace

		return SimpleNamespace(
			ssid='123',
			student_name='Alvarado, Destiny Diane',
			grade='11',
			school_name='Shadow Hills HS',
			school_year='2026-2027',
			counselor_name='Smith',
			weighted_gpa='3.413800000',
			unweighted_gpa='3.275900000',
			class_rank='140',
			class_size='418',
			expected_graduation_year='2028',
			graduation_status='Class of 2028',
			course_history=[
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
				 'course_title': 'Math I CP', 'course_id': '2213SIA', 'display_mark': 'B-',
				 'display_completed': 5.0, 'term_gpa': 3.571428},
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM2',
				 'course_title': 'Math I CP', 'course_id': '2213SIA', 'display_mark': 'B',
				 'display_completed': 5.0, 'term_gpa': 3.6},
				{'school_year_range': '2025-2026', 'school_year': 2025, 'term_code': 'SEM1',
				 'course_title': 'English II', 'course_id': '1042', 'display_mark': 'A',
				 'display_completed': 5.0, 'term_gpa': 4.0},
			],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 101, 'subject_area': 'Economics', 'req_credits': 5.0,
				 'comp_credits': 0.0, 'wip_credits': 0.0, 'needed_credits': 5.0},
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 99, 'subject_area': 'TOTALS', 'req_credits': 220.0,
				 'comp_credits': 120.0, 'wip_credits': 0.0, 'needed_credits': 100.0},
				{'requirement_set': 'District Graduation', 'grad_plan': 'Alt Edu High School Diploma.',
				 'sort_order': 101, 'subject_area': 'Economics', 'req_credits': 5.0,
				 'comp_credits': 5.0, 'wip_credits': 0.0, 'needed_credits': 0.0},
				{'requirement_set': 'District Graduation', 'grad_plan': 'Alt Edu High School Diploma.',
				 'sort_order': 99, 'subject_area': 'TOTALS', 'req_credits': 130.0,
				 'comp_credits': 120.0, 'wip_credits': 0.0, 'needed_credits': 10.0},
			],
			uc_requirements=[],
			csu_requirements=[],
		)

	def test_build_student_details_summary_and_course_history(self):
		from dataexploration.view_modules.student_data_utils import _build_student_details

		out = _build_student_details(self._row())

		self.assertTrue(out['has_any'])
		self.assertEqual(out['summary']['weighted_gpa'], '3.41')
		self.assertEqual(out['summary']['unweighted_gpa'], '3.28')
		self.assertEqual(out['summary']['rank_display'], '140 of 418')
		self.assertEqual(out['summary']['expected_graduation_year'], '2028')

		years = [y['school_year'] for y in out['course_history']]
		self.assertEqual(years, ['2025-2026', '2024-2025'])
		y2024 = out['course_history'][1]
		self.assertEqual(y2024['course_count'], 2)
		self.assertEqual(y2024['credits_earned'], 10.0)
		self.assertEqual([t['term'] for t in y2024['terms']], ['Semester 1', 'Semester 2'])

		plans = out['requirements']['diploma']
		self.assertEqual(
			[p['label'] for p in plans],
			['DS High School', 'Alt Edu High School Diploma'],
		)
		self.assertEqual(out['default_plan'], 'ds-high-school')
		ds = plans[0]
		self.assertEqual(ds['display_label'], 'DS High School Diploma')
		self.assertEqual(ds['requirement_set'], 'District Graduation')
		self.assertEqual(ds['pathway_credits'], 220)
		self.assertEqual(len(ds['rows']), 7)
		self.assertEqual(ds['totals']['req_credits'], 220.0)
		self.assertFalse(ds['rows'][0]['met'])
		alt = plans[1]
		self.assertEqual(alt['display_label'], 'Alternative Education Diploma')
		self.assertEqual(alt['pathway_credits'], 200)
		self.assertEqual(alt['totals']['req_credits'], 200.0)
		# Economics completed credits roll up into Social Studies.
		alt_social = next(r for r in alt['rows'] if r['subject_area'] == 'Social Studies')
		self.assertEqual(alt_social['comp_credits'], 5.0)
		self.assertEqual([t['key'] for t in out['requirement_tabs']], ['diploma', 'ag'])
		self.assertEqual(out['requirement_tabs'][1]['label'], 'UC/CSU A-G')

	def test_academic_recognition_and_high_school(self):
		from dataexploration.view_modules.student_data_utils import (
			_academic_recognition, _is_high_school_grade,
		)

		self.assertEqual(_academic_recognition('4.00'), 'Summa Cum Laude')
		self.assertEqual(_academic_recognition('4.25'), 'Summa Cum Laude')
		self.assertEqual(_academic_recognition('3.99'), 'Magna Cum Laude')
		self.assertEqual(_academic_recognition('3.75'), 'Magna Cum Laude')
		self.assertEqual(_academic_recognition('3.74'), 'Cum Laude')
		self.assertEqual(_academic_recognition('3.50'), 'Cum Laude')
		self.assertEqual(_academic_recognition('3.49'), '')
		self.assertEqual(_academic_recognition(None), '')

		for grade in ('9', '09', '10', '11', '12', 'Grade 11'):
			self.assertTrue(_is_high_school_grade(grade), msg=grade)
		for grade in ('8', '6', 'K', '', None):
			self.assertFalse(_is_high_school_grade(grade), msg=str(grade))

	def test_summary_includes_academic_recognition_for_high_school(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		def make_row(grade, gpa):
			return SimpleNamespace(
				ssid='1', student_name='X', grade=grade, school_name='S', school_year='2025-2026',
				counselor_name='', weighted_gpa=None, unweighted_gpa=gpa, class_rank=None,
				class_size=None, expected_graduation_year='', graduation_status='',
				course_history=[], graduation_requirements=[], uc_requirements=[],
				csu_requirements=[], current_schedule=None,
			)

		hs = _build_student_details(make_row('11', '3.8'))['summary']
		self.assertEqual(hs['academic_recognition'], 'Magna Cum Laude')
		self.assertTrue(hs['is_high_school'])
		summa = _build_student_details(make_row('12', '4.0'))['summary']
		self.assertEqual(summa['academic_recognition'], 'Summa Cum Laude')
		# Middle school is not eligible for the recognition.
		ms = _build_student_details(make_row('8', '4.0'))['summary']
		self.assertEqual(ms['academic_recognition'], '')
		self.assertFalse(ms['is_high_school'])

	def test_ag_on_track_benchmarks(self):
		from dataexploration.view_modules.student_data_utils import (
			_ag_on_track, _CSU_AG_PATTERN,
		)

		def plan(comp):
			rows = [
				{'subject_area': label, 'comp_credits': comp.get(letter, 0.0)}
				for letter, label, _years in _CSU_AG_PATTERN
			]
			return [{'rows': rows}]

		# Grade 9: English + Math (B/C) plus two extra A-G units = 4 units.
		ot = _ag_on_track(plan({'B': 10.0, 'C': 10.0, 'D': 10.0, 'E': 10.0}), 9)
		self.assertEqual(ot['units_expected'], 4)
		self.assertEqual(ot['units_earned'], 4.0)
		self.assertTrue(ot['on_track'])
		self.assertEqual(ot['lacking'], [])

		# Grade 10: only English done -> behind in B (1 yr) and C (2 yrs).
		ot = _ag_on_track(plan({'B': 10.0}), 10)
		self.assertEqual(ot['units_expected'], 7)
		self.assertFalse(ot['on_track'])
		self.assertEqual(
			{a['letter']: a['shortfall_years'] for a in ot['lacking']},
			{'B': 1.0, 'C': 2.0},
		)

		# Grade 11: all required areas met but only 9 of 11 units -> behind on units.
		ot = _ag_on_track(
			plan({'A': 10.0, 'B': 30.0, 'C': 30.0, 'D': 10.0, 'E': 10.0}), 11
		)
		self.assertEqual(ot['units_earned'], 9.0)
		self.assertEqual(ot['units_shortfall'], 2.0)
		self.assertEqual(ot['lacking'], [])
		self.assertFalse(ot['on_track'])
		self.assertEqual(ot['grade'], 11)

	def test_on_track_only_for_grades_9_to_11(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		def make_row(grade):
			return SimpleNamespace(
				ssid='1', student_name='X', grade=grade, school_name='S', school_year='2025-2026',
				counselor_name='', weighted_gpa=None, unweighted_gpa='3.0', class_rank=None,
				class_size=None, expected_graduation_year='', graduation_status='',
				course_history=[], graduation_requirements=[], uc_requirements=[],
				csu_requirements=[], current_schedule=None,
			)

		self.assertIsNotNone(_build_student_details(make_row('9'))['on_track'])
		self.assertIsNotNone(_build_student_details(make_row('10'))['on_track'])
		self.assertIsNotNone(_build_student_details(make_row('11'))['on_track'])
		self.assertIsNone(_build_student_details(make_row('12'))['on_track'])
		self.assertIsNone(_build_student_details(make_row('8'))['on_track'])

	def test_ag_on_track_includes_current_schedule(self):
		from dataexploration.view_modules.student_data_utils import (
			_ag_on_track, _CSU_AG_PATTERN,
		)

		def plan(by_letter):
			rows = []
			for letter, label, _years in _CSU_AG_PATTERN:
				data = by_letter.get(letter, {})
				comp = data.get('comp', 0.0)
				rows.append({
					'subject_area': label,
					'comp_credits': comp,
					'projected_total': data.get('projected', comp),
					'scheduled_current': data.get('scheduled', 0.0),
				})
			return [{'rows': rows}]

		# Grade 10: English completed (1 yr); Math is 0 earned but 2 yrs scheduled.
		ot = _ag_on_track(plan({
			'B': {'comp': 10.0},
			'C': {'comp': 0.0, 'projected': 20.0, 'scheduled': 20.0},
		}), 10)
		self.assertEqual(ot['units_earned'], 1.0)
		self.assertEqual(ot['units_projected'], 3.0)
		math = next(a for a in ot['areas'] if a['letter'] == 'C')
		self.assertEqual(math['earned_years'], 0.0)
		self.assertEqual(math['projected_years'], 2.0)
		self.assertEqual(math['status'], 'on_track')  # projected meets the grade-10 benchmark

	def test_diploma_on_track_uses_expected_per_year(self):
		from dataexploration.view_modules.student_data_utils import (
			_diploma_on_track, default_diploma_expected_credits,
		)

		# Defaults reflect when subjects start (Social Studies starts grade 10,
		# Math must start grade 9) and use whole 5-credit units.
		defaults = default_diploma_expected_credits()
		self.assertEqual(defaults['English'], [10, 10, 10, 10])
		self.assertEqual(defaults['Math'], [10, 10, 10, 0])
		self.assertEqual(defaults['Social Studies'], [0, 10, 10, 10])
		self.assertEqual(defaults['Electives'], [20, 10, 15, 25])
		self.assertTrue(all(v % 5 == 0 for years in defaults.values() for v in years))

		plan = [{
			'display_label': 'DS High School Diploma',
			'pathway_credits': 220,
			'rows': [
				{'subject_area': 'English', 'req_credits': 40.0, 'projected_total': 30.0,
				 'counted_credits': 30.0},
				{'subject_area': 'Math', 'req_credits': 30.0, 'projected_total': 10.0,
				 'counted_credits': 10.0},
			],
		}]
		ot = _diploma_on_track(plan, 11)  # by end of grade 11
		# English expected 30 (10+10+10); Math expected 30 (10+10+10).
		self.assertEqual(ot['credits_expected'], 60)
		self.assertEqual(ot['credits_earned'], 40)
		self.assertEqual(ot['credits_projected'], 40)
		self.assertEqual(ot['credits_shortfall'], 20)
		eng = next(a for a in ot['areas'] if a['label'] == 'English')
		self.assertEqual(eng['expected_credits'], 30)
		self.assertEqual(eng['status'], 'on_track')
		math = next(a for a in ot['areas'] if a['label'] == 'Math')
		self.assertEqual(math['expected_credits'], 30)
		self.assertEqual(math['status'], 'behind')
		self.assertFalse(ot['on_track'])

		# A custom configuration overrides the defaults.
		custom = {'English': [20, 20, 0, 0], 'Math': [0, 0, 0, 0]}
		ot2 = _diploma_on_track(plan, 11, expected_credits=custom)
		eng2 = next(a for a in ot2['areas'] if a['label'] == 'English')
		# English cumulative through grade 11 = 40 of weight 40 -> full 40 expected.
		self.assertEqual(eng2['expected_credits'], 40)
		self.assertTrue(all(a['expected_credits'] % 5 == 0 for a in ot2['areas']))

	def test_diploma_on_track_subject_not_started_is_not_behind(self):
		from dataexploration.view_modules.student_data_utils import _diploma_on_track

		# Social Studies starts in grade 10, so a 9th grader is not behind in it.
		plan = [{
			'display_label': 'DS High School Diploma',
			'pathway_credits': 220,
			'rows': [
				{'subject_area': 'English', 'req_credits': 40.0, 'projected_total': 10.0,
				 'counted_credits': 10.0},
				{'subject_area': 'Social Studies', 'req_credits': 30.0, 'projected_total': 0.0,
				 'counted_credits': 0.0},
			],
		}]
		ot = _diploma_on_track(plan, 9)
		social = next(a for a in ot['areas'] if a['label'] == 'Social Studies')
		self.assertEqual(social['expected_credits'], 0)
		self.assertEqual(social['status'], 'not_started')
		self.assertNotIn(social, ot['lacking'])
		# It is not counted as a shortfall...
		self.assertEqual(ot['credits_shortfall'], 0)
		# ...but by grade 11 the subject is expected and a missing one is behind.
		ot11 = _diploma_on_track(plan, 11)
		social11 = next(a for a in ot11['areas'] if a['label'] == 'Social Studies')
		self.assertEqual(social11['status'], 'behind')
		self.assertIn(social11, ot11['lacking'])

	def test_requirement_plans_met_and_percent(self):
		from dataexploration.view_modules.student_data_utils import _student_requirement_plans

		sets = _student_requirement_plans([
			{'requirement_set': 'CSU', 'grad_plan': 'California State University',
			 'sort_order': 2, 'subject_area': 'B. English', 'req_credits': 40.0,
			 'comp_credits': 40.0, 'wip_credits': 0.0, 'needed_credits': 0.0},
			{'requirement_set': 'CSU', 'grad_plan': 'California State University',
			 'sort_order': 3, 'subject_area': 'C. Mathematics', 'req_credits': 30.0,
			 'comp_credits': 20.0, 'wip_credits': 10.0, 'needed_credits': 10.0},
		])
		rows = sets[0]['rows']
		self.assertEqual(len(rows), 2)
		self.assertTrue(rows[0]['met'])
		self.assertEqual(rows[0]['percent'], 100)
		self.assertFalse(rows[1]['met'])
		self.assertEqual(rows[1]['percent'], 67)
		self.assertIsNone(sets[0]['totals'])

	def test_build_student_details_handles_missing_json(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='123', student_name=None, grade=None, school_name=None,
			school_year=None, counselor_name=None, weighted_gpa=None,
			unweighted_gpa=None, class_rank=None, class_size=None,
			expected_graduation_year=None, graduation_status=None,
			course_history=None, graduation_requirements=None,
			uc_requirements=None, csu_requirements=None,
		)
		out = _build_student_details(row)
		self.assertEqual(out['course_history'], [])
		self.assertEqual(out['requirements']['ag'], [])
		self.assertEqual(out['summary']['rank_display'], '')

	def test_fetch_student_details_empty_when_no_row(self):
		from unittest.mock import patch

		import dataexploration.view_modules.student_data_utils as mod

		with patch.object(mod, '_resolve_ssid', return_value='123'), \
			patch.object(mod, 'CurrentStudent') as cs, \
			patch('studata.models.StudentRpc') as rpc:
			cs.objects.filter.return_value.order_by.return_value.first.return_value = None
			rpc.objects.filter.return_value.first.return_value = None
			out = mod._fetch_student_details('123')

		self.assertFalse(out['has_any'])
		self.assertEqual(out['course_history'], [])
		self.assertEqual(out['requirement_tabs'], [])

	def test_fetch_student_details_returns_built_row(self):
		from unittest.mock import patch

		import dataexploration.view_modules.student_data_utils as mod

		with patch.object(mod, '_resolve_ssid', return_value='123'), \
			patch('studata.models.StudentRpc') as rpc:
			rpc.objects.filter.return_value.first.return_value = self._row()
			out = mod._fetch_student_details('123')

		self.assertTrue(out['has_any'])
		self.assertEqual(out['summary']['class_rank'], '140')

	def test_normalize_mark(self):
		from dataexploration.view_modules.student_data_utils import _normalize_mark

		self.assertEqual(_normalize_mark('A-'), 'A-')
		self.assertEqual(_normalize_mark('A-W'), 'A-')
		self.assertEqual(_normalize_mark('A+W'), 'A+')
		self.assertEqual(_normalize_mark('AW'), 'A')
		self.assertEqual(_normalize_mark('FR'), 'F')
		self.assertEqual(_normalize_mark('PC'), 'P')
		self.assertEqual(_normalize_mark('D+R'), 'D+')
		self.assertEqual(_normalize_mark(''), '')
		self.assertEqual(_normalize_mark(None), '')

	def test_ag_area_helpers(self):
		from dataexploration.view_modules.student_data_utils import _ag_areas, _ag_letter

		self.assertEqual(_ag_letter('GA'), 'G')
		self.assertEqual(_ag_letter('D'), 'D')
		self.assertEqual(_ag_letter(''), '')
		self.assertEqual(_ag_areas('D', 'G'), ['D', 'G'])
		self.assertEqual(_ag_areas('GO', 'G'), ['G'])
		self.assertEqual(_ag_areas('', None), [])

	def test_course_history_ag_tags_and_requirement_by_year(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='2028', graduation_status='',
			course_history=[
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
				 'course_title': 'Biology CP', 'course_id': 'C1', 'display_mark': 'AW',
				 'display_completed': 5.0, 'term_gpa': 3.0},
				{'school_year_range': '2025-2026', 'school_year': 2025, 'term_code': 'SEM1',
				 'course_title': 'Biology CP', 'course_id': 'C1', 'display_mark': 'B+',
				 'display_completed': 5.0, 'term_gpa': 3.3},
			],
			graduation_requirements=[],
			uc_requirements=[
				{'requirement_set': 'UC A-G', 'grad_plan': 'University of California',
				 'sort_order': 4, 'subject_area': 'D. Laboratory Science', 'req_credits': 20.0,
				 'comp_credits': 10.0, 'wip_credits': 0.0, 'needed_credits': 10.0},
			],
			csu_requirements=[
				{'requirement_set': 'CSU', 'grad_plan': 'California State University',
				 'sort_order': 4, 'subject_area': 'D. Laboratory Science', 'req_credits': 20.0,
				 'comp_credits': 10.0, 'wip_credits': 0.0, 'needed_credits': 10.0},
			],
		)
		ag_map = {'C1': {'uc': ['D', 'G'], 'csu': ['D', 'G']}}
		out = _build_student_details(row, ag_map=ag_map)

		self.assertEqual([y['school_year'] for y in out['course_history']], ['2025-2026', '2024-2025'])
		y2024 = out['course_history'][1]['terms'][0]['courses'][0]
		self.assertEqual(y2024['mark'], 'A')
		self.assertEqual(y2024['uc_areas'], ['D', 'G'])
		self.assertEqual(y2024['csu_areas'], ['D', 'G'])

		ag_plan = out['requirements']['ag'][0]
		self.assertEqual(ag_plan['row_count'], 7)
		self.assertEqual(
			[r['subject_area'] for r in ag_plan['rows']],
			['A. History and Social Science', 'B. English', 'C. Mathematics',
			 'D. Laboratory Science', 'E. Language Other Than English',
			 'F. Visual and Performing Arts', 'G. College Preparatory Elective'],
		)
		# CSU pattern: 1 year == 10 credits (A=2, B=4, C=3, D=2, E=2, F=1, G=1).
		self.assertEqual(
			[r['req_credits'] for r in ag_plan['rows']],
			[20.0, 40.0, 30.0, 20.0, 20.0, 10.0, 10.0],
		)
		ag_row = next(r for r in ag_plan['rows'] if r['subject_area'].startswith('D.'))
		self.assertEqual(ag_row['req_years'], 2)
		self.assertEqual([y['school_year'] for y in ag_row['by_year']], ['2025-2026', '2024-2025'])
		self.assertEqual(ag_row['by_year'][0]['credits'], 5.0)
		self.assertEqual(ag_row['by_year'][0]['courses'][0]['mark'], 'B+')
		self.assertEqual(ag_row['by_year'][1]['courses'][0]['mark'], 'A')

	def test_ag_allocation_overflow_to_g(self):
		from dataexploration.view_modules.student_data_utils import _ag_credit_allocation

		course_history = [
			{'school_year': '2024-2025', 'terms': [
				{'courses': [
					{'course_title': 'Art I', 'mark': 'A', 'credits': 10.0, 'csu_areas': ['F', 'G']},
				]},
			]},
			{'school_year': '2025-2026', 'terms': [
				{'courses': [
					{'course_title': 'Art II', 'mark': 'B', 'credits': 10.0, 'csu_areas': ['F', 'G']},
				]},
			]},
		]
		plans = [{'rows': [
			{'subject_area': 'F. Visual and Performing Arts', 'req_credits': 10.0},
			{'subject_area': 'G. College-Preparatory Elective', 'req_credits': 20.0},
		]}]
		alloc = _ag_credit_allocation(course_history, [], plans)
		# The first course satisfies F; the second course falls through to G.
		self.assertEqual([y['credits'] for y in alloc['F']], [10.0])
		self.assertEqual(alloc['F'][0]['school_year'], '2024-2025')
		self.assertEqual([y['credits'] for y in alloc['G']], [10.0])
		self.assertEqual(alloc['G'][0]['school_year'], '2025-2026')
		# Each course represents exactly one A-G letter.
		self.assertEqual(course_history[0]['terms'][0]['courses'][0]['ag_letter'], 'F')
		self.assertEqual(course_history[1]['terms'][0]['courses'][0]['ag_letter'], 'G')

	def test_ag_mark_passes_c_minus_or_better(self):
		from dataexploration.view_modules.student_data_utils import _ag_mark_passes

		for mark in ('A', 'A-', 'B+', 'C', 'C-', 'P', 'CR', '', None):
			self.assertTrue(_ag_mark_passes(mark), msg=f"{mark!r} should pass")
		for mark in ('D+', 'D', 'D-', 'F', 'U', 'NC'):
			self.assertFalse(_ag_mark_passes(mark), msg=f"{mark!r} should fail")

	def test_ag_allocation_lists_d_f_without_credit(self):
		from dataexploration.view_modules.student_data_utils import _ag_credit_allocation

		course_history = [
			{'school_year': '2024-2025', 'terms': [{'courses': [
				{'course_title': 'English I', 'mark': 'D', 'credits': 10.0, 'csu_areas': ['B']},
				{'course_title': 'Math I', 'mark': 'C-', 'credits': 10.0, 'csu_areas': ['C']},
				{'course_title': 'Biology', 'mark': 'F', 'credits': 10.0, 'csu_areas': ['D']},
			]}]},
		]
		plans = [{'rows': [
			{'subject_area': 'B. English', 'req_credits': 40.0},
			{'subject_area': 'C. Mathematics', 'req_credits': 30.0},
			{'subject_area': 'D. Laboratory Science', 'req_credits': 20.0},
		]}]
		alloc = _ag_credit_allocation(course_history, [], plans)
		courses = course_history[0]['terms'][0]['courses']
		# D/F are still assigned to their area and listed, but earn no credit.
		self.assertEqual(courses[0]['ag_letter'], 'B')
		self.assertFalse(courses[0]['ag_credit'])
		self.assertEqual(courses[1]['ag_letter'], 'C')
		self.assertTrue(courses[1]['ag_credit'])
		self.assertEqual(courses[2]['ag_letter'], 'D')
		self.assertFalse(courses[2]['ag_credit'])
		# Only the C- course contributes credits.
		self.assertEqual([y['credits'] for y in alloc['C']], [10.0])
		self.assertEqual([y['credits'] for y in alloc['B']], [0.0])
		self.assertEqual([y['credits'] for y in alloc['D']], [0.0])
		self.assertFalse(alloc['B'][0]['courses'][0]['credit_ok'])
		self.assertEqual(alloc['B'][0]['courses'][0]['name'], 'English I')

	def test_ag_plan_lists_d_course_without_counting_credit(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='', graduation_status='',
			course_history=[
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
				 'course_title': 'English I', 'course_id': 'E1', 'display_mark': 'D',
				 'display_completed': 10.0, 'term_gpa': 1.0},
			],
			graduation_requirements=[],
			uc_requirements=[],
			csu_requirements=[
				{'requirement_set': 'CSU', 'grad_plan': 'California State University',
				 'sort_order': 2, 'subject_area': 'B. English', 'req_credits': 40.0,
				 'comp_credits': 10.0, 'wip_credits': 0.0, 'needed_credits': 30.0},
			],
			current_schedule=None,
		)
		out = _build_student_details(
			row, ag_map={'E1': {'uc': ['B'], 'csu': ['B'], 'subject': 'English'}}
		)
		eng = next(r for r in out['requirements']['ag'][0]['rows']
		           if r['subject_area'].startswith('B.'))
		# The district reported 10 credits, but a D earns none of it.
		self.assertEqual(eng['comp_credits'], 0.0)
		self.assertEqual(eng['counted_credits'], 0.0)
		listed = eng['by_year'][0]['courses'][0]
		self.assertEqual(listed['name'], 'English I')
		self.assertFalse(listed['credit_ok'])

	def test_ag_allocation_ignores_grade_for_scheduled_courses(self):
		from dataexploration.view_modules.student_data_utils import _ag_credit_allocation

		schedule = [
			{'school_year': '2026-2027', 'term': 'Semester 1', 'course_title': 'English III',
			 'mark': '', 'credits': 5.0, 'csu_areas': ['B'], 'earned': False},
		]
		plans = [{'rows': [{'subject_area': 'B. English', 'req_credits': 40.0}]}]
		alloc = _ag_credit_allocation([], schedule, plans)
		self.assertEqual(schedule[0]['ag_letter'], 'B')
		self.assertEqual([y['credits'] for y in alloc['B']], [5.0])

	def test_current_schedule_parsing_and_integration(self):
		from dataexploration.view_modules.student_data_utils import (
			_student_ag_plans, _student_current_schedule,
		)

		raw = [
			{'course_id': 'C1', 'course_title': 'Sculpture I', 'term_code': 'S1', 'period': 1,
			 'credit': 5.0, 'report_card_grade': 'C+', 'mark_status': 'Current',
			 'school_year_range': '2026-2027', 'csu_area_1': 'F', 'csu_area_2': 'G',
			 'school_name': 'S', 'staff_name': 'Evans'},
			{'course_id': 'C2', 'course_title': 'English III', 'term_code': 'S1', 'period': 2,
			 'credit': 5.0, 'report_card_grade': 'A', 'school_year_range': '2026-2027',
			 'csu_area_1': 'B', 'school_name': 'S'},
		]
		items = _student_current_schedule(raw, {})
		self.assertEqual(len(items), 2)
		self.assertEqual(items[0]['term'], 'Semester 1')
		# Live/current grades are hidden; only final (semester) grades show.
		self.assertEqual(items[0]['mark'], '')
		self.assertFalse(items[0]['earned'])
		self.assertEqual(items[1]['csu_areas'], ['B'])

		final_rows = _student_current_schedule(
			[{**raw[0], 'mark_status': 'Final', 'report_card_grade': 'B'}], {}
		)
		# Scheduled courses show credits only (no mark), but are still "earned".
		self.assertEqual(final_rows[0]['mark'], '')
		self.assertTrue(final_rows[0]['earned'])

		# Allocation assigns each in-progress course a single letter and groups it.
		plans = _student_ag_plans([
			{'requirement_set': 'CSU', 'grad_plan': 'California State University',
			 'sort_order': 6, 'subject_area': 'F. Visual and Performing Arts',
			 'req_credits': 10.0, 'comp_credits': 0.0, 'wip_credits': 5.0, 'needed_credits': 10.0},
			{'requirement_set': 'CSU', 'grad_plan': 'California State University',
			 'sort_order': 2, 'subject_area': 'B. English',
			 'req_credits': 40.0, 'comp_credits': 0.0, 'wip_credits': 5.0, 'needed_credits': 40.0},
		], [], items)
		self.assertEqual(items[0]['ag_letter'], 'F')
		self.assertEqual(items[1]['ag_letter'], 'B')
		f_row = next(r for r in plans[0]['rows'] if r['subject_area'].startswith('F.'))
		self.assertEqual(len(f_row['in_progress']), 1)
		self.assertEqual(f_row['in_progress'][0]['course_title'], 'Sculpture I')
		self.assertTrue(f_row['by_year'][0]['in_progress'])
		self.assertEqual(f_row['pct_completed'], 0)
		self.assertGreater(f_row['pct_in_progress'], 0)

	def test_merge_projection_schedule_dedupes_by_identity(self):
		from dataexploration.view_modules.student_data_utils import _merge_projection_schedule

		current = [{'course_id': 'E1', 'course_title': 'English III', 'period': '01'}]
		# Same course, different period spelling -> deduped.
		same = [{'course_id': 'E1', 'course_title': 'English III', 'period': '1',
		         'school_year': '2026-2027'}]
		self.assertEqual(len(_merge_projection_schedule(current, same)), 1)
		# A different course is added.
		other = [{'course_id': 'M2', 'course_title': 'Math II', 'period': '2',
		          'school_year': '2026-2027'}]
		self.assertEqual(len(_merge_projection_schedule(current, other)), 2)
		# A course already recorded in the same year's history is skipped.
		history = {'2026-2027': {('code', 'e1')}}
		self.assertEqual(len(_merge_projection_schedule([], same, history)), 0)

	def test_current_schedule_skips_left_without_semester_mark(self):
		from dataexploration.view_modules.student_data_utils import _student_current_schedule

		raw = [
			# Same class twice: one enrollment left mid-term with no semester mark.
			{'course_id': 'U1', 'course_title': 'US History', 'term_code': 'S1', 'period': 1,
			 'credit': 5.0, 'report_card_grade': '', 'mark_status': '',
			 'school_year_range': '2025-2026', 'leave_date': '2025-10-15'},
			{'course_id': 'U1', 'course_title': 'US History', 'term_code': 'S1', 'period': 1,
			 'credit': 5.0, 'report_card_grade': 'B', 'mark_status': 'Final',
			 'school_year_range': '2025-2026'},
			# A leave date plus a final mark is a completed class — keep it.
			{'course_id': 'E1', 'course_title': 'English III', 'term_code': 'S1', 'period': 2,
			 'credit': 5.0, 'report_card_grade': 'A', 'mark_status': 'Final',
			 'school_year_range': '2025-2026', 'leave_date': '2025-12-20'},
		]
		items = _student_current_schedule(raw, {})
		self.assertEqual(
			[(i['course_title'], i['mark']) for i in items],
			[('US History', ''), ('English III', '')],
		)

	def test_dropped_current_schedule_identities(self):
		from dataexploration.view_modules.student_data_utils import _dropped_current_schedule_identities

		raw = [
			{'course_id': 'U1', 'course_title': 'US History', 'mark_status': '',
			 'leave_date': '2025-10-15'},
			{'course_id': 'E1', 'course_title': 'English III', 'mark_status': 'Final',
			 'report_card_grade': 'A', 'leave_date': '2025-12-20'},
			{'course_id': 'M1', 'course_title': 'Math I', 'mark_status': ''},
		]
		dropped = _dropped_current_schedule_identities(raw)
		self.assertIn(('code', 'u1'), dropped)
		self.assertNotIn(('code', 'e1'), dropped)
		self.assertNotIn(('code', 'm1'), dropped)

	def test_dropped_schedule_rows_are_removed(self):
		from dataexploration.view_modules.student_data_utils import (
			_student_current_schedule, _dropped_current_schedule_identities,
		)

		raw = [
			{'course_id': 'E1', 'course_title': 'English III', 'term_code': 'S1', 'period': 1,
			 'credit': 5.0, 'report_card_grade': 'B', 'mark_status': 'Final',
			 'school_year_range': '2025-2026', 'schedule_status': 'Active'},
			{'course_id': 'M1', 'course_title': 'Math II', 'term_code': 'S1', 'period': 2,
			 'credit': 5.0, 'report_card_grade': '', 'mark_status': '',
			 'school_year_range': '2025-2026', 'schedule_status': 'Dropped'},
			{'course_id': 'H1', 'course_title': 'US History', 'term_code': 'S1', 'period': 3,
			 'credit': 5.0, 'report_card_grade': '', 'mark_status': 'Withdrawn',
			 'school_year_range': '2025-2026'},
			{'course_id': 'S1', 'course_title': 'Biology', 'term_code': 'S1', 'period': 4,
			 'credit': 5.0, 'report_card_grade': '', 'mark_status': '',
			 'school_year_range': '2025-2026', 'enrollment_status': 'Left District'},
		]
		items = _student_current_schedule(raw, {})
		self.assertEqual([i['course_title'] for i in items], ['English III'])
		dropped = _dropped_current_schedule_identities(raw)
		self.assertIn(('code', 'm1'), dropped)
		self.assertIn(('code', 'h1'), dropped)
		self.assertIn(('code', 's1'), dropped)
		self.assertNotIn(('code', 'e1'), dropped)

	def test_course_history_dedupes_by_title_and_skips_left(self):
		from dataexploration.view_modules.student_data_utils import _student_course_history

		raw = [
			# Same class with two course ids in one term -> merged, counted once.
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
			 'course_id': 'M1A', 'course_title': 'Math I', 'display_mark': 'B',
			 'display_completed': 5.0, 'term_gpa': 3.0},
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
			 'course_id': 'M1B', 'course_title': 'Math I', 'display_mark': 'B',
			 'display_completed': 5.0, 'term_gpa': 3.0},
			# Left enrollment with no mark -> dropped.
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM2',
			 'course_id': 'M2', 'course_title': 'Math II', 'display_mark': '',
			 'display_completed': 0.0, 'term_gpa': 0.0, 'leave_date': '2025-03-01'},
		]
		out = _student_course_history(raw, {})
		self.assertEqual([t['term'] for t in out[0]['terms']], ['Semester 1'])
		term = out[0]['terms'][0]
		self.assertEqual(len(term['courses']), 1)
		self.assertEqual(term['courses'][0]['course_title'], 'Math I')
		self.assertEqual(term['courses'][0]['credits'], 5.0)
		self.assertEqual(out[0]['credits_earned'], 5.0)

	def test_current_schedule_dedupes_same_course_in_term(self):
		from dataexploration.view_modules.student_data_utils import _student_current_schedule

		raw = [
			{'course_id': 'U1', 'course_title': 'US History', 'term_code': 'S1', 'period': 1,
			 'credit': 5.0, 'report_card_grade': 'C', 'mark_status': 'Final',
			 'school_year_range': '2025-2026'},
			{'course_id': 'U2', 'course_title': 'US History', 'term_code': 'S1', 'period': 2,
			 'credit': 5.0, 'report_card_grade': 'B', 'mark_status': 'Final',
			 'school_year_range': '2025-2026'},
		]
		items = _student_current_schedule(raw, {})
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]['course_title'], 'US History')

	def test_diploma_does_not_double_count_history_and_schedule(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2025-2026',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='', graduation_status='',
			course_history=[
				{'school_year_range': '2025-2026', 'school_year': 2025, 'term_code': 'SEM1',
				 'course_title': 'Math I', 'course_id': 'M1', 'display_mark': 'B',
				 'display_completed': 5.0, 'term_gpa': 3.0},
			],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 102, 'subject_area': 'Math', 'req_credits': 30.0,
				 'comp_credits': 5.0, 'wip_credits': 0.0, 'needed_credits': 25.0},
			],
			uc_requirements=[], csu_requirements=[],
			current_schedule=[
				# Same course and term as the transcript, listed again as final.
				{'course_id': 'M1', 'course_title': 'Math I', 'term_code': 'SEM1', 'period': 1,
				 'credit': 5.0, 'report_card_grade': 'B', 'mark_status': 'Final',
				 'school_year_range': '2025-2026', 'csu_area_1': 'C'},
			],
		)
		out = _build_student_details(
			row, ag_map={'M1': {'subject': 'Mathematics', 'csu': ['C']}}
		)
		math = next(r for r in out['requirements']['diploma'][0]['rows']
		            if r['subject_area'] == 'Math')
		self.assertEqual(math['comp_credits'], 5.0)
		self.assertEqual(math['counted_credits'], 5.0)
		# The displayed current schedule is the RPC column (kept as-is)...
		self.assertEqual(len(out['current_schedule']), 1)
		# ...but the projection does not double-count it against the transcript.
		self.assertEqual(math['scheduled_current'], 0.0)

	def test_apply_prediction_earned_vs_projected(self):
		from dataexploration.view_modules.student_data_utils import _apply_prediction

		row = {'subject_area': 'B. English', 'req_credits': 40.0, 'comp_credits': 20.0,
		       'needed_credits': 20.0, 'percent': 50}
		courses = [
			{'course_title': 'English III', 'credits': 5.0, 'earned': True},
			{'course_title': 'English III', 'credits': 5.0, 'earned': False},
			{'course_title': 'English III', 'credits': 5.0, 'earned': False},
		]
		_apply_prediction(row, courses)
		self.assertEqual(row['earned_current'], 5.0)
		self.assertEqual(row['scheduled_current'], 10.0)
		self.assertEqual(row['counted_credits'], 25.0)
		self.assertEqual(row['needed_after'], 15.0)
		self.assertEqual(row['projected_total'], 35.0)
		self.assertEqual(row['projection_status'], 'Not Met')
		self.assertEqual(row['pct_earned'], 62)
		self.assertEqual(row['pct_earned'] + row['pct_projected'], 88)

		row2 = {'subject_area': 'F.', 'req_credits': 10.0, 'comp_credits': 0.0,
		        'needed_credits': 10.0, 'percent': 0}
		_apply_prediction(row2, [{'course_title': 'Art I', 'credits': 10.0, 'earned': False}])
		self.assertEqual(row2['projection_status'], 'Scheduled')

		row3 = {'subject_area': 'D.', 'req_credits': 20.0, 'comp_credits': 20.0,
		        'needed_credits': 0.0, 'percent': 100}
		_apply_prediction(row3, [])
		self.assertEqual(row3['projection_status'], 'Met')

	def test_course_history_merges_sections_with_multiple_marks(self):
		from dataexploration.view_modules.student_data_utils import _student_course_history

		raw = [
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
			 'course_id': '1087A', 'course_title': 'Designated ELD Newcomer 11',
			 'display_mark': 'C-', 'display_completed': 5.0, 'term_gpa': 2.0},
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
			 'course_id': '1087A', 'course_title': 'Designated ELD Newcomer 11',
			 'display_mark': 'B', 'display_completed': 5.0, 'term_gpa': 2.0},
			{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
			 'course_id': '1087A', 'course_title': 'Designated ELD Newcomer 11',
			 'display_mark': 'B', 'display_completed': 5.0, 'term_gpa': 2.0},
		]
		out = _student_course_history(raw, {})
		year = out[0]
		self.assertEqual(year['course_count'], 1)
		self.assertEqual(year['credits_earned'], 5.0)
		course = year['terms'][0]['courses'][0]
		self.assertEqual(course['mark'], 'C- / B')
		self.assertEqual(course['credits'], 5.0)

	def test_diploma_earned_from_course_history_when_district_zero(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='2028', graduation_status='',
			course_history=[
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
				 'course_title': 'Spanish I CP', 'course_id': '4611A', 'display_mark': 'A',
				 'display_completed': 5.0, 'term_gpa': 4.0},
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM2',
				 'course_title': 'Spanish I CP', 'course_id': '4611B', 'display_mark': 'A',
				 'display_completed': 5.0, 'term_gpa': 4.0},
			],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 104, 'subject_area': 'Fine Arts/WL/CTE', 'req_credits': 10.0,
				 'comp_credits': 0.0, 'wip_credits': 0.0, 'needed_credits': 10.0},
			],
			uc_requirements=[], csu_requirements=[], current_schedule=None,
		)
		ag_map = {
			'4611A': {'uc': [], 'csu': [], 'subject': 'Fine Arts'},
			'4611B': {'uc': [], 'csu': [], 'subject': 'Fine Arts'},
		}
		out = _build_student_details(row, ag_map=ag_map)
		plan = out['requirements']['diploma'][0]
		# DSUSD Standard Diploma: 7 categories totalling 220 credits.
		self.assertEqual(plan['display_label'], 'DS High School Diploma')
		self.assertEqual(
			[(r['subject_area'], r['req_credits']) for r in plan['rows']],
			[('English', 40.0), ('Math', 30.0), ('Social Studies', 30.0), ('Science', 20.0),
			 ('Physical Education', 20.0), ('Fine Art, CTE, or World Language (LOTE)', 10.0),
			 ('Electives', 70.0)],
		)
		self.assertEqual(sum(r['req_credits'] for r in plan['rows']), 220.0)
		r = next(x for x in plan['rows'] if x['subject_area'] == 'Fine Art, CTE, or World Language (LOTE)')
		self.assertEqual(r['comp_credits'], 10.0)
		self.assertEqual(r['counted_credits'], 10.0)
		self.assertEqual(r['needed_after'], 0.0)
		self.assertEqual(r['projection_status'], 'Met')

	def test_dsusd_continuation_diploma_uses_200_credits(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='12', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='2028', graduation_status='',
			course_history=[],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'Alt Edu High School Diploma',
				 'sort_order': 101, 'subject_area': 'English', 'req_credits': 40.0,
				 'comp_credits': 40.0, 'wip_credits': 0.0, 'needed_credits': 0.0},
				{'requirement_set': 'District Graduation', 'grad_plan': 'Alt Edu High School Diploma',
				 'sort_order': 102, 'subject_area': 'US History', 'req_credits': 10.0,
				 'comp_credits': 10.0, 'wip_credits': 0.0, 'needed_credits': 0.0},
				{'requirement_set': 'District Graduation', 'grad_plan': 'Alt Edu High School Diploma',
				 'sort_order': 103, 'subject_area': 'Economics', 'req_credits': 10.0,
				 'comp_credits': 5.0, 'wip_credits': 0.0, 'needed_credits': 5.0},
			],
			uc_requirements=[], csu_requirements=[], current_schedule=None,
		)
		plan = _build_student_details(row, ag_map={})['requirements']['diploma'][0]
		self.assertEqual(plan['display_label'], 'Alternative Education Diploma')
		self.assertEqual(plan['pathway_credits'], 200)
		required = {r['subject_area']: r['req_credits'] for r in plan['rows']}
		self.assertEqual(required['Electives'], 50.0)
		self.assertEqual(sum(required.values()), 200.0)
		# Social Studies aggregates US History (10) + Economics (5).
		social = next(r for r in plan['rows'] if r['subject_area'] == 'Social Studies')
		self.assertEqual(social['comp_credits'], 15.0)

	def test_diploma_science_lists_courses_for_counted_credits(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import (
			_build_student_details, _diploma_area_for,
		)

		self.assertEqual(_diploma_area_for('Biology', []), 'Life Science')
		self.assertEqual(_diploma_area_for('Chemistry', []), 'Physical Science')
		self.assertEqual(_diploma_area_for('Social Science', []), 'Social Studies')
		self.assertEqual(_diploma_area_for('', ['D']), 'Science')
		self.assertEqual(_diploma_area_for('', ['B']), 'English')

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='', graduation_status='',
			course_history=[
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM1',
				 'course_title': 'Biology', 'course_id': 'B1', 'display_mark': 'B',
				 'display_completed': 10.0, 'term_gpa': 3.0},
				{'school_year_range': '2024-2025', 'school_year': 2024, 'term_code': 'SEM2',
				 'course_title': 'Chemistry', 'course_id': 'C1', 'display_mark': 'C',
				 'display_completed': 10.0, 'term_gpa': 2.0},
			],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 104, 'subject_area': 'Science', 'req_credits': 20.0,
				 'comp_credits': 20.0, 'wip_credits': 0.0, 'needed_credits': 0.0},
			],
			uc_requirements=[], csu_requirements=[], current_schedule=None,
		)
		ag_map = {
			'B1': {'subject': 'Biology', 'csu': ['D']},
			'C1': {'subject': 'Chemistry', 'csu': ['D']},
		}
		out = _build_student_details(row, ag_map=ag_map)
		science = next(r for r in out['requirements']['diploma'][0]['rows']
		               if r['subject_area'] == 'Science')
		self.assertEqual(science['comp_credits'], 20.0)
		names = [c['name'] for y in science['by_year'] for c in y['courses']]
		self.assertIn('Biology', names)
		self.assertIn('Chemistry', names)

	def test_projection_counts_full_year_schedule(self):
		from types import SimpleNamespace

		from dataexploration.view_modules.student_data_utils import _build_student_details

		row = SimpleNamespace(
			ssid='1', student_name='X', grade='11', school_name='S', school_year='2026-2027',
			counselor_name='', weighted_gpa=None, unweighted_gpa=None, class_rank=None,
			class_size=None, expected_graduation_year='', graduation_status='',
			course_history=[],
			graduation_requirements=[
				{'requirement_set': 'District Graduation', 'grad_plan': 'DS High School',
				 'sort_order': 102, 'subject_area': 'Math', 'req_credits': 30.0,
				 'comp_credits': 0.0, 'wip_credits': 0.0, 'needed_credits': 30.0},
			],
			uc_requirements=[], csu_requirements=[],
			current_schedule=[
				{'course_id': 'M1', 'course_title': 'Math I', 'term_code': 'S1', 'period': 1,
				 'credit': 5.0, 'report_card_grade': 'B', 'mark_status': 'Current',
				 'school_year_range': '2026-2027', 'csu_area_1': 'C', 'staff_name': 'T'},
			],
		)
		# M1 repeats the active term (deduped); M2 is the other term of the year.
		schedule_courses = [
			{'school_year': '2026-2027', 'term': 'Scheduled', 'term_code': '', 'period': 1,
			 'course_title': 'Math I', 'course_id': 'M1', 'mark': '', 'mark_status': '',
			 'earned': False, 'credits': 5.0, 'staff_name': 'T', 'csu_areas': ['C'],
			 'subject': 'Mathematics', 'diploma_area': 'Math', 'ag_letter': '', 'from_schedule': True},
			{'school_year': '2026-2027', 'term': 'Scheduled', 'term_code': '', 'period': 2,
			 'course_title': 'Math II', 'course_id': 'M2', 'mark': '', 'mark_status': '',
			 'earned': False, 'credits': 5.0, 'staff_name': 'T', 'csu_areas': ['C'],
			 'subject': 'Mathematics', 'diploma_area': 'Math', 'ag_letter': '', 'from_schedule': True},
		]
		out = _build_student_details(
			row, ag_map={'M1': {'subject': 'Mathematics'}}, schedule_courses=schedule_courses
		)
		math = next(r for r in out['requirements']['diploma'][0]['rows']
		            if r['subject_area'] == 'Math')
		self.assertEqual(math['scheduled_current'], 10.0)
		self.assertEqual(math['projected_total'], 10.0)
		# The displayed current schedule remains the active term only.
		self.assertEqual(len(out['current_schedule']), 1)
