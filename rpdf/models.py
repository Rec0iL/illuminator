"""Know which settings a ComfyUI model needs.

The model family is read from the safetensors header (tensor names), so it
does not depend on file names. Each family brings the workflow type, text
encoder, VAE and sampler settings it was made for. Settings that worked for a
specific file are remembered across projects in ~/.config/rulebook-pdf/models.json.
"""
import configparser
import json
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

USER_FILE = Path.home() / ".config" / "rulebook-pdf" / "models.json"
ENGINE_KEYS = ("workflow", "clip", "clip_type", "vae", "steps", "cfg", "sampler", "scheduler")

FAST_HINT = re.compile(r"(?i)lightning|turbo|hyper|dmd|lcm|schnell|flash")


@dataclass
class Family:
    key: str
    label: str
    usable: bool = True
    note: str = ""
    settings: dict = field(default_factory=dict)        # engine settings
    fast_settings: dict = field(default_factory=dict)   # overrides for distilled/turbo variants
    downloads: dict = field(default_factory=dict)       # component file -> URL
    prompt_style: str = "tags"                          # tags | natural


FAMILIES = {
    "krea2": Family(
        "krea2", "Krea 2", note="Turbo-Modell, wenig Steps, CFG 1 (Negativ-Prompt wirkt nicht).",
        settings=dict(workflow="anima", clip="qwen3vl_4b_fp8_scaled.safetensors", clip_type="krea2",
                      vae="qwen_image_vae.safetensors", steps=8, cfg=1.0, sampler="euler", scheduler="simple"),
        downloads={
            "qwen3vl_4b_fp8_scaled.safetensors":
                "https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors",
            "qwen_image_vae.safetensors":
                "https://huggingface.co/Comfy-Org/Krea-2/resolve/main/vae/qwen_image_vae.safetensors"},
        prompt_style="natural"),
    "anima": Family(
        "anima", "Anima",
        settings=dict(workflow="anima", clip="qwen_3_06b_base.safetensors", clip_type="stable_diffusion",
                      vae="qwen_image_vae.safetensors", steps=28, cfg=4.0, sampler="euler", scheduler="simple"),
        downloads={
            "qwen_3_06b_base.safetensors":
                "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/text_encoders/qwen_3_06b_base.safetensors",
            "qwen_image_vae.safetensors":
                "https://huggingface.co/circlestone-labs/Anima/resolve/main/split_files/vae/qwen_image_vae.safetensors"}),
    "sdxl": Family(
        "sdxl", "SDXL / Illustrious / Pony (Checkpoint)",
        settings=dict(workflow="checkpoint", steps=28, cfg=6.0, sampler="dpmpp_2m", scheduler="karras"),
        fast_settings=dict(steps=8, cfg=2.0, sampler="dpmpp_sde", scheduler="karras")),
    "sd15": Family(
        "sd15", "SD 1.5 (Checkpoint)",
        settings=dict(workflow="checkpoint", steps=25, cfg=7.0, sampler="dpmpp_2m", scheduler="karras"),
        fast_settings=dict(steps=8, cfg=2.0, sampler="dpmpp_sde", scheduler="karras")),
    "flux_ckpt": Family(
        "flux_ckpt", "Flux (Checkpoint mit Encoder und VAE)", note="CFG 1, Negativ-Prompt wirkt nicht.",
        settings=dict(workflow="checkpoint", steps=20, cfg=1.0, sampler="euler", scheduler="simple"),
        fast_settings=dict(steps=4), prompt_style="natural"),
    "flux_kontext": Family("flux_kontext", "Flux Kontext", usable=False,
                           note="Bildbearbeitungsmodell, braucht ein Eingabebild – nicht für Text-zu-Bild."),
    "flux_unet": Family("flux_unet", "Flux (nur Diffusionsmodell)", usable=False,
                        note="Braucht DualCLIP + Guidance – mit „Eigener Workflow“ (API-JSON) nutzen."),
    "wan": Family("wan", "Wan (Video)", usable=False, note="Videomodell – nicht für Einzelbilder."),
    "sdxl_unet": Family("sdxl_unet", "SDXL (nur UNet)", usable=False,
                        note="Ohne Text-Encoder und VAE – das komplette Checkpoint verwenden."),
    "other": Family("other", "kein Bildmodell", usable=False,
                    note="Kein Text-zu-Bild-Modell (z.B. Erkennung, Pose, Segmentierung)."),
    "unknown": Family("unknown", "unbekannt", note="Bauart nicht erkannt – Einstellungen selbst wählen."),
}


def detect_family(header_keys, filename=""):
    keys = header_keys
    has = lambda s: any(s in k for k in keys)
    tops = {k.split(".")[0] for k in keys}
    if has("txtfusion"):
        return "krea2"
    if has("adaln_modulation_cross_attn") or has("llm_adapter"):
        return "anima"
    if has("double_blocks"):
        if "kontext" in filename.lower():
            return "flux_kontext"
        return "flux_ckpt" if ("vae" in tops or "text_encoders" in tops) else "flux_unet"
    if has("conditioner.embedders"):
        return "sdxl"
    if has("cond_stage_model") and has("input_blocks"):
        return "sd15"
    if has("patch_embedding") and has("text_embedding"):
        return "wan"
    if has("input_blocks") and has("label_emb"):
        return "sdxl_unet"
    if {"detector", "tracker"} & tops or ("first_stage_model" in tops and not has("cond_stage_model")):
        return "other"
    return "unknown"


def read_header_keys(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 200_000_000:
            raise ValueError("Kopf zu groß")
        return [k for k in json.loads(f.read(n)) if k != "__metadata__"]


def default_models_dir():
    """ComfyUI models folder from comfy-cli's config (the workspace Pinokio uses)."""
    cfg = Path.home() / ".config" / "comfy-cli" / "config.ini"
    if cfg.exists():
        cp = configparser.ConfigParser()
        cp.read(cfg)
        ws = cp.get("DEFAULT", "default_workspace", fallback="")
        if ws and (Path(ws) / "models").is_dir():
            return str(Path(ws) / "models")
    return ""


@dataclass
class ModelInfo:
    name: str
    loader: str          # unet | checkpoint
    path: str | None
    family: Family
    fast: bool

    @property
    def label(self):
        return f"{self.name}  —  {self.family.label}{' (Turbo)' if self.fast else ''}"


def scan(models_dir, comfy_lists):
    """All models ComfyUI offers, with their detected family. Checkpoints that are
    only a diffusion model are offered as diffusion models (they need linking)."""
    out, seen = [], set()
    candidates = [(n, "unet", "diffusion_models") for n in comfy_lists.get("unet", [])]
    candidates += [(n, "checkpoint", "checkpoints") for n in comfy_lists.get("checkpoint", [])]
    cache = {}
    for name, loader, folder in candidates:
        path = None
        if models_dir:
            p = Path(models_dir) / folder / name
            if p.exists():
                path = os.path.realpath(p)
        if path and path in cache:
            fam_key = cache[path]
        elif path and name.endswith(".safetensors"):
            try:
                fam_key = detect_family(read_header_keys(path), name)
            except Exception:
                fam_key = "unknown"
        else:
            fam_key = "unknown"
        if path:
            cache[path] = fam_key
        family = FAMILIES[fam_key]
        unet_only = fam_key in ("krea2", "anima")
        # a full checkpoint is loaded as checkpoint, a bare diffusion model through UNETLoader
        if (unet_only and loader == "checkpoint") or (not unet_only and fam_key in ("sdxl", "sd15", "flux_ckpt")
                                                      and loader == "unet"):
            continue
        if (name, fam_key) in seen:
            continue
        seen.add((name, fam_key))
        out.append(ModelInfo(name, loader, path, family, bool(FAST_HINT.search(name))))
    # bare diffusion models that only sit in checkpoints/ (need a link into diffusion_models/)
    unet_names = set(comfy_lists.get("unet", []))
    for name in comfy_lists.get("checkpoint", []):
        p = Path(models_dir) / "checkpoints" / name if models_dir else None
        if name in unet_names or not (p and p.exists()):
            continue
        real = os.path.realpath(p)
        fam_key = cache.get(real) or "unknown"
        if fam_key in ("krea2", "anima"):
            out.append(ModelInfo(name, "unet-missing", real, FAMILIES[fam_key], bool(FAST_HINT.search(name))))
    order = {"unet": 0, "checkpoint": 0, "unet-missing": 1}
    out.sort(key=lambda m: (not m.family.usable, order[m.loader], m.name.lower()))
    return out


def recommended(info):
    s = dict(info.family.settings)
    if info.fast:
        s.update(info.family.fast_settings)
    return s


def link_into_diffusion_models(models_dir, name):
    src = Path(models_dir) / "checkpoints" / name
    dst = Path(models_dir) / "diffusion_models" / name
    if not dst.exists():
        dst.symlink_to(Path("..") / "checkpoints" / name)
    return dst


def missing_components(info, comfy_lists):
    s = info.family.settings
    missing = []
    if s.get("clip") and s["clip"] not in comfy_lists.get("clip", []):
        missing.append(s["clip"])
    if s.get("vae") and s["vae"] not in comfy_lists.get("vae", []):
        missing.append(s["vae"])
    return [(m, info.family.downloads.get(m, "")) for m in missing]


# ---------- per-file memory ----------
def load_memory():
    try:
        return json.loads(USER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def remember(model_name, comfy_cfg):
    mem = load_memory()
    mem[model_name] = {k: comfy_cfg.get(k) for k in ENGINE_KEYS}
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_FILE.write_text(json.dumps(mem, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def remembered(model_name):
    return load_memory().get(model_name)


def selected_model(comfy_cfg):
    kind = comfy_cfg.get("workflow")
    return comfy_cfg.get("unet") if kind == "anima" else comfy_cfg.get("checkpoint") if kind == "checkpoint" else None
