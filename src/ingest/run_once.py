#!/usr/bin/env python3
"""
One-shot ingest: ECMWF tf tracks + CMA views, raw-archive everything.

Usage:
  python3 -m src.ingest.run_once
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python3 -m src.ingest.run_once` from repo root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ingest.cma_nmc import fetch_active
from src.ingest.ecmwf_tf import fetch_all
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
    summary = {
        "run_utc": stamp,
        "disclaimer": DISCLAIMER,
        "calibration_status": "uncalibrated",
        "ecmwf": [],
        "cma": {},
        "layer_a": None,
    }

    LOG.info("=== ECMWF type=tf ===")
    ecmwf_results = fetch_all()
    for r in ecmwf_results:
        summary["ecmwf"].append(
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
        )

    LOG.info("=== CMA NMC ===")
    cma = fetch_active(include_stopped=False)
    summary["cma"] = {
        "ok": cma.ok,
        "list_path": cma.list_path,
        "error": cma.error,
        "storms": [
            {k: s.get(k) for k in ("id", "name", "status", "ok", "view_path", "error")}
            for s in cma.storms
        ],
    }

    # Layer A: parse latest archive → consensus + impact products
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

    ecmwf_ok = sum(1 for r in ecmwf_results if r.ok)
    print(f"\nECMWF products OK: {ecmwf_ok}/{len(ecmwf_results)}")
    for r in ecmwf_results:
        flag = "OK" if r.ok else ("MISS" if r.quiet_or_missing else "FAIL")
        print(f"  [{flag}] {r.label} step={r.step} size={r.size} {r.error or ''}")
    print(f"CMA list ok={cma.ok} active_views={sum(1 for s in cma.storms if s.get('ok'))}")
    if summary["layer_a"] and summary["layer_a"].get("ok"):
        h = summary["layer_a"]["headline"] or {}
        print(f"Layer A: {h.get('level_zh')} p≈{h.get('p_main')} — {h.get('one_liner')}")
    else:
        print(f"Layer A: FAIL {summary.get('layer_a')}")
    print(DISCLAIMER)
    # Exit 0 if at least one backbone product archived OR CMA list worked
    if ecmwf_ok == 0 and not cma.ok:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
