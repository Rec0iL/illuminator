# Rulebook PDF

Macht aus einem Regelwerk (Markdown, Word, PDF oder Text) ein illustriertes PDF im Stil eines gedruckten Regelbuchs: Cover, Kapitel-Banner, ein Bild pro Abschnitt, zweispaltiger Satz, Kopfzeilen und Seitenzahlen.

```
Regelwerk ──► Prompts (agy / Gemini) ──► Bilder (ComfyUI) ──► PDF (WeasyPrint)
```

Die Bild-Prompts schreibt **agy** (Antigravity CLI) statt Claude, das spart Claude-Tokens. agy bekommt dabei nur Text und gibt JSON zurück; es läuft in einem leeren Temp-Ordner und erhält keine Datei- oder Shell-Rechte.

## Voraussetzungen

- Einmalig die Schriften laden (nicht im Repo): `python3 fetch_fonts.py` (Google Fonts, SIL OFL)
- Python 3.10+ mit `pip install -r requirements.txt` (PySide6, markdown, WeasyPrint, Pillow)
- [agy](https://antigravity.google) im `PATH`, angemeldet
- ComfyUI (z.B. über Pinokio) mit einem Bildmodell, Standard: `http://127.0.0.1:8188`
- Für PDF-Import: `pdftotext` (Paket poppler-utils)

## Start

```bash
python3 tools/rulebook-pdf/rulebook_pdf.py gui
```

Ohne Argument öffnet die Oberfläche das zuletzt benutzte Projekt. **Letzte Projekte** (neben „Neues Projekt…“) listet die zehn zuletzt geöffneten Projekte.

Bilder im Markdown mit relativem Pfad (z.B. `docs/screenshots/x.png`) werden zuerst neben der Quelldatei gesucht, dann im Projektordner `bilder/` und im Projektordner selbst, jeweils mit Pfad und nur mit Dateiname. Nicht gefundene Bilder meldet der Build als „WARNUNG: Bild nicht gefunden“. Das KINETIK-Projekt liegt unter `assets/pdf/`.

## Ablauf in der Oberfläche

1. **Neues Projekt…**: Regelwerk wählen (`.md`, `.docx`, `.pdf`, `.txt`) und einen leeren Projektordner. Word-Dateien werden lokal über ihre Überschriftenstile umgewandelt, PDF und Text abschnittsweise von agy (Wortlaut bleibt erhalten). Die Überschriften-Ebenen richtet das Tool danach am Original aus: Kapitel sind die Einträge der ersten Ebene im Inhaltsverzeichnis (Word-Stil mit Punktlinien und Seitenzahlen); Teile vor dem ersten solchen Kapitel, die in der größten Schrift gesetzt sind, werden ebenfalls Kapitel. Darunter ergeben die Schriftgrößen der Überschriften die Ebenen, und ein Untertitel in derselben Zeile wird mit seiner Überschrift zusammengeführt. Das Verzeichnis selbst landet nicht im Text. Für ein bereits importiertes Projekt: `rulebook_pdf.py restructure PROJEKT ORIGINAL.pdf` (Sicherung `*.vor-restructure.md`, Bilder von Plätzen, die nur zwischen Kapitel und Abschnitt wechseln, werden mitgenommen). **Das erzeugte Markdown kurz prüfen** (Toolbar: „Quelle öffnen“), vor allem bei PDFs.
2. **Buch**: oben der Schalter **Inhalt** (PnP-Regelwerk / Anderes Dokument, siehe „Inhaltstyp“). Titel, Untertitel, Kicker, Tagline, Kopfzeile, Kapitel-Label, Farben, Schriftgröße, Blocksatz. „Alles automatisch ausfüllen (agy)“ liest den Anfang des Regelwerks und schlägt alle Cover- und Kopfzeilentexte in der Sprache des Buches vor (vorhandene Einträge werden nach Rückfrage ersetzt).
3. **Stil**: Vorlage wählen (Anime Action, Film Noir, Dark Fantasy, Comic …) oder einen freien **Stilwunsch** eintragen, z.B. „soll aussehen wie One Piece“, und **Mit agy verfeinern**. agy macht daraus einen Stil-Suffix, den das gewählte Bildmodell versteht, und schlägt passende Akzentfarben vor. Der Suffix bleibt editierbar. **Komprimieren** neben jedem Feld lässt agy Doppeltes, Widersprüche, Füllwörter und für das gewählte Modell wirkungslose Begriffe entfernen (bei Krea 2 z.B. „masterpiece, best quality“). Was entfernt wurde, steht im Log. **Vorschlagen** daneben lässt agy das Feld (Welt/Thema, Stilwunsch, Stil-Suffix, Negativ-Prompt) aus dem Buch, den anderen Feldern und dem Bildmodell vorschlagen; ein vorhandener Text wird erst nach Rückfrage ersetzt. Der Negativ-Vorschlag lässt Begriffe weg, die dem gewählten Stil widersprechen (bei Pixel Art z.B. „pixelated“, „lowres“).
4. **Engine**: agy-Modell und ComfyUI. „Verbinden“ lädt die Modelle aus dem laufenden ComfyUI. Unter **Bildmodell** stehen alle Modelle mit ihrer erkannten Bauart, die Einstellungen werden beim Auswählen automatisch gesetzt (siehe „Bildmodelle“).
5. **① Prompts planen**: agy liest das Regelwerk, beschreibt Welt und Genre und schreibt für jeden Bildplatz ein Motiv. Ohne gesetzten Stil schlägt agy auch Stil und Farben vor.
6. **② Fehlende Bilder**: generiert alle Bilder, die noch fehlen. Mit **Bild entfernen** (unter dem Prompt-Feld; mehrere Bilder mit Strg/Umschalt-Klick in der Liste markieren) nimmst du Bilder heraus, mit denen du nicht zufrieden bist: Die Datei wandert nach `.build/removed/`, der Prompt bleibt, der Platz bekommt einen neuen Seed (sonst käme dasselbe Bild wieder), und „Fehlende Bilder“ erzeugt ihn neu. Im Dialog gibt es dafür auch „Entfernen und neu generieren“.
7. **③ PDF bauen**.

**▶ Alles** führt 1–3 nacheinander aus und überspringt Vorhandenes.

### Einzelne Bilder nachbessern

In der Bildliste ein Bild wählen. Rechts stehen Motiv-Prompt, ein eigener Negativ-Prompt für dieses Bild (leer = Standard aus „Stil“), Seed und „Stil-Suffix anhängen“. Die Vorschaubilder in der Liste aktualisieren sich, sobald ein Bild fertig ist, nicht erst am Ende des Durchlaufs.

- **Prompt neu (agy)**: neues Motiv, optional mit Wunsch („eher Nacht, zwei Kämpfer“).
- **Analysieren (agy)**: agy sieht sich das aktuelle Bild an, schreibt kurz, was passt und was nicht, und verbessert Motiv-Prompt und Negativ-Prompt gezielt (z.B. „fused fingers“, wenn Hände verschmolzen sind). Ein Wunsch im Feld darüber wird berücksichtigt („mehr Crew zeigen“).
- **Prüfen**: nur die Bildkontrolle (✓ / ✗).
- **Vorschau**: rendert mit den aktuellen Einstellungen, ohne das Bild zu ersetzen.
- **Übernehmen**: ersetzt das Bild durch die Vorschau.
- **Generieren & speichern**: direkt neu erzeugen.

Ein Bild passt nicht? Wunsch eintragen → Analysieren → Vorschau → Übernehmen.

### Füllbilder für Leerraum

Kurze Abschnitte hinterlassen am Seitenende oft eine Lücke, weil der nächste Block aus Überschrift und Bild nicht mehr passt. Mit **Buch → Leerraum mit Füllbildern füllen** setzt das Tool das PDF zuerst probeweise, misst jede Lücke ab der eingestellten Höhe (Standard 55 mm) und legt dafür einen Bildplatz `fill-…` an. Das gilt nur für Lücken nach einem Abschnitt, der auf dieser Seite endet. agy schreibt ein zweites, anderes Motiv zum selben Abschnitt, ComfyUI erzeugt es im Seitenverhältnis der Lücke, und beim Bauen füllt es genau diesen Platz. Die übrige Seitenaufteilung bleibt gleich.

Ändert sich der Text, verschieben sich die Lücken: Füllbilder ohne Lücke werden einfach nicht mehr eingesetzt, neue Lücken bekommen beim nächsten „Prompts planen“ / „Fehlende Bilder“ ein eigenes Bild.

### Bildkontrolle mit agy (optional)

**Engine → Generierte Bilder automatisch prüfen**: Nach jedem generierten Bild sieht sich agy das Bild an und beurteilt Motiv, Textartefakte, Anatomie und ob es zur Welt passt. Mit **Unpassende Bilder neu generieren** schreibt agy bei einem Fehlschlag einen besseren Prompt, und das Bild wird mit neuem Seed neu erzeugt. „Bilder prüfen (agy)“ prüft alle vorhandenen Bilder, „Prüfen“ beim einzelnen Bild nur dieses. Das Ergebnis steht am Bild (✓ / ✗ mit den gefundenen Problemen).

**Neuversuche** legt fest, wie oft ein unpassendes Bild mit einem verbesserten, ähnlichen Prompt neu erzeugt wird (bis zu 3). Der Schalter **Nach 3 Fehlversuchen Konzept radikal neu (bis zu 3×3 Bilder)** erweitert das: Pro Konzept entstehen bis zu 3 Bilder, die beiden letzten mit jeweils verfeinertem Prompt. Passt keins, schreibt agy den Prompt von Grund auf neu. Es kennt die bereits gescheiterten Konzepte samt ihren Problemen und muss ein anderes Hauptmotiv, einen anderen Ort und eine andere Perspektive wählen, die weiter zum Abschnitt passen. Höchstens 3 Konzepte mit je 3 Bildern, also 9 Bilder pro Platz; sobald eins passt, ist Schluss. Die Zahl der Neuversuche gilt in diesem Modus nicht. Das Log zeigt „Konzept 2/3, Bild 1/3“. Das Ergebnis des letzten Versuchs bleibt als Bild stehen, auch wenn keins gepasst hat.

agy bekommt Bilder nur als Datei (im Headless-Modus nimmt es nur Text an). Das Tool legt deshalb für jedes Bild einen Ordner `.build/qc/<bildplatz>/` an, in dem nur eine verkleinerte Kopie liegt. Dort startet es agy mit `--sandbox` und **ohne** `--dangerously-skip-permissions`. agy darf also nichts ausführen oder schreiben, ohne dass eine Freigabe greift. Lesen kann agy die Kopie, weil der Ordner in einem seiner vertrauenswürdigen Arbeitsbereiche liegt:

```json
// ~/.gemini/antigravity-cli/settings.json
"trustedWorkspaces": [ "/home/<du>", ... ]
```

Bildgröße und Hoch-/Querformat bewertet agy dabei nicht, die gibt das Tool vor (Cover und Hintergrund sind absichtlich Hochformat).

Den gewählten Stil (z.B. Pixel Art) kennt agy bei Bildkontrolle, „Analysieren“ und Prompt-Erstellung und bewertet ihn nicht als Fehler: Verpixelung, niedrige Auflösung, begrenzte Palette, Dithering oder flache Schattierung werden nicht bemängelt, und widersprüchliche Begriffe (z.B. „pixelated“, „lowres“) kommen nicht in den Negativ-Prompt. Die Prompts selbst bleiben stilfrei, werden aber so geschrieben, dass sie im Stil gut lesbar sind.

Liegt dein Projekt außerhalb aller `trustedWorkspaces`, meldet die Bildkontrolle „agy konnte das Bild nicht öffnen“. Dann den Projektordner (oder einen übergeordneten Ordner) dort eintragen. Erlaube agy dafür **nicht** pauschal alles.

## Inhaltstyp: PnP-Regelwerk oder anderes Dokument

Der Schalter **Inhalt** im Tab „Buch“ (beim Anlegen eines Projekts wird danach gefragt, auf der Kommandozeile `new … --type document`) steuert, wie agy denkt:

| | PnP-Regelwerk (Standard) | Anderes Dokument (Readme, Tutorial, Doku …) |
| --- | --- | --- |
| Motive | Szenen aus der Spielwelt, Charaktere in Aktion | Szenen, Gegenstände oder Bildmetaphern aus dem Themengebiet; kein Fantasy- oder Sci-Fi-Beiwerk, keine Screenshots, kein Code, keine Beschriftungen |
| „Welt“-Feld | Genre, Setting, Epoche, Ton | wird zu „Thema“: Gebiet, Zielgruppe, Ton |
| Cover | Ikonische Figuren im Kampf | ein klares Sinnbild des Themas |
| Bildkontrolle und „Analysieren“ | achtet auf falsche Epoche oder Genre, Monster ohne Anlass | achtet auf Fantasy-Fremdkörper, falsches Fachgebiet und Stockfoto-Beliebigkeit |
| Stilvorlagen | Anime Action, Film Noir, Dark Fantasy … | Technische Illustration, Flat Vector, Isometrisch, Blaupause, Aquarell, Editorial-Foto, Minimalistisch, Pixel Art |
| PDF-/Text-Import | Regelwerk-Wortlaut bleibt erhalten | zusätzlich bleiben Code, Befehle und Pfade in Backticks und Codeblöcken |

Das Umschalten ändert nur neue agy-Anfragen. Vorhandene Prompts bleiben stehen; zum Ersetzen „Prompts planen“ mit „vorhandene überschreiben“ ausführen. Das Layout selbst ist für beide Typen gleich.

## Bilder im Regelwerk selbst

Bilder, die im Markdown stehen (`![Bildunterschrift](pfad/bild.svg)`), übernimmt das PDF an ihrer Stelle. Relative Pfade gelten relativ zur Markdown-Datei. Der Alt-Text wird zur Bildunterschrift, Hochformat-Bilder bleiben in der Spalte, Querformate gehen über beide Spalten. Unterstützt werden SVG, PNG, JPG, GIF und WebP.

Beim Import fremder Regelwerke bleiben deren Bilder erhalten: Word-Bilder landen in `media/` und stehen an ihrer Stelle im Markdown. Aus PDFs werden größere Bilder (ab 200 px) seitenweise extrahiert und am Ende ihrer Seite eingefügt. Dabei können Deko-Elemente mitkommen, deshalb `media/` und das Markdown kurz prüfen.

## Bildmodelle

Unter **Engine → Bildmodell** listet das Tool alle Modelle aus ComfyUI und erkennt ihre Bauart am Dateikopf (Namen der Tensoren, nicht am Dateinamen). Beim Auswählen setzt es Workflow, Text-Encoder, VAE, Steps, CFG, Sampler und Scheduler. Was du danach unter „Feinabstimmung“ änderst, merkt es sich pro Modelldatei (`~/.config/rulebook-pdf/models.json`, gilt für alle Projekte). **Empfohlene Werte** setzt wieder auf die Vorgaben zurück.

| Bauart | Workflow | Vorgaben | Hinweis |
|---|---|---|---|
| Krea 2 | Diffusion-Modell | `qwen3vl_4b_fp8_scaled` (Typ `krea2`), `qwen_image_vae`, 8 Steps, CFG 1, euler/simple | Turbo-Modell, Negativ-Prompt wirkt bei CFG 1 nicht |
| Anima | Diffusion-Modell | `qwen_3_06b_base`, `qwen_image_vae`, 28 Steps, CFG 4, euler/simple | |
| SDXL / Illustrious / Pony | Checkpoint | 28 Steps, CFG 6, dpmpp_2m/karras | Lightning/Turbo im Namen: 8 Steps, CFG 2, dpmpp_sde/karras |
| SD 1.5 | Checkpoint | 25 Steps, CFG 7, dpmpp_2m/karras | |
| Flux (mit Encoder und VAE) | Checkpoint | 20 Steps (schnell: 4), CFG 1 | |

Nicht für Text-zu-Bild nutzbar und grau markiert: Flux Kontext (Bildbearbeitung), Wan (Video), reine Flux-Diffusionsmodelle (nur über „Eigener Workflow“), Erkennungs-/Pose-Modelle.

Fehlt ein Text-Encoder oder VAE, steht das unter dem Modell mit Download-Link. Liegt ein reines Diffusionsmodell nur in `checkpoints/` (z.B. Krea 2 aus Civitai), erscheint es mit ⚠ und dem Button **In diffusion_models verlinken**. Der Diffusion-Modell-Loader von ComfyUI sucht nur dort.

**Krea 2 auf Karten mit 16 GB oder weniger:** Die schnellen INT8-Kernel brauchen eine RTX 3000 oder neuer. Auf älteren Karten (z.B. RTX 2000 / Quadro RTX) nutzt ComfyUI einen langsameren Weg, der zusätzlichen VRAM braucht. Ohne Reserve bricht die Generierung mit „Allocation on device“ ab. Abhilfe: ComfyUI mit `--reserve-vram 3` starten (bei Pinokio in `comfy.git/start.js`). Dann lagert ComfyUI einen Teil des Modells ins RAM aus.

### Workflow-Typen

| Workflow | Für | Einstellungen |
|---|---|---|
| Diffusion-Modell | Anima und andere Modelle, die UNET, Text-Encoder und VAE getrennt laden (z.B. `trattoNero_nitrattoANIMA`) | Diffusion-Modell, Text-Encoder, Encoder-Typ, VAE |
| Checkpoint | SD 1.5, SDXL, Illustrious, Pony … (alles in einer Datei) | Checkpoint |
| Eigener Workflow | alles, was in ComfyUI läuft (Flux, LoRAs, Upscaler …) | In ComfyUI über **Export (API)** gespeichertes JSON |

Beim eigenen Workflow setzt das Tool Prompt, Negativ-Prompt, Seed und Bildgröße automatisch ein: Es sucht den KSampler, folgt dessen `positive`/`negative`-Eingängen zum Text-Knoten und setzt `width`/`height` in allen Knoten, die beides haben.

## Kommandozeile

```bash
python3 rulebook_pdf.py new    PROJEKT QUELLE    # Projekt anlegen (importiert docx/pdf/txt)
python3 rulebook_pdf.py plan   PROJEKT [--force] # Prompts schreiben (--force: alle neu)
python3 rulebook_pdf.py images PROJEKT [--all]   # fehlende Bilder (--all: alle neu)
python3 rulebook_pdf.py check  PROJEKT [--no-fix]  # Bilder mit agy prüfen (--no-fix: nur prüfen)
python3 rulebook_pdf.py restructure PROJEKT ORIGINAL.pdf  # Überschriften-Ebenen nach dem Original richten
python3 rulebook_pdf.py build  PROJEKT           # PDF setzen
python3 rulebook_pdf.py all    PROJEKT           # plan + images + build
```

## Projektordner

```
projekt/
├── project.json   Einstellungen (Quelle, Ausgabe, Titel, Farben, Stil, Engine)
├── images.json    pro Bildplatz: Motiv-Prompt, Seed, Stil an/aus, optional Negativ-Prompt, Prüfergebnis
├── images/        die generierten Bilder als JPEG (Name = Bildplatz-Schlüssel)
├── media/         Bilder aus importierten Word-/PDF-Regelwerken
└── .build/        Zwischendateien, Vorschauen, Bildkontrolle (nicht versionieren)
```

Bildplatz-Schlüssel entstehen aus den Überschriften (`ch-…` für Kapitel, `sec-…` für Abschnitte, `fill-…` für Füllbilder). Wird eine Überschrift umbenannt, bekommt sie einen neuen Platz und braucht ein neues Bild; der alte Eintrag bleibt in `images.json` stehen.

## Layout-Regeln

- Kurze Abschnitte (unter ca. 700 Zeichen, ohne Tabellen) bekommen ihr Bild als kleineres Bild rechts neben dem Text statt als Banner über der vollen Breite.
- Tabellen mit mehr als 3 Spalten, mehr als 8 Zeilen oder langen Zellen stehen volle Breite zwischen den Spaltenblöcken des Abschnitts (nicht als Spalten-Überspanner, sonst schiebt WeasyPrint sie komplett auf die nächste Seite und lässt Leerraum) und dürfen über Seiten umbrechen; die Kopfzeile wiederholt sich. Kurze Tabellen bleiben in der Spalte und am Stück.
- Generierte Bilder werden nie beschnitten: Banner, Seitenbilder und Kapitelbilder behalten ihr Seitenverhältnis (Banner höchstens 66 mm hoch, dann entsprechend schmaler und zentriert). Das Cover füllt die Seitenbreite; Cover und Hintergrund werden im A4-Verhältnis erzeugt.
- Zeilen mit `•` werden zu Listen, Listen direkt nach einem Absatz werden als Listen erkannt, und kurze Zeilen innerhalb eines Absatzes (Preislisten, „Stufe 1 …“) behalten ihren Zeilenumbruch.
- Emojis werden einfarbig gesetzt (mitgelieferte Noto Emoji). Farbige Emoji-Schriften bringen WeasyPrint mit HarfBuzz 14 zum Absturz.

## Wie das Markdown aussehen sollte

- `#` Buchtitel (optional), `##` Kapitel, `###` Abschnitte. Bei mehreren `#` gelten `#` als Kapitel und `##` als Abschnitte. Unter „Buch → Überschriften“ lässt sich das auch fest einstellen.
- Eine Nummer am Kapitelanfang („3. Der Kampf“) wird als Kapitelnummer genutzt, eine Klammer am Ende („(Das Clash-System)“) als Untertitel.
- Kapitel, deren Titel mit „Anhang“, „Appendix“, „Änderungsprotokoll“ oder „Changelog“ beginnen, werden schlicht und einspaltig gesetzt, ohne Banner (Muster in `project.json` → `appendix_patterns`).

## Hinweise

- agy-Aufrufe laufen nacheinander. Parallel gestartete agy-Läufe stören sich gegenseitig und liefern leere Antworten. Fehlende Prompts fragt das Tool einmal nach.
- WeasyPrint malt `@page`-Hintergrundbilder nur im Satzspiegel; die Seitentextur ist deshalb ein fixiertes Element, das auf jeder Seite wiederholt wird.
- WeasyPrint kann einen zweispaltigen Block nicht umbrechen, wenn darin ein unteilbares Element steckt, das in keine Spalte der Seite mehr passt; es schiebt dann den ganzen Abschnitt weiter. Abbildungen in Spalten sind deshalb auf 100 mm Höhe begrenzt. Hohe Grafiken besser mitten in einen Abschnitt setzen als an seinen Anfang.
- Schriften (Bebas Neue, Oswald, Barlow) liegen in `rpdf/fonts/` (SIL Open Font License).

## Engines für Bilder

`project.json` → `"engine"`:

- **`agy`** (Standard): agy (Antigravity CLI) erzeugt die Bilder mit seinem eingebauten Bildwerkzeug über die eigene Anmeldung, **kein API-Key nötig**. Rund 40–100 s pro Bild. agy legt die Bilder unter `~/.gemini/antigravity-cli/brain/<conversation_id>/` ab, das Tool übernimmt sie von dort. Die Conversation-ID wird als `interaction_id` in `images.json` gespeichert, Änderungen laufen über `agy --conversation ID`.
- **`gemini`**: direkter Aufruf der Interactions API (`gemini-3.1-flash-lite-image`), braucht `GEMINI_API_KEY` oder `~/gemini.key` und `pip install google-genai`.
- **`comfy`**: ComfyUI wie bisher.

Für `agy` und `gemini` gilt:
- Formate kommen nur als 1:1, 16:9, 9:16, 4:3, 3:4; das Tool wählt das nächste und schneidet mittig auf die Zielgröße zu (breite Banner verlieren etwas Höhe). Kein Seed, kein Negativ-Prompt (Negatives werden als „Do not include …“ angehängt).
- `rulebook_pdf.py edit PROJEKT KEY "Anweisung"` ändert ein vorhandenes Bild, statt es neu zu erzeugen.
- Bildkontrolle (QC): 1 Generierung + höchstens 2 Bearbeitungen desselben Bildes (nur die gefundenen Probleme), danach keine weiteren Änderungen. Die Konzept-/Neu-Generieren-Schleife gilt nur für ComfyUI.
- Die Oberfläche (gui) hat noch keinen Engine-Schalter; die Engine wird in `project.json` gesetzt.
