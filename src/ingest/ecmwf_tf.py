"""
ECMWF Open Data tropical cyclone tracks (type=tf).

Only uses interfaces proven in docs/data-sources-audit.md.
Step length is probed (360/240/144) — never assume docs-only 240.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .archive import save_bytes

LOG = logging.getLogger(__name__)

# Proven product matrix (audit 2026-08-02)
PRODUCTS = [
    {"label": "ifs_enfo", "model": "ifs", "stream": "enfo", "type": "tf"},
    {"label": "ifs_oper", "model": "ifs", "stream": "oper", "type": "tf"},
    {"label": "aifs_single", "model": "aifs-single", "stream": "oper", "type": "tf"},
    {"label": "aifs_ens", "model": "aifs-ens", "stream": "enfo", "type": "tf"},
]

# Prefer mirrors first to protect ECMWF portal concurrency budget
SOURCES = ["aws", "ecmwf"]
STEP_CANDIDATES = {
    "enfo": [360, 240, 144],
    "oper": [360, 240, 90],
}


@dataclass
class FetchResult:
    label: str
    ok: bool
    source: Optional[str] = None
    step: Optional[int] = None
    path: Optional[str] = None
    size: int = 0
    datetime_utc: Optional[str] = None
    error: Optional[str] = None
    quiet_or_missing: bool = False


def _retrieve_one(
    source: str,
    model: str,
    stream: str,
    step: int,
    target: Path,
    timeout_sec: int = 90,
) -> datetime:
    """Retrieve with a hard wall-clock timeout so daily jobs cannot hang forever."""
    import concurrent.futures
    from ecmwf.opendata import Client

    def _do():
        client = Client(source=source, model=model if model != "ifs" else "ifs")
        kwargs = {
            "time": 0,
            "stream": stream,
            "type": "tf",
            "step": step,
            "target": str(target),
        }
        if model != "ifs":
            kwargs["model"] = model
        return client.retrieve(**kwargs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_do)
        try:
            result = fut.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"ecmwf retrieve timeout {timeout_sec}s {source} step={step}") from e
    return getattr(result, "datetime", None) or datetime.now(timezone.utc)


def fetch_product(product: dict, work_dir: Path) -> FetchResult:
    label = product["label"]
    stream = product["stream"]
    model = product["model"]
    steps = STEP_CANDIDATES.get(stream, [360, 240])
    last_err: Optional[str] = None

    for source in SOURCES:
        for step in steps:
            target = work_dir / f"{label}_{source}_step{step}.bufr"
            try:
                dt = _retrieve_one(source, model, stream, step, target)
                if not target.exists() or target.stat().st_size == 0:
                    last_err = f"empty file source={source} step={step}"
                    continue
                data = target.read_bytes()
                if data[:4] != b"BUFR":
                    last_err = f"not BUFR magic source={source} step={step}"
                    continue
                archived = save_bytes(
                    source=f"ecmwf/{label}",
                    filename=f"{label}_step{step}.bufr",
                    data=data,
                    meta={
                        "mirror": source,
                        "model": model,
                        "stream": stream,
                        "type": "tf",
                        "step": step,
                        "forecast_ref_time": str(dt),
                        "license": "CC-BY-4.0",
                        "attribution": "ECMWF Open Data",
                    },
                )
                return FetchResult(
                    label=label,
                    ok=True,
                    source=source,
                    step=step,
                    path=str(archived),
                    size=len(data),
                    datetime_utc=str(dt),
                )
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                last_err = msg
                # 404 / cannot establish latest → try next step or source
                LOG.info("ecmwf miss %s %s step=%s: %s", label, source, step, msg)
                continue

    quiet = last_err is not None and any(
        x in (last_err or "").lower()
        for x in ("404", "not found", "cannot establish latest", "no such key")
    )
    return FetchResult(
        label=label,
        ok=False,
        error=last_err,
        quiet_or_missing=quiet,
    )


def fetch_all(work_dir: Optional[Path] = None) -> List[FetchResult]:
    work_dir = work_dir or Path("/tmp/typhoon_ecmwf_tf")
    work_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for product in PRODUCTS:
        r = fetch_product(product, work_dir)
        results.append(r)
        status = "OK" if r.ok else ("QUIET/MISS" if r.quiet_or_missing else "FAIL")
        LOG.warning(
            "ECMWF %s %s size=%s step=%s err=%s",
            r.label,
            status,
            r.size,
            r.step,
            r.error,
        )
    return results
