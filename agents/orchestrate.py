"""编排：识物 → 购买建议 → TTS，对外暴露 POST /api/advise。"""

import base64
import os
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

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
TTS_MODEL = os.getenv("TTS_MODEL", "qwen3-tts-flash")
TTS_VOICE = os.getenv("TTS_VOICE", "Cherry")

app = FastAPI(title="看得见的同行者")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserProfile(BaseModel):
    allergies: list[dict] = Field(default_factory=list)


class AdviseRequest(BaseModel):
    image: str
    user: UserProfile = Field(default_factory=UserProfile)


def _guess_mime(image_base64: str) -> str:
    head = image_base64[:12]
    if head.startswith("/9j/"):
        return "image/jpeg"
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lG"):
        return "image/gif"
    if head.startswith("UklG"):
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
    if not speech:
        return None
    try:
        resp = requests.post(
            TTS_URL,
            headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"},
            json={"model": TTS_MODEL, "input": {"text": speech, "voice": TTS_VOICE}},
            timeout=30,
        )
        resp.raise_for_status()
        audio_url = resp.json()["output"]["audio"]["url"]
        audio = requests.get(audio_url, timeout=30)
        audio.raise_for_status()
        content = _fix_streaming_wav(audio.content)
        mime = audio.headers.get("Content-Type", "").split(";")[0] or "audio/wav"
        return f"data:{mime};base64,{base64.b64encode(content).decode()}"
    except Exception as exc:
        print(f"[tts] 合成失败，本次返回无音频：{exc}")
        return None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/advise")
def api_advise(req: AdviseRequest) -> dict:
    image = req.image.strip()
    if not image:
        raise HTTPException(status_code=400, detail="image 为空")

    try:
        seen = recognize(image, _guess_mime(image))
        print(
            f"[recognize] {seen['product']} / {seen['category']} / {seen['confidence']}",
            flush=True,
        )
    except Exception as exc:
        print(f"[recognize] 识物失败，继续走建议：{exc}", flush=True)

    result = advise(image, req.user.model_dump())
    result["audio"] = synthesize(result.get("speech", ""))
    print(f"[advise] {result.get('advice')} | speech={result.get('speech')}", flush=True)
    return result


FRONTEND = ROOT / "frontend"
if (FRONTEND / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
