"""org.is_demo (live simulator gate) and jobs.log (progress tail)

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('is_demo', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('jobs', sa.Column('log', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'log')
    op.drop_column('orgs', 'is_demo')
