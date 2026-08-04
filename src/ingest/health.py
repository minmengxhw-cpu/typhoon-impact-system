"""
Source health matrix — run anytime to see which chains are live.

  python3 -m src.ingest.health
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "sources.yaml"
OUT = ROOT / "reports" / "source_health.json"
UA = {"User-Agent": "typhoon-impact-system/0.2 health-check"}


def _probe_url(url: str, timeout: int = 12) -> Dict[str, Any]:
    t0 = datetime.now(timezone.utc)
    try:
        r = requests.get(url, timeout=timeout, headers=UA, allow_redirects=True)
        ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        body = r.content[:200]
        return {
            "url": url,
            "status": r.status_code,
            "ms": ms,
            "size": len(r.content),
            "ok": 200 <= r.status_code < 400 and len(r.content) > 0,
            "head": body.decode("utf-8", "replace")[:120],
        }
    except Exception as e:
        ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        return {
            "url": url,
            "status": None,
            "ms": ms,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }


def main() -> int:
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    sources = cfg.get("sources") or {}
    rows: List[Dict[str, Any]] = []

    jobs = []
    for sid, meta in sources.items():
        for ep in meta.get("endpoints") or []:
            if "{" in ep or ep.startswith("ecmwf"):
                continue  # template / SDK
            jobs.append((sid, meta, ep))

    print("Probing", len(jobs), "static endpoints…")
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_probe_url, ep): (sid, meta, ep) for sid, meta, ep in jobs}
        for fut in as_completed(futs):
            sid, meta, ep = futs[fut]
            res = fut.result()
            row = {
                "id": sid,
                "name_zh": meta.get("name_zh"),
                "agency": meta.get("agency"),
                "country": meta.get("country"),
                "role": meta.get("role"),
                "registry_status": meta.get("status"),
                "probe": res,
            }
            rows.append(row)
            flag = "OK" if res.get("ok") else "NO"
            print(f"{flag:2} {sid:28} {res.get('status')} {res.get('ms')}ms {res.get('error') or res.get('size')}")

    # SDK sources noted without HTTP
    for sid, meta in sources.items():
        if sid.startswith("ecmwf"):
            rows.append(
                {
                    "id": sid,
                    "name_zh": meta.get("name_zh"),
                    "registry_status": meta.get("status"),
                    "probe": {"ok": None, "note": "use ecmwf-opendata client"},
                }
            )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "n": len(rows),
        "ok": sum(1 for r in rows if (r.get("probe") or {}).get("ok")),
        "rows": sorted(rows, key=lambda r: r.get("id") or ""),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary ok={report['ok']}/{report['n']} -> {OUT}")
    return 0 if report["ok"] > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
