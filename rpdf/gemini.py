"""Gemini image backend (Interactions API), the same calls as the nb2lite skill for agy.

Same shape as the ComfyUI client in comfy.py (check_config / build_workflow / alive / generate),
plus `edit` for stateful follow-up edits via previous_interaction_id.
"""
import base64
import io
import mimetypes
import os
import re
import time
from pathlib import Path

from PIL import Image

from .comfy import ComfyError, save_image  # noqa: F401  (save_image re-exported for the pipeline)

GeminiError = ComfyError

DEFAULT_MODEL = "gemini-3.1-flash-lite-image"
KEY_FILE = Path.home() / "gemini.key"
RATIOS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4}
THINKING = ("low", "high")
QUALITY_TAGS = re.compile(r"(?i)\b(worst quality|low quality|lowres|blurry|jpeg artifacts|bad anatomy|"
                          r"deformed|extra limbs|masterpiece|best quality)\b,?\s*")


def api_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return key or ""


def nearest_ratio(width, height):
    target = width / height
    return min(RATIOS, key=lambda r: abs(RATIOS[r] - target))


def fit_to(img, width, height):
    """Center-crop to the target aspect ratio, then scale to the target size."""
    w, h = img.size
    want = width / height
    if w / h > want:
        nw = round(h * want)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / want)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize((width, height), Image.LANCZOS)


class Gemini:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = cfg.get("model") or DEFAULT_MODEL
        self.last_interaction_id = None
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise GeminiError("Paket „google-genai“ fehlt: pip install google-genai")
            self._client = genai.Client(api_key=api_key())
        return self._client

    def alive(self):
        return bool(api_key())

    # ---------- workflows (here: request dicts) ----------
    @staticmethod
    def check_config(cfg):
        if not api_key():
            raise GeminiError("Kein Gemini-API-Key: GEMINI_API_KEY setzen oder in ~/gemini.key speichern.")
        if cfg.get("thinking_level", "low") not in THINKING:
            raise GeminiError(f"thinking_level muss {' oder '.join(THINKING)} sein.")

    @staticmethod
    def build_workflow(cfg, prompt, negative, width, height, seed):
        # the Interactions API has no seed and no negative prompt: the latter goes into the text
        avoid = QUALITY_TAGS.sub("", negative or "").strip(" ,")
        text = prompt
        if width / height > 1.9:
            text += ", wide cinematic panoramic composition, main subject in the central horizontal band"
        if avoid:
            text += f". Do not include: {avoid}."
        return {"prompt": text, "size": (width, height), "ratio": nearest_ratio(width, height),
                "thinking": cfg.get("thinking_level", "low")}

    # ---------- run ----------
    def generate(self, req, cancelled=lambda: False, previous_id=None, image_path=None):
        inp = req["prompt"]
        if image_path:
            mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
            data = base64.b64encode(Path(image_path).read_bytes()).decode()
            inp = [{"type": "image", "data": data, "mime_type": mime}, {"type": "text", "text": req["prompt"]}]
        kwargs = dict(model=self.model, input=inp, store=True,
                      response_format={"type": "image", "aspect_ratio": req["ratio"]},
                      generation_config={"thinking_level": req["thinking"]})
        if previous_id:
            kwargs["previous_interaction_id"] = previous_id
            kwargs["response_format"] = {"type": "image"}   # ratio is inherited on edits
        last = None
        for attempt in range(4):
            if cancelled():
                raise GeminiError("Abgebrochen.")
            try:
                it = self.client.interactions.create(**kwargs)
                break
            except GeminiError:
                raise
            except Exception as e:      # rate limit / transient server errors: back off and retry
                last = e
                if not re.search(r"429|500|502|503|504|RESOURCE_EXHAUSTED|UNAVAILABLE|timed? ?out", str(e)):
                    raise GeminiError(f"Gemini-Fehler: {str(e)[:600]}")
                time.sleep(4 * 2 ** attempt)
        else:
            raise GeminiError(f"Gemini nicht erreichbar: {str(last)[:600]}")
        out = getattr(it, "output_image", None)
        data = getattr(out, "data", None) if out else None
        if not data:
            raise GeminiError("Gemini hat kein Bild geliefert (evtl. vom Sicherheitsfilter blockiert) – Prompt anpassen.")
        raw = base64.b64decode(data) if isinstance(data, str) else data
        self.last_interaction_id = it.id
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return fit_to(img, *req["size"])

    def interrupt(self):
        pass
