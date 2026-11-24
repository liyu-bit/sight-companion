# 看得见的同行者

> 2026 Qoder 高校创造力大赛 · Hack4Good 技术向善赛道

帮视障者独立逛一次超市。拍照拿起商品 → 认出是什么 → 核对过敏、保质期、价格 → 给出「建议 + 理由 + 选项」，**最终买不买由使用者自己决定**。

不读纸上的字，而是看眼前的商品；不替人拍板，只把关键信息讲清楚。

## 目录归属（只改自己的目录）

| 目录 | 负责人 | 内容 |
|---|---|---|
| `frontend/` | A | 手机端页面、拍照、语音交互、选项按钮 |
| `agents/recognize.py` | B-1 | 识物 Agent（含品类 `category`） |
| `agents/orchestrate.py` | B-1 | 编排 + TTS + `POST /api/advise` |
| `agents/advise.py` | B-2 | 购买建议 Agent（识别保质期/配料/价格，severity 双轨） |
| `knowledge/` | C 阶段 | 品类→过敏映射、保质期/营养数据 |
| `tests/` | 三人 | 假数据、商品测试集、准确率基线 |
| `docs/` | A | 契约与最终文档 |
| `config/.env` | B-1 | API Key，已被 `.gitignore` 排除，不进仓库 |

集成人是 **B-1**：A / B-2 按分支或 zip 交付，不直接改 main；跨目录改动先沟通。

## 快速启动

```bash
py -3.13 -m pip install -r requirements.txt
```

在 `config/.env` 放百炼 Key（可从 `config/.env.example` 复制）：

```
DASHSCOPE_API_KEY=sk-...
```

启动后端：

```bash
py -3.13 -m uvicorn agents.orchestrate:app --host 0.0.0.0 --port 8000
```

- 健康检查：`GET http://127.0.0.1:8000/api/health`
- `frontend/index.html` 存在时会自动挂到 `/`，与接口同域
- 后端细节见 `agents/README.md`
- Windows 统一用 `py -3.13`，默认 `python` 可能是其他虚拟环境

## 接口摘要

`POST /api/advise`，请求 `image` 为**纯 base64（不带 `data:image/` 前缀）**，`user.allergies` 每项带 `name` 与 `severity`（`mild` / `severe`）。

响应为 9 字段 `product / expiry_days / allergen_hit / price / advice / reason / options / user_decides / speech`，另加 `audio`（`data:audio/x-wav;base64,...`，可直接播放；TTS 失败为 `null`）。

红线：输出是建议不是判决，**不得出现 `verdict` / 能买 / 别买 / 必须买**；`user_decides` 恒为 `true`。

契约以 **`docs/契约.md`** 为准（2026-11-22 新版，含 `severity` 与风险双轨制）。

## 当前状态

- 端到端已跑通：前端拍照 → `POST /api/advise` → 9 字段渲染 → 播放后端合成的语音
- 识物、建议、编排、TTS 均为真实实现（B-1 / B-2）
- 编排已改为「先识物、后建议」：识物品类作为 `evidence` 传给建议 Agent，`product` 为空时用识物结果兜底；冒烟实测热态约 8.1 秒、慢时约 18.5 秒（含 TTS），演示前务必预热
- 演示前端请求超时应设为 30 秒（冷启动较慢）；识物超时可通过 `RECOGNIZE_TIMEOUT` 调整
- 真实商品测试集：`tests/ground_truth.json` 与 17 张商品照（B-2），准确率评测入口 `tests/eval.py`
- 识物基线 `tests/recognition_baseline.json` 已用 22 条清单重跑（17 张真实图 + 5 张 AI 占位图，品类 22/22）；评测入口 `tests/eval_recognize.py`
- B-1 编排单测：`py -3.13 -m unittest discover -s tests -p "test_orchestrate.py" -v`
- 部署说明见 `agents/README.md`；`config/.env` 不进镜像和仓库
