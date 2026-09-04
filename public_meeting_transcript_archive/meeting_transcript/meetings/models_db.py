from sqlalchemy import ForeignKey,String,Text,Boolean,Date
from sqlalchemy.orm import Mapped,mapped_column,relationship
from meeting_transcript.extensions import db
from datetime import datetime,timezone,date


class Meeting(db.Model):
    """
        database model for meetings of governing bodies
    """
    __tablename__ = "meetings"
    id:Mapped[int] = mapped_column(primary_key=True,autoincrement=True)
    meeting_date:Mapped[date] =mapped_column(Date,nullable=False)
    title: Mapped[str] = mapped_column(Text,nullable=False)
    status:Mapped[str] = mapped_column(String(20),nullable=False,default="scheduled")
    audio_object_key:Mapped[str|None] = mapped_column(Text,nullable=True) #these are nullable due to having meetings that havent taken place yet, therefore dont have audio
    transcribe_job_name:Mapped[str |None] = mapped_column(Text,nullable=True)#     or transcriptions
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_completed: Mapped[bool] = mapped_column(default=False,nullable=True)
    #foreign key stuff and cascading deletion when this is deleted
    body_id:Mapped[int] = mapped_column(ForeignKey("governing_body.id"),nullable=False)
    body: Mapped["GoverningBody"] = relationship(
        back_populates="meetings"
    )

    entities: Mapped[list["Entities"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan"
    )

    keys: Mapped[list["KeyPhrases"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan"
    )
    @property
    def transcript_available(self) -> bool:
        return self.transcript is not None


class Entities(db.Model):
    """
        tables for key entities for meetings taken from transcript
    """
    __tablename__ = "entities"
    id:Mapped[int] = mapped_column(primary_key=True,autoincrement=True)
    entity:Mapped[str] = mapped_column(String(150),nullable=False)
    entity_count:Mapped[int] = mapped_column(default=0,nullable=False)
    #foreign keys
    meeting_id:Mapped[int] = mapped_column(ForeignKey("meetings.id"),nullable=False)
    meeting: Mapped["Meeting"] = relationship(
        back_populates="entities"
    )

class KeyPhrases(db.Model):
    """
        tables for key phrases for meetings taken from transcript
    """
    __tablename__ = "keyphrases"
    id:Mapped[int] = mapped_column(primary_key=True,autoincrement=True)
    phrase:Mapped[str] = mapped_column(String(150),nullable=False)
    #foreign keys
    meeting_id:Mapped[int] = mapped_column(ForeignKey("meetings.id"),nullable=False)
    meeting: Mapped["Meeting"] = relationship(
        back_populates="keys"
    )