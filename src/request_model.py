"""Validated request model shared by the API and primary Streamlit UI."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

TransportMode = Literal["WALK", "DRIVE", "TRANSIT", "BICYCLE"]

# Kept in the order a day is eaten, so a slot list is always chronological.
MEAL_SLOTS = ("breakfast", "lunch", "dinner")


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
    # Whether budget_total is the whole party's budget or one person's. The
    # orchestrator only ever sees an absolute total (see to_request_dict), so
    # this stays a presentation concern and no downstream maths changes.
    budget_basis: Literal["total", "per_person"] = "total"
    party_size: int = Field(default=2, ge=1)

    # Which meals to plan. "Lunch and dinner only" was previously inexpressible,
    # so a lunch+dinner request silently got a breakfast too - three meals of
    # budget for a two-meal trip, which is most of why such plans read as
    # wildly over budget.
    meals: list[str] = Field(default_factory=lambda: list(MEAL_SLOTS))
    # Quality gate. Free to enforce: rating and userRatingCount are already in
    # the Places search field mask, and both columns exist offline. None means
    # no threshold, so every caller predating this is unaffected.
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_reviews: int | None = Field(default=None, ge=0)

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

    @field_validator("meals")
    @classmethod
    def _known_meals_in_order(cls, value: list[str]) -> list[str]:
        """Drop anything that is not a real meal slot and restore day order.

        The slot vocabulary is closed, and "dinner, breakfast" must still plan
        breakfast first - the order here decides the order of the day.
        """
        chosen = {str(meal).strip().lower() for meal in value}
        ordered = [meal for meal in MEAL_SLOTS if meal in chosen]
        if not ordered:
            raise ValueError(f"pick at least one of {list(MEAL_SLOTS)}")
        return ordered

    @computed_field
    @property
    def effective_budget_total(self) -> float:
        """The absolute budget for the whole party, whichever basis was used."""
        if self.budget_basis == "per_person":
            return round(self.budget_total * self.party_size, 2)
        return float(self.budget_total)

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
        """Return a JSON-compatible dict for the existing orchestrator API.

        mode="json" matters: the request is embedded verbatim into the Planner
        and Critic prompts with json.dumps, and a bare date object raises
        TypeError there. Converting at this boundary keeps start_date a plain
        ISO string, which _day_label already accepts.
        """
        data = self.model_dump(mode="json", exclude={"tier", "data_backend"})
        data.pop("dates", None)
        data.pop("weekdays", None)
        data.pop("day_labels", None)
        # The orchestrator's budget maths is absolute and stays that way; the
        # per-person basis is resolved here, at the boundary, and the amount the
        # user actually typed is kept alongside it for display.
        data.pop("effective_budget_total", None)
        data["budget_entered"] = float(self.budget_total)
        data["budget_total"] = self.effective_budget_total
        if data.get("max_walk_km") is None:
            data.pop("max_walk_km", None)
        return data
