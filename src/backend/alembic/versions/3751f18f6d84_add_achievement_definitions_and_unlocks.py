"""add achievement definitions and unlocks

Revision ID: 3751f18f6d84
Revises: e52330232643
Create Date: 2026-10-20 22:25:38.640099
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3751f18f6d84"
down_revision: str | None = "e52330232643"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "achievements",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("achievement_key", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column(
            "unlock_condition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("icon_key", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('learning', 'streak', 'difficulty')",
            name=op.f("ck_achievements_category"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_achievements"),
        ),
        sa.UniqueConstraint(
            "achievement_key",
            name=op.f("uq_achievements_achievement_key"),
        ),
    )

    op.create_table(
        "user_achievements",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("achievement_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('ja', 'zh', 'fr', 'de', 'ko', 'es')",
            name=op.f("ck_user_achievements_language"),
        ),
        sa.ForeignKeyConstraint(
            ["achievement_id"],
            ["achievements.id"],
            name=op.f("fk_user_achievements_achievement_id_achievements"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_achievements_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_user_achievements"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "achievement_id",
            "language",
            name=op.f("uq_user_achievements_user_id_achievement_id_language"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    # Enforce the category-language relationship for achievement unlocks.
    op.execute(
        """
        CREATE FUNCTION validate_user_achievement_language()
        RETURNS trigger AS $$
        DECLARE
            achievement_category text;
        BEGIN
            SELECT category INTO achievement_category
            FROM achievements
            WHERE id = NEW.achievement_id;

            IF achievement_category = 'streak' AND NEW.language IS NOT NULL THEN
                RAISE EXCEPTION 'Streak achievements must have NULL language'
                    USING ERRCODE = '23514';
            END IF;

            IF achievement_category IN ('learning', 'difficulty')
               AND NEW.language IS NULL THEN
                RAISE EXCEPTION 'Learning and difficulty achievements require a language'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_user_achievement_language
        BEFORE INSERT OR UPDATE OF achievement_id, language
        ON user_achievements
        FOR EACH ROW
        EXECUTE FUNCTION validate_user_achievement_language();
        """
    )

    # Seed the seven achievements defined in the technical specification.
    achievements = sa.table(
        "achievements",
        sa.column("achievement_key", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("unlock_condition", postgresql.JSONB()),
        sa.column("icon_key", sa.Text()),
        sa.column("is_active", sa.Boolean()),
    )

    op.bulk_insert(
        achievements,
        [
            {
                "achievement_key": "first_step",
                "category": "learning",
                "unlock_condition": {
                    "type": "locations_completed",
                    "threshold": 1,
                },
                "icon_key": "first_step",
                "is_active": True,
            },
            {
                "achievement_key": "starter",
                "category": "learning",
                "unlock_condition": {
                    "type": "locations_completed",
                    "threshold": 5,
                },
                "icon_key": "starter",
                "is_active": True,
            },
            {
                "achievement_key": "explorer",
                "category": "learning",
                "unlock_condition": {
                    "type": "locations_completed",
                    "threshold": 20,
                },
                "icon_key": "explorer",
                "is_active": True,
            },
            {
                "achievement_key": "campus_master",
                "category": "learning",
                "unlock_condition": {
                    "type": "all_playable_locations",
                },
                "icon_key": "campus_master",
                "is_active": True,
            },
            {
                "achievement_key": "week_warrior",
                "category": "streak",
                "unlock_condition": {
                    "type": "streak_days",
                    "threshold": 7,
                },
                "icon_key": "week_warrior",
                "is_active": True,
            },
            {
                "achievement_key": "challenge_master",
                "category": "difficulty",
                "unlock_condition": {
                    "type": "challenging_locations_completed",
                    "threshold": 10,
                },
                "icon_key": "challenge_master",
                "is_active": True,
            },
            {
                "achievement_key": "perfect_session",
                "category": "learning",
                "unlock_condition": {
                    "type": "perfect_session",
                    "locations": 5,
                },
                "icon_key": "perfect_session",
                "is_active": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_user_achievement_language ON user_achievements")
    op.execute("DROP FUNCTION validate_user_achievement_language()")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
