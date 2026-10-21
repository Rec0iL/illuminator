"""The page background: dim the generated texture so the text stays readable.

The dimming happens when the PDF is built, never in the stored image: images/background.jpg is the
picture as the image engine made it (that is what the image check and the editor see), and the layout
uses a dimmed copy. How strongly is calculated from the picture and the text colors, then the user can
shift it with the slider in the Book tab (layout.bg_dim_adjust).

Dark pages (light text, the default) are dimmed. If the user swaps the colors (dark text on a light page),
the picture is lightened towards white instead; the slider always means "brighter to the right".
"""
from PIL import Image, ImageEnhance, ImageFilter

from . import styles

INK_CONTRAST = 7.0         # body text on the page must reach this contrast ratio ...
ACCENT_CONTRAST = 4.5      # ... and headings this one
PERCENTILE = 0.97          # ... measured on the brightest 3 % of the picture, not on single highlights
SATURATION, CONTRAST, BLUR_PX = 0.35, 0.85, 1.2    # the look the page texture always had (BLUR_PX at 1024 px)
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 0.04, 2.5
MAX_LIGHTEN = 0.97         # light pages: at most this share of white is mixed in
PROBE = 160                # the optimum is searched on a picture of this size
ADJUST_RANGE = 100         # slider: -100 … +100, every 50 steps double or halve the brightness


def restore_original(img):
    """Approximate the picture as it was before the old fixed darkening that earlier versions burned into the
    stored file (colour x0.35, brightness x0.28, contrast x0.85, blur). The steps are undone in reverse; for a
    soft texture that is close enough, only fine gradients show some banding when lightened a lot."""
    img = ImageEnhance.Contrast(img.convert("RGB")).enhance(1 / 0.85)
    img = ImageEnhance.Brightness(img).enhance(1 / 0.28)
    return ImageEnhance.Color(img).enhance(1 / 0.35)


def page_is_light(theme):
    """True if the text is darker than the page color (the user swapped the defaults)."""
    bg, ink = styles.parse_hex(theme.get("bg")), styles.parse_hex(theme.get("ink"))
    return bool(bg and ink) and styles.luminance(bg) > styles.luminance(ink)


def _look(img, level, light=False, blur=True):
    """Desaturate, then dim (level = brightness factor) or, on light pages, lighten (level = share of white)."""
    img = ImageEnhance.Color(img).enhance(SATURATION)
    if light:
        img = Image.blend(img, Image.new("RGB", img.size, (255, 255, 255)), level)
    else:
        img = ImageEnhance.Brightness(img).enhance(level)
    img = ImageEnhance.Contrast(img).enhance(CONTRAST)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(BLUR_PX * img.width / 1024))
    return img


def _edge(img, brightest=True):
    """Relative luminance (0 … 1) that only the brightest (or darkest) 3 % of the picture exceed (fall below)."""
    data = img.convert("RGB").tobytes()
    values = sorted(styles.luminance(px) for px in zip(data[0::3], data[1::3], data[2::3]))
    return values[min(len(values) - 1, int(len(values) * (PERCENTILE if brightest else 1 - PERCENTILE)))]


def _bright_edge(img):
    return _edge(img, True)


def target_luminance(theme):
    """Brightest luminance the page may have so that ink and headings keep their contrast."""
    limits = []
    for key, ratio in (("ink", INK_CONTRAST), ("accent", ACCENT_CONTRAST)):
        rgb = styles.parse_hex(theme.get(key))
        if rgb:
            limits.append((styles.luminance(rgb) + 0.05) / ratio - 0.05)
    return max(0.0, min(limits)) if limits else 0.05


def lightest_needed(theme):
    """Light pages: darkest luminance the picture may have so that the (dark) ink and headings keep their contrast."""
    needs = []
    for key, ratio in (("ink", INK_CONTRAST), ("accent", ACCENT_CONTRAST)):
        rgb = styles.parse_hex(theme.get(key))
        if rgb and (key == "ink" or styles.luminance(rgb) < 0.3):    # a pale accent is not meant as text here
            needs.append(ratio * (styles.luminance(rgb) + 0.05) - 0.05)
    return min(0.95, max(needs)) if needs else 0.5


def optimal_lightening(img, theme):
    """Light pages: the smallest share of white (0 … 0.97) that lifts the darkest part of the picture high enough."""
    probe = img.convert("RGB")
    probe.thumbnail((PROBE, PROBE))
    goal = min(0.95, lightest_needed(theme) * 1.1)     # a little reserve, like the dark case
    if _edge(_look(probe, 0.0, True, blur=False), False) >= goal:
        return 0.0
    lo, hi = 0.0, MAX_LIGHTEN
    for _ in range(14):
        mid = (lo + hi) / 2
        if _edge(_look(probe, mid, True, blur=False), False) >= goal:
            hi = mid
        else:
            lo = mid
    return hi


def optimal_brightness(img, theme):
    """The brightest version (factor 0.04 … 1.0) of the picture that still satisfies target_luminance.
    A picture that is dark enough already stays as it is (1.0)."""
    probe = img.convert("RGB")
    probe.thumbnail((PROBE, PROBE))
    goal = target_luminance(theme) * 0.9      # a little reserve: the probe is smoother than the page
    if _bright_edge(_look(probe, 1.0, blur=False)) <= goal:
        return 1.0
    lo, hi = MIN_BRIGHTNESS, 1.0
    for _ in range(14):
        mid = (lo + hi) / 2
        if _bright_edge(_look(probe, mid, blur=False)) <= goal:
            lo = mid
        else:
            hi = mid
    return lo


def brightness_for(optimal, adjust):
    """Slider position -> brightness factor: 0 = the optimum, ±50 = half/double."""
    return max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, optimal * 2 ** (adjust / 50)))


def lightening_for(optimal, adjust):
    """Light pages, slider position -> share of white: right = brighter = more white (every 50 steps the
    remaining share of picture halves or doubles)."""
    return max(0.0, min(MAX_LIGHTEN, 1 - (1 - optimal) * 2 ** (-adjust / 50)))


def dim(img, theme, adjust=0):
    """The stored picture made fit for the page and {mode, optimal, brightness} (the values used): dark pages are
    dimmed (brightness factor), light pages lightened (share of white)."""
    img = img.convert("RGB")
    light = page_is_light(theme)
    optimal = optimal_lightening(img, theme) if light else optimal_brightness(img, theme)
    level = lightening_for(optimal, adjust) if light else brightness_for(optimal, adjust)
    return _look(img, level, light), {"mode": "light" if light else "dark", "optimal": optimal, "brightness": level}


def is_raw(project):
    """True if images/background.jpg is the engine's original; older projects hold a picture that was
    already darkened when it was saved (restore_original() brings it back as well as it goes)."""
    return project.has_image("background") and bool(project.entry("background").get("raw"))


def page_image(project, log=None):
    """file:// URI of the background for the pages, or None. Dimmed from the original at build time."""
    if not project.has_image("background"):
        return None
    path = project.image_path("background")
    adjust = int(project.config["layout"].get("bg_dim_adjust", 0))
    with Image.open(path) as im:
        original = im.convert("RGB") if is_raw(project) else restore_original(im)
        out, info = dim(original, project.config["theme"], adjust)
    if log and not is_raw(project):
        log("Hintergrund: altes, schon abgedunkelt gespeichertes Bild – das Original wird geschätzt, für die "
            "beste Qualität neu erzeugen.")
    target = project.build_dir / "background-page.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target, "JPEG", quality=90)
    if log:
        if info["mode"] == "light":
            log(f"Hintergrund (helle Seite): {info['brightness']:.0%} aufgehellt (berechnetes Optimum "
                f"{info['optimal']:.0%}, Regler {adjust:+d}).")
        else:
            log(f"Hintergrund: Helligkeit {info['brightness']:.2f} (berechnetes Optimum {info['optimal']:.2f}, "
                f"Regler {adjust:+d}).")
    return target.as_uri()


def _rgba(rgb, alpha):
    return "rgba(%d,%d,%d,%s)" % (*(round(c) for c in rgb), alpha)


def page_colors(project, textured):
    """Surface colors that sit on the page, as CSS values: table and code cards, rules, stripes, the color of
    bold text and titles, and the tint the banner and cover fades use. The cards follow the page picture
    (so they follow the brightness slider too), but always keep 7.5:1 contrast to the text color; with dark text
    on a light page they are light, with light text dark."""
    theme = project.config["theme"]
    bg = styles.parse_hex(theme["bg"]) or (11, 12, 16)
    ink = styles.parse_hex(theme["ink"]) or (230, 234, 240)
    base = bg
    target = project.build_dir / "background-page.jpg"
    if textured and target.exists():
        with Image.open(target) as im:
            avg = im.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
        base = tuple((a + b) / 2 for a, b in zip(avg, bg))
    card = styles.ensure_contrast_with(base, ink, 7.5)
    light = page_is_light(theme)
    return {"card": _rgba(card, 0.88), "rule": _rgba(ink, 0.14), "stripe": _rgba(ink, 0.05),
            "strong": "#000000" if light else "#ffffff", "em": theme["ink"] if light else "#d7dce4",
            "scrim0": _rgba(bg, 0), "scrim72": _rgba(bg, 0.72)}


def preview(project, adjust, theme=None, width=170):
    """Small picture for the slider's preview and the values used (see dim); None if there is no background."""
    if not project.has_image("background"):
        return None
    with Image.open(project.image_path("background")) as im:
        small = im.convert("RGB")
        small.thumbnail((width * 2, width * 3))
    if not is_raw(project):
        small = restore_original(small)
    return dim(small, theme or project.config["theme"], adjust)
