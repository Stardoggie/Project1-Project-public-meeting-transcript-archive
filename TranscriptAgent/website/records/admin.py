"""The clerks' admin for governing bodies and meetings.

Django's forms check what's typed, but every save and delete runs the archive's own store function (through
archive_site.archive.run), the same code the clerks' agent uses, so its validation and row locking still apply.
"""
from django.contrib import admin
from django.urls import path, reverse
from django.utils.html import format_html
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.meetings import store as meetings
from archive_site.archive import run
from . import accounts, recording  # noqa: F401  (accounts registers the user admin)
from .models import Entity, GoverningBody, KeyPhrase, Meeting
admin.site.site_header = "Meeting Transcript Archive"
admin.site.site_title = "Archive admin"
admin.site.index_title = "Clerks' admin"


@admin.register(GoverningBody)
class GoverningBodyAdmin(admin.ModelAdmin):
    list_display = ["name", "body", "meeting_count", "id"]
    list_filter = ["body"]
    search_fields = ["name", "description"]
    fields = ["name", "body", "description"]

    @admin.display(description="meetings")
    def meeting_count(self, governing_body):
        return governing_body.meetings.count()

    def save_model(self, request, governing_body, form, change):
        details = {"name": governing_body.name, "body": governing_body.body, "description": governing_body.description}
        if change:
            run(bodies.update_body, governing_body.pk, details)
        else:
            governing_body.pk = run(bodies.create_body, details).id

    def delete_model(self, request, governing_body):
        run(bodies.delete_body, governing_body.pk)

    def delete_queryset(self, request, queryset):
        for governing_body in queryset:
            self.delete_model(request, governing_body)


class ReadOnlyInline(admin.TabularInline):
    """What Comprehend found in a transcript: shown, never edited by hand."""
    extra = 0
    can_delete = False

    def has_add_permission(self, request, meeting=None):
        return False

    def has_change_permission(self, request, meeting=None):
        return False


class EntityInline(ReadOnlyInline):
    model = Entity
    fields = ["entity", "entity_count"]


class KeyPhraseInline(ReadOnlyInline):
    model = KeyPhrase
    fields = ["phrase"]


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ["title", "body", "meeting_date", "status", "has_transcript", "id"]
    list_filter = ["status", "body"]
    search_fields = ["title"]
    date_hierarchy = "meeting_date"
    inlines = [EntityInline, KeyPhraseInline]
    # Only the transcription steps change these, so clerks see them but don't type them
    status_fields = ["status", "audio_object_key", "transcribe_job_name", "analysis_completed", "transcript"]

    def get_inlines(self, request, meeting=None):
        # A new meeting has no transcript yet, so nothing Comprehend found to show
        if meeting is None:
            return []
        return self.inlines

    def get_fields(self, request, meeting=None):
        if meeting is None:
            return ["body", "title", "meeting_date"]
        return ["body", "title", "meeting_date", "recording_link", *self.status_fields]

    def get_readonly_fields(self, request, meeting=None):
        if meeting is None:
            return []
        # A meeting stays with the governing body it was made for, like in the archive's store
        return ["body", "recording_link", *self.status_fields]

    @admin.display(boolean=True, description="transcript")
    def has_transcript(self, meeting):
        return meeting.transcript is not None

    @admin.display(description="recording and transcription")
    def recording_link(self, meeting):
        return format_html('<a href="{}">Upload a recording, or start or check its transcription</a>',
                           reverse("admin:meeting_recording", args=[meeting.pk]))

    def save_model(self, request, meeting, form, change):
        details = {"title": meeting.title, "meeting_date": meeting.meeting_date.isoformat()}
        if change:
            run(meetings.edit_meeting, meeting.pk, details)
        else:
            meeting.pk = run(meetings.create_meeting, meeting.body_id, details).id

    def delete_model(self, request, meeting):
        run(meetings.delete_meeting, meeting.pk)

    def delete_queryset(self, request, queryset):
        for meeting in queryset:
            self.delete_model(request, meeting)

    def get_urls(self):
        guarded = self.admin_site.admin_view
        own_urls = [
            path("<int:meeting_id>/recording/", guarded(self.recording_view), name="meeting_recording"),
            path("<int:meeting_id>/recording/form/", guarded(self.upload_form_view), name="meeting_upload_form"),
            path("<int:meeting_id>/recording/attach/", guarded(self.attach_view), name="meeting_attach"),
            path("<int:meeting_id>/transcription/start/", guarded(self.start_view), name="meeting_transcription_start"),
            path("<int:meeting_id>/transcription/check/", guarded(self.check_view), name="meeting_transcription_check")
        ]
        return own_urls + super().get_urls()

    # The recording page and the calls its buttons make; each needs the right to change meetings

    def recording_view(self, request, meeting_id):
        return recording.page(self, request, meeting_id)

    def upload_form_view(self, request, meeting_id):
        return recording.upload_form(self, request, meeting_id)

    def attach_view(self, request, meeting_id):
        return recording.attach(self, request, meeting_id)

    def start_view(self, request, meeting_id):
        return recording.start_transcription(self, request, meeting_id)

    def check_view(self, request, meeting_id):
        return recording.check_transcription(self, request, meeting_id)
