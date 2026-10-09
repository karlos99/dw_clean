"""
Tests for the dataLoader app.
"""
from django.test import TestCase, SimpleTestCase
from dataLoader.utils.column_analyzer import (
    analyze_csv, detect_header_row, _find_question_columns,
    QuestionColumn, AnalysisResult
)


TEXT_ONLY_CSV = (
    "Client Name,School Name,School ID,Student First Name,Student Last Name,"
    "Student ID,Student Grade Level,"
    "How often do you stay focused? TEXT,"
    "How positive or negative is the mood at your school? TEXT\n"
    "Desert Sands,Test MS,335,John,Doe,10001,6,Almost always,Very positive\n"
    "Desert Sands,Test MS,335,Jane,Smith,10002,7,Almost never,Very negative\n"
)


class TextOnlyLayoutTests(SimpleTestCase):
    """Tests for the newer Panorama layout that omits the numeric VALUES column."""

    def test_find_question_columns_detects_text_only(self):
        headers = [
            'Student ID',
            'How often do you stay focused? TEXT',
            'How positive or negative is the mood at your school? TEXT',
        ]
        questions = _find_question_columns(headers)
        self.assertEqual(len(questions), 2)
        self.assertTrue(all(q.text_only for q in questions))
        self.assertEqual(questions[0].question_text, 'How often do you stay focused?')
        self.assertEqual(questions[0].text_column_index, 1)
        self.assertEqual(questions[0].value_column, '')

    def test_analyze_csv_maps_labels_to_values(self):
        result = analyze_csv(TEXT_ONLY_CSV, 'Fall 26 Equity Survey Grades 6 - 8.csv')
        self.assertEqual(result.detected_survey_type, 'Equity Survey')
        self.assertEqual(result.detected_grade_range, 'Grades 6-8')
        self.assertEqual(len(result.questions), 2)
        self.assertEqual(result.warnings, [])

        focus, mood = result.questions
        self.assertTrue(focus.text_only)
        self.assertEqual(focus.response_scale_max, 5)
        self.assertEqual(focus.scale_labels[1], 'Almost never')
        self.assertEqual(focus.scale_labels[5], 'Almost always')

        self.assertEqual(mood.response_scale_max, 7)
        self.assertEqual(mood.scale_labels[1], 'Very negative')
        self.assertEqual(mood.scale_labels[7], 'Very positive')

    def test_grade_range_with_spaces(self):
        self.assertEqual(
            analyze_csv(TEXT_ONLY_CSV, 'x Grades 6 - 8.csv').detected_grade_range,
            'Grades 6-8',
        )
        self.assertEqual(
            analyze_csv(TEXT_ONLY_CSV, 'x Grades 9 - 12.csv').detected_grade_range,
            'Grades 9-12',
        )


class SurveySettingsViewTests(TestCase):
    """Tests for the Survey Settings page (select survey, assign categories)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from dataLoader.models import (
            SurveyDefinition, SurveyQuestion, SurveyTopic, TestingWindow,
        )

        self.reverse = reverse
        user = get_user_model().objects.create_superuser(
            username='admin', email='admin@test.com', password='pw'
        )
        self.client.force_login(user)

        self.survey = SurveyDefinition.objects.create(
            name='Equity Test', survey_type='Equity Survey',
            school_year='2026-2027', testing_window=TestingWindow.FALL,
            grade_range='Grades 6-8',
        )
        self.question = SurveyQuestion.objects.create(
            survey_definition=self.survey,
            question_text='How much support do the adults at your school give you?',
            value_column_name='How much support do the adults at your school give you? TEXT',
            text_column_name='How much support do the adults at your school give you? TEXT',
            order=0,
        )
        self.topic = SurveyTopic.objects.create(name='Teacher-Student Relationships')

    def test_settings_page_lists_survey_questions_and_topics(self):
        resp = self.client.get(self.reverse('dataLoader:survey_settings'),
                               {'survey': self.survey.pk})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('question-topic-select', body)
        self.assertIn('Teacher-Student Relationships', body)
        self.assertIn(self.question.question_text, body)

    def test_assign_question_topic(self):
        resp = self.client.post(
            self.reverse('dataLoader:assign_question_topic',
                         args=[self.survey.pk, self.question.pk]),
            data='{"topic_id": %d}' % self.topic.pk,
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.question.refresh_from_db()
        self.assertEqual(self.question.topic_id, self.topic.pk)


class ColorPaletteTests(TestCase):
    """Tests for saved color palettes and per-question color assignment."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from dataLoader.models import SurveyDefinition, SurveyQuestion, TestingWindow

        self.reverse = reverse
        user = get_user_model().objects.create_superuser(
            username='admin2', email='admin2@test.com', password='pw'
        )
        self.client.force_login(user)
        self.survey = SurveyDefinition.objects.create(
            name='Equity Test 2', survey_type='Equity Survey',
            school_year='2026-2027', testing_window=TestingWindow.FALL,
            grade_range='Grades 6-8',
        )
        self.question = SurveyQuestion.objects.create(
            survey_definition=self.survey,
            question_text='How much support do the adults at your school give you?',
            value_column_name='How much support do the adults at your school give you? TEXT',
            text_column_name='How much support do the adults at your school give you? TEXT',
            order=0,
            response_scale_max=5,
        )

    def test_create_update_delete_palette(self):
        from dataLoader.models import SurveyColorPalette

        resp = self.client.post(
            self.reverse('dataLoader:color_palette_list'),
            data='{"name": "Low-Mid-High", "colors": {"scale_5": {"1": "#111111", "5": "#eeeeee"}, "bogus": "x"}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201)
        palette_id = resp.json()['palette']['id']
        palette = SurveyColorPalette.objects.get(pk=palette_id)
        # Invalid scale keys are dropped, valid ones kept
        self.assertEqual(palette.colors, {'scale_5': {'1': '#111111', '5': '#eeeeee'}})

        resp = self.client.post(
            self.reverse('dataLoader:update_color_palette', args=[palette_id]),
            data='{"name": "Renamed", "colors": {"scale_7": {"7": "#00ff00"}}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        palette.refresh_from_db()
        self.assertEqual(palette.name, 'Renamed')
        self.assertEqual(palette.colors, {'scale_7': {'7': '#00ff00'}})

        resp = self.client.post(
            self.reverse('dataLoader:delete_color_palette', args=[palette_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SurveyColorPalette.objects.filter(pk=palette_id).exists())

    def test_save_question_colors_stores_override(self):
        resp = self.client.post(
            self.reverse('dataLoader:save_question_colors',
                         args=[self.survey.pk, self.question.pk]),
            data='{"colors": {"1": "#111111", "5": "#eeeeee"}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.survey.refresh_from_db()
        self.assertEqual(
            self.survey.response_colors.get('questions', {}).get('0'),
            {'1': '#111111', '5': '#eeeeee'},
        )

    def test_settings_page_embeds_palettes_as_json_object(self):
        import json
        import re
        from dataLoader.models import SurveyColorPalette

        SurveyColorPalette.objects.create(
            name='default_X', colors={'scale_5': {'1': '#000000'}})

        resp = self.client.get(
            self.reverse('dataLoader:survey_settings'), {'survey': self.survey.pk})
        body = resp.content.decode()
        match = re.search(
            r'<script id="colorPalettesData" type="application/json">(.*?)</script>',
            body, re.S,
        )
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        # Must be a dict of palettes (not a double-encoded string).
        self.assertIsInstance(data, dict)
        self.assertIn('default_X', [v['name'] for v in data.values()])


class CreateDefaultColorPalettesCommandTests(TestCase):
    """Management command: seed SurveyColorPalette from existing survey colors."""

    def test_creates_default_palettes_skips_today_and_existing_names(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        from dataLoader.models import SurveyDefinition, SurveyColorPalette, TestingWindow

        scale5 = {'1': '#ef581c', '5': '#1caa49'}
        scale5_7 = {
            'scale_5': scale5,
            'scale_7': {'1': '#ef581c', '7': '#1caa49'},
        }

        old_a = SurveyDefinition.objects.create(
            name='Old A', survey_type='Equity Survey',
            school_year='2025-2026', testing_window=TestingWindow.FALL,
            grade_range='Grades 6-8',
            response_colors={'scale_5': scale5},
        )
        old_b = SurveyDefinition.objects.create(
            name='Old B', survey_type='Equity Survey',
            school_year='2024-2025', testing_window=TestingWindow.FALL,
            grade_range='Grades 6-8',
            response_colors=scale5_7,
        )
        yesterday = timezone.now() - timedelta(days=1)
        SurveyDefinition.objects.filter(pk__in=[old_a.pk, old_b.pk]).update(created_at=yesterday)

        today_survey = SurveyDefinition.objects.create(
            name='Today', survey_type='Equity Survey',
            school_year='2026-2027', testing_window=TestingWindow.FALL,
            grade_range='Grades 6-8',
            response_colors={'scale_5': {'1': '#000000', '5': '#ffffff'}},
        )
        self.assertEqual(
            timezone.localtime(today_survey.created_at).date(),
            timezone.localdate(),
        )

        call_command('create_default_color_palettes')

        self.assertTrue(SurveyColorPalette.objects.filter(name='default_1').exists())
        self.assertTrue(SurveyColorPalette.objects.filter(name='default_2').exists())
        p1 = SurveyColorPalette.objects.get(name='default_1')
        self.assertEqual(p1.colors, {'scale_5': scale5})

        # Second run must not duplicate or overwrite.
        call_command('create_default_color_palettes')
        self.assertEqual(SurveyColorPalette.objects.filter(name='default_1').count(), 1)

        # Today's-only palette must not become a saved default.
        self.assertFalse(any(
            (p.colors.get('scale_5') or {}).get('1') == '#000000'
            for p in SurveyColorPalette.objects.all()
        ))


class TextOnlyImportTests(TestCase):
    """End-to-end import of a TEXT-only layout into the database."""

    def test_loader_resolves_text_labels_to_numeric_values(self):
        from dataLoader.models import (
            ImportLog, SurveyResponse, TestingWindow, ImportStatus,
        )
        from dataLoader.utils.response_loader import ResponseLoader, LoaderConfig

        analysis = analyze_csv(TEXT_ONLY_CSV, 'Fall 26 Equity Survey Grades 6 - 8.csv')
        import_log = ImportLog.objects.create(
            name='Equity Test',
            school_year='2026-2027',
            testing_window=TestingWindow.FALL,
            filename='equity.csv',
        )
        loader = ResponseLoader(
            import_log, analysis,
            config=LoaderConfig(skip_unmatched_students=False),
        )
        loader.create_survey_definition(user=None)
        loader.create_questions()
        loader.load_responses(TEXT_ONLY_CSV)

        self.assertEqual(loader.questions['How often do you stay focused? TEXT']
                         .response_scale_max, 5)
        self.assertEqual(SurveyResponse.objects.count(), 4)

        john_focus = SurveyResponse.objects.get(
            student_id='10001',
            question__question_text='How often do you stay focused?',
        )
        self.assertEqual(john_focus.value, 5)  # Almost always

        jane_mood = SurveyResponse.objects.get(
            student_id='10002',
            question__question_text='How positive or negative is the mood at your school?',
        )
        self.assertEqual(jane_mood.value, 1)  # Very negative

        self.assertEqual(import_log.status, ImportStatus.COMPLETED)


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


class SATSchoolResolverTests(SimpleTestCase):
    """Tests for mapping SAT institutions to district school short names."""

    def test_canonical_forms_match(self):
        from dataLoader.utils.sat_school_resolver import _canonical
        self.assertEqual(_canonical('INDIO HIGH SCHOOL'), _canonical('Indio HS'))
        self.assertEqual(_canonical('SUMMIT HIGH SCHOOL'), _canonical('Summit HS'))

    def test_resolve_school_short_name(self):
        from unittest.mock import patch
        from dataLoader.utils import sat_school_resolver as resolver

        by_name = {
            resolver._canonical('Indio HS'): 'Indio HS',
            resolver._normalize('Palm Desert HS'): 'Palm Desert HS',
        }
        by_code = {'33670583330669': 'Indio HS'}
        with patch.object(resolver, '_get_lookup', return_value=(by_name, by_code)):
            self.assertEqual(
                resolver.resolve_school_short_name('INDIO HIGH SCHOOL'), 'Indio HS')
            self.assertEqual(
                resolver.resolve_school_short_name('', '33670583330669'), 'Indio HS')
            self.assertEqual(
                resolver.resolve_school_short_name('Some Other Academy'),
                resolver.NON_DS_SCHOOL)
