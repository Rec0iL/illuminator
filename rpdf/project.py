"""Project folder: project.json (settings), images.json (prompts/seeds), images/*.jpg."""
import copy
import json
import random
import re
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
        "strictness": 3,           # 3 = streng (motif as in the prompt), 2 = mittel, 1 = locker
    },
    "content_type": "rulebook",    # rulebook (PnP) | document (README, tutorial, docs ...): changes the agy prompts
    "style_preset": "",
    "style_wish": "",
    "style": "",
    "negative": "worst quality, low quality, blurry, jpeg artifacts, bad anatomy, watermark, "
                "text, letters, signature, logo, deformed, extra limbs, lowres",
    "world": "",                   # short genre/setting summary written by the planner
    "summary": "",                 # short summary of the book, written on the first LLM use (see summarize())
    "translations": [],            # more languages: [{lang, source, output, title, ..., chapter_level}]
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

LANG_NAMES = {"de": "Deutsch", "en": "English", "pl": "Polski", "fr": "Français", "es": "Español",
              "it": "Italiano", "nl": "Nederlands", "pt": "Português", "cs": "Čeština", "sv": "Svenska",
              "da": "Dansk", "fi": "Suomi", "hu": "Magyar", "ro": "Română", "tr": "Türkçe", "ru": "Русский",
              "uk": "Українська", "ja": "日本語", "zh": "中文", "ko": "한국어"}
CHAPTER_LABELS = {"de": "Kapitel", "en": "Chapter", "pl": "Rozdział", "fr": "Chapitre", "es": "Capítulo",
                  "it": "Capitolo", "nl": "Hoofdstuk", "pt": "Capítulo", "cs": "Kapitola", "sv": "Kapitel",
                  "da": "Kapitel", "fi": "Luku", "hu": "Fejezet", "ro": "Capitolul", "tr": "Bölüm",
                  "ru": "Глава", "uk": "Розділ"}
# texts that differ per language; everything else (layout, colors, style, images) is shared
TEXT_KEYS = ("title", "subtitle", "kicker", "tagline", "running_title", "chapter_label")


def lang_label(code):
    return f"{code} – {LANG_NAMES[code]}" if code in LANG_NAMES else code


def clean_lang(code):
    """'PL ', 'pl_PL' -> 'pl'; only letters and '-' survive (the code ends up in file names)."""
    code = re.sub(r"[^a-zA-Z-]", "", (code or "").strip().replace("_", "-"))
    return code.lower()


# frequent short words per language, enough to tell the languages of a document apart
_STOPWORDS = {
    "en": "the and of to in is that for with as are this be on or by it from at an can you your will if not",
    "de": "der die das und ist nicht mit den von zu ein eine für auf im sich dem des wird auch als oder sind wenn",
    "pl": "i w nie na się z do to że jest jak po przez dla oraz lub od przy ale tak jego być może jeśli gdy",
    "fr": "le la les des et est que pour dans une un du en qui pas sur avec au ou par plus sont vous se",
    "es": "el la los las de y que en es un una para con por no se del al su como más lo si puede son",
    "it": "il la le di e che è in un una per con non si del della dei sono come più al da gli lo se",
    "nl": "de het een en van is dat op te zijn voor met niet aan er maar om ook als bij of je wordt",
    "pt": "o a os as de e que em um uma para com não se do da dos das no na por mais como ou ao é são",
}


def detect_language(text):
    """Best guess ('en', 'de', 'pl' ...) of the language of a text, or '' when it is too short to tell."""
    words = re.findall(r"[^\W\d_]+", (text or "").lower()[:20000])
    if len(words) < 40:
        return ""
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    scores = {lang: sum(counts.get(w, 0) for w in set(sw.split())) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    ordered = sorted(scores.values(), reverse=True)
    return best if ordered[0] >= 8 and ordered[0] > 1.15 * ordered[1] else ""


def filler_title(title, tag=""):
    return f"Füllbild [{tag}]: {title}" if tag else f"Füllbild: {title}"


FILLER_TITLE_RE = re.compile(r"^Füllbild(?: \[[^\]]*\])?: ")


def strip_filler_title(title):
    return FILLER_TITLE_RE.sub("", title)


# Review state of an image. The user's verdict ("review" in images.json) always wins over the AI check ("qc").
APPROVED, REJECTED = "approved", "rejected"     # set by the user
AI_OK, AI_FLAG = "ai_ok", "ai_flag"             # from the AI check
NEEDS_REDO = (REJECTED, AI_FLAG)

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


def align_keys(doc, base, tag):
    """Give the chapters and sections of a translation the keys (and appendix flag) of the blocks at the
    same position in the main-language document. Blocks without a counterpart get a language prefix,
    so they never pick up an image that belongs to another block."""
    for i, ch in enumerate(doc.chapters):
        b = base.chapters[i] if i < len(base.chapters) else None
        if b:
            ch.key, ch.appendix = b.key, b.appendix
        else:
            ch.key = f"{tag}-{ch.key}"
        for j, s in enumerate(ch.sections):
            s.key = b.sections[j].key if b and j < len(b.sections) else f"{tag}-{s.key}"


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
    lang: str = ""   # fillers: language tag of the translation they belong to ("" = main language)


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

    # ---------- languages ----------
    @property
    def primary_lang(self):
        return clean_lang(self.config.get("language")) or "de"

    def languages(self):
        """Language codes in build order: the main language first, then the translations."""
        return [self.primary_lang] + [t["lang"] for t in self.config["translations"]]

    def build_languages(self):
        """Languages a normal build produces: the main language and every enabled translation."""
        return [self.primary_lang] + [t["lang"] for t in self.config["translations"] if t.get("enabled", True)]

    def translation(self, lang):
        return next((t for t in self.config["translations"] if t["lang"] == lang), None)

    def lang_tag(self, lang=None):
        """"" for the main language, else the code of the translation (ValueError for an unknown code)."""
        if not lang or lang == self.primary_lang:
            return ""
        if not self.translation(lang):
            raise ValueError(f"Unbekannte Sprache: {lang}")
        return lang

    def settings_for(self, lang=None):
        """The project config as one language sees it: texts of the translation replace the main texts."""
        tag = self.lang_tag(lang)
        if not tag:
            return self.config
        t = self.translation(tag)
        c = dict(self.config)
        c.update({k: t.get(k, "") for k in TEXT_KEYS})
        c["language"] = tag
        return c

    def source_for(self, lang=None):
        tag = self.lang_tag(lang)
        if not tag:
            return self.source_path
        src = self.translation(tag).get("source")
        return self.path(src) if src else None

    def output_for(self, lang=None):
        tag = self.lang_tag(lang)
        if not tag:
            return self.output_path
        out = self.translation(tag).get("output")
        return self.path(out) if out else self.default_output(tag)

    def default_output(self, lang):
        out = self.output_path
        return out.with_name(f"{out.stem}.{lang}.pdf")

    def translation_dir(self, lang):
        return self.root / "translations" / lang

    def check_new_language(self, lang):
        """The cleaned code of a language that may be added; ValueError if it is empty or already there."""
        lang = clean_lang(lang)
        if not lang:
            raise ValueError("Sprachcode fehlt (z.B. en, de, pl).")
        if lang in self.languages():
            hint = (f" (Hauptsprache ist „{lang}“ – falls das nicht stimmt, unter Buch → Sprache ändern)"
                    if lang == self.primary_lang else "")
            raise ValueError(f"Die Sprache „{lang}“ gibt es schon in diesem Projekt{hint}.")
        return lang

    def add_translation(self, lang, source, **texts):
        """Register a translation (a Markdown file); returns its config entry. Texts default to the
        translated document's own title and the chapter word of the language."""
        lang = self.check_new_language(lang)
        t = {"lang": lang, "enabled": True, "source": str(Path(source).resolve()),
             "output": str(self.default_output(lang)), "chapter_level": 0}
        t.update({k: "" for k in TEXT_KEYS})
        t.update({k: v for k, v in texts.items() if k in TEXT_KEYS or k in ("output", "chapter_level")})
        self.config["translations"].append(t)
        try:
            doc = self.document(lang)
        except FileNotFoundError:
            doc = None
        title = (doc.title if doc else "") or Path(source).stem
        t["title"] = t["title"] or title
        t["running_title"] = t["running_title"] or title.split(":")[0].strip()
        t["chapter_label"] = t["chapter_label"] or CHAPTER_LABELS.get(lang, "")
        return t

    def remove_translation(self, lang):
        """Forget a translation and its filler slots. Its Markdown stays on disk, its filler images are
        moved to .build/removed/."""
        self.config["translations"] = [t for t in self.config["translations"] if t["lang"] != lang]
        for key in [k for k, e in self.manifest.items() if e.get("kind") == "filler" and e.get("lang") == lang]:
            self.trash_image(key)
            del self.manifest[key]

    def trash_image(self, key):
        """Move an image out of images/ into .build/removed/ (nothing is deleted). True if there was one."""
        import time
        path = self.image_path(key)
        if not path.exists():
            return False
        trash = self.build_dir / "removed"
        trash.mkdir(parents=True, exist_ok=True)
        path.replace(trash / f"{key}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        return True

    def filler_key(self, block_key, lang=None):
        tag = self.lang_tag(lang)
        return f"fill-{tag}-{block_key}" if tag else f"fill-{block_key}"

    def alignment_issues(self, lang):
        """Why the images of the main language may not fit a translation's blocks (empty = same structure)."""
        base, doc = self.document(), self.document(lang)
        issues = []
        if len(doc.chapters) != len(base.chapters):
            issues.append(f"{len(doc.chapters)} Kapitel statt {len(base.chapters)}")
        for i, (a, b) in enumerate(zip(base.chapters, doc.chapters), 1):
            if len(a.sections) != len(b.sections):
                issues.append(f"Kapitel {i} „{b.title}“: {len(b.sections)} Abschnitte statt {len(a.sections)} "
                              f"(„{a.title}“)")
        return issues

    # ---------- document ----------
    def document(self, lang=None):
        """Parsed text of one language. The blocks of a translation carry the keys of the matching
        main-language blocks (same position), so both languages share their topic images."""
        tag = self.lang_tag(lang)
        src = self.source_for(tag)
        if not src or not src.exists():
            raise FileNotFoundError(f"Quelldatei nicht gefunden: {src}")
        level = (self.translation(tag).get("chapter_level") if tag else self.config.get("chapter_level")) or None
        doc = mdparse.parse(src.read_text(encoding="utf-8"), chapter_level=level,
                            appendix_patterns=self.config["appendix_patterns"])
        if tag:
            align_keys(doc, self.document(), tag)
        return doc

    def slots(self, doc=None, lang=None):
        """Image slots. Cover, background, chapter and section images belong to the main-language
        text and are shared by all languages; `lang` adds that language's filler images (`doc` is the
        document of `lang`, default: parsed here)."""
        tag = self.lang_tag(lang)
        ldoc = doc or self.document(tag)
        pdoc = self.document() if tag else ldoc
        fill = {}
        for ch in ldoc.chapters:
            if ch.appendix:
                continue
            fill[ch.key] = self._filler_slots(ch.key, ch.full_title, ch.intro_md, ch.key, tag)
            for s in ch.sections:
                fill[s.key] = self._filler_slots(s.key, s.title, s.body_md, ch.key, tag)
        title = self.config["title"] or pdoc.title or "Regelwerk"
        overview = "\n".join(f"- {c.full_title}" for c in pdoc.chapters)
        out = [Slot("cover", "cover", f"Cover: {title}", overview),
               Slot("background", "background", "Seitenhintergrund", overview)]
        for ch in pdoc.chapters:
            if ch.appendix:
                continue
            out.append(Slot(ch.key, "chapter", ch.full_title,
                            ch.intro_md + "\n".join(s.title for s in ch.sections), ch.key))
            out += fill.pop(ch.key, [])
            for s in ch.sections:
                if self.config["layout"]["section_images"]:
                    out.append(Slot(s.key, "section", s.title, s.body_md, ch.key))
                out += fill.pop(s.key, [])
        for rest in fill.values():      # fillers behind blocks that only the translation has
            out += rest
        return out

    def all_slots(self):
        """Topic slots once, plus the filler slots of every enabled language."""
        out = self.slots()
        for t in self.config["translations"]:
            if not t.get("enabled", True):
                continue
            try:
                out += [s for s in self.slots(lang=t["lang"]) if s.kind == "filler"]
            except FileNotFoundError:
                continue      # missing translation file: reported when that language is built
        return out

    def slot(self, key):
        return next(s for s in self.all_slots() if s.key == key)

    def chapters_by_key(self):
        """Chapters of all languages by block key (for planners that need the text of a slot's chapter)."""
        out = {}
        for lang in self.languages():
            try:
                out.update({c.key: c for c in self.document(lang).chapters if c.key not in out})
            except FileNotFoundError:
                continue
        return out

    def _filler_slots(self, block_key, title, context, chapter_key, tag=""):
        key = self.filler_key(block_key, tag)
        e = self.manifest.get(key)
        if not (self.config["layout"].get("fill_gaps") and e and e.get("active")):
            return []
        aspect = e["w_mm"] / e["h_mm"] if e.get("h_mm") else None
        return [Slot(key, "filler", filler_title(title, tag), context, chapter_key, aspect, tag)]

    def sync_manifest(self, slots):
        """Ensure every slot has a manifest entry; returns the slots."""
        for s in slots:
            e = self.manifest.setdefault(s.key, {})
            e.setdefault("prompt", "")
            e.setdefault("seed", random.randint(1, 2**31 - 1))
            e["kind"], e["title"] = s.kind, s.title
            if s.kind == "filler":
                e["lang"] = s.lang
        return slots

    def entry(self, key):
        return self.manifest.setdefault(key, {"prompt": "", "seed": random.randint(1, 2**31 - 1)})

    # ---------- review ----------
    def review(self, key):
        """The user's verdict: APPROVED, REJECTED or ""."""
        v = self.manifest.get(key, {}).get("review")
        return v if v in (APPROVED, REJECTED) else ""

    def set_review(self, key, verdict):
        e = self.entry(key)
        if verdict in (APPROVED, REJECTED):
            e["review"] = verdict
        else:
            e.pop("review", None)

    def status(self, key):
        """APPROVED / REJECTED (user), AI_OK / AI_FLAG (AI check) or "" (unreviewed or no image)."""
        if not self.has_image(key):
            return ""
        if self.review(key):
            return self.review(key)
        qc = self.manifest.get(key, {}).get("qc")
        return (AI_OK if qc["fits"] else AI_FLAG) if qc else ""

    def reset_review(self, key):
        """A new image replaced the old one: neither the user's nor the AI's verdict applies any more."""
        e = self.entry(key)
        e.pop("qc", None)
        e.pop("review", None)

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
    p.config["language"] = detect_language(Path(source).read_text(encoding="utf-8")) or p.config["language"]
    p.config["chapter_label"] = CHAPTER_LABELS.get(p.config["language"], p.config["chapter_label"])
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
