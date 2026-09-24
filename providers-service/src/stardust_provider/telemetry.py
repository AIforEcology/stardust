"""Carbon capture provider telemetry (spec §20), mirroring schema/provider-telemetry.schema.json.

Field names are the spec's 3-letter codes. Only raw fields are accepted here; derived
``D-`` fields are computed by Stardust's analytics layer and stored separately (§20.7).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class TechType(str, Enum):
    DAC = "DAC"  # Direct Air Capture
    PSC = "PSC"  # Point-Source Capture
    NBC = "NBC"  # Nature-Based Capture
    OBC = "OBC"  # Ocean-Based Capture


class DataSourceType(str, Enum):
    direct_metered = "direct_metered"
    estimated = "estimated"
    modeled = "modeled"


class StoragePathway(str, Enum):
    geologic = "geologic"
    mineralization = "mineralization"
    utilization = "utilization"
    transport = "transport"


class ReportingInterval(str, Enum):
    realtime = "realtime"
    hourly = "hourly"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class UptimeLog(BaseModel):
    uptime_hrs: Optional[float] = Field(default=None, ge=0)
    downtime_hrs: Optional[float] = Field(default=None, ge=0)
    reason_code: Optional[str] = None


class Calibration(BaseModel):
    calibrated_at: Optional[datetime] = None
    status: Optional[str] = None


class Geo(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


def _pct(description: str):
    return Field(default=None, ge=0, le=100, description=description)


class TelemetryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    FPI: str = Field(description="Facility/Project ID")
    tech_type: TechType
    timestamp: datetime
    DST: DataSourceType = Field(description="Data Source Type")

    # Capture performance (§20.2)
    CRI: Optional[float] = Field(default=None, ge=0, description="Capture Rate Interval, kg/hr")
    CEP: Optional[float] = _pct("Capture Efficiency Percentage")
    CUR: Optional[float] = _pct("Capacity Utilization Rate, %")
    UDL: Optional[UptimeLog] = None
    # Material accounting (§20.3)
    CCM: Optional[float] = Field(default=None, ge=0, description="Cumulative Captured Mass, kg")
    SUP: Optional[StoragePathway] = None
    CSP: Optional[float] = _pct("CO2 Stream Purity, %")
    PLR: Optional[float] = Field(default=None, ge=0, description="Post-Capture Leakage Rate, %")
    # Energy & resource inputs (§20.4)
    ECT: Optional[float] = Field(default=None, ge=0, description="kWh per tonne captured")
    ESM: Optional[Dict[str, float]] = Field(default=None, description="% split: grid | renewable | onsite")
    WCT: Optional[float] = Field(default=None, ge=0, description="L per tonne captured")
    SCR: Optional[float] = Field(default=None, ge=0, description="kg sorbent/solvent per tonne")
    RCC: Optional[int] = Field(default=None, ge=0, description="Regeneration Cycle Count")
    # Verification & provenance (§20.5)
    SCT: Optional[Calibration] = None
    MSR: Optional[str] = Field(default=None, description="MRV Standard Reference")
    GLC: Optional[Geo] = None
    RFI: Optional[ReportingInterval] = None
