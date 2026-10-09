"""Project folder: project.json (settings), images.json (prompts/seeds), images/*.jpg."""
import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path

from . import mdparse, planner

DEFAULTS = {
    "source": "",
    "output": "output.pdf",
    "title": "",
    "subtitle": "",
    "kicker": "",
    "tagline": "",
    "running_title": "",
    "chapter_label": "Kapitel",
    "language": "de",
    "chapter_level": 0,            # 0 = auto
    "appendix_patterns": ["^Anhang", "^Appendix", "^Änderungsprotokoll", "^Changelog"],
    "theme": {
        "bg": "#0b0c10",
        "ink": "#e6eaf0",
        "accent": "#2fe6ff",
        "accent2": "#ff2f92",
    },
    "layout": {
        "section_images": True,
        "page_texture": True,
        "justify": True,
        "font_size_pt": 9.6,
        "fill_gaps": False,        # fill large empty space after short blocks with extra images
        "filler_min_mm": 55,
    },
    "qc": {
        "enabled": False,          # let agy check every generated image (needs file read access)
        "auto_fix": True,          # rewrite the prompt and regenerate when an image does not fit
        "rounds": 1,
        "radical": False,          # after 3 failed tries rewrite the prompt radically: up to 3 concepts x 3 images
    },
    "content_type": "rulebook",    # rulebook (PnP) | document (README, tutorial, docs ...): changes the agy prompts
    "style_preset": "",
    "style_wish": "",
    "style": "",
    "negative": "worst quality, low quality, blurry, jpeg artifacts, bad anatomy, watermark, "
                "text, letters, signature, logo, deformed, extra limbs, lowres",
    "world": "",                   # short genre/setting summary written by the planner
    "llm": "agy",                  # prompt generator and image judge: agy | claude (Claude Code CLI)
    "agy": {"model": "gemini-3.8-flash-medium"},
    "claude": {"model": "sonnet"},
    "quota_wait": False,           # agy quota used up: wait for the reset and continue automatically
    "engine": "agy",               # agy (Antigravity CLI, no key) | gemini (Interactions API, GEMINI_API_KEY) | comfy
    "gemini": {
        "model": "gemini-3.1-flash-lite-image",
        "thinking_level": "low",   # low | high (high renders lettering and complex scenes better)
    },
    "comfy": {
        "url": "http://127.0.0.1:8188",
        "models_dir": "",          # ComfyUI models folder for model detection ("" = from comfy-cli)
        "workflow": "anima",       # anima | checkpoint | custom
        "unet": "",
        "clip": "qwen_3_06b_base.safetensors",
        "clip_type": "stable_diffusion",
        "vae": "qwen_image_vae.safetensors",
        "checkpoint": "",
        "custom_workflow": "",
        "steps": 28,
        "cfg": 4.0,
        "sampler": "euler",
        "scheduler": "simple",
    },
}

# image sizes per slot kind (width, height)
SIZES = {
    "cover": (1024, 1456),        # A4 portrait ratio, so the page shows the whole image
    "background": (1024, 1456),
    "chapter": (1600, 704),
    "section": (1280, 560),
}


def size_for(slot):
    """Generation size in pixels (multiples of 16)."""
    if slot.kind != "filler" or not slot.aspect:
        return SIZES[slot.kind]
    w, h = 1280, 1280 / slot.aspect
    if h > 1600:
        w, h = w * 1600 / h, 1600
    r16 = lambda v: max(384, int(round(v / 16)) * 16)
    return r16(w), r16(h)


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Slot:
    key: str
    kind: str        # cover | background | chapter | section
    title: str
    context_md: str  # text the image should illustrate
    chapter_key: str = ""
    aspect: float | None = None  # width / height, only for fillers


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = copy.deepcopy(DEFAULTS)
        self.manifest = {}
        cfg = self.root / "project.json"
        if cfg.exists():
            self.config = _merge(DEFAULTS, json.loads(cfg.read_text(encoding="utf-8")))
        man = self.root / "images.json"
        if man.exists():
            self.manifest = json.loads(man.read_text(encoding="utf-8"))
        planner.configure(self.config)

    # ---------- persistence ----------
    def save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "project.json").write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.save_manifest()

    def save_manifest(self):
        (self.root / "images.json").write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---------- paths ----------
    def path(self, rel):
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def source_path(self):
        return self.path(self.config["source"]) if self.config["source"] else None

    @property
    def output_path(self):
        return self.path(self.config["output"])

    @property
    def images_dir(self):
        return self.root / "images"

    @property
    def build_dir(self):
        return self.root / ".build"

    def image_path(self, key):
        return self.images_dir / f"{key}.jpg"

    def has_image(self, key):
        return self.image_path(key).exists()

    # ---------- document ----------
    def document(self):
        src = self.source_path
        if not src or not src.exists():
            raise FileNotFoundError(f"Quelldatei nicht gefunden: {src}")
        return mdparse.parse(src.read_text(encoding="utf-8"),
                             chapter_level=self.config.get("chapter_level") or None,
                             appendix_patterns=self.config["appendix_patterns"])

    def slots(self, doc=None):
        doc = doc or self.document()
        title = self.config["title"] or doc.title or "Regelwerk"
        overview = "\n".join(f"- {c.full_title}" for c in doc.chapters)
        out = [Slot("cover", "cover", f"Cover: {title}", overview),
               Slot("background", "background", "Seitenhintergrund", overview)]
        for ch in doc.chapters:
            if ch.appendix:
                continue
            out.append(Slot(ch.key, "chapter", ch.full_title,
                            ch.intro_md + "\n".join(s.title for s in ch.sections), ch.key))
            out += self._filler_slots(ch.key, ch.full_title, ch.intro_md, ch.key)
            for s in ch.sections:
                if self.config["layout"]["section_images"]:
                    out.append(Slot(s.key, "section", s.title, s.body_md, ch.key))
                out += self._filler_slots(s.key, s.title, s.body_md, ch.key)
        return out

    def _filler_slots(self, block_key, title, context, chapter_key):
        e = self.manifest.get(f"fill-{block_key}")
        if not (self.config["layout"].get("fill_gaps") and e and e.get("active")):
            return []
        aspect = e["w_mm"] / e["h_mm"] if e.get("h_mm") else None
        return [Slot(f"fill-{block_key}", "filler", f"Füllbild: {title}", context, chapter_key, aspect)]

    def sync_manifest(self, slots):
        """Ensure every slot has a manifest entry; returns the slots."""
        for s in slots:
            e = self.manifest.setdefault(s.key, {})
            e.setdefault("prompt", "")
            e.setdefault("seed", random.randint(1, 2**31 - 1))
            e["kind"], e["title"] = s.kind, s.title
        return slots

    def entry(self, key):
        return self.manifest.setdefault(key, {"prompt": "", "seed": random.randint(1, 2**31 - 1)})

    def full_prompt(self, key):
        e = self.entry(key)
        prompt = e.get("prompt", "").strip()
        style = self.config.get("style", "").strip()
        if style and e.get("include_style", True):
            prompt = f"{prompt}, {style}" if prompt else style
        return prompt

    def negative(self, key):
        return self.entry(key).get("negative") or self.config["negative"]


def create(root, source, output=None, content_type="rulebook"):
    """Create a new project folder for `source` (a Markdown file)."""
    p = Project(root)
    p.config["content_type"] = content_type
    p.config["source"] = str(Path(source).resolve())
    p.config["output"] = output or str(Path(root).resolve() / (Path(source).stem + ".pdf"))
    doc = p.document()
    p.config["title"] = doc.title or Path(source).stem
    p.config["running_title"] = (doc.title or Path(source).stem).split(":")[0].strip()
    p.sync_manifest(p.slots(doc))
    p.save()
    return p


def rekey(project, mapping):
    """Move images and manifest entries to new slot keys: mapping = {old_key: new_key}."""
    moved = []
    for old, new in mapping.items():
        if old == new or old not in project.manifest:
            continue
        project.manifest[new] = project.manifest.pop(old)
        if project.image_path(old).exists() and not project.image_path(new).exists():
            project.image_path(old).rename(project.image_path(new))
            moved.append((old, new))
    project.save_manifest()
    return moved
