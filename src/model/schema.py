"""
Unified tropical-cyclone track schema.

All parsers (ECMWF BUFR, CMA JSONP, …) normalize into Track / TrackPoint.
Wind is always stored with explicit averaging convention; never mix raw
1-min / 2-min / 10-min numbers without conversion + labels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# Wind averaging conventions used in this codebase
WIND_10MIN = "10min"  # JMA, ECMWF 10 m wind (approx)
WIND_2MIN = "2min"  # CMA operational typical
WIND_1MIN = "1min"  # JTWC


@dataclass
class TrackPoint:
    """One position on a track (analysis or forecast)."""

    lead_hours: float  # 0 = analysis / best-track point at valid time
    lat: float
    lon: float
    # Intensity — original units preserved; see wind_ms / wind_averaging
    wind_ms: Optional[float] = None
    wind_averaging: Optional[str] = None  # WIND_* constant
    pressure_hpa: Optional[float] = None
    valid_time_utc: Optional[str] = None  # ISO-8601 if known
    grade: Optional[str] = None  # e.g. TS / TY / SuperTY (source-native)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["extra"]:
            d.pop("extra")
        return d


@dataclass
class Track:
    """A single forecast or analysis track from one source/member."""

    source: str  # e.g. ecmwf_ifs_enfo, ecmwf_ifs_oper, cma_babj
    storm_id: str  # basin id if known, else source-native
    storm_name: str
    basetime_utc: Optional[str]  # forecast init time ISO-8601
    member: Optional[int] = None  # ensemble member; None = deterministic
    points: List[TrackPoint] = field(default_factory=list)
    basin: Optional[str] = None  # WP / EP / …
    agency: Optional[str] = None  # ECMWF / CMA / …
    raw_path: Optional[str] = None  # archive path of source payload
    notes: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "storm_id": self.storm_id,
            "storm_name": self.storm_name,
            "basetime_utc": self.basetime_utc,
            "member": self.member,
            "basin": self.basin,
            "agency": self.agency,
            "raw_path": self.raw_path,
            "notes": self.notes,
            "meta": self.meta,
            "points": [p.to_dict() for p in self.points],
        }

    @property
    def is_ensemble_member(self) -> bool:
        return self.member is not None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    from math import asin, cos, radians, sin, sqrt

    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(min(1.0, a)))
