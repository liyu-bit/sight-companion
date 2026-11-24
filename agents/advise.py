"""购买建议 Agent（B-2）。

契约见 docs/契约.md：
- 签名 advise(image_base64, user) -> dict，返回固定 9 字段 JSON（不含 audio）。
- 只给建议 + 理由 + 选项，user_decides 恒为 True，绝不替用户拍板。
"""

import base64
import json
import os
import sys
from datetime import date
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-vl-plus"
NEAR_EXPIRY_DAYS = 7

SYSTEM_PROMPT = """你是视障用户的购物助手，负责从商品照片里读信息，只做识别，不做决定。
从图片中完成三件事：
1. 读保质期喷码或标签：找「保质期至/到期日」直接换算剩余天数；若只有「生产日期 + 保质期 X 天/月」，据此推算到期日再换算剩余天数。看不清就填 null，不要猜。
2. 读配料表，对照用户档案里的过敏/不耐受清单判断有无命中（例如：用户乳糖不耐受，配料是生牛乳/纯牛奶，则命中「乳糖」）。没有命中填 null。
   - 档案中 severity 为 severe 的是致命过敏（如花生/坚果/甲壳类/鱼类/牛奶蛋白/鸡蛋/大豆/小麦），必须逐字核对配料表，包括「可能含微量/交叉污染」提示，绝不可漏判。
   - severity 为 mild 的是非致命不耐受（如乳糖），可按品类常识判断（纯牛奶/酸奶含乳糖）。
   - 无论是否命中，都要如实报告 ingredients_read：配料表在照片里是否读清（读不到/读不清一律 false）。
3. 读价签上的价格，保留两位小数字符串，如 "5.00"。看不清填 null。

只输出一个 JSON 对象，不要输出任何其他文字或 markdown 代码块：
{
  "product": "商品名称，看不清填 null",
  "expiry_days": 整数剩余天数（已过期为负数）或 null,
  "allergen_hit": "命中的过敏原名称" 或 null,
  "price": "5.00" 或 null,
  "ingredients_read": true 或 false
}"""


def _normalize_allergies(user: dict) -> list[dict]:
    """兼容旧字符串数组："乳糖" 按 mild 处理。"""
    normalized = []
    for item in user.get("allergies") or []:
        if isinstance(item, str):
            normalized.append({"name": item, "severity": "mild"})
        else:
            normalized.append(
                {"name": item["name"], "severity": item.get("severity", "mild")}
            )
    return normalized


def _load_api_key() -> str:
    env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DASHSCOPE_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置（config/.env）")
    return key


def _perceive(client: OpenAI, image_base64: str, user: dict, evidence: str | None) -> dict:
    today = date.today().isoformat()
    text = (
        f"今天是 {today}，按今天计算剩余保质期天数。\n"
        f"用户档案：{json.dumps(user, ensure_ascii=False)}"
    )
    if evidence:
        text += f"\n参考依据片段（仅作识别参考）：\n{evidence}"

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                    },
                    {"type": "text", "text": text},
                ],
            },
        ],
        temperature=0,
    )
    raw = resp.choices[0].message.content.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"模型返回无法解析为 JSON：{e}\n原文：{raw}") from e


def _build_advice(product: str | None, expiry_days: int | None, allergen_hit: str | None) -> dict:
    if expiry_days is None and allergen_hit is None:
        return {
            "advice": "建议让家人帮你看一眼",
            "reason": "没看清保质期",
            "options": ["拍照发给家人", "再拍一次"],
            "speech": "没看清保质期，建议让家人帮你看一眼，你自己定。",
        }

    risky = allergen_hit is not None or expiry_days <= NEAR_EXPIRY_DAYS
    if not risky:
        return {
            "advice": "可以考虑这盒",
            "reason": "没看到过期或过敏风险",
            "options": ["就选这盒", "再看看别的"],
            "speech": "没看到过期或过敏风险，可以考虑这盒，最终你决定。",
        }

    parts = []
    if expiry_days is not None and expiry_days <= NEAR_EXPIRY_DAYS:
        parts.append(f"还有 {expiry_days} 天过期" if expiry_days >= 0 else "已经过期")
    if allergen_hit == "乳糖":
        parts.append("你乳糖不耐受")
    elif allergen_hit:
        parts.append(f"配料里有{allergen_hit}")
    reason = "，且".join(parts)

    if allergen_hit == "乳糖":
        options = ["换一盒无乳糖牛奶", "这盒尽快喝完"]
    else:
        options = ["换一件同类商品", "让家人帮你看一眼"]

    noun = "牛奶" if product and "奶" in product else "商品"
    speech = f"这盒{noun}{reason.replace('，且', '，')}，建议换一盒；要不要听听别的选项？"
    return {
        "advice": "建议换一盒",
        "reason": reason,
        "options": options,
        "speech": speech,
    }


def _severe_help_advice(severe_names: list[str]) -> dict:
    names = "、".join(severe_names)
    return {
        "advice": "建议让家人帮你看一眼",
        "reason": f"没看清配料表，无法确认是否含{names}",
        "options": ["拍照发给家人", "再拍一次"],
        "speech": f"没看清配料表，你对{names}过敏，建议让家人帮你看一眼，你自己定。",
    }


def advise(image_base64: str, user: dict, evidence: str | None = None) -> dict:
    """返回契约规定的 9 字段 JSON（不含 audio）。image_base64 为纯 base64，无前缀。"""
    allergies = _normalize_allergies(user)
    profile = {**user, "allergies": allergies}
    client = OpenAI(api_key=_load_api_key(), base_url=BASE_URL)
    seen = _perceive(client, image_base64, profile, evidence)

    product = seen.get("product")
    expiry_days = seen.get("expiry_days")
    allergen_hit = seen.get("allergen_hit")
    price = seen.get("price")

    if expiry_days is not None:
        expiry_days = int(expiry_days)
    if price is not None:
        try:
            price = f"{float(price):.2f}"
        except (TypeError, ValueError):
            price = None

    severe_names = [a["name"] for a in allergies if a["severity"] == "severe"]
    if severe_names and not seen.get("ingredients_read"):
        suggested = _severe_help_advice(severe_names)
    else:
        suggested = _build_advice(product, expiry_days, allergen_hit)
    return {
        "product": product,
        "expiry_days": expiry_days,
        "allergen_hit": allergen_hit,
        "price": price,
        "advice": suggested["advice"],
        "reason": suggested["reason"],
        "options": suggested["options"],
        "user_decides": True,
        "speech": suggested["speech"],
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python agents/advise.py <图片路径>")
        sys.exit(1)
    image_base64 = base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii")
    result = advise(image_base64, {"allergies": [{"name": "乳糖", "severity": "mild"}]})
    print(json.dumps(result, ensure_ascii=False, indent=2))