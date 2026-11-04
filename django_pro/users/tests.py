from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import Client, TestCase

from studata.models import School as StudataSchool
from users.admin import CustomUserAdmin
from users.models import Role, User

class CustomUserAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = CustomUserAdmin(User, self.site)
        User._school_cache = {}
        
        # Create some test schools in the database
        self.school1 = StudataSchool.objects.create(
            dw_schoolkey=1,
            state_id="SCH1",
            school_name="School One"
        )
        self.school2 = StudataSchool.objects.create(
            dw_schoolkey=2,
            state_id="SCH2",
            school_name="School Two"
        )

    def test_get_school_single(self):
        user = User.objects.create(
            username="testuser1",
            email="test1@example.com",
            schools=["SCH1"]
        )
        self.assertEqual(user.first_school_name, "School One")
        self.assertEqual(self.admin.get_school(user), "School One")

    def test_get_school_multiple_only_first(self):
        user = User.objects.create(
            username="testuser2",
            email="test2@example.com",
            schools=["SCH2", "SCH1"]
        )
        self.assertEqual(user.first_school_name, "School Two")
        self.assertEqual(self.admin.get_school(user), "School Two")

    def test_get_school_empty_or_invalid(self):
        user_empty = User(schools=[])
        self.assertEqual(user_empty.first_school_name, "-")
        self.assertEqual(self.admin.get_school(user_empty), "-")
        
        user_none = User(schools=None)
        self.assertEqual(user_none.first_school_name, "-")
        self.assertEqual(self.admin.get_school(user_none), "-")

        user_not_list = User(schools="SCH1")  # Not a list
        self.assertEqual(user_not_list.first_school_name, "-")
        self.assertEqual(self.admin.get_school(user_not_list), "-")


class ActiveRoleMiddlewareSchoolTest(TestCase):
    def setUp(self):
        for i in range(5):
            StudataSchool.objects.create(
                dw_schoolkey=100 + i,
                state_id="DUP1",
                school_name=f"School Variant {i}",
            )
        self.user = User.objects.create(
            username="schooluser",
            email="school@example.com",
            schools=["DUP1"],
        )
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = "DUP1"
        session.save()

    def test_home_loads_when_state_id_is_not_unique(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.email, response.wsgi_request.user.email)
        self.assertIsNotNone(response.wsgi_request.user.active_school)
        self.assertEqual(response.wsgi_request.user.active_school.state_id, "DUP1")


class DistrictRoleVisibilityTests(TestCase):
    def setUp(self):
        self.project_admin, _ = Role.objects.get_or_create(name='Project Admin')
        self.district_admin, _ = Role.objects.get_or_create(name='District Admin')
        self.district_view, _ = Role.objects.get_or_create(name='District View')
        self.teacher, _ = Role.objects.get_or_create(name='Teacher')

    def _user(self, name, roles, schools=None):
        user = User.objects.create(username=name, email=f'{name}@x.com', schools=schools or [])
        user.roles.set(roles)
        return user

    def test_district_roles_unrestricted_even_without_active_role(self):
        from dataexploration.utils import is_unrestricted
        for role in (self.district_admin, self.district_view, self.project_admin):
            user = self._user(f'u_{role.name.replace(" ", "_")}', [role])
            # No active role context selected (e.g. "All Roles" switcher).
            user.active_role = None
            self.assertTrue(is_unrestricted(user), msg=f'{role.name} should be unrestricted')
            # Even with assigned schools, an unrestricted role sees everything.
            user.schools = ['0110791']
            self.assertTrue(is_unrestricted(user), msg=f'{role.name} with schools should be unrestricted')
            # Explicit District role context also unrestricted.
            user.active_role = role
            self.assertTrue(is_unrestricted(user))

    def test_school_scoped_roles_are_not_unrestricted(self):
        from dataexploration.utils import is_unrestricted
        teacher = self._user('u_teacher', [self.teacher], schools=['0110791'])
        teacher.active_role = self.teacher
        self.assertFalse(is_unrestricted(teacher))

    def test_district_view_gets_full_student_access(self):
        view_user = self._user('u_dv', [self.district_view])
        view_user.active_role = self.district_view
        self.assertIsNone(view_user.get_accessible_student_keys())
        admin_user = self._user('u_da', [self.district_admin])
        admin_user.active_role = self.district_admin
        self.assertIsNone(admin_user.get_accessible_student_keys())

    def test_district_admin_bypasses_school_filter(self):
        from dataexploration.utils import _apply_de_school_filter
        from types import SimpleNamespace

        admin = self._user('u_da_school', [self.district_admin], schools=['Unknown'])
        admin.active_role = self.district_admin
        admin.active_school = SimpleNamespace(state_id='Unknown')

        where, params = [], []
        _apply_de_school_filter(where, params, admin)
        self.assertEqual(where, [])
        self.assertEqual(params, [])

    def test_teacher_with_active_school_gets_school_filter(self):
        from dataexploration.utils import _apply_de_school_filter
        from types import SimpleNamespace

        teacher = self._user('u_teacher_school', [self.teacher], schools=['0110791'])
        teacher.active_role = self.teacher
        teacher.active_school = SimpleNamespace(state_id='0110791')

        where, params = [], []
        _apply_de_school_filter(where, params, teacher)
        self.assertEqual(where, ['cs.school_state_id = %s'])
        self.assertEqual(params, ['0110791'])

    def test_district_view_can_switch_to_any_school(self):
        from studata.models import School
        from users.views import switch_role
        from django.test import RequestFactory

        School.objects.get_or_create(state_id='0110791', defaults={'dw_schoolkey': 101, 'school_name': 'School A'})
        School.objects.get_or_create(state_id='0999999', defaults={'dw_schoolkey': 102, 'school_name': 'School B'})

        dv_user = self._user('u_dv_switch', [self.district_view], schools=['0110791'])
        dv_user.active_role = self.district_view

        factory = RequestFactory()
        request = factory.post('/users/switch-role/', {'school_id': '0999999'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        request.user = dv_user
        request.session = {}

        response = switch_role(request)
        self.assertEqual(response.status_code, 200)
        import json
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['school_id'], '0999999')

    def test_district_view_middleware_populates_all_available_schools(self):
        from studata.models import School
        from users.middleware import ActiveRoleMiddleware

        School.objects.get_or_create(state_id='0110791', defaults={'dw_schoolkey': 101, 'school_name': 'School A'})
        School.objects.get_or_create(state_id='0999999', defaults={'dw_schoolkey': 102, 'school_name': 'School B'})

        dv_user = self._user('u_dv_mid', [self.district_view], schools=['0110791'])
        middleware = ActiveRoleMiddleware(get_response=lambda r: None)

        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/')
        request.user = dv_user
        request.session = {}

        middleware(request)
        available_ids = list(request.user.available_schools.values_list('state_id', flat=True))
        self.assertIn('0110791', available_ids)
        self.assertIn('0999999', available_ids)


class DraftMenuPreviewTests(TestCase):
    def setUp(self):
        from users.models_pages import Page

        self.viewer = User.objects.create(username='preview_user', email='preview@x.com')
        self.category = Page.objects.create(
            slug='preview-cat',
            name='Preview Cat',
            url_path='',
            status='draft',
            is_menu_category=True,
            show_in_menu=True,
        )
        self.category.menu_allowed_users.add(self.viewer)
        self.child = Page.objects.create(
            slug='preview-child',
            name='Preview Child',
            url_path='/dataviz/pages/preview-child/',
            status='draft',
            show_in_menu=True,
            parent_page=self.category,
        )

    def test_preview_menu_includes_draft_children_under_category_grant(self):
        from django.test import RequestFactory
        from users.context_processors import navigation_pages, user_can_preview_draft_site_page

        request = RequestFactory().get('/')
        request.user = self.viewer
        nav = navigation_pages(request)['navigation_pages']
        slugs = {p.slug for p in nav}
        self.assertIn('preview-cat', slugs)
        cat = next(p for p in nav if p.slug == 'preview-cat')
        child_slugs = {c.slug for c in cat.accessible_children}
        self.assertIn('preview-child', child_slugs)
        self.assertTrue(user_can_preview_draft_site_page(self.viewer, self.child))


class UserSchoolsAndRoleManagementTests(TestCase):
    def setUp(self):
        User._school_cache = {}
        self.sch1 = StudataSchool.objects.create(dw_schoolkey=201, state_id='SCH_A', school_name='Alpha Academy')
        self.sch2 = StudataSchool.objects.create(dw_schoolkey=202, state_id='SCH_B', school_name='Beta High')
        self.sch3 = StudataSchool.objects.create(dw_schoolkey=203, state_id='SCH_C', school_name='Gamma Middle')

        self.role_admin, _ = Role.objects.get_or_create(name='Project Admin')
        self.role_teacher, _ = Role.objects.get_or_create(name='Teacher')
        self.role_spec, _ = Role.objects.get_or_create(name='Testing Coordinator')

        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin_test@example.com',
            password='pass',
            schools=['SCH_A']
        )
        self.admin_user.roles.add(self.role_admin)

        self.staff_user = User.objects.create_user(
            username='staff_test',
            email='staff_test@example.com',
            name='Alice Staff',
            staff_id='STF_123',
            schools=['SCH_A', 'SCH_B', 'SCH_C']
        )

        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_assigned_schools_list_multiple(self):
        schools_list = self.staff_user.assigned_schools_list
        self.assertEqual(len(schools_list), 3)
        names = [s['school_name'] for s in schools_list]
        self.assertIn('Alpha Academy', names)
        self.assertIn('Beta High', names)
        self.assertIn('Gamma Middle', names)

    def test_assigned_schools_list_empty(self):
        u_empty = User.objects.create_user(username='empty_u', email='empty@example.com', schools=[])
        self.assertEqual(u_empty.assigned_schools_list, [])

    def test_manage_users_roles_tab(self):
        resp = self.client.get('/users/manage/?tab=roles')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('role_cards', resp.context)
        card_names = [c['name'] for c in resp.context['role_cards']]
        self.assertIn('Project Admin', card_names)
        self.assertIn('Teacher', card_names)

    def test_search_users_for_role(self):
        resp = self.client.get('/users/manage/users-search/?q=Alice')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('users', data)
        self.assertEqual(len(data['users']), 1)
        self.assertEqual(data['users'][0]['email'], 'staff_test@example.com')
        self.assertEqual(data['users'][0]['staff_id'], 'STF_123')

    def test_add_and_remove_users_from_role(self):
        # Add staff_user to Teacher role
        add_url = f'/users/manage/roles/{self.role_teacher.id}/add-users/'
        resp = self.client.post(
            add_url,
            data={'user_ids': [self.staff_user.id]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.assertTrue(self.staff_user.roles.filter(id=self.role_teacher.id).exists())

        # Remove staff_user from Teacher role
        remove_url = f'/users/manage/roles/{self.role_teacher.id}/remove-user/{self.staff_user.id}/'
        resp_rem = self.client.post(remove_url)
        self.assertEqual(resp_rem.status_code, 200)
        self.assertTrue(resp_rem.json()['success'])
        self.assertFalse(self.staff_user.roles.filter(id=self.role_teacher.id).exists())


class RoleExpirationAndLimitedStudentTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone

        self.now = timezone.now()
        self.timedelta = timedelta
        self.teacher, _ = Role.objects.get_or_create(name='Teacher')
        self.school_view, _ = Role.objects.get_or_create(name='School View')
        self.limited_role, _ = Role.objects.get_or_create(
            name='Limited Student Access',
            defaults={'is_student_limited': True},
        )
        self.limited_role.is_student_limited = True
        self.limited_role.save(update_fields=['is_student_limited'])
        self.user = User.objects.create_user(
            username='expiry_user',
            email='expiry_user@example.com',
            schools=['SCH_A'],
        )

    def _active_limited_student(self, ssid, student_id, key):
        from studata.models import CurrentStudent
        return CurrentStudent.objects.create(
            stu_annual_key=key,
            ssid=ssid,
            student_id=student_id,
            student_name=f'Student {key}',
            school_year='2025-2026',
        )

    def test_expired_assignment_is_not_effective(self):
        self.user.role_assignments.create(
            role=self.teacher,
            expires_at=self.now - self.timedelta(minutes=5),
        )
        self.assertNotIn('Teacher', self.user.role_names())
        self.assertFalse(self.user.effective_roles().filter(id=self.teacher.id).exists())

    def test_sync_expired_assignment_applies_fallback_role(self):
        self.user.role_assignments.create(role=self.school_view)
        self.user.role_assignments.create(
            role=self.teacher,
            expires_at=self.now - self.timedelta(minutes=5),
            fallback_role=self.school_view,
        )

        changed = self.user.sync_expired_role_assignments()

        self.assertEqual(changed.get(self.teacher.id), self.school_view.id)
        self.assertFalse(self.user.role_assignments.filter(role=self.teacher).exists())
        self.assertTrue(self.user.effective_roles().filter(id=self.school_view.id).exists())

    def test_limited_role_restricts_to_listed_ssid_and_student_id(self):
        first = self._active_limited_student('SSID-1', 'LOCAL-1', 'key-1')
        second = self._active_limited_student('SSID-2', 'LOCAL-2', 'key-2')
        self._active_limited_student('SSID-3', 'LOCAL-3', 'key-3')

        self.user.role_assignments.create(
            role=self.limited_role,
            limited_ssids=['SSID-1'],
            limited_student_ids=['LOCAL-2'],
        )
        self.user.active_role = self.limited_role

        keys = self.user.get_accessible_student_keys()
        self.assertEqual(set(keys), {first.stu_annual_key, second.stu_annual_key})

    def test_limited_role_without_students_grants_none(self):
        self.user.role_assignments.create(role=self.limited_role)
        self.user.active_role = self.limited_role

        self.assertEqual(self.user.get_accessible_student_keys(), [])

    def test_user_detail_saves_expiration_fallback_and_limited_students(self):
        from datetime import timedelta

        admin_role, _ = Role.objects.get_or_create(name='Project Admin')
        admin = User.objects.create_superuser(
            username='role_admin',
            email='role_admin@example.com',
            password='pass',
        )
        admin.roles.add(admin_role)
        self.user.roles.add(self.school_view)

        client = Client()
        client.force_login(admin)
        expires_at = (self.now + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

        response = client.post(
            f'/users/manage/{self.user.id}/',
            data={
                'name': self.user.name,
                'job_title': self.user.job_title,
                'staff_id': self.user.staff_id or '',
                'is_active': 'on',
                'roles': [self.limited_role.id, self.school_view.id],
                f'role_expires_{self.limited_role.id}': expires_at,
                f'role_fallback_{self.limited_role.id}': self.school_view.id,
                f'role_ssids_{self.limited_role.id}': 'SSID-1, SSID-2',
                f'role_student_ids_{self.limited_role.id}': 'LOCAL-1\nLOCAL-2',
            },
        )

        self.assertEqual(response.status_code, 302)
        assignment = self.user.role_assignments.get(role=self.limited_role)
        self.assertIsNotNone(assignment.expires_at)
        self.assertEqual(assignment.fallback_role_id, self.school_view.id)
        self.assertEqual(assignment.limited_ssids, ['SSID-1', 'SSID-2'])
        self.assertEqual(assignment.limited_student_ids, ['LOCAL-1', 'LOCAL-2'])

        school_view_assignment = self.user.role_assignments.get(role=self.school_view)
        self.assertIsNone(school_view_assignment.expires_at)


class CounselorCaseloadTests(TestCase):
    def setUp(self):
        from unittest.mock import MagicMock

        self.MagicMock = MagicMock
        self.school_view, _ = Role.objects.get_or_create(name='School View')
        self.user = User.objects.create_user(
            username='counselor',
            email='Counselor@Example.com',
            schools=['SCH_A'],
        )
        self.user.role_assignments.create(role=self.school_view)

        from studata.models import CurrentStudent

        self.caseload_student = CurrentStudent.objects.create(
            stu_annual_key='key-in-caseload',
            ssid='SSID-1',
            student_id='LOCAL-1',
            student_name='Caseload Student',
            school_year='2026-2027',
        )
        self.other_student = CurrentStudent.objects.create(
            stu_annual_key='key-not-in-caseload',
            ssid='SSID-2',
            student_id='LOCAL-2',
            student_name='Other Student',
            school_year='2026-2027',
        )

    def _caseload_patch(self, rows):
        queryset = self.MagicMock()
        queryset.exists.return_value = bool(rows)
        queryset.values_list.return_value = rows
        stack = ExitStack()
        stack.enter_context(
            patch(
                'studata.models.CounselorAssignment.objects.filter',
                return_value=queryset,
            )
        )
        stack.enter_context(
            patch(
                'users.models.counselor_assignment_table_exists',
                return_value=True,
            )
        )
        return stack

    def test_user_in_caseload_table_has_caseload_available(self):
        with self._caseload_patch([('2026', 'SSID-1', 'LOCAL-1')]):
            self.assertTrue(self.user.has_counselor_caseload())
            self.assertTrue(self.user.counselor_caseload_available)

    def test_caseload_context_restricts_to_table_students(self):
        # School View normally grants school-wide student access.
        self.assertIsNone(self.user.get_accessible_student_keys())

        self.user.active_caseload = True
        with self._caseload_patch([('2026', 'SSID-1', 'LOCAL-1')]):
            keys = self.user.get_accessible_student_keys()

        self.assertEqual(keys, [self.caseload_student.stu_annual_key])
        self.assertNotIn(self.other_student.stu_annual_key, keys)

    def test_caseload_ignores_prior_year_assignments(self):
        self.user.active_caseload = True
        with self._caseload_patch([
            ('2026', 'SSID-1', 'LOCAL-1'),
            ('2024', 'SSID-2', 'LOCAL-2'),
        ]):
            keys = self.user.get_accessible_student_keys()

        self.assertEqual(keys, [self.caseload_student.stu_annual_key])

    def test_switch_role_to_caseload_sets_session_flag(self):
        client = Client()
        client.force_login(self.user)

        with self._caseload_patch([('2026', 'SSID-1', 'LOCAL-1')]):
            response = client.post('/users/switch-role/', {'role_id': 'caseload'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get('active_caseload'))

    def test_switching_back_to_a_role_clears_caseload(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_caseload'] = True
        session.save()

        with self._caseload_patch([('2026', 'SSID-1', 'LOCAL-1')]):
            response = client.post(
                '/users/switch-role/',
                {'role_id': self.school_view.id},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.session.get('active_caseload'))
        self.assertEqual(
            client.session.get('active_role_id'),
            str(self.school_view.id),
        )

    def test_caseload_option_rejected_without_table_rows(self):
        client = Client()
        client.force_login(self.user)

        with self._caseload_patch([]):
            response = client.post('/users/switch-role/', {'role_id': 'caseload'})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(client.session.get('active_caseload'))


class NavbarConfigSplitPanelTests(TestCase):
    def setUp(self):
        from users.models_pages import Page
        self.admin_user = User.objects.create_superuser(
            username='admin_pages',
            email='admin_pages@example.com',
            password='password123'
        )
        self.top_page = Page.objects.create(
            name='Assessments',
            slug='assessments',
            url_path='/assessments/',
            show_in_menu=True,
            status='active'
        )
        self.child_page = Page.objects.create(
            name='State Testing',
            slug='state-testing',
            url_path='/assessments/state-testing/',
            parent_page=self.top_page,
            show_in_menu=True,
            status='active'
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_navbar_config_split_panel_and_opacity_defaults(self):
        from users.models_pages import NavbarConfig
        cfg = NavbarConfig.get()
        self.assertEqual(cfg.submenu_opacity, 100)
        self.assertEqual(cfg.submenu_opacity_decimal, 1.0)

        cfg.submenu_style = 'split_panel'
        cfg.submenu_opacity = 85
        cfg.save()

        reloaded = NavbarConfig.get()
        self.assertEqual(reloaded.submenu_style, 'split_panel')
        self.assertEqual(reloaded.submenu_opacity, 85)
        self.assertEqual(reloaded.submenu_opacity_decimal, 0.85)

    def test_navbar_settings_post_saves_split_panel_and_opacity(self):
        from users.models_pages import NavbarConfig
        resp = self.client.post(
            '/users/pages/navbar-settings/',
            data={
                'navbar_style': 'classic',
                'label_case': 'uppercase',
                'submenu_style': 'split_panel',
                'submenu_opacity': '90',
                'sticky': 'on'
            }
        )
        self.assertEqual(resp.status_code, 302)
        cfg = NavbarConfig.get()
        self.assertEqual(cfg.submenu_style, 'split_panel')
        self.assertEqual(cfg.submenu_opacity, 90)
        self.assertTrue(cfg.sticky)

    def test_manage_pages_navbar_tab_renders_split_panel_preview(self):
        resp = self.client.get('/users/pages/?tab=navbar')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode('utf-8')
        self.assertIn('Split panel (half-page panel with left categories + right items list)', content)
        self.assertIn('Submenu Background Opacity', content)
        self.assertIn('id="submenu_opacity_range"', content)
        self.assertNotIn('Submenu Explorer', content)

    def test_page_has_accessible_subpages_property(self):
        from users.models_pages import Page
        # top_page has child_page, but child_page has no children -> has_accessible_subpages is False
        self.top_page.accessible_children = [self.child_page]
        self.child_page.accessible_children = []
        self.assertFalse(self.top_page.has_accessible_subpages)

        # when grandchild is added to child_page -> has_accessible_subpages is True
        grandchild = Page(name='Grandchild', slug='grandchild', url_path='/gc/')
        self.child_page.accessible_children = [grandchild]
        self.assertTrue(self.top_page.has_accessible_subpages)






