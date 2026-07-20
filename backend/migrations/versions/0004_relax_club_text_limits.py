"""relax club text field limits

Revision ID: 0004_relax_club_text_limits
Revises: 0003_default_existing_club_categories
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_relax_club_text_limits"
down_revision = "0003_default_existing_club_categories"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("club_revisions") as batch:
        batch.alter_column("name", existing_type=sa.String(length=30), type_=sa.String(length=50), existing_nullable=False)
        batch.alter_column("short_intro", existing_type=sa.String(length=100), type_=sa.String(length=200), existing_nullable=False)
        batch.alter_column("recruitment_slogan", existing_type=sa.String(length=80), type_=sa.String(length=120), existing_nullable=False)


def downgrade():
    with op.batch_alter_table("club_revisions") as batch:
        batch.alter_column("name", existing_type=sa.String(length=50), type_=sa.String(length=30), existing_nullable=False)
        batch.alter_column("short_intro", existing_type=sa.String(length=200), type_=sa.String(length=100), existing_nullable=False)
        batch.alter_column("recruitment_slogan", existing_type=sa.String(length=120), type_=sa.String(length=80), existing_nullable=False)
