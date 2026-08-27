"""Validated request model shared by the API and primary Streamlit UI."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, computed_field

TransportMode = Literal["WALK", "DRIVE", "TRANSIT", "BICYCLE"]


class Origin(BaseModel):
    """Optional starting point for future route planning."""

    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    label: str = "Your location"

    @property
    def is_resolved(self) -> bool:
        return self.lat is not None and self.lon is not None


class TripRequest(BaseModel):
    """Single source of truth for planning inputs across entrypoints."""

    city: str = "Calgary"
    start_date: date | None = None
    days: int = Field(default=2, ge=1, le=7)
    origin: Origin = Field(default_factory=Origin)
    budget_total: float = Field(default=500, gt=0)
    party_size: int = Field(default=2, ge=1)
    cuisines: list[str] = Field(default_factory=lambda: ["international"])
    attraction_types: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    search_radius_km: float = Field(default=5.0, gt=0)
    max_leg_minutes: float = Field(default=25.0, gt=0)
    max_daily_travel_minutes: float = Field(default=120.0, gt=0)
    transport_mode: TransportMode = "WALK"
    attractions_per_day: int = Field(default=1, ge=0, le=3)
    tier: int = Field(default=2, ge=1, le=2)
    data_backend: Literal["auto", "live", "local"] = "auto"
    # Backward-compatible alias used by the existing scenarios and orchestrator.
    max_walk_km: float | None = Field(default=None, gt=0)

    @computed_field
    @property
    def dates(self) -> list[date]:
        if self.start_date is None:
            return []
        return [self.start_date + timedelta(days=index) for index in range(self.days)]

    @computed_field
    @property
    def weekdays(self) -> list[str]:
        return [day.strftime("%a").lower() for day in self.dates]

    @computed_field
    @property
    def day_labels(self) -> list[str]:
        if not self.dates:
            return [f"Day {index + 1}" for index in range(self.days)]
        return [f"Day {index + 1} · {day.strftime('%a %b')} {day.day}"
                for index, day in enumerate(self.dates)]

    def to_request_dict(self) -> dict:
        """Return a JSON-compatible dict for the existing orchestrator API."""
        data = self.model_dump(exclude={"tier", "data_backend"})
        data.pop("dates", None)
        data.pop("weekdays", None)
        data.pop("day_labels", None)
        if data.get("max_walk_km") is None:
            data.pop("max_walk_km", None)
        return data
