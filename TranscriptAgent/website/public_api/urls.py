from django.urls import path
from . import views
urlpatterns = [
    path("", views.index),
    path("bodies", views.body_list),
    path("bodies/<int:body_id>", views.body_detail),
    path("bodies/<int:body_id>/meetings", views.body_meetings),
    path("bodies/<int:body_id>/trending-entities", views.trending_entities),
    path("meetings/<int:meeting_id>", views.meeting_detail),
    path("meetings/<int:meeting_id>/transcript", views.meeting_transcript),
    path("search", views.search)
]
