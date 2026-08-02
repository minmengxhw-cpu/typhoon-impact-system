"""
Equal-weight multi-source track consensus + ensemble spread.

Layer A baseline only:
- Consensus position at each lead = mean lat/lon of available members/sources
- Spread = mean great-circle distance of members from consensus (km)
- Historical error cone is NOT applied here (uncalibrated); callers must
  display spread with the mandatory caveat from the task book.

Does NOT produce a "certainty score".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .schema import Track, TrackPoint, haversine_km


@dataclass
class ConsensusPoint:
    lead_hours: float
    lat: float
    lon: float
    n_members: int
    spread_km: float  # mean distance from consensus
    spread_p90_km: Optional[float] = None
    mean_wind_ms: Optional[float] = None
    wind_note: str = "mixed_or_unknown_averaging"

    def to_dict(self) -> dict:
        return {
            "lead_hours": self.lead_hours,
            "lat": round(self.lat, 3),
            "lon": round(self.lon, 3),
            "n_members": self.n_members,
            "spread_km": round(self.spread_km, 1),
            "spread_p90_km": round(self.spread_p90_km, 1) if self.spread_p90_km is not None else None,
            "mean_wind_ms": round(self.mean_wind_ms, 1) if self.mean_wind_ms is not None else None,
            "wind_note": self.wind_note,
            "caveat": "低离散度不等于高确定性；各模式误差相关，未叠加历史误差圈前仅供参考",
        }


@dataclass
class ConsensusTrack:
    storm_key: str
    storm_name: str
    method: str
    points: List[ConsensusPoint] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    n_tracks: int = 0
    calibration_status: str = "uncalibrated"

    def to_dict(self) -> dict:
        return {
            "storm_key": self.storm_key,
            "storm_name": self.storm_name,
            "method": self.method,
            "calibration_status": self.calibration_status,
            "n_tracks": self.n_tracks,
            "sources_used": self.sources_used,
            "points": [p.to_dict() for p in self.points],
            "disclaimer": (
                "内部研判参考，不构成气象预报。"
                "权威信息以上海市气象局、上海市防汛指挥部发布为准。"
            ),
        }


def _interp_point(track: Track, lead: float) -> Optional[Tuple[float, float, Optional[float]]]:
    """Linear interpolate lat/lon (and wind) at a given lead hour."""
    pts = sorted(track.points, key=lambda p: p.lead_hours)
    if not pts:
        return None
    if lead < pts[0].lead_hours - 0.01 or lead > pts[-1].lead_hours + 0.01:
        # allow exact ends only outside range
        if abs(lead - pts[0].lead_hours) < 0.01:
            p = pts[0]
            return p.lat, p.lon, p.wind_ms
        if abs(lead - pts[-1].lead_hours) < 0.01:
            p = pts[-1]
            return p.lat, p.lon, p.wind_ms
        return None
    # exact / nearest segment
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if a.lead_hours <= lead <= b.lead_hours:
            if b.lead_hours == a.lead_hours:
                return a.lat, a.lon, a.wind_ms
            t = (lead - a.lead_hours) / (b.lead_hours - a.lead_hours)
            lat = a.lat + t * (b.lat - a.lat)
            lon = a.lon + t * (b.lon - a.lon)
            wind = None
            if a.wind_ms is not None and b.wind_ms is not None:
                wind = a.wind_ms + t * (b.wind_ms - a.wind_ms)
            elif a.wind_ms is not None and t < 0.5:
                wind = a.wind_ms
            elif b.wind_ms is not None:
                wind = b.wind_ms
            return lat, lon, wind
    # match last
    p = pts[-1]
    if abs(p.lead_hours - lead) < 0.01:
        return p.lat, p.lon, p.wind_ms
    return None


def equal_weight_consensus(
    tracks: Sequence[Track],
    storm_name: str = "",
    storm_key: str = "",
    leads: Optional[Sequence[float]] = None,
    min_members: int = 3,
) -> ConsensusTrack:
    """
    Equal-weight mean track across all provided tracks (ensemble members
    and/or deterministic agency tracks treated as one member each).
    """
    tracks = [t for t in tracks if t.points]
    sources = sorted({t.source for t in tracks})
    if leads is None:
        # union of common leads that appear often: 0,6,...,120, then 24h
        lead_set = set()
        for t in tracks:
            for p in t.points:
                lead_set.add(float(p.lead_hours))
        leads = sorted(lead_set)
        # prefer regular grid if dense
        if len(leads) > 80:
            leads = [h for h in leads if h <= 168 and (h % 6 == 0 or h == 0)]

    cpts: List[ConsensusPoint] = []
    for lead in leads:
        samples: List[Tuple[float, float, Optional[float]]] = []
        for t in tracks:
            ip = _interp_point(t, float(lead))
            if ip is not None:
                samples.append(ip)
        if len(samples) < min_members:
            continue
        mlat = sum(s[0] for s in samples) / len(samples)
        mlon = sum(s[1] for s in samples) / len(samples)
        dists = [haversine_km(mlat, mlon, s[0], s[1]) for s in samples]
        dists_sorted = sorted(dists)
        p90 = dists_sorted[int(0.9 * (len(dists_sorted) - 1))] if dists_sorted else None
        winds = [s[2] for s in samples if s[2] is not None]
        cpts.append(
            ConsensusPoint(
                lead_hours=float(lead),
                lat=mlat,
                lon=mlon,
                n_members=len(samples),
                spread_km=sum(dists) / len(dists) if dists else 0.0,
                spread_p90_km=p90,
                mean_wind_ms=(sum(winds) / len(winds)) if winds else None,
                wind_note="ensemble_mean_mixed_averaging_do_not_compare_agencies_raw",
            )
        )

    return ConsensusTrack(
        storm_key=storm_key or (tracks[0].storm_id if tracks else "unknown"),
        storm_name=storm_name or (tracks[0].storm_name if tracks else ""),
        method="equal_weight_mean",
        points=cpts,
        sources_used=sources,
        n_tracks=len(tracks),
        calibration_status="uncalibrated",
    )


def group_tracks_by_storm(tracks: Sequence[Track]) -> Dict[str, List[Track]]:
    """
    Group tracks for consensus. Prefer storm_name (upper) when not nameless;
    else storm_id.
    """
    groups: Dict[str, List[Track]] = defaultdict(list)
    for t in tracks:
        name = (t.storm_name or "").strip().upper()
        if name and name not in ("NAMELESS", "UNKNOWN", ""):
            key = name
        else:
            key = (t.storm_id or "unknown").strip().upper()
        groups[key].append(t)
    return dict(groups)
