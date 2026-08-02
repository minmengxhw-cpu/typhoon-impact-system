# Phase 0 剩余实测清单

完成前不得编写对应解析代码（任务书硬约束 §1.1）。

> **2026-08-02 更新**：Phase 0 主干（ECMWF tf + CMA + UCAR 回算样本）已完成，Layer A 已可基于上述源运行。下列项为补测/扩展，不阻塞 Layer A 骨架。

## 双环境补测

- [ ] 在 GitHub Actions 运行 `scripts/audit/01_ecmwf_opendata.py`
- [ ] 在 GitHub Actions 运行 `scripts/audit/02_agency_sources.py`
- [ ] 将结果填回 `data-sources-audit.md` 的 GHA 列

## 机构路径二次发现

- [ ] JMA：浏览器/抓包确认当前 bosai 台风数据 URL，粘贴真实 JSON 样本
- [ ] HKO：多机构路径对比是否有 open data 或 XML/JSON
- [ ] JTWC：寻找上海可达的实时 TC 公报/ATCF 镜像（NRL 本轮超时）
- [ ] CWA 侵袭机率 API（需 token 则记录申请流程，无样本不接入）

## 补充源

- [ ] NCEP GEFS TC track（若有）
- [ ] UKMO / CMC 开放路径
- [ ] TIGGE 历史集合访问方式
- [ ] CIMSS 环境场与 ADT

## 上海本地

- [ ] 市气象台预警查询接口或可归档页面
- [ ] 吴淞 / 黄浦公园 / 芦潮港潮位
- [ ] 市水务/防汛、太湖流域
- [ ] 天文潮预报源
- [ ] 停课停工通告来源（标签库）
