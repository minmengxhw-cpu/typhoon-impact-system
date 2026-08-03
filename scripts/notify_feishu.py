#!/usr/bin/env python3
"""
Feishu typhoon briefs for Shanghai desk.

Modes:
  --slot morning|evening   always send scheduled brief (综合多源)
  --major                  only send if major change vs last_state
  --force                  always send (like evening/morning without schedule label)
  --dry-run                print only

Usage:
  python3 scripts/notify_feishu.py --slot morning
  python3 scripts/notify_feishu.py --slot evening
  python3 scripts/notify_feishu.py --major
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
STORY = ROOT / "web" / "data" / "story.json"
PRODUCTS = ROOT / "products" / "latest"
STATE = ROOT / "reports" / "last_state.json"
CFG = ROOT / "config" / "notify.yaml"
DISCLAIMER = (
    "内部研判参考，不构成气象预报。"
    "权威信息以上海市气象局、上海市防汛指挥部发布为准。系统未校准。"
)
LEVEL_RANK = {"none": 0, "watch": 1, "alert": 2, "action": 3}
SH = (31.23, 121.47)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    if yaml is None:
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def hav(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(min(1.0, a**0.5))


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
        "todos": list(v.get("for_activity_planner") or [])[:4],
        "uncertainty": v.get("uncertainty_plain") or "",
    }


def detect_major(prev: Optional[dict], cur: dict, rules: dict) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if prev is None:
        if rules.get("notify_on_first_run", False):
            return True, ["首次建立基线状态"]
        return False, []

    if rules.get("level_change", True) and prev.get("level") != cur.get("level"):
        pr = LEVEL_RANK.get(str(prev.get("level")), 0)
        cr = LEVEL_RANK.get(str(cur.get("level")), 0)
        direction = "升级" if cr > pr else "降级"
        reasons.append(f"等级{direction}：{prev.get('level_zh')} → {cur.get('level_zh')}")

    if rules.get("focus_storm_change", True):
        if (prev.get("storm_en") or "") != (cur.get("storm_en") or ""):
            reasons.append(f"焦点台风变化：{prev.get('storm_zh')} → {cur.get('storm_zh')}")

    p_delta = float(rules.get("p_main_delta") or 0.12)
    if abs(float(cur.get("p_main") or 0) - float(prev.get("p_main") or 0)) >= p_delta:
        reasons.append(f"参考概率变化：{float(prev.get('p_main') or 0):.2f} → {float(cur.get('p_main') or 0):.2f}")

    drop = float(rules.get("now_dist_drop_km") or 400)
    prev_now = float(prev.get("now_dist_km") or 9e9)
    cur_now = float(cur.get("now_dist_km") or 9e9)
    if prev_now - cur_now >= drop:
        reasons.append(f"当前位置明显逼近上海：{prev_now:.0f} → {cur_now:.0f} km")

    for thr in rules.get("dca_threshold_km") or [800, 400, 200]:
        thr = float(thr)
        prev_dca = float(prev.get("dca_km") or 9e9)
        cur_dca = float(cur.get("dca_km") or 9e9)
        if prev_dca > thr >= cur_dca:
            reasons.append(f"集合最近点进入 {thr:.0f} km 圈（{prev_dca:.0f}→{cur_dca:.0f}）")
        elif cur_dca > thr >= prev_dca:
            reasons.append(f"集合最近点离开 {thr:.0f} km 圈（{prev_dca:.0f}→{cur_dca:.0f}）")

    enter_h = float(rules.get("dca_lead_enter_hours") or 120)
    prev_lead = float(prev.get("dca_lead_hours") or 9e9)
    cur_lead = float(cur.get("dca_lead_hours") or 9e9)
    if prev_lead > enter_h >= cur_lead:
        reasons.append(f"最近点时效进入 {enter_h:.0f}h 内：{prev_lead:.0f}h → {cur_lead:.0f}h")

    return len(reasons) > 0, reasons


def _point_at_lead(points: List[dict], lead: float) -> Optional[dict]:
    if not points:
        return None
    return min(points, key=lambda p: abs(float(p.get("lead_hours") or 0) - lead))


def multi_source_lines(story: dict, storm_en: str) -> List[str]:
    """Build plain-language multi-agency comparison from products tracks."""
    lines: List[str] = []
    tracks_path = PRODUCTS / "tracks.json"
    slim = (story.get("tracks_slim") or {}) if story else {}

    # Prefer full products if present
    tracks: List[dict] = []
    if tracks_path.exists():
        try:
            doc = json.loads(tracks_path.read_text(encoding="utf-8"))
            tracks = [
                t
                for t in (doc.get("tracks") or [])
                if (t.get("storm_name") or "").upper() == storm_en.upper()
                or (t.get("storm_id") or "").upper() in ("15W", storm_en.upper())
            ]
        except Exception:
            tracks = []

    def desc_track(label: str, t: Optional[dict], wind_note: str) -> Optional[str]:
        if not t or not t.get("points"):
            return None
        pts = t["points"]
        p0 = pts[0]
        # furthest forecast with position
        p_far = pts[-1]
        # prefer ~72h and ~120h
        p72 = _point_at_lead(pts, 72) or p_far
        p120 = _point_at_lead(pts, 120) or p_far
        d0 = hav(SH[0], SH[1], float(p0["lat"]), float(p0["lon"]))
        d72 = hav(SH[0], SH[1], float(p72["lat"]), float(p72["lon"]))
        d120 = hav(SH[0], SH[1], float(p120["lat"]), float(p120["lon"]))
        w0 = p0.get("wind_ms")
        wnote = f"，风速约 {w0:.0f} m/s（{wind_note}）" if isinstance(w0, (int, float)) else f"（{wind_note}）"
        return (
            f"**{label}**：近中心距上海约 {d0:.0f} km{wnote}；"
            f"约 3 天后约 {d72:.0f} km，约 5 天后约 {d120:.0f} km"
        )

    # Named sources
    cma = next((t for t in tracks if t.get("source") == "cma_babj"), None) or slim.get(
        "cma_forecast"
    )
    oper = next(
        (t for t in tracks if t.get("source") == "ecmwf_ifs_oper" and t.get("member") is None),
        None,
    ) or slim.get("ecmwf_oper")
    aifs = next(
        (t for t in tracks if "aifs_single" in (t.get("source") or "") and t.get("member") is None),
        None,
    )

    for label, t, note in [
        ("中国 · 中央气象台", cma, "CMA 约 2 分钟风，国内决策优先引用"),
        ("欧洲 · ECMWF 确定性", oper, "ECMWF 约 10 米风"),
        ("欧洲 · AIFS（AI）", aifs, "AI 路径，强度常偏弱"),
    ]:
        s = desc_track(label, t, note)
        if s:
            lines.append(s)

    # Ensemble spread from consensus
    cons = story.get("consensus") or {}
    pts = cons.get("points") or []
    if pts:
        p120 = _point_at_lead(pts, 120) or pts[min(len(pts) - 1, 20)]
        spread = p120.get("spread_km")
        d = hav(SH[0], SH[1], float(p120["lat"]), float(p120["lon"]))
        lines.append(
            f"**欧洲 · ECMWF/AIFS 集合共识**：约 5 天后中心距上海约 {d:.0f} km"
            + (f"，成员平均离散约 {spread:.0f} km（越大越不确定）" if spread is not None else "")
        )

    # Honesty about unavailable agencies
    lines.append(
        "**日本 JMA / 美军 JTWC**：实时接口本阶段尚未稳定接入"
        "（旧路径 404 或网络超时），本报不编造其路径；回算可用 UCAR a-deck 历史库。"
    )
    lines.append(
        "**香港天文台 / 台湾 CWA**：未作为路径主源解析；有官方公报时以原文为准。"
    )
    if not lines:
        lines.append("多源细节暂不可用，仅有综合等级结论。")
    return lines


def advice_for_slot(slot: str, cur: dict) -> List[str]:
    level = cur.get("level") or "none"
    dca_h = float(cur.get("dca_lead_hours") or 999)
    dca = float(cur.get("dca_km") or 9999)
    base = list(cur.get("todos") or [])
    extra: List[str] = []
    if slot == "morning":
        extra.append("今日：扫一眼中央气象台与市气象局晨间更新，活动排期标「观察」即可")
        if level in ("none", "watch") and dca_h > 120:
            extra.append("今日一般无需改场地/取消；重点看 5–7 天后的户外场次")
        elif level == "alert" or (dca_h <= 120 and dca < 400):
            extra.append("今日起备选方案应成型，对外口径统一，合同取消时限提前核对")
        elif level == "action":
            extra.append("今日进入决策窗口：按单位流程明确是否调整活动")
    else:  # evening
        extra.append("今晚：对照早报看路径/强度是否收拢；若无明显变化，明日继续观察")
        if level in ("none", "watch"):
            extra.append("明日白天一般可按原计划；把后天及以后的风险场次再标一遍")
        if dca_h <= 96:
            extra.append("最近点已进入约 4 天内：明晨起提高查看频率（官方公报 + 本页）")
    # merge unique
    out: List[str] = []
    for x in extra + base:
        if x and x not in out:
            out.append(x)
    return out[:5]


def build_scheduled_markdown(
    cur: dict,
    story: dict,
    slot: str,
    slot_label: str,
    reasons: Optional[List[str]] = None,
    urgent: bool = False,
    page_url: str = "",
) -> str:
    local = datetime.now().astimezone()
    title_bits = []
    if urgent:
        title_bits.append("【升级】")
    title_bits.append(f"台风{slot_label}")
    title = "".join(title_bits)

    multi = multi_source_lines(story, cur.get("storm_en") or "DOLPHIN")
    advice = advice_for_slot(slot, cur)

    lines = [
        f"**{title}** · {local.strftime('%Y-%m-%d %H:%M')}（北京时间）",
        "",
        f"**焦点**：{cur.get('storm_zh')}（{cur.get('storm_en')}）",
        f"**对上海综合研判**：**{cur.get('level_zh')}**"
        f"（参考 p≈{float(cur.get('p_main') or 0):.2f}，未校准；非蓝黄橙红预警）",
        f"**现在**：距上海约 {float(cur.get('now_dist_km') or 0):.0f} km · {cur.get('now_grade')}",
        (
            f"**集合「可能最近」**：约 {float(cur.get('dca_lead_hours') or 0)/24:.1f} 天后 · "
            f"{float(cur.get('dca_km') or 0):.0f} km"
            + (
                f" · 散布 {cur.get('dca_spread_km')} km"
                if cur.get("dca_spread_km") is not None
                else ""
            )
        ),
        "",
        f"**一句话**：{cur.get('one_liner') or '—'}",
        "",
        "**多国/多源对照（人话）**",
    ]
    for m in multi:
        lines.append(f"- {m}")

    if reasons:
        lines += ["", "**相对上一报的变化**"]
        for r in reasons:
            lines.append(f"- {r}")

    lines += ["", f"**{slot_label}行动建议**"]
    for i, a in enumerate(advice, 1):
        lines.append(f"{i}. {a}")

    if cur.get("uncertainty"):
        lines += ["", f"**不确定性**：{cur.get('uncertainty')}"]

    lines += [
        "",
        f"看板：{page_url}",
        "",
        f"_{DISCLAIMER}_",
    ]
    return "\n".join(lines)


def send_feishu(chat_id: str, markdown: str, as_identity: str = "bot") -> dict:
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
    ap.add_argument("--slot", choices=["morning", "evening"], default=None)
    ap.add_argument("--major", action="store_true", help="only notify on major change")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--story", type=Path, default=STORY)
    args = ap.parse_args()

    if not args.story.exists():
        print(f"missing {args.story}", file=sys.stderr)
        return 2

    cfg = load_yaml(CFG)
    feishu = cfg.get("feishu") or {}
    rules = cfg.get("major_change") or {}
    schedule = cfg.get("schedule") or {}
    if not feishu.get("enabled", True) and not args.force:
        print("feishu disabled")
        return 0
    chat_id = feishu.get("chat_id") or ""
    if not chat_id:
        print("no chat_id", file=sys.stderr)
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

    # Decide send mode
    slot = args.slot
    always = bool(slot) or args.force
    only_major = args.major and not always

    if only_major and not major and not args.force:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        print("no major change — state updated, no notify")
        return 0

    if not always and not major and not args.force:
        # default behaviour when no flags: major-only
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        print("no major change — state updated, no notify")
        return 0

    # scheduled slot label
    if slot:
        slot_label = (schedule.get(slot) or {}).get("label_zh") or (
            "早报" if slot == "morning" else "晚报"
        )
    else:
        slot_label = "快报"
        slot = "evening"  # default advice tone

    urgent = False
    if major and prev:
        if LEVEL_RANK.get(cur["level"], 0) >= 2 and LEVEL_RANK.get(cur["level"], 0) > LEVEL_RANK.get(
            prev.get("level"), 0
        ):
            urgent = True
    if cur.get("level") == "action":
        urgent = True
    if float(cur.get("dca_lead_hours") or 999) <= 120 and float(cur.get("dca_km") or 9999) < 400:
        if major or slot:
            urgent = urgent or (LEVEL_RANK.get(cur["level"], 0) >= 2)

    page = feishu.get("page_url") or "https://minmengxhw-cpu.github.io/typhoon-impact-system/"
    md = build_scheduled_markdown(
        cur,
        story,
        slot=slot or "evening",
        slot_label=slot_label,
        reasons=reasons if major else None,
        urgent=urgent,
        page_url=page,
    )
    print(f"send slot={slot} major={major} urgent={urgent}")
    print("---")
    print(md)
    print("---")

    if args.dry_run:
        print("dry-run: not sending")
        return 0

    result = send_feishu(chat_id, md, as_identity=str(feishu.get("as") or "bot"))
    print("sent:", json.dumps(result, ensure_ascii=False)[:400])

    cur["last_notified_at_utc"] = datetime.now(timezone.utc).isoformat()
    cur["last_notify_slot"] = slot
    cur["last_notify_reasons"] = reasons
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    logp = ROOT / "reports" / "notify.log"
    with open(logp, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "at": cur["last_notified_at_utc"],
                    "slot": slot,
                    "major": major,
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
