#!/usr/bin/env python3
"""
Daily typhoon watch for Shanghai desk.

1) Ingest ECMWF + CMA (raw archive)
2) Build Layer A products
3) Export story-first web/data (focus 白海豚 when active)
4) Write reports/daily_YYYYMMDD.md
5) Optional: git commit + push so GitHub Pages refreshes

Usage:
  python3 scripts/daily_watch.py
  python3 scripts/daily_watch.py --push
  python3 scripts/daily_watch.py --no-fetch   # rebuild from latest archive only
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
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        check=check,
        text=True,
        capture_output=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="git commit web/data + reports and push")
    ap.add_argument("--no-fetch", action="store_true", help="skip network ingest")
    args = ap.parse_args()

    print(DISCLAIMER)
    print("calibration_status: UNCALIBRATED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    local = datetime.now().astimezone()
    print(f"daily_watch utc={stamp} local={local.isoformat()}")

    if not args.no_fetch:
        # ingest + layer A (run_once already chains layer A)
        rc = run([sys.executable, "-m", "src.ingest.run_once"], check=False).returncode
        if rc != 0:
            print(f"WARN: run_once exit {rc}", flush=True)
            # try layer A from whatever archive exists
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
    report = REPORTS / f"daily_{stamp}.md"
    body = f"""# 台风日更 · {local.strftime("%Y-%m-%d %H:%M %Z")}

> {DISCLAIMER}
> 系统未校准 · 非法定预警 · 内部用语：关注 / 警戒 / 行动

## 焦点：{f.get("zh") or f.get("en") or "—"}（{f.get("en") or ""}）

| 项 | 结论 |
|---|---|
| 研判等级 | **{v.get("level_zh") or "—"}**（p≈{v.get("p_main")}） |
| 现在位置 | {now.get("location_plain") or "—"} |
| 离上海 | {now.get("dist_shanghai_km")} km · {now.get("dist_plain") or ""} |
| 现在强度 | {now.get("grade_plain") or now.get("grade") or "—"} |
| 集合最近点 | 约 {closest.get("days")} 天后 · {closest.get("dist_km")} km · 散布 {closest.get("spread_km")} km |

## 一句话

{v.get("one_liner") or "—"}

## 单位建议

"""
    for i, t in enumerate(v.get("for_activity_planner") or [], 1):
        body += f"{i}. {t}\n"
    body += f"""
## 不确定性

{v.get("uncertainty_plain") or "—"}

## 页面

https://minmengxhw-cpu.github.io/typhoon-impact-system/

---
自动生成 by `scripts/daily_watch.py`
"""
    report.write_text(body, encoding="utf-8")
    # also latest symlink-style copy
    (REPORTS / "latest.md").write_text(body, encoding="utf-8")
    print(f"report → {report}")
    print(f"LEVEL {v.get('level_zh')} storm={f.get('zh')} dist={now.get('dist_shanghai_km')}km")

    if args.push:
        run(["git", "add", "web/data", "reports", "scripts/daily_watch.py"], check=False)
        # only commit if something staged
        st = subprocess.run(
            ["git", "status", "--porcelain", "web/data", "reports"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if st.stdout.strip():
            msg = (
                f"chore(daily): {f.get('zh') or f.get('en')} "
                f"{v.get('level_zh')} dist={now.get('dist_shanghai_km')}km"
            )
            run(["git", "commit", "-m", msg], check=False)
            run(["git", "push", "origin", "main"], check=False)
        else:
            print("no web/data changes to push")

    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
