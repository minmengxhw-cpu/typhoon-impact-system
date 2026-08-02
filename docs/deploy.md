# 部署说明（GitHub Pages）

## 页面地址

推送到 `main` 并启用 Pages 后：

`https://<owner>.github.io/typhoon-impact-system/`

静态根目录为仓库内 `web/`（workflow：`.github/workflows/pages.yml`）。

## 数据快照

- 运行时完整产品：`products/latest/`（gitignore，不进库）
- Pages 用精简快照：`web/data/*.json`

更新展示数据：

```bash
python3 -m src.model.run_layer_a          # 或指定 archive/...
python3 scripts/export_web_data.py
git add web/data && git commit -m "chore: refresh web data snapshot" && git push
```

## 本地预览

```bash
python3 -m http.server 8765
# http://127.0.0.1:8765/web/
```

## 免责

页面固定展示「内部研判参考，不构成气象预报」与「未校准」标识。  
权威信息以上海市气象局、上海市防汛指挥部发布为准。
