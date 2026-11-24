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

    @patch('landing.views.render')
    def test_home_dashboard_hybrid_ssr_serves_warm_and_defers_cold_slots(self, mock_render):
        from django.core.cache import cache
        from dataviz.models import Dashboard, DashboardChart, SavedChart
        from dataviz.views.api.chart_api import build_chart_render_cache_key
        from clickhouse_utils.client import get_analytics_sync_version

        cache.clear()
        mock_render.side_effect = lambda req, tmpl, ctx: ctx
        user = User.objects.create(username='home_hybrid', email='hybrid@example.com')
        user.roles.add(self.district_admin_role)

        warm_chart = SavedChart.objects.create(
            name='Warm Home Chart',
            chart_type='bar',
            source_type=SavedChart.SOURCE_COLUMNS,
            config={'selected_columns': ['grade'], 'chart_type': 'bar'},
            is_user_menu=True,
            owner=user,
        )
        cold_chart = SavedChart.objects.create(
            name='Cold Home Chart',
            chart_type='bar',
            source_type=SavedChart.SOURCE_COLUMNS,
            config={'selected_columns': ['school'], 'chart_type': 'bar'},
            is_user_menu=True,
            owner=user,
        )
        dash = Dashboard.objects.create(name='My Charts', owner=user, is_home=True)
        DashboardChart.objects.create(dashboard=dash, chart=warm_chart, position=0)
        DashboardChart.objects.create(dashboard=dash, chart=cold_chart, position=1)

        rkey, _, _, _, _, _ = build_chart_render_cache_key(
            warm_chart, {}, is_embed=True, sync_version=get_analytics_sync_version(),
        )
        cache.set(rkey, '<div class="chart-container">warm</div>', 60)

        request = self.factory.get('/')
        request.user = user
        request.session = {}

        with patch('dataviz.views.api.chart_api._render_saved_chart') as mock_render_chart:
            ctx = index(request)
            mock_render_chart.assert_not_called()

        slots_by_chart = {s.chart_id: s for s in ctx['home_slots']}
        self.assertEqual(slots_by_chart[warm_chart.pk].rendered_html, '<div class="chart-container">warm</div>')
        self.assertFalse(slots_by_chart[warm_chart.pk].needs_async_load)
        self.assertEqual(slots_by_chart[cold_chart.pk].rendered_html, '')
        self.assertTrue(slots_by_chart[cold_chart.pk].needs_async_load)


