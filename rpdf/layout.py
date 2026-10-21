"""Lay out a parsed rulebook as HTML and render it to PDF with WeasyPrint.

Every subsection is one block: full-width heading + illustration, then its
text in two columns. Headings can never end up in a different column than
their content.
"""
import html as htmllib
import re
import urllib.parse
from pathlib import Path
from string import Template

import markdown

from . import background
from .project import filler_title

PKG = Path(__file__).parent
NARROW_TABLE_MAX_COLS = 3
NARROW_TABLE_MAX_ROWS = 8         # longer tables span both columns and may break across pages
NARROW_TABLE_MAX_CELL = 60        # ... as do tables with long cell texts
COMPACT_SECTION_MAX_CHARS = 700   # short sections: smaller image beside the text
SINGLE_COLUMN_MAX_CHARS = 600   # shorter texts look torn apart in two columns


# Emoji-like code points. WeasyPrint 70 + HarfBuzz 14 crash while drawing color glyphs (COLR,
# e.g. Noto Color Emoji), so emojis are forced to text presentation (U+FE0E) and drawn with
# the bundled monochrome Noto Emoji instead.
EMOJI_RE = re.compile("([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF])\uFE0F?(?!\uFE0E)")


def _text_emoji(html):
    return EMOJI_RE.sub("\\1\uFE0E", html)


def _hex_rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _unfold_details(text):
    """<details> blocks are raw HTML to the Markdown parser, so tables inside stay unparsed.
    Drop the wrapper (a PDF cannot fold) and keep the summary as a paragraph."""
    text = re.sub(r"</?details[^>]*>", "", text)
    return re.sub(r"<summary[^>]*>(.*?)</summary>", r"\n\n\1\n\n", text, flags=re.S)


def _md(text, base=None, extra_dirs=()):
    text = _unfold_details(text)
    out = markdown.markdown(text, extensions=["tables", "sane_lists", "fenced_code"])
    return _wrap_tables(_figures(out, base, extra_dirs))


def _image_aspect(path):
    """width / height of a local image (SVG via its viewBox), or None."""
    try:
        if path.suffix.lower() == ".svg":
            m = re.search(r'viewBox="\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)', path.read_text(encoding="utf-8"))
            return float(m.group(1)) / float(m.group(2)) if m else None
        from PIL import Image
        with Image.open(path) as im:
            return im.width / im.height
    except Exception:
        return None


def _figures(html, base, extra_dirs=()):
    """Images from the Markdown: resolve relative paths against the source file and
    turn stand-alone images into figures (alt text becomes the caption).
    If the file is not next to the source, the project's own folders (extra_dirs,
    e.g. `bilder/`) are searched by relative path, then by file name."""
    def resolve(src):
        if base is None or re.match(r"^(https?|file|data):", src):
            return src, None
        rel = htmllib.unescape(src)
        path = (base / rel).resolve()
        if not path.exists():
            for d in extra_dirs:
                for cand in (d / rel, d / Path(rel).name):
                    if cand.is_file():
                        path = cand.resolve()
                        break
                else:
                    continue
                break
        return path.as_uri(), path

    def img_attr(tag, name):
        m = re.search(rf'{name}="([^"]*)"', tag)
        return m.group(1) if m else ""

    def figure(m):
        tag = m.group(1)
        src, path = resolve(img_attr(tag, "src"))
        alt = img_attr(tag, "alt")
        aspect = _image_aspect(path) if path else None
        cls = "md-figure wide" if aspect and aspect > 1.25 else "md-figure"
        cap = f"<figcaption>{alt}</figcaption>" if alt else ""
        return f'<figure class="{cls}"><img src="{src}" alt="{alt}">{cap}</figure>'

    html = re.sub(r"<p>\s*(<img [^>]*>)\s*</p>", figure, html)
    # remaining inline images: only fix the path
    return re.sub(r'(<img [^>]*src=")([^"]+)(")',
                  lambda m: m.group(1) + resolve(m.group(2))[0] + m.group(3), html)


def _wrap_tables(html):
    def repl(m):
        table = m.group(0).replace("<table>", '<table class="rules-table">', 1)
        first_row = re.search(r"<tr>(.*?)</tr>", table, re.S)
        ncols = len(re.findall(r"<t[hd][ >]", first_row.group(1))) if first_row else 0
        nrows = len(re.findall(r"<tr>", table)) - 1
        cells = [re.sub("<.*?>", "", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", table, re.S)]
        longest = max((len(c.strip()) for c in cells), default=0)
        narrow = (ncols <= NARROW_TABLE_MAX_COLS and nrows <= NARROW_TABLE_MAX_ROWS
                  and longest <= NARROW_TABLE_MAX_CELL and "<img" not in table)   # screenshot grids need the full width
        cls = "table-narrow" if narrow else "table-wide"
        return f'<div class="{cls}">{table}</div>'
    return re.sub(r"<table>.*?</table>", repl, html, flags=re.S)


def _plain_len(md_text):
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md_text)
    return len(re.sub(r"[#*_|>\-\s]+", " ", plain).strip())


def _cols(md_text):
    return "cols single" if _plain_len(md_text) < SINGLE_COLUMN_MAX_CHARS else "cols"


WIDE_BLOCK = re.compile(r'(<div class="table-wide">.*?</div>|<figure class="md-figure wide">.*?</figure>)', re.S)


def _columns(html, md_text, extra=""):
    """Section text in columns. Wide tables and figures are not spanners inside the column box
    (WeasyPrint moves such a spanner whole to the next page and leaves the page below the
    heading empty) but stand between column groups, so they break across pages like normal blocks."""
    extra = f" {extra}" if extra else ""
    pieces = [p for p in WIDE_BLOCK.split(html) if p.strip()]
    if len(pieces) == 1 and not WIDE_BLOCK.fullmatch(pieces[0]):
        return f'<div class="{_cols(md_text)}{extra}">{pieces[0]}</div>'
    out = []
    for p in pieces:
        if WIDE_BLOCK.fullmatch(p):
            out.append(p)
        else:
            short = len(re.sub(r"<[^>]+>|\s+", " ", p).strip()) < SINGLE_COLUMN_MAX_CHARS
            out.append(f'<div class="cols{" single" if short else ""}{extra}">{p}</div>')
    return "".join(out)


def _compact(md_text):
    """Short section without tables or own images: image goes beside the text."""
    return (_plain_len(md_text) < COMPACT_SECTION_MAX_CHARS and "|" not in md_text
            and "![" not in md_text)


def _dropcap(html):
    m = re.search(r"<p>([^\W\d_])", html)
    if not m:
        return html
    return html[:m.start()] + f'<p class="lead"><span class="dropcap">{m.group(1)}</span>' + html[m.end():]


def _css_string(text):
    """CSS string literal that is safe inside a double-quoted style attribute."""
    css = "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return htmllib.escape(css, quote=True)


def _esc(text):
    return htmllib.escape(text or "")


def _filler(fillers, block_key, img, fkey):
    """Image filling the empty space after a block: fillers = {block_key: height_mm}."""
    if not fillers or block_key not in fillers:
        return ""
    src = img(fkey(block_key))
    if not src:
        return ""
    return (f'<figure class="filler" style="height:{fillers[block_key]:.1f}mm">'
            f'<img src="{src}" alt=""></figure>')


def find_gaps(document, min_gap_mm):
    """Empty space at the bottom of pages where a block ends.

    Returns [(block_key, gap_mm, width_mm)] for gaps of at least min_gap_mm. A gap
    only counts if the block that ends the page does not continue on the next page.
    The gap is measured from the bottom edge of the block (including its margins),
    not from its last line, so a filler of that height really fits.
    """
    px_to_mm = 25.4 / 96
    leaf_types = {"LineBox", "BlockReplacedBox", "TableBox", "InlineReplacedBox"}

    def walk(box, block, leaves, edges):
        el = getattr(box, "element", None)
        if el is not None:
            cls = el.get("class") or ""
            if "pagebg" in cls or "opener" in cls:
                return
            eid = el.get("id") or ""
            if eid.startswith("b-") and type(box).__name__ == "BlockBox":
                block = eid[2:]
                edges[block] = max(edges.get(block, 0), box.position_y + box.margin_height())
        if type(box).__name__ in leaf_types:
            leaves.append((box.position_y + box.margin_height(), block))
            return
        for child in getattr(box, "children", []):
            walk(child, block, leaves, edges)

    pages = []
    for page in document.pages:
        pb = page._page_box
        leaves, edges = [], {}
        for child in pb.children:
            if type(child).__name__ != "MarginBox":
                walk(child, None, leaves, edges)
        pages.append((leaves, edges, pb.margin_top + pb.height, pb.width))

    gaps = []
    for i, (leaves, edges, bottom, width) in enumerate(pages):
        tagged = [(y, b) for y, b in leaves if b]
        if not tagged:
            continue
        last_block = max(tagged)[1]
        nxt = {b for _, b in pages[i + 1][0]} if i + 1 < len(pages) else set()
        gap_mm = (bottom - edges.get(last_block, max(tagged)[0])) * px_to_mm
        if last_block not in nxt and gap_mm >= min_gap_mm:
            gaps.append((last_block, gap_mm, width * px_to_mm))
    return gaps


def build_html(project, doc, fillers=None, lang=None, log=None):
    """HTML of one language. Topic images are shared by all languages (the translation's blocks carry
    the same keys); filler images are looked up per language."""
    cfg = project.settings_for(lang)
    source = project.source_for(lang)
    base = source.parent if source else None
    fkey = lambda block: project.filler_key(block, lang)
    image_dirs = [project.root / "bilder", project.root]
    md = lambda text: _md(text, base, image_dirs)
    theme, lay = cfg["theme"], cfg["layout"]
    img = lambda key: project.image_path(key).as_uri() if project.has_image(key) else None
    running = cfg.get("running_title") or (cfg.get("title") or doc.title or "").split(":")[0]

    parts = []
    page_bg = background.page_image(project, log) if lay.get("page_texture") else None
    if page_bg:
        parts.append(f'<div class="pagebg" style="background-image:url(\'{page_bg}\')"></div>')

    # cover
    cover_img = img("cover")
    title = cfg.get("title") or doc.title or ""
    parts.append(f"""<section class="cover">
      {f'<div class="cover-img" style="background-image:url({chr(39)}{cover_img}{chr(39)})"></div><div class="cover-scrim"></div>' if cover_img else ''}
      <div class="cover-text">
        {f'<div class="cover-kicker">{_esc(cfg["kicker"])}</div>' if cfg.get("kicker") else ''}
        <h1 class="cover-title">{_esc(title)}</h1>
        {f'<div class="cover-sub">{_esc(cfg["subtitle"])}</div>' if cfg.get("subtitle") else ''}
        {f'<div class="cover-meta">{_esc(cfg["tagline"])}</div>' if cfg.get("tagline") else ''}
      </div></section>""")

    # foreword: only when the text before chapter 1 is more than a tagline
    intro_plain = re.sub(r"[#*_>\-\s]+", " ", doc.intro_md).strip()
    if len(intro_plain) > 300:
        parts.append(f'<section class="foreword"><div class="runhead-anchor" '
                     f'style="string-set: runhead {_css_string(running)}, chaptitle \'\'"></div>'
                     f'{_columns(_dropcap(md(doc.intro_md)), doc.intro_md)}</section>')

    label = cfg.get("chapter_label") or ""
    for idx, ch in enumerate(doc.chapters, 1):
        if ch.appendix:
            body = md(ch.intro_md) + "".join(f"<h3>{_esc(s.title)}</h3>{md(s.body_md)}" for s in ch.sections)
            head = ch.full_title.split(":")[0]
            parts.append(f'<section class="chapter appendix"><h2 style="string-set: runhead '
                         f'{_css_string(running)}, chaptitle {_css_string(head)}">{_esc(ch.full_title)}</h2>'
                         f'<div class="appendix-body">{body}</div></section>')
            continue

        num = ch.number if ch.number is not None else idx
        banner = img(ch.key)
        cls = "opener" + ("" if banner else " plain")
        out = [f'<section class="chapter">', f'<header class="{cls}">']
        if banner:
            out.append(f'<img src="{banner}" alt=""><div class="opener-scrim"></div>')
        out.append('<div class="opener-label">'
                   f'<div class="chapter-num">{_esc(label)} {num:02d}</div>'
                   f'<h2>{_esc(ch.title)}</h2>'
                   + (f'<div class="chapter-sub">{_esc(ch.subtitle)}</div>' if ch.subtitle else "")
                   + '</div></header>')
        # re-enables the running heads from the page after the opener on
        out.append(f'<div class="runhead-anchor" style="string-set: runhead {_css_string(running)}, '
                   f'chaptitle {_css_string(ch.title)}"></div>')

        has_intro = bool(ch.intro_md.strip())
        if has_intro:
            out.append(f'<div class="intro" id="b-{ch.key}">{_columns(_dropcap(md(ch.intro_md)), ch.intro_md)}</div>')
            out.append(_filler(fillers, ch.key, img, fkey))
        for i, s in enumerate(ch.sections):
            fig = img(s.key) if lay.get("section_images") else None
            body = md(s.body_md)
            if not has_intro and i == 0:
                body = _dropcap(body)
            if fig and _compact(s.body_md):
                out.append(f'<section class="sub compact" id="b-{s.key}"><div class="sub-head"><h3>{_esc(s.title)}</h3></div>'
                           f'<div class="compact-body"><figure class="side-figure"><img src="{fig}" alt=""></figure>'
                           f'{body}</div></section>')
            else:
                fig_html = f'<figure class="sub-figure"><img src="{fig}" alt=""></figure>' if fig else ""
                out.append(f'<section class="sub" id="b-{s.key}"><div class="sub-head"><h3>{_esc(s.title)}</h3>{fig_html}</div>'
                           f'{_columns(body, s.body_md)}</section>')
            out.append(_filler(fillers, s.key, img, fkey))
        out.append("</section>")
        parts.append("\n".join(out))

    fonts_file = PKG / "fonts" / "fonts.css"
    if fonts_file.exists():
        fonts_css = fonts_file.read_text(encoding="utf-8").replace(
            "url('fonts/", f"url('{(PKG / 'fonts').as_uri()}/")
    else:
        print("WARNUNG: Schriften fehlen (python3 fetch_fonts.py ausführen), es werden Systemschriften benutzt.")
        fonts_css = ""
    css = Template((PKG / "theme.css").read_text(encoding="utf-8")).substitute(
        bg=theme["bg"], ink=theme["ink"], ink_dim=_hex_rgba(theme["ink"], .62),
        accent=theme["accent"], accent2=theme["accent2"],
        accent_soft=_hex_rgba(theme["accent"], .10), accent_line=_hex_rgba(theme["accent"], .35),
        font_size=lay.get("font_size_pt", 9.6), lead_size=round(lay.get("font_size_pt", 9.6) * 1.08, 2),
        text_align="justify" if lay.get("justify", True) else "left",
        filler_gap=FILLER_SPACING_MM - 3,
        cover_title_size=118 if len(title) <= 10 else max(48, int(118 * 10 / len(title))))

    return _text_emoji(f"""<!doctype html>
<html lang="{_esc(cfg.get('language', 'de'))}"><head><meta charset="utf-8">
<title>{_esc(title)}</title><style>{fonts_css}
{css}</style></head>
<body>
{chr(10).join(parts)}
</body></html>""")


FILLER_SPACING_MM = 9   # breathing room above a filler and safety margin below


def _build_name(stem, tag):
    return f"{stem}.{tag}.html" if tag else f"{stem}.html"


def detect_fillers(project, doc=None, log=print, lang=None):
    """Lay out one language without fillers and register a filler slot for every large gap.
    Each language has its own gaps (the translated text flows differently), so its own fillers."""
    from weasyprint import HTML

    tag = project.lang_tag(lang)
    doc = doc or project.document(tag)
    project.build_dir.mkdir(parents=True, exist_ok=True)
    html_path = project.build_dir / _build_name("probe", tag)
    html_path.write_text(build_html(project, doc, lang=tag), encoding="utf-8")
    document = HTML(filename=str(html_path)).render()
    min_gap = project.config["layout"].get("filler_min_mm", 55)
    gaps = find_gaps(document, min_gap)
    titles = {c.key: c.title for c in doc.chapters}
    titles.update({s.key: s.title for c in doc.chapters for s in c.sections})
    chapter_of = {c.key: c.key for c in doc.chapters}
    chapter_of.update({s.key: c.key for c in doc.chapters for s in c.sections})
    mine = lambda e: e.get("kind") == "filler" and (e.get("lang") or "") == tag
    for e in project.manifest.values():
        if mine(e):
            e["active"] = False
    for block, gap_mm, width_mm in gaps:
        if block not in titles:
            continue
        e = project.entry(project.filler_key(block, tag))
        e.update(kind="filler", lang=tag, active=True, title=filler_title(titles[block], tag), after=block,
                 chapter=chapter_of[block], h_mm=round(gap_mm - FILLER_SPACING_MM, 1),
                 w_mm=round(width_mm, 1))
    # forget fillers for gaps that no longer exist, unless an image was already made
    for key in [k for k, e in project.manifest.items() if mine(e) and not e.get("active") and not project.has_image(k)]:
        del project.manifest[key]
    project.save_manifest()
    log(f"Leerraum-Analyse{f' ({tag})' if tag else ''}: {len(gaps)} Lücke(n) ab {min_gap} mm gefunden.")
    return document


def render(project, doc=None, log=print, lang=None):
    """Typeset the PDF of one language (the main language by default)."""
    from weasyprint import HTML

    tag = project.lang_tag(lang)
    doc = doc or project.document(tag)
    project.build_dir.mkdir(parents=True, exist_ok=True)
    fillers = None
    if project.config["layout"].get("fill_gaps"):
        detect_fillers(project, doc, log, tag)
        fillers = {e["after"]: e["h_mm"] for k, e in project.manifest.items()
                   if e.get("kind") == "filler" and (e.get("lang") or "") == tag and e.get("active")
                   and project.has_image(k)}
        log(f"Füllbilder eingesetzt: {len(fillers)}")
    html_path = project.build_dir / _build_name("index", tag)
    html = build_html(project, doc, fillers, tag, log)
    html_path.write_text(html, encoding="utf-8")
    for uri in sorted(set(re.findall(r'<img [^>]*src="(file://[^"]+)"', html))):
        path = Path(urllib.parse.unquote(urllib.parse.urlparse(uri).path))
        if not path.exists():
            log(f"WARNUNG: Bild nicht gefunden: {path}")
    out = project.output_for(tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    log("Setze PDF mit WeasyPrint …")
    document = HTML(filename=str(html_path)).render()
    document.write_pdf(str(out))
    log(f"PDF geschrieben: {out} ({len(document.pages)} Seiten)")
    return out, len(document.pages)
