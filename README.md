# 台风影响研判系统

把多源台风路径预报翻译成**本单位行动决策**（这周活动要不要动、何时拍板）。  
**不自建路径数值模式**；不与上海台风研究所比路径精度。

> 内部研判参考，不构成气象预报。权威信息以上海市气象局、上海市防汛指挥部发布为准。

## 能力分层

| 层 | 内容 | 状态 |
|---|---|---|
| A | 多源集合共识 + 不确定性（离散度须叠加历史误差圈） | Phase 1 目标 |
| B | 性能加权与上海影响降尺度 | 需 1–2 个台风季存档 |
| C | 自跑 AI 气象模型（可选） | 实验分支 |

## 当前进度

| 阶段 | 状态 |
|---|---|
| **Phase 0 数据源实测** | 主干完成；GHA/JMA/本地潮位见 `docs/phase0-remaining.md` |
| **Phase 1 Layer A** | **已可运行**：BUFR/CMA 统一路径 → 等权共识 → 未校准影响评分 → 三屏静态页 |

- ✅ ECMWF `type=tf`（IFS ENS/oper、AIFS）+ CMA JSONP 解析  
- ✅ 等权共识路径 + 集合离散度（**非**确定性分数；未叠历史误差圈）  
- ✅ 上海影响规则 → 无影响/关注/警戒/行动（阈值 `initial_guess`）  
- ✅ `web/index.html` 决策 / 路径 / 影响三屏  
- ⏳ GitHub Actions 双环境补测  
- ⏳ JMA 实时、潮位、回算校准（Layer B）

## 硬约束（摘录）

1. 禁止编造数据源；无审计样本不得写解析器  
2. 内部用语：关注 / 警戒 / 行动；禁用预警蓝黄橙红  
3. 每屏固定免责声明  
4. D-5 以外不画确定性路径  
5. 抓取失败显式标注，不用历史值伪装  
6. 强度标注来源与平均时距  
7. **每次抓取原样存档** → `archive/YYYYMMDD/HHz/{source}/`

## 快速开始

```bash
cd typhoon-impact-system
python3 -m pip install -r requirements.txt
# eccodes 系统库（macOS）
brew install eccodes

# 1) 拉取并原样存档 + 自动跑 Layer A
python3 -m src.ingest.run_once

# 2) 仅从已有 archive 重建产品（不联网）
python3 -m src.model.run_layer_a
# 或指定某次存档：
python3 -m src.model.run_layer_a archive/20260802/13z

# 3) 本地打开决策页
python3 -m http.server 8765
# 浏览器: http://127.0.0.1:8765/web/
```

产品输出：`products/latest/{summary,assessment,consensus,tracks}.json`

## 目录

```
docs/           任务书、审计、目标定义
config/         阈值与换算（初始猜测）
scripts/audit/  数据源实测
data/samples/   审计真实样本（勿当业务缓存）
archive/        业务预报原样存档
products/       Layer A 产品 JSON（运行时生成）
web/            三屏静态前端
src/ingest/     抓取与存档
src/model/      解析 / 共识 / 影响评分
```

## GitHub Pages

**https://minmengxhw-cpu.github.io/typhoon-impact-system/**

## 早晚自动研判 → 飞书

| 时间 | 任务 |
|---|---|
| **08:30** | 早报：拉数 → 多源研判 → 飞书 + 更新 Pages |
| **20:30** | 晚报：同上 |
| 重大变化 | 等级/距离圈/焦点台风变化时，简报内标注「相对上一报的变化」 |

```bash
# 本机已装 launchd：com.typhoon.morning / com.typhoon.evening
# 手动补发
python3 scripts/daily_watch.py --slot morning --push
python3 scripts/daily_watch.py --slot evening --push
```

飞书群：`config/notify.yaml` → `oc_381bea46653394d135daf14739524904`（机器人「团宝」）

综合源（人话对照）：**中国中央气象台** + **欧洲 ECMWF 确定性/集合** + **AIFS**；JMA/JTWC 实时未稳定接入时明确写「不编造」。

## 文档

- [构建任务书 v2](docs/构建任务书-v2.md)
- [数据源审计](docs/data-sources-audit.md)
- [预测目标](docs/targets.md)
- [Phase 0 剩余](docs/phase0-remaining.md)
- [部署](docs/deploy.md)
