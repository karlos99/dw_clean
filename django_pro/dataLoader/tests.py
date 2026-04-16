"""
Tests for the dataLoader app.
"""
from django.test import TestCase
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
