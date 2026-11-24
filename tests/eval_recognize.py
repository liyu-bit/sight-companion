"""识物基线评测：读 tests/recognition_set.json，逐张跑识物 Agent，统计品类/商品命中。

运行：
  py -3.13 tests/eval_recognize.py             # 全量
  py -3.13 tests/eval_recognize.py --limit 1   # 只跑前 1 张，先验证脚本
  py -3.13 tests/eval_recognize.py --write     # 覆盖 tests/recognition_baseline.json

注意：每张图会真实调用一次 qwen3-vl-plus，会产生费用。
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# httpx 解析 NO_PROXY 里的裸 IPv6 条目（::1 / [::1]）会抛 InvalidURL，先剔除
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = ",".join(
        e for e in os.environ["NO_PROXY"].split(",") if e.strip() not in ("::1", "[::1]")
    )

from agents.recognize import recognize_file

MANIFEST = ROOT / "tests" / "recognition_set.json"
BASELINE = ROOT / "tests" / "recognition_baseline.json"


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def category_hit(pred: str | None, expected: list[str]) -> bool:
    pred = (pred or "").strip()
    if not pred or pred == "未知":
        return False
    return any(e and (e in pred or pred in e) for e in expected)


def product_hit(pred: str | None, expected: list[str]) -> bool | None:
    if not expected:
        return None
    pred = (pred or "").strip()
    return any(e and e in pred for e in expected)


def arg_value(name: str) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    records = json.loads(MANIFEST.read_text(encoding="utf-8"))
    limit = arg_value("--limit")
    if limit is not None:
        records = records[: int(limit)]

    rows = []
    category_ok = product_ok = product_checked = 0
    hard_total = hard_ok = 0

    for i, rec in enumerate(records, 1):
        path = ROOT / "tests" / rec["image"]
        expected_categories = as_list(rec.get("expected_category"))
        expected_products = as_list(rec.get("expected_product"))
        t0 = time.perf_counter()
        try:
            pred = recognize_file(path)
            error = None
        except Exception as exc:
            pred = {"product": "ERROR", "category": "ERROR", "confidence": 0}
            error = str(exc)
        elapsed = time.perf_counter() - t0

        ch = category_hit(pred.get("category"), expected_categories)
        ph = product_hit(pred.get("product"), expected_products)
        category_ok += ch
        if ph is not None:
            product_checked += 1
            product_ok += ph
        if rec.get("difficulty") == "hard":
            hard_total += 1
            hard_ok += ch

        rows.append((rec, pred, ch, ph, elapsed, error))
        detail = f" -> {pred.get('product')} / {pred.get('category')} / {pred.get('confidence')}"
        if error:
            detail += f"  报错：{error}"
        print(f"[{i}/{len(records)}] {rec['image']}{detail}  ({elapsed:.1f}s)", flush=True)

    n = len(records)
    print()
    print("| 图片 | 来源 | 难度 | 预测商品 | 预测品类 | 期望品类 | 品类 | 商品 |")
    print("|---|---|---|---|---|---|---|---|")
    for rec, pred, ch, ph, _, _ in rows:
        expected = " / ".join(as_list(rec.get("expected_category")))
        product_mark = "-" if ph is None else ("Y" if ph else "N")
        print(
            f"| {rec['image']} | {rec.get('source', '-')} | {rec.get('difficulty', '-')} | "
            f"{pred.get('product')} | {pred.get('category')} | {expected} | "
            f"{'Y' if ch else 'N'} | {product_mark} |"
        )

    print()
    if n:
        print(f"品类命中：{category_ok}/{n} ({category_ok / n:.1%})")
        if product_checked:
            print(f"商品命中：{product_ok}/{product_checked} ({product_ok / product_checked:.1%})")
        if hard_total:
            print(f"困难样本品类命中：{hard_ok}/{hard_total}")

    if "--write" in sys.argv:
        payload = {
            "model": "qwen3-vl-plus",
            "run_at": date.today().isoformat(),
            "summary": {
                "total": n,
                "category_hits": category_ok,
                "product_checked": product_checked,
                "product_hits": product_ok,
                "hard_total": hard_total,
                "hard_category_hits": hard_ok,
            },
            "samples": [
                {
                    "file": f"tests/{rec['image']}",
                    "source": rec.get("source"),
                    "difficulty": rec.get("difficulty"),
                    "expected_category": as_list(rec.get("expected_category")),
                    "expected_product": as_list(rec.get("expected_product")),
                    "product": pred.get("product"),
                    "category": pred.get("category"),
                    "confidence": pred.get("confidence"),
                    "category_hit": ch,
                    "product_hit": ph,
                    "hit": ch,
                }
                for rec, pred, ch, ph, _, _ in rows
            ],
            "note": "由 tests/eval_recognize.py --write 生成；ai-placeholder 需替换成真实商品照片",
        }
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {BASELINE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
