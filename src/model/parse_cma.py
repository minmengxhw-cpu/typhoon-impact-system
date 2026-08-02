"""
Parse CMA / NMC typhoon JSONP into Track objects.

Proven layout (audit 2026-08-02, view_{id}):
  typhoon = [
    id, en_name, zh_name, num, ..., status,   # indices 0..7-ish
    points,                                    # list of best-track points
    links,
  ]
  point = [
    point_id,              # 0
    'YYYYMMDDHHMM',        # 1  valid time
    ts_ms,                 # 2
    grade,                 # 3  TD/TS/STS/TY/…
    lon, lat,              # 4, 5
    pressure_hpa,          # 6
    wind_ms,               # 7  CMA ~2-min mean
    move_dir, move_speed,  # 8, 9
    wind_radii,            # 10  [['30KTS', NE,SE,SW,NW, id], ...]
    forecasts,             # 11  {'BABJ': [[lead_h, base, lon, lat, p, wind, 'BABJ', grade], ...]}
    ...
  ]

We emit:
  - one analysis/best-track Track (source=cma_best) from all points
  - one forecast Track per agency key in the **latest** point that has forecasts
    (source=cma_{agency.lower()}, typically cma_babj)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .schema import WIND_2MIN, Track, TrackPoint

LOG = logging.getLogger(__name__)


def strip_jsonp(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\w+\(\((\{.*\})\)\)\s*;?\s*$", text, re.S)
    if not m:
        m = re.search(r"\w+\((\{.*\})\s*\)\s*;?\s*$", text, re.S)
    if not m:
        m = re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("not JSONP/JSON")
    return json.loads(m.group(1))


def _parse_cma_time(s: str) -> Optional[str]:
    """'202608021200' → ISO UTC (CMA times are treated as UTC for storage)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        if len(s) >= 12:
            dt = datetime.strptime(s[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        elif len(s) >= 10:
            dt = datetime.strptime(s[:10], "%Y%m%d%H").replace(tzinfo=timezone.utc)
        else:
            return None
        return dt.isoformat()
    except Exception:
        return None


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_view_dict(data: dict, raw_path: Optional[str] = None) -> List[Track]:
    ty = data.get("typhoon")
    if not isinstance(ty, list) or len(ty) < 9:
        raise ValueError("unexpected typhoon payload shape")

    storm_id = str(ty[0])
    en_name = str(ty[1] or storm_id)
    zh_name = str(ty[2] or "")
    status = ""
    for x in ty[3:8]:
        if isinstance(x, str) and x in ("start", "stop"):
            status = x
            break

    points_raw = ty[8]
    if not isinstance(points_raw, list):
        raise ValueError("typhoon[8] is not a point list")

    best_points: List[TrackPoint] = []
    latest_fc_by_agency: Dict[str, Tuple[str, List[TrackPoint]]] = {}

    for pt in points_raw:
        if not isinstance(pt, list) or len(pt) < 8:
            continue
        tstr = pt[1] if isinstance(pt[1], str) else ""
        valid = _parse_cma_time(tstr)
        lon = _f(pt[4])
        lat = _f(pt[5])
        if lat is None or lon is None:
            continue
        pressure = _f(pt[6])
        wind = _f(pt[7])
        grade = pt[3] if isinstance(pt[3], str) else None
        best_points.append(
            TrackPoint(
                lead_hours=0.0,
                lat=lat,
                lon=lon,
                wind_ms=wind,
                wind_averaging=WIND_2MIN if wind is not None else None,
                pressure_hpa=pressure,
                valid_time_utc=valid,
                grade=grade,
                extra={"move_dir": pt[8] if len(pt) > 8 else None},
            )
        )
        # Forecasts attached to this analysis point
        if len(pt) > 11 and isinstance(pt[11], dict):
            base_iso = valid
            for agency, rows in pt[11].items():
                if not isinstance(rows, list):
                    continue
                fc_pts: List[TrackPoint] = []
                # include current position as lead 0
                fc_pts.append(
                    TrackPoint(
                        lead_hours=0.0,
                        lat=lat,
                        lon=lon,
                        wind_ms=wind,
                        wind_averaging=WIND_2MIN if wind is not None else None,
                        pressure_hpa=pressure,
                        valid_time_utc=valid,
                        grade=grade,
                    )
                )
                for row in rows:
                    if not isinstance(row, list) or len(row) < 6:
                        continue
                    lead = _f(row[0])
                    flon = _f(row[2])
                    flat = _f(row[3])
                    if lead is None or flat is None or flon is None:
                        continue
                    fc_pts.append(
                        TrackPoint(
                            lead_hours=float(lead),
                            lat=flat,
                            lon=flon,
                            wind_ms=_f(row[5]) if len(row) > 5 else None,
                            wind_averaging=WIND_2MIN,
                            pressure_hpa=_f(row[4]) if len(row) > 4 else None,
                            grade=row[7] if len(row) > 7 and isinstance(row[7], str) else None,
                            valid_time_utc=None,
                        )
                    )
                if len(fc_pts) > 1:
                    latest_fc_by_agency[str(agency)] = (base_iso or "", fc_pts)

    tracks: List[Track] = []
    if best_points:
        tracks.append(
            Track(
                source="cma_best",
                storm_id=storm_id,
                storm_name=en_name,
                basetime_utc=best_points[-1].valid_time_utc,
                member=None,
                points=best_points,
                basin="WP",
                agency="CMA",
                raw_path=raw_path,
                notes=["best_track_or_analysis_series", f"status={status}", f"zh={zh_name}"],
                meta={"zh_name": zh_name, "status": status},
            )
        )
    for agency, (base_iso, fc_pts) in latest_fc_by_agency.items():
        tracks.append(
            Track(
                source=f"cma_{agency.lower()}",
                storm_id=storm_id,
                storm_name=en_name,
                basetime_utc=base_iso or None,
                member=None,
                points=fc_pts,
                basin="WP",
                agency=f"CMA-{agency}",
                raw_path=raw_path,
                notes=["forecast_from_latest_point_with_agency_key"],
                meta={"zh_name": zh_name, "forecast_agency": agency},
            )
        )
    return tracks


def parse_view_file(path: Path) -> List[Track]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    data = strip_jsonp(raw)
    return parse_view_dict(data, raw_path=str(path))
