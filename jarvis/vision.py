"""Vision helpers: answer a question about a screenshot, or find where something is on it.

Uses the local Ollama vision model (qwen3-vl by default) or Claude when brain.provider = "anthropic".
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re

import requests
from PIL import Image

from .config import get_settings

log = logging.getLogger(__name__)


def to_jpeg(img: Image.Image, max_side: int = 1568, quality: int = 80) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _provider() -> str:
    return str(get_settings().get("brain.provider", "ollama")).lower()


def _ollama(prompt: str, image: bytes) -> str:
    s = get_settings()
    body = {
        "model": s.get("brain.ollama.vision_model") or s.get("brain.ollama.model", "qwen3-vl:8b"),
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}],
        "stream": False,
        "keep_alive": s.get("brain.ollama.keep_alive", "30m"),
        # Same num_ctx as the brain so Ollama doesn't reload the model between calls.
        "options": {"num_ctx": int(s.get("brain.ollama.num_ctx", 16384)), "temperature": 0.2},
    }
    host = str(s.get("brain.ollama.host", "http://127.0.0.1:11434")).rstrip("/").replace("://localhost", "://127.0.0.1")
    with requests.Session() as http:
        http.trust_env = False
        r = http.post(host + "/api/chat", json=body, timeout=180)
    r.raise_for_status()
    text = r.json().get("message", {}).get("content", "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _claude(prompt: str, image: bytes) -> str:
    import anthropic
    s = get_settings()
    client = anthropic.Anthropic(timeout=60)
    resp = client.messages.create(
        model=s.get("brain.anthropic.model", "claude-opus-5-5"),
        max_tokens=4000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": prompt},
        ]}],
    )
    if resp.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def describe(image: bytes, question: str) -> str:
    prompt = (f"This is a screenshot of the user's Windows PC. {question}\n"
              "Answer precisely and concisely. Quote on-screen text exactly where it matters.")
    return _claude(prompt, image) if _provider() == "anthropic" else _ollama(prompt, image)


def locate(img: Image.Image, target: str) -> tuple[int, int] | None:
    """Find the centre of a described UI element; returns pixel coords in `img`, or None."""
    jpeg = to_jpeg(img)
    shown = Image.open(io.BytesIO(jpeg)).size
    if _provider() == "anthropic":
        prompt = (f"The image is {shown[0]}x{shown[1]} pixels. Find the UI element: \"{target}\". "
                  'Reply with only JSON {"x": <int>, "y": <int>} giving its centre in image pixels, '
                  'or {"x": null, "y": null} if it is not visible.')
        reply, scale = _claude(prompt, jpeg), (img.width / shown[0], img.height / shown[1])
    else:
        # Qwen-VL models ground objects in coordinates normalised to 0-1000.
        prompt = (f"Locate the UI element: \"{target}\". Reply with only JSON {{\"x\": <int>, \"y\": <int>}} "
                  "giving its centre, with coordinates normalised to 0-1000 across the image width and height. "
                  'If it is not visible reply {"x": null, "y": null}.')
        reply, scale = _ollama(prompt, jpeg), (img.width / 1000, img.height / 1000)
    m = re.search(r"\{[^{}]*\}", reply or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("x") is None or data.get("y") is None:
            return None
        return int(float(data["x"]) * scale[0]), int(float(data["y"]) * scale[1])
    except (ValueError, TypeError):
        return None
