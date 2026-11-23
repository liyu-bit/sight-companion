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

红线：输出是建议不是判决，不得出现 `verdict` / 能买 / 别买 / 必须买；`user_decides` 恒为 `true`。

## 识物单测

```bash
py -3.13 agents/recognize.py tests/samples/milk_torn.png
# {"product": "纯牛奶", "category": "纯牛奶", "confidence": 0.95}
```

基线记录见 `tests/recognition_baseline.json`。

## 当前状态

- `agents/advise.py` 是**临时 stub**（返回 `tests/mock_advise.json` 的有风险样例），B-2 交付真实实现后整体替换
- `agents/orchestrate.py` 中识物失败只记日志、继续走建议；失败/求助的对外信号待 C 阶段接口定稿
