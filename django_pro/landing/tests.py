from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from users.models import Role
from studata.models import School
from landing.views import index

User = get_user_model()


class LandingPageSchoolFilterTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.school1 = School.objects.create(dw_schoolkey=1, state_id='SCH1', school_name='School One', short_name='SCH1')
        self.school2 = School.objects.create(dw_schoolkey=2, state_id='SCH2', school_name='School Two', short_name='SCH2')
        self.school3 = School.objects.create(dw_schoolkey=3, state_id='SCH3', school_name='School Three', short_name='SCH3')

        self.district_view_role, _ = Role.objects.get_or_create(name='District View')
        self.district_admin_role, _ = Role.objects.get_or_create(name='District Admin')
        self.teacher_role, _ = Role.objects.get_or_create(name='Teacher')

    @patch('landing.views.render')
    def test_district_view_sees_all_schools_even_with_assigned_schools(self, mock_render):
        mock_render.side_effect = lambda req, tmpl, ctx: ctx
        user = User.objects.create(
            username='dv_user',
            email='dv@example.com',
            schools=['SCH1'],  # only SCH1 assigned in profile
        )
        user.roles.add(self.district_view_role)

        request = self.factory.get('/')
        request.user = user
        request.session = {}

        ctx = index(request)
        context_schools = ctx['user_schools']
        self.assertEqual(len(context_schools), 3)
        school_ids = {s['state_id'] for s in context_schools}
        self.assertEqual(school_ids, {'SCH1', 'SCH2', 'SCH3'})
        self.assertEqual(ctx['school_count'], 3)

    @patch('landing.views.render')
    def test_restricted_user_only_sees_assigned_schools(self, mock_render):
        mock_render.side_effect = lambda req, tmpl, ctx: ctx
        user = User.objects.create(
            username='teacher_user',
            email='teacher@example.com',
            schools=['SCH1'],
        )
        user.roles.add(self.teacher_role)

        request = self.factory.get('/')
        request.user = user
        request.session = {}

        ctx = index(request)
        context_schools = ctx['user_schools']
        self.assertEqual(len(context_schools), 1)
        self.assertEqual(context_schools[0]['state_id'], 'SCH1')
        self.assertEqual(ctx['school_count'], 1)

