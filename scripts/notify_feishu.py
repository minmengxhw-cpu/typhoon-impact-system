#!/usr/bin/env python3
"""
Detect major assessment changes and push Feishu group message.

State: reports/last_state.json
Config: config/notify.yaml

Usage:
  python3 scripts/notify_feishu.py              # compare + maybe send
  python3 scripts/notify_feishu.py --force       # always send current brief
  python3 scripts/notify_feishu.py --dry-run     # print only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
STORY = ROOT / "web" / "data" / "story.json"
STATE = ROOT / "reports" / "last_state.json"
CFG = ROOT / "config" / "notify.yaml"
DISCLAIMER = (
    "内部研判参考，不构成气象预报。"
    "权威信息以上海市气象局、上海市防汛指挥部发布为准。系统未校准。"
)

LEVEL_RANK = {"none": 0, "watch": 1, "alert": 2, "action": 3}


def load_yaml(path: Path) -> dict:
    if yaml is None:
        # minimal fallback for chat_id only
        text = path.read_text(encoding="utf-8")
        out: dict = {"feishu": {}, "major_change": {}}
        for line in text.splitlines():
            if "chat_id:" in line:
                out["feishu"]["chat_id"] = line.split(":", 1)[1].strip().strip('"')
            if "enabled:" in line and "feishu" not in str(out.get("_sec")):
                out["feishu"]["enabled"] = "true" in line.lower()
        return out
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def snapshot_from_story(story: dict) -> dict:
    f = story.get("focus_storm") or {}
    v = story.get("verdict") or {}
    now = f.get("status_now") or {}
    closest = v.get("closest") or {}
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "storm_en": (f.get("en") or "").upper(),
        "storm_zh": f.get("zh") or f.get("en") or "",
        "level": v.get("level") or "none",
        "level_zh": v.get("level_zh") or "",
        "p_main": float(v.get("p_main") or 0),
        "now_dist_km": float(now.get("dist_shanghai_km") or 9e9),
        "now_grade": now.get("grade") or now.get("grade_plain") or "",
        "dca_km": float(closest.get("dist_km") or 9e9),
        "dca_lead_hours": float(closest.get("lead_hours") or 9e9),
        "dca_spread_km": closest.get("spread_km"),
        "one_liner": v.get("one_liner") or "",
        "headline": v.get("headline") or "",
    }


def detect_major(prev: Optional[dict], cur: dict, rules: dict) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if prev is None:
        if rules.get("notify_on_first_run", True):
            return True, ["首次建立基线状态"]
        return False, []

    if rules.get("level_change", True) and prev.get("level") != cur.get("level"):
        pr = LEVEL_RANK.get(str(prev.get("level")), 0)
        cr = LEVEL_RANK.get(str(cur.get("level")), 0)
        direction = "升级" if cr > pr else "降级"
        reasons.append(
            f"等级{direction}：{prev.get('level_zh')} → {cur.get('level_zh')}"
        )

    if rules.get("focus_storm_change", True):
        if (prev.get("storm_en") or "") != (cur.get("storm_en") or ""):
            reasons.append(
                f"焦点台风变化：{prev.get('storm_zh')} → {cur.get('storm_zh')}"
            )

    p_delta = float(rules.get("p_main_delta") or 0.15)
    if abs(float(cur.get("p_main") or 0) - float(prev.get("p_main") or 0)) >= p_delta:
        reasons.append(
            f"参考概率变化：{prev.get('p_main'):.2f} → {cur.get('p_main'):.2f}"
        )

    drop = float(rules.get("now_dist_drop_km") or 500)
    prev_now = float(prev.get("now_dist_km") or 9e9)
    cur_now = float(cur.get("now_dist_km") or 9e9)
    if prev_now - cur_now >= drop:
        reasons.append(
            f"当前位置明显逼近上海：{prev_now:.0f} → {cur_now:.0f} km（减少≥{drop:.0f}）"
        )

    thresholds = rules.get("dca_threshold_km") or [800, 400, 200]
    prev_dca = float(prev.get("dca_km") or 9e9)
    cur_dca = float(cur.get("dca_km") or 9e9)
    for thr in thresholds:
        thr = float(thr)
        crossed_in = prev_dca > thr >= cur_dca
        crossed_out = cur_dca > thr >= prev_dca
        if crossed_in:
            reasons.append(f"集合最近点进入 {thr:.0f} km 圈内（{prev_dca:.0f}→{cur_dca:.0f}）")
        elif crossed_out:
            reasons.append(f"集合最近点离开 {thr:.0f} km 圈（{prev_dca:.0f}→{cur_dca:.0f}）")

    enter_h = float(rules.get("dca_lead_enter_hours") or 120)
    prev_lead = float(prev.get("dca_lead_hours") or 9e9)
    cur_lead = float(cur.get("dca_lead_hours") or 9e9)
    if prev_lead > enter_h >= cur_lead:
        reasons.append(
            f"最近点时效进入 {enter_h:.0f}h 内（约 D-5）：{prev_lead:.0f}h → {cur_lead:.0f}h"
        )

    # upgrade urgency flag for title
    return (len(reasons) > 0), reasons


def build_markdown(
    cur: dict,
    reasons: List[str],
    page_url: str,
    urgent: bool,
) -> str:
    title = "【升级】台风研判重大变化" if urgent else "台风研判重大变化"
    lines = [
        f"**{title}**",
        "",
        f"**焦点**：{cur.get('storm_zh')}（{cur.get('storm_en')}）",
        f"**等级**：{cur.get('level_zh')}（p≈{float(cur.get('p_main') or 0):.2f}，未校准）",
        f"**现在**：距上海约 {float(cur.get('now_dist_km') or 0):.0f} km · {cur.get('now_grade')}",
        (
            f"**集合最近点**：约 {float(cur.get('dca_lead_hours') or 0)/24:.1f} 天后 · "
            f"{float(cur.get('dca_km') or 0):.0f} km"
            + (
                f" · 散布 {cur.get('dca_spread_km')} km"
                if cur.get("dca_spread_km") is not None
                else ""
            )
        ),
        "",
        "**变化原因**",
    ]
    for r in reasons:
        lines.append(f"- {r}")
    lines += [
        "",
        f"**一句话**：{cur.get('one_liner') or '—'}",
        "",
        f"页面：{page_url}",
        "",
        f"_{DISCLAIMER}_",
    ]
    return "\n".join(lines)


def send_feishu(chat_id: str, markdown: str, as_identity: str = "user") -> dict:
    cmd = [
        "lark-cli",
        "im",
        "+messages-send",
        "--as",
        as_identity,
        "--chat-id",
        chat_id,
        "--markdown",
        markdown,
        "--format",
        "json",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise RuntimeError(f"lark-cli failed rc={p.returncode}: {out[-2000:]}")
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"raw": p.stdout}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--story", type=Path, default=STORY)
    args = ap.parse_args()

    if not args.story.exists():
        print(f"missing {args.story}", file=sys.stderr)
        return 2
    cfg = load_yaml(CFG) if CFG.exists() else {}
    feishu = cfg.get("feishu") or {}
    rules = cfg.get("major_change") or {}
    if not feishu.get("enabled", True) and not args.force:
        print("feishu notify disabled")
        return 0

    chat_id = feishu.get("chat_id") or ""
    if not chat_id:
        print("no chat_id in config/notify.yaml", file=sys.stderr)
        return 2

    story = json.loads(args.story.read_text(encoding="utf-8"))
    cur = snapshot_from_story(story)
    prev = None
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            prev = None

    major, reasons = detect_major(prev, cur, rules)
    if args.force and not reasons:
        reasons = ["手动强制推送"]
        major = True

    print(f"major={major} reasons={reasons}")
    print(
        f"cur level={cur['level_zh']} storm={cur['storm_zh']} "
        f"now_dist={cur['now_dist_km']:.0f} dca={cur['dca_km']:.0f}@{cur['dca_lead_hours']:.0f}h"
    )

    # always update state after evaluation (so first-run only once)
    STATE.parent.mkdir(parents=True, exist_ok=True)

    if not major:
        STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        print("no major change — state updated, no notify")
        return 0

    urgent = False
    if prev and LEVEL_RANK.get(cur["level"], 0) > LEVEL_RANK.get(prev.get("level"), 0):
        if LEVEL_RANK.get(cur["level"], 0) >= 2:
            urgent = True
    if cur.get("level") in ("alert", "action"):
        if any("进入" in r or "升级" in r for r in reasons):
            urgent = True
    if cur.get("dca_lead_hours", 9e9) <= 120 and cur.get("dca_km", 9e9) < 400:
        urgent = True

    page = feishu.get("page_url") or "https://minmengxhw-cpu.github.io/typhoon-impact-system/"
    md = build_markdown(cur, reasons, page, urgent=urgent)
    print("--- message ---")
    print(md)
    print("--- end ---")

    if args.dry_run:
        print("dry-run: not sending, not writing state")
        return 0

    result = send_feishu(chat_id, md, as_identity=str(feishu.get("as") or "user"))
    print("sent:", json.dumps(result, ensure_ascii=False)[:500])
    cur["last_notified_at_utc"] = datetime.now(timezone.utc).isoformat()
    cur["last_notify_reasons"] = reasons
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    # log
    logp = ROOT / "reports" / "notify.log"
    with open(logp, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "at": cur["last_notified_at_utc"],
                    "reasons": reasons,
                    "level": cur["level"],
                    "storm": cur["storm_zh"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
