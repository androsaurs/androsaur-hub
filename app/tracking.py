"""Short-lived diagnostic observations, never images or recognised identities."""

import math
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class TrackingSample(BaseModel):
    seq: int = Field(ge=0, le=2**53 - 1)
    age_ms: int = Field(alias="ageMs", ge=0, le=60_000)
    visible: bool
    x: float | None = Field(default=None, ge=-1, le=1)
    y: float | None = Field(default=None, ge=-1, le=1)

    @model_validator(mode="after")
    def coordinates(self) -> "TrackingSample":
        if self.visible:
            if self.x is None or self.y is None or not math.isfinite(self.x + self.y):
                raise ValueError("visible target requires finite X/Y")
        elif self.x is not None or self.y is not None:
            raise ValueError("lost target has no coordinates")
        return self


class TrackingBatch(BaseModel):
    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    session: str
    samples: list[TrackingSample] = Field(min_length=1, max_length=128)

    @field_validator("session")
    @classmethod
    def session_id(cls, value: str) -> str:
        return str(UUID(value))
