"""add persisted public club display order

Revision ID: 0002_club_display_order
Revises: 0001_initial_schema
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_club_display_order"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clubs")}
    if "sort_order" not in columns:
        op.add_column("clubs", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clubs")}
    if "ix_clubs_sort_order" not in indexes:
        op.create_index("ix_clubs_sort_order", "clubs", ["sort_order"])


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("clubs")}
    if "ix_clubs_sort_order" in indexes:
        op.drop_index("ix_clubs_sort_order", table_name="clubs")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("clubs")}
    if "sort_order" in columns:
        op.drop_column("clubs", "sort_order")
