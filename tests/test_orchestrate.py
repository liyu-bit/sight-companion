"""B-1 编排单元测试：mock 识物/建议/TTS，不调用真实模型。

运行：py -3.13 -m unittest discover -s tests -p "test_orchestrate.py" -v
"""

import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agents.orchestrate as orch

IMAGE = base64.b64encode(b"fake-image").decode()
USER = {"allergies": [{"name": "乳糖", "severity": "mild"}]}
SEEN = {"product": "纯牛奶", "category": "纯牛奶", "confidence": 0.95}
ADVICE = {
    "product": None,
    "expiry_days": 3,
    "allergen_hit": "乳糖",
    "price": "5.00",
    "advice": "建议换一盒",
    "reason": "还有 3 天过期，且你乳糖不耐受",
    "options": ["换一盒无乳糖牛奶", "这盒尽快喝完"],
    "user_decides": True,
    "speech": "这盒牛奶还有 3 天过期，你乳糖不耐受，建议换一盒；要不要听听别的选项？",
}
AUDIO = "data:audio/x-wav;base64,AAA"


def request(image: str = IMAGE) -> orch.AdviseRequest:
    return orch.AdviseRequest(image=image, user=USER)


class OrchestrateTest(unittest.TestCase):
    def test_category_passed_to_advise_and_product_fallback(self):
        captured = {}

        def fake_advise(image, user, evidence=None):
            captured["image"] = image
            captured["user"] = user
            captured["evidence"] = evidence
            return dict(ADVICE)

        with (
            mock.patch.object(orch, "recognize", return_value=SEEN) as rec,
            mock.patch.object(orch, "advise", side_effect=fake_advise),
            mock.patch.object(orch, "synthesize", return_value=AUDIO) as tts,
        ):
            out = orch.api_advise(request())

        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs["timeout"], orch.RECOGNIZE_TIMEOUT)
        self.assertEqual(captured["image"], IMAGE)
        self.assertEqual(captured["user"], USER)
        self.assertIn("纯牛奶", captured["evidence"])
        self.assertIn("0.95", captured["evidence"])
        self.assertEqual(out["product"], "纯牛奶")
        self.assertEqual(out["audio"], AUDIO)
        self.assertTrue(out["user_decides"])
        tts.assert_called_once_with(ADVICE["speech"])

    def test_recognize_failure_still_calls_advise_without_evidence(self):
        captured = {}

        def fake_advise(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return dict(ADVICE)

        with (
            mock.patch.object(orch, "recognize", side_effect=RuntimeError("timeout")),
            mock.patch.object(orch, "advise", side_effect=fake_advise),
            mock.patch.object(orch, "synthesize", return_value=None),
        ):
            out = orch.api_advise(request())

        self.assertEqual(len(captured["args"]), 2)
        self.assertEqual(captured["kwargs"], {})
        self.assertIsNone(out["audio"])

    def test_advise_failure_returns_502(self):
        with (
            mock.patch.object(orch, "recognize", return_value=SEEN),
            mock.patch.object(orch, "advise", side_effect=RuntimeError("boom")),
            mock.patch.object(orch, "synthesize", return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                orch.api_advise(request())

        self.assertEqual(ctx.exception.status_code, 502)

    def test_empty_image_returns_400(self):
        with self.assertRaises(HTTPException) as ctx:
            orch.api_advise(request("   "))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_oversized_image_returns_413(self):
        with mock.patch.object(orch, "MAX_IMAGE_CHARS", 4):
            with self.assertRaises(HTTPException) as ctx:
                orch.api_advise(request("AAAAA"))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_evidence_ignores_unrecognized(self):
        self.assertIsNone(orch._evidence_from(None))
        self.assertIsNone(
            orch._evidence_from({"product": "未识别", "category": "未知", "confidence": 0})
        )


if __name__ == "__main__":
    unittest.main()
