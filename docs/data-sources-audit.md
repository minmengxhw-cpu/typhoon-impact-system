# 数据源审计报告 · Phase 0

> **审计时间**: 2026-08-02（UTC 约 13:30–14:00）  
> **本机网络环境**: macOS，开发者本机（上海侧出站，记为 **Local**）  
> **境外 CI 环境**: 本轮**未**在 GitHub Actions 实测（记为 **GHA: 待补测**）  
> **硬约束遵循**: 凡写入本报告的源均经真实 HTTP/SDK 调用；样本路径见 `data/samples/`；无法出示真实返回的源不进入 Phase 1 解析代码。

---

## 0. 总览

| 源 | 定位 | Local | GHA | 样本 | Phase 1 建议 |
|---|---|---|---|---|---|
| **ECMWF Open Data `type=tf`** | **骨干主源** | ✅ | 待补测 | ✅ BUFR + 解码 | **立即接入** |
| ECMWF AWS 镜像 | 主源回退 | ✅ | 待补测 | ✅ | 与直连并行 |
| ECMWF Azure 镜像 | 回退 | ⚠️ 需 SAS | 待补测 | 目录 409 | 经 `ecmwf-opendata` 取 SAS |
| CMA 中央气象台 `typhoon.nmc.cn` | 决策引用主源 | ✅ | 待补测 | ✅ JSONP | **立即接入** |
| JTWC 官网 metoc/nrl | 独立预报 | ❌ 超时 | 待补测 | — | 不直连；改用 UCAR a-deck |
| NHC ATCF `aid_public` | 历史预报格式参考 | ✅ 仅 AL/EP/CP | 待补测 | ✅ a-deck | 格式参考；**无 WP** |
| **UCAR RAL adecks_open / bdecks_open** | **回算历史预报+最佳路径** | ✅ | 待补测 | ✅ WP a/b-deck | **回算必接** |
| IBTrACS | 历史最佳路径 | ✅ | 待补测 | ✅ CSV 头 | 历史类比/校准 |
| HKO Open Data | 补充/对照 | ✅ 通用天气 | 待补测 | ✅ JSON | 预警类可用；路径 GIS 旧接口 404 |
| JMA bosai 台风 JSON | 机构预报 | ❌ 旧路径 404 | 待补测 | HTML 壳可达 | **暂不接入**，待二次发现 |
| CWA / KMA 页面 | 次要 | ✅ HTML | 待补测 | HTML | 暂不解析 |
| CMA-STI `tcdata.typhoon.org.cn` | 最佳路径 | ❌ WAF 468 | 待补测 | SafeLine 拦截页 | **暂不接入** |
| Digital Typhoon (NII) | 历史 | ✅ HTML | 待补测 | HTML | 历史补充，非实时 |
| NCEP GEFS / UKMO / CMC 路径产品 | 补充集合 | 本轮未深测 | — | — | Phase 0.1 再测 |
| 上海本地潮位/预警 | 影响层 | 本轮未测 | — | — | Phase 0.2 |

---

## 1. 骨干源：ECMWF Open Data（热带气旋路径）

### 1.1 接口与访问方式

| 项 | 内容 |
|---|---|
| Python 包 | `ecmwf-opendata==0.3.34` |
| BUFR 解码 | `eccodes`（Homebrew 2.48.0 + Python binding 2.43.0） |
| 直连 | `https://data.ecmwf.int/forecasts/` |
| AWS 镜像 | `https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/` |
| Azure | `https://ai4edataeuwest.blob.core.windows.net/ecmwf`（匿名 list 返回 409 PublicAccessNotPermitted；客户端可生成 SAS） |
| 许可 | **CC BY 4.0**（下载时客户端打印条款；需署名 ECMWF） |
| 更新频率 | 00/06/12/18 UTC 起报；产品一般在起报后约 7–9 小时可用 |

### 1.2 实测请求参数（关键修正）

任务书与 PyPI 文档写 `step=240`（enfo 00/12）。**2026-08 实测文件名为 `360h`，`step=240` 对最新 cycle 返回 404。**

成功调用：

```python
from ecmwf.opendata import Client
client = Client(source="aws")  # 或 "ecmwf"
client.retrieve(
    date=-1,
    time=0,
    stream="enfo",
    type="tf",
    step=360,           # ← 实测成功；240 对 20260801 00z 失败
    target="ens_tf.bufr",
)
```

| 产品 | stream | model | 文件名样例（AWS） | Local | 大小样例 |
|---|---|---|---|---|---|
| IFS ENS 路径 | `enfo` | `ifs` | `.../enfo/20260801000000-360h-enfo-tf.bufr` | ✅ | 1,503,532 B |
| IFS 确定性 | `oper` | `ifs` | `.../oper/20260801000000-360h-oper-tf.bufr` | ✅ | 74,462 B |
| AIFS single | `oper` | `aifs-single` | `.../aifs-single/0p25/oper/...-360h-oper-tf.bufr` | ✅ | 49,276 B |
| AIFS ENS | `enfo` | `aifs-ens` | `.../aifs-ens/0p25/enfo/...-360h-enfo-tf.bufr` | ✅ | 1,391,500 B |

历史周期仍可能出现 `240h` 文件名（如 2025-10-10 实测存在 `240h-enfo-tf.bufr`）。**实现须按目录 listing / HEAD 探测实际 step，不可写死 240。**

### 1.3 真实返回样本

**原始字节头（`client_aws_enfo_step360.bufr`）**:

```
hex: 42 55 46 52 00 33 48 04 00 00 16 00 00 62 ...
ascii: BUFR.3H......b...
```

**eccodes 解码字段样例（2026-08-01 00z ENS，风暴 GENEVIEVE / 07E）**:

| 字段 | 值 |
|---|---|
| `longStormName` | `GENEVIEVE` |
| `stormIdentifier` | `07E` |
| `numberOfSubsets` | 51（集合成员） |
| `latitude`（前若干） | 22.4, 22.5, 22.3, … |
| `longitude` | -130.7, -130.8, … |
| `windSpeedAt10M` (m/s) | 22.6, 24.7, 21.6, … |
| `ensembleMemberNumber` | 1..51 |
| 缺失值 | `-1e+100`（eccodes 约定） |

> 说明：审计日全球有东太平洋系统，西北太平洋未必同时有命名台风；**有 TC 即出 `tf` 文件**。平静到全球无 TC 时，该文件可能缺失——属正常，不是故障。

**IFS oper 解码样例（2025-10-10，JERRY / 10L）**:  
`ensembleMemberNumber=52`（控制/确定性约定编号），含 lat/lon/风/气压时间序列。

样本目录: `data/samples/ecmwf/`

### 1.4 字段含义（业务用）

| 解码键 | 含义 | 备注 |
|---|---|---|
| stormIdentifier | 盆地+编号 | 如 07E、10L、WP 编号视命名 |
| longStormName | 英文名 | 可能有前导空格 |
| latitude/longitude | 路径点 | 度；集合为按成员/时次展开的数组 |
| windSpeedAt10M | 10 m 风速 | **m/s**；与 JMA/JTWC 时距不同，比较前须标注 |
| pressureReducedToMeanSeaLevel | 中心气压 | Pa 量级需再确认单位换算 |
| timePeriod | 时效 | 相对起报 |
| ensembleMemberNumber | 成员号 | ENS 1–50+；确定性常见 52 |

### 1.5 稳定性与定位

- **主源**: AWS 镜像优先（直连有 500 并发提示，本机两者均可）。  
- **备源**: `source="ecmwf"` 直连。  
- **Azure**: 保留为第三回退，走官方客户端 SAS，不手写匿名 URL。  
- **稳定性**: 高；开放数据产品线正式、许可清晰。  
- **注意**: 直连门户在高峰可能限流；实现「多源回退 + 指数退避」。

### 1.6 网络双环境

| 环境 | data.ecmwf.int | AWS S3 | Azure 匿名 |
|---|---|---|---|
| Local（本轮） | ✅ 门户 200；tf step=360 下载成功 | ✅ list + 下载 | ❌ 409 |
| GHA | **待补测** | **待补测** | **待补测** |

---

## 2. 官方机构预报

### 2.1 中央气象台（CMA / NMC）— 决策引用主源

| 项 | 内容 |
|---|---|
| 列表 | `http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default` |
| 单台详情 | `http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}` |
| 格式 | **JSONP**（非纯 JSON）：`typhoon_jsons_list_default(({...}))`（注意**双括号**） |
| Local | ✅ 200 |
| 许可 | 未明示开放许可；属业务网站接口，**仅内部研判引用**，页面须标注权威以市气象局/防汛为准 |
| 更新 | 活跃台风期间频繁更新 |

**列表真实返回头**:

```text
typhoon_jsons_list_default(({"typhoonList":[[3285913,"nameless","热带低压","20260015","20260015",20260015,null,"start"],[3279904,"DOLPHIN","白海豚","2613","2613",null,"生活在香港水域的中华白海豚，亦是香港的吉祥物","start"],[3277083,"NOUL","红霞","2612","2612",20260013,"红色的天空","stop"],...
```

**详情真实返回头（DOLPHIN / id=3279904）**:

```text
typhoon_jsons_view_3279904({"typhoon":[3279904,"DOLPHIN","白海豚",2613,2613,null,"...","start",[[3280037,"202607270600",1785132000000,"TS",176.9,13.2,998,18,"W",23,[["30KTS",250,150,150,250,3280037]],{"BABJ":[[12,"202607270600",174.4,13,990,23,"BABJ","TS"],[24,...,980,28,"BABJ","STS"],...
```

**字段解读（观测点 + BABJ 预报）**:

- 观测点: 时间、等级、经纬度、气压(hPa)、风速、移向移速、风圈  
- `BABJ`: 中央台预报时效(h)、位置、气压、风速、等级  
- **风速口径**: 中国业务常用 **2 分钟平均**（展示必须标注；与 JMA 10 分 / JTWC 1 分不可并列裸比）

样本: `data/samples/agencies/cma_list_full.jsonp`, `cma_view_*_*.jsonp`

| 环境 | 可达性 |
|---|---|
| Local | ✅ |
| GHA | **高风险不可达或改写**（境内源 + 境外 IP）；必须在 CI 单独测，失败则 CI 不依赖此源做回归 |

### 2.2 日本气象厅 RSMC Tokyo（JMA）

| URL 尝试 | 结果 |
|---|---|
| `.../bosai/typhoon/data/typhoon_info.json` | 404 |
| `.../bosai/typhoon/data/forecast.json` | 404 |
| `.../bosai/map.html#...typhoon` | 200 HTML 壳 |
| `.../bosai/forecast/data/forecast/010000.json` | 200（**一般天气预报**，非台风路径） |
| Digital Typhoon 年列表 | 200 HTML |

**结论**: 旧社区流传的 bosai 台风 JSON 路径本轮**全部 404**。页面可达但无已验证机器可读路径接口。  
**Phase 1 不编写 JMA 解析器**；列入 Phase 0.1 二次发现（浏览器抓包 / 官方开放数据目录），有样本后再准入。

### 2.3 美军 JTWC / ATCF

| 端点 | Local | 说明 |
|---|---|---|
| `www.metoc.navy.mil` | ❌ ReadTimeout | 官网不可达 |
| `www.nrlmry.navy.mil` | ❌ ReadTimeout | NRL ATCF 不可达 |
| `ftp.nhc.noaa.gov/atcf/aid_public/` | ✅ | **仅 AL/EP/CP**，**无 awp\* 西北太平洋** |
| `ftp.nhc.noaa.gov/atcf/archive/{year}/` | ✅ | 同样无 WP 前缀 |

**NHC a-deck 真实样本（大西洋，证明 ATCF 文法）** `aal012026.dat.gz` 解压后:

```text
AL, 01, 2026061518, 01, CARQ, -24, 218N,  998W,  15,    0, DB,  34, AAA,    0,    0,    0,    0, ...
AL, 01, 2026061518, 01, CARQ, -18, 235N, 1003W,  15,    0, DB,  34, AAA,    0, ...
```

**西北太平洋历史 a-deck / b-deck（UCAR，回算主来源）**:

- 目录: `https://hurricanes.ral.ucar.edu/repository/data/adecks_open/{year}/`  
- 样本: `awp062024.dat`（4.4 MB，明文 ATCF）

```text
WP, 06, 2024080700, 01, CARQ,   0, 251N, 1410E,  25, 1000, XX,  34, NEQ,    0, ...
```

- b-deck: `bdecks_open/2024/bwp012024.dat` → `BEST` 路径点  
- 风速口径: **JTWC 1 分钟平均**

| 环境 | JTWC 官网 | NHC ATCF | UCAR adecks |
|---|---|---|---|
| Local | ❌ | ✅（无 WP） | ✅ WP |
| GHA | 待补测 | 待补测 | 待补测 |

**定位**: 实时 JTWC 公报本轮无可靠机器入口 → **不编造爬虫**。回算与多模式历史用 **UCAR a-deck**；实时路径以 ECMWF + CMA 为主。

### 2.4 香港天文台（HKO）

| URL | 结果 |
|---|---|
| Open Data `warnsum` / `fnd` / `flw` | ✅ JSON |
| `tc_gis_info.xml` / `tc_gis.xml` | 404 |
| 台风主页 HTML | 200 |

**warnsum 样本**:

```json
{"WTS":{"name":"Thunderstorm Warning","code":"WTS","actionCode":"EXTEND","issueTime":"2026-08-02T19:00:00+08:00",...
```

**定位**: 开放数据适合本地天气背景；**多机构路径对比产品本轮未找到稳定 API**。路径层不依赖 HKO，直至二次审计拿到样本。

### 2.5 台湾 CWA / 韩国 KMA

- CWA 台风页、OpenData Swagger：HTML 200，未完成鉴权 API 实测 → **不接入**  
- KMA 台风通报页：HTML 200 → **不接入**

---

## 3. 补充源（已测部分）

### 3.1 IBTrACS（历史最佳路径）

- 目录: NCEI IBTrACS v04r01 CSV  
- `ibtracs.WP.list.v04r01.csv` Local ✅（约 114 MB）  
- `ibtracs.last3years.list.v04r01.csv` ✅  

**CSV 头**:

```text
SID,SEASON,NUMBER,BASIN,SUBBASIN,NAME,ISO_TIME,NATURE,LAT,LON,WMO_WIND,WMO_PRES,WMO_AGENCY,TRACK_TYPE,...
```

**定位**: 历史类比、校准标签辅助；**不是实时预报**。

### 3.2 CMA-STI 最佳路径数据集

- `https://tcdata.typhoon.org.cn/` → **HTTP 468**，响应为 SafeLine WAF 拦截页  
- **本轮删除接入计划**；改用 IBTrACS + UCAR b-deck

### 3.3 Digital Typhoon

- 年列表 HTML 可达；适合人工/半自动历史，非 Phase 1 实时管道

### 3.4 未测（明确标注，禁止假装已接入）

- NCEP GEFS / UKMO / CMC 热带气旋轨迹产品  
- 葵花九号 / 风云四 / ASCAT / CIMSS  
- TIGGE 历史集合  
- 上海：市气象台预警 API、吴淞/黄浦公园/芦潮港潮位、水务/太湖、天文潮  

→ 见 `docs/phase0-remaining.md` 清单，完成实测前不得写解析代码。

---

## 4. 实现约束（从审计直接导出）

1. **ECMWF `tf` step 自适应**: listing 或依次尝试 360/240/144，禁止写死文档旧值。  
2. **平静期**: 无 `tf` 文件 → UI 显示「当前无热带气旋路径产品」，**禁止**用历史路径填充。  
3. **CMA JSONP**: 必须剥双层括号再 `json.loads`；接口无官方 SLA，失败显式标红。  
4. **强度并列**: 配置 `wind_averaging`：`jtwc_1min` / `jma_10min` / `cma_2min` / `ecmwf_10m`；默认换算系数 `1min ≈ 10min × 1.14`，页面同时显示原文。  
5. **存档路径**: `archive/YYYYMMDD/HHz/{source}/` 原样字节（BUFR/JSONP/ATCF）。  
6. **双环境**: 每个 fetcher 健康检查区分 `local` / `ci`；CI 不对境内源失败判红（可 skip）。

---

## 5. 样本与脚本索引

| 路径 | 说明 |
|---|---|
| `scripts/audit/01_ecmwf_opendata.py` | ECMWF 多源 probe |
| `scripts/audit/02_agency_sources.py` | 机构端点 probe |
| `data/samples/ecmwf/*.bufr` | 真实 BUFR |
| `data/samples/ecmwf/bufr_download_decode.json` | 下载与解码报告 |
| `data/samples/agencies/cma_*.jsonp` | CMA 列表/详情 |
| `data/samples/agencies/ucar_*awp*` | WP a-deck 原文 |
| `data/samples/agencies/aal012026.dat.gz*` | NHC ATCF 样例 |
| `data/samples/agencies/ibtracs_*` | IBTrACS 头样本 |

---

## 6. Phase 0 结论（准入 / 暂缓）

### 立即准入 Phase 1

1. ECMWF Open Data `type=tf`（IFS ENS / oper / AIFS single / AIFS ENS）+ AWS 回退  
2. CMA `list_default` + `view_{id}`  
3. 预报原样存档管道  
4. UCAR a-deck/b-deck（回算支线，可与 Layer A 并行脚手架）  
5. IBTrACS WP（历史类比数据层）

### 暂缓（无合格样本或不可达）

- JMA 实时路径 API  
- JTWC 实时官网  
- HKO 多机构路径产品  
- CMA-STI 官网数据集  
- CWA/KMA 结构化路径  
- 本地潮位与市局预警 API  

### 必须补测

- **GitHub Actions 全表重跑** `01`/`02` 脚本，把 GHA 列从「待补测」改为实测结果。

---

*本报告满足任务书 §3：接口、双环境列、真实样本、字段、频率、许可、稳定性、主备定位。未完成双环境的源不得在 CI 中当作已验证。*
