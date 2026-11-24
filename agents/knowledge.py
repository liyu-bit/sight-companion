"""C2：品类先验依据检索——过敏映射 + 保质期规则 + 营养数据，拼成可引用片段。"""

import json
from pathlib import Path

from agents.advise import evidence_for_category

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    return json.loads((ROOT / "knowledge" / name).read_text(encoding="utf-8"))


def _match(entries: list[dict], category: str) -> list[dict]:
    return [e for e in entries if e["category"] in category or category in e["category"]]


def evidence_for(category: str | None) -> str | None:
    if not category:
        return None
    parts = []
    allergen = evidence_for_category(category)
    if allergen:
        parts.append(allergen)
    for rule in _match(_load("shelf_life_rules.json")["rules"], category):
        parts.append(
            f"{rule['category']}保质期参考：{rule['typical_shelf_life']}；"
            f"储存：{rule['storage']}；开封后：{rule['opened']}。来源：{rule['source']}"
        )
    nutrition = _load("nutrition.json")
    for item in _match(nutrition["items"], category):
        parts.append(
            f"{item['category']}营养（每100g约值）：能量{item['energy_kcal']}千卡、"
            f"蛋白质{item['protein_g']}g、脂肪{item['fat_g']}g、碳水{item['carb_g']}g、"
            f"钙{item['calcium_mg']}mg。来源：{nutrition['source']}"
        )
    return "\n".join(parts) if parts else None