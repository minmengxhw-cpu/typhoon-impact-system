#!/usr/bin/env python3
"""
Run Layer A product build from the latest archive (or a given run path).

Usage:
  python3 -m src.model.run_layer_a
  python3 -m src.model.run_layer_a archive/20260802/13z
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.model.pipeline import (  # noqa: E402
    build_products,
    collect_tracks_from_archive,
    latest_archive_run,
    run_from_latest_archive,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    print(
        "内部研判参考，不构成气象预报。"
        "权威信息以上海市气象局、上海市防汛指挥部发布为准。"
    )
    print("calibration_status: UNCALIBRATED")
    if argv:
        run = Path(argv[0])
        if not run.is_absolute():
            run = ROOT / run
        tracks = collect_tracks_from_archive(run)
        summary = build_products(tracks)
    else:
        summary = run_from_latest_archive()

    h = summary.get("headline") or {}
    print(f"\n等级: {h.get('level_zh')} ({h.get('level')})  p≈{h.get('p_main')}")
    print(f"结论: {h.get('one_liner')}")
    print(f"tracks={summary.get('n_tracks')} storms={summary.get('n_storms_assessed')}")
    print(f"products: {summary.get('products_dir')}")
    print(summary.get("ui_banner"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
