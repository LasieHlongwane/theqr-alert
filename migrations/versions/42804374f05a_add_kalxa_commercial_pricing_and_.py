"""add Kalxa commercial pricing and distribution

Revision ID: 42804374f05a
Revises: f57dde65cd0c
Create Date: 2026-09-07 15:59:58.334239

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '42804374f05a'
down_revision = 'f57dde65cd0c'
branch_labels = None
depends_on = None

def upgrade():

    # =====================================================
    # CONTENT ITEM COMMERCIAL FIELDS
    # =====================================================

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "pricing_model",
                sa.String(
                    length=30
                ),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "commercial_duration_days",
                sa.Integer(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "commercial_starts_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "commercial_expires_at",
                sa.DateTime(),
                nullable=True,
            )
        )

        # -----------------------------------------------
        # Temporarily nullable so existing records can
        # safely be migrated.
        # -----------------------------------------------

        batch_op.add_column(
            sa.Column(
                "payment_status",
                sa.String(
                    length=30
                ),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "amount_due",
                sa.Numeric(
                    precision=10,
                    scale=2,
                ),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "amount_paid",
                sa.Numeric(
                    precision=10,
                    scale=2,
                ),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "payment_reference",
                sa.String(
                    length=150
                ),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "paid_at",
                sa.DateTime(),
                nullable=True,
            )
        )


    # =====================================================
    # BACKFILL EXISTING CONTENT
    #
    # Everything already in Kalxa existed before the
    # payment engine, so don't classify it as unpaid.
    #
    # "waived" means Kalxa intentionally allows it.
    # =====================================================

    op.execute(
        """
        UPDATE content_items
        SET payment_status = 'waived'
        WHERE payment_status IS NULL
        """
    )


    # =====================================================
    # NOW MAKE PAYMENT STATUS REQUIRED
    # =====================================================

    with op.batch_alter_table(
        "content_items",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "payment_status",
            existing_type=sa.String(
                length=30
            ),
            nullable=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_pricing_model"
            ),
            [
                "pricing_model"
            ],
            unique=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_commercial_starts_at"
            ),
            [
                "commercial_starts_at"
            ],
            unique=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_commercial_expires_at"
            ),
            [
                "commercial_expires_at"
            ],
            unique=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_payment_status"
            ),
            [
                "payment_status"
            ],
            unique=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_items_payment_reference"
            ),
            [
                "payment_reference"
            ],
            unique=False,
        )


    # =====================================================
    # DISTRIBUTION ZONE TABLE
    # =====================================================

    op.create_table(

        "content_distribution_zones",

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "content_item_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.Column(
            "zone_id",
            sa.Integer(),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            [
                "content_item_id"
            ],
            [
                "content_items.id"
            ],
            ondelete="CASCADE",
        ),

        sa.ForeignKeyConstraint(
            [
                "zone_id"
            ],
            [
                "zones.id"
            ],
            ondelete="CASCADE",
        ),

        sa.PrimaryKeyConstraint(
            "id"
        ),

        sa.UniqueConstraint(
            "content_item_id",
            "zone_id",
            name=(
                "uq_content_item_distribution_zone"
            ),
        ),

    )


    # =====================================================
    # DISTRIBUTION INDEXES
    # =====================================================

    with op.batch_alter_table(
        "content_distribution_zones",
        schema=None,
    ) as batch_op:

        batch_op.create_index(
            batch_op.f(
                "ix_content_distribution_zones_content_item_id"
            ),
            [
                "content_item_id"
            ],
            unique=False,
        )


        batch_op.create_index(
            batch_op.f(
                "ix_content_distribution_zones_zone_id"
            ),
            [
                "zone_id"
            ],
            unique=False,
        )

def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('content_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_content_items_pricing_model'))
        batch_op.drop_index(batch_op.f('ix_content_items_payment_status'))
        batch_op.drop_index(batch_op.f('ix_content_items_payment_reference'))
        batch_op.drop_index(batch_op.f('ix_content_items_commercial_starts_at'))
        batch_op.drop_index(batch_op.f('ix_content_items_commercial_expires_at'))
        batch_op.drop_column('paid_at')
        batch_op.drop_column('payment_reference')
        batch_op.drop_column('amount_paid')
        batch_op.drop_column('amount_due')
        batch_op.drop_column('payment_status')
        batch_op.drop_column('commercial_expires_at')
        batch_op.drop_column('commercial_starts_at')
        batch_op.drop_column('commercial_duration_days')
        batch_op.drop_column('pricing_model')

    with op.batch_alter_table('content_distribution_zones', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_content_distribution_zones_zone_id'))
        batch_op.drop_index(batch_op.f('ix_content_distribution_zones_content_item_id'))

    op.drop_table('content_distribution_zones')
    # ### end Alembic commands ###
