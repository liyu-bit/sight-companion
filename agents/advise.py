"""购买建议 Agent（B-2）。

契约见 docs/契约.md：
- 签名 advise(image_base64, user) -> dict，返回固定 9 字段 JSON（不含 audio）。
- 只给建议 + 理由 + 选项，user_decides 恒为 True，绝不替用户拍板。
- 双轨制（3.5 节）：mild 用品类先验（本地映射表），severe 必须读配料表，
  读不到或品类先验与模型结论冲突时绝不判「安全」。
"""

import base64
import json
import os
import sys
from datetime import date
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-vl-plus"
NEAR_EXPIRY_DAYS = 7

# httpx 解析 NO_PROXY 里的裸 IPv6 条目（::1 / [::1]）会抛 InvalidURL，先剔除
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = ",".join(
        e for e in os.environ["NO_PROXY"].split(",") if e.strip() not in ("::1", "[::1]")
    )

SYSTEM_PROMPT = """你是视障用户的购物助手，负责从商品照片里读信息，只做识别，不做决定。
从图片中完成四件事：
1. 读保质期喷码或标签：找「保质期至/到期日」直接换算剩余天数；若只有「生产日期 + 保质期 X 天/月」，据此推算到期日再换算剩余天数。看不清就填 null，不要猜。
2. 读配料表，对照用户档案里的过敏/不耐受清单判断有无命中（例如：用户乳糖不耐受，配料是生牛乳/纯牛奶，则命中「乳糖」）。没有命中填 null。
   - 档案中 severity 为 severe 的是致命过敏（如花生/坚果/甲壳类/鱼类/牛奶蛋白/鸡蛋/大豆/小麦），必须逐字核对配料表，包括「可能含微量/交叉污染」提示，绝不可漏判。
   - severity 为 mild 的是非致命不耐受（如乳糖），可按品类常识判断（纯牛奶/酸奶含乳糖）。
   - 豆乳/植物奶是大豆制品，不含乳糖，不要误判为乳制品。
   - 无论是否命中，都要如实报告 ingredients_read：配料表在照片里是否读清（读不到/读不清一律 false）。
3. 读价签上的价格，保留两位小数字符串，如 "5.00"。看不清填 null。
4. 判断品类 category（如纯牛奶/酸奶/奶酪/饼干/坚果/薯片/饮用水），判断不了填 null。

只输出一个 JSON 对象，不要输出任何其他文字或 markdown 代码块：
{
  "product": "商品名称，看不清填 null",
  "category": "品类" 或 null,
  "expiry_days": 整数剩余天数（已过期为负数）或 null,
  "allergen_hit": "命中的过敏原名称" 或 null,
  "price": "5.00" 或 null,
  "ingredients_read": true 或 false
}"""


def _load_allergen_map() -> list[dict]:
    path = ROOT / "knowledge" / "allergen_map.json"
    return json.loads(path.read_text(encoding="utf-8"))["categories"]


ALLERGEN_MAP = _load_allergen_map()


def _category_allergens(category: str | None) -> list[dict]:
    if not category:
        return []
    hits = []
    for entry in ALLERGEN_MAP:
        cat = entry["category"]
        if cat in category or category in cat:
            hits.extend(entry["allergens"])
    return hits


def evidence_for_category(category: str | None) -> str | None:
    """按品类检索映射表，返回可引用的依据片段（含权威来源）。"""
    if not category:
        return None
    lines = []
    for entry in ALLERGEN_MAP:
        cat = entry["category"]
        if cat in category or category in cat:
            names = "、".join(
                f"{a['name']}（{'非致命不耐受' if a['severity'] == 'mild' else '致命过敏'}）"
                for a in entry["allergens"]
            )
            lines.append(f"{cat}含{names}。来源：{entry['source']}")
    return "\n".join(lines) if lines else None


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
    env_path = ROOT / "config" / ".env"
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
        text += f"\n参考依据片段（权威数据，判断过敏时优先采信）：\n{evidence}"

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


def _build_advice(
    product: str | None, category: str | None, expiry_days: int | None, allergen_hit: str | None
) -> dict:
    if expiry_days is None and allergen_hit is None:
        return {
            "advice": "建议让家人帮你看一眼",
            "reason": "没看清",
            "options": ["拍照发给家人", "再拍一次"],
            "speech": "没看清，建议让家人帮你看一眼，你自己定。",
        }

    near = expiry_days is not None and expiry_days <= NEAR_EXPIRY_DAYS
    if not near and allergen_hit is None:
        return {
            "advice": "可以考虑这盒",
            "reason": "没看到过敏或过期风险",
            "options": ["就选这盒", "再看看别的"],
            "speech": "没看到过敏或过期风险，可以考虑这盒，最终你决定。",
        }

    if allergen_hit == "乳糖":
        allergen_part = "你乳糖不耐受"
        options = ["换一盒无乳糖牛奶", "再看看别的"]
    elif allergen_hit:
        allergen_part = f"配料里有{allergen_hit}"
        options = ["换一件同类商品", "让家人帮你看一眼"]
    else:
        allergen_part = None
        options = ["换一盒新鲜的", "再看看别的"]

    if near:
        # 第二幕：俯拍顶部读到了日期
        expiry_part = f"还有 {expiry_days} 天过期" if expiry_days >= 0 else "已经过期"
        reason = "，且".join([expiry_part] + ([allergen_part] if allergen_part else []))
        if allergen_part:
            speech = f"这盒{expiry_part}，{allergen_part}，建议换一盒；要不要听听别的选项？"
        else:
            speech = f"这盒{expiry_part}，建议换一盒，要不要听听别的选项？"
    else:
        # 第一幕：正面照，品类先验命中，没读到日期
        cat = category or product or "商品"
        if allergen_hit == "乳糖":
            reason = f"{cat}含乳糖，你乳糖不耐受"
            speech = f"这是{cat}，你乳糖不耐受，建议换一盒无乳糖的。"
        else:
            reason = f"{cat}通常含{allergen_hit}"
            speech = f"这是{cat}，通常含{allergen_hit}，建议换一件别的；要不要听听别的选项？"
            options = ["换一件同类商品", "再看看别的"]
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


def _severe_recheck_advice(names: list[str], category: str | None) -> dict:
    """模型自报读清且未命中，但品类先验认为该含致命过敏原——不信模型，请用户再核对。"""
    joined = "、".join(names)
    cat = category or "这类商品"
    return {
        "advice": "建议再核对一下配料表",
        "reason": f"配料表未见{joined}，但{cat}通常含{joined}，请你再核对",
        "options": ["拍照发给家人", "再拍一次配料表"],
        "speech": f"配料表没看到{joined}，但{cat}通常含{joined}，请你再核对一下，也可以拍照发给家人确认。",
    }


def advise(image_base64: str, user: dict, evidence: str | None = None) -> dict:
    """返回契约规定的 9 字段 JSON（不含 audio）。image_base64 为纯 base64，无前缀。"""
    allergies = _normalize_allergies(user)
    profile = {**user, "allergies": allergies}
    client = OpenAI(api_key=_load_api_key(), base_url=BASE_URL)
    seen = _perceive(client, image_base64, profile, evidence)

    product = seen.get("product")
    category = seen.get("category")
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

    mild_names = {a["name"] for a in allergies if a["severity"] == "mild"}
    severe_names = [a["name"] for a in allergies if a["severity"] == "severe"]
    prior = _category_allergens(category)

    # mild 轨：品类先验本地判定，不依赖配料表
    if allergen_hit is None:
        for a in prior:
            if a["severity"] == "mild" and a["name"] in mild_names:
                allergen_hit = a["name"]
                break

    prior_severe = [
        a["name"] for a in prior if a["severity"] == "severe" and a["name"] in severe_names
    ]
    if severe_names and not seen.get("ingredients_read"):
        suggested = _severe_help_advice(severe_names)
    elif severe_names and prior_severe and allergen_hit not in prior_severe:
        suggested = _severe_recheck_advice(prior_severe, category)
    else:
        suggested = _build_advice(product, category, expiry_days, allergen_hit)

    if severe_names and "拍照发给家人" not in suggested["options"]:
        suggested["options"].append("拍照发给家人")

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