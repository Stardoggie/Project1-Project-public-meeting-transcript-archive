from sqlalchemy import ForeignKey,String,Text
from sqlalchemy.orm import Mapped,mapped_column,relationship
from meeting_transcript.extensions import db
from datetime import datetime,timezone


class GoverningBody(db.Model):
    """
        database model for governing body
    """
    __tablename__ = "governing_body"
    id:Mapped[int] = mapped_column(autoincrement=True,primary_key=True)
    name:Mapped[str] = mapped_column(String(60),nullable=False)
    body:Mapped[str] = mapped_column(String(20),nullable=False)
    description:Mapped[str] = mapped_column(Text,nullable=False)  #does it need a description?

    meetings: Mapped[list["Meeting"]] = relationship(
    back_populates="body",
    cascade="all, delete-orphan"
)






