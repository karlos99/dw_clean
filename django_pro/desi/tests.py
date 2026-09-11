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
    AccommodationResource,
    Assessment,
    AssessmentCriteria,
    DesiExportConfiguration,
    DesiSchoolAccess,
    DesiSeis,
    DesiSeisDisplaySettings,
    StudentAccommodation,
    StudentAssessmentCompletion,
    SupportRule,
    SupportRuleCriteria,
)
from desi.views import (
    check_criteria,
    can_edit_desi,
    hidden_support_ids,
    EXPORT_ENDPOINT_SIGNING_SALT,
)
from desi import importers

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
            is_active=True
        )
        self.assessment_ela = Assessment.objects.create(
            name="SBAC ELA",
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

    def test_edit_support_with_comma_option_names_can_save_resources(self):
        """Regression: option names may contain commas (e.g. '...Large, Black.').
        Editing such a support (here to add a resource) must not split the names
        or fail the unique-name validation."""
        self.client.force_login(self.admin_user)
        acc = Accommodation.objects.create(name="Mouse Pointer", code="MP")
        opts = []
        for name, code in [
            ("Mouse Pointer is selected as Large, Black.", "MP_L_BLK"),
            ("Mouse Pointer is selected as Large, White.", "MP_L_WHT"),
            ("Mouse Pointer is selected as Extra Large, Black.", "MP_XL_BLK"),
        ]:
            opts.append(AccommodationOption.objects.create(
                accommodation=acc, name=name, code=code
            ))
        for ast in (self.assessment_math, self.assessment_ela):
            ast.supported_options.add(*opts)

        edit_url = reverse('desi:edit_accommodation', args=[acc.id])
        response = self.client.post(edit_url, {
            'name': acc.name,
            'code': acc.code,
            'category': acc.category,
            'description': acc.description,
            'video_url': '',
            'order': str(acc.order),
            'option_id[]': [str(o.id) for o in opts],
            'option_name[]': [o.name for o in opts],
            'option_code[]': [o.code for o in opts],
            'supported_assessments': [str(self.assessment_math.id), str(self.assessment_ela.id)],
            f'assessment_opts_{self.assessment_math.id}': [o.name for o in opts],
            f'assessment_opts_{self.assessment_ela.id}': [o.name for o in opts],
            'resource_id[]': [''],
            'resource_label[]': ['Video guide'],
            'resource_url[]': ['https://www.youtube.com/watch?v=abc123'],
            'resource_kind[]': ['youtube'],
        })
        self.assertEqual(response.status_code, 302)
        acc.refresh_from_db()
        # The three options still exist intact (not split on commas).
        self.assertEqual(acc.options.count(), 3)
        self.assertTrue(acc.options.filter(name="Mouse Pointer is selected as Large, Black.").exists())
        self.assertTrue(acc.options.filter(name="Mouse Pointer is selected as Extra Large, Black.").exists())
        # Resource was saved.
        self.assertTrue(acc.resources.filter(url="https://www.youtube.com/watch?v=abc123").exists())

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


CSV_SAMPLE = """Column,Display Name,Value,Value Definition,Content Area,Program,Setting Definition,Type
A,"Student SSID
(Example: 1234567890)",10 digits,NA,• all,•  all,This is a state-assigned student identifier.,NA
B,"Abacus (NON-EMBEDDED)
(Computer - M, S)",Blank (default),Abacus is not selected.,• math,"• Smarter math
• CAA math
• CAA science",Abacus resource text.,non-embedded accommodation
B,"Abacus (NON-EMBEDDED)
(Computer - M, S)",NEA_Abacus,Abacus is selected.,• math,"• Smarter math
• CAA math
• CAA science",Abacus resource text.,non-embedded accommodation
I,"*Color Contrast (EMBEDDED)
(Computer - E)",Blank (default),Color Contrast is not selected.,• ELA,• Smarter ELA,Color contrast text.,embedded designated support
I,"*Color Contrast (EMBEDDED)
(Computer - E)",TDS_CCInvert,Reverse Contrast,• ELA,• Smarter ELA,Color contrast text.,embedded designated support
I,"*Color Contrast (EMBEDDED)
(Computer - E)",TDS_CCMagenta,Black on Rose,• ELA,• Smarter ELA,Color contrast text.,embedded designated support
N,"*Noise Buffers (NON-EMBEDDED)
(Computer - M)",Blank (default),Noise Buffers is not selected.,• science,"• CAA Science
• CAST",Noise buffers text.,non-embedded designated support
N,"*Noise Buffers (NON-EMBEDDED)
(Computer - M)",NEDS_NoiseBuf,Noise Buffers is selected.,• science,"• CAA Science
• CAST",Noise buffers text.,non-embedded designated support
"""


class DesiCsvImporterTests(TestCase):

    def _parsed(self):
        return importers.parse_rows(csv.DictReader(io.StringIO(CSV_SAMPLE)))

    def test_parse_groups_supports_and_dedupes_assessments(self):
        parsed = self._parsed()

        # SSID (Type = NA) and "Blank (default)" rows are skipped.
        # 'CAA Science' (capital) on Noise Buffers folds into the earlier
        # 'CAA science' spelling rather than creating a new assessment.
        assessment_names = [a["name"] for a in parsed["assessments"]]
        self.assertEqual(
            assessment_names,
            ["Smarter math", "CAA math", "CAA science", "Smarter ELA", "CAST"],
        )

        self.assertEqual(len(parsed["supports"]), 3)
        by_letter = {s["column_letter"]: s for s in parsed["supports"]}
        self.assertEqual(by_letter["B"]["name"], "Abacus")
        self.assertEqual(by_letter["I"]["name"], "Color Contrast")
        self.assertEqual(by_letter["N"]["name"], "Noise Buffers")
        self.assertEqual(by_letter["N"]["category"], "non-embedded designated support")

        # "Blank (default)" rows do not become options
        self.assertEqual(len(by_letter["B"]["options"]), 1)
        self.assertEqual(by_letter["B"]["options"][0]["code"], "NEA_Abacus")
        self.assertEqual(len(by_letter["I"]["options"]), 2)
        self.assertEqual(
            [o["code"] for o in by_letter["I"]["options"]],
            ["TDS_CCInvert", "TDS_CCMagenta"],
        )

    def test_import_builds_assessments_supports_and_mappings(self):
        parsed = self._parsed()
        summary = importers.import_configuration(parsed, clear=True)

        self.assertEqual(summary, {"assessments": 5, "supports": 3, "options": 4})
        self.assertEqual(Assessment.objects.count(), 5)
        self.assertEqual(Accommodation.objects.count(), 3)
        self.assertEqual(AccommodationOption.objects.count(), 4)

        # Assessments are imported inactive by default
        self.assertFalse(Assessment.objects.filter(is_active=True).exists())

        caa_science = Assessment.objects.get(name="CAA science")
        cast = Assessment.objects.get(name="CAST")
        smarter_math = Assessment.objects.get(name="Smarter math")

        noise = AccommodationOption.objects.get(code="NEDS_NoiseBuf")
        self.assertIn(noise, caa_science.supported_options.all())
        self.assertIn(noise, cast.supported_options.all())
        self.assertNotIn(noise, smarter_math.supported_options.all())

        # The option is present on the assessments listed in its Program cell
        abacus = AccommodationOption.objects.get(code="NEA_Abacus")
        self.assertIn(abacus, smarter_math.supported_options.all())
        self.assertNotIn(abacus, cast.supported_options.all())

    def test_import_can_activate_assessments(self):
        parsed = self._parsed()
        importers.import_configuration(parsed, clear=True, active=True)
        self.assertEqual(Assessment.objects.filter(is_active=True).count(), 5)

    def test_import_is_rebuildable_without_duplicates(self):
        parsed = self._parsed()
        importers.import_configuration(parsed, clear=True)
        importers.import_configuration(parsed, clear=True)

        self.assertEqual(Assessment.objects.count(), 5)
        self.assertEqual(Accommodation.objects.count(), 3)
        self.assertEqual(AccommodationOption.objects.count(), 4)
        self.assertEqual(Accommodation.objects.filter(name="Color Contrast").count(), 1)

    def test_import_is_additive_and_stamps_tag(self):
        # Pre-existing manual records must survive an import (no wipe by default).
        Assessment.objects.create(name="Manual Test", subject="X", is_active=True)
        Accommodation.objects.create(name="Manual Support", code="M")

        parsed = self._parsed()
        importers.import_configuration(parsed, tag="CAASPP")

        self.assertTrue(Assessment.objects.filter(name="Manual Test").exists())
        self.assertTrue(Accommodation.objects.filter(name="Manual Support").exists())

        # Imported records carry the tag.
        self.assertEqual(Assessment.objects.get(name="Smarter math").tag, "CAASPP")
        self.assertEqual(Accommodation.objects.filter(tag="CAASPP").count(), 3)

        # Existing assessments are reused, not duplicated.
        self.assertEqual(Assessment.objects.count(), 6)

    def test_import_same_tag_is_idempotent(self):
        parsed = self._parsed()
        importers.import_configuration(parsed, tag="CAASPP")
        importers.import_configuration(parsed, tag="CAASPP")

        self.assertEqual(Assessment.objects.count(), 5)
        self.assertEqual(Accommodation.objects.count(), 3)
        self.assertEqual(AccommodationOption.objects.count(), 4)
        self.assertEqual(Accommodation.objects.filter(tag="CAASPP").count(), 3)

    def test_import_different_tags_keep_separate_supports(self):
        parsed = self._parsed()
        importers.import_configuration(parsed, tag="CAASPP")
        importers.import_configuration(parsed, tag="ELPAC")

        # Same assessment names are deduped across tags, but designated
        # supports are kept separate per source file.
        self.assertEqual(Assessment.objects.count(), 5)
        self.assertEqual(Accommodation.objects.count(), 6)
        self.assertEqual(AccommodationOption.objects.count(), 8)
        self.assertEqual(set(Accommodation.objects.values_list("tag", flat=True)), {"CAASPP", "ELPAC"})

    def test_map_only_adds_missing_associations_without_other_changes(self):
        parsed = self._parsed()

        # Pre-seed the tagged support + option + assessments.
        accommodation = Accommodation.objects.create(
            name="Abacus", code="B", column_letter="B", tag="CAASPP"
        )
        option = AccommodationOption.objects.create(
            accommodation=accommodation, name="Abacus is selected.", code="NEA_Abacus"
        )
        smarter = Assessment.objects.create(name="Smarter math", is_active=True)
        caa_math = Assessment.objects.create(name="CAA math", is_active=True)
        Assessment.objects.create(name="CAA science", is_active=True)
        unrelated = Assessment.objects.create(name="Unrelated Test", is_active=False)
        smarter.supported_options.add(option)  # already mapped

        summary = importers.update_assessment_mappings(parsed, tag="CAASPP", apply=True)

        # Added the two missing associations; existing one is untouched.
        self.assertEqual(summary["added_mappings"], 2)
        self.assertTrue(caa_math.supported_options.filter(id=option.id).exists())
        self.assertTrue(Assessment.objects.get(name="CAA science").supported_options.filter(id=option.id).exists())
        self.assertTrue(smarter.supported_options.filter(id=option.id).exists())
        self.assertFalse(unrelated.supported_options.filter(id=option.id).exists())

        # Nothing else was created or altered.
        self.assertEqual(Assessment.objects.count(), 4)
        self.assertEqual(Accommodation.objects.count(), 1)
        self.assertEqual(AccommodationOption.objects.count(), 1)

        # Idempotent: re-running adds nothing.
        summary2 = importers.update_assessment_mappings(parsed, tag="CAASPP", apply=True)
        self.assertEqual(summary2["added_mappings"], 0)

    def test_map_only_falls_back_when_tag_differs(self):
        parsed = self._parsed()

        # Support imported under the DEFAULT filename tag (e.g. CAASPP_Upload).
        accommodation = Accommodation.objects.create(
            name="Abacus", code="B", column_letter="B", tag="CAASPP_Upload"
        )
        option = AccommodationOption.objects.create(
            accommodation=accommodation, name="Abacus is selected.", code="NEA_Abacus"
        )
        for name in ("Smarter math", "CAA math", "CAA science"):
            Assessment.objects.create(name=name, is_active=True)

        # Running with a different --tag still adds the missing mappings.
        summary = importers.update_assessment_mappings(parsed, tag="CAASPP", apply=True)
        self.assertEqual(summary["added_mappings"], 3)
        for name in ("Smarter math", "CAA math", "CAA science"):
            self.assertTrue(Assessment.objects.get(name=name).supported_options.filter(id=option.id).exists())


class DesiTestingCoordinatorAndRulesTests(TestCase):

    def setUp(self):
        self.teacher_role = Role.objects.create(name="Teacher")
        self.coordinator_role = Role.objects.create(name="Testing Coordinator")
        self.project_role = Role.objects.create(name="Project Admin")

        self.user = User.objects.create_user(
            username="coord-teacher@test.com",
            email="coord-teacher@test.com",
            password="password123",
        )
        self.user.roles.add(self.teacher_role, self.coordinator_role)
        self.user.schools = ["SCH-A"]
        self.user.save()

        self.student = CurrentStudent.objects.create(
            stu_annual_key="desi_coord_student",
            ssid="coordssid",
            student_id="coordlocal",
            student_name="Coordinator Student",
            grade="03",
            school_state_id="SCH-A",
            school_year="2025-2026",
        )

    def test_coordinator_addon_role_is_schoolwide_even_when_teacher_active(self):
        # Simulate middleware attaching an active role of Teacher.
        self.user.active_role = self.teacher_role
        self.assertIsNone(self.user.get_accessible_student_keys())

    def test_can_edit_matrix(self):
        # Coordinator holding only Testing Coordinator is read-only.
        coord = User.objects.create_user(username="coord@test.com", email="coord@test.com", password="password123")
        coord.roles.add(self.coordinator_role)
        self.assertFalse(can_edit_desi(coord, student=self.student))

        # Teacher default = edit.
        self.assertTrue(can_edit_desi(self.user, student=self.student))

        # View-only school flips teacher to read-only.
        DesiSchoolAccess.objects.create(school_state_id="SCH-A", teachers_can_edit=False)
        self.assertFalse(can_edit_desi(self.user, student=self.student))

    def test_view_only_school_blocks_teacher_save(self):
        DesiSchoolAccess.objects.create(school_state_id="SCH-A", teachers_can_edit=False)
        assessment = Assessment.objects.create(name="Blocked Test", is_active=True)
        self.user.active_role = self.teacher_role
        self.client.force_login(self.user)

        url = reverse("desi:assign_accommodations", args=[self.student.stu_annual_key])
        response = self.client.post(url, {"completed_assessments": [str(assessment.id)]})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StudentAssessmentCompletion.objects.filter(
                student_annual_key=self.student.stu_annual_key, assessment=assessment
            ).exists()
        )

    def test_support_rule_hides_support(self):
        accommodation = Accommodation.objects.create(name="Calculator", code="CALC")
        option = AccommodationOption.objects.create(accommodation=accommodation, name="Standard", code="STD")
        assessment = Assessment.objects.create(name="Hidden Math", subject="Math", is_active=True)
        assessment.supported_options.add(option)

        rule = SupportRule.objects.create(name="No grade 03 calculators", is_active=True)
        rule.supports.add(accommodation)
        SupportRuleCriteria.objects.create(rule=rule, field_name="grade", operator="in", value="04,05")

        # Student is grade 03 -> criteria fail -> support hidden.
        hidden = hidden_support_ids(self.student)
        self.assertIn(accommodation.id, hidden)

        # Grade 04 student keeps the support.
        grade4 = CurrentStudent.objects.create(
            stu_annual_key="grade4",
            ssid="g4ssid",
            student_id="g4",
            student_name="Grade Four",
            grade="04",
            school_state_id="SCH-A",
            school_year="2025-2026",
        )
        self.assertNotIn(accommodation.id, hidden_support_ids(grade4))

    def test_support_rule_form_creates_rule(self):
        project = User.objects.create_user(username="p@test.com", email="p@test.com", password="password123")
        project.roles.add(self.project_role)
        self.client.force_login(project)
        accommodation = Accommodation.objects.create(name="Masking", code="M")

        response = self.client.post(reverse("desi:support_rule_create"), {
            "name": "Masking only grade 05",
            "description": "",
            "is_active": "on",
            "supports": [str(accommodation.id)],
            "crit_field[]": ["grade"],
            "crit_operator[]": ["is"],
            "crit_value[]": ["05"],
        })
        self.assertEqual(response.status_code, 302)
        rule = SupportRule.objects.get(name="Masking only grade 05")
        self.assertIn(accommodation, rule.supports.all())
        self.assertEqual(rule.criteria.count(), 1)

    def test_resource_crud_and_embed(self):
        accommodation = Accommodation.objects.create(name="TTS", code="TTS")
        resource = AccommodationResource.objects.create(
            accommodation=accommodation,
            label="Guide",
            url="https://www.youtube.com/watch?v=abc123",
            kind="youtube",
        )
        self.assertIn("youtube.com/embed/abc123", resource.get_embed_url())

    def test_school_report_counts(self):
        from desi.report_utils import build_school_report

        assessment = Assessment.objects.create(name="Report Math", subject="Math", is_active=True)
        CurrentStudent.objects.create(
            stu_annual_key="r1",
            ssid="r1ssid",
            student_id="r1",
            student_name="Report One",
            grade="03",
            school_state_id="SCH-A",
            school_year="2025-2026",
        )
        other = CurrentStudent.objects.create(
            stu_annual_key="r2",
            ssid="r2ssid",
            student_id="r2",
            student_name="Report Two",
            grade="03",
            school_state_id="SCH-A",
            school_year="2025-2026",
        )
        StudentAssessmentCompletion.objects.create(
            student_annual_key="r1",
            assessment=assessment,
            is_completed=True,
        )

        report = build_school_report("SCH-A", "2025-2026")
        summary = {row["assessment"].id: row for row in report["summary"]}
        self.assertEqual(summary[assessment.id]["eligible"], 3)
        self.assertEqual(summary[assessment.id]["completed"], 1)
        self.assertEqual(summary[assessment.id]["pending"], 2)
        self.assertIn(other, report["pending_by_assessment"][assessment.id])
        self.assertIn(self.student, report["pending_by_assessment"][assessment.id])

    def test_entered_supports_report(self):
        from desi.report_utils import build_entered_supports_report

        accommodation = Accommodation.objects.create(name="Calculator", code="CALC")
        option = AccommodationOption.objects.create(accommodation=accommodation, name="Scientific", code="SCI")
        updater = User.objects.create_user(username="up@test.com", email="up@test.com", password="password123")
        record, _ = StudentAccommodation.objects.get_or_create(
            student_annual_key=self.student.stu_annual_key,
        )
        record.options.add(option)
        record.updated_by = updater
        record.save()

        report = build_entered_supports_report("SCH-A", "2025-2026")
        rows = {row["ssid"]: row for row in report["rows"]}
        self.assertIn("Calculator: Scientific", rows["coordssid"]["supports"])
        self.assertEqual(rows["coordssid"]["updated_by"], "up@test.com")

    def test_coordinator_dashboard_access_tab_renders(self):
        coord = User.objects.create_user(username="cd@test.com", email="cd@test.com", password="password123")
        coord.roles.add(self.coordinator_role)
        coord.schools = ["SCH-A"]
        coord.save()
        self.client.force_login(coord)

        response = self.client.get(reverse("desi:coordinator_dashboard"), HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support Admin")
        self.assertContains(response, "Teacher Editing Access by School")


class DesiMappingPreservationTests(TestCase):

    def setUp(self):
        role = Role.objects.create(name="Project Admin")
        self.user = User.objects.create_user(username="map@test.com", email="map@test.com", password="password123")
        self.user.roles.add(role)

        self.acc = Accommodation.objects.create(name="Text-to-Speech Mapping", code="TTSM")
        self.opt = AccommodationOption.objects.create(accommodation=self.acc, name="Standard", code="STD")

        self.active = Assessment.objects.create(name="Active Mapping Test", is_active=True)
        self.inactive = Assessment.objects.create(name="Inactive Mapping Test", is_active=False)
        self.extra = Assessment.objects.create(name="Extra Mapping Test", is_active=True)
        self.active.supported_options.add(self.opt)
        self.inactive.supported_options.add(self.opt)

    def _edit_post(self, map_action=None, supported=None):
        data = {
            "name": self.acc.name,
            "code": self.acc.code,
            "category": self.acc.category,
            "description": "",
            "order": "0",
            "option_id[]": [str(self.opt.id)],
            "option_name[]": ["Standard"],
            "option_code[]": ["STD"],
            "supported_assessments": supported or [],
        }
        if map_action:
            data["map_action"] = map_action
        self.client.force_login(self.user)
        return self.client.post(reverse("desi:edit_accommodation", args=[self.acc.id]), data)

    def test_replace_save_preserves_inactive_assessment_mapping(self):
        response = self._edit_post(supported=[str(self.extra.id)])
        self.assertEqual(response.status_code, 302)

        # Active assessments not selected are removed; inactive stays mapped.
        self.assertFalse(self.active.supported_options.filter(id=self.opt.id).exists())
        self.assertTrue(self.inactive.supported_options.filter(id=self.opt.id).exists())
        self.assertTrue(self.extra.supported_options.filter(id=self.opt.id).exists())

    def test_merge_keeps_existing_mappings_and_adds_selected(self):
        response = self._edit_post(map_action="merge", supported=[str(self.extra.id)])
        self.assertEqual(response.status_code, 302)

        # Existing mappings (active + inactive) are untouched; extra added.
        self.assertTrue(self.active.supported_options.filter(id=self.opt.id).exists())
        self.assertTrue(self.inactive.supported_options.filter(id=self.opt.id).exists())
        self.assertTrue(self.extra.supported_options.filter(id=self.opt.id).exists())

    def test_option_used_only_by_inactive_assessment_is_never_dropped(self):
        self.client.force_login(self.user)

        # An option that only serves the inactive assessment.
        inactive_only = AccommodationOption.objects.create(
            accommodation=self.acc, name="Inactive Only", code="INO"
        )
        self.inactive.supported_options.add(inactive_only)

        # Edit posts ONLY the Standard option row (not Inactive Only).
        response = self.client.post(reverse("desi:edit_accommodation", args=[self.acc.id]), {
            "name": self.acc.name,
            "code": self.acc.code,
            "category": self.acc.category,
            "description": "",
            "order": "0",
            "option_id[]": [str(self.opt.id)],
            "option_name[]": ["Standard"],
            "option_code[]": ["STD"],
            "supported_assessments": [str(self.active.id)],
        })
        self.assertEqual(response.status_code, 302)

        inactive_only.refresh_from_db()
        self.assertTrue(self.inactive.supported_options.filter(id=inactive_only.id).exists())
        self.assertTrue(self.active.supported_options.filter(id=self.opt.id).exists())


class DesiSeisDisplaySettingsTests(TestCase):
    """Admin SEIS display selection + workspace gating."""

    def setUp(self):
        self.role = Role.objects.create(name="Project Admin")
        self.user = User.objects.create_user(
            username="seis-admin@test.com",
            email="seis-admin@test.com",
            password="password123",
        )
        self.user.roles.add(self.role)
        self.client.force_login(self.user)

        # Assessments: one active, one inactive, one unrelated.
        self.ast_math = Assessment.objects.create(name="Smarter math", is_active=True)
        self.ast_ela = Assessment.objects.create(name="Smarter ELA", is_active=False)
        self.ast_other = Assessment.objects.create(name="Unrelated Test", is_active=True)

        self.acc = Accommodation.objects.create(name="Text-to-Speech", code="TTS")
        self.opt = AccommodationOption.objects.create(
            accommodation=self.acc, name="Standard", code="TTS"
        )
        self.ast_math.supported_options.add(self.opt)
        self.ast_ela.supported_options.add(self.opt)
        self.ast_other.supported_options.add(self.opt)

        self.student = CurrentStudent.objects.create(
            stu_annual_key="seis_display_student",
            ssid="seisdisplay",
            student_id="seis-local",
            student_name="SEIS Display Student",
            grade="04",
            school_year="2025-2026",
        )

    def test_admin_dashboard_saves_selection(self):
        url = reverse("desi:admin_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SEIS Display Settings")

        # Save a selection of two assessments (one inactive).
        response = self.client.post(url, {
            "seis_display_assessments": [str(self.ast_math.id), str(self.ast_ela.id)],
        })
        self.assertEqual(response.status_code, 302)

        settings = DesiSeisDisplaySettings.objects.first()
        self.assertIsNotNone(settings)
        self.assertEqual(
            set(settings.assessments.values_list("id", flat=True)),
            {self.ast_math.id, self.ast_ela.id},
        )

    def test_dashboard_echoes_saved_selection(self):
        import re
        settings, _ = DesiSeisDisplaySettings.objects.get_or_create(pk=1)
        settings.assessments.set([self.ast_math.id])
        response = self.client.get(reverse("desi:admin_dashboard"))
        html = response.content.decode()

        def is_checked(value):
            # value appears before the optional whitespace-separated "checked"
            pattern = re.compile(r'name="seis_display_assessments" value="%s"\s*(checked)?' % value)
            m = pattern.search(html)
            return bool(m and m.group(1))

        # The selected math row is checked; ELA is not.
        self.assertTrue(is_checked(self.ast_math.id))
        self.assertFalse(is_checked(self.ast_ela.id))

class DesiSeisDisplaySettingsViewTests(TestCase):
    """SEIS admin display selection applied on the student workspace view."""

    def setUp(self):
        from unittest import mock
        self._patchers = []
        self._fake_map = {}

        def _fake_build(ssid, assessments):
            return self._fake_map, {acc_id for _a, acc_id in self._fake_map}

        patcher_build = mock.patch.object(
            __import__("desi.views", fromlist=["build_seis_option_map"]),
            "build_seis_option_map",
            side_effect=_fake_build,
        )
        self._patchers.append(patcher_build)
        for p in self._patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

        self.role = Role.objects.create(name="Project Admin")
        self.user = User.objects.create_user(
            username="seis-view-admin@test.com",
            email="seis-view-admin@test.com",
            password="password123",
        )
        self.user.roles.add(self.role)
        self.client.force_login(self.user)

        self.ast_math = Assessment.objects.create(name="Smarter math", is_active=True)
        self.ast_ela = Assessment.objects.create(name="Smarter ELA", is_active=False)

        self.acc = Accommodation.objects.create(name="Text-to-Speech", code="TTS")
        self.opt = AccommodationOption.objects.create(
            accommodation=self.acc, name="Standard", code="TTS"
        )
        self.ast_math.supported_options.add(self.opt)
        self.ast_ela.supported_options.add(self.opt)

        self.student = CurrentStudent.objects.create(
            stu_annual_key="seis_view_student",
            ssid="seisview",
            student_id="seis-view",
            student_name="SEIS View Student",
            grade="04",
            school_year="2025-2026",
        )
        # Make the superuser/PA an admin that can see the student.
        self.user.is_superuser = True
        self.user.save()

    def _stop_patchers(self):
        for p in self._patchers:
            try:
                p.stop()
            except Exception:
                pass

    def test_selected_assessments_show_seis_locks_and_readonly(self):
        # Simulate the student's SEIS row referencing both math and ELA.
        self._fake_map = {
            (self.ast_math.id, self.acc.id): self.opt,
            (self.ast_ela.id, self.acc.id): self.opt,
        }
        DesiSeisDisplaySettings.objects.create(pk=1)
        settings = DesiSeisDisplaySettings.objects.get(pk=1)
        settings.assessments.set([self.ast_math.id, self.ast_ela.id])

        response = self.client.get(
            reverse("desi:assign_accommodations", args=[self.student.stu_annual_key]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # Both math (active) and ELA (inactive) are SEIS-managed: the read-only
        # SEIS section lists only their SEIS supports, and neither test is
        # shown as an editable card.
        self.assertIn("SEIS settings (read only)", html)
        self.assertIn("Smarter math", html)
        self.assertIn("Smarter ELA", html)
        self.assertIn("Imported from SEIS", html)

    def test_unselected_assessment_hides_its_seis(self):
        self._fake_map = {
            (self.ast_math.id, self.acc.id): self.opt,
            (self.ast_ela.id, self.acc.id): self.opt,
        }
        # Admin only selected math -> ELA's SEIS is hidden even though the
        # simulated SEIS source references it.
        DesiSeisDisplaySettings.objects.create(pk=1)
        settings = DesiSeisDisplaySettings.objects.get(pk=1)
        settings.assessments.set([self.ast_math.id])

        response = self.client.get(
            reverse("desi:assign_accommodations", args=[self.student.stu_annual_key]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # Math is SEIS-managed (read-only); ELA is not selected, so no SEIS
        # section lists it.
        self.assertIn("SEIS settings (read only)", html)
        self.assertIn("Smarter math", html)
        self.assertIn("Imported from SEIS", html)
        # Smarter ELA must not appear at all (not selected => not SEIS).
        self.assertNotIn("Smarter ELA", html)
