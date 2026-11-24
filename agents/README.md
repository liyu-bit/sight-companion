# agents/（B-1 负责）

识物 Agent、编排接口、TTS。契约以 `docs/契约.md` 为准。

## 环境

```bash
py -3.13 -m pip install -r requirements.txt
```

Key 放 `config/.env`（已被 .gitignore 排除，不进仓库）：

```
DASHSCOPE_API_KEY=sk-...
```

## 启动

```bash
py -3.13 -m uvicorn agents.orchestrate:app --host 0.0.0.0 --port 8000
```

- 健康检查：`GET http://<IP>:8000/api/health`
- `frontend/index.html` 存在时会自动挂到 `/`，与接口同域
- Windows 上统一用 `py -3.13`：默认 `python` 可能是别的虚拟环境

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

基线记录见 `tests/recognition_baseline.json`。

## 当前状态

- `agents/advise.py` 已是 B-2 的真实实现：读生产日期/保质期、配料表、价格，按 `severity` 双轨给建议；`ingredients_read` 为模型自报字段，severe 轨的 fail-safe 仍在收紧
- 识物与建议在 `orchestrate.py` 中并发执行，热态单次约 7 秒、冷启动约 19 秒，前端请求超时建议 30 秒
- 识物失败只记日志、继续走建议；`advise` 抛错返回 502。severe 场景的「第一次就显示求助按钮」需要前端配合，待统一
