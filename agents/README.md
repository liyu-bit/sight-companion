# agents/（B-1 负责）

识物 Agent、编排接口、TTS。契约以 `docs/契约.md` 为准。

## 环境

```bash
py -3.13 -m pip install -r requirements.txt
```

Key 放 `config/.env`（已被 .gitignore 排除，不进仓库）：

```bash
cp config/.env.example config/.env
# 编辑 config/.env，填入 DASHSCOPE_API_KEY
```

可选环境变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `VISION_MODEL` | `qwen3-vl-plus` | 识物模型 |
| `TTS_MODEL` / `TTS_VOICE` | `qwen3-tts-flash` / `Cherry` | 语音合成 |
| `PORT` | `8000` | 监听端口 |
| `RECOGNIZE_TIMEOUT` | `20` | 识物超时秒数，超时则跳过品类先验继续建议 |
| `ALLOW_ORIGINS` | `*` | CORS 允许来源，逗号分隔；上线时建议收紧 |

## 启动

```bash
py -3.13 -m uvicorn agents.orchestrate:app --host 0.0.0.0 --port 8000
```

- 健康检查：`GET http://<IP>:8000/api/health`
- `frontend/index.html` 存在时会自动挂到 `/`，与接口同域
- Windows 上统一用 `py -3.13`：默认 `python` 可能是别的虚拟环境

## 编排流程

1. 识物 Agent 返回 `product / category / confidence`；
2. 把品类先验作为 `evidence` 传给 `advise(image, user, evidence=...)`（契约 3.5 节）；
3. `advise` 返回 9 字段建议，`product` 为空时用识物结果兜底；
4. `speech` 调 TTS，失败返回 `audio: null`，前端降级到浏览器语音。

识物失败/超时只记日志，不阻塞建议；建议失败返回 502。

## 接口

`POST /api/advise`

```json
{
  "image": "<纯 base64，不带 data:image/ 前缀>",
  "user": {"allergies": [{"name": "乳糖", "severity": "mild"}]}
}
```

响应 = 契约 9 字段 + `audio`：

- `product / expiry_days / allergen_hit / price / advice / reason / options / user_decides / speech`
- `audio`：`data:audio/x-wav;base64,...`，可直接给 `<audio src>` 播放；TTS 失败时为 `null`
- 错误码：`image` 为空 400、缺字段 422、图片超过约 4.5MB 返回 413、建议生成失败 502

红线：输出是建议不是判决，不得出现 `verdict` / 能买 / 别买 / 必须买；`user_decides` 恒为 `true`。

## 冒烟检查

服务启动后运行，校验 9 字段、音频、红线词与错误码：

```bash
py -3.13 tests/smoke_api.py
# 可指定地址：py -3.13 tests/smoke_api.py https://<隧道地址>
```

## 识物单测

```bash
py -3.13 agents/recognize.py tests/samples/milk_torn.png
# {"product": "纯牛奶", "category": "纯牛奶", "confidence": 0.95}
```

基线记录见 `tests/recognition_baseline.json`；最近一次 22 条品类 22/22、商品 21/22（唯一未中是「风味酸牛奶」与关键词「酸奶」的子串匹配问题，实际识别正确）。

## 识物基线评测

清单 `tests/recognition_set.json` 已整理 22 条（17 张真实商品图 + 5 张样例图），逐张调用识物并统计品类/商品命中：

```bash
py -3.13 tests/eval_recognize.py --limit 1   # 先跑 1 张验证
py -3.13 tests/eval_recognize.py             # 全量，会产生视觉调用费用
py -3.13 tests/eval_recognize.py --write     # 覆盖 recognition_baseline.json
```

其中 5 张困难样本仍是 AI 占位图，C4 阶段需替换成真实「撕标/磨花/散装」照片后重跑。

## 编排单测（不调用模型）

```bash
py -3.13 -m unittest discover -s tests -p "test_orchestrate.py" -v
```

覆盖：品类先验传入 `advise`、识物失败降级、`product` 兜底、400/413/502。

## 部署

相机和麦克风要求 HTTPS（`localhost` 除外），所以公开演示必须是 HTTPS 地址。

- 同域部署：FastAPI 同时托管 `frontend/`，前端用相对路径 `/api/advise`，无跨域问题。
- 分离部署：后端单独跑，前端改成后端绝对地址，并把 `ALLOW_ORIGINS` 设为前端域名。
- Docker：

```bash
docker build -t sight-companion .
docker run --rm -p 8000:8000 -e DASHSCOPE_API_KEY=sk-... sight-companion
```

`config/.env` 不会进镜像（`.dockerignore` 已排除），运行时用环境变量注入 Key。

**Qoder Sites 注意**：当前仓库没有 Sites 配置。如果 Sites 只能托管静态页，公开链接里的 `/api/advise` 会打不到 FastAPI，前端会走离线假数据；上线前必须确认 Sites 能否托管这个 Python 服务，或改用「Sites 页面 + 独立后端」并把前端接口地址改成绝对地址。

## 当前状态

- `agents/advise.py` 已是 B-2 的真实实现：读生产日期/保质期、配料表、价格，按 `severity` 双轨给建议；`ingredients_read` 为模型自报字段，severe 轨的 fail-safe 仍在收紧
- `orchestrate.py` 已从并发改为「先识物、后建议」：识物品类作为 `evidence` 传给建议，`product` 为空时用识物结果兜底；冒烟实测热态约 8.1 秒、慢时约 18.5 秒（含 TTS），演示前务必预热
- 识物失败/超时只记日志、继续走建议；`advise` 抛错返回 502。severe 场景的「第一次就显示求助按钮」需要前端配合，待统一
