#!/usr/bin/env python3
"""
Export a slim products snapshot into web/data/ for GitHub Pages.

Keeps summary + assessment + consensus fully, but trims tracks.json:
  - prefer named storms (esp. headline storm)
  - cap ensemble members per source
  - drop leads > 168 h for map payload size
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "products" / "latest"
DST = ROOT / "web" / "data"
MAX_MEMBERS_PER_SOURCE = 15
MAX_LEAD = 168.0
MAX_STORMS_FULL_TRACKS = 6


def main() -> int:
    if not SRC.is_dir():
        print("missing products/latest — run: python3 -m src.model.run_layer_a", file=sys.stderr)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    summary = json.loads((SRC / "summary.json").read_text(encoding="utf-8"))
    assessment = json.loads((SRC / "assessment.json").read_text(encoding="utf-8"))
    consensus = json.loads((SRC / "consensus.json").read_text(encoding="utf-8"))
    tracks_doc = json.loads((SRC / "tracks.json").read_text(encoding="utf-8"))

    headline = (summary.get("headline") or {}).get("storm_name") or ""
    # Storm priority: headline, then named assessments
    priority = []
    if headline:
        priority.append(headline.upper())
    for s in assessment.get("storms") or []:
        k = (s.get("storm_key") or s.get("storm_name") or "").upper()
        if k and k not in priority:
            priority.append(k)
    keep_keys = set(priority[:MAX_STORMS_FULL_TRACKS])

    # Filter consensus / assessment to keep page useful but smaller
    consensus["storms"] = [
        s
        for s in (consensus.get("storms") or [])
        if (s.get("storm_key") or "").upper() in keep_keys
        or (s.get("storm_name") or "").upper() in keep_keys
    ][:MAX_STORMS_FULL_TRACKS]
    # Still keep all assessments for decision list, but cap
    assessment["storms"] = (assessment.get("storms") or [])[:12]

    slim_tracks = []
    member_counts: dict[str, int] = {}
    for t in tracks_doc.get("tracks") or []:
        name = (t.get("storm_name") or "").upper()
        sid = (t.get("storm_id") or "").upper()
        key = name if name in keep_keys else (sid if sid in keep_keys else None)
        if key is None and name not in keep_keys and sid not in keep_keys:
            # match priority names loosely
            if not any(k in name or k in sid for k in keep_keys):
                continue
            key = name or sid

        src = t.get("source") or "unknown"
        member = t.get("member")
        if member is not None:
            ck = f"{key}:{src}"
            n = member_counts.get(ck, 0)
            if n >= MAX_MEMBERS_PER_SOURCE:
                continue
            # sample every ~member spacing by taking first N only (already sorted-ish)
            member_counts[ck] = n + 1

        pts = [
            p
            for p in (t.get("points") or [])
            if float(p.get("lead_hours") or 0) <= MAX_LEAD
        ]
        if not pts:
            continue
        t2 = dict(t)
        t2["points"] = pts
        slim_tracks.append(t2)

    tracks_out = {
        "generated_at_utc": tracks_doc.get("generated_at_utc"),
        "n_tracks": len(slim_tracks),
        "n_tracks_full": tracks_doc.get("n_tracks"),
        "slim": True,
        "max_members_per_source": MAX_MEMBERS_PER_SOURCE,
        "max_lead_hours": MAX_LEAD,
        "disclaimer": tracks_doc.get("disclaimer"),
        "calibration_status": "uncalibrated",
        "tracks": slim_tracks,
    }

    summary = dict(summary)
    summary["web_export"] = True
    summary["note"] = "Static snapshot for GitHub Pages demo; regenerate offline with run_layer_a."

    for name, obj in (
        ("summary.json", summary),
        ("assessment.json", assessment),
        ("consensus.json", consensus),
        ("tracks.json", tracks_out),
    ):
        (DST / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Also mirror under products/latest is runtime-only; document size
    sizes = {p.name: p.stat().st_size for p in DST.glob("*.json")}
    print("exported →", DST)
    for k, v in sizes.items():
        print(f"  {k}: {v/1024:.1f} KB")
    print("headline:", (summary.get("headline") or {}).get("one_liner"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
