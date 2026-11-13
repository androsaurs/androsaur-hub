"""Phone observations and stand charging decisions are distinct measurements."""

from pydantic import BaseModel, Field, StrictBool


class PhoneBatteryReport(BaseModel):
    percent: int = Field(strict=True, ge=0, le=100)
    charging: StrictBool
    age_ms: int = Field(alias="ageMs", strict=True, ge=0, le=60_000)


class BatteryView(BaseModel):
    percent: int
    charging: bool
    ago_s: int
    fresh: bool
