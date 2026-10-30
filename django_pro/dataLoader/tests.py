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


SAMPLE_IB_CSV = (
    '"Year","Month","School","Registration number","Personal code","Name","Category","","","","","","EE/TOK points:","Total points:","Result:","Diploma requirements code:"\n'
    '"Year","Month","School","Registration number","Personal code","Name","Category","Subject","Level","Language","Predicted grade","Grade","","","",""\n'
    '"2024","MAY","001237","0001","knp351","Doe, John","DIPLOMA","","","","","","3","34","Diploma awarded","1"\n'
    '"2024","MAY","001237","0001","knp351","Doe, John","DIPLOMA","ENGLISH A: Literature","HL","ENGLISH","5","5"\n'
    '"2024","MAY","001237","0001","knp351","Doe, John","DIPLOMA","SPANISH B","SL","SPANISH","6","6"\n'
    '"2024","MAY","001237","0001","knp351","Doe, John","DIPLOMA","HISTORY","HL","ENGLISH","5","4"\n'
    '"2024","MAY","001237","0002","knp352","Smith, Jane","COURSE","","","","","","","","",""\n'
    '"2024","MAY","001237","0002","knp352","Smith, Jane","COURSE","MATHEMATICS","SL","ENGLISH","4","4"\n'
)


class IBColumnAnalyzerTests(SimpleTestCase):
    """Tests for parsing, normalizing, and analyzing IB Candidate Report CSVs."""

    def test_normalize_ib_csv_content_merges_split_lines(self):
        from dataLoader.utils.ib_column_analyzer import normalize_ib_csv_content
        normalized = normalize_ib_csv_content(SAMPLE_IB_CSV)
        lines = [line for line in normalized.splitlines() if line.strip()]
        # Header rows 0 and 1 are merged into a single header row
        self.assertIn('EE/TOK points:', lines[0])
        self.assertIn('Subject', lines[0])
        self.assertIn('Grade', lines[0])
        # First candidate line contains both subject and summary points
        first_data = lines[1]
        self.assertIn('Doe, John', first_data)
        self.assertIn('ENGLISH A: Literature', first_data)
        self.assertIn('Diploma awarded', first_data)

    def test_analyze_ib_csv(self):
        from dataLoader.utils.ib_column_analyzer import analyze_ib_csv
        result = analyze_ib_csv(SAMPLE_IB_CSV)
        self.assertEqual(result.total_candidates, 2)
        self.assertEqual(result.total_results, 4)
        self.assertEqual(result.school_code, '001237')
        self.assertIn('ENGLISH A: Literature', result.unique_subjects)
        self.assertIn('SPANISH B', result.unique_subjects)
        self.assertIn('MATHEMATICS', result.unique_subjects)
        self.assertIn('HL', result.unique_levels)
        self.assertIn('SL', result.unique_levels)
        self.assertIn('DIPLOMA', result.unique_categories)
        self.assertIn('COURSE', result.unique_categories)
        self.assertEqual(len(result.sample_candidates), 2)
        cand1 = result.sample_candidates[0]
        self.assertEqual(cand1.name, 'Doe, John')
        self.assertEqual(cand1.total_points, '34')
        self.assertEqual(cand1.result, 'Diploma awarded')
        self.assertEqual(cand1.subject_count, 3)

    def test_parse_ib_csv_rows(self):
        from dataLoader.utils.ib_column_analyzer import parse_ib_csv_rows
        candidates, results, header_count = parse_ib_csv_rows(SAMPLE_IB_CSV)
        self.assertEqual(header_count, 1)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len(results), 4)

        doe = candidates[0]
        self.assertEqual(doe['personal_code'], 'knp351')
        self.assertEqual(doe['name'], 'Doe, John')
        self.assertEqual(doe['category'], 'DIPLOMA')
        self.assertEqual(doe['ee_tok_points'], '3')
        self.assertEqual(doe['total_points'], '34')
        self.assertEqual(doe['result'], 'Diploma awarded')
        self.assertEqual(len(doe['subjects']), 3)

        first_res = results[0]
        self.assertEqual(first_res['candidate_name'], 'Doe, John')
        self.assertEqual(first_res['subject'], 'ENGLISH A: Literature')
        self.assertEqual(first_res['level'], 'HL')
        self.assertEqual(first_res['grade'], '5')
        self.assertEqual(first_res['ee_tok_points'], '3')
        self.assertEqual(first_res['total_points'], '34')
        self.assertEqual(first_res['result_code'], 'Diploma awarded')
        self.assertEqual(first_res['session_year'], '2024')
        self.assertEqual(first_res['session_month'], 'MAY')


class IBColumnUtilsTests(SimpleTestCase):
    """Tests for IB column key generation and parsing."""

    def test_ib_column_key_generation(self):
        from dataexploration.column_utils import ib_column_key
        # Subject-level key without level in key
        key_pl = ib_column_key('2023-2024', 'English A LAL', 'PL')
        self.assertEqual(key_pl, 'ib_2023_2024_English_A_LAL_PL')
        key_cat = ib_column_key('2023-2024', 'Spanish B', kind='CAT')
        self.assertEqual(key_cat, 'ib_2023_2024_Spanish_B_CAT')
        key_lvl = ib_column_key('2023-2024', 'Math Apps', kind='LVL')
        self.assertEqual(key_lvl, 'ib_2023_2024_Math_Apps_LVL')

        # Legacy key with level embedded
        legacy_key = ib_column_key('2023-2024', 'English A LAL', 'HL', 'PL')
        self.assertEqual(legacy_key, 'ib_2023_2024_English_A_LAL_HL_PL')

    def test_parse_ib_column_key(self):
        from dataexploration.column_utils import parse_ib_column_key
        # Subject-level key without level
        parsed = parse_ib_column_key('ib_2023_2024_English_A_LAL_PL')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['year'], '2023-2024')
        self.assertEqual(parsed['subj_slug'], 'English_A_LAL')
        self.assertIsNone(parsed['level'])
        self.assertEqual(parsed['kind'], 'PL')

        # Legacy key with level
        parsed_legacy = parse_ib_column_key('ib_2023_2024_English_A_LAL_HL_PL')
        self.assertIsNotNone(parsed_legacy)
        self.assertEqual(parsed_legacy['year'], '2023-2024')
        self.assertEqual(parsed_legacy['subj_slug'], 'English_A_LAL')
        self.assertEqual(parsed_legacy['level'], 'HL')
        self.assertEqual(parsed_legacy['kind'], 'PL')

        # Invalid key returns None
        self.assertIsNone(parse_ib_column_key('sat_2023_total'))
        self.assertIsNone(parse_ib_column_key('not_a_valid_ib_key'))


class IBStudentMatcherTests(TestCase):
    """Tests for matching IB candidates to students enrolled at La Quinta High School."""

    def setUp(self):
        from studata.models import StudentEnroll
        # LQHS student
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_doe_john',
            ssid='1000000001',
            student_id='ID001',
            first_name='John',
            last_name='Doe',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )
        # LQHS student with suffix
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_smith_jane',
            ssid='1000000002',
            student_id='ID002',
            first_name='Jane',
            last_name='Smith Jr',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )
        # LQHS student with compound last name
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_arvizu_isabella',
            ssid='1000000006',
            student_id='ID006',
            first_name='Isabella',
            last_name='Arvizu Mondragon',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )
        # LQHS student who goes by middle name as preferred first name
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_valdovinos_lili',
            ssid='1000000007',
            student_id='ID007',
            first_name='Liliana',
            middle_name='Arela',
            last_name='Valdovinos',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )
        # LQHS student with 'Jr' in candidate last name
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_jones_zakee',
            ssid='1000000008',
            student_id='ID008',
            first_name='Zakee Asin',
            last_name='Jones',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )
        # Non-LQHS student with same name
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_doe_indio',
            ssid='1000000003',
            student_id='ID003',
            first_name='John',
            last_name='Doe',
            school_name='Indio High School',
            state_school_code='3330668',
            school_year='2023-2024',
        )
        # LQHS student in different year
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_garcia_carlos',
            ssid='1000000004',
            student_id='ID004',
            first_name='Carlos',
            last_name='Garcia',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2022-2023',
        )

    def test_exact_name_matching_lqhs(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        match = matcher.match('knp351', 'Doe, John')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000001')
        self.assertEqual(match.student_name, 'Doe, John')
        self.assertIn(match.match_method, ('exact_full', 'exact_last_first'))
        self.assertGreaterEqual(match.confidence, 0.95)

    def test_candidate_name_without_comma_first_part_is_last(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        # Candidate name entered as 'DOE JOHN' without comma -> first part is last name
        match = matcher.match('knp351', 'DOE JOHN')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000001')

    def test_suffix_stripping_match(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        match = matcher.match('knp352', 'Smith, Jane')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000002')
        self.assertEqual(match.student_name, 'Smith Jr, Jane')

    def test_suffix_in_candidate_last_name(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        # Suffix attached to candidate last name: 'Jones Jr, Zakee Asin'
        match = matcher.match('knp358', 'Jones Jr, Zakee Asin')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000008')
        self.assertEqual(match.student_name, 'Jones, Zakee Asin')

    def test_compound_last_name_match(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        # Candidate has partial or full compound last name: 'Arvizu, Isabella' vs 'Arvizu Mondragon, Isabella'
        match = matcher.match('knp356', 'Arvizu, Isabella')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000006')

    def test_middle_name_as_preferred_first_name(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        # Student enrolled as 'Valdovinos, Liliana Arela' but candidate registered as 'Valdovinos, Arela'
        match = matcher.match('knp357', 'Valdovinos, Arela')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000007')
        self.assertEqual(match.match_method, 'middle_as_first_name')

    def test_non_lqhs_student_not_matched(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        from studata.models import StudentEnroll
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_miller_alice',
            ssid='1000000005',
            student_id='ID005',
            first_name='Alice',
            last_name='Miller',
            school_name='Indio High School',
            state_school_code='3330668',
            school_year='2023-2024',
        )
        matcher = IBStudentMatcher('2023-2024')
        match = matcher.match('knp353', 'Miller, Alice')
        self.assertFalse(match.matched)
        self.assertEqual(match.ssid, '')

    def test_wrong_year_not_matched(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        matcher = IBStudentMatcher('2023-2024')
        # Carlos Garcia is only enrolled in 2022-2023
        match = matcher.match('knp354', 'Garcia, Carlos')
        self.assertFalse(match.matched)
        self.assertEqual(match.ssid, '')

    def test_manual_override(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        overrides = {'knp351': '1000000099'}
        matcher = IBStudentMatcher('2023-2024', manual_overrides=overrides)
        match = matcher.match('knp351', 'Doe, John')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '1000000099')
        self.assertEqual(match.match_method, 'manual_override')
        self.assertEqual(match.confidence, 1.0)

    def test_global_search_across_schools(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        from studata.models import StudentEnroll

        StudentEnroll.objects.create(
            stu_enrollment_key='enr_global_test',
            ssid='9990001112',
            student_id='ID_GLOBAL',
            first_name='Maria',
            last_name='Rodriguez-Alonso',
            school_name='Indio High School',
            school_year='2022-2023',
            grade='11',
        )

        matcher = IBStudentMatcher('2023-2024')
        # Local search (LQHS 2023-2024) does not find Maria
        local_results = matcher.search_students(query='Rodriguez-Alonso')
        self.assertEqual(len(local_results), 0)

        # Global search finds Maria at Indio High School
        global_results = matcher.search_students_global(query='Rodriguez-Alonso')
        self.assertGreaterEqual(len(global_results), 1)
        self.assertEqual(global_results[0]['ssid'], '9990001112')
        self.assertEqual(global_results[0]['school_name'], 'Indio High School')

    def test_manual_override_by_name(self):
        from dataLoader.utils.ib_student_matcher import IBStudentMatcher
        from studata.models import StudentEnroll

        StudentEnroll.objects.create(
            stu_enrollment_key='enr_name_override',
            ssid='8880002221',
            student_id='ID_NAME_OVER',
            first_name='Lucas',
            last_name='Skywalker',
            school_name='Shadow Hills High',
            school_year='2021-2022',
            grade='12',
        )

        # User passes student name instead of SSID as override
        overrides = {'knp999': 'Skywalker, Lucas'}
        matcher = IBStudentMatcher('2023-2024', manual_overrides=overrides)
        match = matcher.match('knp999', 'Skywalker, Luke')
        self.assertTrue(match.matched)
        self.assertEqual(match.ssid, '8880002221')
        self.assertEqual(match.match_method, 'manual_override')


class IBResponseLoaderTests(TestCase):
    """Tests for loading IB CSV results into database models."""

    def setUp(self):
        from studata.models import StudentEnroll
        StudentEnroll.objects.create(
            stu_enrollment_key='enr_doe_john',
            ssid='1000000001',
            student_id='ID001',
            first_name='John',
            last_name='Doe',
            school_name='La Quinta HS',
            state_school_code='3330669',
            school_year='2023-2024',
        )

    def test_load_ib_csv(self):
        from dataLoader.models import IBImportLog, IBExamResult, IBImportStatus
        from dataLoader.utils.ib_response_loader import IBResponseLoader

        import_log = IBImportLog.objects.create(
            name='Test Import',
            filename='test_ib.csv',
            school_year='2023-2024',
            status=IBImportStatus.PENDING,
        )

        loader = IBResponseLoader(import_log)
        loader.load_ib_data(SAMPLE_IB_CSV)

        import_log.refresh_from_db()
        self.assertEqual(import_log.status, IBImportStatus.COMPLETED)
        self.assertEqual(import_log.total_candidates, 2)
        # 1 record per student per year created in database
        self.assertEqual(import_log.results_created, 2)
        # 4 total exam results detected across candidate CSV rows
        self.assertEqual(import_log.results_detected, 4)
        self.assertEqual(import_log.students_matched, 1)  # Doe John matched, Smith Jane unmatched

        results = IBExamResult.objects.filter(import_log=import_log).order_by('candidate_name')
        self.assertEqual(results.count(), 2)

        # Check matched student record
        doe_result = results.get(candidate_name='Doe, John')
        self.assertEqual(doe_result.ssid, '1000000001')
        self.assertEqual(doe_result.school_year, '2023-2024')
        self.assertEqual(doe_result.session_year, '2024')
        self.assertEqual(doe_result.session_month, 'MAY')
        self.assertEqual(doe_result.category, 'DIPLOMA')
        self.assertEqual(doe_result.total_points, '34')
        self.assertEqual(doe_result.result_code, 'Diploma awarded')

        # Check multiple results in JSON column
        self.assertIsInstance(doe_result.exam_results, list)
        self.assertEqual(len(doe_result.exam_results), 3)

        subjects = [e['subject'] for e in doe_result.exam_results]
        self.assertIn('ENGLISH A: Literature', subjects)
        self.assertIn('SPANISH B', subjects)
        self.assertIn('HISTORY', subjects)

        eng_exam = next(e for e in doe_result.exam_results if e['subject'] == 'ENGLISH A: Literature')
        self.assertEqual(eng_exam['level'], 'HL')
        self.assertEqual(eng_exam['grade'], '5')
        self.assertEqual(eng_exam['predicted_grade'], '5')

        # Check unmatched student record
        smith_result = results.get(candidate_name='Smith, Jane')
        self.assertEqual(smith_result.ssid, '')
        self.assertEqual(len(smith_result.exam_results), 1)
        self.assertEqual(smith_result.exam_results[0]['subject'], 'MATHEMATICS')
        self.assertEqual(smith_result.exam_results[0]['level'], 'SL')
        self.assertEqual(smith_result.exam_results[0]['grade'], '4')

        # Check publishing toggle
        self.assertFalse(import_log.is_published)
        import_log.is_published = True
        import_log.save()
        self.assertTrue(import_log.is_published)


