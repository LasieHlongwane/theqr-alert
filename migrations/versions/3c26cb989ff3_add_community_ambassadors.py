"""add community ambassadors

Revision ID: 3c26cb989ff3
Revises: 0ac6220890ee
Create Date: 2026-10-04 01:27:42.274613

"""

from alembic import op
import sqlalchemy as sa


# ============================================================
# REVISION IDENTIFIERS
# ============================================================

revision = "3c26cb989ff3"
down_revision = "0ac6220890ee"
branch_labels = None
depends_on = None


# ============================================================
# UPGRADE
# ============================================================

def upgrade():

    # ========================================================
    # COMMUNITY AMBASSADORS
    # ========================================================

    op.create_table(

        "community_ambassadors",


        # ====================================================
        # PRIMARY KEY
        # ====================================================

        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),


        # ====================================================
        # IDENTITY
        # ====================================================

        sa.Column(
            "name",
            sa.String(
                length=150,
            ),
            nullable=False,
        ),

        sa.Column(
            "email",
            sa.String(
                length=255,
            ),
            nullable=False,
        ),

        sa.Column(
            "phone",
            sa.String(
                length=50,
            ),
            nullable=True,
        ),


        # ====================================================
        # AUTHENTICATION
        # ====================================================

        sa.Column(
            "password_hash",
            sa.String(
                length=255,
            ),
            nullable=False,
        ),


        # ====================================================
        # COMMUNITY / ZONE ASSIGNMENT
        # ====================================================

        sa.Column(
            "zone_id",
            sa.Integer(),
            nullable=False,
        ),


        # ====================================================
        # ACCOUNT STATUS
        # ====================================================

        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
        ),


        # ====================================================
        # TIMESTAMPS
        # ====================================================

        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),

        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),


        # ====================================================
        # FOREIGN KEY
        # ====================================================

        sa.ForeignKeyConstraint(
            [
                "zone_id",
            ],
            [
                "zones.id",
            ],
            ondelete="RESTRICT",
        ),


        # ====================================================
        # PRIMARY KEY CONSTRAINT
        # ====================================================

        sa.PrimaryKeyConstraint(
            "id",
        ),

    )


    # ========================================================
    # COMMUNITY AMBASSADOR INDEXES
    # ========================================================

    with op.batch_alter_table(
        "community_ambassadors",
        schema=None,
    ) as batch_op:

        batch_op.create_index(
            batch_op.f(
                "ix_community_ambassadors_active"
            ),
            [
                "active",
            ],
            unique=False,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_community_ambassadors_email"
            ),
            [
                "email",
            ],
            unique=True,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_community_ambassadors_phone"
            ),
            [
                "phone",
            ],
            unique=True,
        )

        batch_op.create_index(
            batch_op.f(
                "ix_community_ambassadors_zone_id"
            ),
            [
                "zone_id",
            ],
            unique=False,
        )


# ============================================================
# DOWNGRADE
# ============================================================

def downgrade():

    # ========================================================
    # REMOVE COMMUNITY AMBASSADOR INDEXES
    # ========================================================

    with op.batch_alter_table(
        "community_ambassadors",
        schema=None,
    ) as batch_op:

        batch_op.drop_index(
            batch_op.f(
                "ix_community_ambassadors_zone_id"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_community_ambassadors_phone"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_community_ambassadors_email"
            )
        )

        batch_op.drop_index(
            batch_op.f(
                "ix_community_ambassadors_active"
            )
        )


    # ========================================================
    # REMOVE COMMUNITY AMBASSADORS TABLE
    # ========================================================

    op.drop_table(
        "community_ambassadors"
    )