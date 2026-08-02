#!/usr/bin/env python3
"""
Phase 0 — Probe official agency TC forecast endpoints (real HTTP only).

Records reachability, status codes, content-type, and raw head samples.
Does NOT invent endpoints: only well-known public URLs are tried; failures
are recorded honestly.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

OUT = Path(__file__).resolve().parents[2] / "data" / "samples" / "agencies"
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "typhoon-impact-audit/0.1 (+internal research; contact: local-dev; "
    "not a production crawler)"
)

# Curated starting set of *public* URLs commonly used for WNP TC products.
# Each will be proven or deleted in the audit report based on real response.
PROBES = [
    # --- JMA RSMC Tokyo ---
    {
        "id": "jma_rsmc_list",
        "agency": "JMA RSMC Tokyo",
        "url": "https://www.jma.go.jp/bosai/typhoon/data/typhoon_info.json",
        "note": "JMA bosai typhoon info JSON (if still published)",
    },
    {
        "id": "jma_rsmc_target",
        "agency": "JMA RSMC Tokyo",
        "url": "https://www.jma.go.jp/bosai/typhoon/data/targettelemeter.json",
        "note": "JMA target telemeter",
    },
    {
        "id": "jma_typhoon_page",
        "agency": "JMA RSMC Tokyo",
        "url": "https://www.jma.go.jp/bosai/map.html#5/29.503/138.956/&elem=root&typhoon=all&contents=typhoon",
        "note": "JMA typhoon map page (HTML shell)",
    },
    {
        "id": "jma_rsmc_digital_typhoon_list",
        "agency": "JMA / Digital Typhoon (NII)",
        "url": "http://agora.ex.nii.ac.jp/digital-typhoon/year/wnp/2025.html.en",
        "note": "Digital Typhoon historical index",
    },
    # --- CMA / NMC ---
    {
        "id": "cma_typhoon_nmc",
        "agency": "CMA 中央气象台",
        "url": "http://typhoon.nmc.cn/web.html",
        "note": "NMC typhoon web portal",
    },
    {
        "id": "cma_typhoon_index_api_guess",
        "agency": "CMA 中央气象台",
        "url": "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default",
        "note": "Common community-documented list endpoint (must verify)",
    },
    {
        "id": "cma_typhoon_wzt",
        "agency": "CMA 中央气象台",
        "url": "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default?t="
        + str(int(time.time() * 1000)),
        "note": "list_default with cache-buster",
    },
    # --- JTWC / ATCF ---
    {
        "id": "jtwc_rss",
        "agency": "JTWC",
        "url": "https://www.metoc.navy.mil/jtwc/jtwc.html",
        "note": "JTWC main page",
    },
    {
        "id": "jtwc_atcf_adeck_mirror",
        "agency": "JTWC ATCF",
        "url": "https://ftp.nhc.noaa.gov/atcf/aid_public/",
        "note": "NHC ATCF a-deck public (includes WP basins when present)",
    },
    {
        "id": "jtwc_nrl_atcf",
        "agency": "JTWC ATCF NRL",
        "url": "https://www.nrlmry.navy.mil/atcf_web/docs/current_storms/",
        "note": "NRL ATCF current storms listing",
    },
    {
        "id": "noaa_nhc_atcf_techlist",
        "agency": "NOAA ATCF tech",
        "url": "https://ftp.nhc.noaa.gov/atcf/index/techlist.dat",
        "note": "ATCF techlist",
    },
    # --- HKO ---
    {
        "id": "hko_tc_page",
        "agency": "HKO 香港天文台",
        "url": "https://www.hko.gov.hk/en/informtc/tcMain.htm",
        "note": "HKO TC main page",
    },
    {
        "id": "hko_tc_track",
        "agency": "HKO",
        "url": "https://www.hko.gov.hk/wxinfo/currwx/tc_gis_info.xml",
        "note": "HKO TC GIS XML (historical public product)",
    },
    {
        "id": "hko_warning",
        "agency": "HKO",
        "url": "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=en",
        "note": "HKO open data warning summary JSON",
    },
    {
        "id": "hko_opendata_fnd",
        "agency": "HKO",
        "url": "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=en",
        "note": "HKO 9-day forecast open data",
    },
    # --- CWA Taiwan ---
    {
        "id": "cwa_typhoon",
        "agency": "CWA 台湾中央气象署",
        "url": "https://www.cwa.gov.tw/V8/C/P/Typhoon/TY_NEWS.html",
        "note": "CWA typhoon news page",
    },
    {
        "id": "cwa_opendata_catalog",
        "agency": "CWA",
        "url": "https://opendata.cwa.gov.tw/dist/opendata-swagger.html",
        "note": "CWA open data swagger UI",
    },
    # --- KMA ---
    {
        "id": "kma_typhoon",
        "agency": "KMA 韩国气象厅",
        "url": "https://www.weather.go.kr/w/typhoon/report.do",
        "note": "KMA typhoon report page",
    },
    # --- IBTrACS / best track (historical) ---
    {
        "id": "ibtracs_index",
        "agency": "IBTrACS",
        "url": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/",
        "note": "IBTrACS CSV access directory",
    },
    {
        "id": "cma_sti_besttrack_page",
        "agency": "CMA-STI best track",
        "url": "https://tcdata.typhoon.org.cn/zjljsj.html",
        "note": "Shanghai Typhoon Institute best-track dataset page",
    },
]


def probe_one(item: dict, session: requests.Session) -> dict:
    url = item["url"]
    result = {
        "id": item["id"],
        "agency": item["agency"],
        "url": url,
        "note": item.get("note"),
        "ok": False,
        "status": None,
        "content_type": None,
        "final_url": None,
        "bytes": 0,
        "elapsed_ms": None,
        "head_text": None,
        "head_hex": None,
        "error": None,
        "sample_path": None,
    }
    t0 = time.time()
    try:
        resp = session.get(
            url,
            timeout=25,
            allow_redirects=True,
            headers={"User-Agent": UA, "Accept": "*/*"},
        )
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["status"] = resp.status_code
        result["content_type"] = resp.headers.get("content-type")
        result["final_url"] = str(resp.url)
        body = resp.content[:800]
        result["bytes"] = len(resp.content)
        result["head_hex"] = body[:48].hex(" ")
        result["head_text"] = body[:500].decode("utf-8", errors="replace")
        result["ok"] = 200 <= resp.status_code < 400 and len(resp.content) > 0
        # save sample
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", item["id"])
        sample = OUT / f"{safe}.sample"
        sample.write_bytes(resp.content[:50000])  # cap 50KB sample
        result["sample_path"] = str(sample)
    except Exception as e:
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def main() -> int:
    env = os.environ.get("AUDIT_NETWORK_ENV", "local-macos-dev")
    session = requests.Session()
    results = []
    for item in PROBES:
        print(f"=== {item['id']} ===", flush=True)
        r = probe_one(item, session)
        print(
            json.dumps(
                {
                    "ok": r["ok"],
                    "status": r["status"],
                    "ct": r["content_type"],
                    "err": r["error"],
                    "head": (r["head_text"] or "")[:160],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        results.append(r)

    report = {
        "probe_time_utc": datetime.now(timezone.utc).isoformat(),
        "network_env": env,
        "results": results,
    }
    out = OUT / "agency_probe_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print("OK:", sum(1 for r in results if r["ok"]), "/", len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
