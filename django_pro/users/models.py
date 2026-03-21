from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

class School(models.Model):
    name = models.CharField(max_length=255)
    # Add other fields as necessary
    
    def __str__(self):
        return self.name

class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class User(AbstractUser):
    # AbstractUser already has:
    # username, first_name, last_name, email, is_staff, is_active, date_joined
    
    name = models.CharField(_("Full Name"), max_length=255, blank=True)
    job_title = models.CharField(_("Job Title"), max_length=255, blank=True)
    staff_id = models.CharField(_("Staff ID"), max_length=50, blank=True, null=True, unique=True, db_index=True)
    
    # Role can be dynamic as per requirement "create roles"
    role = models.ForeignKey(
        Role, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="users"
    )
    
    # Access to multiple schools
    schools = models.ManyToManyField(School, blank=True, related_name="users")
    
    # Preferences placeholder
    preferences = models.JSONField(default=dict, blank=True)

    # Make email unique and required as it is key for Google Auth
    email = models.EmailField(_("email address"), unique=True)
    
    avatar = models.URLField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.email
