"""add commercial fields to pending submissions

Revision ID: 1b835ce34501
Revises: 42804374f05a
Create Date: 2026-09-08 03:37:31.501772
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b835ce34501"
down_revision = "42804374f05a"
branch_labels = None
depends_on = None


def upgrade():

    # =====================================================
    # STEP 1
    # Add the new commercial fields.
    #
    # payment_status MUST initially be nullable because
    # existing pending_submissions rows do not yet have
    # a value for this field.
    # =====================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                "pricing_model",
                sa.String(length=30),
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
                "amount_due",
                sa.Numeric(
                    precision=10,
                    scale=2,
                ),
                nullable=True,
            )
        )

        # IMPORTANT:
        # Temporarily nullable so existing rows can survive
        # the schema change.
        batch_op.add_column(
            sa.Column(
                "payment_status",
                sa.String(length=30),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "distribution_zone_ids",
                sa.JSON(),
                nullable=True,
            )
        )

        # Increase category capacity because Kalxa has
        # production category slugs longer than the
        # original assumptions.
        batch_op.alter_column(
            "category",
            existing_type=sa.VARCHAR(length=50),
            type_=sa.String(length=100),
            existing_nullable=False,
        )

    # =====================================================
    # STEP 2
    # BACKFILL EXISTING SUBMISSIONS
    #
    # Existing submissions were created before Kalxa's
    # commercial package system.
    #
    # They should therefore NOT suddenly become unpaid
    # commercial submissions.
    #
    # "waived" identifies them as legacy/pilot records.
    # =====================================================

    op.execute(
        sa.text(
            """
            UPDATE pending_submissions
            SET payment_status = 'waived'
            WHERE payment_status IS NULL
            """
        )
    )

    # =====================================================
    # STEP 3
    # Make payment_status mandatory now that every existing
    # row has a valid value.
    # =====================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "payment_status",
            existing_type=sa.String(length=30),
            nullable=False,
        )

    # =====================================================
    # STEP 4
    # CREATE INDEXES
    # =====================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.create_index(
            batch_op.f(
                "ix_pending_submissions_category"
            ),
            ["category"],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_pending_submissions_payment_status"
            ),
            ["payment_status"],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_pending_submissions_pricing_model"
            ),
            ["pricing_model"],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_pending_submissions_status"
            ),
            ["status"],
            unique=False,
        )


def downgrade():

    # =====================================================
    # REMOVE INDEXES
    # =====================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            batch_op.f(
                "ix_pending_submissions_status"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_pending_submissions_pricing_model"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_pending_submissions_payment_status"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_pending_submissions_category"
            )
        )

    # =====================================================
    # RESTORE ORIGINAL CATEGORY LENGTH + REMOVE COMMERCIAL
    # FIELDS
    # =====================================================

    with op.batch_alter_table(
        "pending_submissions",
        schema=None,
    ) as batch_op:

        batch_op.alter_column(
            "category",
            existing_type=sa.String(length=100),
            type_=sa.VARCHAR(length=50),
            existing_nullable=False,
        )

        batch_op.drop_column(
            "distribution_zone_ids"
        )

        batch_op.drop_column(
            "payment_status"
        )

        batch_op.drop_column(
            "amount_due"
        )

        batch_op.drop_column(
            "commercial_duration_days"
        )

        batch_op.drop_column(
            "pricing_model"
        )
