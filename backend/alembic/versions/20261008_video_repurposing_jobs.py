"""Add isolated durable job checkpoints for video repurposing.

Revision ID: video_repurpose_20261008
Revises: multimodal_jobs_20260918
"""

from alembic import op
import sqlalchemy as sa


revision = "video_repurpose_20261008"
down_revision = "multimodal_jobs_20260918"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_repurposing_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_video_repurposing_jobs_owner_id",
        "video_repurposing_jobs", ["owner_id"],
    )
    op.create_index(
        "ix_video_repurposing_jobs_status",
        "video_repurposing_jobs", ["status"],
    )


def downgrade():
    op.drop_index("ix_video_repurposing_jobs_status", "video_repurposing_jobs")
    op.drop_index("ix_video_repurposing_jobs_owner_id", "video_repurposing_jobs")
    op.drop_table("video_repurposing_jobs")
