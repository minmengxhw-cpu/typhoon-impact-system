"""
JMA / RSMC Tokyo ingest (proven endpoints only).

Proven (2026-08-04 local):
  GET https://www.jma.go.jp/bosai/typhoon/data/targetTc.json
  -> [{"tropicalCyclone":"TC2615","typhoonNumber":"2613","category":"TY","issue":"..."}]

Full track JSON under bosai/typhoon/data/forecast/ is still unstable (404/timeout).
We archive targetTc + attempt forecast URL; parser emits metadata tracks when path missing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from .archive import save_text

LOG = logging.getLogger(__name__)
UA = {"User-Agent": "typhoon-impact-system/0.2 (internal research)"}

TARGET_TC = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"
# candidate forecast templates (tried in order; may 404)
FORECAST_CANDIDATES = [
    "https://www.jma.go.jp/bosai/typhoon/data/forecast/{tc}.json",
]


@dataclass
class JmaFetchResult:
    ok: bool
    storms: List[Dict[str, Any]] = field(default_factory=list)
    list_path: Optional[str] = None
    error: Optional[str] = None


def fetch_target_tc(timeout: int = 25) -> JmaFetchResult:
    try:
        r = requests.get(TARGET_TC, timeout=timeout, headers=UA)
        r.raise_for_status()
        raw = r.content.decode("utf-8", errors="replace")
        path = save_text(
            source="jma_bosai",
            filename="targetTc.json",
            text=raw,
            meta={"url": TARGET_TC, "content_type": r.headers.get("content-type")},
        )
        data = json.loads(raw)
        if not isinstance(data, list):
            return JmaFetchResult(ok=False, error="targetTc not a list", list_path=str(path))

        storms: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            tc = item.get("tropicalCyclone") or ""
            num = item.get("typhoonNumber") or ""
            entry: Dict[str, Any] = {
                "tropicalCyclone": tc,
                "typhoonNumber": num,
                "category": item.get("category"),
                "issue": item.get("issue"),
                "raw": item,
                "forecast_ok": False,
                "forecast_path": None,
                "forecast_error": None,
            }
            # try forecast body
            for tmpl in FORECAST_CANDIDATES:
                url = tmpl.format(tc=tc)
                try:
                    fr = requests.get(url, timeout=timeout, headers=UA)
                    if fr.status_code == 200 and fr.content[:1] in (b"{", b"["):
                        fpath = save_text(
                            source="jma_bosai",
                            filename=f"forecast_{tc}.json",
                            text=fr.content.decode("utf-8", errors="replace"),
                            meta={"url": url},
                        )
                        entry["forecast_ok"] = True
                        entry["forecast_path"] = str(fpath)
                        entry["forecast"] = fr.json()
                        break
                    entry["forecast_error"] = f"HTTP {fr.status_code}"
                except Exception as e:
                    entry["forecast_error"] = f"{type(e).__name__}: {e}"
            storms.append(entry)
            LOG.info(
                "JMA %s num=%s cat=%s forecast_ok=%s",
                tc,
                num,
                item.get("category"),
                entry["forecast_ok"],
            )
        return JmaFetchResult(ok=True, storms=storms, list_path=str(path))
    except Exception as e:
        return JmaFetchResult(ok=False, error=f"{type(e).__name__}: {e}")
