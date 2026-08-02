"""Raw forecast archival — Layer B prerequisite. Never skip on successful fetch."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT = ROOT / "archive"


def archive_dir(source: str, when: Optional[datetime] = None) -> Path:
    """Return archive/YYYYMMDD/HHz/{source}/ for the given UTC time."""
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    day = when.strftime("%Y%m%d")
    # Floor to forecast-cycle style hour bucket used for runs
    hour = f"{when.hour:02d}z"
    path = ARCHIVE_ROOT / day / hour / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_bytes(
    source: str,
    filename: str,
    data: bytes,
    meta: Optional[dict[str, Any]] = None,
    when: Optional[datetime] = None,
) -> Path:
    """Write raw payload + sidecar metadata JSON."""
    d = archive_dir(source, when=when)
    target = d / filename
    target.write_bytes(data)
    sidecar = {
        "source": source,
        "filename": filename,
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "size_bytes": len(data),
        "meta": meta or {},
    }
    (d / f"{filename}.meta.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def save_text(
    source: str,
    filename: str,
    text: str,
    meta: Optional[dict[str, Any]] = None,
    when: Optional[datetime] = None,
    encoding: str = "utf-8",
) -> Path:
    return save_bytes(source, filename, text.encode(encoding), meta=meta, when=when)
