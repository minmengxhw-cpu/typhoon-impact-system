"""
ATCF a-deck / b-deck parser + UCAR fetchers.

ATCF line (comma-separated), common fields:
  0 basin, 1 CY, 2 YYYYMMDDHH, 3 technum, 4 tech, 5 tau,
  6 lat (tenths deg + N/S), 7 lon (tenths deg + E/W),
  8 vmax (kt), 9 mslp (mb), ...

Proven endpoints (audit 2026-08-04):
  - https://hurricanes.ral.ucar.edu/repository/data/adecks_open/{year}/
  - https://hurricanes.ral.ucar.edu/repository/data/chips_realtime_atcf/
  - https://hurricanes.ral.ucar.edu/repository/data/tcvitals_open/combined_tcvitals.{year}.dat
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from .archive import save_bytes, save_text

LOG = logging.getLogger(__name__)
UA = {"User-Agent": "typhoon-impact-system/0.2 (internal research)"}

ADECK_YEAR = "https://hurricanes.ral.ucar.edu/repository/data/adecks_open/{year}/"
ADECK_FILE = "https://hurricanes.ral.ucar.edu/repository/data/adecks_open/{year}/{filename}"
CHIPS_YEAR = "https://hurricanes.ral.ucar.edu/repository/data/chips_realtime_atcf/{year}/"
CHIPS_FILE = "https://hurricanes.ral.ucar.edu/repository/data/chips_realtime_atcf/{year}/{filename}"
CHIPS_ALL = "https://hurricanes.ral.ucar.edu/repository/data/chips_realtime_atcf/all_chips_adecks.{year}"
# Per-storm a/b decks also mirrored under realtime plots (proven 2026-08-04)
NWP_STORM = "https://hurricanes.ral.ucar.edu/realtime/plots/northwestpacific/{year}/{storm}/"
TCVITALS = "https://hurricanes.ral.ucar.edu/repository/data/tcvitals_open/combined_tcvitals.{year}.dat"

# Tech codes we care about for multi-agency view (when present in a-deck)
TECH_LABELS = {
    "OFCL": "JTWC_official",
    "JTWC": "JTWC",
    "JTWI": "JTWC_interim",
    "CONW": "JTWC_consensus",
    "EGRR": "UKMO",
    "UKM": "UKMO",
    "UKX": "UKMO",
    "CMME": "CMC",
    "CMC": "CMC",
    "AVNO": "GFS",
    "GFSO": "GFS",
    "EMX": "ECMWF",
    "ECMF": "ECMWF",
    "JGSM": "JMA",
    "JMAT": "JMA",
    "HWRF": "HWRF",
    "HMON": "HMON",
    "CTCX": "COAMPS",
    "CARQ": "CARQ_analysis",
    "BEST": "BEST",
}


@dataclass
class AtcfPoint:
    basetime: datetime
    lead_hours: int
    lat: float
    lon: float
    vmax_kt: Optional[float]
    mslp_mb: Optional[float]
    tech: str
    storm_id: str  # e.g. WP152026
    name: str = ""


@dataclass
class AtcfFetchResult:
    ok: bool
    paths: List[str] = field(default_factory=list)
    points: List[AtcfPoint] = field(default_factory=list)
    techs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    source: str = ""


def _parse_lat(token: str) -> Optional[float]:
    token = token.strip()
    if not token or token[-1] not in "NSns":
        return None
    hemi = token[-1].upper()
    try:
        v = float(token[:-1]) / 10.0
    except ValueError:
        return None
    return v if hemi == "N" else -v


def _parse_lon(token: str) -> Optional[float]:
    token = token.strip()
    if not token or token[-1] not in "EWew":
        return None
    hemi = token[-1].upper()
    try:
        v = float(token[:-1]) / 10.0
    except ValueError:
        return None
    return v if hemi == "E" else -v


def parse_atcf_text(text: str, default_basin: str = "WP") -> List[AtcfPoint]:
    out: List[AtcfPoint] = []
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 9:
            continue
        basin = parts[0] or default_basin
        cy = parts[1].zfill(2)
        ymdh = parts[2]
        tech = parts[4]
        try:
            tau = int(float(parts[5]))
        except ValueError:
            continue
        lat = _parse_lat(parts[6])
        lon = _parse_lon(parts[7])
        if lat is None or lon is None:
            continue
        try:
            basetime = datetime.strptime(ymdh[:10], "%Y%m%d%H").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        vmax = None
        mslp = None
        try:
            if parts[8] not in ("", "nan"):
                vmax = float(parts[8])
        except ValueError:
            pass
        try:
            if len(parts) > 9 and parts[9] not in ("", "nan"):
                mslp = float(parts[9])
        except ValueError:
            pass
        # name field often ~ index 27
        name = parts[27] if len(parts) > 27 else ""
        year = basetime.year
        storm_id = f"{basin}{cy}{year}"
        out.append(
            AtcfPoint(
                basetime=basetime,
                lead_hours=tau,
                lat=lat,
                lon=lon,
                vmax_kt=vmax,
                mslp_mb=mslp,
                tech=tech,
                storm_id=storm_id,
                name=name,
            )
        )
    return out


def atcf_points_to_tracks(points: List[AtcfPoint]):
    """Group into Track-like dicts per (storm_id, tech, basetime)."""
    from src.model.schema import WIND_1MIN, Track, TrackPoint

    groups: Dict[Tuple[str, str, str], List[AtcfPoint]] = {}
    for p in points:
        key = (p.storm_id, p.tech, p.basetime.isoformat())
        groups.setdefault(key, []).append(p)

    tracks: List[Track] = []
    for (sid, tech, bt), pts in groups.items():
        pts = sorted(pts, key=lambda x: x.lead_hours)
        label = TECH_LABELS.get(tech, tech)
        name = next((p.name for p in pts if p.name), sid)
        tpoints = []
        for p in pts:
            wind_ms = p.vmax_kt * 0.514444 if p.vmax_kt is not None else None
            tpoints.append(
                TrackPoint(
                    lead_hours=float(p.lead_hours),
                    lat=p.lat,
                    lon=p.lon,
                    wind_ms=wind_ms,
                    wind_averaging=WIND_1MIN if wind_ms is not None else None,
                    pressure_hpa=p.mslp_mb,
                    valid_time_utc=(p.basetime + timedelta(hours=p.lead_hours)).isoformat(),
                    extra={"vmax_kt": p.vmax_kt, "tech": tech},
                )
            )
        tracks.append(
            Track(
                source=f"atcf_{tech.lower()}",
                storm_id=sid,
                storm_name=name.strip() or sid,
                basetime_utc=bt,
                member=None,
                points=tpoints,
                basin=sid[:2],
                agency=label,
                notes=[f"atcf_tech={tech}", f"agency_label={label}"],
                meta={"tech": tech, "format": "ATCF"},
            )
        )
    return tracks


def _get(url: str, timeout: int = 45) -> requests.Response:
    r = requests.get(url, timeout=timeout, headers=UA)
    r.raise_for_status()
    return r


def list_adeck_files(year: int) -> List[str]:
    url = ADECK_YEAR.format(year=year)
    r = _get(url, timeout=30)
    return sorted(set(re.findall(r'href="(a?wp\d{2}' + str(year) + r'\.dat)"', r.text, re.I)))


def fetch_adeck_file(year: int, filename: str) -> AtcfFetchResult:
    url = ADECK_FILE.format(year=year, filename=filename)
    try:
        r = _get(url, timeout=60)
        text = r.content.decode("utf-8", errors="replace")
        path = save_text(
            source=f"ucar_adeck/{year}",
            filename=filename,
            text=text,
            meta={"url": url, "year": year},
        )
        pts = parse_atcf_text(text)
        techs = sorted({p.tech for p in pts})
        return AtcfFetchResult(
            ok=True, paths=[str(path)], points=pts, techs=techs, source="ucar_adeck"
        )
    except Exception as e:
        return AtcfFetchResult(ok=False, error=f"{type(e).__name__}: {e}", source="ucar_adeck")


def list_chips_wp_files(year: int) -> List[str]:
    url = CHIPS_YEAR.format(year=year)
    r = _get(url, timeout=30)
    return sorted(set(re.findall(r'href="(awp\d{2}' + str(year) + r'\.dat)"', r.text, re.I)))


def fetch_chips_wp_file(year: int, filename: str) -> AtcfFetchResult:
    """Fetch one WP a-deck from chips_realtime_atcf (multi-model including JTWC tech)."""
    url = CHIPS_FILE.format(year=year, filename=filename)
    try:
        r = _get(url, timeout=120)
        text = r.content.decode("utf-8", errors="replace")
        path = save_text(
            source=f"ucar_chips/{year}",
            filename=filename,
            text=text,
            meta={"url": url, "year": year},
        )
        pts = parse_atcf_text(text)
        techs = sorted({p.tech for p in pts})
        LOG.info("chips %s points=%s techs=%s", filename, len(pts), techs[:20])
        return AtcfFetchResult(
            ok=True, paths=[str(path)], points=pts, techs=techs, source="ucar_chips"
        )
    except Exception as e:
        return AtcfFetchResult(ok=False, error=f"{type(e).__name__}: {e}", source="ucar_chips")


def fetch_nwp_storm_adeck(year: int, storm: str) -> AtcfFetchResult:
    """
    storm e.g. 'wp122026' (no trailing slash).
    Downloads awpXXYYYY.dat + bwpXXYYYY.dat when present.
    """
    storm = storm.strip("/")
    base = NWP_STORM.format(year=year, storm=storm)
    paths: List[str] = []
    all_pts: List[AtcfPoint] = []
    techs: List[str] = []
    errors: List[str] = []
    for kind in ("a", "b"):
        # awp122026.dat / bwp122026.dat
        num = storm[2:4] if len(storm) >= 4 else storm
        fn = f"{kind}wp{num}{year}.dat"
        # filename often matches full storm id without basin letters properly:
        # wp122026 -> awp122026.dat
        m = re.match(r"wp(\d{2})(\d{4})", storm, re.I)
        if m:
            fn = f"{kind}wp{m.group(1)}{m.group(2)}.dat"
        url = base + fn
        try:
            r = _get(url, timeout=120)
            text = r.content.decode("utf-8", errors="replace")
            path = save_text(
                source=f"ucar_nwp/{year}/{storm}",
                filename=fn,
                text=text,
                meta={"url": url},
            )
            paths.append(str(path))
            pts = parse_atcf_text(text)
            all_pts.extend(pts)
            techs.extend(p.tech for p in pts)
            LOG.info("nwp %s %s points=%s", storm, fn, len(pts))
        except Exception as e:
            errors.append(f"{fn}: {type(e).__name__}: {e}")
    if not paths:
        return AtcfFetchResult(
            ok=False, error="; ".join(errors) or "no files", source="ucar_nwp"
        )
    return AtcfFetchResult(
        ok=True,
        paths=paths,
        points=all_pts,
        techs=sorted(set(techs)),
        source="ucar_nwp",
        error="; ".join(errors) if errors else None,
    )


def fetch_chips_for_storms(year: int, storm_ids: List[str]) -> AtcfFetchResult:
    """
    Fetch chips a-decks for selected storms only (e.g. wp122026 -> awp122026.dat).
    chips files are often intensity-model only; NWP plots a-deck is richer multi-model.
    """
    paths: List[str] = []
    merged: List[AtcfPoint] = []
    techs: List[str] = []
    errors: List[str] = []
    for storm in storm_ids:
        m = re.match(r"wp(\d{2})(\d{4})", storm.strip("/"), re.I)
        if not m:
            continue
        fn = f"awp{m.group(1)}{m.group(2)}.dat"
        r = fetch_chips_wp_file(year, fn)
        if r.ok:
            paths.extend(r.paths)
            merged.extend(r.points)
            techs.extend(r.techs)
        else:
            errors.append(f"{fn}:{r.error}")
    return AtcfFetchResult(
        ok=bool(paths),
        paths=paths,
        points=merged,
        techs=sorted(set(techs)),
        source="ucar_chips",
        error="; ".join(errors) if errors else None,
    )


def fetch_tcvitals(year: Optional[int] = None) -> AtcfFetchResult:
    year = year or datetime.now(timezone.utc).year
    url = TCVITALS.format(year=year)
    try:
        r = _get(url, timeout=45)
        text = r.content.decode("utf-8", errors="replace")
        path = save_text(
            source=f"ucar_tcvitals/{year}",
            filename=f"combined_tcvitals.{year}.dat",
            text=text,
            meta={"url": url},
        )
        # TCVitals is not ATCF; store raw, optional light parse later
        return AtcfFetchResult(ok=True, paths=[str(path)], points=[], techs=[], source="ucar_tcvitals")
    except Exception as e:
        return AtcfFetchResult(ok=False, error=f"{type(e).__name__}: {e}", source="ucar_tcvitals")


def list_nwp_storms(year: int) -> List[str]:
    url = f"https://hurricanes.ral.ucar.edu/realtime/plots/northwestpacific/{year}/"
    r = _get(url, timeout=30)
    return sorted(
        set(re.findall(r'href="(wp\d{2}' + str(year) + r'/)"', r.text, re.I))
    )


def fetch_wp_realtime_bundle(year: Optional[int] = None) -> Dict[str, AtcfFetchResult]:
    """
    US/multi-model realtime chain:
      1) NWP plots a/b-decks for recent named WP storms (rich multi-model ATCF)
      2) CHIPS a-decks for same storms (intensity models)
      3) TCVitals (JTWC analysis positions)
      4) Prior-year open adeck indices (backtest inventory)
    """
    year = year or datetime.now(timezone.utc).year
    out: Dict[str, AtcfFetchResult] = {}
    targets: List[str] = []
    try:
        storms = [s.strip("/") for s in list_nwp_storms(year)]
        # invests are wp90-99; named/numbered systems wp01-wp29
        named = [s for s in storms if re.match(r"wp[0-2]\d", s, re.I)]
        targets = named[-5:] if len(named) > 5 else named
        LOG.info("NWP targets year=%s %s", year, targets)
        for storm in targets:
            out[f"nwp_{storm}"] = fetch_nwp_storm_adeck(year, storm)
    except Exception as e:
        out["nwp_list"] = AtcfFetchResult(
            ok=False, error=f"{type(e).__name__}: {e}", source="ucar_nwp"
        )
    out["chips"] = fetch_chips_for_storms(year, targets)
    out["tcvitals"] = fetch_tcvitals(year)
    for y in (year - 1, year - 2):
        try:
            files = list_adeck_files(y)
            idx = _get(ADECK_YEAR.format(year=y), timeout=30)
            path = save_text(
                source=f"ucar_adeck/{y}",
                filename="index.html",
                text=idx.text,
                meta={"n_files": len(files), "sample_files": files[:15]},
            )
            out[f"adeck_index_{y}"] = AtcfFetchResult(
                ok=True,
                paths=[str(path)],
                techs=[],
                source=f"ucar_adeck_{y}",
                points=[],
            )
            LOG.info("adeck index %s files=%s", y, len(files))
        except Exception as e:
            out[f"adeck_index_{y}"] = AtcfFetchResult(
                ok=False, error=f"{type(e).__name__}: {e}", source=f"ucar_adeck_{y}"
            )
    return out
