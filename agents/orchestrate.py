"""编排 + TTS（B-1）：POST /api/advise，串联识物、建议两个 Agent，合成语音。

启动（项目根目录）：uvicorn agents.orchestrate:app --port 8000
契约见 docs/契约.md 第 1 节：请求纯 base64 图片，响应 9 字段建议 JSON + audio。
"""

import base64
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.advise import advise
from agents.knowledge import evidence_for
from agents.recognize import recognize

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

# httpx 解析 NO_PROXY 里的裸 IPv6 条目（::1 / [::1]）会抛 InvalidURL，先剔除
if os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = ",".join(
        e for e in os.environ["NO_PROXY"].split(",") if e.strip() not in ("::1", "[::1]")
    )

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
TTS_MODEL = "qwen3-tts-flash"
TTS_VOICE = "Cherry"

app = FastAPI(title="看得见的同行者")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AdviseRequest(BaseModel):
    image: str
    user: dict


def _sniff_mime(image_base64: str) -> str:
    head = base64.b64decode(image_base64[:32])
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"RIFF") and b"WEBP" in head:
        return "image/webp"
    if head.startswith(b"GIF8"):
        return "image/gif"
    return "image/jpeg"


def _tts(speech: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("config/.env 中缺少 DASHSCOPE_API_KEY")
    resp = requests.post(
        TTS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": TTS_MODEL, "input": {"text": speech, "voice": TTS_VOICE}},
        timeout=60,
    )
    resp.raise_for_status()
    audio_url = resp.json()["output"]["audio"]["url"]
    try:
        audio = requests.get(audio_url, timeout=60)
        audio.raise_for_status()
        return base64.b64encode(audio.content).decode("ascii")
    except requests.RequestException:
        # 下载失败（如本机 DNS 抖动）时按契约回退为可播放 URL
        return audio_url


@app.post("/api/advise")
def api_advise(req: AdviseRequest):
    mime = _sniff_mime(req.image)
    recognized = recognize(req.image, mime)
    evidence = evidence_for(recognized["category"])
    result = advise(req.image, req.user, evidence=evidence)
    try:
        audio = _tts(result["speech"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS 合成失败：{e}")
    return {**result, "audio": audio, "category": recognized["category"], "evidence": evidence}


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")