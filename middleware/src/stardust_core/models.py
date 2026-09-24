"""Event models mirroring schema/usage-event.schema.json and enriched-event.schema.json (spec §7)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SourceLayer(str, Enum):
    chat_plugin = "chat_plugin"
    browser_ext = "browser_ext"
    infra_agent = "infra_agent"


class ConfidenceTier(str, Enum):
    measured = "measured"
    modeled = "modeled"
    estimated = "estimated"


class GridStyle(str, Enum):
    dedicated = "dedicated"
    grid_blend = "grid-blend"


class UsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    event_id: UUID = Field(default_factory=uuid4)
    source_layer: SourceLayer
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    region: Optional[str] = None
    timestamp: datetime
    tokens_in: Optional[int] = Field(default=None, ge=0)
    tokens_out: Optional[int] = Field(default=None, ge=0)
    tokens_estimated: bool = False
    compute_seconds: Optional[float] = Field(default=None, ge=0)
    bytes_transferred: Optional[int] = Field(default=None, ge=0)
    storage_delta_bytes: Optional[int] = None
    user_id: Optional[UUID] = None
    org_id: Optional[UUID] = None

    @property
    def total_tokens(self) -> Optional[int]:
        if self.tokens_in is None and self.tokens_out is None:
            return None
        return (self.tokens_in or 0) + (self.tokens_out or 0)


class EnrichedEvent(UsageEvent):
    job_sji: str
    cost_usd: Optional[float]
    energy_wh: float
    grid_intensity_g_per_kwh: float
    co2e_g: float
    water_ml: float
    particulate_mg: Optional[float] = None
    ewaste_mg: Optional[float] = None
    confidence_tier: ConfidenceTier
    indicator_code: str
    energy_source_code: str
    energy_mix_breakdown: Optional[Dict[str, float]] = None
    grid_style: GridStyle
    grid_majority_share_pct: Optional[float] = None
    grid_diversified: bool = False
    model_tier: str
    methodology_version: str
