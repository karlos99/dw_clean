"""
Tests for the dataLoader app.
"""
from django.test import TestCase, SimpleTestCase
from dataLoader.utils.column_analyzer import (
    analyze_csv, detect_header_row, _find_question_columns,
    QuestionColumn, AnalysisResult
)


class ColumnAnalyzerTests(TestCase):
    """Tests for the column analyzer utility."""
    
    def setUp(self):
        """Sample CSV content for testing."""
        self.sample_csv = '''Client Name,District Name,School Name,School ID,Student First Name,Student Last Name,Student Email,Student ID,Student Grade Level,How often do you stay focused? VALUES,How often do you stay focused? TEXT;How likely are you to try again? VALUES,How likely are you to try again? TEXT
Desert Sands,Desert Sands USD,Test Elementary,12345,John,Doe,john@test.com,10001,4,5,Almost always,4,Frequently
Desert Sands,Desert Sands USD,Test Elementary,12345,Jane,Smith,jane@test.com,10002,5,3,Sometimes,5,Almost always'''
    
    def test_analyze_csv_detects_questions(self):
        """Test that analyze_csv correctly detects question columns."""
        result = analyze_csv(self.sample_csv, 'test_file.csv')
        
        # Should detect demographic columns
        self.assertIn('student_id', result.demographic_columns)
        self.assertIn('first_name', result.demographic_columns)
        self.assertIn('last_name', result.demographic_columns)
    
    def test_find_question_columns_pairs_values_and_text(self):
        """Test that VALUE/TEXT column pairs are correctly identified."""
        headers = [
            'Student ID',
            'How focused can you stay? VALUES',
            'How focused can you stay? TEXT',
            'How likely to try again? VALUES',
            'How likely to try again? TEXT',
        ]
        
        questions = _find_question_columns(headers)
        
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].question_text, 'How focused can you stay?')
        self.assertEqual(questions[1].question_text, 'How likely to try again?')


class SurveyModelTests(TestCase):
    """Tests for survey models."""
    
    def test_survey_definition_str(self):
        """Test SurveyDefinition string representation."""
        from dataLoader.models import SurveyDefinition, TestingWindow
        
        survey = SurveyDefinition(
            name='Test Survey',
            survey_type='Skills and Competencies',
            school_year='2025-2026',
            testing_window=TestingWindow.SPRING,
        )
        
        self.assertIn('Test Survey', str(survey))
        self.assertIn('2025-2026', str(survey))
    
    def test_question_key_generation(self):
        """Test that question_key is generated from question text."""
        from dataLoader.models import SurveyQuestion
        
        key = SurveyQuestion._generate_question_key(
            "How often do you stay focused on the same goal?"
        )
        
        self.assertTrue(len(key) > 0)
        self.assertNotIn(' ', key)  # Should be slugified


class SATAnalyzerTests(SimpleTestCase):
    """Tests for the SAT column analyzer / loader helpers."""

    SAMPLE = (
        "AI_CODE,AI_NAME,NAME_LAST,NAME_FIRST,NAME_MI,GENDER,BIRTH_DATE,CB_ID,STATE_STUDENT_ID,"
        "LATEST_SAT_DATE,LATEST_SAT_TOTAL,LATEST_SAT_EBRW,LATEST_SAT_MATH_SECTION,"
        "PERCENTILE_STATE_TOTAL,PERCENTILE_STATE_RW,PERCENTILE_STATE_MATH,"
        "PERCENTILE_COUNTRY_TOTAL,PERCENTILE_COUNTRY_RW,PERCENTILE_COUNTRY_MATH,"
        "PERCENTILE_NATREP_SAT_TOTAL,PERCENTILE_NATREP_SAT_EBRW,PERCENTILE_NATREP_SAT_MATH_SECTION,"
        "PERCENTILE_NATUSER_SAT_TOTAL,PERCENTILE_NATUSER_SAT_EBRW,PERCENTILE_NATUSER_SAT_MATH_SECTION\n"
        "050276,SUMMIT HIGH SCHOOL,ELENA,CESAR,A,M,2008-02-01,152957413,,"
        "2026-08-22,550,350,200,1,5,0,1,6,1,1,5,1,1,6,1\n"
        "051247,INDIO HIGH SCHOOL,DURINZI,DAMIANO,S,M,2008-11-05,144147311,7527538800,"
        "2026-08-22,1020,510,510,40,37,43,53,49,57,52,51,52,50,47,53\n"
    )

    def test_analyze_sat_csv(self):
        from dataLoader.utils.sat_column_analyzer import analyze_sat_csv

        result = analyze_sat_csv(self.SAMPLE, 'sat.csv')
        self.assertEqual(result.total_students, 2)
        self.assertEqual(result.total_with_scores, 2)
        self.assertEqual(result.total_missing_state_id, 1)
        self.assertIn('STATE_STUDENT_ID', result.student_columns)
        self.assertIn('LATEST_SAT_TOTAL', result.score_columns)
        self.assertEqual(
            sorted(result.available_percentile_regions),
            ['country', 'natrep', 'natuser', 'state'],
        )
        self.assertEqual(result.sample_students[0].total_score, '550')

    def test_parse_helpers(self):
        from dataLoader.utils.sat_column_analyzer import parse_int, parse_date

        self.assertEqual(parse_int('550'), 550)
        self.assertIsNone(parse_int(''))
        self.assertIsNone(parse_int('N/A'))
        self.assertEqual(parse_date('2026-08-22').isoformat(), '2026-08-22')
        self.assertEqual(parse_date('08/22/2026').isoformat(), '2026-08-22')
        self.assertIsNone(parse_date(''))

    def test_build_record_key(self):
        from dataLoader.utils.sat_response_loader import build_record_key

        key = build_record_key('152957413', '', 'ELENA', 'CESAR', '2008-02-01', '2026-08-22')
        self.assertEqual(key, '152957413|2026-08-22')
        # Falls back to name + dob when both IDs are missing
        key2 = build_record_key('', '', 'ELENA', 'CESAR', '2008-02-01', '')
        self.assertEqual(key2, 'ELENA|CESAR|2008-02-01|nodate')
