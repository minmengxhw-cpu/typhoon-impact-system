"""
Build Layer A products from archived raw fetches (or live paths).

Outputs under products/YYYYMMDD/HHz/:
  tracks.json       — all normalized tracks
  consensus.json    — per-storm equal-weight consensus
  assessment.json   — Shanghai impact assessments + decision timeline
  summary.json      — run summary for the UI
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .consensus import equal_weight_consensus, group_tracks_by_storm
from .impact import DISCLAIMER, assess_from_consensus, decision_timeline, load_thresholds
from .parse_cma import parse_view_file
from .parse_ecmwf import parse_bufr_file
from .schema import Track

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def _products_dir(when: Optional[datetime] = None) -> Path:
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    d = ROOT / "products" / when.strftime("%Y%m%d") / f"{when.hour:02d}z"
    d.mkdir(parents=True, exist_ok=True)
    return d


def collect_tracks_from_archive(archive_run: Path, only_wp: bool = True) -> List[Track]:
    """
    Parse an archive run directory:
      archive/YYYYMMDD/HHz/ecmwf/*/...bufr
      archive/YYYYMMDD/HHz/cma_nmc/view_*.jsonp
    """
    tracks: List[Track] = []
    archive_run = Path(archive_run)

    ecmwf_root = archive_run / "ecmwf"
    if ecmwf_root.is_dir():
        for bufr in sorted(ecmwf_root.rglob("*.bufr")):
            # label from parent dir name: ifs_enfo, ifs_oper, …
            label = bufr.parent.name
            source = f"ecmwf_{label}"
            try:
                ts = parse_bufr_file(
                    bufr,
                    source_label=source,
                    only_basin="WP" if only_wp else None,
                )
                LOG.info("parsed %s → %d tracks", bufr.name, len(ts))
                tracks.extend(ts)
            except Exception as e:
                LOG.warning("parse ecmwf %s failed: %s", bufr, e)

    cma_root = archive_run / "cma_nmc"
    if cma_root.is_dir():
        for view in sorted(cma_root.glob("view_*.jsonp")):
            try:
                ts = parse_view_file(view)
                LOG.info("parsed %s → %d tracks", view.name, len(ts))
                tracks.extend(ts)
            except Exception as e:
                LOG.warning("parse cma %s failed: %s", view, e)

    return tracks


def latest_archive_run() -> Optional[Path]:
    root = ROOT / "archive"
    if not root.is_dir():
        return None
    # archive/YYYYMMDD/HHz
    candidates = []
    for day in root.iterdir():
        if not day.is_dir() or not day.name.isdigit():
            continue
        for hz in day.iterdir():
            if hz.is_dir() and hz.name.endswith("z"):
                candidates.append(hz)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: str(p))[-1]


def build_products(
    tracks: Sequence[Track],
    when: Optional[datetime] = None,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    when = when or datetime.now(timezone.utc)
    out_dir = out_dir or _products_dir(when)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Drop pure best-track series from consensus members (no lead structure)
    fc_tracks = [
        t
        for t in tracks
        if t.source != "cma_best"
        and any(p.lead_hours > 0 for p in t.points)
    ]
    # Prefer WP named systems for assessment; keep invests with enough members
    groups = group_tracks_by_storm(fc_tracks)

    consensus_docs = []
    assessments = []
    thresholds = load_thresholds()

    for key, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        name = group[0].storm_name
        # Ensemble-only consensus if we have many members; else mix agencies
        cons = equal_weight_consensus(
            group,
            storm_name=name,
            storm_key=key,
            min_members=1 if len(group) < 3 else 3,
        )
        if not cons.points:
            continue
        consensus_docs.append(cons.to_dict())
        ass = assess_from_consensus(cons, thresholds=thresholds)
        ad = ass.to_dict()
        ad["decision_timeline"] = decision_timeline(ass.level, ass.dca_lead_hours)
        assessments.append(ad)

    def _is_invest_label(a: dict) -> bool:
        name = (a.get("storm_name") or "").strip().upper()
        key = (a.get("storm_key") or "").strip().upper()
        if name in ("NAMELESS", ""):
            return True
        # ATCF-style invests: 70W–99W with name==id
        if key.endswith("W") and key[:-1].isdigit() and name in (key, ""):
            return True
        if name.isdigit():
            return True
        return False

    def _headline_rank(a: dict) -> tuple:
        """Prefer named storms, then higher p, then nearer DCA."""
        invest = _is_invest_label(a)
        p = float(a.get("p_main") or 0)
        dca = float(a.get("dca_km") or 9e9)
        return (1 if invest else 0, -p, dca)

    assessments.sort(key=_headline_rank)

    # Attach source tags for UI
    name_to_sources = {
        (c.get("storm_key") or "").upper(): c.get("sources_used") or []
        for c in consensus_docs
    }
    for a in assessments:
        a["sources_used"] = name_to_sources.get((a.get("storm_key") or "").upper(), [])

    tracks_doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_tracks": len(tracks),
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "tracks": [t.to_dict() for t in tracks],
    }
    consensus_doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "storms": consensus_docs,
    }
    assessment_doc = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "ui_banner": "未校准 · 概率切点为初始猜测 · 仅供内部参考",
        "storms": assessments,
    }

    # Decision headline: prefer named system with highest urgency
    if assessments:
        # Among named, pick max p; if all invests, still use rank order
        named = [
            a
            for a in assessments
            if a.get("storm_name")
            and not (
                str(a.get("storm_key", "")).upper().endswith("W")
                and str(a.get("storm_key", ""))[:-1].isdigit()
                and str(a.get("storm_name", "")).upper()
                in (str(a.get("storm_key", "")).upper(), "NAMELESS")
            )
        ]
        top = max(named, key=lambda a: a.get("p_main") or 0) if named else assessments[0]
        lead = top.get("dca_lead_hours")
        lead_note = ""
        if lead is not None and float(lead) > 120:
            lead_note = "（最近点在 D-5 以外，仅作集合趋势）"
        headline = {
            "level": top["level"],
            "level_zh": top["level_zh"],
            "storm_name": top["storm_name"],
            "p_main": top["p_main"],
            "one_liner": (
                f"对上海：{top['level_zh']}（p≈{top['p_main']:.2f}，未校准）。"
                f"关注过程 {top['storm_name']}；"
                f"最近点约 {top.get('dca_km')} km / lead {top.get('dca_lead_hours')} h"
                f"{lead_note}。"
            ),
        }
    else:
        headline = {
            "level": "none",
            "level_zh": "无影响",
            "storm_name": None,
            "p_main": 0.0,
            "one_liner": "当前无可用西北太平洋路径成员，或全球平静。",
        }

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "ui_banner": "未校准 · 概率切点为初始猜测 · 仅供内部参考",
        "headline": headline,
        "n_tracks": len(tracks),
        "n_forecast_tracks": len(fc_tracks),
        "n_storms_assessed": len(assessments),
        "sources": sorted({t.source for t in tracks}),
        "products_dir": str(out_dir),
    }

    (out_dir / "tracks.json").write_text(
        json.dumps(tracks_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "consensus.json").write_text(
        json.dumps(consensus_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "assessment.json").write_text(
        json.dumps(assessment_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Convenience copy for UI
    latest = ROOT / "products" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for name in ("tracks.json", "consensus.json", "assessment.json", "summary.json"):
        (latest / name).write_text((out_dir / name).read_text(encoding="utf-8"), encoding="utf-8")

    LOG.info("products written → %s", out_dir)
    return summary


def run_from_latest_archive() -> Dict[str, Any]:
    run = latest_archive_run()
    if run is None:
        raise FileNotFoundError("no archive run found under archive/")
    LOG.info("using archive run %s", run)
    tracks = collect_tracks_from_archive(run)
    return build_products(tracks)
