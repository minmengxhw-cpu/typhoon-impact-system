"""
Shanghai impact rule scorer — Layer A, uncalibrated.

Maps multi-source consensus + intensity features → p(Y_main=1) → level.
All thresholds from config/thresholds.yaml (status: initial_guess).

UI must show: 系统未校准 · 概率切点为初始猜测
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .consensus import ConsensusTrack
from .schema import haversine_km

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLDS = ROOT / "config" / "thresholds.yaml"

DISCLAIMER = (
    "内部研判参考，不构成气象预报。"
    "权威信息以上海市气象局、上海市防汛指挥部发布为准。"
)


@dataclass
class ImpactAssessment:
    storm_key: str
    storm_name: str
    level: str  # none / watch / alert / action
    level_zh: str
    p_main: float
    dca_km: Optional[float]
    dca_lead_hours: Optional[float]
    wind_at_closest_ms: Optional[float]
    quadrant: Optional[str]
    motion_kmh: Optional[float]
    features: Dict[str, Any]
    rationale: List[str]
    calibration_status: str = "uncalibrated"

    def to_dict(self) -> dict:
        return {
            "storm_key": self.storm_key,
            "storm_name": self.storm_name,
            "level": self.level,
            "level_zh": self.level_zh,
            "p_main": round(self.p_main, 3),
            "dca_km": round(self.dca_km, 1) if self.dca_km is not None else None,
            "dca_lead_hours": self.dca_lead_hours,
            "wind_at_closest_ms": (
                round(self.wind_at_closest_ms, 1)
                if self.wind_at_closest_ms is not None
                else None
            ),
            "quadrant": self.quadrant,
            "motion_kmh": (
                round(self.motion_kmh, 1) if self.motion_kmh is not None else None
            ),
            "features": self.features,
            "rationale": self.rationale,
            "calibration_status": self.calibration_status,
            "disclaimer": DISCLAIMER,
            "ui_banner": "未校准 · 概率切点为初始猜测 · 仅供内部参考",
        }


def load_thresholds(path: Optional[Path] = None) -> dict:
    path = path or DEFAULT_THRESHOLDS
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _level_from_p(p: float, levels: dict) -> tuple[str, str]:
    # Check action → alert → watch → none
    order = ["action", "alert", "watch", "none"]
    for key in order:
        cfg = levels.get(key) or {}
        pmin = float(cfg.get("p_min", 0))
        pmax = float(cfg.get("p_max", 1))
        if pmin <= p < pmax or (key == "action" and p >= pmin):
            return key, cfg.get("label_zh", key)
    return "none", "无影响"


def _quadrant(sh_lat: float, sh_lon: float, lat: float, lon: float) -> str:
    """Shanghai relative to storm centre (storm-centric)."""
    # Position of Shanghai relative to storm
    dlat = sh_lat - lat
    dlon = sh_lon - lon
    ns = "N" if dlat >= 0 else "S"
    ew = "E" if dlon >= 0 else "W"
    return f"{ns}{ew}"  # e.g. NW = Shanghai is north-west of storm (storm SE of city)


def _motion_kmh(consensus: ConsensusTrack) -> Optional[float]:
    pts = [p for p in consensus.points if p.lead_hours <= 48]
    if len(pts) < 2:
        return None
    a, b = pts[0], pts[-1]
    dt = b.lead_hours - a.lead_hours
    if dt <= 0:
        return None
    dist = haversine_km(a.lat, a.lon, b.lat, b.lon)
    return dist / dt


def assess_from_consensus(
    consensus: ConsensusTrack,
    thresholds: Optional[dict] = None,
) -> ImpactAssessment:
    cfg = thresholds or load_thresholds()
    sh = cfg.get("shanghai_impact") or {}
    sh_lat = float(sh.get("shanghai_lat", 31.23))
    sh_lon = float(sh.get("shanghai_lon", 121.47))
    dca_watch = float(sh.get("dca_watch_km", 800))
    dca_alert = float(sh.get("dca_alert_km", 400))
    dca_action = float(sh.get("dca_action_km", 200))
    wind_ts = float(sh.get("wind_ts_ms", 17.2))
    wind_ty = float(sh.get("wind_ty_ms", 32.7))
    slow_kmh = float(sh.get("slow_motion_kmh", 15))

    rationale: List[str] = []
    # Closest approach on consensus track
    dca = None
    dca_lead = None
    wind_closest = None
    quad = None
    for p in consensus.points:
        d = haversine_km(sh_lat, sh_lon, p.lat, p.lon)
        if dca is None or d < dca:
            dca = d
            dca_lead = p.lead_hours
            wind_closest = p.mean_wind_ms
            quad = _quadrant(sh_lat, sh_lon, p.lat, p.lon)

    motion = _motion_kmh(consensus)

    # Feature scores in [0, 1] — uncalibrated heuristic
    # Lead-time discount: beyond ~120 h (D-5) path geometry is weak evidence only
    lead_h = float(dca_lead) if dca_lead is not None else 999.0
    if lead_h <= 72:
        lead_w = 1.0
    elif lead_h <= 120:
        lead_w = 0.75
    elif lead_h <= 168:
        lead_w = 0.45
    else:
        lead_w = 0.25

    if dca is None:
        f_dca = 0.0
        rationale.append("无可用共识路径点")
    elif dca <= dca_action:
        f_dca = 1.0 * lead_w
        rationale.append(
            f"最近点距离 {dca:.0f} km ≤ 行动阈 {dca_action:.0f} km"
            f"（时效 {lead_h:.0f} h，权重 {lead_w:.2f}）"
        )
    elif dca <= dca_alert:
        f_dca = 0.7 * lead_w
        rationale.append(
            f"最近点距离 {dca:.0f} km ≤ 警戒阈 {dca_alert:.0f} km"
            f"（时效 {lead_h:.0f} h，权重 {lead_w:.2f}）"
        )
    elif dca <= dca_watch:
        f_dca = 0.35 * lead_w
        rationale.append(
            f"最近点距离 {dca:.0f} km ≤ 关注阈 {dca_watch:.0f} km"
            f"（时效 {lead_h:.0f} h，权重 {lead_w:.2f}）"
        )
    else:
        f_dca = max(0.0, 0.15 * (1500 - dca) / 700) * lead_w if dca < 1500 else 0.0
        rationale.append(f"最近点距离 {dca:.0f} km，暂远离上海")

    if wind_closest is None:
        f_wind = 0.2
        rationale.append("最近点强度缺失，风因子弱默认")
    elif wind_closest >= wind_ty:
        f_wind = 1.0
        rationale.append(f"最近点附近风速 {wind_closest:.0f} m/s（约台风级，10 min 相当）")
    elif wind_closest >= wind_ts:
        f_wind = 0.55
        rationale.append(f"最近点附近风速 {wind_closest:.0f} m/s（约热带风暴级）")
    else:
        f_wind = 0.2
        rationale.append(f"最近点附近风速 {wind_closest:.0f} m/s，偏弱")

    f_slow = 0.0
    if motion is not None and motion < slow_kmh and (dca or 9999) < dca_watch:
        f_slow = 0.25
        rationale.append(f"移速约 {motion:.0f} km/h，慢速滞留抬升累计雨量风险")

    f_quad = 0.0
    if quad and (dca or 9999) < dca_watch:
        # NE of storm ≈ right-front for westward movers → wind/surge; NW → rain
        if quad in ("NE", "SE"):
            f_quad = 0.15
            rationale.append(f"上海位于风暴 {quad} 象限（偏风/增水情景，启发式）")
        elif quad in ("NW", "SW"):
            f_quad = 0.12
            rationale.append(f"上海位于风暴 {quad} 象限（偏雨情景，启发式）")

    # Mean spread near closest lead — high spread slightly lowers p (uncertainty)
    f_spread_penalty = 0.0
    if consensus.points and dca_lead is not None:
        near = min(consensus.points, key=lambda p: abs(p.lead_hours - dca_lead))
        if near.spread_km > 300:
            f_spread_penalty = 0.1
            rationale.append(
                f"时效 {near.lead_hours:.0f} h 离散度 {near.spread_km:.0f} km，"
                "不确定性高（非确定性分数）"
            )

    # Combine — weights are initial_guess
    raw = (
        0.50 * f_dca
        + 0.30 * f_wind
        + 0.10 * f_slow
        + 0.10 * f_quad
        - f_spread_penalty
    )
    raw = max(0.0, min(1.0, raw))
    # Map feature score → p with mild compression (still uncalibrated)
    p = 0.05 + 0.90 * raw

    levels = cfg.get("levels") or {}
    level, level_zh = _level_from_p(p, levels)

    return ImpactAssessment(
        storm_key=consensus.storm_key,
        storm_name=consensus.storm_name,
        level=level,
        level_zh=level_zh,
        p_main=p,
        dca_km=dca,
        dca_lead_hours=dca_lead,
        wind_at_closest_ms=wind_closest,
        quadrant=quad,
        motion_kmh=motion,
        features={
            "f_dca": round(f_dca, 3),
            "f_wind": round(f_wind, 3),
            "f_slow": round(f_slow, 3),
            "f_quad": round(f_quad, 3),
            "f_spread_penalty": round(f_spread_penalty, 3),
            "raw_score": round(raw, 3),
            "shanghai": {"lat": sh_lat, "lon": sh_lon},
            "thresholds_status": (cfg.get("meta") or {}).get("status", "initial_guess"),
        },
        rationale=rationale,
        calibration_status="uncalibrated",
    )


def decision_timeline(level: str, dca_lead_hours: Optional[float]) -> List[dict]:
    """Fixed decision timeline component (task book §8)."""
    rows = [
        {"horizon": "D-7~D-6", "nature": "生成潜势与集合趋势，仅概率", "action": "扫描活动排期，标记风险场次"},
        {"horizon": "D-5", "nature": "官方路径开始有效，分歧较大", "action": "通知相关条线，备选方案成型"},
        {"horizon": "D-4~D-3", "nature": "多来源收敛度可判", "action": "准备对外沟通口径"},
        {"horizon": "D-2", "nature": "风雨潮量级可估", "action": "预备决策（核对合同免费取消时限）"},
        {"horizon": "D-1", "nature": "误差大幅收敛", "action": "正式决策发布"},
        {"horizon": "D-0", "nature": "实况监测", "action": "执行与现场安全"},
    ]
    # Highlight row nearest to closest approach if known
    if dca_lead_hours is not None:
        days = dca_lead_hours / 24.0
        if days >= 6:
            focus = "D-7~D-6"
        elif days >= 4.5:
            focus = "D-5"
        elif days >= 2.5:
            focus = "D-4~D-3"
        elif days >= 1.5:
            focus = "D-2"
        elif days >= 0.5:
            focus = "D-1"
        else:
            focus = "D-0"
        for r in rows:
            r["highlight"] = r["horizon"] == focus
    return rows
