
"""add job location classification

Revision ID: 0ac6220890ee
Revises: 6d5e788b3fbb
Create Date: 2026-09-23 07:41:23.140204

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0ac6220890ee"
down_revision = "6d5e788b3fbb"
branch_labels = None
depends_on = None


def upgrade():

    # ========================================================
    # CONTENT ITEMS
    #
    # Stores the public location classification after an
    # approved Jobs submission becomes a ContentItem.
    #
    # Examples:
    #
    # KwaMhlanga
    # Mpumalanga
    # Gauteng
    # National
    # Remote
    # ========================================================

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "location_classification",
                sa.String(
                    length=50
                ),
                nullable=True,
            )
        )


        batch_op.create_index(
            "ix_content_items_location_classification",
            [
                "location_classification",
            ],
            unique=False,
        )


    # ========================================================
    # PENDING SUBMISSIONS
    #
    # Stores the classification while an imported/community
    # job is waiting for moderation.
    # ========================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "location_classification",
                sa.String(
                    length=50
                ),
                nullable=True,
            )
        )


        batch_op.create_index(
            "ix_pending_submissions_location_classification",
            [
                "location_classification",
            ],
            unique=False,
        )


def downgrade():

    # ========================================================
    # PENDING SUBMISSIONS
    # ========================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            "ix_pending_submissions_location_classification"
        )


        batch_op.drop_column(
            "location_classification"
        )


    # ========================================================
    # CONTENT ITEMS
    # ========================================================

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            "ix_content_items_location_classification"
        )


        batch_op.drop_column(
            "location_classification"
        )

