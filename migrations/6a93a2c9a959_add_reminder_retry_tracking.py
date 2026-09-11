"""add reminder retry tracking

Revision ID: 6a93a2c9a959
Revises: 20d76c8bcd9b
Create Date: 2026-09-11 08:59:57.980751

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a93a2c9a959'
down_revision = '20d76c8bcd9b'
branch_labels = None
depends_on = None

def upgrade():

    # =====================================================
    # ADD RETRY TRACKING COLUMNS
    # =====================================================

    with op.batch_alter_table(
        "content_reminders",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

        batch_op.add_column(
            sa.Column(
                "next_retry_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "last_attempt_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "last_error",
                sa.Text(),
                nullable=True,
            )
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_reminders_next_retry_at"
            ),
            [
                "next_retry_at"
            ],
            unique=False,
        )


    # =====================================================
    # OPTIONAL:
    # REMOVE DATABASE-LEVEL DEFAULT AFTER EXISTING ROWS
    # HAVE BEEN BACKFILLED.
    #
    # New values will still get default=0 from the model.
    # =====================================================

    with op.batch_alter_table(
        "content_reminders",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "retry_count",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )


def downgrade():

    with op.batch_alter_table(
        "content_reminders",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            batch_op.f(
                "ix_content_reminders_next_retry_at"
            )
        )

        batch_op.drop_column(
            "last_error"
        )

        batch_op.drop_column(
            "last_attempt_at"
        )

        batch_op.drop_column(
            "next_retry_at"
        )

        batch_op.drop_column(
            "retry_count"
        )
    # ### end Alembic commands ###
