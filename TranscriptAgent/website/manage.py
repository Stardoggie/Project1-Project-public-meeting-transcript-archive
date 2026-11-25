#!/usr/bin/env python
"""Django's command-line tool for the archive's website: runserver, migrate, createsuperuser, setup_groups."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "archive_site.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
