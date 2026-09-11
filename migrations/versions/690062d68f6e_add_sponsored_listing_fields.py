"""add sponsored listing fields

Revision ID: 690062d68f6e
Revises: 6a93a2c9a959
Create Date: 2026-09-11 11:33:15.336762

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '690062d68f6e'
down_revision = '6a93a2c9a959'
branch_labels = None
depends_on = None

def upgrade():

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "is_sponsored",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

        batch_op.add_column(
            sa.Column(
                "sponsorship_status",
                sa.String(length=30),
                nullable=False,
                server_default="inactive",
            )
        )

        batch_op.add_column(
            sa.Column(
                "sponsored_starts_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "sponsored_expires_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "sponsored_priority",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

        batch_op.add_column(
            sa.Column(
                "sponsorship_reference",
                sa.String(length=150),
                nullable=True,
            )
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_is_sponsored"
            ),
            [
                "is_sponsored"
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_content_items_sponsorship_status"
            ),
            [
                "sponsorship_status"
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_content_items_sponsored_starts_at"
            ),
            [
                "sponsored_starts_at"
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_content_items_sponsored_expires_at"
            ),
            [
                "sponsored_expires_at"
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_content_items_sponsored_priority"
            ),
            [
                "sponsored_priority"
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_content_items_sponsorship_reference"
            ),
            [
                "sponsorship_reference"
            ],
            unique=False,
        )


    # =====================================================
    # REMOVE TEMPORARY DATABASE DEFAULTS
    #
    # Existing rows have now safely received:
    #
    # is_sponsored = False
    # sponsorship_status = inactive
    # sponsored_priority = 0
    #
    # The SQLAlchemy model handles defaults for new rows.
    # =====================================================

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "is_sponsored",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=None,
        )

        batch_op.alter_column(
            "sponsorship_status",
            existing_type=sa.String(length=30),
            nullable=False,
            server_default=None,
        )

        batch_op.alter_column(
            "sponsored_priority",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=None,
        )
    # ### end Alembic commands ###

def downgrade():

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_sponsorship_reference"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_sponsored_priority"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_sponsored_expires_at"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_sponsored_starts_at"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_sponsorship_status"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_content_items_is_sponsored"
            )
        )


        batch_op.drop_column(
            "sponsorship_reference"
        )

        batch_op.drop_column(
            "sponsored_priority"
        )

        batch_op.drop_column(
            "sponsored_expires_at"
        )

        batch_op.drop_column(
            "sponsored_starts_at"
        )

        batch_op.drop_column(
            "sponsorship_status"
        )

        batch_op.drop_column(
            "is_sponsored"
        )
    # ### end Alembic commands ###
