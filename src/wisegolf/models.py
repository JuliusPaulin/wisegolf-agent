from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TeeSlot(BaseModel):
    """One row from GET /reservations/?productid=&date=&golf=1."""
    model_config = ConfigDict(populate_by_name=True)

    reservation_time_id: int = Field(alias="reservationTimeId")
    start: datetime
    end: datetime
    status: int
    quantity: int
    is_user_reservation: bool = Field(alias="isUserReservation", default=False)
    is_sellable: bool = Field(alias="isSellable", default=False)
    label: str | None = None
    override_name: str | None = Field(alias="overrideName", default=None)
    resources: list[dict[str, Any]] = []
    raw: dict[str, Any] | None = None

    @property
    def hhmm(self) -> str:
        return self.start.strftime("%H:%M")


class Player(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    person_id: int = Field(alias="personId")
    player_id: str | None = Field(alias="playerId", default=None)
    member_no: str | None = Field(alias="memberNO", default=None)
    first_name: str | None = Field(alias="firstName", default=None)
    family_name: str | None = Field(alias="familyName", default=None)
    club_id: int | None = Field(alias="clubId", default=None)


class GolfReservation(BaseModel):
    """My-bookings row."""
    reservation_time_id: int | None = Field(alias="reservationTimeId", default=None)
    start: datetime | None = None
    end: datetime | None = None
    raw: dict[str, Any] | None = None


class BookingRequest(BaseModel):
    slot: TeeSlot
    person_ids: list[int]
    note: str | None = None


class BookingResult(BaseModel):
    reservation_time_id: int
    person_ids: list[int]
    confirmed_at: datetime
    raw: dict[str, Any] | None = None
