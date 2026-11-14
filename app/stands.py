"""Stand identities and delegated hub access. A stand is nested under one phone."""

import ipaddress
import math
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

PhoneStandLinkState = Literal["online", "offline", "checking", "unpaired", "unknown", "stale"]

class PhoneStandReport(BaseModel):
    """Phone's own authenticated link check, separate from the hub's direct probe."""

    id: str | None = Field(default=None, pattern=r"^stand-[0-9a-f]{12}$")
    state: Literal["online", "offline", "checking", "unpaired"]
    seen_ago_ms: int | None = Field(default=None, alias="seenAgoMs", ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def consistent(self) -> "PhoneStandReport":
        if self.state != "unpaired" and self.id is None:
            raise ValueError("stand identity required")
        if self.state == "online" and self.seen_ago_ms is None:
            raise ValueError("successful check age required")
        return self


class StandEnrollment(BaseModel):
    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    id: str = Field(pattern=r"^stand-[0-9a-f]{12}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    key: SecretStr
    ip: str

    @field_validator("key")
    @classmethod
    def valid_key(cls, key: SecretStr) -> SecretStr:
        raw = key.get_secret_value()
        if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
            raise ValueError("invalid credential")
        return key

    @field_validator("ip")
    @classmethod
    def local_ip(cls, value: str) -> str:
        address = ipaddress.IPv4Address(value)
        if not any(
            address in ipaddress.IPv4Network(n)
            for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ):
            raise ValueError("a private LAN IPv4 address is required")
        return str(address)


class StandBatteryStatus(BaseModel):
    output_enabled: bool = Field(alias="outputEnabled", strict=True)
    charge_allowed: bool = Field(alias="chargeAllowed", strict=True)
    start_percent: int = Field(alias="startPercent", ge=0, le=99, strict=True)
    stop_percent: int = Field(alias="stopPercent", ge=1, le=100, strict=True)
    state: Literal["waiting", "stale", "allowed", "paused"]
    percent: int | None = Field(default=None, ge=0, le=100, strict=True)
    charging: bool | None = Field(default=None, strict=True)
    age_ms: int | None = Field(default=None, alias="ageMs", ge=0, strict=True)
    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def consistent(self) -> "StandBatteryStatus":
        if self.start_percent >= self.stop_percent:
            raise ValueError("invalid charge thresholds")
        if any(v is not None for v in (self.percent, self.charging, self.age_ms)) and any(
            v is None for v in (self.percent, self.charging, self.age_ms)
        ):
            raise ValueError("incomplete battery observation")
        return self


class TrackingSettings(BaseModel):
    """Human units at the API boundary; angles use the stand's nominal servo mapping."""

    model_config = {"populate_by_name": True, "extra": "forbid", "allow_inf_nan": False}
    enabled: bool = Field(strict=True)
    pan_range_deg: float = Field(alias="panRangeDeg", ge=0.5, le=90)
    tilt_range_deg: float = Field(alias="tiltRangeDeg", ge=0.5, le=45)
    speed_deg_s: float = Field(alias="speedDegS", ge=1, le=90)
    step_deg: float = Field(alias="stepDeg", ge=0.1, le=10)
    smoothing_ms: float = Field(alias="smoothingMs", ge=0, le=1000)
    dead_zone_percent: float = Field(alias="deadZonePercent", ge=1, le=20)
    pan_reverse: bool = Field(alias="panReverse", strict=True)
    tilt_reverse: bool = Field(alias="tiltReverse", strict=True)


class TrackingControl(BaseModel):
    enabled: bool = Field(strict=True)
    model_config = {"extra": "forbid"}


class StandTrackingStatus(BaseModel):
    model_config = {"populate_by_name": True, "allow_inf_nan": False}
    output_enabled: bool = Field(alias="outputEnabled", strict=True)
    state: str
    settings: TrackingSettings | None = None
    pan_deg: float | None = Field(default=None, alias="panDeg")
    tilt_deg: float | None = Field(default=None, alias="tiltDeg")
    age_ms: int | None = Field(default=None, alias="ageMs", ge=0)
    x: float | None = None
    y: float | None = None


class StandStatus(BaseModel):
    id: str = Field(pattern=r"^stand-[0-9a-f]{12}$")
    phone_id: str = Field(alias="phoneId", pattern=r"^[A-Za-z0-9-]{8,64}$")
    model_config = {"populate_by_name": True}
    firmware: str = Field(max_length=64)
    protocol: int = Field(ge=1, le=1)
    capabilities: list[str] = Field(max_length=32)
    quiet: bool
    battery: StandBatteryStatus | None = None
    tracking: StandTrackingStatus | None = None
    sensors: dict[str, float] = Field(default_factory=dict, max_length=32)

    @field_validator("capabilities")
    @classmethod
    def bounded_capabilities(cls, values: list[str]) -> list[str]:
        if any(len(v) > 32 for v in values):
            raise ValueError("capability name too long")
        return values

    @field_validator("sensors")
    @classmethod
    def finite_sensors(cls, values: dict[str, float]) -> dict[str, float]:
        if any(len(k) > 40 or not math.isfinite(v) for k, v in values.items()):
            raise ValueError("invalid sensor value")
        return values


class StandView(BaseModel):
    id: str
    device: str
    ip: str
    online: bool
    last_seen_at: float | None
    status: StandStatus | None
    error: str | None
    phone_link: PhoneStandLinkState = "unknown"
    phone_reported_ago_s: int | None = None
    battery_ago_s: int | None = None
    battery_fresh: bool = False
