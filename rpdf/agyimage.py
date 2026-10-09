"""Image engine "agy": the Antigravity CLI generates and edits the images with its built-in image tool.

No API key needed, agy uses its own login. agy stores results in
~/.gemini/antigravity-cli/brain/<conversation_id>/; the conversation id doubles as the
"interaction id" so later edits continue the same conversation (`agy --conversation ID`).
"""
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from . import planner
from .gemini import Gemini, GeminiError, fit_to

BRAIN = Path.home() / ".gemini" / "antigravity-cli" / "brain"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
RATIO_WORDS = {"1:1": "square 1:1", "16:9": "wide landscape 16:9", "9:16": "tall portrait 9:16",
               "4:3": "landscape 4:3", "3:4": "portrait 3:4"}
RULES = ("Use your image generation tool for this, nothing else: do not run commands, do not create or "
         "edit other files. Reply with the path of the resulting image file only.")


def _images(folder):
    if not folder.is_dir():
        return {}
    return {p: p.stat().st_mtime for p in folder.iterdir() if p.suffix.lower() in IMG_EXT}


class AgyImage(Gemini):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.timeout = int(cfg.get("timeout_s", 300))
        self.agy_model = None

    def alive(self):
        return planner.agy_available()

    @staticmethod
    def check_config(cfg):
        if not planner.agy_available():
            raise GeminiError("agy wurde nicht gefunden (Antigravity CLI, angemeldet).")

    def _run(self, prompt, conversation=None, cwd=None, cancelled=lambda: False):
        cmd = ["agy", "-p", prompt, "--output-format", "json", "--print-timeout", f"{self.timeout}s"]
        if conversation:
            cmd += ["--conversation", conversation]
        if self.agy_model:
            cmd += ["--model", self.agy_model]
        with tempfile.TemporaryDirectory(prefix="rpdf-agy-img-") as tmp:
            before = _images(BRAIN / conversation) if conversation else {}
            proc = subprocess.run(cmd, cwd=cwd or tmp, capture_output=True, text=True, timeout=self.timeout + 60)
        out = proc.stdout.strip()
        start = out.find("{")
        if start < 0:
            raise GeminiError(f"agy lieferte keine Antwort: {(proc.stderr or out)[-500:]}")
        data = json.loads(out[start:])
        if data.get("status") != "SUCCESS":
            raise GeminiError(f"agy-Fehler ({data.get('status')}): {(data.get('error') or data.get('response') or '')[:400]}")
        cid = data["conversation_id"]
        # the image subagent may still be writing after agy returned: wait for a new, stable file
        folder, deadline, last = BRAIN / cid, time.time() + self.timeout, None
        while time.time() < deadline:
            if cancelled():
                raise GeminiError("Abgebrochen.")
            new = {p: m for p, m in _images(folder).items() if p not in before}
            if new:
                path = max(new, key=new.get)
                size = path.stat().st_size
                if size and size == last:
                    return cid, path
                last = size
            time.sleep(2)
        raise GeminiError(f"agy hat kein Bild geliefert: {(data.get('response') or '')[:300]}")

    def generate(self, req, cancelled=lambda: False, previous_id=None, image_path=None):
        if previous_id:
            prompt = (f"Edit the previous image as follows: {req['prompt']}\n"
                      f"Keep everything else unchanged. {RULES}")
            cid, path = self._run(prompt, conversation=previous_id, cancelled=cancelled)
        elif image_path:
            with tempfile.TemporaryDirectory(prefix="rpdf-agy-edit-") as tmp:
                shutil.copy(image_path, Path(tmp) / "image.jpg")
                prompt = (f"Edit the image image.jpg in the current directory as follows: {req['prompt']}\n"
                          f"Keep everything else unchanged. {RULES}")
                cid, path = self._run(prompt, cwd=tmp, cancelled=cancelled)
        else:
            prompt = (f"Generate one image, {RATIO_WORDS[req['ratio']]} aspect ratio, from this prompt "
                      f"(use it as written):\n{req['prompt']}\n{RULES}")
            cid, path = self._run(prompt, cancelled=cancelled)
        self.last_interaction_id = cid
        with Image.open(path) as im:
            return fit_to(im.convert("RGB"), *req["size"])
