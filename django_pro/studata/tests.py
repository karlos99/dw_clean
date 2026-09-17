import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from users.models import User

from .pictures import PLACEHOLDER_SVG, picture_path


class StudentPictureLookupTests(TestCase):
    def test_finds_jpg_named_by_ssid(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '1001179754.jpg').write_bytes(b'\xff\xd8\xff\xd9')
            with override_settings(STUDENT_PICTURES_DIR=tmp):
                self.assertEqual(picture_path('1001179754'), Path(tmp) / '1001179754.jpg')

    def test_missing_ssid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(STUDENT_PICTURES_DIR=tmp):
                self.assertIsNone(picture_path('9999999999'))
                self.assertIsNone(picture_path(''))
                self.assertIsNone(picture_path('../etc/passwd'))


class StudentPictureViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='picuser', email='pic@example.com')

    def test_requires_login(self):
        response = self.client.get(reverse('studata:picture', args=['1001179754']))
        self.assertEqual(response.status_code, 302)

    def test_serves_photo_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '1001179754.jpg').write_bytes(b'\xff\xd8\xff\xd9')
            self.client.force_login(self.user)
            with override_settings(STUDENT_PICTURES_DIR=tmp):
                response = self.client.get(reverse('studata:picture', args=['1001179754']))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/jpeg')
            self.assertEqual(b''.join(response.streaming_content), b'\xff\xd8\xff\xd9')

    def test_placeholder_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.client.force_login(self.user)
            with override_settings(STUDENT_PICTURES_DIR=tmp):
                response = self.client.get(reverse('studata:picture', args=['missing']))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/svg+xml')
            self.assertEqual(response.content, PLACEHOLDER_SVG)
