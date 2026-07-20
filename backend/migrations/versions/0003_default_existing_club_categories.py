"""assign the academic category to uncategorized club revisions

Revision ID: 0003_default_existing_club_categories
Revises: 0002_club_display_order
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_default_existing_club_categories"
down_revision = "0002_club_display_order"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    academic_id = bind.execute(sa.text("SELECT id FROM club_categories WHERE slug = 'academic' AND is_active = 1 LIMIT 1")).scalar()
    if academic_id is not None:
        bind.execute(sa.text("UPDATE club_revisions SET category_id = :category_id WHERE category_id IS NULL"), {"category_id": academic_id})


def downgrade():
    # Existing categories may have been deliberately set by users after this migration.
    pass
