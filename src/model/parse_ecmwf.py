"""
Parse ECMWF Open Data type=tf BUFR into Track objects.

Layout proven against archive samples (2026-08-02):
- One BUFR message per storm.
- numberOfSubsets = ensemble size (1 for oper/control-style).
- timePeriod = forecast leads in hours (6, 12, …); analysis is lead 0.
- windSpeedAt10M / pressure: one value per (time × member), time-major.
- latitude / longitude: **two** positions per (time × member), time-major,
  interleaved as [m0_primary, m0_secondary, m1_primary, …].
  We use the **primary** (first of pair) as the storm centre track.
- Missing values are eccodes ~1e100; filtered out.

Wind: ECMWF 10 m wind → averaging label WIND_10MIN (approximate).
Pressure: Pa → hPa.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .schema import WIND_10MIN, Track, TrackPoint

LOG = logging.getLogger(__name__)

# eccodes missing sentinel is approximately 1e100
_MISSING_ABS = 1.0e20


def _is_missing(v: float) -> bool:
    return v is None or abs(float(v)) >= _MISSING_ABS


def _basin_from_id(storm_id: str) -> Optional[str]:
    if not storm_id:
        return None
    # e.g. 15W, 07E, 10L
    suf = storm_id[-1].upper() if storm_id[-1].isalpha() else ""
    return {
        "W": "WP",
        "E": "EP",
        "C": "CP",
        "L": "AL",
        "A": "NI",
        "B": "NI",
        "S": "SI",
        "P": "SP",
        "U": "SL",
    }.get(suf)


def _basetime_iso(year: int, month: int, day: int, hour: int) -> str:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


def parse_bufr_file(
    path: Path,
    source_label: str,
    agency: str = "ECMWF",
    only_basin: Optional[str] = None,
) -> List[Track]:
    """Decode all storms in a tf BUFR file into Track list (one per member)."""
    try:
        import eccodes as ec
    except ImportError as e:
        raise RuntimeError("eccodes Python package required to parse BUFR") from e

    tracks: List[Track] = []
    path = Path(path)
    with open(path, "rb") as f:
        msg_i = 0
        while True:
            bid = ec.codes_bufr_new_from_file(f)
            if bid is None:
                break
            msg_i += 1
            try:
                tracks.extend(
                    _parse_message(
                        ec,
                        bid,
                        source_label=source_label,
                        agency=agency,
                        raw_path=str(path),
                        only_basin=only_basin,
                    )
                )
            except Exception as e:
                LOG.warning("BUFR msg %s in %s failed: %s", msg_i, path.name, e)
            finally:
                ec.codes_release(bid)
    return tracks


def _get_array(ec, bid, key: str) -> list:
    try:
        return list(ec.codes_get_array(bid, key))
    except Exception:
        try:
            return [ec.codes_get(bid, key)]
        except Exception:
            return []


def _parse_message(
    ec,
    bid,
    source_label: str,
    agency: str,
    raw_path: str,
    only_basin: Optional[str],
) -> List[Track]:
    ec.codes_set(bid, "unpack", 1)

    def g(key, default=None):
        try:
            return ec.codes_get(bid, key)
        except Exception:
            return default

    nsub = int(g("numberOfSubsets", 1) or 1)
    name = str(g("longStormName", "") or "").strip()
    sid = str(g("stormIdentifier", "") or "").strip() or "unknown"
    basin = _basin_from_id(sid)
    if only_basin and basin and basin != only_basin:
        return []

    year = int(g("year", 1970) or 1970)
    month = int(g("month", 1) or 1)
    day = int(g("day", 1) or 1)
    hour = int(g("hour", 0) or 0)
    basetime = _basetime_iso(year, month, day, hour)
    base_dt = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)

    lat = [float(x) for x in _get_array(ec, bid, "latitude")]
    lon = [float(x) for x in _get_array(ec, bid, "longitude")]
    wind = [float(x) for x in _get_array(ec, bid, "windSpeedAt10M")]
    pres = [float(x) for x in _get_array(ec, bid, "pressureReducedToMeanSeaLevel")]
    tp = [int(x) for x in _get_array(ec, bid, "timePeriod")]
    ens_nums = [int(x) for x in _get_array(ec, bid, "ensembleMemberNumber")]

    if not lat or not lon:
        return []

    # Leads: analysis (0) + timePeriod hours
    if tp:
        leads = [0] + tp
    else:
        leads = [0]

    # Intensity samples: time-major, one per member
    if wind and len(wind) % nsub == 0:
        n_times_w = len(wind) // nsub
    else:
        n_times_w = 0

    # Positions: 2 per (time × member), time-major
    block = 2 * nsub
    if len(lat) >= block and len(lat) // block >= 1:
        n_times_pos = len(lat) // block
        dual = True
    elif len(lat) % nsub == 0:
        n_times_pos = len(lat) // nsub
        dual = False
        block = nsub
    else:
        # Deterministic single-subset without clean division — fall back
        n_times_pos = min(len(lat), len(leads) if leads else len(lat))
        dual = len(lat) >= 2 * max(1, n_times_w)
        nsub = 1
        block = 2 if dual else 1

    n_times = min(n_times_pos, len(leads) if leads else n_times_pos)
    if n_times_w:
        n_times = min(n_times, n_times_w)

    # Ensemble member numbers (pad if missing)
    if len(ens_nums) < nsub:
        ens_nums = list(range(1, nsub + 1))

    tracks: List[Track] = []
    for m in range(nsub):
        member_id = ens_nums[m] if m < len(ens_nums) else m + 1
        # Deterministic products often use a single subset with ens 51/52
        is_det = nsub == 1
        points: List[TrackPoint] = []
        for t in range(n_times):
            lead = float(leads[t]) if t < len(leads) else float(t)
            if dual:
                idx = t * block + m * 2  # primary
            else:
                idx = t * nsub + m
            if idx >= len(lat) or idx >= len(lon):
                break
            la, lo = lat[idx], lon[idx]
            if _is_missing(la) or _is_missing(lo):
                continue
            w = None
            p_hpa = None
            if n_times_w:
                widx = t * nsub + m
                if widx < len(wind) and not _is_missing(wind[widx]):
                    w = float(wind[widx])
                if widx < len(pres) and not _is_missing(pres[widx]):
                    # Pa → hPa
                    p_hpa = float(pres[widx]) / 100.0
            valid = (base_dt + timedelta(hours=lead)).isoformat()
            points.append(
                TrackPoint(
                    lead_hours=lead,
                    lat=la,
                    lon=lo,
                    wind_ms=w,
                    wind_averaging=WIND_10MIN if w is not None else None,
                    pressure_hpa=p_hpa,
                    valid_time_utc=valid,
                )
            )
        if not points:
            continue
        tracks.append(
            Track(
                source=source_label,
                storm_id=sid,
                storm_name=name or sid,
                basetime_utc=basetime,
                member=None if is_det else int(member_id),
                points=points,
                basin=basin,
                agency=agency,
                raw_path=raw_path,
                notes=["position=primary_of_dual_pair"] if dual else [],
                meta={
                    "n_subsets": nsub,
                    "dual_position": dual,
                    "n_times": n_times,
                },
            )
        )
    return tracks


def parse_product_dir(
    bufr_path: Path,
    label: str,
    only_wp: bool = True,
) -> List[Track]:
    """Convenience: parse one archived product file."""
    return parse_bufr_file(
        bufr_path,
        source_label=f"ecmwf_{label}",
        only_basin="WP" if only_wp else None,
    )
