from django.test import TestCase
from django.contrib.admin.sites import AdminSite
from users.models import User
from users.admin import CustomUserAdmin
from studata.models import School as StudataSchool

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


