#!/usr/bin/env python3
"""
Export story-first web snapshot → web/data/ for GitHub Pages.

Primary artifact: story.json (human-readable 白海豚 / focus-storm briefing).
Also writes slim summary/assessment/consensus for compatibility.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "products" / "latest"
DST = ROOT / "web" / "data"
SH_LAT, SH_LON = 31.23, 121.47


def hav(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(min(1.0, a**0.5))


def wind_level_zh(ms) -> str:
    if ms is None:
        return "强度未知"
    if ms >= 51:
        return "超强台风级"
    if ms >= 41.5:
        return "强台风级"
    if ms >= 32.7:
        return "台风级"
    if ms >= 24.5:
        return "强热带风暴级"
    if ms >= 17.2:
        return "热带风暴级"
    return "热带低压级"


def dist_plain(km) -> str:
    if km is None:
        return "距离未知"
    if km > 2500:
        return f"还很远，大约 {km:.0f} 公里（远在西太平洋洋面）"
    if km > 1500:
        return f"仍较远，大约 {km:.0f} 公里"
    if km > 800:
        return f"进入近海外围视野，大约 {km:.0f} 公里"
    if km > 400:
        return f"开始值得盯紧，大约 {km:.0f} 公里"
    if km > 200:
        return f"较近，大约 {km:.0f} 公里"
    return f"很近，大约 {km:.0f} 公里"


def pick_focus(ass_storms, preferred=("DOLPHIN", "白海豚")):
    for name in preferred:
        for s in ass_storms:
            if name.upper() in (s.get("storm_name") or "").upper() or name.upper() in (
                s.get("storm_key") or ""
            ).upper():
                return s
    return ass_storms[0] if ass_storms else None


def build_story() -> dict:
    if not SRC.is_dir():
        raise FileNotFoundError("missing products/latest — run: python3 -m src.model.run_layer_a")

    ass_doc = json.loads((SRC / "assessment.json").read_text(encoding="utf-8"))
    cons_doc = json.loads((SRC / "consensus.json").read_text(encoding="utf-8"))
    tr_doc = json.loads((SRC / "tracks.json").read_text(encoding="utf-8"))
    summary = json.loads((SRC / "summary.json").read_text(encoding="utf-8"))

    storm = pick_focus(ass_doc.get("storms") or [])
    if not storm:
        raise RuntimeError("no assessed storms")
    key = (storm.get("storm_key") or storm.get("storm_name") or "").upper()
    name_u = (storm.get("storm_name") or key).upper()

    c = next(
        (
            s
            for s in (cons_doc.get("storms") or [])
            if (s.get("storm_key") or "").upper() == key
            or (s.get("storm_name") or "").upper() == name_u
        ),
        None,
    )
    if not c:
        raise RuntimeError(f"no consensus for {key}")

    tracks = tr_doc.get("tracks") or []

    def match(t):
        return (t.get("storm_name") or "").upper() == name_u or (
            t.get("storm_id") or ""
        ).upper() in (key, name_u)

    cma_fc = next((t for t in tracks if t.get("source") == "cma_babj" and match(t)), None)
    cma_best = next((t for t in tracks if t.get("source") == "cma_best" and match(t)), None)
    oper = next((t for t in tracks if "ifs_oper" in (t.get("source") or "") and match(t)), None)

    zh_name = "白海豚" if name_u == "DOLPHIN" else (storm.get("storm_name") or key)
    now_pt = cma_best["points"][-1] if cma_best and cma_best.get("points") else c["points"][0]
    now_lat, now_lon = now_pt["lat"], now_pt["lon"]
    now_dist = hav(SH_LAT, SH_LON, now_lat, now_lon)
    now_wind = now_pt.get("wind_ms") or now_pt.get("mean_wind_ms")
    now_grade = now_pt.get("grade") or wind_level_zh(now_wind)

    milestones = []
    for lead in [0, 24, 48, 72, 96, 120, 144, 168]:
        pts = [p for p in c["points"] if abs(p["lead_hours"] - lead) < 0.1]
        if not pts:
            pts = sorted(c["points"], key=lambda p: abs(p["lead_hours"] - lead))[:1]
        p = pts[0]
        d = hav(SH_LAT, SH_LON, p["lat"], p["lon"])
        milestones.append(
            {
                "lead_hours": p["lead_hours"],
                "day_label": f"约 {p['lead_hours']/24:.0f} 天后" if p["lead_hours"] else "现在",
                "lat": p["lat"],
                "lon": p["lon"],
                "dist_km": round(d, 1),
                "dist_plain": dist_plain(d),
                "spread_km": p.get("spread_km"),
                "wind_ms": p.get("mean_wind_ms"),
                "wind_plain": wind_level_zh(p.get("mean_wind_ms")),
            }
        )

    closest = min(c["points"], key=lambda p: hav(SH_LAT, SH_LON, p["lat"], p["lon"]))
    closest_dist = hav(SH_LAT, SH_LON, closest["lat"], closest["lon"])
    level = storm["level_zh"]
    days = (storm.get("dca_lead_hours") or closest["lead_hours"]) / 24.0

    if level == "无影响":
        what = "按当前多源集合，本周活动基本可按原计划推进，只需保持扫一眼最新公报的习惯。"
    elif level == "关注":
        what = (
            f"现在还不到改方案的时候。{zh_name}眼下远在西北太平洋洋面，"
            "集合平均要大约一周后才有可能靠近华东沿海，而且路径分歧很大。"
            "建议：把本周及下周可能受影响的活动先标出来，先不取消、先不对外承诺变更。"
        )
    elif level == "警戒":
        what = "路径开始向上海一侧收敛的概率升高，应启动备选方案与对外沟通口径准备。"
    else:
        what = "进入预备/正式决策窗口，请按单位流程拍板活动是否调整。"

    uncertainty = (
        f"在「可能最近」的时刻（约 {days:.0f} 天后），各模式路径像一把扇子散开，"
        f"平均偏离共识约 {closest.get('spread_km') or '数百'} 公里。"
        "意思是：现在画的「会不会路过上海附近」只有趋势意义，不是定论。"
    )

    ens = [
        t
        for t in tracks
        if t.get("member") is not None
        and match(t)
        and "ifs_enfo" in (t.get("source") or "")
    ][::3][:17]

    story = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "focus_storm": {
            "en": storm.get("storm_name") or key,
            "zh": zh_name,
            "id_cma": "2613" if name_u == "DOLPHIN" else None,
            "atcf": "15W" if name_u == "DOLPHIN" else None,
            "status_now": {
                "grade": now_grade,
                "grade_plain": (
                    "目前是超强台风（非常强），但位置还在很远的洋面上"
                    if (now_wind or 0) >= 50 or str(now_grade).upper() in ("SUPERTY", "STY")
                    or "超强" in str(now_grade)
                    else f"当前等级：{now_grade}"
                ),
                "lat": now_lat,
                "lon": now_lon,
                "dist_shanghai_km": round(now_dist, 1),
                "dist_plain": dist_plain(now_dist),
                "wind_ms": now_wind,
                "wind_plain": wind_level_zh(now_wind if isinstance(now_wind, (int, float)) else None),
                "pressure_hpa": now_pt.get("pressure_hpa"),
                "valid_time_utc": now_pt.get("valid_time_utc"),
                "location_plain": "西太平洋（日本以东洋面一带），向西偏北方向移动",
            },
        },
        "verdict": {
            "level": storm["level"],
            "level_zh": level,
            "p_main": storm["p_main"],
            "headline": f"{zh_name}：对上海暂为「{level}」",
            "one_liner": what,
            "for_activity_planner": [
                "本周末之前：无需改场地/取消；把排期表里 7–10 天后的户外/大型活动标成「观察」",
                "每天看一眼中央气象台与市气象局是否升级通报",
                "真正要拍板的窗口更可能在路径进入 5 天预报可靠区之后（约 D-5 起）",
                "不要只看一张「路径会画到上海旁边」的图就恐慌——当前离散度很大",
            ],
            "uncertainty_plain": uncertainty,
            "closest": {
                "lead_hours": closest["lead_hours"],
                "days": round(closest["lead_hours"] / 24, 1),
                "dist_km": round(closest_dist, 1),
                "lat": closest["lat"],
                "lon": closest["lon"],
                "spread_km": closest.get("spread_km"),
                "wind_ms": closest.get("mean_wind_ms"),
                "plain": (
                    f"多源集合平均：大约 {closest['lead_hours']/24:.0f} 天后，"
                    f"路径中心可能到离上海约 {closest_dist:.0f} 公里的位置；"
                    f"但同时段成员散布约 {closest.get('spread_km') or '—'} 公里，可靠性低。"
                ),
            },
        },
        "milestones": milestones,
        "sources_plain": [
            {"id": "cma", "name": "中央气象台", "role": "国内权威路径，决策引用优先看它"},
            {"id": "ecmwf_ens", "name": "欧洲中心集合", "role": "几十个成员画出「可能走廊」，看分歧用"},
            {"id": "ecmwf_oper", "name": "欧洲中心确定性", "role": "一条「主线」参考，不代表必然"},
            {"id": "aifs", "name": "欧洲 AI 路径", "role": "补充视角；AI 常偏弱强度"},
        ],
        "glossary": [
            {"term": "关注 / 警戒 / 行动", "def": "本系统内部用语，不是气象局蓝黄橙红预警"},
            {"term": "集合离散度", "def": "很多预报「各说各话」的程度；大=更不确定"},
            {"term": "最近点距离 DCA", "def": "预报路径离上海最近时有多远"},
            {"term": "未校准", "def": "阈值还没拿历史台风回算过，只能当趋势参考"},
        ],
        "assessment": storm,
        "consensus": {
            "n_tracks": c["n_tracks"],
            "sources_used": c["sources_used"],
            "method": c["method"],
            "points": [p for p in c["points"] if p["lead_hours"] <= 192],
        },
        "tracks_slim": {
            "cma_forecast": cma_fc,
            "cma_best_tail": (
                {
                    **{k: cma_best[k] for k in cma_best if k != "points"},
                    "points": cma_best["points"][-16:],
                }
                if cma_best
                else None
            ),
            "ecmwf_oper": (
                {
                    **{k: oper[k] for k in oper if k != "points"},
                    "points": [p for p in oper["points"] if p["lead_hours"] <= 192],
                }
                if oper
                else None
            ),
            "ensemble": [
                {
                    "member": t["member"],
                    "source": t["source"],
                    "points": [
                        {"lat": p["lat"], "lon": p["lon"], "lead_hours": p["lead_hours"]}
                        for p in t["points"]
                        if p["lead_hours"] <= 168
                    ],
                }
                for t in ens
            ],
        },
        "disclaimer": "内部研判参考，不构成气象预报。权威信息以上海市气象局、上海市防汛指挥部发布为准。",
        "ui_banner": "未校准 · 仅供内部参考 · 非法定预警",
        "shanghai": {"lat": SH_LAT, "lon": SH_LON, "name": "上海"},
    }

    # compatibility files
    summary = dict(summary)
    summary["headline"] = {
        "level": storm["level"],
        "level_zh": level,
        "storm_name": zh_name,
        "p_main": storm["p_main"],
        "one_liner": story["verdict"]["headline"] + "。" + what[:100],
    }
    summary["focus"] = name_u
    summary["web_export"] = True

    DST.mkdir(parents=True, exist_ok=True)
    (DST / "story.json").write_text(
        json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DST / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DST / "assessment.json").write_text(
        json.dumps(
            {
                "storms": [storm],
                "ui_banner": story["ui_banner"],
                "disclaimer": story["disclaimer"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (DST / "consensus.json").write_text(
        json.dumps(
            {"storms": [c], "disclaimer": story["disclaimer"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return story


def main() -> int:
    try:
        story = build_story()
    except Exception as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 1
    print("exported →", DST)
    print("focus:", story["focus_storm"]["zh"], story["verdict"]["level_zh"])
    print("now dist km:", story["focus_storm"]["status_now"]["dist_shanghai_km"])
    for p in DST.glob("*.json"):
        print(f"  {p.name}: {p.stat().st_size/1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
