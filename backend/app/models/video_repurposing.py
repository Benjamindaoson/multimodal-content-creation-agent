"""Durable checkpoints for long-video content repurposing."""

from sqlalchemy import JSON, Column, DateTime, String
from sqlalchemy.sql import func

from app.core.database import Base


class VideoRepurposingJob(Base):
    __tablename__ = "video_repurposing_jobs"

    job_id = Column(String(64), primary_key=True)
    owner_id = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    source_path = Column(String(1024), nullable=False)
    state_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
