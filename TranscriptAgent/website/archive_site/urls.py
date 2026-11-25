"""The site's pages: the home page, signing in, the clerks' admin, the chatbot and the public API."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView
from . import session
urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    # Only signing in and out and changing a password: there's no email to reset one, since clerks make the accounts
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password_change/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("accounts/password_change/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    # Signing in and out from the Vue frontend (archive_site/session.py)
    path("api/session", session.who_am_i, name="session"),
    path("api/session/login", session.sign_in, name="session_login"),
    path("api/session/logout", session.sign_out, name="session_logout"),
    path("admin/", admin.site.urls),
    path("chat/", include("chat.urls")),
    path("api/v1/", include("public_api.urls"))
]
