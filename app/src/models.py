from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class Direction(str, PyEnum):
    ENTRY = "entry"
    EXIT = "exit"


class BarrierAction(str, PyEnum):
    OPEN = "open"
    CLOSE = "close"
    LOCK = "lock"
    UNLOCK = "unlock"


class BarrierActor(str, PyEnum):
    ADMIN = "admin"
    TELEGRAM = "telegram"
    AUTO_ANPR = "auto_anpr"
    AUTO_LOOP = "auto_loop"


class Plate(Base):
    __tablename__ = "plates"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    links: Mapped[list["PlatePhoneLink"]] = relationship(
        back_populates="plate", cascade="all, delete-orphan"
    )


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    can_control_barrier: Mapped[bool] = mapped_column(Boolean, default=True)
    # When true, the user can press the "Mening loglarim" button in the bot
    # to see their own barrier actions and entry/exit events for linked cars.
    can_view_own_events: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # When true, this user gets every ANPR event (regardless of plate links).
    receive_all_events: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    links: Mapped[list["PlatePhoneLink"]] = relationship(
        back_populates="telegram_user", cascade="all, delete-orphan"
    )


class PlatePhoneLink(Base):
    """Links a plate to a phone number so Telegram notifications fire on entry/exit."""

    __tablename__ = "plate_phone_links"
    __table_args__ = (
        UniqueConstraint("plate_id", "telegram_user_id", name="uq_plate_phone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("plates.id", ondelete="CASCADE"))
    telegram_user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    plate: Mapped[Plate] = relationship(back_populates="links")
    telegram_user: Mapped[TelegramUser] = relationship(back_populates="links")


class EntryExitLog(Base):
    """Every time the ANPR cameras see a plate — stored here."""

    __tablename__ = "entry_exit_logs"
    __table_args__ = (
        Index("ix_entry_exit_plate_time", "plate_number", "event_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_number: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[Direction] = mapped_column(
        Enum(Direction, name="direction", values_callable=lambda x: [e.value for e in x]),
        index=True,
    )
    camera_name: Mapped[str] = mapped_column(String(64))
    camera_host: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[int | None] = mapped_column(nullable=True)
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    plate_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)


class BarrierActionLog(Base):
    """Tracks every manual/auto open / close of the barrier."""

    __tablename__ = "barrier_action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_name: Mapped[str] = mapped_column(String(64))
    action: Mapped[BarrierAction] = mapped_column(
        Enum(BarrierAction, name="barrieraction", values_callable=lambda x: [e.value for e in x]),
        index=True,
    )
    actor: Mapped[BarrierActor] = mapped_column(
        Enum(BarrierActor, name="barrieractor", values_callable=lambda x: [e.value for e in x]),
        index=True,
    )
    actor_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    plate_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(nullable=True)
    status_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
