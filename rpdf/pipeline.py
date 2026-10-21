"""Pipeline steps shared by CLI and GUI.

Each step takes a Context with log/progress callbacks and a cancel flag.
"""
import random
import re
from dataclasses import dataclass
from pathlib import Path

from . import agyimage, comfy, gemini, layout, planner, styles
from .project import NEEDS_REDO, size_for


def engine(project):
    """(module/class holder, client, engine settings) for the project's image engine."""
    if project.config.get("engine") == "agy":
        cfg = project.config["gemini"]
        client = agyimage.AgyImage(cfg)
        client.agy_model = planner.fast_variant(project.config["agy"].get("model")) or None
        return agyimage.AgyImage, client, cfg
    if project.config.get("engine") == "gemini":
        cfg = project.config["gemini"]
        return gemini.Gemini, gemini.Gemini(cfg), cfg
    cfg = project.config["comfy"]
    return comfy.Comfy, comfy.Comfy(cfg["url"]), cfg


QUOTA_MARGIN_S = 90        # wait a bit longer than the reset time before trying again
QUOTA_FALLBACK_S = 1800    # reset time unknown: try again every 30 minutes


def with_quota_wait(project, ctx, fn):
    """Run fn(); if agy's image quota is used up and project.config["quota_wait"] is on, sleep until
    the reset time (cancelable, with a status line every 10 minutes) and try again."""
    import time
    from datetime import datetime
    while True:
        try:
            return fn()
        except agyimage.QuotaExhausted as e:
            if not project.config.get("quota_wait"):
                raise
            if e.reset_at:
                until = e.reset_at.timestamp() + QUOTA_MARGIN_S
            else:
                until = time.time() + QUOTA_FALLBACK_S
            ctx.log(f"agy-Kontingent erschöpft – warte bis {datetime.fromtimestamp(until):%H:%M} Uhr "
                    "und mache dann automatisch weiter (Abbrechen jederzeit möglich).")
            next_note = time.time() + 600
            while time.time() < until:
                ctx.check()
                time.sleep(2)
                if time.time() >= next_note:
                    mins = int((until - time.time()) // 60)
                    ctx.log(f"    noch ca. {mins // 60} h {mins % 60:02d} min bis zum Weitermachen")
                    next_note += 600
            ctx.log("Wartezeit vorbei – mache weiter.")


def _ready(project, ctx=None):
    E, client, cfg = engine(project)
    if ctx is not None:
        with_quota_wait(project, ctx, lambda: E.check_config(cfg))
    else:
        E.check_config(cfg)
    if not client.alive():
        raise comfy.ComfyError(f"ComfyUI unter {cfg['url']} nicht erreichbar – in Pinokio starten.")
    return E, client, cfg


class Cancelled(Exception):
    pass


class Context:
    def __init__(self, log=print, progress=lambda done, total: None, cancelled=lambda: False,
                 image_saved=lambda key: None, isolate=False):
        self.log, self.progress, self.cancelled = log, progress, cancelled
        self.image_saved = image_saved     # called with the slot key after each saved image
        # Run WeasyPrint in a child process. Needed inside the Qt GUI: Qt and WeasyPrint
        # share FreeType/HarfBuzz, which are not thread-safe, and the GUI segfaulted.
        self.isolate = isolate

    def check(self):
        if self.cancelled():
            raise Cancelled()


def run_isolated(project, command, ctx, *extra):
    """Run a WeasyPrint step (`build` or `detect`) as `illuminator.py <command>` in a child process,
    streaming its output to the log. Returns the last output line."""
    import subprocess
    import sys
    from pathlib import Path

    project.save()
    script = Path(__file__).resolve().parent.parent / "illuminator.py"
    proc = subprocess.Popen([sys.executable, "-u", str(script), command, str(project.root), *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last = ""
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            ctx.log(line)
            last = line
        if ctx.cancelled():
            proc.terminate()
    code = proc.wait()
    if ctx.cancelled():
        raise Cancelled()
    if code != 0:
        reason = "abgestürzt (Speicherzugriffsfehler)" if code < 0 else f"Fehlercode {code}"
        raise RuntimeError(f"PDF-Satz {reason}: {last}")
    # the child may have changed the manifest (filler slots)
    project.__init__(project.root)
    return last


def _detect(project, ctx):
    """Measure the gaps of every language and register filler slots for them."""
    if not project.config["layout"].get("fill_gaps"):
        return
    if ctx.isolate:
        run_isolated(project, "detect", ctx)
        return
    for lang in project.build_languages():
        try:
            layout.detect_fillers(project, None, ctx.log, lang)
        except FileNotFoundError as e:
            ctx.log(f"Sprache {lang}: {e}")


def _outline(doc):
    return "\n".join(f"- {c.full_title}" + "".join(f"\n  - {s.title}" for s in c.sections) for c in doc.chapters)


def _excerpts(doc, limit=planner.SUMMARY_TEXT_LIMIT):
    """The first lines of every chapter, for the one-time summary of the book."""
    def clean(text, n):
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        return re.sub(r"\s+", " ", re.sub(r"[#*_`|>]+", " ", text)).strip()[:n]

    per = max(250, min(900, limit // (len(doc.chapters) + 1)))
    parts = [clean(doc.intro_md, per)] if doc.intro_md.strip() else []
    for ch in doc.chapters:
        body = ch.intro_md if ch.intro_md.strip() else " ".join(s.body_md for s in ch.sections[:2])
        parts.append(f"{ch.full_title}: {clean(body, per)}")
    return "\n\n".join(parts)


def ensure_summary(project, ctx=None, doc=None, force=False):
    """The short summary of the book. Made by the first LLM function that needs to know the book and stored
    in project.json; after that, functions get these ~100 words instead of excerpts of the text."""
    cfg = project.config
    if cfg.get("summary", "").strip() and not force:
        return cfg["summary"].strip()
    doc = doc or project.document()
    if ctx:
        ctx.log("KI: kurze Zusammenfassung des Buchs (einmalig) …")
    cfg["summary"] = planner.summarize(cfg.get("title") or doc.title or "", _outline(doc), _excerpts(doc),
                                       cfg["agy"].get("model") or None, _kind(project))
    project.save()
    if ctx:
        ctx.log(f"Zusammenfassung: {cfg['summary']}")
    return cfg["summary"]


def plan(project, ctx, force=False, keys=None, suggest_style=None):
    """Fill missing image prompts via agy. force=True rewrites existing prompts too."""
    doc = project.document()
    _detect(project, ctx)
    slots = project.sync_manifest(project.all_slots())
    model = project.config["agy"].get("model") or None
    wanted = [s for s in slots if (keys is None or s.key in keys)
              and (force or not project.entry(s.key).get("prompt"))]
    if not wanted:
        ctx.log("Alle Prompts sind schon vorhanden.")
        return

    cfg = project.config
    need_global = (not cfg.get("world") or any(s.kind in ("cover", "background") for s in wanted)
                   or (suggest_style if suggest_style is not None else not cfg.get("style")))
    total = len({s.chapter_key for s in wanted if s.chapter_key}) + (1 if need_global else 0)
    done = 0
    ctx.progress(done, total)

    if need_global:
        summary = ensure_summary(project, ctx, doc)
        ctx.log("KI: Welt, Stil, Cover und Hintergrund …")
        g = planner.plan_global(_outline(doc), summary, cfg.get("title") or doc.title or "", model, _kind(project))
        cfg["world"] = g["world"]
        if suggest_style or (suggest_style is None and not cfg.get("style")):
            styles.readable_accents(g, cfg["theme"])
            cfg["style"] = g["style"]
            cfg["theme"]["accent"], cfg["theme"]["accent2"] = g["accent"], g["accent2"]
            ctx.log(f"Stil-Vorschlag übernommen: {g['style']}")
        for key in ("cover", "background"):
            if any(s.key == key for s in wanted):
                project.entry(key).update(prompt=g[key], include_style=True)
        project.save()
        done += 1
        ctx.progress(done, total)

    by_chapter = {}
    for s in wanted:
        if s.chapter_key:
            by_chapter.setdefault(s.chapter_key, []).append(s)
    chapters = project.chapters_by_key()

    # Sequential on purpose: concurrent agy runs interfere with each other and
    # come back with empty results.
    for ck, chapter_slots in by_chapter.items():
        ctx.check()
        title = chapters[ck].full_title
        pending, got = list(chapter_slots), {}
        for attempt in range(2):
            try:
                got.update(planner.plan_chapter(chapters[ck], pending, _world(project), model,
                                                  project.config.get('style', ''), _kind(project)))
            except planner.AgyError as e:
                ctx.log(f"KI-Fehler bei „{title}“: {e}")
            pending = [s for s in chapter_slots if s.key not in got]
            if not pending:
                break
            if attempt == 0:
                ctx.log(f"„{title}“: {len(pending)} Prompt(s) fehlen, frage erneut …")
        # last resort: ask for the remaining slots one by one
        for s in list(pending):
            try:
                got[s.key] = planner.plan_single(s, _world(project), model, style=project.config.get('style', ''),
                                              kind=_kind(project))
                pending.remove(s)
            except planner.AgyError as e:
                ctx.log(f"KI-Fehler bei „{s.title}“: {e}")
        for key, prompt in got.items():
            project.entry(key).update(prompt=prompt, include_style=True)
        ctx.log(f"Prompts für „{title}“: {len(got)}/{len(chapter_slots)}"
                + (f" (fehlt: {', '.join(s.title for s in pending)})" if pending else ""))
        project.save_manifest()
        done += 1
        ctx.progress(done, total)
    project.save()


def _kind(project):
    """Content type of the project: "rulebook" (PnP) or "document" (README, tutorial, ...)."""
    return project.config.get("content_type") or "rulebook"


def _world(project):
    """World description plus what the selected image model understands."""
    from .styles import model_hint
    return f"{project.config.get('world', '')}\n{model_hint(project.config)}".strip()


def replan_one(project, key, hint="", ctx=None):
    slot = project.slot(key)
    prompt = planner.plan_single(slot, _world(project), project.config["agy"].get("model") or None, hint,
                                 project.config.get("style", ""), _kind(project))
    project.entry(key).update(prompt=prompt, include_style=True)
    project.save_manifest()
    return prompt


def gen_count(project):
    """How many images one slot gets per run (setting "gen_count", 1 … 8)."""
    try:
        return max(1, min(8, int(project.config.get("gen_count", 1))))
    except (TypeError, ValueError):
        return 1


def images(project, ctx, keys=None, regenerate=False, new_seed=False, count=None):
    """Generate images. By default only slots without an image file. With count > 1 every slot gets that many
    images in one go; all are kept as variants to choose from (see Project.variants)."""
    count = gen_count(project) if count is None else max(1, int(count))
    if keys is None:
        _detect(project, ctx)
    slots = project.sync_manifest(project.all_slots())
    todo = [s for s in slots if (keys is None or s.key in keys)
            and (regenerate or not project.has_image(s.key))]
    if not todo:
        ctx.log("Keine Bilder zu generieren.")
        return
    no_prompt = [s for s in todo if not project.entry(s.key).get("prompt", "").strip()]
    if no_prompt:
        ctx.log(f"Ohne Prompt übersprungen: {', '.join(s.title for s in no_prompt)} – erst „Prompts planen“.")
        todo = [s for s in todo if s not in no_prompt]

    E, client, cfg = _ready(project, ctx)

    qc = project.config.get("qc", {})
    for i, s in enumerate(todo):
        ctx.check()
        ctx.progress(i, len(todo))
        e = project.entry(s.key)
        if new_seed:
            e["seed"] = random.randint(1, 2**31 - 1)
        ctx.log(f"[{i + 1}/{len(todo)}] {s.title}" + (f" – {count} Bilder" if count > 1 else ""))
        if count > 1:
            _generate_batch(project, client, cfg, s, ctx, count)
            continue
        _generate(project, client, cfg, s, ctx)
        if qc.get("enabled"):
            _check_and_fix(project, client, cfg, s, ctx, fix=qc.get("auto_fix", True),
                           rounds=int(qc.get("rounds", 1)), radical=bool(qc.get("radical")),
                           strictness=qc_strictness(project))
    ctx.progress(len(todo), len(todo))


def _generate_batch(project, client, cfg, slot, ctx, count):
    """`count` images for one slot, all kept as variants. The first is the one the book uses, unless the image
    check (when on) finds a later one that fits. If the check is on and none of them fits, the usual correction
    follows (edits or new prompt, as set up under Engine) and its results are variants too."""
    qc = project.config.get("qc", {})
    e = project.entry(slot.key)
    vids = []
    for n in range(count):
        ctx.check()
        if n:
            e["seed"] = random.randint(1, 2**31 - 1)     # ComfyUI: another image; agy and Gemini ignore seeds
        ctx.log(f"    Bild {n + 1}/{count}")
        vids.append(_generate(project, client, cfg, slot, ctx, activate=(n == 0)))
    chosen = vids[0]
    if qc.get("enabled"):
        model, strictness = project.config["agy"].get("model") or None, qc_strictness(project)
        fits = []
        for n, vid in enumerate(vids):
            ctx.check()
            result = _judge(project, slot, model, strictness, vid)
            ctx.log(f"    Bildkontrolle Bild {n + 1}: " + ("passt" if result["fits"]
                    else "passt nicht – " + "; ".join(result["problems"])))
            if result["fits"]:
                fits.append(vid)
        if fits:
            chosen = fits[0]
    project.use_variant(slot.key, chosen)      # the book shows the chosen one; its prompt and seed come back
    project.save_manifest()
    ctx.image_saved(slot.key)
    if qc.get("enabled") and not fits:
        ctx.log("    Keins der Bilder passt – Korrektur wie bei einem einzelnen Bild …")
        _check_and_fix(project, client, cfg, slot, ctx, fix=qc.get("auto_fix", True),
                       rounds=int(qc.get("rounds", 1)), radical=bool(qc.get("radical")),
                       strictness=qc_strictness(project))
    elif qc.get("enabled") and chosen != vids[0]:
        ctx.log(f"    Bild {vids.index(chosen) + 1} passt und wird verwendet.")


def redo_rejected(project, ctx, new_prompts=False):
    """Regenerate every rejected image (red X from the user or AI flag) with a new seed.
    new_prompts=True: let the LLM write a new prompt for each of them first. Approved images
    (green or blue check) are not touched."""
    slots = project.sync_manifest(project.all_slots())
    todo = [s for s in slots if project.status(s.key) in NEEDS_REDO]
    if not todo:
        ctx.log("Keine abgelehnten Bilder.")
        return
    if new_prompts:
        model = project.config["agy"].get("model") or None
        for i, s in enumerate(todo):
            ctx.check()
            ctx.progress(i, len(todo))
            ctx.log(f"[{i + 1}/{len(todo)}] neuer Prompt für {s.title}")
            e = project.entry(s.key)
            try:
                prompt = planner.plan_single(s, _world(project), model, _redo_hint(e),
                                             project.config.get("style", ""), _kind(project))
            except planner.AgyError as err:
                ctx.log(f"    KI-Fehler, alter Prompt bleibt: {err}")
                continue
            e.update(prompt=prompt, include_style=True)
            project.save_manifest()
    ctx.log(f"Generiere {len(todo)} abgelehnte(s) Bild(er) mit neuem Seed …")
    images(project, ctx, keys={s.key for s in todo}, regenerate=True, new_seed=True)


def _redo_hint(entry):
    """Wish for the prompt writer: a clearly different description, avoiding what the AI check found."""
    hint = ("The previous image for this slot was rejected. Its description was: "
            f"\"{entry.get('prompt', '')}\". Write a different description.")
    qc = entry.get("qc")
    if qc and not qc.get("fits") and qc.get("problems"):
        hint += " Avoid these problems found in the image: " + "; ".join(qc["problems"])
    return hint


def qc_strictness(project):
    try:
        level = int(project.config.get("qc", {}).get("strictness", planner.STRICTNESS_DEFAULT))
    except (TypeError, ValueError):
        return planner.STRICTNESS_DEFAULT
    return level if level in planner.STRICTNESS else planner.STRICTNESS_DEFAULT


def edit_image(project, key, instruction, ctx):
    """agy/Gemini: change an existing image with a text instruction (chained via previous_interaction_id;
    falls back to uploading the JPEG when no interaction id is stored)."""
    if project.config.get("engine") not in ("agy", "gemini"):
        raise comfy.ComfyError("Bild bearbeiten geht nur mit den Engines „agy“ und „gemini“.")
    slot = project.slot(key)
    if not project.has_image(key):
        raise FileNotFoundError("Für diesen Platz gibt es noch kein Bild.")
    E, client, cfg = _ready(project, ctx)
    e = project.entry(key)
    w, h = size_for(slot)
    req = E.build_workflow(cfg, instruction, "", w, h, 0)
    req["prompt"] = instruction     # edits are plain instructions: no panorama/avoid suffixes
    prev = e.get("interaction_id")
    ctx.log(f"Bearbeite {slot.title} ({'Interaction ' + prev[:12] + '…' if prev else 'lokale Datei'})")
    img = with_quota_wait(project, ctx, lambda: client.generate(
        req, cancelled=ctx.cancelled, previous_id=prev, image_path=None if prev else project.image_path(key)))
    _save_variant(project, client, slot, img, "bearbeitet", True)     # the unedited image stays a variant
    ctx.log(f"    gespeichert: {project.image_path(key).name}")
    ctx.image_saved(key)


def remove_images(project, keys):
    """Take images out of the book so that "Fehlende Bilder" makes new ones. The file is moved to
    .build/removed/ (not deleted) and the slot gets a new seed, otherwise the same prompt and seed
    would just produce the same image again. Returns the keys that had an image."""
    removed = []
    for key in keys:
        if not project.trash_image(key):
            continue
        project.reset_review(key)
        project.entry(key)["seed"] = random.randint(1, 2**31 - 1)
        removed.append(key)
    project.save_manifest()
    return removed


def _save_variant(project, client, slot, img, source="gen", activate=True):
    """Store a freshly made image as a variant of the slot; activate = the book uses it from now on. The
    prompt, seed and the engine's edit chain id are kept with it. Returns the variant id."""
    e = project.entry(slot.key)
    vid, path = project.new_variant_path(slot.key)
    comfy.save_image(img, path, slot.kind)      # stored as the engine made it (the page background is dimmed at build time)
    project.register_variant(slot.key, vid, source, activate, prompt=e.get("prompt", ""), seed=e.get("seed"),
                             interaction_id=getattr(client, "last_interaction_id", None), raw=True)
    project.save_manifest()
    return vid


def _generate(project, client, cfg, slot, ctx, activate=True):
    e = project.entry(slot.key)
    w, h = size_for(slot)
    wf = type(client).build_workflow(cfg, project.full_prompt(slot.key), project.negative(slot.key), w, h, e["seed"])
    img = with_quota_wait(project, ctx, lambda: client.generate(wf, cancelled=ctx.cancelled))
    vid = _save_variant(project, client, slot, img, "gen", activate)
    ctx.log(f"    gespeichert: {project.variant_path(slot.key, vid).name}" + ("" if activate else " (Variante)"))
    ctx.image_saved(slot.key)
    return vid


RADICAL_CONCEPTS = 3    # radical mode: up to 3 concepts ...
RADICAL_TRIES = 3       # ... with up to 3 images each (3x3)


GEMINI_EDITS = 2    # agy/gemini: 1 generation + at most 2 edits of that image, then it stays as it is


def _judge(project, slot, model, strictness, vid=None):
    """One AI check of the slot's image (or of one of its variants); the result goes into the manifest ("qc"),
    never into the user's verdict ("review")."""
    e = project.entry(slot.key)
    vid = vid or project.active_variant(slot.key)
    rec = project.variant(slot.key, vid) if vid else None
    path = project.variant_path(slot.key, vid) if rec else project.image_path(slot.key)
    result = planner.check_image(path, project.build_dir / "qc" / slot.key,
                                 slot, (rec or e).get("prompt", ""), _world(project), model,
                                 project.config.get("style", ""), _kind(project), strictness)
    qc = {"fits": result["fits"], "problems": result["problems"]}
    if rec:
        project.set_variant_qc(slot.key, vid, qc)
    else:
        e["qc"] = qc
    project.save_manifest()
    return result


def check_variant(project, ctx, key, vid):
    """AI check of one variant of a slot (also an image that is not in the book)."""
    slot = project.slot(key)
    ctx.log(f"Prüfe Variante von „{slot.title}“ …")
    result = _judge(project, slot, project.config["agy"].get("model") or None, qc_strictness(project), vid)
    ctx.log("    passt" if result["fits"] else "    passt nicht – " + "; ".join(result["problems"]))
    return result


def _check_and_edit(project, slot, ctx, fix, model, strictness):
    """Gemini variant of the image check: no new concepts, the same image is edited via its
    interaction chain (max GEMINI_EDITS edits). The result of the last edit is not checked again."""
    for edit in range(GEMINI_EDITS + 1):
        ctx.check()
        result = _judge(project, slot, model, strictness)
        if result["fits"]:
            ctx.log("    Bildkontrolle: passt")
            return True
        ctx.log("    Bildkontrolle: passt nicht – " + "; ".join(result["problems"]))
        if not fix or edit == GEMINI_EDITS:
            return False
        ctx.log(f"    Bearbeitung {edit + 1}/{GEMINI_EDITS} …")
        edit_image(project, slot.key, "Fix only these problems and keep everything else (composition, characters, "
                   "style, colors) unchanged: " + "; ".join(result["problems"]), ctx)
        if edit + 1 == GEMINI_EDITS:
            ctx.log("    Letzte Bearbeitung erledigt – keine weitere Änderung.")
            return True
    return False


def _check_and_fix(project, client, cfg, slot, ctx, fix, rounds, radical=False, strictness=planner.STRICTNESS_DEFAULT):
    """agy image check; on a miss rewrite the prompt and regenerate.

    Normal: the first image plus `rounds` retries with a refined (similar) prompt.
    Radical: up to RADICAL_TRIES images per concept; when they all fail, the prompt is rewritten
    from scratch as a different concept (still on the section's topic), up to RADICAL_CONCEPTS concepts.
    """
    model = project.config["agy"].get("model") or None
    if project.config.get("engine") in ("agy", "gemini"):
        return _check_and_edit(project, slot, ctx, fix, model, strictness)
    concepts, tries = (RADICAL_CONCEPTS, RADICAL_TRIES) if radical else (1, rounds + 1)
    failed = []     # [(prompt, problems)] of concepts that did not work
    for concept in range(concepts):
        for attempt in range(tries):
            ctx.check()
            e = project.entry(slot.key)
            result = _judge(project, slot, model, strictness)
            where = f" (Konzept {concept + 1}/{concepts}, Bild {attempt + 1}/{tries})" if radical else ""
            if result["fits"]:
                ctx.log("    Bildkontrolle: passt" + where)
                return True
            ctx.log("    Bildkontrolle: passt nicht" + where + " – " + "; ".join(result["problems"]))
            if not fix:
                return False
            last_try, last_concept = attempt == tries - 1, concept == concepts - 1
            if last_try and last_concept:
                return False
            if last_try:        # radical: this concept is used up
                failed.append((e.get("prompt", ""), result["problems"]))
                ctx.log(f"    {tries} Bilder ohne Treffer – neues Konzept {concept + 2}/{concepts} wird entworfen …")
                try:
                    prompt = planner.plan_single(slot, _world(project), model, "", project.config.get("style", ""),
                                                 _kind(project), avoid=failed)
                except planner.AgyError as err:
                    ctx.log(f"    Die KI konnte kein neues Konzept liefern: {err}")
                    return False
                e.update(prompt=prompt, include_style=True, seed=random.randint(1, 2**31 - 1))
            else:
                e.update(prompt=result["better_prompt"], include_style=True, seed=random.randint(1, 2**31 - 1))
                ctx.log("    neuer Prompt, generiere neu …")
            _generate(project, client, cfg, slot, ctx)
    return False


def check(project, ctx, keys=None, fix=None, strictness=None):
    """Run the agy image check over existing images (fix=None / strictness=None: use the project setting).

    Images the user has rated (green check or red X) are skipped, their verdict wins. Only when they
    are asked for explicitly via `keys` they are checked too, but never changed."""
    qc = project.config.get("qc", {})
    fix = qc.get("auto_fix", True) if fix is None else fix
    strictness = qc_strictness(project) if strictness is None else strictness
    slots = [s for s in project.sync_manifest(project.all_slots())
             if project.has_image(s.key) and s.kind != "background" and (keys is None or s.key in keys)]
    if keys is None:
        rated = [s for s in slots if project.review(s.key)]
        if rated:
            ctx.log(f"{len(rated)} von dir bewertete(s) Bild(er) übersprungen – deine Bewertung gilt.")
        slots = [s for s in slots if not project.review(s.key)]
    E, client, cfg = _ready(project, ctx) if fix else engine(project)
    ctx.log(f"Strenge der Bildkontrolle: {planner.STRICTNESS[strictness]}")
    misses = 0
    for i, s in enumerate(slots):
        ctx.check()
        ctx.progress(i, len(slots))
        ctx.log(f"[{i + 1}/{len(slots)}] prüfe {s.title}")
        if not _check_and_fix(project, client, cfg, s, ctx, fix=fix and not project.review(s.key),
                              rounds=int(qc.get("rounds", 1)), radical=bool(qc.get("radical")),
                              strictness=strictness):
            misses += 1
    ctx.progress(len(slots), len(slots))
    ctx.log(f"Bildkontrolle fertig: {len(slots) - misses} passen, {misses} auffällig.")


def refine_style(project, wish):
    """Ask agy to turn a free-text style wish into a style suffix + accent colors."""
    from .styles import model_hint
    cfg = project.config
    summary = cfg.get("summary", "") if cfg.get("world") else ensure_summary(project)
    r = planner.refine_style(wish, cfg.get("style", ""), cfg.get("world", ""),
                             model_hint(cfg), cfg["agy"].get("model") or None, _kind(project), summary)
    return styles.readable_accents(r, cfg["theme"])


def suggest_field(project, field, current):
    """agy proposes one project field from the book: field in world | style | negative | wish.
    current = what the other fields hold right now ({'world': ..., 'style': ..., ...})."""
    from .styles import model_hint
    doc = project.document()
    if field == "summary":      # "Vorschlagen" on the summary box: write it again
        return {"text": ensure_summary(project, doc=doc, force=True), "note": ""}
    summary = ensure_summary(project, doc=doc)
    own = "style wish" if field == "wish" else field
    others = {k: v for k, v in current.items() if k != own}
    return planner.suggest_field(field, project.config.get("title") or doc.title or "", _outline(doc), summary,
                                 others, model_hint(project.config),
                                 project.config["agy"].get("model") or None, _kind(project))


PALETTE_KEYS = ("bg", "ink", "accent", "accent2")


def _hex(value):
    """'#abc' / 'ABCDEF' / '#a1b2c3' -> '#a1b2c3', anything else -> None."""
    v = str(value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return "#" + v.lower() if len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v) else None


def contrast(fg, bg):
    """WCAG contrast ratio of two #rrggbb colors (1 … 21)."""
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def suggest_palettes(project):
    """The LLM proposes 4 color palettes for the layout: [{name, note, bg, ink, accent, accent2}].
    It gets the summary of the book, the style suffix and the style wish, not the book text. Palettes with
    invalid colors are dropped; contrast the LLM got wrong is repaired for the dark pages."""
    cfg = project.config
    r = planner.suggest_palettes(ensure_summary(project), cfg.get("title", ""), cfg.get("style", ""),
                                 cfg.get("style_wish", ""), {k: cfg["theme"][k] for k in PALETTE_KEYS},
                                 cfg["agy"].get("model") or None, _kind(project))
    out = []
    for p in r.get("palettes", []):
        colors = {k: _hex(p.get(k)) for k in PALETTE_KEYS}
        if all(colors.values()):
            out.append({"name": str(p.get("name", "")).strip() or f"Palette {len(out) + 1}",
                        "note": str(p.get("note", "")).strip(), **styles.fit_palette(colors, cfg["theme"])})
    if not out:
        raise planner.AgyError("Die KI lieferte keine brauchbare Farbpalette.")
    return out[:4]


COVER_KEYS = ("title", "subtitle", "kicker", "tagline", "running_title", "chapter_label")


def suggest_cover(project, current=None, lang=None):
    """agy proposes all cover and running-head texts from the book (returns a dict with COVER_KEYS).
    For a translation it reads that translation and writes in its language."""
    doc = project.document(lang)
    chapters = [c.full_title for c in doc.chapters]
    current = {k: v for k, v in (current or {}).items() if v}
    r = planner.suggest_cover(doc.title or "", doc.intro_md, chapters, current,
                              project.config["agy"].get("model") or None, _kind(project))
    out = {k: str(r.get(k, "")).strip() for k in COVER_KEYS}
    if not out["title"]:
        out["title"] = doc.title or current.get("title", "")
    if not out["running_title"]:
        out["running_title"] = out["title"].split(":")[0][:30]
    return out


def preview(project, key, ctx, prompt=None, style=None, seed=None):
    """Render one slot with the current settings into .build/ without touching the project images."""
    slot = project.slot(key)
    e = project.entry(key)
    subject = (prompt if prompt is not None else e.get("prompt", "")).strip()
    style = (style if style is not None else project.config.get("style", "")).strip()
    full = f"{subject}, {style}" if style and e.get("include_style", True) else subject
    E, client, cfg = _ready(project, ctx)
    w, h = size_for(slot)
    ctx.log(f"Vorschau: {slot.title}")
    wf = type(client).build_workflow(cfg, full, project.negative(key), w, h, seed or e["seed"])
    img = with_quota_wait(project, ctx, lambda: client.generate(wf, cancelled=ctx.cancelled))
    path = project.build_dir / f"preview-{key}.jpg"
    comfy.save_image(img, path, slot.kind)
    return path


@dataclass
class BuildResult:
    lang: str
    path: Path | None
    pages: int = 0
    error: str = ""


def build(project, ctx, langs=None):
    """Typeset the PDFs of all languages (or of `langs`) one after another and return a BuildResult for
    each. A language that fails (missing file ...) is logged and does not stop the others; the call raises
    only when nothing could be built."""
    langs = list(langs or project.build_languages())
    results = []
    for i, lang in enumerate(langs):
        ctx.check()
        ctx.progress(i, len(langs))
        if len(langs) > 1:
            ctx.log(f"PDF {i + 1}/{len(langs)}: {lang}")
        try:
            results.append(_build_one(project, ctx, lang))
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            ctx.log(f"FEHLER ({lang}): {e}")
            results.append(BuildResult(lang, None, 0, str(e)))
    ctx.progress(len(langs), len(langs))
    if not any(r.path for r in results):
        raise RuntimeError("; ".join(r.error for r in results))
    return results


def _build_one(project, ctx, lang):
    tag = project.lang_tag(lang)
    for issue in (project.alignment_issues(tag) if tag else []):
        ctx.log(f"Achtung ({lang}): {issue} – Bilder werden nach Position zugeordnet.")
    if not ctx.isolate:
        path, pages = layout.render(project, log=ctx.log, lang=tag)
        return BuildResult(lang, path, pages)
    run_isolated(project, "build", ctx, "--lang", lang)
    import subprocess
    out = project.output_for(tag)
    info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True).stdout
    n = next((int(l.split()[-1]) for l in info.splitlines() if l.startswith("Pages:")), 0)
    return BuildResult(lang, out, n)


def analyse(project, key, wish="", ctx=None):
    """agy looks at the current image and returns an improved prompt and negative prompt."""
    from .styles import model_hint
    slot = project.slot(key)
    if not project.has_image(key):
        raise FileNotFoundError("Für diesen Platz gibt es noch kein Bild.")
    e = project.entry(key)
    return planner.analyse_image(project.image_path(key), project.build_dir / "qc" / key, slot,
                                 e.get("prompt", ""), project.negative(key), project.config.get("world", ""),
                                 model_hint(project.config), wish,
                                 project.config["agy"].get("model") or None, project.config.get("style", ""),
                                 _kind(project))


def compress_field(project, kind, text):
    from .styles import model_hint
    return planner.compress(text, kind, model_hint(project.config),
                            project.config["agy"].get("model") or None, _kind(project))
