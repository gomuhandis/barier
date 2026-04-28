"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-24 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    """Postgres enum — create/drop handled explicitly so we can be idempotent."""
    return postgresql.ENUM(*values, name=name, create_type=False)


direction_enum = _enum("direction", "entry", "exit")
barrier_action_enum = _enum("barrieraction", "open", "close", "lock", "unlock")
barrier_actor_enum = _enum(
    "barrieractor", "admin", "telegram", "auto_anpr", "auto_loop"
)


def upgrade() -> None:
    bind = op.get_bind()
    # Create enum types once. `checkfirst=True` no-ops if they already exist.
    direction_enum.create(bind, checkfirst=True)
    barrier_action_enum.create(bind, checkfirst=True)
    barrier_actor_enum.create(bind, checkfirst=True)

    op.create_table(
        "plates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plate_number", sa.String(32), nullable=False, unique=True),
        sa.Column("owner_name", sa.String(128), nullable=True),
        sa.Column("is_allowed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_plates_plate_number", "plates", ["plate_number"])

    op.create_table(
        "telegram_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("phone_number", sa.String(32), nullable=False, unique=True),
        sa.Column("full_name", sa.String(128), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger, nullable=True, unique=True),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "can_control_barrier",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_telegram_users_phone_number", "telegram_users", ["phone_number"])

    op.create_table(
        "plate_phone_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "plate_id",
            sa.Integer,
            sa.ForeignKey("plates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "telegram_user_id",
            sa.Integer,
            sa.ForeignKey("telegram_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("plate_id", "telegram_user_id", name="uq_plate_phone"),
    )

    op.create_table(
        "entry_exit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plate_number", sa.String(32), nullable=False),
        sa.Column("direction", direction_enum, nullable=False),
        sa.Column("camera_name", sa.String(64), nullable=False),
        sa.Column("camera_host", sa.String(128), nullable=False),
        sa.Column("confidence", sa.Integer, nullable=True),
        sa.Column("is_allowed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("plate_color", sa.String(32), nullable=True),
        sa.Column(
            "event_time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("raw_payload", sa.Text, nullable=True),
    )
    op.create_index("ix_entry_exit_logs_plate_number", "entry_exit_logs", ["plate_number"])
    op.create_index("ix_entry_exit_logs_direction", "entry_exit_logs", ["direction"])
    op.create_index("ix_entry_exit_logs_event_time", "entry_exit_logs", ["event_time"])
    op.create_index(
        "ix_entry_exit_plate_time", "entry_exit_logs", ["plate_number", "event_time"]
    )

    op.create_table(
        "barrier_action_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("camera_name", sa.String(64), nullable=False),
        sa.Column("action", barrier_action_enum, nullable=False),
        sa.Column("actor", barrier_actor_enum, nullable=False),
        sa.Column("actor_detail", sa.String(256), nullable=True),
        sa.Column("plate_number", sa.String(32), nullable=True),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("status_message", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_barrier_action_logs_action", "barrier_action_logs", ["action"]
    )
    op.create_index("ix_barrier_action_logs_actor", "barrier_action_logs", ["actor"])
    op.create_index(
        "ix_barrier_action_logs_plate_number",
        "barrier_action_logs",
        ["plate_number"],
    )
    op.create_index(
        "ix_barrier_action_logs_created_at", "barrier_action_logs", ["created_at"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("barrier_action_logs")
    op.drop_table("entry_exit_logs")
    op.drop_table("plate_phone_links")
    op.drop_table("telegram_users")
    op.drop_table("plates")
    barrier_actor_enum.drop(bind, checkfirst=True)
    barrier_action_enum.drop(bind, checkfirst=True)
    direction_enum.drop(bind, checkfirst=True)
