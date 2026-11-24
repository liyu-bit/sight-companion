"""advise.py 离线单测（mock VL 返回，不调 API）：python tests/test_advise.py"""

import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agents.advise as advise

FIELDS = ["product", "expiry_days", "allergen_hit", "price", "advice", "reason", "options", "user_decides", "speech"]
FORBIDDEN = ("verdict", "能买", "别买", "必须买", "不能买")
MILD = {"allergies": [{"name": "乳糖", "severity": "mild"}]}
SEVERE_PEANUT = {"allergies": [{"name": "花生", "severity": "severe"}]}
SEVERE_NUT = {"allergies": [{"name": "坚果", "severity": "severe"}]}


def run(seen: dict, user: dict) -> dict:
    payload = json.dumps(seen, ensure_ascii=False)
    fake = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=payload))]
                )
            )
        )
    )
    advise._load_api_key = lambda: "fake"
    advise.OpenAI = lambda **kw: fake
    return advise.advise("fakeb64", user)


def base(**kw) -> dict:
    seen = {"product": "某品牌纯牛奶", "category": "纯牛奶", "expiry_days": None,
            "allergen_hit": None, "price": "5.00", "ingredients_read": False}
    seen.update(kw)
    return seen


class TestAdvise(unittest.TestCase):
    def check_contract(self, r):
        self.assertEqual(list(r.keys()), FIELDS)
        self.assertIs(r["user_decides"], True)
        text = json.dumps(r, ensure_ascii=False)
        for word in FORBIDDEN:
            self.assertNotIn(word, text)

    def test_mild_act1_category_prior(self):
        r = run(base(), MILD)
        self.check_contract(r)
        self.assertEqual(r["allergen_hit"], "乳糖")
        self.assertEqual(r["speech"], "这是纯牛奶，你乳糖不耐受，建议换一盒无乳糖的。")
        self.assertEqual(r["advice"], "建议换一盒")

    def test_mild_act1_legacy_string_profile(self):
        r = run(base(), {"allergies": ["乳糖"]})
        self.assertEqual(r["speech"], "这是纯牛奶，你乳糖不耐受，建议换一盒无乳糖的。")

    def test_mild_act2_with_date(self):
        r = run(base(expiry_days=3, allergen_hit="乳糖"), MILD)
        self.check_contract(r)
        self.assertEqual(r["speech"], "这盒还有 3 天过期，你乳糖不耐受，建议换一盒；要不要听听别的选项？")
        self.assertEqual(r["reason"], "还有 3 天过期，且你乳糖不耐受")

    def test_mild_yogurt_prior(self):
        r = run(base(category="酸奶", product="某品牌酸奶"), MILD)
        self.assertEqual(r["allergen_hit"], "乳糖")
        self.assertEqual(r["speech"], "这是酸奶，你乳糖不耐受，建议换一盒无乳糖的。")

    def test_soy_milk_not_dairy(self):
        r = run(base(category="豆乳", product="某豆乳饮料"), MILD)
        self.assertEqual(r["allergen_hit"], None)
        self.assertEqual(r["advice"], "建议让家人帮你看一眼")

    def test_no_risk(self):
        r = run(base(category="薯片", product="某薯片", expiry_days=30), MILD)
        self.check_contract(r)
        self.assertEqual(r["advice"], "可以考虑这盒")
        self.assertEqual(r["speech"], "没看到过敏或过期风险，可以考虑这盒，最终你决定。")

    def test_near_expiry_only(self):
        r = run(base(category="薯片", product="某薯片", expiry_days=2), MILD)
        self.assertEqual(r["speech"], "这盒还有 2 天过期，建议换一盒，要不要听听别的选项？")

    def test_severe_unread_goes_help(self):
        r = run(base(category="饼干", expiry_days=30), SEVERE_PEANUT)
        self.check_contract(r)
        self.assertEqual(r["advice"], "建议让家人帮你看一眼")
        self.assertIn("拍照发给家人", r["options"])
        self.assertIn("花生", r["speech"])
        self.assertNotEqual(r["advice"], "可以考虑这盒")

    def test_severe_missing_ingredients_read_goes_help(self):
        seen = base(category="饼干", expiry_days=30)
        del seen["ingredients_read"]
        r = run(seen, SEVERE_PEANUT)
        self.assertEqual(r["advice"], "建议让家人帮你看一眼")

    def test_severe_read_hit_goes_swap(self):
        r = run(base(category="花生酱", allergen_hit="花生", ingredients_read=True, expiry_days=30), SEVERE_PEANUT)
        self.check_contract(r)
        self.assertEqual(r["advice"], "建议换一盒")

    def test_severe_read_no_hit_no_prior_can_consider_with_help_option(self):
        r = run(base(category="薯片", product="某薯片", ingredients_read=True, expiry_days=30), SEVERE_PEANUT)
        self.check_contract(r)
        self.assertEqual(r["advice"], "可以考虑这盒")
        self.assertIn("拍照发给家人", r["options"])

    def test_severe_read_no_hit_but_prior_conflict_never_safe(self):
        r = run(base(category="混合坚果", product="某混合坚果", ingredients_read=True, expiry_days=30), SEVERE_NUT)
        self.check_contract(r)
        self.assertNotEqual(r["advice"], "可以考虑这盒")
        self.assertIn("再核对", r["speech"])
        self.assertIn("拍照发给家人", r["options"])

    def test_evidence_has_source(self):
        ev = advise.evidence_for_category("纯牛奶")
        self.assertIn("乳糖", ev)
        self.assertIn("《中国食物成分表》第6版", ev)
        self.assertIsNone(advise.evidence_for_category("饮用水"))


if __name__ == "__main__":
    unittest.main(verbosity=2)