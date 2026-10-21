# Illuminator

[English](README.md) · **Deutsch**

Macht aus einem Regelwerk oder Dokument (Markdown, Word, PDF oder Text) ein illustriertes PDF im Stil eines gedruckten Buchs: Cover, Kapitel-Banner, ein Bild pro Abschnitt, zweispaltiger Satz, Kopfzeilen und Seitenzahlen. Die Bilder erzeugt eine KI, passend zum Inhalt der jeweiligen Abschnitte.

```
Text ──► Prompts (agy | Claude) ──► Bilder (agy | Gemini API | ComfyUI) ──► PDF (WeasyPrint)
```

Die Bild-Prompts schreibt **agy** (Antigravity CLI) oder alternativ die **Claude Code CLI** (`claude`, Einstellung unter Engine → Prompt-Generator, `"llm": "claude"` in `project.json`). Das CLI bekommt dabei nur Text und gibt JSON zurück; es läuft in einem leeren Temp-Ordner und erhält keine Datei- oder Shell-Rechte. Die Bilder selbst erzeugt standardmäßig ebenfalls agy (eingebautes Bildwerkzeug, kein API-Key nötig), alternativ die Gemini API oder ein lokales ComfyUI, siehe „Engines für Bilder“.

## Voraussetzungen

- Python 3.10+ mit `pip install -r requirements.txt` (PySide6, markdown, WeasyPrint, Pillow; `google-genai` nur für die Gemini-API-Engine)
- Einmalig die Schriften laden (nicht im Repo): `python3 fetch_fonts.py` (Google Fonts, SIL OFL)
- [agy](https://antigravity.google) im `PATH`, angemeldet (für Prompts und die Standard-Bild-Engine); alternativ für die Prompts die [Claude Code CLI](https://claude.com/claude-code) (`claude`)
- Nur für die Engine ComfyUI: ComfyUI (z.B. über Pinokio) mit einem Bildmodell, Standard: `http://127.0.0.1:8188`
- Für PDF-Import: `pdftotext` (Paket poppler-utils)

## Start

```bash
python3 illuminator.py gui
```

Ohne Argument öffnet die Oberfläche das zuletzt benutzte Projekt. **Letzte Projekte** (neben „Neues Projekt…“) listet die zehn zuletzt geöffneten Projekte.

Bilder im Markdown mit relativem Pfad (z.B. `docs/screenshots/x.png`) werden zuerst neben der Quelldatei gesucht, dann im Projektordner `bilder/` und im Projektordner selbst, jeweils mit Pfad und nur mit Dateiname. Nicht gefundene Bilder meldet der Build als „WARNUNG: Bild nicht gefunden“.

## Ablauf in der Oberfläche

1. **Neues Projekt…**: Regelwerk wählen (`.md`, `.docx`, `.pdf`, `.txt`) und einen leeren Projektordner. Word-Dateien werden lokal über ihre Überschriftenstile umgewandelt, PDF und Text abschnittsweise von agy (Wortlaut bleibt erhalten). Die Überschriften-Ebenen richtet das Tool danach am Original aus: Kapitel sind die Einträge der ersten Ebene im Inhaltsverzeichnis (Word-Stil mit Punktlinien und Seitenzahlen); Teile vor dem ersten solchen Kapitel, die in der größten Schrift gesetzt sind, werden ebenfalls Kapitel. Darunter ergeben die Schriftgrößen der Überschriften die Ebenen, und ein Untertitel in derselben Zeile wird mit seiner Überschrift zusammengeführt. Das Verzeichnis selbst landet nicht im Text. Für ein bereits importiertes Projekt: `illuminator.py restructure PROJEKT ORIGINAL.pdf` (Sicherung `*.vor-restructure.md`, Bilder von Plätzen, die nur zwischen Kapitel und Abschnitt wechseln, werden mitgenommen). **Das erzeugte Markdown kurz prüfen** (Toolbar: „Quelle öffnen“), vor allem bei PDFs.
2. **Buch**: als **Quelle** geht Markdown, Word, PDF oder Text (die letzten drei werden wie beim Anlegen eines Projekts umgewandelt). Oben der Schalter **Inhalt** (PnP-Regelwerk / Anderes Dokument, siehe „Inhaltstyp“). Titel, Untertitel, Kicker, Tagline, Kopfzeile, Kapitel-Label, Farben, Schriftgröße, Blocksatz. „Alles automatisch ausfüllen (KI)“ liest den Anfang des Regelwerks und schlägt alle Cover- und Kopfzeilentexte in der Sprache des Buches vor (vorhandene Einträge werden nach Rückfrage ersetzt).
3. **Stil**: Vorlage wählen (Anime Action, Film Noir, Dark Fantasy, Comic …) oder einen freien **Stilwunsch** eintragen, z.B. „soll aussehen wie One Piece“, und **Mit KI verfeinern**. agy macht daraus einen Stil-Suffix, den das gewählte Bildmodell versteht, und schlägt passende Akzentfarben vor. Der Suffix bleibt editierbar. **Komprimieren** neben jedem Feld lässt agy Doppeltes, Widersprüche, Füllwörter und für das gewählte Modell wirkungslose Begriffe entfernen (bei Krea 2 z.B. „masterpiece, best quality“). Was entfernt wurde, steht im Log. **Vorschlagen** daneben lässt agy das Feld (Welt/Thema, Stilwunsch, Stil-Suffix, Negativ-Prompt) aus dem Buch, den anderen Feldern und dem Bildmodell vorschlagen; ein vorhandener Text wird erst nach Rückfrage ersetzt. Der Negativ-Vorschlag lässt Begriffe weg, die dem gewählten Stil widersprechen (bei Pixel Art z.B. „pixelated“, „lowres“).
4. **Engine**: agy-Modell für die Prompts und die **Bild-Engine** (agy, Gemini API oder ComfyUI). Bei ComfyUI lädt „Verbinden“ die Modelle aus dem laufenden ComfyUI; unter **Bildmodell** stehen alle Modelle mit ihrer erkannten Bauart, die Einstellungen werden beim Auswählen automatisch gesetzt (siehe „Bildmodelle“). Bei agy und Gemini gibt es kaum etwas einzustellen.
5. **① Prompts planen**: agy liest das Regelwerk, beschreibt Welt und Genre und schreibt für jeden Bildplatz ein Motiv. Ohne gesetzten Stil schlägt agy auch Stil und Farben vor.
6. **② Fehlende Bilder**: generiert alle Bilder, die noch fehlen. Mit **Bild entfernen** (unter dem Prompt-Feld; mehrere Bilder mit Strg/Umschalt-Klick in der Liste markieren) nimmst du Bilder heraus, mit denen du nicht zufrieden bist: Die Datei wandert nach `.build/removed/`, der Prompt bleibt, der Platz bekommt einen neuen Seed (sonst käme dasselbe Bild wieder), und „Fehlende Bilder“ erzeugt ihn neu. Im Dialog gibt es dafür auch „Entfernen und neu generieren“.
7. **③ PDF bauen**.

**▶ Alles** führt 1–3 nacheinander aus und überspringt Vorhandenes.

### Einzelne Bilder nachbessern

In der Bildliste ein Bild wählen. Rechts stehen Motiv-Prompt, ein eigener Negativ-Prompt für dieses Bild (leer = Standard aus „Stil“), Seed und „Stil-Suffix anhängen“. Die Vorschaubilder in der Liste aktualisieren sich, sobald ein Bild fertig ist, nicht erst am Ende des Durchlaufs.

- **Prompt neu (KI)**: neues Motiv, optional mit Wunsch („eher Nacht, zwei Kämpfer“).
- **Analysieren (KI)**: agy sieht sich das aktuelle Bild an, schreibt kurz, was passt und was nicht, und verbessert Motiv-Prompt und Negativ-Prompt gezielt (z.B. „fused fingers“, wenn Hände verschmolzen sind). Ein Wunsch im Feld darüber wird berücksichtigt („mehr Crew zeigen“).
- **Prüfen**: nur die Bildkontrolle (blauer Haken oder Flagge).
- **Vorschau**: rendert mit den aktuellen Einstellungen, ohne das Bild zu ersetzen.
- **Übernehmen**: ersetzt das Bild durch die Vorschau.
- **Generieren & speichern**: direkt neu erzeugen.

Ein Bild passt nicht? Wunsch eintragen → Analysieren → Vorschau → Übernehmen.

### Füllbilder für Leerraum

Kurze Abschnitte hinterlassen am Seitenende oft eine Lücke, weil der nächste Block aus Überschrift und Bild nicht mehr passt. Mit **Buch → Leerraum mit Füllbildern füllen** setzt das Tool das PDF zuerst probeweise, misst jede Lücke ab der eingestellten Höhe (Standard 55 mm) und legt dafür einen Bildplatz `fill-…` an. Das gilt nur für Lücken nach einem Abschnitt, der auf dieser Seite endet. agy schreibt ein zweites, anderes Motiv zum selben Abschnitt, die Bild-Engine erzeugt es im Seitenverhältnis der Lücke, und beim Bauen füllt es genau diesen Platz. Die übrige Seitenaufteilung bleibt gleich.

Ändert sich der Text, verschieben sich die Lücken: Füllbilder ohne Lücke werden einfach nicht mehr eingesetzt, neue Lücken bekommen beim nächsten „Prompts planen“ / „Fehlende Bilder“ ein eigenes Bild.

### Bildkontrolle mit agy (optional)

**Engine → Generierte Bilder automatisch prüfen**: Nach jedem generierten Bild sieht sich agy das Bild an und beurteilt Motiv, Textartefakte, Anatomie und ob es zur Welt passt. Mit **Unpassende Bilder korrigieren** schreibt agy bei einem Fehlschlag einen besseren Prompt, und das Bild wird mit neuem Seed neu erzeugt (nur ComfyUI; bei agy und Gemini wird das Bild stattdessen bearbeitet, siehe „Engines für Bilder“). „Bilder prüfen (KI)“ prüft alle vorhandenen Bilder, „Prüfen“ beim einzelnen Bild nur dieses.

Jedes Bild in der Liste hat in der Ecke ein Status-Symbol. Die Bildkontrolle setzt einen **blauen Haken** (passt) oder eine **orange Flagge** (Probleme gefunden; die Probleme stehen im Tooltip und neben dem Bild). Ein Klick auf das Symbol setzt deine eigene Bewertung: **grüner Haken** (freigegeben), dann **rotes ✗** (abgelehnt), dann wieder das Ergebnis der KI. Die Knöpfe „✓ Freigeben“, „✗ Ablehnen“ und „Zurücksetzen“ neben dem Bild (und das Rechtsklick-Menü) machen dasselbe für alle markierten Bilder. Deine Bewertung gilt immer: „Bilder prüfen“ überspringt von dir bewertete Bilder und ändert sie nie. Die Bewertungen stehen in `images.json`; ein neues Bild ist wieder unbewertet.

Die beiden Knöpfe unten arbeiten mit allen abgelehnten Bildern (rotes ✗ und KI-Flagge); grün und blau abgehakte bleiben. **Neue Bilder, neue Seeds** erzeugt sie mit gleichem Prompt neu, **Neue Prompts + neue Bilder** lässt vorher für jedes einen neuen Prompt schreiben. Ist die automatische Prüfung an, wird jedes neue Bild wieder geprüft.

**Strenge** (Engine → Bildkontrolle, `"qc": {"strictness": 3}`): 3 = streng, das Bild muss dem Prompt genau folgen (Standard); 2 = mittel, das Motiv darf abweichen, solange das Bild klar zum Abschnitt passt; 1 = locker, nur kaputte Bilder (Artefakte, verzerrte Schrift, verformte Figuren) oder klar themenfremde fallen durch.

Die folgenden Absätze zu Neuversuchen und Konzepten gelten für die Engine ComfyUI. **Neuversuche** legt fest, wie oft ein unpassendes Bild mit einem verbesserten, ähnlichen Prompt neu erzeugt wird (bis zu 3). Der Schalter **Nach 3 Fehlversuchen Konzept radikal neu (bis zu 3×3 Bilder)** erweitert das: Pro Konzept entstehen bis zu 3 Bilder, die beiden letzten mit jeweils verfeinertem Prompt. Passt keins, schreibt agy den Prompt von Grund auf neu. Es kennt die bereits gescheiterten Konzepte samt ihren Problemen und muss ein anderes Hauptmotiv, einen anderen Ort und eine andere Perspektive wählen, die weiter zum Abschnitt passen. Höchstens 3 Konzepte mit je 3 Bildern, also 9 Bilder pro Platz; sobald eins passt, ist Schluss. Die Zahl der Neuversuche gilt in diesem Modus nicht. Das Log zeigt „Konzept 2/3, Bild 1/3“. Das Ergebnis des letzten Versuchs bleibt als Bild stehen, auch wenn keins gepasst hat.

agy bekommt Bilder nur als Datei (im Headless-Modus nimmt es nur Text an). Das Tool legt deshalb für jedes Bild einen Ordner `.build/qc/<bildplatz>/` an, in dem nur eine verkleinerte Kopie liegt. Dort startet es agy mit `--sandbox` und **ohne** `--dangerously-skip-permissions`. agy darf also nichts ausführen oder schreiben, ohne dass eine Freigabe greift. Lesen kann agy die Kopie, weil der Ordner in einem seiner vertrauenswürdigen Arbeitsbereiche liegt:

```json
// ~/.gemini/antigravity-cli/settings.json
"trustedWorkspaces": [ "/home/<du>", ... ]
```

Bildgröße und Hoch-/Querformat bewertet agy dabei nicht, die gibt das Tool vor (Cover und Hintergrund sind absichtlich Hochformat).

Den gewählten Stil (z.B. Pixel Art) kennt agy bei Bildkontrolle, „Analysieren“ und Prompt-Erstellung und bewertet ihn nicht als Fehler: Verpixelung, niedrige Auflösung, begrenzte Palette, Dithering oder flache Schattierung werden nicht bemängelt, und widersprüchliche Begriffe (z.B. „pixelated“, „lowres“) kommen nicht in den Negativ-Prompt. Die Prompts selbst bleiben stilfrei, werden aber so geschrieben, dass sie im Stil gut lesbar sind.

Liegt dein Projekt außerhalb aller `trustedWorkspaces`, meldet die Bildkontrolle „agy konnte das Bild nicht öffnen“. Dann den Projektordner (oder einen übergeordneten Ordner) dort eintragen. Erlaube agy dafür **nicht** pauschal alles.

## Sprachen (Übersetzungen)

Ein Projekt kann dasselbe Buch in mehreren Sprachen erzeugen, z.B. Englisch (Hauptsprache) plus Deutsch und Polnisch, in einem Durchlauf und ohne die Bilder neu zu erzeugen.

- **Tab „Sprachen“ → Übersetzung hinzufügen…** nimmt eine Markdown-, Word-, PDF- oder Textdatei und fragt nach dem Sprachcode (aus dem Text geraten). Word und PDF werden wie beim Anlegen eines Projekts umgewandelt, das Markdown liegt danach in `translations/<code>/`. Auf der Kommandozeile: `illuminator.py add-lang PROJEKT DATEI --lang pl`.
- **Die Bilder werden geteilt.** Cover, Seitenhintergrund, Kapitelbanner und Abschnittsbilder gehören zum Text der Hauptsprache. Kapitel und Abschnitte einer Übersetzung bekommen die Bilder des Kapitels/Abschnitts an derselben Position; die Übersetzung braucht also dieselbe Gliederung (gleich viele Kapitel und Abschnitte je Kapitel). Der Tab zeigt, ob die Gliederung passt, und listet Abweichungen auf; Blöcke ohne Gegenstück bekommen einfach kein Bild. Eine übersetzte Überschrift braucht nie ein neues Bild.
- **Füllbilder gibt es pro Sprache.** Der übersetzte Text bricht anders um, die Lücken liegen woanders. Mit „Leerraum mit Füllbildern füllen“ misst jede Sprache ihre eigenen Lücken und bekommt eigene Plätze `fill-<code>-…` (in der Liste unter „Füllbilder für“, neben dem Sprachumschalter über der Liste). „Prompts planen“ und „Fehlende Bilder“ erfassen alle aktiven Sprachen.
- **Texte pro Sprache:** Titel, Untertitel, Kicker, Tagline, Kopfzeile und Kapitel-Label stehen pro Übersetzung („Texte automatisch ausfüllen“ liest die Übersetzung und schreibt sie in deren Sprache). Farben, Stil und Layout sind gemeinsam. Die Sprache der Silbentrennung folgt dem Code.
- **In einem Rutsch:** „③ PDF bauen“ und „▶ Alles“ setzen die Hauptsprache und jede angehakte Übersetzung nacheinander (standardmäßig `en.pdf`, `en.pl.pdf`, `en.de.pdf`; der Ausgabepfad ist pro Sprache änderbar). Eine fehlschlagende Sprache hält die anderen nicht auf. **Nur diese Sprache bauen** und `illuminator.py build PROJEKT --lang pl` setzen nur eine.
- **Bilder aus einer Übersetzung entfernen:** Ein PDF-Import lässt die eigenen Bilder des Dokuments normalerweise weg (es wären die alten Illustrationen). Stehen im Markdown einer Übersetzung noch Bild-Verweise, entfernt **Bilder aus dem Markdown entfernen…** (Kommandozeile: `strip-images PROJEKT --lang pl [--list] [--only 0,3]`) alle oder nur die markierten; die Dateien bleiben, der Originaltext liegt danach als `*.vor-bildentfernung.md` daneben. Beim Hinzufügen überstimmt „Bilder aus dem Dokument übernehmen“ den Standard.

## Bilder aus dem Quelltext: aufnehmen oder auslassen

Die Bilder, die im Quelltext stehen (Screenshots, Grafiken, Bilder aus einem Import, Badges), lassen sich aus dem PDF auslassen, ohne das Markdown anzufassen: **Buch → Bilder aus dem Dokument ins PDF** schaltet alle, **Medien auswählen…** listet jedes Bild jeder Sprache (jede Datei einmal, mit Vorschau, Sprachen, Häufigkeit und Abschnitt) mit einem Haken für „kommt ins PDF“. Abgewählte Bilder fehlen überall, wo sie im Text stehen, und das Layout passt sich an (ein Abschnitt ohne sein Bild kann wieder kompakt werden). Die Auswahl steht als `layout.document_images` und `media_exclude` in `project.json`; weil die Quelldateien unverändert bleiben, lässt sie sich jederzeit zurücknehmen. Kommandozeile: `illuminator.py media PROJEKT --list`, `--exclude 0,3`, `--include 3`, `--off` / `--on`; `new … --no-images` übernimmt die Bilder eines Word-/PDF-Dokuments gar nicht erst. (`strip-images` für Übersetzungen entfernt die Verweise dagegen dauerhaft aus dem Markdown der Übersetzung.)

## Varianten: mehrere Bilder pro Platz, das beste wählen

Jedes Bild, das eine Engine erzeugt, bleibt erhalten (`variants/<Platz>/`, Einträge in `images.json`); `images/<Platz>.jpg` ist das, was das Buch benutzt. Unter dem großen Bild zeigt eine Leiste alle Varianten des gewählten Platzes (sie erscheint ab dem zweiten Bild), je mit dem Ergebnis der KI-Prüfung: ✓ / ✗, und was der KI nicht gefallen hat, steht im Label und im Tooltip. Ein Klick zeigt die Variante groß; **Diese Variante verwenden** übernimmt sie ins Buch, bringt Prompt, Seed und Bearbeitungskette zurück, mit denen sie entstand, und behält das bisherige Bild als Variante. **Variante prüfen (KI)** beurteilt eine noch ungeprüfte, **Variante löschen** verschiebt sie nach `.build/removed/`. Auch ein Bild, das mit „Bild entfernen“ weggenommen oder durch eine neue Generierung, eine Bearbeitung oder eine übernommene Vorschau ersetzt wurde, bleibt wählbar. Bilder aus der Zeit vor diesem Feature werden beim ersten Ersetzen als „vorher“ archiviert.

**Bilder pro Platz** (untere Leiste, `gen_count` in `project.json`, Kommandozeile `images --count 4`) erzeugt so viele Bilder pro Platz in einem Durchlauf, bei „Fehlende Bilder“, „Alle Bilder neu“ und „Generieren & speichern“. Ohne Bildkontrolle nimmt das Buch das erste, du wählst aus. Ist die Kontrolle an, wird jedes Bild beurteilt und das Buch nimmt das erste, das die KI gut findet; passt keins, läuft die gewohnte Korrektur (Bearbeitungen bei agy/Gemini, neuer Prompt bei ComfyUI) vom ersten Bild aus weiter, und ihre Ergebnisse werden ebenfalls Varianten.

## Seitenhintergrund und seine Helligkeit

Die Hintergrundtextur wird genau so gespeichert, wie die Bild-Engine sie erzeugt hat (`images/background.jpg`). Abgedunkelt und entsättigt wird sie erst beim Bau des PDFs. Bildkontrolle, „Analysieren“ und der Editor sehen also das echte Bild und bemängeln es nicht mehr als „zu dunkel“ (außerdem wissen sie, dass der Hintergrund automatisch abgedunkelt wird, und beurteilen nur Text, auffällige Motive und Stimmung). Wie stark abgedunkelt wird, berechnet das Tool aus dem Bild und den Textfarben: Die hellsten 3 % des Bilds müssen zum Fließtext ein Kontrastverhältnis von 7:1 halten und zu den Überschriften 4,5:1. **Buch → Hintergrund-Helligkeit** verschiebt dieses Optimum: In der Mitte steht der berechnete Wert, nach links wird es dunkler, nach rechts heller (alle 50 Schritte verdoppelt oder halbiert sich die Helligkeit; `layout.bg_dim_adjust` in `project.json`). Die Vorschau unter dem Regler zeigt Beispieltext auf dem Ergebnis und folgt den Textfarben. Hintergründe aus früheren Versionen wurden schon beim Speichern abgedunkelt; sie werden unverändert benutzt, und der Regler wirkt erst, wenn das Bild neu erzeugt wird („Bild entfernen“, dann „Fehlende Bilder“).

## Wunsch für alle Bild-Prompts

Unter **Stil → Wunsch für alle Bild-Prompts** steht eine Anweisung, die in jeden Bild-Prompt einfließt: Cover, Hintergrund, Kapitel- und Abschnittsbilder, Füllbilder, „Prompt neu“ und die neuen Prompts beim Neumachen abgelehnter Bilder. Schreib sie in deiner Sprache, die Prompts bleiben englisch. Sie hat Vorrang vor den allgemeinen Regeln, wo die sich widersprechen, z.B.: „Alle Figuren sind Mäuse. Keine echten Waffen, nur Improvisiertes: ein Holzschwert ist ein Zahnstocher, ein besseres Schwert eine Nadel, ein Hammer ein Fingerhut.“ Die Bildkontrolle und „Analysieren“ kennen den Wunsch ebenfalls: Sie nennen das Ergebnis nicht unrealistisch und beanstanden ein Bild, das die gewöhnliche Version (ein echtes Schwert) zeigt. Vorhandene Prompts behalten ihren Text, bis du **Prompts planen** mit **vorhandene überschreiben** laufen lässt; das schreibt alle neu (auch Cover und Hintergrund). In `project.json` heißt das Feld `"prompt_wish"`.

## Kurzfassung und Palette: weniger Text für die LLM

Die erste LLM-Funktion, die das Buch kennen muss (Planen, Vorschläge für Welt/Stil, Palette), schreibt einmal eine Kurzfassung des Buchs (ca. 100 Wörter aus Inhaltsverzeichnis und den ersten Zeilen jedes Kapitels) und speichert sie in `project.json` (`summary`, sichtbar und editierbar unter **Stil → Kurzfassung des Buchs**; „Vorschlagen“ schreibt sie neu, eine neue Quelle verwirft sie). Danach bekommen die buchbezogenen Funktionen diese Kurzfassung plus die Gliederung statt Textauszügen:

- **Prompts planen** (Welt, Stil, Cover, Hintergrund): Gliederung + Kurzfassung. Die Prompts für Kapitel und Abschnitte brauchen weiter den echten Kapiteltext.
- **Vorschlagen** neben Welt/Stil/Negativ/Wunsch: Gliederung + Kurzfassung + die anderen Felder.
- **Buch → 4 Farbpaletten vorschlagen**: Kurzfassung + aktueller Stil-Suffix + Stilwunsch + aktuelle Farben, sonst nichts. Ein Dialog zeigt die Paletten; fehlender Kontrast wird korrigiert (das Theme ist dunkel).
- Die Cover-Texte lesen weiter den echten Textanfang (sie müssen daraus zitieren).

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

## Bildmodelle (ComfyUI)

Unter **Engine → Bildmodell** listet das Tool alle Modelle aus ComfyUI und erkennt ihre Bauart am Dateikopf (Namen der Tensoren, nicht am Dateinamen). Beim Auswählen setzt es Workflow, Text-Encoder, VAE, Steps, CFG, Sampler und Scheduler. Was du danach unter „Feinabstimmung“ änderst, merkt es sich pro Modelldatei (`~/.config/illuminator/models.json`, gilt für alle Projekte). **Empfohlene Werte** setzt wieder auf die Vorgaben zurück.

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
python3 illuminator.py new    PROJEKT QUELLE    # Projekt anlegen (importiert docx/pdf/txt)
python3 illuminator.py plan   PROJEKT [--force] # Prompts schreiben (--force: alle neu)
python3 illuminator.py images PROJEKT [--all]   # fehlende Bilder (--all: alle neu)
python3 illuminator.py check  PROJEKT [--no-fix] [--strictness 1|2|3]  # Bilder mit agy prüfen (--no-fix: nur prüfen)
python3 illuminator.py restructure PROJEKT ORIGINAL.pdf  # Überschriften-Ebenen nach dem Original richten
python3 illuminator.py build  PROJEKT [--lang pl]  # PDFs setzen (alle Sprachen oder nur eine)
python3 illuminator.py add-lang PROJEKT DATEI --lang pl [--keep-images]  # Übersetzung hinzufügen (md, docx, pdf, txt)
python3 illuminator.py remove-lang PROJEKT --lang pl
python3 illuminator.py strip-images PROJEKT --lang pl [--list] [--only 0,3]  # Bild-Verweise aus einer Übersetzung entfernen
python3 illuminator.py summary PROJEKT [--force]  # Kurzfassung des Buchs (neu) schreiben
python3 illuminator.py all    PROJEKT           # plan + images + build
```

## Projektordner

```
projekt/
├── project.json   Einstellungen (Quelle, Ausgabe, Titel, Farben, Stil, Engine)
├── images.json    pro Bildplatz: Motiv-Prompt, Seed, Stil an/aus, optional Negativ-Prompt, Prüfergebnis
├── images/        die generierten Bilder als JPEG (Name = Bildplatz-Schlüssel; `fill-<Sprache>-…` = Füllbild einer Übersetzung)
├── media/         Bilder aus importierten Word-/PDF-Regelwerken
├── translations/  <Sprache>/ mit dem Markdown jeder Übersetzung
└── .build/        Zwischendateien, Vorschauen, Bildkontrolle (nicht versionieren)
```

Bildplatz-Schlüssel entstehen aus den Überschriften (`ch-…` für Kapitel, `sec-…` für Abschnitte, `fill-…` für Füllbilder). Wird eine Überschrift umbenannt, bekommt sie einen neuen Platz und braucht ein neues Bild; der alte Eintrag bleibt in `images.json` stehen.

## Prompt-Generator: agy oder Claude

Unter **Engine → Prompt-Generator** (oder `project.json` → `"llm": "agy"` bzw. `"claude"`) wählst du, welches CLI die Prompts schreibt und die Bilder beurteilt (Bildkontrolle, „Analysieren“). Bei `claude` gilt das Modell aus `"claude": {"model": "sonnet"}` (`sonnet`, `opus`, `haiku`).

- Claude läuft ohne Werkzeuge in einem leeren Temp-Ordner (`claude -p … --tools "" --json-schema …`), kein Rechte-Bypass.
- Nur für die Bildbeurteilung darf Claude das Lesen-Werkzeug nutzen, um die verkleinerte Bildkopie zu öffnen.
- Die Bild-Engine ist davon unabhängig: Mit `engine: agy` erzeugt weiter agy die Bilder, auch wenn Claude die Prompts schreibt.
- Kosten: Aufrufe laufen über dein Claude-Konto bzw. Abo.

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
- Schriften (Bebas Neue, Oswald, Barlow, Noto Emoji) lädt `fetch_fonts.py` nach `rpdf/fonts/` (Google Fonts, SIL Open Font License); sie sind nicht im Repo.

## Engines für Bilder

`project.json` → `"engine"`:

- **`agy`** (Standard): agy (Antigravity CLI) erzeugt die Bilder mit seinem eingebauten Bildwerkzeug über die eigene Anmeldung, **kein API-Key nötig**. Rund 40–100 s pro Bild. agy legt die Bilder unter `~/.gemini/antigravity-cli/brain/<conversation_id>/` ab, das Tool übernimmt sie von dort. Die Conversation-ID wird als `interaction_id` in `images.json` gespeichert, Änderungen laufen über `agy --conversation ID`.
- **`gemini`**: direkter Aufruf der Interactions API (`gemini-3.1-flash-lite-image`), braucht `GEMINI_API_KEY` oder `~/gemini.key` und `pip install google-genai`.
- **`comfy`**: ComfyUI wie bisher.

Für `agy` und `gemini` gilt:
- Formate kommen nur als 1:1, 16:9, 9:16, 4:3, 3:4; das Tool wählt das nächste und schneidet mittig auf die Zielgröße zu (breite Banner verlieren etwas Höhe). Kein Seed, kein Negativ-Prompt (Negatives werden als „Do not include …“ angehängt).
- `illuminator.py edit PROJEKT KEY "Anweisung"` ändert ein vorhandenes Bild, statt es neu zu erzeugen.
- Bildkontrolle (QC): 1 Generierung + höchstens 2 Bearbeitungen desselben Bildes (nur die gefundenen Probleme), danach keine weiteren Änderungen. Die Konzept-/Neu-Generieren-Schleife gilt nur für ComfyUI.
- Ist das agy-Bildkontingent erschöpft (`QUOTA_EXHAUSTED` in `~/.gemini/antigravity-cli/cli.log`), bricht der Lauf sofort mit der Reset-Zeit ab, statt auf Bilder zu warten; ein noch aktiver Fehler wird schon vor dem Start erkannt. Bis dahin die Engine wechseln oder **Bei erschöpftem agy-Kontingent warten und automatisch fortsetzen** einschalten (GUI: Engine → Bild-Engine; `"quota_wait": true` in `project.json`; CLI: `--wait-quota`). Das Tool wartet dann bis zur Reset-Zeit aus dem Log plus 90 s (Statuszeile alle 10 Minuten, jederzeit abbrechbar) und macht dort weiter, wo es stand; fertige Bilder bleiben erhalten. Nennt das Log keine Reset-Zeit, versucht es alle 30 Minuten neu.
- Die Engine wählst du in der Oberfläche unter **Engine → Bild-Engine** (oder in `project.json`).

## Lizenz

[MIT](LICENSE)
