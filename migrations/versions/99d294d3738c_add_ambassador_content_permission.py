"""add ambassador content permission

Revision ID: 99d294d3738c
Revises: 3c26cb989ff3
Create Date: 2026-10-04 10:31:34.074954

"""

from alembic import op
import sqlalchemy as sa


# ============================================================
# REVISION IDENTIFIERS
# ============================================================

revision = "99d294d3738c"
down_revision = "3c26cb989ff3"
branch_labels = None
depends_on = None


# ============================================================
# UPGRADE
# ============================================================

def upgrade():

    # ========================================================
    # COMMUNITY AMBASSADOR
    # ========================================================
    #
    # Existing Ambassadors must not automatically receive
    # permission to create community content.
    #
    # server_default=False allows PostgreSQL to safely populate
    # the new NOT NULL column for existing rows.
    #
    # The default is removed afterwards so permission remains
    # controlled explicitly by the application.
    # ========================================================

    with op.batch_alter_table(
        "community_ambassadors",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "can_add_content",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


    # ========================================================
    # REMOVE DATABASE-LEVEL DEFAULT
    # ========================================================
    #
    # Existing records now contain False.
    #
    # New Ambassador records will use the SQLAlchemy model
    # default:
    #
    #     default=False
    #
    # rather than relying on a permanent database default.
    # ========================================================

    with op.batch_alter_table(
        "community_ambassadors",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "can_add_content",
            server_default=None,
        )


# ============================================================
# DOWNGRADE
# ============================================================

def downgrade():

    with op.batch_alter_table(
        "community_ambassadors",
        schema=None,
    ) as batch_op:

        batch_op.drop_column(
            "can_add_content"
        )
