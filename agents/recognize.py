"""识物 Agent：看商品图片，返回商品名、品类、置信度。"""

import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = os.getenv("VISION_MODEL", "qwen3-vl-plus")

PROMPT = """你是视障人士购物助手的识物 Agent。看图片判断这是超市或便利店里的什么商品。

要求：
1. 即使商品没有标签、标签被撕掉或磨花、是散装商品，也要根据包装形状、颜色、材质、典型外观尽力判断，不要因为看不清文字就拒答。
2. category 必须是常见品类，例如：纯牛奶、酸奶、花生酱、坚果、调味品、零食、饮料、面包、火腿肠、食用油、挂面、方便面、水果、蔬菜、日用品。该字段用于后续过敏判断，务必尽量准确。
3. 如果完全无法判断，product 填「未识别」，category 填「未知」，confidence 填 0。

只输出 JSON，不要输出任何其他文字：
{"product": "商品名", "category": "品类", "confidence": 0.0}
"""


def _client() -> OpenAI:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("config/.env 中缺少 DASHSCOPE_API_KEY")
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"模型没有返回 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


def _normalize(data: dict) -> dict:
    confidence = data.get("confidence", 0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "product": str(data.get("product") or "未识别"),
        "category": str(data.get("category") or "未知"),
        "confidence": round(confidence, 2),
    }


def recognize(image_base64: str, mime: str = "image/jpeg") -> dict:
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_base64}"},
                    },
                ],
            }
        ],
        temperature=0,
    )
    return _normalize(_parse_json(response.choices[0].message.content or ""))


def recognize_file(path: str | Path) -> dict:
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return recognize(base64.b64encode(path.read_bytes()).decode(), mime)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python agents/recognize.py <图片路径>")
        sys.exit(1)
    print(json.dumps(recognize_file(sys.argv[1]), ensure_ascii=False, indent=2))
