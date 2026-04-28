from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlateCreate(BaseModel):
    plate_number: str = Field(min_length=2, max_length=32)
    owner_name: str | None = None
    is_allowed: bool = True
    note: str | None = None


class PlateUpdate(BaseModel):
    plate_number: str | None = Field(default=None, min_length=2, max_length=32)
    owner_name: str | None = None
    is_allowed: bool | None = None
    note: str | None = None


class PlateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plate_number: str
    owner_name: str | None
    is_allowed: bool
    note: str | None
    created_at: datetime


class TelegramUserCreate(BaseModel):
    phone_number: str = Field(min_length=4, max_length=32)
    full_name: str | None = None
    is_active: bool = True
    can_control_barrier: bool = True
    can_view_own_events: bool = True
    receive_all_events: bool = False


class TelegramUserUpdate(BaseModel):
    phone_number: str | None = Field(default=None, min_length=4, max_length=32)
    full_name: str | None = None
    is_active: bool | None = None
    can_control_barrier: bool | None = None
    can_view_own_events: bool | None = None
    receive_all_events: bool | None = None


class TelegramUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    full_name: str | None
    telegram_chat_id: int | None
    telegram_username: str | None
    is_active: bool
    can_control_barrier: bool
    can_view_own_events: bool
    receive_all_events: bool
    created_at: datetime


class LinkCreate(BaseModel):
    plate_id: int
    telegram_user_id: int


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plate_id: int
    telegram_user_id: int
    created_at: datetime


class EntryExitLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plate_number: str
    direction: str
    camera_name: str
    camera_host: str
    confidence: int | None
    is_allowed: bool
    plate_color: str | None
    event_time: datetime


class BarrierCommand(BaseModel):
    role: str = Field(description="'entry' or 'exit'")
    mode: str = Field(description="'open' | 'close' | 'lock' | 'unlock'")


class BarrierResult(BaseModel):
    ok: bool
    status_code: int
    message: str
