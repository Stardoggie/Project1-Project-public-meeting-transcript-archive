"""The WSGI app gunicorn serves, locally and in the website's Lambda."""
import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "archive_site.settings")
application = get_wsgi_application()
