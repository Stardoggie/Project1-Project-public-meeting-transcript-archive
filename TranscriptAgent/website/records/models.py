"""The archive's tables, as Django sees them for the admin.

They're unmanaged: the archive's SQLAlchemy models (meeting_transcript/*/models_db.py) and its Alembic migrations
own the tables, and Django never creates or changes them. Keep the fields here in step with those models.
Saving and deleting go through the archive's store functions (see admin.py), so its checks and locking still apply.
"""
from django.core.validators import MinLengthValidator
from django.db import models
BODY_TYPES = [("council", "Council"), ("commission", "Commission"), ("board", "Board")]
MEETING_STATUSES = [
    ("scheduled", "Scheduled"),
    ("recorded", "Recorded"),
    ("transcribing", "Transcribing"),
    ("archived", "Archived")
]


class GoverningBody(models.Model):
    name = models.CharField(max_length=60)
    body = models.CharField(max_length=20, choices=BODY_TYPES)
    description = models.TextField(validators=[MinLengthValidator(5)])

    class Meta:
        managed = False
        db_table = "governing_body"
        verbose_name = "governing body"
        verbose_name_plural = "governing bodies"
        ordering = ["id"]

    def __str__(self):
        return self.name


class Meeting(models.Model):
    body = models.ForeignKey(GoverningBody, on_delete=models.CASCADE, related_name="meetings",
                             verbose_name="governing body")
    title = models.CharField(max_length=60)
    meeting_date = models.DateField()
    status = models.CharField(max_length=20, choices=MEETING_STATUSES, default="scheduled")
    audio_object_key = models.TextField(null=True, blank=True, verbose_name="recording")
    transcribe_job_name = models.TextField(null=True, blank=True, verbose_name="transcription job")
    transcript = models.TextField(null=True, blank=True)
    analysis_completed = models.BooleanField(null=True, default=False)
    analysis_claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "meetings"
        ordering = ["-meeting_date", "-id"]

    def __str__(self):
        return f"{self.title} ({self.meeting_date})"


class Entity(models.Model):
    """A person, place or organization Comprehend found in a meeting's transcript."""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="entities")
    entity = models.CharField(max_length=150)
    entity_count = models.IntegerField(default=0, verbose_name="mentions")

    class Meta:
        managed = False
        db_table = "entities"
        verbose_name_plural = "entities"
        ordering = ["-entity_count"]

    def __str__(self):
        return self.entity


class KeyPhrase(models.Model):
    """A key phrase Comprehend found in a meeting's transcript."""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="key_phrases")
    phrase = models.CharField(max_length=150)

    class Meta:
        managed = False
        db_table = "keyphrases"
        verbose_name = "key phrase"

    def __str__(self):
        return self.phrase
