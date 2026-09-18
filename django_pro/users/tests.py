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



