"""Convert other people's rulebooks (DOCX, PDF, TXT) to Markdown.

DOCX is converted locally from its heading styles. PDF/TXT text has no
reliable structure, so agy restores headings, lists and tables chunk by
chunk while keeping the wording.
"""
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import planner

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WEB_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MIN_PDF_IMAGE_PX = 200   # smaller PDF images are usually icons or ornaments
CHUNK_CHARS = 8000
HEADING_STYLE_RE = re.compile(r"(?i)(heading|berschrift|titre|titolo|titulo|kop)\s*(\d)")


def to_markdown(src, dest, ctx, model=None, kind="rulebook"):
    src, dest = Path(src), Path(dest)
    ext = src.suffix.lower()
    if ext in (".md", ".markdown"):
        shutil.copy(src, dest)
    elif ext == ".docx":
        dest.write_text(docx_to_markdown(src, dest.parent / "media", ctx), encoding="utf-8")
    elif ext == ".pdf":
        md = _llm_restructure(pdf_text(src, dest.parent / "media", ctx), ctx, model, kind)
        toc = toc_from_pdf(src)
        md, report = relevel_from_pdf(md, src, toc)
        if report and report[0].startswith("Keine Schriftinformationen"):
            md = apply_toc_levels(md, toc)
            report = [f"Gliederung aus dem Inhaltsverzeichnis übernommen ({len(toc)} Einträge)."] if toc else []
        for line in report:
            ctx.log(line)
        dest.write_text(md, encoding="utf-8")
    elif ext == ".txt":
        dest.write_text(_llm_restructure(src.read_text(encoding="utf-8", errors="replace"), ctx, model, kind),
                        encoding="utf-8")
    else:
        raise ValueError(f"Format {ext} wird nicht unterstützt (md, docx, pdf, txt).")
    ctx.log(f"Markdown geschrieben: {dest}")
    return dest


# ---------- DOCX ----------
def _run_text(r):
    t = "".join(x.text or "" for x in r.iter(W + "t"))
    if not t.strip():
        return t
    props = r.find(W + "rPr")
    bold = props is not None and props.find(W + "b") is not None
    ital = props is not None and props.find(W + "i") is not None
    if bold:
        t = f"**{t.strip()}** " if t.endswith(" ") else f"**{t.strip()}**"
    elif ital:
        t = f"*{t.strip()}* " if t.endswith(" ") else f"*{t.strip()}*"
    return t


def _para(p, images=None):
    pics = [images[b.get(R + "embed")] for b in p.iter(A + "blip")
            if images and images.get(b.get(R + "embed"))]
    style = p.find(f"{W}pPr/{W}pStyle")
    style_id = style.get(W + "val") if style is not None else ""
    text = "".join(_run_text(r) for r in p.iter(W + "r")).strip()
    text = re.sub(r"\*\*\s*\*\*", "", text)
    pic_md = "\n\n".join(f"![]({src})" for src in pics)
    if not text:
        return pic_md
    if pic_md:
        return text + "\n\n" + pic_md
    if style_id.lower() == "title":
        return "# " + re.sub(r"\*", "", text)
    m = HEADING_STYLE_RE.search(style_id)
    if m:
        level = min(int(m.group(2)) + 1, 6)   # Heading 1 -> "##", the title owns "#"
        return "#" * level + " " + re.sub(r"\*", "", text)
    num = p.find(f"{W}pPr/{W}numPr")
    if num is not None:
        lvl = num.find(W + "ilvl")
        depth = int(lvl.get(W + "val")) if lvl is not None else 0
        return "    " * depth + "* " + text
    return text


def _table(tbl):
    rows = []
    for tr in tbl.iter(W + "tr"):
        cells = [" ".join(_para(p) for p in tc.iter(W + "p")).replace("|", "/").strip()
                 for tc in tr.iter(W + "tc")]
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _docx_images(z, media_dir, ctx):
    """Extract embedded images; returns {relationship id: relative markdown path}."""
    try:
        rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    out = {}
    for rel in rels:
        target = rel.get("Target", "")
        if not rel.get("Type", "").endswith("/image"):
            continue
        name = Path(target).name
        if Path(name).suffix.lower() not in WEB_IMAGE_EXT:
            ctx.log(f"Bild übersprungen (Format {Path(name).suffix} nicht unterstützt): {name}")
            continue
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / name).write_bytes(z.read("word/" + target.lstrip("/").removeprefix("word/")))
        out[rel.get("Id")] = f"{media_dir.name}/{name}"
    if out:
        ctx.log(f"{len(out)} Bild(er) aus dem Word-Dokument übernommen → {media_dir}")
    return out


def docx_to_markdown(path, media_dir=None, ctx=None):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
        images = _docx_images(z, media_dir, ctx) if media_dir and ctx else {}
    body = root.find(W + "body")
    blocks, prev_list = [], False
    for el in body:
        if el.tag == W + "p":
            line = _para(el, images)
            if not line:
                continue
            is_list = line.lstrip().startswith("* ")
            sep = "\n" if (is_list and prev_list) else "\n\n"
            blocks.append(sep + line)
            prev_list = is_list
        elif el.tag == W + "tbl":
            blocks.append("\n\n" + _table(el))
            prev_list = False
    return "".join(blocks).strip() + "\n"


# ---------- PDF / TXT ----------
def pdf_text(path, media_dir=None, ctx=None):
    """Text per page; larger embedded images are extracted and referenced at the end of their page."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext fehlt (Paket poppler-utils).")
    text = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"],
                          capture_output=True, text=True, check=True).stdout
    if not (media_dir and ctx and shutil.which("pdfimages")):
        return text
    media_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdfimages", "-png", "-p", str(path), str(media_dir / "img")], check=True,
                   capture_output=True)
    from PIL import Image
    by_page = {}
    for f in sorted(media_dir.glob("img-*.png")):
        m = re.match(r"img-(\d+)-\d+\.png", f.name)
        with Image.open(f) as im:
            small = min(im.size) < MIN_PDF_IMAGE_PX
        if small or not m:
            f.unlink()
            continue
        by_page.setdefault(int(m.group(1)), []).append(f"{media_dir.name}/{f.name}")
    pages = text.split("\f")
    for page_no, refs in by_page.items():
        if 0 < page_no <= len(pages):
            pages[page_no - 1] += "\n\n" + "\n\n".join(f"![]({r})" for r in refs) + "\n"
    count = sum(len(v) for v in by_page.values())
    if count:
        ctx.log(f"{count} Bild(er) aus dem PDF übernommen → {media_dir} (bitte prüfen, ob Deko dabei ist)")
    return "\f".join(pages)


def _chunks(text):
    pages = text.split("\f")
    chunk = ""
    for page in pages:
        if len(chunk) + len(page) > CHUNK_CHARS and chunk:
            yield chunk
            chunk = ""
        chunk += page + "\n"
    if chunk.strip():
        yield chunk


TOC_LINE = re.compile(r"^.{2,}?(\s*\.){4,}\s*\d+\s*$")
TOC_TITLE = re.compile(r"^\s*(inhalt|inhaltsverzeichnis|contents|table of contents|sommaire|indice)\s*$", re.I)


def strip_toc(text):
    """Remove table-of-contents lines (title ..... 12) and the TOC heading."""
    out = []
    for line in text.split("\n"):
        if TOC_LINE.match(line.strip()) or TOC_TITLE.match(line):
            continue
        out.append(line)
    return "\n".join(out)


TOC_LAYOUT_LINE = re.compile(r"^( *)(\S.*?)(?:\s*\.){4,}\s*\d+\s*$")


def _norm(title):
    t = re.sub(r"[*_`#]", "", title).lower()
    return re.sub(r"[^0-9a-zäöüß]+", "", t)


def toc_from_pdf(path, max_pages=8):
    """[(normalized title, level)] from a Word-style table of contents (dot leaders + page number).
    Levels come from the indentation, clustered so that small layout jitter does not matter."""
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-layout", "-f", "1", "-l", str(max_pages), str(path), "-"],
                         capture_output=True, text=True).stdout
    entries = [(len(m.group(1)), m.group(2).strip()) for m in map(TOC_LAYOUT_LINE.match, out.splitlines()) if m]
    if len(entries) < 4:
        return []
    indents = sorted({i for i, _ in entries})
    level_of, level, prev = {}, 0, None
    for i in indents:
        if prev is None or i - prev > 1:
            level += 1
        level_of[i], prev = level, i
    return [(_norm(t), level_of[i]) for i, t in entries]


def apply_toc_levels(md, toc):
    """Set heading levels from the author's table of contents: TOC level 1 -> '##' (chapter).
    Headings that are not in the TOC go one level below the last matched heading."""
    if not toc:
        return md
    out, j, last = [], 0, 2
    for line in md.split("\n"):
        m = re.match(r"^(#{1,6}) (.*)$", line)
        if m and not (len(m.group(1)) == 1 and not out):          # keep a leading book title
            key, hit = _norm(m.group(2)), None
            for k in range(j, min(j + 25, len(toc))):
                if toc[k][0] == key:
                    hit = k
                    break
            if hit is not None:
                level = min(toc[hit][1] + 1, 6)
                j, last = hit + 1, level
            else:
                level = min(last + 1, 6)
            line = "#" * level + " " + m.group(2)
        if line.strip() or out:
            out.append(line)
    return "\n".join(out)


def pdf_line_styles(path):
    """Visual lines of a PDF with their style: {normalized text: (size, bold, color, page, top)}.
    Uses pdftohtml's XML; runs on the same page and baseline are joined into one line, and
    wrapped headings (two consecutive lines in the same style) are also registered joined."""
    import html as htmllib
    import tempfile
    if not shutil.which("pdftohtml"):
        return {}, set()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftohtml", "-xml", "-i", "-q", "-s", str(path), f"{tmp}/x"],
                       capture_output=True, check=False)
        try:
            xml = Path(f"{tmp}/x.xml").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return {}, set()
    fonts = {m.group(1): (round(float(m.group(2))), m.group(3).lower())
             for m in re.finditer(r'<fontspec id="(\d+)" size="([\d.]+)" family="[^"]*" color="([^"]*)"', xml)}
    runs = []
    for page_no, page in enumerate(re.findall(r"<page [^>]*>(.*?)</page>", xml, re.S), 1):
        for m in re.finditer(r'<text top="(\d+)" left="(\d+)" width="\d+" height="(\d+)" font="(\d+)">(.*?)</text>', page):
            raw = m.group(5)
            text = htmllib.unescape(re.sub("<.*?>", "", raw)).strip()
            if not text or m.group(4) not in fonts:
                continue
            bold = "<b>" in raw
            size, color = fonts[m.group(4)]
            runs.append((page_no, int(m.group(1)), int(m.group(2)), int(m.group(3)), size, bold, color, text))
    # join runs into visual lines
    lines = []
    for r in sorted(runs, key=lambda r: (r[0], r[1], r[2])):
        if lines and lines[-1]["page"] == r[0] and abs(lines[-1]["top"] - r[1]) <= 3:
            ln = lines[-1]
            ln["parts"].append(r)
        else:
            lines.append({"page": r[0], "top": r[1], "height": r[3], "parts": [r]})
    chars = {}
    for ln in lines:
        main = max(ln["parts"], key=lambda r: (r[4], len(r[7])))
        ln["size"], ln["color"] = main[4], main[6]
        ln["bold"] = all(r[5] for r in ln["parts"] if r[7].strip())
        ln["text"] = " ".join(r[7] for r in ln["parts"])
        key = (ln["size"], ln["bold"], ln["color"])
        chars[key] = chars.get(key, 0) + len(ln["text"])
    total = sum(chars.values()) or 1
    body = {k for k, n in chars.items() if n / total > 0.05}      # styles of running text
    styles = {}
    def put(key, entry):
        # the same words appear in the TOC and in running text; the heading is the biggest one
        old = styles.get(key)
        rank = lambda e: (e[:3] not in body, e[0], e[1])
        if key and (old is None or rank(entry) > rank(old)):
            styles[key] = entry
    for i, ln in enumerate(lines):
        if re.search(r"\.{5,}", ln["text"]):        # table of contents line
            continue
        entry = (ln["size"], ln["bold"], ln["color"], ln["page"], ln["top"])
        put(_norm(ln["text"]), entry)
        for part in ln["parts"]:                      # heading + subtitle on one line
            put(_norm(part[7]), entry)
        if i + 1 < len(lines):                        # wrapped heading
            nx = lines[i + 1]
            if (nx["page"] == ln["page"] and (nx["size"], nx["bold"], nx["color"]) == (ln["size"], ln["bold"], ln["color"])
                    and 0 < nx["top"] - ln["top"] <= 1.8 * ln["height"]):
                put(_norm(ln["text"] + " " + nx["text"]), entry)
    return styles, body


def relevel_from_pdf(md, pdf_path, toc=None):
    """Re-level all headings of an imported Markdown after the original PDF's typography.

    - chapters ('##'): headings in the largest heading size, plus top-level TOC entries
    - inside a chapter: the remaining heading sizes become '###', '####', ... (a size used by
      only one heading in that chapter joins the next smaller size)
    - a heading followed directly by another one that sits on the same PDF line (subtitle) is
      merged into 'Title – Subtitle'
    Headings not found in the PDF keep their position relative to their neighbours.
    Returns (markdown, report lines)."""
    styles, body = pdf_line_styles(pdf_path)
    if not styles:
        return md, ["Keine Schriftinformationen im PDF gefunden – Gliederung unverändert."]
    toc_top = {k for k, lvl in (toc or []) if lvl == 1}
    toc_all = {k for k, _ in (toc or [])}
    lines = md.split("\n")
    heads = [(i, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines)
             for m in [re.match(r"^(#{1,6}) (.+)$", l)] if m]
    report = []

    # 1) merge heading + subtitle on the same PDF line
    drop = set()
    for (i, _, t), (j, _, u) in zip(heads, heads[1:]):
        between = [l for l in lines[i + 1:j] if l.strip()]
        a, b = styles.get(_norm(t)), styles.get(_norm(u))
        if not between and a and b and a[3:] == b[3:]:
            sub = re.sub(r"^\s*[-–—]\s*", "", u)
            lines[i] = lines[i].rstrip() + " – " + sub
            drop.add(j)
            report.append(f"Untertitel zusammengeführt: {t} – {sub}")
    heads = [(i, lvl, lines[i].split(" ", 1)[1].strip()) for i, lvl, _ in heads if i not in drop]

    # 2) classify
    info = []
    for i, md_lvl, t in heads:
        st = styles.get(_norm(t)) or styles.get(_norm(re.sub(r" – .*$", "", t)))
        # authors often set sub-headings as plain or partly bold text; such headings keep their
        # relative level instead of getting one from their (body) font size
        styled = bool(st) and (st[0], st[1], st[2]) not in body
        info.append({"i": i, "md": md_lvl, "text": t, "size": st[0] if styled else None, "is_body": False})
    sizes = [h["size"] for h in info if h["size"] and not h["is_body"]]
    top_size = max(sizes) if sizes else None

    # 3) chapters: the table of contents decides; the biggest type size only for headings before
    #    the first TOC chapter (parts the TOC does not cover, like a lore section) or without a TOC
    seen_toc_top = False
    for h in info:
        in_toc_top = _norm(h["text"]) in toc_top
        seen_toc_top = seen_toc_top or in_toc_top
        big = bool(h["size"] and top_size and h["size"] >= top_size - 1)
        h["chapter"] = in_toc_top or (big and (not toc_top or not seen_toc_top))
    # leading title ('#') stays when it is the very first heading and the PDF's biggest text
    # 4) levels inside chapters
    level = {}
    chunk = []
    def flush(chunk):
        if not chunk:
            return
        tiers = sorted({h["size"] for h in chunk if h["size"]}, reverse=True)
        count = {t: sum(1 for h in chunk if h["size"] == t) for t in tiers}
        merged = {}
        for k, t in enumerate(tiers):
            target = t
            if count[t] == 1 and k + 1 < len(tiers):
                target = tiers[k + 1]
            merged[t] = target
        rank = {t: n for n, t in enumerate(sorted(set(merged.values()), reverse=True))}
        prev_out, prev_md, by_md = 2, None, {}
        for h in chunk:
            if h["size"]:
                out = 3 + rank[merged[h["size"]]]
            elif h["md"] in by_md:   # unstyled: same level as the last heading of the same md level
                out = by_md[h["md"]]
            else:                    # otherwise keep the relation to the previous heading
                out = prev_out + (1 if prev_md is not None and h["md"] > prev_md else 0)
            level[h["i"]] = min(max(out, 3), 6)
            prev_out, prev_md = level[h["i"]], h["md"]
            by_md.setdefault(h["md"], level[h["i"]])
    for h in info:
        if h["is_body"]:
            continue
        if h["chapter"]:
            flush(chunk); chunk = []
            level[h["i"]] = 2
        else:
            chunk.append(h)
    flush(chunk)

    # 5) write
    demoted = 0
    for h in info:
        text = lines[h["i"]].split(" ", 1)[1].strip()
        if h["is_body"]:
            lines[h["i"]] = f"**{text}**"
            demoted += 1
        else:
            lines[h["i"]] = "#" * level[h["i"]] + " " + text
    out = "\n".join(l for k, l in enumerate(lines) if k not in drop)
    n_ch = sum(1 for h in info if h["chapter"] and not h["is_body"])
    report.insert(0, f"Gliederung aus Schriftbild und Inhaltsverzeichnis übernommen: {n_ch} Kapitel, "
                     f"{len(info) - n_ch} Unterüberschriften.")
    return out, report


def promote_chapters_from_toc(md, toc):
    """Make every heading that the table of contents lists on its top level a chapter ('##').
    Only promotes, never demotes, so existing sections (and their images) stay sections.
    Returns (new_md, [promoted heading texts])."""
    top = {key for key, level in toc if level == 1}
    out, promoted = [], []
    for line in md.split("\n"):
        m = re.match(r"^(#{3,6}) (.*)$", line)
        if m and _norm(m.group(2)) in top:
            line = "## " + m.group(2)
            promoted.append(m.group(2).strip())
        out.append(line)
    return "\n".join(out), promoted


def normalize_heading_levels(md, first):
    """agy picks heading levels per chunk. Make chapters '##' everywhere: if a chunk uses '#'
    for chapters, shift all its headings one level down (a book title at the very start of
    the first chunk stays '#')."""
    lines = md.split("\n")
    title_idx = None
    if first:
        for i, line in enumerate(lines):
            if line.strip():
                title_idx = i if re.match(r"^# \S", line) else None
                break
    has_h1 = any(re.match(r"^# \S", l) for i, l in enumerate(lines) if i != title_idx)
    if not has_h1:
        return md
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,5}) (.*)$", line)
        if m and i != title_idx:
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def _llm_restructure(text, ctx, model=None, kind="rulebook"):
    text = strip_toc(text)
    chunks = list(_chunks(text))
    out, headings = [], []
    for i, chunk in enumerate(chunks):
        ctx.check()
        ctx.progress(i, len(chunks))
        ctx.log(f"agy: Abschnitt {i + 1}/{len(chunks)} in Markdown umwandeln …")
        md = planner.convert_to_markdown(chunk, headings, first=(i == 0), model=model, kind=kind)
        md = normalize_heading_levels(md, first=(i == 0))
        headings += re.findall(r"(?m)^#{1,4} .+$", md)
        out.append(md.strip())
    ctx.progress(len(chunks), len(chunks))
    return "\n\n".join(out) + "\n"
