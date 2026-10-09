#!/usr/bin/env python3
"""Illuminator: illustrated PDFs from Markdown rulebooks and documents.

  python3 illuminator.py gui [PROJEKT]              Oberfläche starten
  python3 illuminator.py new PROJEKT QUELLE          Projekt anlegen (md, docx, pdf, txt), --type document für Readme/Tutorial
  python3 illuminator.py plan PROJEKT [--force]      Bild-Prompts mit agy schreiben
  python3 illuminator.py images PROJEKT [--all]      Fehlende (oder alle) Bilder generieren
  python3 illuminator.py edit PROJEKT KEY "Anweisung"  Bild per Gemini bearbeiten (Interaction-Kette)
  python3 illuminator.py check PROJEKT [--no-fix]    Bilder mit agy prüfen (und unpassende neu machen)
  python3 illuminator.py restructure PROJEKT ORIGINAL.pdf
                                                      Überschriften-Ebenen nach dem Original-PDF richten
  python3 illuminator.py build PROJEKT               PDF setzen
  python3 illuminator.py all PROJEKT                 plan + images + build
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["gui", "new", "plan", "images", "check", "edit", "build", "all", "detect", "restructure"])
    ap.add_argument("project", nargs="?")
    ap.add_argument("source", nargs="?")
    ap.add_argument("instruction", nargs="?", help="edit: was am Bild geändert werden soll")
    ap.add_argument("--type", choices=["rulebook", "document"], default="rulebook",
                    help="new: PnP-Regelwerk (Standard) oder anderes Dokument (Readme, Tutorial, Doku)")
    ap.add_argument("--force", action="store_true", help="plan: vorhandene Prompts neu schreiben")
    ap.add_argument("--all", action="store_true", help="images: auch vorhandene Bilder neu generieren")
    ap.add_argument("--no-fix", action="store_true", help="check: nur prüfen, nichts neu generieren")
    args = ap.parse_args()

    if args.command == "gui":
        from rpdf.gui import run
        return run(args.project)

    if not args.project:
        ap.error("PROJEKT fehlt")
    from rpdf import comfy, pipeline, planner
    ctx = pipeline.Context(log=lambda msg: print(msg, flush=True), progress=lambda d, t: None)
    try:
        run_command(args, ap, ctx)
    except (comfy.ComfyError, planner.AgyError, FileNotFoundError, ValueError, RuntimeError) as e:
        sys.exit(f"Fehler: {e}")


def run_command(args, ap, ctx):
    from rpdf import importer, pipeline, project as proj
    if args.command == "new":
        if not args.source:
            ap.error("QUELLE fehlt")
        root = Path(args.project)
        root.mkdir(parents=True, exist_ok=True)
        src = Path(args.source)
        if src.suffix.lower() not in (".md", ".markdown"):
            src = importer.to_markdown(src, root / (src.stem + ".md"), ctx, proj.DEFAULTS["agy"]["model"], args.type)
        p = proj.create(root, src, content_type=args.type)
        print(f"Projekt angelegt: {p.root} ({len(p.manifest)} Bild-Plätze)")
        return

    p = proj.Project(args.project)
    if args.command == "restructure":
        return restructure(p, args.source, ctx)
    if args.command in ("plan", "all"):
        pipeline.plan(p, ctx, force=args.force)
    if args.command in ("images", "all"):
        pipeline.images(p, ctx, regenerate=args.all)
    if args.command == "edit":
        if not (args.source and args.instruction):
            ap.error("edit PROJEKT KEY \"Anweisung\"")
        pipeline.edit_image(p, args.source, args.instruction, ctx)
    if args.command == "check":
        pipeline.check(p, ctx, fix=False if args.no_fix else None)
    if args.command in ("build", "all"):
        pipeline.build(p, ctx)
    if args.command == "detect":   # internal: gap measurement for filler images
        from rpdf import layout
        layout.detect_fillers(p, log=ctx.log)


def restructure(p, original, ctx):
    """Re-level the headings after the original PDF (typography + table of contents),
    keeping images of slots that change between chapter and section."""
    import re
    import shutil
    from rpdf import importer, mdparse, project as proj
    if not original:
        sys.exit("ORIGINAL.pdf fehlt (das Original-PDF des Regelwerks).")
    src = p.source_path
    old_md = src.read_text(encoding="utf-8")
    toc = importer.toc_from_pdf(original)
    md, report = importer.relevel_from_pdf(old_md, original, toc)
    if report and report[0].startswith("Keine Schriftinformationen"):
        md, promoted = importer.promote_chapters_from_toc(old_md, toc)
        report = [f"Zu Kapiteln gemacht: {', '.join(promoted)}"] if promoted else []
    for line in report:
        ctx.log(line)
    if md == old_md:
        ctx.log("Nichts zu tun: die Gliederung stimmt schon.")
        return
    old_slots = {s.title: s for s in p.slots()}
    backup = src.with_name(src.stem + ".vor-restructure" + src.suffix)
    if not backup.exists():
        shutil.copy(src, backup)
    src.write_text(md, encoding="utf-8")
    new_slots = {s.title: s for s in p.slots()}
    mapping = {old_slots[t].key: new_slots[t].key for t in old_slots.keys() & new_slots.keys()
               if old_slots[t].key != new_slots[t].key}
    moved = proj.rekey(p, mapping)
    p.sync_manifest(list(new_slots.values()))
    p.save()
    missing = [s.title for s in new_slots.values() if not p.has_image(s.key)]
    ctx.log(f"Bilder übernommen: {len(moved)} · ohne Bild: {len(missing)} · Sicherung: {backup.name}")
    if missing:
        ctx.log("Fehlende Bilder mit „Prompts planen“ und „Fehlende Bilder“ erzeugen.")


if __name__ == "__main__":
    main()
