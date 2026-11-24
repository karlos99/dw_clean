from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from unittest.mock import patch

from ca_dash.services.loader import (
    _better_school_label,
    format_cds,
    normalize_row,
    school_display_name,
)


class SchoolDisplayNameTests(SimpleTestCase):
    def test_unknown_short_loses_to_valid_label(self):
        self.assertEqual(_better_school_label('Unknown', 'Washington'), 'Washington')
        self.assertEqual(_better_school_label('Washington', 'Unknown'), 'Washington')

    def test_display_name_falls_back_to_cde_schoolname(self):
        with patch('ca_dash.services.loader._load_school_display_labels', return_value={}):
            self.assertEqual(
                school_display_name('33670586031959', 'George Washington Charter'),
                'George Washington Charter',
            )

    def test_normalize_row_uses_cde_when_short_would_be_unknown(self):
        with patch(
            'ca_dash.services.loader._load_school_display_labels',
            return_value={'6031959': 'Unknown'},
        ):
            row = normalize_row({
                'cds': '33670586031959',
                'rtype': 'S',
                'schoolname': 'George Washington Charter',
            })
        self.assertEqual(row['school_short_name'], 'George Washington Charter')


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