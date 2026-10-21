"""Style presets: an image-prompt suffix plus matching layout accent colors."""

PRESETS = {
    "Anime Action": {
        "style": "cinematic anime action illustration, rain-soaked neon city, dark gritty atmosphere, "
                 "bold inked linework, cel shading, extreme high contrast, magenta and cyan rim light, "
                 "wide cinematic composition, masterpiece, best quality",
        "accent": "#2fe6ff", "accent2": "#ff2f92",
    },
    "Film Noir": {
        "style": "film noir illustration, black and white with deep shadows, venetian blind light, "
                 "cigarette smoke, 1940s, chiaroscuro, high contrast ink, grainy, dramatic low angle, "
                 "masterpiece, best quality",
        "accent": "#e8e4d8", "accent2": "#c0392b",
    },
    "Dark Fantasy": {
        "style": "dark fantasy oil painting, muted earthy colors, dramatic candle and torch light, "
                 "gothic atmosphere, painterly brushwork, detailed armor and cloth, fog, "
                 "masterpiece, best quality",
        "accent": "#d4a35a", "accent2": "#a8322d",
    },
    "Klassische Fantasy (Aquarell)": {
        "style": "classic fantasy rulebook illustration, ink and watercolor, warm parchment tones, "
                 "soft natural light, detailed linework, storybook atmosphere, masterpiece, best quality",
        "accent": "#e0b062", "accent2": "#5fa36b",
    },
    "Comic / Graphic Novel": {
        "style": "american comic book art, bold black ink outlines, flat saturated colors, halftone dots, "
                 "dynamic foreshortening, speed lines, graphic novel panel composition, masterpiece",
        "accent": "#ffd23f", "accent2": "#ee4266",
    },
    "Cyberpunk": {
        "style": "cyberpunk concept art, neon signage glow, holographic haze, chrome and wet asphalt "
                 "reflections, teal and purple palette, volumetric fog, highly detailed, masterpiece",
        "accent": "#00f0ff", "accent2": "#b14aed",
    },
    "Sci-Fi Concept Art": {
        "style": "hard science fiction concept art, matte painting, vast scale, clean industrial design, "
                 "cold blue lighting with orange accents, cinematic, highly detailed, masterpiece",
        "accent": "#6ec6ff", "accent2": "#ff8a3d",
    },
    "Gothic Horror": {
        "style": "gothic horror illustration, desaturated palette with blood red accents, moonlight, "
                 "heavy shadows, unsettling atmosphere, etching-like linework, masterpiece, best quality",
        "accent": "#c9c3b6", "accent2": "#b3121b",
    },
    "Retro Pulp (50er)": {
        "style": "1950s pulp magazine cover art, gouache painting, saturated retro colors, heroic poses, "
                 "dramatic lighting, vintage print texture, masterpiece",
        "accent": "#ffcf56", "accent2": "#e4572e",
    },
    "Sumi-e / Tusche": {
        "style": "japanese sumi-e ink wash painting, expressive brush strokes, rice paper texture, "
                 "minimal red accent color, negative space, elegant motion, masterpiece",
        "accent": "#e8e1d3", "accent2": "#c8102e",
    },
}

DOC_PRESETS = {
    "Technische Illustration": {
        "style": "clean technical illustration, precise linework, muted blue-gray palette, soft studio lighting, "
                 "isometric details, blueprint-like clarity, highly detailed, high quality",
        "accent": "#4aa3ff", "accent2": "#ffb347",
    },
    "Flat Vector / Infografik": {
        "style": "flat vector illustration, geometric shapes, limited bright palette, clean edges, soft gradients, "
                 "modern editorial style, simple readable composition",
        "accent": "#3ddc97", "accent2": "#ff6b6b",
    },
    "Isometrisch (3D)": {
        "style": "isometric 3d illustration, soft pastel colors, clean smooth shading, miniature diorama look, "
                 "soft shadows, tidy composition",
        "accent": "#7bdff2", "accent2": "#f7a1c4",
    },
    "Blaupause": {
        "style": "blueprint style technical drawing, white linework on deep blue, grid paper, engineering sketch, "
                 "annotated look without readable text, precise",
        "accent": "#9ad1ff", "accent2": "#ffd166",
    },
    "Aquarell-Skizze": {
        "style": "loose watercolor sketch with fine ink linework, warm paper texture, light airy colors, "
                 "hand-drawn feel, soft edges",
        "accent": "#e0b062", "accent2": "#5fa36b",
    },
    "Editorial-Foto": {
        "style": "editorial photography, natural light, shallow depth of field, realistic, high detail, "
                 "calm professional atmosphere",
        "accent": "#f2c14e", "accent2": "#5bc0be",
    },
    "Minimalistisch": {
        "style": "minimalist illustration, large calm shapes, two-tone palette, generous negative space, "
                 "subtle grain, elegant composition",
        "accent": "#ff8a5b", "accent2": "#6ec6ff",
    },
    "Retro Pixel Art": {
        "style": "16-bit pixel art, retro aesthetic, clean pixel outlines, limited vibrant palette, "
                 "crisp dithering, pixel-perfect rendering",
        "accent": "#ffd23f", "accent2": "#3bceac",
    },
}

CONTENT_TYPES = {"rulebook": "PnP-Regelwerk", "document": "Anderes Dokument (Readme, Tutorial, Doku …)"}


def presets_for(kind):
    """Style presets that fit the content type."""
    return DOC_PRESETS if kind == "document" else PRESETS


CUSTOM = "Eigener Stil"


def model_family(comfy_cfg):
    """Detected family of the selected model (reads the file header when the models folder is known)."""
    from pathlib import Path
    from . import models
    name = models.selected_model(comfy_cfg) or ""
    folder = "checkpoints" if comfy_cfg.get("workflow") == "checkpoint" else "diffusion_models"
    root = comfy_cfg.get("models_dir") or models.default_models_dir()
    path = Path(root) / folder / name if root and name else None
    if path and path.exists():
        try:
            return models.FAMILIES[models.detect_family(models.read_header_keys(path), name)], name
        except Exception:
            pass
    return models.FAMILIES["unknown"], name


def model_hint(config):
    """Tell agy what kind of prompt the selected image model understands (config = project config)."""
    if config.get("engine") in ("agy", "gemini"):
        name = config.get("gemini", {}).get("model") or "Gemini image model (via agy)"
        return (f"Image model: {name} (Google Gemini image model; understands rich natural-language scene "
                "descriptions, composition, lighting and style words; do NOT use Danbooru tags, quality tags like "
                "'masterpiece', or weighted keywords; it follows negations poorly, so describe what should be there; "
                "it renders text well, so explicitly forbid lettering when none is wanted).")
    comfy_cfg = config["comfy"]
    family, name = model_family(comfy_cfg)
    low = name.lower()
    if family.key == "anima" or any(k in low for k in ("anima", "illustrious", "noob", "pony", "animagine")):
        return (f"Image model: {name or 'anime model'} (anime-focused; understands Danbooru-style tags, "
                "artist and franchise names as tags, and short natural-language phrases).")
    if family.key == "krea2":
        return (f"Image model: {name} (Krea 2, a modern model with a large language text encoder; "
                "understands detailed natural-language descriptions, medium and lighting words; "
                "quality tags like 'masterpiece' are useless; negative prompts are ignored at CFG 1).")
    if family.key == "flux_ckpt" or "flux" in low or "qwen" in low:
        return f"Image model: {name} (understands detailed natural-language descriptions; no negative prompt)."
    if family.key in ("sdxl", "sd15"):
        return f"Image model: {name} ({family.label}; works best with comma-separated tags and weighted keywords)."
    return f"Image model: {name or 'Stable Diffusion checkpoint'} (works best with comma-separated tags)."


# ---------- palette checks ----------
def parse_hex(value):
    """'#abc', 'aabbcc' -> (r, g, b), or None."""
    h = str(value or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else None
    except ValueError:
        return None


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _luminance(rgb):
    lin = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


luminance = _luminance


def contrast(a, b):
    """WCAG contrast ratio of two (r, g, b) colors."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure_contrast(color, bg, minimum):
    """Lighten `color` step by step until it reads on the dark page color `bg` (the PDF theme is dark)."""
    rgb = color
    for step in range(21):
        if contrast(rgb, bg) >= minimum:
            break
        rgb = tuple(c + (255 - c) * 0.05 for c in rgb)
    return to_hex(rgb)


MAX_BG_LIGHTNESS = 0.06      # relative luminance: the layout (white headings, dark scrims) needs a dark page


def fit_palette(palette, fallback):
    """Take a proposed palette {bg, ink, accent, accent2}, repair what cannot work on a dark page
    (not a hex color, too light a background, too little contrast) and fill gaps from `fallback`."""
    rgb = {k: parse_hex(palette.get(k)) or parse_hex(fallback[k]) for k in ("bg", "ink", "accent", "accent2")}
    bg = rgb["bg"]
    while _luminance(bg) > MAX_BG_LIGHTNESS:
        bg = tuple(c * 0.85 for c in bg)
    out = {"bg": to_hex(bg)}
    bg = parse_hex(out["bg"])
    out["ink"] = ensure_contrast(rgb["ink"], bg, 7.0)
    out["accent"] = ensure_contrast(rgb["accent"], bg, 4.5)
    out["accent2"] = ensure_contrast(rgb["accent2"], bg, 4.5)
    return out


def readable_accents(result, theme):
    """Repair the accent colors an LLM proposed (keys accent, accent2) so that they read on the
    theme's page color; a value that is not a color is replaced by the current one."""
    bg = parse_hex(theme["bg"])
    for key in ("accent", "accent2"):
        rgb = parse_hex(result.get(key)) or parse_hex(theme[key])
        result[key] = ensure_contrast(rgb, bg, 4.5)
    return result
