"""编排 + TTS（B-1）：POST /api/advise，串联识物、建议两个 Agent，合成语音。

流程：识物 Agent 认出 product/category → 作为品类先验传给建议 Agent → TTS。
启动（项目根目录）：py -3.13 -m uvicorn agents.orchestrate:app --host 0.0.0.0 --port 8000
契约见 docs/契约.md 第 1 节：请求纯 base64 图片，响应 9 字段建议 JSON + audio。
"""

import base64
import os
import time
from pathlib import Path

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.advise import advise
from agents.recognize import recognize

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

# httpx 解析 NO_PROXY 里的裸 IPv6 条目（::1 / [::1]）会抛 InvalidURL，先剔除
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = ",".join(
        e for e in os.environ["NO_PROXY"].split(",") if e.strip() not in ("::1", "[::1]")
    )

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
TTS_MODEL = os.getenv("TTS_MODEL", "qwen3-tts-flash")
TTS_VOICE = os.getenv("TTS_VOICE", "Cherry")
PORT = int(os.getenv("PORT", "8000"))
RECOGNIZE_TIMEOUT = float(os.getenv("RECOGNIZE_TIMEOUT", "20"))
MAX_IMAGE_CHARS = 6 * 1024 * 1024  # base64 字符数上限，约 4.5MB 原图
ALLOW_ORIGINS = [o.strip() for o in os.getenv("ALLOW_ORIGINS", "*").split(",") if o.strip()] or ["*"]

app = FastAPI(title="看得见的同行者")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AdviseRequest(BaseModel):
    image: str
    user: dict = Field(default_factory=dict)


def _guess_mime(image_base64: str) -> str:
    if image_base64.startswith("/9j/"):
        return "image/jpeg"
    if image_base64.startswith("iVBOR"):
        return "image/png"
    if image_base64.startswith("R0lG"):
        return "image/gif"
    if image_base64.startswith("UklG"):
        return "image/webp"
    return "image/jpeg"


def _fix_streaming_wav(data: bytes) -> bytes:
    """TTS 返回流式 WAV，data 块长度是占位值，按真实字节数修正以便正常播放/解析。"""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    buf = bytearray(data)
    buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
    i = 12
    while i + 8 <= len(buf):
        size = int.from_bytes(buf[i + 4 : i + 8], "little")
        if buf[i : i + 4] == b"data":
            if size > len(buf) - i - 8:
                buf[i + 4 : i + 8] = (len(buf) - i - 8).to_bytes(4, "little")
            break
        i += 8 + size + (size & 1)
    return bytes(buf)


def synthesize(speech: str) -> str | None:
    """失败返回 None，接口仍返回建议（前端降级用 speechSynthesis）。"""
    if not speech:
        return None
    try:
        resp = requests.post(
            TTS_URL,
            headers={
                "Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={"model": TTS_MODEL, "input": {"text": speech, "voice": TTS_VOICE}},
            timeout=60,
        )
        resp.raise_for_status()
        audio_url = resp.json()["output"]["audio"]["url"]
        audio = requests.get(audio_url, timeout=60)
        audio.raise_for_status()
        content = _fix_streaming_wav(audio.content)
        mime = audio.headers.get("Content-Type", "").split(";")[0] or "audio/wav"
        return f"data:{mime};base64,{base64.b64encode(content).decode()}"
    except Exception as exc:
        print(f"[tts] 合成失败，本次返回无音频：{exc}", flush=True)
        return None


def _evidence_from(seen: dict | None) -> str | None:
    """把识物结果整理成品类先验，交给建议 Agent 参考。"""
    if not seen:
        return None
    product = seen.get("product")
    category = seen.get("category")
    if product in (None, "", "未识别") and category in (None, "", "未知"):
        return None
    return (
        f"识物 Agent 判断：商品「{product or '未识别'}」，品类「{category or '未知'}」，"
        f"置信度 {seen.get('confidence')}。以上仅作品类先验，请以照片中实际可见信息为准。"
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/advise")
def api_advise(req: AdviseRequest) -> dict:
    image = req.image.strip()
    if not image:
        raise HTTPException(status_code=400, detail="image 为空")
    if len(image) > MAX_IMAGE_CHARS:
        raise HTTPException(status_code=413, detail="图片过大，请压缩后再传")

    # 先识物再建议：category 是契约 3.5 节要求的品类先验，撕标/磨花时靠它判断乳糖
    t0 = time.perf_counter()
    seen = None
    t_seen = None
    try:
        seen = recognize(image, _guess_mime(image), timeout=RECOGNIZE_TIMEOUT)
        t_seen = time.perf_counter() - t0
        print(
            f"[recognize] {seen['product']} / {seen['category']} / {seen['confidence']} "
            f"({t_seen:.1f}s)",
            flush=True,
        )
    except Exception as exc:
        print(f"[recognize] 识物失败，继续走建议：{exc}", flush=True)

    evidence = _evidence_from(seen)
    t_advise_start = time.perf_counter()
    try:
        if evidence:
            result = advise(image, req.user, evidence=evidence)
        else:
            result = advise(image, req.user)
        t_advise = time.perf_counter() - t_advise_start
    except Exception as exc:
        print(f"[advise] 建议生成失败：{exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"建议生成失败：{exc}") from exc

    if (
        result.get("product") in (None, "", "未识别")
        and seen
        and seen.get("product") not in (None, "", "未识别")
    ):
        result["product"] = seen["product"]

    t_tts_start = time.perf_counter()
    result["audio"] = synthesize(result.get("speech", ""))
    t_end = time.perf_counter()

    seen_cost = f"{t_seen:.1f}s" if t_seen is not None else "失败"
    print(
        f"[timing] recognize={seen_cost} advise={t_advise:.1f}s "
        f"tts={t_end - t_tts_start:.1f}s total={t_end - t0:.1f}s",
        flush=True,
    )
    print(f"[advise] {result.get('advice')} | speech={result.get('speech')}", flush=True)
    return result


FRONTEND = ROOT / "frontend"
if (FRONTEND / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
