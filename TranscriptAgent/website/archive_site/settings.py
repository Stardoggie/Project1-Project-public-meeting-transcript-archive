"""Settings for the archive's website.

Nothing secret is written here or in an environment variable:
- the database login comes from the secret named by DATABASE_SECRET_ID (the same one the agents use), or from
  DATABASE_URL locally
- Django's secret key and CloudFront's origin header come from the secret named by WEB_SECRET_ID (at the version in
  WEB_SECRET_VERSION, the same one CloudFront uses), or from DJANGO_SECRET_KEY locally
"""
import json
import os
from pathlib import Path
import boto3
from django.core.management.utils import get_random_secret_key
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import make_url
# The repo's .env (local dev); the archive's config reads it too
load_dotenv(find_dotenv(usecwd=True))
from meeting_transcript.config import AWS_REGION, database_url  # noqa: E402
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"


def _web_secret() -> dict:
    """The website's own secret: {"django_secret_key": ..., "origin_verify": ...}, or {} when WEB_SECRET_ID is unset."""
    secret_id = os.environ.get("WEB_SECRET_ID")
    if not secret_id:
        return {}
    secrets = boto3.client("secretsmanager", region_name=AWS_REGION)
    # The deploy pins CloudFront's copy of origin_verify to this version, so read the same one
    version = os.environ.get("WEB_SECRET_VERSION")
    pinned = {"VersionId": version} if version else {}
    return json.loads(secrets.get_secret_value(SecretId=secret_id, **pinned)["SecretString"])


def _secret_key(web_secret: dict) -> str:
    """The deployed key from the website's secret; locally DJANGO_SECRET_KEY, or a new random key while debugging."""
    key = web_secret.get("django_secret_key") or os.environ.get("DJANGO_SECRET_KEY")
    if key:
        return key
    if DEBUG:
        # Sign-ins only last until the server restarts, which is fine for local work
        return get_random_secret_key()
    raise RuntimeError("Set WEB_SECRET_ID (deployed) or DJANGO_SECRET_KEY, or DJANGO_DEBUG=1 for local work")


def _database(url) -> dict:
    """Django's database settings from the archive's own database URL, so both use the same login and SSL mode."""
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.database,
        "USER": url.username,
        "PASSWORD": url.password,
        "HOST": url.host,
        "PORT": url.port or 5432,
        "OPTIONS": dict(url.query),
        # Lambda keeps the process between requests, so keep the connection a little while too
        "CONN_MAX_AGE": 60
    }


_web = _web_secret()
SECRET_KEY = _secret_key(_web)
# CloudFront adds this header to every request it sends on; requests without it didn't come through the site
ORIGIN_VERIFY = _web.get("origin_verify", "")
if os.environ.get("WEB_SECRET_ID") and not ORIGIN_VERIFY:
    raise RuntimeError("The website's secret needs an origin_verify value")
# CloudFront calls the Lambda by its function URL, so a CloudFront Function passes the site's own address on as
# X-Forwarded-Host; that header is only trusted because require_cloudfront refuses anything that skipped CloudFront
USE_X_FORWARDED_HOST = True
ALLOWED_HOSTS = [".cloudfront.net", "localhost", "127.0.0.1"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "records",
    "public_api",
    "chat"
]
MIDDLEWARE = [
    "archive_site.middleware.require_cloudfront",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware"
]
ROOT_URLCONF = "archive_site.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages"
            ]
        }
    }
]
WSGI_APPLICATION = "archive_site.wsgi.application"
DATABASES = {"default": _database(make_url(database_url()))}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}
]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/chat/"
LOGOUT_REDIRECT_URL = "/"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
# The deploy collects the admin's CSS and scripts straight into the Lambda's bundle
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))
# The chatbot: the deployed runtime when CHATBOT_RUNTIME_ARN is set, otherwise `agentcore dev --agent chatbot --port 8090`
CHATBOT_RUNTIME_ARN = os.environ.get("CHATBOT_RUNTIME_ARN", "")
CHATBOT_URL = os.environ.get("CHATBOT_URL", "http://127.0.0.1:8090/invocations")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # The archive's Flask app sets up Alembic too, which lists its plugins at INFO
    "loggers": {"alembic": {"level": "WARNING"}}
}
