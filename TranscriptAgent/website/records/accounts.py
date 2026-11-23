"""User accounts in the admin. Clerks may add viewer accounts and change them, but only superusers can make clerks
or superusers, so a clerk can't give anyone (including themselves) more access."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group, User
from .groups import VIEWERS
# What a clerk sees of an account: no staff, superuser, group or permission settings
CLERK_FIELDSETS = [
    (None, {"fields": ["username", "password"]}),
    ("Personal info", {"fields": ["first_name", "last_name", "email"]}),
    ("Access", {"fields": ["is_active", "last_login", "date_joined"]})
]
admin.site.unregister(User)


@admin.register(User)
class ArchiveUserAdmin(UserAdmin):
    def get_fieldsets(self, request, user=None):
        if request.user.is_superuser or user is None:
            return super().get_fieldsets(request, user)
        return CLERK_FIELDSETS

    def get_readonly_fields(self, request, user=None):
        if request.user.is_superuser:
            return super().get_readonly_fields(request, user)
        return ["last_login", "date_joined"]

    def get_queryset(self, request):
        """Clerks only see viewer accounts: never other clerks or superusers."""
        users = super().get_queryset(request)
        if request.user.is_superuser:
            return users
        return users.filter(is_staff=False, is_superuser=False)

    def save_model(self, request, user, form, change):
        if not request.user.is_superuser:
            # Whatever was sent, an account a clerk saves stays a viewer
            user.is_staff = False
            user.is_superuser = False
        super().save_model(request, user, form, change)
        if not change and not request.user.is_superuser:
            user.groups.add(Group.objects.get_or_create(name=VIEWERS)[0])
