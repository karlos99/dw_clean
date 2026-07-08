from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import signing
import csv
import io

from studata.models import CurrentStudent
from users.models import Role
from desi.models import (
    Accommodation,
    AccommodationOption,
    Assessment,
    AssessmentCriteria,
    DesiExportConfiguration,
    StudentAccommodation,
    StudentAssessmentCompletion,
)
from desi.views import check_criteria, EXPORT_ENDPOINT_SIGNING_SALT

User = get_user_model()


class DesiModelsAndEligibilityTests(TestCase):

    def setUp(self):
        # Create standard accommodation
        self.acc_tts = Accommodation.objects.create(
            name="Text-to-Speech",
            code="TTS",
            category="Presentation",
            question_text="Does the student need Text-to-Speech?"
        )
        self.opt_tts = AccommodationOption.objects.create(
            accommodation=self.acc_tts,
            name="Standard",
            code="TTS"
        )

        self.acc_et = Accommodation.objects.create(
            name="Extra Time 1.5x",
            code="ET1.5",
            category="Timing"
        )
        self.opt_et = AccommodationOption.objects.create(
            accommodation=self.acc_et,
            name="Standard",
            code="1.5X"
        )

        # Create assessment
        self.assessment = Assessment.objects.create(
            name="Smarter Balanced Math",
            subject="Math",
            school_year="2025-2026",
            is_active=True
        )
        self.assessment.supported_options.add(self.opt_tts, self.opt_et)

        # Setup criteria: SPED == 'Y' AND Grade in '03,04,05'
        self.criterion_sped = AssessmentCriteria.objects.create(
            assessment=self.assessment,
            field_name="sped",
            operator="is",
            value="Y"
        )
        self.criterion_grade = AssessmentCriteria.objects.create(
            assessment=self.assessment,
            field_name="grade",
            operator="in",
            value="03,04,05"
        )

        # Create students
        self.student_eligible = CurrentStudent.objects.create(
            stu_annual_key="eligible_key",
            ssid="ssid123",
            student_id="local123",
            student_name="Eligible Student",
            grade="03",
            sped="Y",
            school_year="2025-2026"
        )

        self.student_ineligible_grade = CurrentStudent.objects.create(
            stu_annual_key="ineligible_grade_key",
            ssid="ssid456",
            student_id="local456",
            student_name="Ineligible Grade",
            grade="06",
            sped="Y",
            school_year="2025-2026"
        )

        self.student_ineligible_sped = CurrentStudent.objects.create(
            stu_annual_key="ineligible_sped_key",
            ssid="ssid789",
            student_id="local789",
            student_name="Ineligible SPED",
            grade="03",
            sped="N",
            school_year="2025-2026"
        )

    def test_dynamic_criteria_evaluation(self):
        # 1. Eligible student tests
        eligible, failed = check_criteria(self.assessment.criteria.all(), self.student_eligible)
        self.assertTrue(eligible)
        self.assertEqual(len(failed), 0)

        # 2. Ineligible grade tests
        eligible, failed = check_criteria(self.assessment.criteria.all(), self.student_ineligible_grade)
        self.assertFalse(eligible)
        self.assertEqual(len(failed), 1)
        self.assertIn("Grade Level", failed[0])

        # 3. Ineligible SPED tests
        eligible, failed = check_criteria(self.assessment.criteria.all(), self.student_ineligible_sped)
        self.assertFalse(eligible)
        self.assertEqual(len(failed), 1)
        self.assertIn("Special Education", failed[0])


class DesiWorkflowAndViewsTests(TestCase):

    def setUp(self):
        # Roles setup
        self.admin_role = Role.objects.create(name="Project Admin")
        self.teacher_role = Role.objects.create(name="Teacher")

        # Users setup
        self.admin_user = User.objects.create_user(
            email="admin@test.com",
            username="admin@test.com",
            password="password123"
        )
        self.admin_user.roles.add(self.admin_role)

        self.teacher_user = User.objects.create_user(
            email="teacher@test.com",
            username="teacher@test.com",
            password="password123"
        )
        self.teacher_user.roles.add(self.teacher_role)

        # Assessment setup
        self.assessment_math = Assessment.objects.create(
            name="SBAC Math",
            school_year="2025-2026",
            is_active=True
        )
        self.assessment_ela = Assessment.objects.create(
            name="SBAC ELA",
            school_year="2025-2026",
            is_active=True
        )

        # Accommodation setup
        self.acc = Accommodation.objects.create(
            name="Text-to-Speech", 
            code="TTS",
            question_text="Does the student need Text-to-Speech?"
        )
        self.opt = AccommodationOption.objects.create(accommodation=self.acc, name="Standard", code="TTS")
        self.assessment_math.supported_options.add(self.opt)
        self.assessment_ela.supported_options.add(self.opt)

        # Student setup
        self.student = CurrentStudent.objects.create(
            stu_annual_key="student_key",
            ssid="ssid111",
            student_id="local111",
            student_name="Test Student",
            grade="04",
            school_year="2025-2026"
        )

    def test_accommodation_creation_support_mapping(self):
        self.client.force_login(self.admin_user)
        create_url = reverse('desi:create_accommodation')
        
        # Create Accommodation and check ELA and Math assessments with custom options
        post_data = {
            'name': 'Calculator',
            'code': 'CALC',
            'category': 'Response',
            'description': 'Graphing/Scientific calculator helper.',
            'question_text': 'Does the student require a calculator?',
            'bulk_options': 'Scientific, Graphing',
            'supported_assessments': [self.assessment_math.id, self.assessment_ela.id],
            f'assessment_opts_custom_{self.assessment_math.id}': 'Scientific Only, Basic 4-Function',
            f'assessment_opts_custom_{self.assessment_ela.id}': 'Graphing Only'
        }
        
        response = self.client.post(create_url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify options are created (2 master + 3 custom = 5 unique options)
        calc = Accommodation.objects.get(name='Calculator')
        self.assertEqual(calc.options.count(), 5)

        # Verify Math supports the correct custom options
        opt_sci_only = AccommodationOption.objects.get(accommodation=calc, name='Scientific Only')
        opt_basic = AccommodationOption.objects.get(accommodation=calc, name='Basic 4-Function')
        opt_grp_only = AccommodationOption.objects.get(accommodation=calc, name='Graphing Only')

        self.assertIn(opt_sci_only, self.assessment_math.supported_options.all())
        self.assertIn(opt_basic, self.assessment_math.supported_options.all())
        self.assertNotIn(opt_grp_only, self.assessment_math.supported_options.all())

        self.assertIn(opt_grp_only, self.assessment_ela.supported_options.all())
        self.assertNotIn(opt_sci_only, self.assessment_ela.supported_options.all())

    def test_assign_accommodations_post_with_assessment_completion(self):
        self.client.force_login(self.admin_user)
        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])
        
        # Check Standard ELA option and mark Math as completed but ELA as not completed
        post_data = {
            'student_options': [self.opt.id],
            'completed_assessments': [self.assessment_math.id]
        }
        
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify global option is saved
        student_acc = StudentAccommodation.objects.get(student_annual_key=self.student.stu_annual_key)
        self.assertIn(self.opt, student_acc.options.all())

        # Verify completion records
        comp_math = StudentAssessmentCompletion.objects.get(student_annual_key=self.student.stu_annual_key, assessment=self.assessment_math)
        self.assertTrue(comp_math.is_completed)

        comp_ela = StudentAssessmentCompletion.objects.get(student_annual_key=self.student.stu_annual_key, assessment=self.assessment_ela)
        self.assertFalse(comp_ela.is_completed)

    def test_assign_accommodations_htmx_post_returns_updated_sidebar_row(self):
        self.client.force_login(self.admin_user)
        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])

        post_data = {
            'student_options': [self.opt.id],
            'completed_assessments': [self.assessment_math.id, self.assessment_ela.id]
        }

        response = self.client.post(url, post_data, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="student-list-item-student_key"')
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, 'Completed (2/2)')

    def test_shared_accommodations_grouping_on_workspace(self):
        self.client.force_login(self.admin_user)
        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])

        # Since TTS has the same option 'Standard' in ELA and Math, it should be listed in shared_accommodations
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        shared_accs = response.context['shared_accommodations']
        self.assertEqual(len(shared_accs), 1)
        self.assertEqual(shared_accs[0]['accommodation'], self.acc)
        self.assertIn(self.assessment_math, shared_accs[0]['applies_to'])
        self.assertIn(self.assessment_ela, shared_accs[0]['applies_to'])

        # Since there are no unique/test-specific accommodations, assessment cards should show no unique accommodations
        assessment_cards = response.context['assessment_cards']
        self.assertEqual(len(assessment_cards), 2)
        for card in assessment_cards:
            self.assertEqual(len(card['accommodations']), 0)

    def test_create_accommodation_fails_when_no_assessments_exist(self):
        self.client.force_login(self.admin_user)
        
        # Delete all assessments
        Assessment.objects.all().delete()
        
        create_url = reverse('desi:create_accommodation')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 302) # Redirect to dashboard
        
        # Verify the redirect goes to admin dashboard and shows error
        self.assertRedirects(response, reverse('desi:admin_dashboard'))

    def test_assign_accommodations_htmx_request(self):
        self.client.force_login(self.admin_user)
        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])

        # Get with HTMX headers
        response = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'desi/partials/workspace_form.html')
        self.assertTemplateNotUsed(response, 'desi/student_list.html')

    def test_edit_designated_support_option_name_and_code(self):
        self.client.force_login(self.admin_user)
        edit_url = reverse('desi:edit_accommodation', args=[self.acc.id])
        response = self.client.post(edit_url, {
            'name': self.acc.name,
            'code': self.acc.code,
            'category': self.acc.category,
            'description': self.acc.description,
            'video_url': '',
            'order': str(self.acc.order),
            'option_id[]': [str(self.opt.id)],
            'option_name[]': ['Read Aloud'],
            'option_code[]': ['RA'],
            'supported_assessments': [str(self.assessment_math.id), str(self.assessment_ela.id)],
            f'assessment_opts_{self.assessment_math.id}': ['Read Aloud'],
            f'assessment_opts_{self.assessment_ela.id}': ['Read Aloud'],
        })
        self.assertEqual(response.status_code, 302)
        self.opt.refresh_from_db()
        self.assertEqual(self.opt.name, 'Read Aloud')
        self.assertEqual(self.opt.code, 'RA')

    def test_create_designated_support_with_dependency(self):
        self.client.force_login(self.admin_user)
        parent_support = Accommodation.objects.create(name="Read Aloud", code="RA")
        parent_option = AccommodationOption.objects.create(
            accommodation=parent_support,
            name="Student Reads Independently",
            code="NO_RA"
        )

        create_url = reverse('desi:create_accommodation')
        response = self.client.post(create_url, {
            'name': 'Human Reader',
            'code': 'HR',
            'category': 'Presentation',
            'description': 'Human reader support',
            'bulk_options': 'Standard',
            'depends_on_accommodation': str(parent_support.id),
            'depends_on_option': str(parent_option.id),
        })
        self.assertEqual(response.status_code, 302)

        created = Accommodation.objects.get(name='Human Reader')
        self.assertEqual(created.depends_on_accommodation_id, parent_support.id)
        self.assertEqual(created.depends_on_option_id, parent_option.id)

    def test_dependency_selection_hides_and_removes_saved_entry(self):
        self.client.force_login(self.admin_user)
        trigger_support = Accommodation.objects.create(name="Read Aloud", code="RA")
        trigger_option = AccommodationOption.objects.create(
            accommodation=trigger_support,
            name="Student Reads Independently",
            code="NO_RA"
        )
        dependent_support = Accommodation.objects.create(
            name="Human Reader",
            code="HR",
            depends_on_accommodation=trigger_support,
            depends_on_option=trigger_option,
        )
        dependent_option = AccommodationOption.objects.create(
            accommodation=dependent_support,
            name="Standard",
            code="HR_STD"
        )
        self.assessment_math.supported_options.add(trigger_option, dependent_option)

        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])
        response = self.client.post(url, {
            'student_options': [trigger_option.id, dependent_option.id],
        })
        self.assertEqual(response.status_code, 302)

        student_acc = StudentAccommodation.objects.get(student_annual_key=self.student.stu_annual_key)
        saved_option_ids = set(student_acc.options.values_list('id', flat=True))
        self.assertIn(trigger_option.id, saved_option_ids)
        self.assertNotIn(dependent_option.id, saved_option_ids)

    def test_dependency_scoped_to_some_assessments_does_not_remove_global_entry(self):
        self.client.force_login(self.admin_user)
        trigger_support = Accommodation.objects.create(name="Read Aloud Scoped", code="RAS")
        trigger_option = AccommodationOption.objects.create(
            accommodation=trigger_support,
            name="Independent",
            code="IND"
        )
        dependent_support = Accommodation.objects.create(
            name="Human Reader Scoped",
            code="HRS",
            depends_on_accommodation=trigger_support,
            depends_on_option=trigger_option,
        )
        dependent_support.dependency_assessments.add(self.assessment_math)
        dependent_option = AccommodationOption.objects.create(
            accommodation=dependent_support,
            name="Standard",
            code="STD"
        )
        self.assessment_math.supported_options.add(trigger_option, dependent_option)
        self.assessment_ela.supported_options.add(dependent_option)

        url = reverse('desi:assign_accommodations', args=[self.student.stu_annual_key])
        response = self.client.post(url, {
            'student_options': [trigger_option.id, dependent_option.id],
        })
        self.assertEqual(response.status_code, 302)

        student_acc = StudentAccommodation.objects.get(student_annual_key=self.student.stu_annual_key)
        saved_option_ids = set(student_acc.options.values_list('id', flat=True))
        self.assertIn(trigger_option.id, saved_option_ids)
        self.assertIn(dependent_option.id, saved_option_ids)

    def test_export_data_respects_column_rename_and_order(self):
        self.client.force_login(self.admin_user)
        export_url = reverse('desi:export_data')

        post_data = {
            'school_year': '2025-2026',
            'assessment_ids': [str(self.assessment_math.id)],
            'include__student_name': 'on',
            'header__student_name': 'Learner Name',
            'order__student_name': '2',
            'include__assessment_name': 'on',
            'header__assessment_name': 'Assessment',
            'order__assessment_name': '1',
        }
        # include defaults for non-selected columns
        for key, label, _group in [
            ('student_id', 'Local Student ID', 'student'),
            ('ssid', 'SSID', 'student'),
            ('stu_annual_key', 'Student Annual Key', 'student'),
            ('grade', 'Grade', 'student'),
            ('school_name', 'School', 'student'),
            ('school_year', 'School Year', 'student'),
            ('assessment_subject', 'Assessment Subject', 'support'),
            ('assessment_school_year', 'Assessment School Year', 'support'),
            ('assessment_completed', 'Assessment Completed', 'support'),
            ('support_name', 'Designated Support', 'support'),
            ('support_code', 'Support Code', 'support'),
            ('selected_option_name', 'Selected Option', 'support'),
            ('selected_option_code', 'Selected Option Code', 'support'),
            ('support_selected', 'Support Selected', 'support'),
        ]:
            post_data[f'header__{key}'] = label
            post_data[f'order__{key}'] = '99'

        response = self.client.post(export_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))

        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0], ['Assessment', 'Learner Name'])
        self.assertEqual(rows[1][1], 'Test Student')

    def test_export_configuration_can_be_saved_and_reused(self):
        self.client.force_login(self.admin_user)
        export_url = reverse('desi:export_data')

        response = self.client.post(export_url, {
            'school_year': '2025-2026',
            'save_configuration': '1',
            'save_configuration_name': 'Math export',
            'save_configuration_description': 'Math assessment export',
            'assessment_ids': [str(self.assessment_math.id)],
            'support_value_field': 'code',
            'only_with_supports': 'on',
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            'header__assessment_name': 'Assessment',
            'order__assessment_name': '2',
            'header__student_id': 'Local Student ID',
            'order__student_id': '99',
            'header__ssid': 'SSID',
            'order__ssid': '99',
            'header__stu_annual_key': 'Student Annual Key',
            'order__stu_annual_key': '99',
            'header__grade': 'Grade',
            'order__grade': '99',
            'header__school_name': 'School',
            'order__school_name': '99',
            'header__school_year': 'School Year',
            'order__school_year': '99',
            'header__assessment_subject': 'Assessment Subject',
            'order__assessment_subject': '99',
            'header__assessment_school_year': 'Assessment School Year',
            'order__assessment_school_year': '99',
            'header__assessment_completed': 'Assessment Completed',
            'order__assessment_completed': '99',
            'header__support_name': 'Designated Support',
            'order__support_name': '99',
            'header__support_code': 'Support Code',
            'order__support_code': '99',
            'header__selected_option_name': 'Selected Option',
            'order__selected_option_name': '99',
            'header__selected_option_code': 'Selected Option Code',
            'order__selected_option_code': '99',
            'header__support_selected': 'Support Selected',
            'order__support_selected': '99',
        })
        self.assertEqual(response.status_code, 200)
        config = DesiExportConfiguration.objects.get(owner=self.admin_user, name='Math export')
        self.assertEqual(config.config['school_year'], '2025-2026')
        self.assertEqual(config.config['support_value_field'], 'code')
        self.assertTrue(config.config['only_with_supports'])
        self.assertEqual(config.config['assessment_ids'], [self.assessment_math.id])

        load_response = self.client.get(export_url, {'config_id': config.id})
        self.assertEqual(load_response.status_code, 200)
        self.assertEqual(load_response.context['school_year'], '2025-2026')
        self.assertEqual(load_response.context['support_value_field'], 'code')
        self.assertTrue(load_response.context['only_with_supports'])
        self.assertEqual(load_response.context['selected_assessment_ids'], {self.assessment_math.id})

    def test_export_data_multiple_assessments_creates_multiple_rows(self):
        self.client.force_login(self.admin_user)
        export_url = reverse('desi:export_data')
        response = self.client.post(export_url, {
            'school_year': '2025-2026',
            'assessment_ids': [str(self.assessment_math.id), str(self.assessment_ela.id)],
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            'include__assessment_name': 'on',
            'header__assessment_name': 'Assessment',
            'order__assessment_name': '2',
            'header__student_id': 'Local Student ID',
            'order__student_id': '99',
            'header__ssid': 'SSID',
            'order__ssid': '99',
            'header__stu_annual_key': 'Student Annual Key',
            'order__stu_annual_key': '99',
            'header__grade': 'Grade',
            'order__grade': '99',
            'header__school_name': 'School',
            'order__school_name': '99',
            'header__school_year': 'School Year',
            'order__school_year': '99',
            'header__assessment_subject': 'Assessment Subject',
            'order__assessment_subject': '99',
            'header__assessment_school_year': 'Assessment School Year',
            'order__assessment_school_year': '99',
            'header__assessment_completed': 'Assessment Completed',
            'order__assessment_completed': '99',
            'header__support_name': 'Designated Support',
            'order__support_name': '99',
            'header__support_code': 'Support Code',
            'order__support_code': '99',
            'header__selected_option_name': 'Selected Option',
            'order__selected_option_name': '99',
            'header__selected_option_code': 'Selected Option Code',
            'order__selected_option_code': '99',
            'header__support_selected': 'Support Selected',
            'order__support_selected': '99',
        })
        self.assertEqual(response.status_code, 200)
        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        assessment_values = [r[1] for r in rows[1:]]
        self.assertIn('SBAC Math', assessment_values)
        self.assertIn('SBAC ELA', assessment_values)

    def test_export_data_supports_are_columns(self):
        self.client.force_login(self.admin_user)
        export_url = reverse('desi:export_data')
        support_col_key = f'support__{self.acc.id}'
        response = self.client.post(export_url, {
            'school_year': '2025-2026',
            'assessment_ids': [str(self.assessment_math.id)],
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            f'include__{support_col_key}': 'on',
            f'header__{support_col_key}': 'TTS Support',
            f'order__{support_col_key}': '2',
            'header__assessment_name': 'Assessment Name',
            'order__assessment_name': '99',
            'header__student_id': 'Local Student ID',
            'order__student_id': '99',
            'header__ssid': 'SSID',
            'order__ssid': '99',
            'header__stu_annual_key': 'Student Annual Key',
            'order__stu_annual_key': '99',
            'header__grade': 'Grade',
            'order__grade': '99',
            'header__school_name': 'School',
            'order__school_name': '99',
            'header__school_year': 'School Year',
            'order__school_year': '99',
            'header__assessment_subject': 'Assessment Subject',
            'order__assessment_subject': '99',
            'header__assessment_school_year': 'Assessment School Year',
            'order__assessment_school_year': '99',
            'header__assessment_completed': 'Assessment Completed',
            'order__assessment_completed': '99',
        })
        self.assertEqual(response.status_code, 200)
        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        self.assertEqual(rows[0], ['Student', 'TTS Support'])

    def test_export_data_support_columns_can_use_option_code(self):
        self.client.force_login(self.admin_user)
        StudentAccommodation.objects.create(student_annual_key=self.student.stu_annual_key).options.add(self.opt)
        export_url = reverse('desi:export_data')
        support_col_key = f'support__{self.acc.id}'
        response = self.client.post(export_url, {
            'school_year': '2025-2026',
            'support_value_field': 'code',
            'assessment_ids': [str(self.assessment_math.id)],
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            f'include__{support_col_key}': 'on',
            f'header__{support_col_key}': 'TTS Code',
            f'order__{support_col_key}': '2',
            'header__assessment_name': 'Assessment Name',
            'order__assessment_name': '99',
            'header__student_id': 'Local Student ID',
            'order__student_id': '99',
            'header__ssid': 'SSID',
            'order__ssid': '99',
            'header__stu_annual_key': 'Student Annual Key',
            'order__stu_annual_key': '99',
            'header__grade': 'Grade',
            'order__grade': '99',
            'header__school_name': 'School',
            'order__school_name': '99',
            'header__school_year': 'School Year',
            'order__school_year': '99',
            'header__assessment_subject': 'Assessment Subject',
            'order__assessment_subject': '99',
            'header__assessment_school_year': 'Assessment School Year',
            'order__assessment_school_year': '99',
            'header__assessment_completed': 'Assessment Completed',
            'order__assessment_completed': '99',
        })
        self.assertEqual(response.status_code, 200)
        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        self.assertEqual(rows[0], ['Student', 'TTS Code'])
        self.assertEqual(rows[1][1], 'TTS')

    def test_export_data_only_with_supports_skips_blank_rows(self):
        self.client.force_login(self.admin_user)
        StudentAccommodation.objects.create(student_annual_key=self.student.stu_annual_key).options.add(self.opt)
        CurrentStudent.objects.create(
            stu_annual_key="blank_student_key",
            ssid="blankssid",
            student_id="blankid",
            student_name="Blank Student",
            grade="04",
            school_year="2025-2026"
        )

        export_url = reverse('desi:export_data')
        response = self.client.post(export_url, {
            'school_year': '2025-2026',
            'only_with_supports': 'on',
            'assessment_ids': [str(self.assessment_math.id), str(self.assessment_ela.id)],
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            'include__assessment_name': 'on',
            'header__assessment_name': 'Assessment',
            'order__assessment_name': '2',
            'header__student_id': 'Local Student ID',
            'order__student_id': '99',
            'header__ssid': 'SSID',
            'order__ssid': '99',
            'header__stu_annual_key': 'Student Annual Key',
            'order__stu_annual_key': '99',
            'header__grade': 'Grade',
            'order__grade': '99',
            'header__school_name': 'School',
            'order__school_name': '99',
            'header__school_year': 'School Year',
            'order__school_year': '99',
            'header__assessment_subject': 'Assessment Subject',
            'order__assessment_subject': '99',
            'header__assessment_school_year': 'Assessment School Year',
            'order__assessment_school_year': '99',
            'header__assessment_completed': 'Assessment Completed',
            'order__assessment_completed': '99',
            f'header__support__{self.acc.id}': 'TTS',
            f'order__support__{self.acc.id}': '3',
        })
        self.assertEqual(response.status_code, 200)
        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        exported_students = [r[0] for r in rows[1:]]
        self.assertIn('Test Student', exported_students)
        self.assertNotIn('Blank Student', exported_students)

    def test_export_endpoint_requires_valid_auth_token(self):
        endpoint_url = reverse('desi:export_data_endpoint')
        response = self.client.get(endpoint_url)
        self.assertEqual(response.status_code, 403)

    def test_export_endpoint_returns_csv_with_token_auth(self):
        endpoint_url = reverse('desi:export_data_endpoint')
        token = signing.dumps(
            {'uid': self.admin_user.id, 'purpose': 'desi_export'},
            salt=EXPORT_ENDPOINT_SIGNING_SALT
        )

        response = self.client.get(endpoint_url, {
            'auth': token,
            'school_year': '2025-2026',
            'assessment_ids': [str(self.assessment_math.id)],
            'include__student_name': 'on',
            'header__student_name': 'Student',
            'order__student_name': '1',
            'include__assessment_name': 'on',
            'header__assessment_name': 'Assessment',
            'order__assessment_name': '2',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        reader = csv.reader(io.StringIO(response.content.decode('utf-8')))
        rows = list(reader)
        self.assertEqual(rows[0], ['Student', 'Assessment'])
