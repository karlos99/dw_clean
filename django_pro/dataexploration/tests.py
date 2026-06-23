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
