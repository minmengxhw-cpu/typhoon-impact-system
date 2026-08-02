"""
CMA / NMC typhoon JSONP — proven endpoints only (audit 2026-08-02).

list:  http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default
view:  http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}

Wind values are CMA operational (typically 2-min mean) — never compare
raw numbers to JMA/JTWC without conversion + labels.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

from .archive import save_text

LOG = logging.getLogger(__name__)

LIST_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
VIEW_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}"
UA = "typhoon-impact-system/0.1 (internal research; not a public crawler)"


@dataclass
class CmaFetchResult:
    ok: bool
    storms: List[Dict[str, Any]]
    list_path: Optional[str] = None
    error: Optional[str] = None


def _strip_jsonp(text: str) -> dict:
    """Handle both name(({...})) and name({...}) wrappers."""
    text = text.strip()
    m = re.search(r"\w+\(\((\{.*\})\)\)\s*;?\s*$", text, re.S)
    if not m:
        m = re.search(r"\w+\((\{.*\})\s*\)\s*;?\s*$", text, re.S)
    if not m:
        m = re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("not JSONP/JSON")
    return json.loads(m.group(1))


def fetch_list(session: Optional[requests.Session] = None) -> Tuple[str, dict]:
    sess = session or requests.Session()
    r = sess.get(LIST_URL, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    raw = r.content.decode("utf-8", errors="replace")
    data = _strip_jsonp(raw)
    path = save_text(
        source="cma_nmc",
        filename="list_default.jsonp",
        text=raw,
        meta={"url": LIST_URL, "content_type": r.headers.get("content-type")},
    )
    return str(path), data


def fetch_view(
    storm_id: int,
    session: Optional[requests.Session] = None,
    name: str = "",
) -> Tuple[str, dict]:
    sess = session or requests.Session()
    url = VIEW_URL.format(id=storm_id)
    r = sess.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    raw = r.content.decode("utf-8", errors="replace")
    data = _strip_jsonp(raw)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name or str(storm_id))[:40]
    path = save_text(
        source="cma_nmc",
        filename=f"view_{storm_id}_{safe}.jsonp",
        text=raw,
        meta={
            "url": url,
            "storm_id": storm_id,
            "name": name,
            "wind_averaging": "cma_2min_typical",
        },
    )
    return str(path), data


def fetch_active(include_stopped: bool = False) -> CmaFetchResult:
    """Fetch list + view for storms with status start (and optionally stop)."""
    sess = requests.Session()
    try:
        list_path, data = fetch_list(sess)
    except Exception as e:
        return CmaFetchResult(ok=False, storms=[], error=f"{type(e).__name__}: {e}")

    typhoon_list = data.get("typhoonList") or []
    out: List[Dict[str, Any]] = []
    for row in typhoon_list:
        # [id, en, zh, num, ..., status]
        if not row or len(row) < 2:
            continue
        sid = row[0]
        en = row[1]
        status = row[-1] if isinstance(row[-1], str) else ""
        if status != "start" and not include_stopped:
            continue
        try:
            vpath, vdata = fetch_view(int(sid), sess, name=str(en))
            out.append(
                {
                    "id": sid,
                    "name": en,
                    "status": status,
                    "list_row": row,
                    "view_path": vpath,
                    "ok": True,
                }
            )
            LOG.info("CMA view ok id=%s name=%s", sid, en)
        except Exception as e:
            out.append(
                {
                    "id": sid,
                    "name": en,
                    "status": status,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
            LOG.warning("CMA view fail id=%s: %s", sid, e)

    return CmaFetchResult(ok=True, storms=out, list_path=list_path)
