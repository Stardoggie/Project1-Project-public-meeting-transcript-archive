"""analysis claim timestamp, so an analysis abandoned by a process that died can be claimed again

Revision ID: ed240a3a0d12
Revises: cf3d34fda5db
Create Date: 2026-11-10 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ed240a3a0d12'
down_revision = 'cf3d34fda5db'
branch_labels = None
depends_on = None


def upgrade():
    #additive and nullable: existing meetings have no claim, and analysis_completed is left as it is
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('analysis_claimed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.drop_column('analysis_claimed_at')
