from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ca_dash.services.loader import format_cds, normalize_row


class NormalizeRowLabelTests(TestCase):
    def test_ela_status_labels(self):
        row = normalize_row(
            {
                'cds': '33670583330669',
                'indicator': 'ELA',
                'studentgroup': 'ALL',
                'statuslevel': '4',
                'changelevel': '3',
                'color': '4',
                'box': '180',
                'reportingyear': '2023',
            }
        )
        self.assertEqual(row['studentgroup_label'], 'All Students')
        self.assertEqual(row['statuslevel_label'], 'High')
        self.assertIn('changelevel_label', row)

    def test_elpi_higher_is_better_status(self):
        row = normalize_row(
            {
                'cds': '33670580000000',
                'indicator': 'ELPI',
                'studentgroup': 'EL',
                'statuslevel': '3',
                'changelevel': '3',
                'color': '3',
                'box': '130',
                'reportingyear': '2023',
            }
        )
        self.assertEqual(row['statuslevel_label'], 'Medium')


class CaDashViewSmokeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='cadash_user',
            email='cadash@example.com',
            password='test-pass-123',
        )

    def test_performance_map_requires_login(self):
        url = reverse('ca_dash:performance_map')
        guest = self.client.get(url)
        self.assertEqual(guest.status_code, 302)
        self.client.force_login(self.user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'School Performance Map')

    def test_indicators_api(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('ca_dash:api_indicators'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(len(data), 8)
        keys = {item['key'] for item in data}
        self.assertIn('ela', keys)
        self.assertIn('chronic', keys)