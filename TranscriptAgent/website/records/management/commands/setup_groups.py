"""Creates the Clerks and Viewers groups, or brings their permissions up to date. Safe to run again."""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from records.groups import CLERK_PERMISSIONS, CLERKS, VIEWERS


class Command(BaseCommand):
    help = "Create the Clerks and Viewers groups with their permissions"

    def handle(self, *args, **options):
        clerks, _ = Group.objects.get_or_create(name=CLERKS)
        permissions = []
        for name in CLERK_PERMISSIONS:
            app_label, codename = name.split(".")
            permissions.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        clerks.permissions.set(permissions)
        # Viewers need no permissions: being signed in is enough for the chatbot page
        Group.objects.get_or_create(name=VIEWERS)
        self.stdout.write(f"{CLERKS} ({len(permissions)} permissions) and {VIEWERS} are ready.")
