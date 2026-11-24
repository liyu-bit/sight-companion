"""冒烟检查：启动后端后运行，验证契约字段与关键路径。

用法：py -3.13 tests/smoke_api.py [base_url]
默认 http://127.0.0.1:8000；全部通过退出码 0。
"""

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
FIELDS = [
    "product",
    "expiry_days",
    "allergen_hit",
    "price",
    "advice",
    "reason",
    "options",
    "user_decides",
    "speech",
]
RED_LINES = ["verdict", "能买", "别买", "必须买", "不能买"]
MAX_IMAGE_CHARS = 6 * 1024 * 1024
USER = {"allergies": [{"name": "乳糖", "severity": "mild"}]}

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        failures.append(name)


def http(method: str, path: str, payload: dict | None = None, timeout: int = 90):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def main() -> None:
    status, _, _ = http("GET", "/api/health")
    check("GET /api/health = 200", status == 200, status)

    status, ctype, _ = http("GET", "/")
    check("GET / 返回页面", status == 200 and "text/html" in ctype, f"{status} {ctype}")

    image = base64.b64encode((ROOT / "tests" / "milk.jpg").read_bytes()).decode()
    status, _, body = http("POST", "/api/advise", {"image": image, "user": USER})
    check("POST /api/advise = 200", status == 200, status)
    if status == 200:
        result = json.loads(body)
        missing = [f for f in FIELDS if f not in result]
        check("9 字段齐全", not missing, missing)
        check("user_decides 恒为 true", result.get("user_decides") is True, result.get("user_decides"))
        audio = result.get("audio")
        check("audio 为 data URL 或 null", audio is None or str(audio).startswith("data:audio/"), str(audio)[:40])
        text = " ".join(
            [
                str(result.get("advice") or ""),
                str(result.get("reason") or ""),
                " ".join(result.get("options") or []),
                str(result.get("speech") or ""),
            ]
        )
        hits = [w for w in RED_LINES if w in text]
        check("无红线词", not hits, hits)
        check("options 非空", bool(result.get("options")), result.get("options"))
        check("speech 非空", bool(result.get("speech")), result.get("speech"))

    status, _, _ = http("POST", "/api/advise", {"image": "  ", "user": USER})
    check("空图片 = 400", status == 400, status)

    status, _, _ = http("POST", "/api/advise", {"user": USER})
    check("缺 image = 422", status == 422, status)

    status, _, _ = http("POST", "/api/advise", {"image": "A" * (MAX_IMAGE_CHARS + 10), "user": USER})
    check("超大图片 = 413", status == 413, status)

    print()
    if failures:
        print(f"{len(failures)} 项失败：" + "，".join(failures))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
