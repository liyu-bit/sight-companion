"""购买建议 Agent（临时 stub）：B-2 交付真实实现后整体替换本文件。"""

import json
from pathlib import Path

MOCK = Path(__file__).resolve().parent.parent / "tests" / "mock_advise.json"

_FALLBACK = {
    "product": "某品牌纯牛奶",
    "expiry_days": 3,
    "allergen_hit": "乳糖",
    "price": "5.00",
    "advice": "建议换一盒",
    "reason": "还有 3 天过期，且你乳糖不耐受",
    "options": ["换一盒无乳糖牛奶", "这盒尽快喝完"],
    "user_decides": True,
    "speech": "这盒牛奶还有 3 天过期，你乳糖不耐受，建议换一盒；要不要听听别的选项？",
}


def advise(image_base64: str, user: dict) -> dict:
    if MOCK.exists():
        return json.loads(MOCK.read_text(encoding="utf-8"))["有风险"]
    return dict(_FALLBACK)
