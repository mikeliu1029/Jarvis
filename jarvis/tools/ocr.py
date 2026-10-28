"""Windows' built-in OCR engine (Windows.Media.Ocr) - local, fast, nothing extra to install."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from PIL import Image


@dataclass
class OcrWord:
    text: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class OcrLine:
    text: str
    words: list[OcrWord]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        x0 = min(w.x for w in self.words)
        y0 = min(w.y for w in self.words)
        x1 = max(w.x + w.w for w in self.words)
        y1 = max(w.y + w.h for w in self.words)
        return x0, y0, x1 - x0, y1 - y0


def _engine():
    from winrt.windows.media.ocr import OcrEngine
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        from winrt.windows.globalization import Language
        engine = OcrEngine.try_create_from_language(Language("en-US"))
    if engine is None:
        raise RuntimeError("Windows OCR isn't available - add an OCR language in Settings > Time & language.")
    return engine


async def _recognize_async(img: Image.Image):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                    rgba.width, rgba.height)
    return await _engine().recognize_async(bitmap)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}  # called from inside an event loop: run on a helper thread instead
    t = threading.Thread(target=lambda: box.setdefault("r", asyncio.run(coro)))
    t.start()
    t.join()
    return box["r"]


def recognize(img: Image.Image, scale: float = 1.0) -> list[OcrLine]:
    """OCR an image; word boxes come back in the original image's pixel coordinates."""
    from winrt.windows.media.ocr import OcrEngine
    limit = int(OcrEngine.max_image_dimension)
    scale = min(scale, limit / max(img.width, img.height))
    work = img if abs(scale - 1) < 0.01 else img.resize((int(img.width * scale), int(img.height * scale)))
    result = _run(_recognize_async(work))
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append(OcrWord(w.text, int(r.x / scale), int(r.y / scale), int(r.width / scale), int(r.height / scale)))
        if words:
            lines.append(OcrLine(line.text, words))
    return lines
