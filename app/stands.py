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


class StandStatus(BaseModel):
    id: str = Field(pattern=r"^stand-[0-9a-f]{12}$")
    phone_id: str = Field(alias="phoneId", pattern=r"^[A-Za-z0-9-]{8,64}$")
    model_config = {"populate_by_name": True}
    firmware: str = Field(max_length=64)
    protocol: int = Field(ge=1, le=1)
    capabilities: list[str] = Field(max_length=32)
    quiet: bool
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
