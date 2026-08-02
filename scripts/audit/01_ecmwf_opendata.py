#!/usr/bin/env python3
"""
Phase 0 — ECMWF Open Data tropical cyclone track (type=tf) probe.

Hard constraint: real network calls only; paste sample bytes into audit report.
Quiet periods (no TC) returning empty/404 are expected, not failures.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Ensure user site-packages
import site

site.main()

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "samples" / "ecmwf"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT = OUT_DIR / "probe_report.json"


def hex_preview(path: Path, n: int = 128) -> str:
    data = path.read_bytes()[:n]
    return data.hex(" ")


def textish_preview(path: Path, n: int = 200) -> str:
    raw = path.read_bytes()[:n]
    # BUFR starts with "BUFR"
    if raw[:4] == b"BUFR":
        return f"BUFR magic OK; total_bytes={path.stat().st_size}; head_hex={raw[:32].hex(' ')}"
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return repr(raw)


def try_retrieve(label: str, source: str, kwargs: dict) -> dict:
    from ecmwf.opendata import Client

    result = {
        "label": label,
        "source": source,
        "request": {k: v for k, v in kwargs.items() if k != "target"},
        "ok": False,
        "error": None,
        "path": None,
        "size_bytes": None,
        "preview": None,
        "hex_head": None,
    }
    target = kwargs["target"]
    try:
        client = Client(source=source)
        # latest=True by default when date not set
        client.retrieve(**kwargs)
        p = Path(target)
        if p.exists() and p.stat().st_size > 0:
            result["ok"] = True
            result["path"] = str(p)
            result["size_bytes"] = p.stat().st_size
            result["preview"] = textish_preview(p)
            result["hex_head"] = hex_preview(p)
        else:
            result["error"] = "file missing or empty after retrieve"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-1500:]
    return result


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    network_env = os.environ.get("AUDIT_NETWORK_ENV", "local-shanghai-dev")
    results = []

    probes = [
        # ENS tropical cyclone tracks (51 members)
        {
            "label": "ens_tf_enfo",
            "kwargs_base": {
                "time": 0,
                "stream": "enfo",
                "type": "tf",
                "step": 240,
            },
        },
        # HRES / oper deterministic TC tracks
        {
            "label": "oper_tf",
            "kwargs_base": {
                "time": 0,
                "stream": "oper",
                "type": "tf",
                "step": 240,
            },
        },
        # AIFS single deterministic
        {
            "label": "aifs_single_tf",
            "kwargs_base": {
                "model": "aifs-single",
                "time": 0,
                "stream": "oper",
                "type": "tf",
                "step": 240,
            },
        },
        # AIFS ensemble
        {
            "label": "aifs_ens_tf",
            "kwargs_base": {
                "model": "aifs-ens",
                "time": 0,
                "stream": "enfo",
                "type": "tf",
                "step": 240,
            },
        },
    ]

    # Prefer AWS/Azure mirrors when direct ECMWF is rate-limited; test all.
    sources = ["ecmwf", "aws", "azure"]

    for probe in probes:
        for source in sources:
            fname = f"{probe['label']}_{source}_{stamp}.bufr"
            target = str(OUT_DIR / fname)
            kwargs = {**probe["kwargs_base"], "target": target}
            print(f"\n=== {probe['label']} source={source} ===", flush=True)
            r = try_retrieve(f"{probe['label']}:{source}", source, kwargs)
            print(json.dumps({k: r[k] for k in ("ok", "error", "size_bytes", "preview")}, ensure_ascii=False, indent=2), flush=True)
            results.append(r)
            # If one mirror works for this product, still record others for audit table.
            # Continue to next source for dual-path knowledge.

    # Also probe catalog / latest index if available
    index_probe = {"label": "index_list_latest", "ok": False, "error": None, "sample": None}
    try:
        from ecmwf.opendata import Client

        client = Client(source="aws")
        # Attempt to resolve latest date for enfo/tf
        latest = client.latest(
            stream="enfo",
            type="tf",
            step=240,
            time=0,
        )
        index_probe["ok"] = True
        index_probe["sample"] = str(latest)
        print("latest enfo/tf:", latest, flush=True)
    except Exception as e:
        index_probe["error"] = f"{type(e).__name__}: {e}"
        index_probe["traceback"] = traceback.format_exc()[-800:]
        print("latest failed:", index_probe["error"], flush=True)

    report = {
        "probe_time_utc": stamp,
        "network_env": network_env,
        "python": sys.version,
        "package": "ecmwf-opendata",
        "results": results,
        "latest": index_probe,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {REPORT}", flush=True)

    any_ok = any(r.get("ok") for r in results) or index_probe.get("ok")
    # Exit 0 even if quiet period — we still want the audit artifact.
    # Exit 2 only if every call hard-failed in a way that looks like network/auth break.
    hard_fails = [r for r in results if not r.get("ok")]
    if len(hard_fails) == len(results) and not any_ok:
        # Distinguish "no TC product" vs total network death by message patterns
        msgs = " ".join(str(r.get("error") or "") for r in results).lower()
        if any(x in msgs for x in ("404", "not found", "no data", "does not exist", "empty")):
            print("All retrieves empty/404 — likely quiet TC period or product not published for latest cycle.")
            return 0
        print("All retrieves failed — check network.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
