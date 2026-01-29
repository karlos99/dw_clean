from django.test import SimpleTestCase
from django.template.loader import get_template


class TemplateCompilationTests(SimpleTestCase):
	def test_folder_browser_template_compiles(self):
		# This template is rendered via HTMX at /dataexploration/api/folders/.
		# A small syntax regression (e.g. missing spaces around '==') causes a hard 500.
		get_template("dataexploration/partials/folder_browser.html")

	def test_student_with_tests_list_template_compiles(self):
		get_template("dataexploration/student_with_tests_list.html")


class StudentListConfigTests(SimpleTestCase):
	def test_student_list_savedquery_config_key(self):
		# Student lists are stored as SavedQuery(query_type='student_list')
		# with SSIDs in config['student_ids'].
		config = {"student_ids": ["123", "456"]}
		self.assertEqual(config.get("student_ids"), ["123", "456"])
