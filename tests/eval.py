"""准确率评测：分别跑识物（recognize）和建议（advise），对比 ground_truth.json。

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
from agents.recognize import recognize

ADVISE_FIELDS = ["product", "price", "expiry_days", "allergen_hit"]
RECOGNIZE_FIELDS = ["product", "category"]


def match_contains(pred, gt) -> bool:
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


def match_equal(pred, gt) -> bool:
    return pred == gt


MATCHERS = {
    "product": match_contains,
    "category": match_contains,
    "price": match_price,
    "expiry_days": match_expiry,
    "allergen_hit": match_equal,
}


def print_table(title, fields, rows, correct, n):
    print(f"### {title}")
    print()
    print("| 图片 | " + " | ".join(fields) + " |")
    print("|---|" + "---|" * len(fields))
    for name, marks in rows:
        cells = " | ".join("✓" if marks[f] else "✗" for f in fields)
        print(f"| {name} | {cells} |")
    cells = " | ".join(f"{correct[f]}/{n} ({correct[f] / n:.1%})" for f in fields)
    print(f"| **总正确率** | {cells} |")
    print()


def main():
    records = json.loads((ROOT / "tests" / "ground_truth.json").read_text(encoding="utf-8"))
    n = len(records)
    rec_rows, adv_rows = [], []
    rec_correct = {f: 0 for f in RECOGNIZE_FIELDS}
    adv_correct = {f: 0 for f in ADVISE_FIELDS}
    errors = []

    for rec in records:
        b64 = base64.b64encode((ROOT / "tests" / rec["image"]).read_bytes()).decode("ascii")

        try:
            seen = recognize(b64)
        except Exception as e:
            seen = {}
            errors.append(f"{rec['image']}: recognize 调用失败：{e}")
        marks = {}
        for f in RECOGNIZE_FIELDS:
            ok = MATCHERS[f](seen.get(f), rec.get(f))
            marks[f] = ok
            rec_correct[f] += ok
        rec_rows.append((rec["image"], marks))

        try:
            pred = advise(b64, rec["user"])
        except Exception as e:
            pred = {}
            errors.append(f"{rec['image']}: advise 调用失败：{e}")
        marks = {}
        for f in ADVISE_FIELDS:
            ok = MATCHERS[f](pred.get(f), rec[f])
            marks[f] = ok
            adv_correct[f] += ok
        adv_rows.append((rec["image"], marks))

        print(f"{rec['image']} 完成", flush=True)

    print()
    print_table("识物 Agent（recognize）", RECOGNIZE_FIELDS, rec_rows, rec_correct, n)
    print_table("购买建议 Agent（advise）", ADVISE_FIELDS, adv_rows, adv_correct, n)

    if errors:
        for e in errors:
            print(f"- {e}")


if __name__ == "__main__":
    main()