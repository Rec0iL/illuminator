"""The page background: dim the generated texture so the text stays readable.

The dimming happens when the PDF is built, never in the stored image: images/background.jpg is the
picture as the image engine made it (that is what the image check and the editor see), and the layout
uses a dimmed copy. How strongly is calculated from the picture and the text colors, then the user can
shift it with the slider in the Book tab (layout.bg_dim_adjust).
"""
from PIL import Image, ImageEnhance, ImageFilter

from . import styles

INK_CONTRAST = 7.0         # body text on the page must reach this contrast ratio ...
ACCENT_CONTRAST = 4.5      # ... and headings this one
PERCENTILE = 0.97          # ... measured on the brightest 3 % of the picture, not on single highlights
SATURATION, CONTRAST, BLUR_PX = 0.35, 0.85, 1.2    # the look the page texture always had (BLUR_PX at 1024 px)
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 0.04, 1.5
PROBE = 160                # the optimum is searched on a picture of this size
ADJUST_RANGE = 100         # slider: -100 … +100, every 50 steps double or halve the brightness


def _look(img, brightness, blur=True):
    img = ImageEnhance.Color(img).enhance(SATURATION)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    img = ImageEnhance.Contrast(img).enhance(CONTRAST)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(BLUR_PX * img.width / 1024))
    return img


def _bright_edge(img):
    """Relative luminance (0 … 1) that only the brightest 3 % of the picture exceed."""
    data = img.convert("RGB").tobytes()
    values = sorted(styles.luminance(px) for px in zip(data[0::3], data[1::3], data[2::3]))
    return values[min(len(values) - 1, int(len(values) * PERCENTILE))]


def target_luminance(theme):
    """Brightest luminance the page may have so that ink and headings keep their contrast."""
    limits = []
    for key, ratio in (("ink", INK_CONTRAST), ("accent", ACCENT_CONTRAST)):
        rgb = styles.parse_hex(theme.get(key))
        if rgb:
            limits.append((styles.luminance(rgb) + 0.05) / ratio - 0.05)
    return max(0.0, min(limits)) if limits else 0.05


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


def dim(img, theme, adjust=0):
    """Dimmed copy of the stored picture and {optimal, brightness} (the factors used)."""
    img = img.convert("RGB")
    optimal = optimal_brightness(img, theme)
    brightness = brightness_for(optimal, adjust)
    return _look(img, brightness), {"optimal": optimal, "brightness": brightness}


def is_raw(project):
    """True if images/background.jpg is the engine's original; older projects hold a picture that was
    already darkened when it was saved (it is used as it is, the slider cannot act on it)."""
    return project.has_image("background") and bool(project.entry("background").get("raw"))


def page_image(project, log=None):
    """file:// URI of the background for the pages, or None. Dimmed from the original at build time."""
    if not project.has_image("background"):
        return None
    path = project.image_path("background")
    if not is_raw(project):
        if log:
            log("Hintergrund: altes, schon abgedunkeltes Bild – für den Helligkeitsregler neu erzeugen.")
        return path.as_uri()
    adjust = int(project.config["layout"].get("bg_dim_adjust", 0))
    with Image.open(path) as im:
        out, info = dim(im, project.config["theme"], adjust)
    target = project.build_dir / "background-page.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target, "JPEG", quality=90)
    if log:
        log(f"Hintergrund: Helligkeit {info['brightness']:.2f} (berechnetes Optimum {info['optimal']:.2f}, "
            f"Regler {adjust:+d}).")
    return target.as_uri()


def preview(project, adjust, theme=None, width=170):
    """Small dimmed picture for the slider's preview and {optimal, brightness}; None if there is no original."""
    if not is_raw(project):
        return None
    with Image.open(project.image_path("background")) as im:
        small = im.convert("RGB")
        small.thumbnail((width * 2, width * 3))
    return dim(small, theme or project.config["theme"], adjust)
