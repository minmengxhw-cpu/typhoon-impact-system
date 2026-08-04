# 数据源矩阵 · 底座 v2（2026-08-04 实测）

> 硬约束：无真实 HTTP 样本不得写解析器。本表状态均为本机实测。

## 总览

| ID | 机构/国家 | 角色 | 实时 | 本机状态 | 在管道中 |
|---|---|---|---|---|---|
| ecmwf_opendata_tf | ECMWF / EU | 骨干集合+确定性+AIFS | ✅ | **OK** | ✅ `src/ingest/ecmwf_tf.py` |
| cma_nmc_jsonp | CMA / CN | 国内权威路径 | ✅ | **OK** | ✅ `src/ingest/cma_nmc.py` |
| jma_bosai_target_tc | JMA / JP | 活动台风列表 | ✅ | **OK（间歇超时）** | ✅ `src/ingest/jma.py` |
| jma 完整路径 JSON | JMA / JP | 机构预报点 | ✅ | **未稳定**（404/超时） | ⏳ 列表已接，点迹待二次发现 |
| ucar_chips_realtime_atcf | UCAR+多模式 / US | **美系/多模式实时 a-deck 链** | ✅ | **OK（目录/年度文件存在）** | ✅ `src/ingest/atcf.py` |
| ucar_tcvitals | multi / US | 分析 vitals | ✅ | **OK** | ✅ |
| ucar_adeck_open | UCAR / US | 历史 a-deck（回算） | 季后 | **2024/2025 OK，2026 目录 404** | ✅ 索引归档 |
| ucar_bdeck_open | UCAR / US | 历史最佳路径 | 否 | OK（既有样本） | 回算 |
| jtwc_official | JTWC / US | 官网/RSS | ✅ | **上海超时** | ❌ 不直连 → 走 UCAR ATCF tech |
| nhc_atcf | NHC / US | ATCF 公开 | ✅ | 超时；且**无 WP** | ❌ |
| ibtracs | NOAA / US | 历史最佳 | 近实时 | 间歇超时 | 回算/类比 |
| hko_opendata | HKO / HK | 天气上下文 | ✅ | 本轮超时；旧 GIS 404 | ⏳ |
| cwa_page | CWA / TW | 页面 | ✅ | HTML only | ⏳ 无 token 不接 API |

## 信息链（设计意图）

```
                    ┌─ CMA JSONP ─────────── 国内决策权威
实时路径 ──────────┼─ ECMWF tf BUFR ─────── 集合+确定性+AIFS
                    ├─ JMA targetTc ──────── 日方编号/强度类别（路径点待补）
                    └─ UCAR chips ATCF ───── 美系/多模式 tech 码（OFCL/GFS/…）

分析位置 ────────── UCAR tcvitals

回算 / Layer B ─── UCAR adeck_open + bdeck + IBTrACS
```

## 为何以前「日本/美国是空的」

1. **JTWC 官网**在上海侧长期 **TCP 超时**，不能当主源。  
2. **NHC ATCF** 不做西北太平洋。  
3. **JMA** 旧 bosai 路径 URL 大面积 404；**新发现** `targetTc.json` 可用，完整 forecast JSON 仍在探测。  
4. **正确的美系替代链**是 UCAR：`chips_realtime_atcf` + 历史 `adecks_open`（含 JTWC 等 tech）。

## 运维命令

```bash
# 全源拉取
python3 -m src.ingest.run_once

# 只做健康矩阵
python3 -m src.ingest.health
# -> reports/source_health.json
```

## 已打通的美系/多模式 tech（实测 awp122026.dat · 白海豚=JTWC 12W）

| tech | 含义（近似） |
|---|---|
| CARQ | 分析/查询位 |
| AEMN / AP## | GFS 集合平均/成员 |
| NGX / NP## / NEMN | NAVGEM 及相关 |
| CMC / CEMN / CP## | 加拿大模式 |
| UKM | 英国 |
| CHIP/CHP* | CHIPS 强度类 |

> 注：本文件切片中未出现 OFCL 字段名；**JTWC 官方分析位置**以 `tcvitals` 中 `JTWC 12W DOLPHIN` 行为准。

## 下一步（底座未完项）

1. JMA 完整路径：从 bosai 前端 JS 抓包确认 forecast URL，有样本再解析。  
2. NWP a-deck tech 切片写入统一 Track，进入 multi-agency consensus。  
3. HKO/CWA 在可达时补上下文（预警信号，不作主路径）。  
4. GHA 双环境复测填 GHA 列。
