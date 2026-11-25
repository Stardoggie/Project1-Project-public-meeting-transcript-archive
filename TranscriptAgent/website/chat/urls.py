from django.urls import path
from . import views
urlpatterns = [
    path("", views.page, name="chat"),
    path("ask", views.ask, name="chat_ask"),
    path("recent", views.recent, name="chat_recent")
]
