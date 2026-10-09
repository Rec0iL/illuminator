"""Minimal ComfyUI HTTP client with three workflow types: anima, checkpoint, custom."""
import copy
import io
import json
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageFilter

WORKFLOWS = {
    "anima": "Diffusion-Modell (UNET + CLIP + VAE, z.B. Anima)",
    "checkpoint": "Checkpoint (SD1.5 / SDXL / Illustrious / Pony)",
    "custom": "Eigener Workflow (ComfyUI API-JSON)",
}


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def _get(self, path, timeout=10):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return r.read()

    def _get_json(self, path, timeout=10):
        return json.loads(self._get(path, timeout))

    def alive(self):
        try:
            self._get_json("/system_stats", timeout=3)
            return True
        except Exception:
            return False

    def _choices(self, node, field):
        try:
            info = self._get_json(f"/object_info/{node}")
            return list(info[node]["input"]["required"][field][0])
        except Exception:
            return []

    def models(self):
        """Model lists for the settings dropdowns."""
        return {
            "unet": self._choices("UNETLoader", "unet_name"),
            "checkpoint": self._choices("CheckpointLoaderSimple", "ckpt_name"),
            "clip": self._choices("CLIPLoader", "clip_name"),
            "clip_type": self._choices("CLIPLoader", "type"),
            "vae": self._choices("VAELoader", "vae_name"),
            "sampler": self._choices("KSampler", "sampler_name"),
            "scheduler": self._choices("KSampler", "scheduler"),
        }

    # ---------- workflows ----------
    @staticmethod
    def check_config(cfg):
        """Raise a readable error if the selected workflow is missing a model."""
        kind = cfg.get("workflow", "anima")
        required = {"anima": [("unet", "Diffusion-Modell"), ("clip", "Text-Encoder"), ("vae", "VAE")],
                    "checkpoint": [("checkpoint", "Checkpoint")],
                    "custom": [("custom_workflow", "API-Workflow")]}[kind]
        missing = [label for key, label in required if not cfg.get(key)]
        if missing:
            raise ComfyError(f"Nicht gewählt: {', '.join(missing)} – unter „Engine“ einstellen.")

    @staticmethod
    def build_workflow(cfg, prompt, negative, width, height, seed):
        kind = cfg.get("workflow", "anima")
        if kind == "custom":
            return Comfy._patch_custom(cfg, prompt, negative, width, height, seed)

        sampler = {"seed": seed, "steps": int(cfg["steps"]), "cfg": float(cfg["cfg"]),
                   "sampler_name": cfg["sampler"], "scheduler": cfg["scheduler"], "denoise": 1.0}
        if kind == "checkpoint":
            loaders = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": cfg["checkpoint"]}}}
            model, clip, vae = ["1", 0], ["1", 1], ["1", 2]
        else:
            loaders = {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"}},
                "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": cfg["clip"],
                                                            "type": cfg.get("clip_type") or "stable_diffusion",
                                                            "device": "default"}},
                "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
            }
            model, clip, vae = ["1", 0], ["2", 0], ["3", 0]
        return {
            **loaders,
            "10": {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": prompt}},
            "11": {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": negative}},
            "12": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "13": {"class_type": "KSampler", "inputs": {"model": model, "positive": ["10", 0],
                                                        "negative": ["11", 0], "latent_image": ["12", 0],
                                                        **sampler}},
            "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": vae}},
            "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": "rulebook", "images": ["14", 0]}},
        }

    @staticmethod
    def _patch_custom(cfg, prompt, negative, width, height, seed):
        """Fill prompt/negative/seed/size into a workflow exported via 'Export (API)'."""
        path = cfg.get("custom_workflow")
        if not path:
            raise ComfyError("Kein eigener Workflow gewählt.")
        with open(path, encoding="utf-8") as f:
            wf = copy.deepcopy(json.load(f))
        if "nodes" in wf:
            raise ComfyError("Das ist ein UI-Workflow. Bitte in ComfyUI über 'Export (API)' speichern.")

        def text_node(ref):
            # follow conditioning links (e.g. through FluxGuidance) to the text encoder
            seen = set()
            while isinstance(ref, list) and ref[0] not in seen:
                seen.add(ref[0])
                node = wf.get(ref[0], {})
                inputs = node.get("inputs", {})
                if isinstance(inputs.get("text"), str):
                    return node
                ref = inputs.get("conditioning") or inputs.get("positive")
            return None

        samplers = [n for n in wf.values() if n.get("class_type", "").startswith("KSampler")
                    or "noise_seed" in n.get("inputs", {})]
        if not samplers:
            raise ComfyError("Kein KSampler im Workflow gefunden.")
        for s in samplers:
            inp = s["inputs"]
            for k in ("seed", "noise_seed"):
                if k in inp:
                    inp[k] = seed
            pos, neg = text_node(inp.get("positive")), text_node(inp.get("negative"))
            if pos:
                pos["inputs"]["text"] = prompt
            if neg:
                neg["inputs"]["text"] = negative
            if pos is None:
                raise ComfyError("Positiver Prompt-Knoten nicht gefunden.")
        for n in wf.values():
            inp = n.get("inputs", {})
            if "width" in inp and "height" in inp and not isinstance(inp["width"], list):
                inp["width"], inp["height"] = width, height
        return wf

    # ---------- run ----------
    def generate(self, workflow, timeout=900, cancelled=lambda: False):
        data = json.dumps({"prompt": workflow}).encode()
        req = urllib.request.Request(self.url + "/prompt", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ComfyError(f"ComfyUI lehnt den Workflow ab: {e.read().decode(errors='replace')[:800]}")
        pid = resp["prompt_id"]
        start = time.time()
        while time.time() - start < timeout:
            if cancelled():
                self.interrupt()
                raise ComfyError("Abgebrochen.")
            hist = self._get_json(f"/history/{pid}")
            if pid in hist:
                entry = hist[pid]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    detail = msgs[0][1].get("exception_message", "") if msgs else ""
                    raise ComfyError(f"ComfyUI-Fehler: {detail.strip()[:800]}")
                for out in entry.get("outputs", {}).values():
                    for img in out.get("images", []):
                        q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                                                    "type": img.get("type", "output")})
                        return Image.open(io.BytesIO(self._get(f"/view?{q}", timeout=60))).convert("RGB")
                if status.get("completed"):
                    raise ComfyError("Workflow lief durch, hat aber kein Bild gespeichert.")
            time.sleep(1.5)
        raise ComfyError("Zeitüberschreitung beim Generieren.")

    def interrupt(self):
        try:
            urllib.request.urlopen(urllib.request.Request(self.url + "/interrupt", data=b"", method="POST"), timeout=5)
        except Exception:
            pass


def save_image(img, path, kind):
    """Store as JPEG; the page background is darkened so text stays readable."""
    if kind == "background":
        img = ImageEnhance.Color(img).enhance(0.35)
        img = ImageEnhance.Brightness(img).enhance(0.28)
        img = ImageEnhance.Contrast(img).enhance(0.85)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=86, optimize=True)
