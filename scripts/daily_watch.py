#!/usr/bin/env python3
"""
Typhoon watch for Shanghai desk — fetch, assess, export, report, Feishu.

Usage:
  python3 scripts/daily_watch.py --slot morning --push
  python3 scripts/daily_watch.py --slot evening --push
  python3 scripts/daily_watch.py --no-fetch --slot evening
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
DISCLAIMER = (
    "内部研判参考，不构成气象预报。"
    "权威信息以上海市气象局、上海市防汛指挥部发布为准。"
)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(ROOT), check=check, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument(
        "--slot",
        choices=["morning", "evening", "auto"],
        default="auto",
        help="morning/evening Feishu brief; auto picks by local hour",
    )
    ap.add_argument(
        "--major-only-feishu",
        action="store_true",
        help="only Feishu on major change (default: always send for slot)",
    )
    args = ap.parse_args()

    print(DISCLAIMER)
    print("calibration_status: UNCALIBRATED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    local = datetime.now().astimezone()
    hour = local.hour
    if args.slot == "auto":
        slot = "morning" if hour < 14 else "evening"
    else:
        slot = args.slot
    slot_zh = "早报" if slot == "morning" else "晚报"
    print(f"daily_watch slot={slot} local={local.isoformat()}")

    if not args.no_fetch:
        rc = run([sys.executable, "-m", "src.ingest.run_once"], check=False).returncode
        if rc != 0:
            print(f"WARN: run_once exit {rc}", flush=True)
            run([sys.executable, "-m", "src.model.run_layer_a"], check=False)
    else:
        run([sys.executable, "-m", "src.model.run_layer_a"], check=False)

    rc = run([sys.executable, str(ROOT / "scripts" / "export_web_data.py")], check=False).returncode
    if rc != 0:
        print("export_web_data failed", file=sys.stderr)
        return rc

    story_path = ROOT / "web" / "data" / "story.json"
    if not story_path.exists():
        print("missing story.json", file=sys.stderr)
        return 2
    story = json.loads(story_path.read_text(encoding="utf-8"))
    f = story.get("focus_storm") or {}
    v = story.get("verdict") or {}
    now = f.get("status_now") or {}
    closest = v.get("closest") or {}

    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"daily_{stamp}_{slot}.md"
    body = f"""# 台风{slot_zh} · {local.strftime("%m/%d %H:%M")}

**{f.get("zh") or f.get("en") or "—"}**｜**{v.get("level_zh") or "—"}**

**走向** {now.get("location_plain") or "—"}  
现在距上海约 **{now.get("dist_shanghai_km")} km** · {now.get("grade") or now.get("grade_plain") or "—"}  
集合趋势：约 {closest.get("days")} 天后最近点约 {closest.get("dist_km")} km（分歧 {closest.get("spread_km")} km，仅趋势）

**对本市** {v.get("one_liner") or "—"}

**建议** 先观察、不轻易改行程；5–10 天后户外场次标「观察」；进 D-5 再拍板。

> {DISCLAIMER}

https://minmengxhw-cpu.github.io/typhoon-impact-system/
"""
    report.write_text(body, encoding="utf-8")
    (REPORTS / "latest.md").write_text(body, encoding="utf-8")
    print(f"report → {report}")
    print(f"LEVEL {v.get('level_zh')} storm={f.get('zh')} dist={now.get('dist_shanghai_km')}km")

    # Feishu: always for morning/evening slot; optional major-only
    notify_cmd = [sys.executable, str(ROOT / "scripts" / "notify_feishu.py")]
    if args.major_only_feishu:
        notify_cmd.append("--major")
    else:
        notify_cmd.extend(["--slot", slot])
    notify_rc = run(notify_cmd, check=False).returncode
    if notify_rc != 0:
        print(f"WARN: notify_feishu exit {notify_rc}", flush=True)

    if args.push:
        run(
            [
                "git",
                "add",
                "web/data",
                "reports/*.md",
                "config/notify.yaml",
                "scripts/",
            ],
            check=False,
        )
        st = subprocess.run(
            ["git", "status", "--porcelain", "web/data", "reports", "config", "scripts"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if st.stdout.strip():
            msg = (
                f"chore({slot}): {f.get('zh') or f.get('en')} "
                f"{v.get('level_zh')} dist={now.get('dist_shanghai_km')}km"
            )
            run(["git", "commit", "-m", msg], check=False)
            run(["git", "push", "origin", "main"], check=False)
        else:
            print("no changes to push")

    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
