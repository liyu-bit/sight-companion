"""购买建议 Agent 准确率评测：ground_truth.json vs advise() 输出。

运行：PYTHONIOENCODING=utf-8 python tests/eval.py
"""

import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# httpx 解析 NO_PROXY 里的裸 IPv6 条目（::1 / [::1]）会抛 InvalidURL，先剔除
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = ",".join(
        e for e in os.environ["NO_PROXY"].split(",") if e.strip() not in ("::1", "[::1]")
    )

from agents.advise import advise

FIELDS = ["product", "price", "expiry_days", "allergen_hit"]


def match_product(pred, gt) -> bool:
    if gt is None or pred is None:
        return gt is None and pred is None
    return gt in pred or pred in gt


def match_price(pred, gt) -> bool:
    if gt is None or pred is None:
        return gt is None and pred is None
    try:
        return abs(float(pred) - float(gt)) <= 0.01 + 1e-9
    except (TypeError, ValueError):
        return False


def match_expiry(pred, gt) -> bool:
    if gt is None or pred is None:
        return gt is None and pred is None
    return abs(int(pred) - int(gt)) <= 3


def match_allergen(pred, gt) -> bool:
    return pred == gt


MATCHERS = {
    "product": match_product,
    "price": match_price,
    "expiry_days": match_expiry,
    "allergen_hit": match_allergen,
}


def main():
    records = json.loads((ROOT / "tests" / "ground_truth.json").read_text(encoding="utf-8"))
    rows = []
    correct = {f: 0 for f in FIELDS}
    errors = []

    for rec in records:
        b64 = base64.b64encode((ROOT / "tests" / rec["image"]).read_bytes()).decode("ascii")
        try:
            pred = advise(b64, rec["user"])
        except Exception as e:
            pred = {}
            errors.append(f"{rec['image']}: advise 调用失败：{e}")
        marks = {}
        for f in FIELDS:
            ok = MATCHERS[f](pred.get(f), rec[f])
            marks[f] = ok
            correct[f] += ok
        rows.append((rec["image"], marks))
        print(f"{rec['image']} 完成", flush=True)

    n = len(records)
    print()
    print("| 图片 | product | price | expiry_days | allergen_hit |")
    print("|---|---|---|---|---|")
    for name, marks in rows:
        cells = " | ".join("✓" if marks[f] else "✗" for f in FIELDS)
        print(f"| {name} | {cells} |")
    cells = " | ".join(f"{correct[f]}/{n} ({correct[f] / n:.1%})" for f in FIELDS)
    print(f"| **总正确率** | {cells} |")

    if errors:
        print()
        for e in errors:
            print(f"- {e}")


if __name__ == "__main__":
    main()