#!/usr/bin/env python3
"""
One-shot multi-source ingest + Layer A.

Chains (order in config/sources.yaml):
  ECMWF tf · CMA · JMA targetTc · UCAR chips/tcvitals/adeck index

Usage:
  python3 -m src.ingest.run_once
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest.atcf import fetch_wp_realtime_bundle
from src.ingest.cma_nmc import fetch_active
from src.ingest.ecmwf_tf import fetch_all
from src.ingest.jma import fetch_target_tc
from src.model.pipeline import latest_archive_run, run_from_latest_archive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOG = logging.getLogger("run_once")

DISCLAIMER = (
    "内部研判参考，不构成气象预报。"
    "权威信息以上海市气象局、上海市防汛指挥部发布为准。"
)


def main() -> int:
    print(DISCLAIMER)
    print("calibration_status: UNCALIBRATED (initial_guess thresholds)")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary: dict = {
        "run_utc": stamp,
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "sources": {},
        "layer_a": None,
    }

    # --- backbone ---
    LOG.info("=== ECMWF type=tf ===")
    ecmwf_results = fetch_all()
    summary["sources"]["ecmwf"] = [
        {
            "label": r.label,
            "ok": r.ok,
            "source": r.source,
            "step": r.step,
            "size": r.size,
            "path": r.path,
            "error": r.error,
            "quiet_or_missing": r.quiet_or_missing,
        }
        for r in ecmwf_results
    ]

    LOG.info("=== CMA NMC ===")
    cma = fetch_active(include_stopped=False)
    summary["sources"]["cma"] = {
        "ok": cma.ok,
        "list_path": cma.list_path,
        "error": cma.error,
        "storms": [
            {k: s.get(k) for k in ("id", "name", "status", "ok", "view_path", "error")}
            for s in cma.storms
        ],
    }

    # --- Japan ---
    LOG.info("=== JMA bosai targetTc ===")
    jma = fetch_target_tc()
    summary["sources"]["jma"] = {
        "ok": jma.ok,
        "list_path": jma.list_path,
        "error": jma.error,
        "storms": [
            {
                "tropicalCyclone": s.get("tropicalCyclone"),
                "typhoonNumber": s.get("typhoonNumber"),
                "category": s.get("category"),
                "issue": s.get("issue"),
                "forecast_ok": s.get("forecast_ok"),
                "forecast_error": s.get("forecast_error"),
            }
            for s in jma.storms
        ],
    }

    # --- US / multi-model via UCAR ---
    LOG.info("=== UCAR ATCF chips + tcvitals + adeck index ===")
    us = fetch_wp_realtime_bundle()
    summary["sources"]["ucar"] = {
        k: {
            "ok": v.ok,
            "source": v.source,
            "error": v.error,
            "paths": v.paths,
            "n_points": len(v.points),
            "techs": v.techs[:40],
            "n_techs": len(v.techs),
        }
        for k, v in us.items()
    }

    # --- Layer A ---
    LOG.info("=== Layer A products ===")
    try:
        layer_a = run_from_latest_archive()
        summary["layer_a"] = {
            "ok": True,
            "headline": layer_a.get("headline"),
            "n_tracks": layer_a.get("n_tracks"),
            "n_storms_assessed": layer_a.get("n_storms_assessed"),
            "products_dir": layer_a.get("products_dir"),
            "archive_run": str(latest_archive_run()),
        }
    except Exception as e:
        LOG.warning("Layer A failed: %s", e)
        summary["layer_a"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    out = ROOT / "archive" / f"run_summary_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("summary -> %s", out)

    # Human chain status
    ecmwf_ok = sum(1 for r in ecmwf_results if r.ok)
    print(f"\n=== SOURCE CHAIN ===")
    print(f"ECMWF     {ecmwf_ok}/{len(ecmwf_results)} OK")
    print(f"CMA       ok={cma.ok} active={sum(1 for s in cma.storms if s.get('ok'))}")
    print(f"JMA       ok={jma.ok} storms={len(jma.storms)} (forecast bodies may still be empty)")
    for k, v in us.items():
        print(f"UCAR/{k:16} ok={v.ok} points={len(v.points)} techs={len(v.techs)} err={v.error}")
    if summary["layer_a"] and summary["layer_a"].get("ok"):
        h = summary["layer_a"]["headline"] or {}
        print(f"Layer A: {h.get('level_zh')} p≈{h.get('p_main')}")
    else:
        print(f"Layer A: FAIL {summary.get('layer_a')}")
    print(DISCLAIMER)

    if ecmwf_ok == 0 and not cma.ok:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
