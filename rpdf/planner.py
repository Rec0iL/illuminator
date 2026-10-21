"""Write image prompts with the agy CLI (Gemini) instead of spending Claude tokens.

agy only receives text in the prompt and returns JSON; it runs in an empty
temp directory and is never given file or shell permissions.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CHAPTER_TEXT_LIMIT = 14000
SUMMARY_TEXT_LIMIT = 12000

RULEBOOK_RULES = """Rules for every prompt:
- One concrete, vivid scene that shows what the section is about (its mechanic, theme or example),
  set in the world/genre of this rulebook.
- English, 25-60 words: subjects, action, setting, mood, camera angle. Wide landscape composition
  unless stated otherwise.
- Do NOT add art-style words (medium, line art, rendering, quality tags) - a style suffix is appended automatically.
- No readable text, letters, numbers, logos, signs with writing, UI elements or panels.
- People should match the setting. No monsters, creatures or wings unless the section is about them.
- Vary characters, places and camera angles between sections so the book does not repeat itself."""

DOCUMENT_RULES = """Rules for every prompt:
- One concrete, clear illustration that makes visible what the section is about: a scene, object, tool,
  workflow, place or visual metaphor from the domain of this document. Show the real subject matter
  rather than generic stock imagery; for abstract topics (configuration, interfaces, theory) use a
  fitting visual metaphor instead of screens or code.
- English, 25-60 words: subjects, action, setting, mood, camera angle. Wide landscape composition
  unless stated otherwise.
- Do NOT add art-style words (medium, line art, rendering, quality tags) - a style suffix is appended automatically.
- No readable text, letters, numbers, code, logos, screenshots, UI elements, charts or labeled diagrams.
- Stay realistic and inside the document's domain: no fantasy, monsters, magic or sci-fi elements unless
  the topic itself is about them. People, if shown, match the audience and setting.
- Vary subjects, places and camera angles between sections so the document does not repeat itself."""

PROFILES = {
    "rulebook": dict(
        noun="tabletop RPG rulebook", book="rulebook", rules=RULEBOOK_RULES, world_label="World of the book",
        judge_misfit="Elements that do not belong (monsters or wings when not asked for, wrong era, wrong genre)?",
        judge_role="an art director of a published rulebook",
        world_desc="describing genre, setting, era and tone, for other illustrators",
        style_for="that suits this book's genre",
        cover="a portrait-format cover scene with the book's iconic characters in dramatic action; keep the "
              "lower third as calm dark empty space for the title.",
        background="an abstract, dark, low-contrast texture fitting the setting, for page backgrounds; "
                   "no people, no objects in focus.",
        cover_texts='subtitle: a short subtitle line, e.g. the system or genre ("Cinematic Action Roleplaying"); '
                    'empty if none fits.\nkicker: a short line over the title in capital letters, 2-4 keywords '
                    'separated by " · " (genre, setting, tone); empty if none fits.',
        convert_extra=""),
    "document": dict(
        noun="document (README, tutorial, guide, documentation or similar)", book="document",
        rules=DOCUMENT_RULES, world_label="Topic and tone of the document",
        judge_misfit="Elements that do not belong (fantasy or sci-fi elements, wrong industry or era, a different "
                     "subject than the section)? Does it show the topic instead of generic stock imagery?",
        judge_role="an art director of a published technical or educational document",
        world_desc="describing the topic, domain, audience and tone, for other illustrators",
        style_for="that suits this document's subject and audience",
        cover="a portrait-format cover illustration that symbolizes the document's subject in one clear "
              "image; keep the lower third as calm empty space for the title.",
        background="a subtle, low-contrast abstract texture or pattern fitting the topic, for page backgrounds; "
                   "no people, no objects in focus.",
        cover_texts='subtitle: a short subtitle line that says what the document is (for example "A practical '
                    'guide" or "User documentation"); empty if none fits.\nkicker: a short line over the title '
                    'in capital letters, 2-4 keywords separated by " · " (topic, audience, kind of document); '
                    'empty if none fits.',
        convert_extra='- Keep code, commands, file paths and configuration exactly as written: inline code in '
                      'backticks, code blocks in fenced ``` blocks with their language if known.\n'),
}
RULES = RULEBOOK_RULES      # kept for callers that still import it


def profile(kind):
    return PROFILES.get(kind or "rulebook", PROFILES["rulebook"])


def style_for_planning(style):
    """Tell agy which art style is applied later, so motifs fit it (without writing style words)."""
    if not (style or "").strip():
        return ""
    return (f"Art style that is applied to every image automatically: {style.strip()}\n"
            "Write motifs that read well in this style (clear shapes, silhouettes, poses, colors) and avoid "
            "details it cannot show. Still do not write style words into the prompt.")


def style_for_judging(style):
    """The chosen art style is intentional: agy must not criticize it when checking images."""
    if not (style or "").strip():
        return ""
    return (f"Art style of the book (chosen on purpose, applied to every image): {style.strip()}\n"
            "This look is intentional. Never criticize it and never ask to change it - this includes "
            "pixelation, a low-resolution or retro look, a limited palette, dithering, flat shading, outlines, "
            "grain, stylized or simplified anatomy and painterly strokes. Judge only the motif, the fit to the "
            "section, text artifacts and elements that do not belong, always within what the style can show.")


def wish_for_planning():
    """The user's standing wish for all image prompts (project setting "prompt_wish"), as a prompt block."""
    wish = BACKEND["prompt_wish"].strip()
    if not wish:
        return ""
    return ("Instruction from the user for EVERY image prompt (it may be written in any language; the prompt "
            "itself stays English). Follow it strictly. It overrides the rules above wherever they disagree, "
            f"for example about realism or which objects may appear:\n{wish}")


def wish_for_judging():
    """Same wish for the image check: it is intentional and the image has to follow it."""
    wish = BACKEND["prompt_wish"].strip()
    if not wish:
        return ""
    return ("Instruction from the user that applies to every motif of this book (chosen on purpose, may be "
            f"written in any language): {wish}\n"
            "Never criticize the result as unrealistic or out of place because of it. Judge whether the image "
            "follows it: an element that contradicts the instruction (for example the ordinary version of "
            "something the user wants replaced) is a problem; better_prompt must keep following it.")


class AgyError(RuntimeError):
    pass


# which CLI writes prompts and judges images: "agy" (Antigravity) or "claude" (Claude Code), and the
# project's standing wish for all image prompts; both come from the project config (see configure)
BACKEND = {"llm": "agy", "claude_model": "sonnet", "prompt_wish": ""}
CLAUDE_MODELS = ["sonnet", "opus", "haiku"]


def configure(cfg):
    """Take the prompt generator (cfg["llm"], cfg["claude"]["model"]) and the prompt wish from a project config."""
    BACKEND["prompt_wish"] = cfg.get("prompt_wish") or ""
    BACKEND["llm"] = cfg.get("llm") or "agy"
    BACKEND["claude_model"] = (cfg.get("claude") or {}).get("model") or "sonnet"


def agy_available():
    return shutil.which("agy") is not None


def available():
    """Is the CLI of the selected prompt generator installed?"""
    return shutil.which(BACKEND["llm"]) is not None


def _claude(prompt, schema, cwd, tools="", timeout=600):
    """One Claude Code print-mode turn with structured output. tools="" = no tools; "Read" = file viewing only
    (pre-approved, nothing else; no permission bypass)."""
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema),
           "--model", BACKEND["claude_model"], "--tools", tools, "--no-session-persistence"]
    if tools:
        cmd += ["--allowedTools", tools]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    out = proc.stdout.strip()
    start = out.find("{")
    if start < 0:
        raise AgyError(f"claude lieferte keine Antwort: {(proc.stderr or out)[-600:]}")
    data = json.loads(out[start:])
    if data.get("is_error"):
        raise AgyError(f"claude-Fehler: {str(data.get('result') or data.get('subtype'))[:400]}")
    data.setdefault("response", data.get("result"))
    return data


def list_models():
    if BACKEND["llm"] == "claude":
        return list(CLAUDE_MODELS)
    try:
        out = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    return [line.split("\t")[0] for line in out.splitlines() if "\t" in line]


NO_TOOLS = """IMPORTANT: Everything you need is in this message. Do not use any tools: do not run
commands, do not read, create or edit files, do not browse. Think, then answer only with the
requested JSON.

"""
RETRY_NOTE = """Your previous attempt tried to use a tool, which is not allowed here, and produced no
answer. Do not use tools this time; return the JSON answer directly.

"""


def _find_json(text, required):
    """Last JSON object in `text` that has all `required` keys, or None."""
    dec = json.JSONDecoder()
    for i in [i for i, ch in enumerate(text) if ch == "{"][::-1]:
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and required <= obj.keys():
            return obj
    return None


def _unwrap(obj, required):
    """Models sometimes put a whole ```json {...}``` answer into a single field.
    Unpack such nesting until the fields hold plain values."""
    for _ in range(3):
        nested = None
        for key in required:
            value = obj.get(key)
            if isinstance(value, str) and value.lstrip().startswith(("```", "{")):
                nested = _find_json(value, required)
                if nested:
                    break
        if not nested:
            return obj
        obj = nested
    return obj


def _structured(data, schema):
    """structured_output, or a JSON object with the required keys found in the response text."""
    required = set(schema.get("required", []))
    obj = data.get("structured_output") or _find_json(data.get("response") or "", required)
    return _unwrap(obj, required) if obj else None


def fast_variant(model):
    """Same model family with low reasoning effort (agy encodes effort in the model name)."""
    return re.sub(r"-(medium|high)$", "-low", model) if model else model


def run(prompt, schema, model=None, timeout=600):
    """Run one agy print-mode turn and return its structured JSON output.

    agy runs in an empty temp dir without auto-approved permissions; if it still
    tries a tool (which then gets denied) and comes back empty, ask once more.
    """
    if not available():
        raise AgyError(f"{BACKEND['llm']} wurde nicht gefunden (CLI im PATH?).")
    if BACKEND["llm"] == "claude":
        with tempfile.TemporaryDirectory(prefix="rpdf-claude-") as tmp:
            for attempt in range(2):
                data = _claude(NO_TOOLS + prompt, schema, tmp, timeout=timeout)
                result = _structured(data, schema)
                if result is not None:
                    return result
        raise AgyError(f"claude lieferte kein gültiges JSON: {str(data.get('response'))[:300]}")
    data = {}
    for attempt in range(2):
        full = NO_TOOLS + (RETRY_NOTE if attempt else "") + prompt
        with tempfile.TemporaryDirectory(prefix="rpdf-agy-") as tmp:
            schema_path = Path(tmp) / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            cmd = ["agy", "-p", full, "--output-format", "json",
                   "--json-schema", str(schema_path), "--print-timeout", f"{timeout}s"]
            if model:
                cmd += ["--model", model]
            proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout + 60)
        out = proc.stdout.strip()
        start = out.find("{")
        if start < 0:
            raise AgyError(f"agy lieferte keine Antwort: {(proc.stderr or out)[-600:]}")
        data = json.loads(out[start:])
        if data.get("status") == "SUCCESS":
            result = _structured(data, schema)
            if result is not None:
                return result
        if not data.get("denied_actions"):
            break
    denied = ", ".join(a.get("display_name", a.get("action", "?")) for a in data.get("denied_actions", []))
    if denied:
        raise AgyError(f"agy wollte ein Werkzeug benutzen ({denied}), das ist hier gesperrt, "
                       "und hat danach keine Antwort geliefert – auch nicht im zweiten Versuch.")
    raise AgyError(f"agy-Fehler ({data.get('status')}): {(data.get('error') or data.get('response') or '')[:400]}")


def summarize(title, outline, excerpts, model=None, kind="rulebook"):
    """Short summary of the whole book. Written once (the first time an LLM function needs to know the
    book) and stored in the project; later calls send this instead of excerpts of the text."""
    pr = profile(kind)
    prompt = f"""You summarize a {pr['noun']} so that other assistants (art direction, color choice, cover
texts, style suggestions) can work from your summary without reading the book.
Book title: {title or '(unknown)'}

Table of contents:
{outline[:3000]}

Excerpts from the start of each chapter:
{excerpts[:SUMMARY_TEXT_LIMIT]}

Write the summary in English, 80-150 words, plain text without lists: what this is, its topic/genre and
setting, the audience, the tone and mood, what the chapters cover, and the concrete places, characters,
objects or imagery that stand out (they inspire illustrations and colors). Use only facts from the text."""
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    return str(run(prompt, schema, model)["summary"]).strip()


def plan_global(outline, summary, title, model=None, kind="rulebook"):
    """Genre summary, a style suggestion, theme colors, cover and background prompts."""
    pr = profile(kind)
    prompt = f"""You are the art director for an illustrated {pr['noun']} PDF.
Book title: {title}

Table of contents:
{outline}

Summary of the book:
{summary}

Return:
- world: 2-3 sentences (English) {pr['world_desc']}.
- style: a comma-separated art-style suffix (English, 15-35 words) for an image model {pr['style_for']}: medium, line work, lighting, color mood, plus quality tags. No subjects.
- accent, accent2: two hex colors for the PDF layout (headings and highlights) that fit the style,
  readable on a near-black background.
- cover: {pr['cover']}
- background: {pr['background']}

{pr['rules']}

{wish_for_planning()}"""
    schema = {"type": "object", "properties": {
        "world": {"type": "string"}, "style": {"type": "string"},
        "accent": {"type": "string"}, "accent2": {"type": "string"},
        "cover": {"type": "string"}, "background": {"type": "string"}},
        "required": ["world", "style", "accent", "accent2", "cover", "background"]}
    return run(prompt, schema, model)


def plan_chapter(chapter, slots, world, model=None, style="", kind="rulebook"):
    """Prompts for the chapter banner and its section slots: {key: prompt}."""
    from .project import strip_filler_title     # project imports this module
    text = chapter.intro_md + "".join(f"\n\n### {s.title}\n{s.body_md}" for s in chapter.sections)
    def describe(s):
        if s.kind == "chapter":
            return f'chapter banner for "{s.title}"'
        if s.kind == "filler":
            shape = "portrait" if s.aspect < 0.9 else ("square" if s.aspect < 1.25 else "landscape")
            return (f'additional {shape} illustration for "{strip_filler_title(s.title)}" '
                    f'- a different moment, place or character than its section image')
        return f'section "{s.title}"'
    wanted = "\n".join(f"- {s.key}: {describe(s)}" for s in slots)
    pr = profile(kind)
    prompt = f"""You write prompts for an image model that illustrates a {pr['noun']}.
{pr['world_label']}: {world}
{style_for_planning(style)}

Chapter "{chapter.full_title}":
{text[:CHAPTER_TEXT_LIMIT]}

Write one prompt for each of these keys:
{wanted}

{pr['rules']}

{wish_for_planning()}"""
    schema = {"type": "object", "properties": {"images": {"type": "array", "items": {
        "type": "object", "properties": {"key": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["key", "prompt"]}}}, "required": ["images"]}
    result = run(prompt, schema, model)
    keys = {s.key for s in slots}
    out = {}
    for item in result.get("images", []):
        key = item.get("key", "").strip().strip("`'\"[]- ").lower()
        if key in keys and item.get("prompt", "").strip():
            out[key] = item["prompt"].strip()
    return out


META_TEXT = ("successfully", "requested json", "json format", "as requested", "here is", "generated prompt")


def _real_prompt(text):
    """True if `text` is an actual image description and not a status message about one."""
    t = (text or "").strip()
    return len(t.split()) >= 10 and not any(m in t.lower() for m in META_TEXT)


def _orientation(slot):
    aspect = getattr(slot, "aspect", None)
    if slot.kind in ("cover", "background"):
        return "portrait"
    if slot.kind == "filler" and aspect:
        return "portrait" if aspect < 0.9 else "square" if aspect < 1.25 else "landscape"
    return "wide landscape"


def plan_single(slot, world, model=None, hint="", style="", kind="rulebook", avoid=None):
    """New prompt for one slot, optionally steered by a user hint.

    avoid: [(prompt, problems)] of concepts that were already tried and failed; the new description
    must be a clearly different concept that still fits the section."""
    pr = profile(kind)
    if avoid:
        tried = "\n".join(f"- {p} (problems: {'; '.join(pb) or 'did not fit'})" for p, pb in avoid)
        hint = (hint + " " if hint else "") + (
            "Radical rethink: the concepts below were tried and did not work. Do not repeat or paraphrase "
            "them. Invent a completely different concept for this section - other main subject, other place, "
            "other composition and camera angle - that still fits its topic.\nTried concepts:\n" + tried)
    prompt = f"""You write prompts for an image model that illustrates a {pr['noun']}.
{pr['world_label']}: {world}
{style_for_planning(style)}

Section "{slot.title}":
{slot.context_md[:CHAPTER_TEXT_LIMIT]}

Write one fresh image description for this {slot.kind} image ({_orientation(slot)} format).{(' User wish: ' + hint) if hint else ''}
{'Keep the lower third dark and empty for the title.' if slot.kind == 'cover' else ''}
Put the description itself into "image_prompt" - not a note that you wrote it.

{pr['rules']}

{wish_for_planning()}"""
    schema = {"type": "object", "properties": {"image_prompt": {
        "type": "string", "description": "The image description itself, 25-60 English words."}},
        "required": ["image_prompt"]}
    for _ in range(2):
        text = run(prompt, schema, model)["image_prompt"].strip()
        if _real_prompt(text):
            return text
        prompt += "\n\nYour last answer was a status message, not an image description. Return the description."
    raise AgyError(f"Die KI lieferte keine Bildbeschreibung, sondern: „{text[:120]}“")


def _run_on_image(image_path, work_dir, prompt, schema, model=None):
    """agy call that may read exactly one file: a downscaled copy of the image.

    work_dir contains nothing but that copy. agy runs there with --sandbox and normal
    permission handling (no auto-approval); it can read the image because the folder
    lies in one of agy's trusted workspaces.
    """
    from PIL import Image

    work_dir.mkdir(parents=True, exist_ok=True)
    for old in work_dir.iterdir():
        old.unlink()
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        im.thumbnail((768, 768))
        im.save(work_dir / "image.jpg", "JPEG", quality=85)
    schema = {**schema, "properties": {"could_view": {"type": "boolean"}, **schema["properties"]},
              "required": ["could_view"] + schema["required"]}
    head = ("Look at the image file image.jpg in the current directory with your file viewing tool. "
            "Only view that file: do not run commands, do not create or edit files.\n"
            "Set could_view=false if you could not open the image.\n\n")
    if not available():
        raise AgyError(f"{BACKEND['llm']} wurde nicht gefunden (CLI im PATH?).")
    if BACKEND["llm"] == "claude":
        data = _claude(head + prompt, schema, work_dir, tools="Read", timeout=360)
        result = _structured(data, schema)
        if not result:
            raise AgyError(f"claude-Fehler bei der Bildanalyse: {str(data.get('response'))[:400]}")
        if not result.get("could_view"):
            raise AgyError("claude konnte das Bild nicht öffnen.")
        return result
    with tempfile.TemporaryDirectory(prefix="rpdf-agy-") as tmp:
        schema_path = Path(tmp) / "schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        cmd = ["agy", "-p", head + prompt, "--output-format", "json", "--json-schema", str(schema_path),
               "--sandbox", "--print-timeout", "300s"]
        if model:
            cmd += ["--model", model]
        proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=360)
    out = proc.stdout.strip()
    start = out.find("{")
    if start < 0:
        raise AgyError(f"agy lieferte keine Antwort: {(proc.stderr or out)[-600:]}")
    data = json.loads(out[start:])
    result = _structured(data, schema) if data.get("status") == "SUCCESS" else None
    if not result:
        raise AgyError(f"agy-Fehler bei der Bildanalyse: {(data.get('error') or data.get('response') or '')[:400]}")
    if not result.get("could_view"):
        raise AgyError("agy konnte das Bild nicht öffnen. Der Projektordner muss in agys "
                       "'trustedWorkspaces' liegen (siehe README, Abschnitt Bildkontrolle).")
    return result


IMAGE_FORMAT_NOTE = ("Do NOT judge or mention image size, aspect ratio or orientation (portrait/landscape) - "
                     "the tool sets the format on purpose.")


STRICTNESS = {1: "locker", 2: "mittel", 3: "streng"}     # image check: 3 = follow the prompt closely
STRICTNESS_DEFAULT = 3


def _judge_criteria(pr, strictness):
    """What the image check asks for, from strict (3, the motif must match the prompt) to lenient (1)."""
    if strictness == 1:
        return f"""Judge it like {pr['judge_role']}, but be lenient: the intended motif is only a guideline,
do not judge how closely the image follows it.
Reject the image (fits=false) only if:
- it is broken: strong artifacts, an unreadable or garbled image, garbled text or letters, deformed
  subjects (extra limbs, fused hands, broken faces), or
- it is clearly off-topic: it has nothing to do with the section or the {pr['book']}'s topic.
Otherwise ask yourself: does it still fit the section and work as an illustration? If yes, fits=true.
{IMAGE_FORMAT_NOTE}"""
    if strictness == 2:
        return f"""Judge it like {pr['judge_role']}, without insisting on the exact motif:
- The image does not have to match the intended motif in every detail. Other subjects, a different
  composition or missing details are fine as long as it still clearly fits the section and the {pr['book']}'s topic.
- Garbled or readable text, letters, logos? Broken anatomy (extra limbs, fused hands, faces)?
- {pr['judge_misfit']}
{IMAGE_FORMAT_NOTE}
fits=true if it clearly fits the section and is usable in print, even if it differs from the intended motif."""
    return f"""Judge it like {pr['judge_role']}:
- Does it show the intended motif and fit the section and the {pr['book']}'s topic?
- Garbled or readable text, letters, logos? Broken anatomy (extra limbs, fused hands, faces)?
- {pr['judge_misfit']}
{IMAGE_FORMAT_NOTE}
fits=true only if it is good enough to print."""


def check_image(image_path, work_dir, slot, prompt_used, world, model=None, style="", kind="rulebook",
                strictness=STRICTNESS_DEFAULT):
    """Let agy look at one generated image and judge whether it fits its section.
    strictness: 3 = streng (motif as in the prompt), 2 = mittel, 1 = locker (only broken or off-topic images fail)."""
    pr = profile(kind)
    prompt = f"""The image was generated to illustrate a {pr['noun']}.
{pr['world_label']}: {world or 'unknown'}
{style_for_judging(style)}
{wish_for_judging()}
Section: "{slot.title}"
Section text (excerpt):
{slot.context_md[:1500]}

Intended motif: {prompt_used}

{_judge_criteria(pr, strictness)}
problems: short German bullet points. better_prompt: an improved English motif prompt (25-60 words,
no style words, no text in the image) that avoids the problems."""
    schema = {"type": "object", "properties": {
        "fits": {"type": "boolean"}, "problems": {"type": "array", "items": {"type": "string"}},
        "better_prompt": {"type": "string"}}, "required": ["fits", "problems", "better_prompt"]}
    return _run_on_image(image_path, work_dir, prompt, schema, model)


def analyse_image(image_path, work_dir, slot, prompt_used, negative_used, world, model_hint, wish="", model=None,
                  style="", kind="rulebook"):
    """agy looks at the image and fine-tunes motif prompt and negative prompt for the next render."""
    pr = profile(kind)
    prompt = f"""You are fine-tuning the prompt for an image that illustrates a {pr['noun']}.
{model_hint}
{pr['world_label']}: {world or 'unknown'}
{style_for_judging(style)}
{wish_for_judging()}
Section: "{slot.title}"
Section text (excerpt):
{slot.context_md[:1500]}

Motif prompt used: {prompt_used}
Negative prompt used: {negative_used or '(none)'}
{('User wish for the next version: ' + wish) if wish else ''}

Compare the image with the section and the motif prompt. Then write:
- analysis: 2-4 short German sentences: what works, what is off (motif, composition, anatomy,
  text artifacts, mood, details that contradict the section).
- prompt: the improved English motif prompt (25-60 words, no style words, no text in the image),
  keeping what works and fixing what is off{', and realizing the user wish' if wish else ''}.
- negative: an English negative prompt for this image: the generic quality terms plus terms that
  target the concrete problems you saw (comma-separated, max 40 words). Leave out every term that
  contradicts the art style above (for pixel art, for example, "pixelated", "lowres", "blurry",
  "jpeg artifacts" must not appear).
{IMAGE_FORMAT_NOTE}"""
    schema = {"type": "object", "properties": {
        "analysis": {"type": "string"}, "prompt": {"type": "string"}, "negative": {"type": "string"}},
        "required": ["analysis", "prompt", "negative"]}
    return _run_on_image(image_path, work_dir, prompt, schema, model)


COMPRESS_TASK_DOCUMENT = {"world": "a short description of the document's topic, audience and tone that is given to "
                                   "other illustrators"}

COMPRESS_TASK = {
    "summary": "a short summary of the book that other assistants use as background for art direction",
    "style": "a style suffix that is appended to every image prompt (medium, line work, palette, "
             "lighting, quality tags)",
    "negative": "a negative prompt (things the image model must avoid)",
    "world": "a short description of the book's world, genre and tone that is given to other illustrators",
    "wish": "a free-text style wish from the user",
}


SUGGEST_TASK = {
    "world": ("world: 2-3 sentences (English) {world_desc}. Facts only from the book; no style words."),
    "style": ("style: a comma-separated art-style suffix (English, 15-35 words) for an image model "
              "{style_for}: medium, line work, lighting, color mood, plus quality tags that work for the model. "
              "No subjects, no scenes, no text or logos. Realize the user's style wish if there is one."),
    "negative": ("negative: a comma-separated negative prompt (English, at most 40 words): generic quality "
                 "flaws plus typical flaws for this kind of subject (anatomy, text artifacts, watermark). It must "
                 "not contradict the art style (for pixel art, for example, leave out pixelated, lowres, blurry, "
                 "jpeg artifacts)."),
    "wish": ("wish: ONE short sentence in the language of the book, written the way a user would type a style "
             "wish ('düster wie Dark Souls, aber farbig'): a promising, concrete visual direction for this book "
             "(medium, reference look, mood, palette). No subjects or scenes."),
}


def suggest_field(field, title, outline, summary, current, model_hint, model=None, kind="rulebook"):
    """Propose the content of one project field (world/topic, style, negative prompt, style wish)."""
    pr = profile(kind)
    task = SUGGEST_TASK[field].format(world_desc=pr["world_desc"], style_for=pr["style_for"])
    have = "\n".join(f"- {k}: {v}" for k, v in current.items() if v) or "(nothing yet)"
    prompt = f"""You help set up the image generation for an illustrated {pr['noun']} PDF.
{model_hint}
Book title: {title or '(unknown)'}
Outline:
{outline[:1800]}
Summary of the book:
{summary}

What the user already has (use it, stay consistent with it): 
{have}

Write a suggestion for this field:
{task}
note: one short German sentence on what the suggestion is based on."""
    key = field
    schema = {"type": "object", "properties": {key: {"type": "string"}, "note": {"type": "string"}},
              "required": [key, "note"]}
    r = run(prompt, schema, model)
    return {"text": str(r.get(key, "")).strip(), "note": str(r.get("note", "")).strip()}


def compress(text, kind, model_hint, model=None, content="rulebook"):
    """Remove redundant or ineffective parts from a prompt field."""
    prompt = f"""You are an expert prompt engineer for image-generation models.
{model_hint}

This text is {(COMPRESS_TASK_DOCUMENT if content == "document" else {}).get(kind) or COMPRESS_TASK[kind]}:
---
{text}
---
Make it shorter and more effective for this model: remove duplicates and near-duplicates,
contradictions, filler words, vague or ineffective phrases and things the model cannot use.
Keep every distinct, useful idea and the original language of the text. Do not add new ideas.
compressed: the result. removed: short German list of what you removed and why."""
    schema = {"type": "object", "properties": {
        "compressed": {"type": "string"}, "removed": {"type": "array", "items": {"type": "string"}}},
        "required": ["compressed", "removed"]}
    return run(prompt, schema, model)


def refine_style(wish, base_style, world, model_hint, model=None, kind="rulebook", summary=""):
    """Turn a free-text style wish ("should look like One Piece") into a style suffix."""
    prompt = f"""You are an expert prompt engineer for image-generation models.
{model_hint}
{profile(kind)['world_label']} being illustrated: {world or profile(kind)['noun']}
{('Summary of the book: ' + summary) if summary else ''}
Current style suffix: {base_style or '(none)'}
User's style wish (any language): {wish}

Write an improved style suffix that realizes the user's wish for this model: concrete, comma-separated
English tags/phrases for medium, artist or franchise look, line work, color palette, lighting, plus
quality tags. 15-40 words. Keep parts of the current suffix that do not contradict the wish.
Describe only style, never subjects or scenes. No text, logos or letters.
Also pick two hex accent colors for the PDF layout that match the style and are readable on a
near-black background, and explain your choice in one short sentence (German)."""
    schema = {"type": "object", "properties": {
        "style": {"type": "string"}, "accent": {"type": "string"}, "accent2": {"type": "string"},
        "note": {"type": "string"}}, "required": ["style", "accent", "accent2", "note"]}
    return run(prompt, schema, model)


def suggest_palettes(summary, title, style, wish, current, model=None, kind="rulebook"):
    """Four different color palettes (bg, ink, accent, accent2) for the PDF layout that fit the book's topic.
    Gets the summary of the book and the style the user wants, never the book text."""
    pr = profile(kind)
    prompt = f"""You design the color palette for the PDF layout of a {pr['noun']}.
Book title: {title or '(none)'}
Summary of the book: {summary or '(none)'}
Art style of the illustrations (applied to every image; the palettes must go with it): {style or '(none)'}
{('The user wants this look: ' + wish) if wish else ''}
Current palette: {current}

Propose exactly 4 palettes that fit the topic and mood of this {pr['book']}. Make them clearly different from
each other (different hue families and moods), not four variations of one idea.
Every palette has:
- name: 2-4 German words that name the mood (for example "Neon-Boxengasse").
- bg: the page background. It must be very dark (near black, lightness below 12%), it may be tinted
  towards the palette's mood. The layout is built for dark pages.
- ink: the body text color, light and easy to read on bg (contrast at least 10:1).
- accent: headings, tables, links. Clearly readable on bg (contrast at least 4.5:1).
- accent2: chapter labels, initials and rule lines. Readable on bg and clearly different from accent.
- note: one short German sentence why it fits the {pr['book']}.
All colors as #rrggbb hex."""
    item = {"type": "object", "properties": {k: {"type": "string"} for k in (
        "name", "bg", "ink", "accent", "accent2", "note")},
        "required": ["name", "bg", "ink", "accent", "accent2", "note"]}
    schema = {"type": "object", "properties": {"palettes": {"type": "array", "items": item}},
              "required": ["palettes"]}
    return run(prompt, schema, model)


def suggest_cover(title, intro, chapters, current, model=None, kind="rulebook"):
    """Propose the cover and running-head texts from the book's own text."""
    pr = profile(kind)
    prompt = f"""You fill in the cover and page-header texts of a {pr['noun']} PDF.
Write every text in the language of the book (the text below), never translate it.
Use only facts that are in the text: do not invent version numbers, authors, editions or claims.
Book title found in the text: {title or '(none)'}
Chapter titles: {chapters[:30]}
Start of the book:
---
{intro[:2500]}
---
Texts the user already has (improve or keep them where they are fine): {current}

title: the book's title as it should stand on the cover (no subtitle).
{pr['cover_texts']}
tagline: one short sentence under the title (what the book is, who it is for); reuse a subtitle or tagline line
  of the text when there is one.
running_title: the short book name for the page header, at most 30 characters.
chapter_label: the word for "Chapter" in the book's language (for example "Kapitel")."""
    schema = {"type": "object", "properties": {k: {"type": "string"} for k in (
        "title", "subtitle", "kicker", "tagline", "running_title", "chapter_label")},
        "required": ["title", "subtitle", "kicker", "tagline", "running_title", "chapter_label"]}
    return run(prompt, schema, model)


def convert_to_markdown(text, previous_headings, first, model=None, kind="rulebook"):
    """Turn a chunk of extracted rulebook text into structured Markdown (used by the importer)."""
    pr = profile(kind)
    prompt = f"""Convert this chunk of text extracted from a {pr['noun']} into clean Markdown.
Put the Markdown itself into the "markdown" field - plain Markdown, not JSON and not a code block.
- Keep the wording exactly as it is (same language, no summarizing, no additions, no translation).
- Restore structure: {'"# " only for the book title, ' if first else 'never use "# ", '}"## " for chapters,
  "### " for sections, "#### " below that; bullet and numbered lists; Markdown tables for tabular data.
- Remove page numbers, running headers/footers and hyphenation at line ends.
{pr['convert_extra']}- Keep every image reference like ![](media/img-012-000.png) exactly as written, on its own line,
  at the position where it appears in the text.
- Headings so far (keep the same levels for the same kind of heading): {previous_headings[-12:]}

Text:
{text}"""
    schema = {"type": "object", "properties": {"markdown": {"type": "string"}}, "required": ["markdown"]}
    return run(prompt, schema, fast_variant(model), timeout=900)["markdown"]
