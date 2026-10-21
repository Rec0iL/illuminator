"""PySide6 front end for Illuminator."""
import html
import random
import shutil
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter,
    QMenu, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout,
    QWidget,
)

from . import background, comfy, importer, models, pipeline, planner, styles
from .project import (AI_FLAG, AI_OK, APPROVED, DEFAULTS, LANG_NAMES, NEEDS_REDO, REJECTED, TEXT_KEYS, Project,
                      clean_lang, create, lang_label)

TW, TH = 190, 84   # thumbnail size
BG_PREVIEW = (170, 240)   # preview of the dimmed page background
RECENT_MAX = 10
KIND_LABEL = {"cover": "Cover", "background": "Hintergrund", "chapter": "Kapitel", "section": "Abschnitt",
              "filler": "Füllbild"}
STATUS_ROLE = Qt.UserRole + 1     # review status of a list item; None = no image, no badge
BADGE = 24
STATUS_TEXT = {APPROVED: "von dir freigegeben", REJECTED: "von dir abgelehnt", AI_OK: "KI-Prüfung: passt",
               AI_FLAG: "KI-Prüfung: auffällig", "": "noch nicht bewertet"}
STATUS_COLOR = {APPROVED: "#2fb35a", REJECTED: "#e04848", AI_OK: "#3d8bfd", AI_FLAG: "#f08c00"}
STRICTNESS_HELP = {
    3: "Streng: das Bild muss dem Prompt genau folgen (bisheriges Verhalten).",
    2: "Mittel: das Motiv darf vom Prompt abweichen, das Bild muss aber klar zum Abschnitt passen und "
       "brauchbar sein.",
    1: "Locker: nur kaputte Bilder (Artefakte, verzerrte Schrift, verformte Figuren) oder klar themenfremde "
       "werden abgelehnt.",
}
WORKFLOW_DEFAULTS = {
    "anima": {"cfg": 4.0, "sampler": "euler", "scheduler": "simple", "steps": 28},
    "checkpoint": {"cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras", "steps": 28},
}


# ---------------------------------------------------------------- worker
class Job(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)
    image_saved = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn, self._cancel = fn, False

    def cancel(self):
        self._cancel = True

    def run(self):
        ctx = pipeline.Context(log=self.log.emit, progress=self.progress.emit, cancelled=lambda: self._cancel,
                               image_saved=self.image_saved.emit, isolate=True)
        try:
            self.done.emit(self.fn(ctx))
        except pipeline.Cancelled:
            self.failed.emit("Abgebrochen.")
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.failed.emit(f"{e}\n{traceback.format_exc(limit=3)}" if not isinstance(
                e, (comfy.ComfyError, planner.AgyError, FileNotFoundError, ValueError, RuntimeError)) else str(e))


# ---------------------------------------------------------------- small widgets
class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(90)
        self.clicked.connect(self._pick)
        self.set_color("#000000")

    def set_color(self, hex_color):
        self.color = hex_color
        light = QColor(hex_color).lightness() > 128
        self.setText(hex_color)
        self.setStyleSheet(f"background:{hex_color}; color:{'#000' if light else '#fff'}; border-radius:4px;")
        self.changed.emit()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Farbe wählen")
        if c.isValid():
            self.set_color(c.name())


class ImageView(QLabel):
    """Shows a pixmap scaled to the available space. It never asks for space itself,
    otherwise setting a pixmap at the current size makes the splitter grow step by step."""

    def __init__(self, text=""):
        super().__init__(text)
        self._pm = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setMinimumSize(120, 240)

    def set_image(self, pm, empty_text=""):
        self._pm = pm
        if pm.isNull():
            super().setPixmap(QPixmap())
            self.setText(empty_text)
        else:
            self._rescale()

    def _rescale(self):
        if self._pm.isNull():
            return
        r = self.contentsRect()
        super().setPixmap(self._pm.scaled(max(r.width(), 1), max(r.height(), 1),
                                          Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._rescale()


def paint_badge(p, r, status):
    """Review badge like in Minesweeper. The user's verdicts are round (green check, red X), the AI's are
    square (blue check, orange flag), unreviewed is an empty ring."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(r).adjusted(1, 1, -1, -1)
    color = STATUS_COLOR.get(status)
    if color is None:
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.8, Qt.DashLine))
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawEllipse(r)
        p.restore()
        return
    p.setPen(QPen(QColor(0, 0, 0, 160), 1))
    p.setBrush(QColor(color))
    if status in (APPROVED, REJECTED):
        p.drawEllipse(r)
    else:
        p.drawRoundedRect(r, 4, 4)
    x, y, w = r.x(), r.y(), r.width()
    pt = lambda fx, fy: QPointF(x + fx * w, y + fy * w)
    p.setPen(QPen(QColor("#ffffff"), max(2.0, w / 9), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    if status in (APPROVED, AI_OK):
        path = QPainterPath(pt(0.26, 0.52))
        path.lineTo(pt(0.43, 0.69))
        path.lineTo(pt(0.75, 0.33))
        p.drawPath(path)
    elif status == REJECTED:
        p.drawLine(pt(0.31, 0.31), pt(0.69, 0.69))
        p.drawLine(pt(0.69, 0.31), pt(0.31, 0.69))
    else:   # AI flag
        p.drawLine(pt(0.34, 0.22), pt(0.34, 0.80))
        flag = QPainterPath(pt(0.36, 0.22))
        flag.lineTo(pt(0.76, 0.37))
        flag.lineTo(pt(0.36, 0.52))
        flag.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawPath(flag)
    p.restore()


def badge_pixmap(status, size=BADGE):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_badge(p, QRect(0, 0, size, size), status)
    p.end()
    return pm


class StatusDelegate(QStyledItemDelegate):
    """Draws the review badge in the corner of each thumbnail; a click on it emits `clicked(row)`."""
    clicked = Signal(int)

    def _badge_rect(self, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        icon = style.subElementRect(QStyle.SE_ItemViewItemDecoration, opt, opt.widget)
        return QRect(icon.right() - BADGE - 3, icon.top() + 3, BADGE, BADGE)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        status = index.data(STATUS_ROLE)
        if status is not None:
            paint_badge(painter, self._badge_rect(option, index), status)

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton
                and index.data(STATUS_ROLE) is not None
                and self._badge_rect(option, index).contains(event.position().toPoint())):
            self.clicked.emit(index.row())
            return True
        return super().editorEvent(event, model, option, index)


def palette_pixmap(pal, w=280, h=150):
    """Small mock page in the palette's colors: chapter label, heading, body text, swatches."""
    pm = QPixmap(w, h)
    pm.fill(QColor(pal["bg"]))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont()
    f.setPixelSize(10)
    f.setBold(True)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
    p.setFont(f)
    p.setPen(QColor(pal["accent2"]))
    p.drawText(14, 22, "KAPITEL 1")
    p.fillRect(14, 28, 70, 2, QColor(pal["accent2"]))
    f.setPixelSize(17)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
    p.setFont(f)
    p.setPen(QColor(pal["accent"]))
    p.drawText(14, 52, "Überschrift")
    f.setPixelSize(10)
    f.setBold(False)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 0)
    p.setFont(f)
    p.setPen(QColor(pal["ink"]))
    for i, line in enumerate(["Fließtext im Regelwerk, gut lesbar auf", "dem Seitenhintergrund. Ein Link und",
                              "eine Tabelle nutzen die Akzentfarbe."]):
        p.drawText(14, 72 + 14 * i, line)
    sw = (w - 28) // 4
    for i, k in enumerate(("bg", "ink", "accent", "accent2")):
        p.fillRect(14 + i * sw, h - 30, sw - 4, 18, QColor(pal[k]))
    p.setPen(QPen(QColor(255, 255, 255, 60), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(14, h - 30, sw - 4, 18)
    p.end()
    return pm


class PaletteDialog(QDialog):
    """Shows the proposed palettes as small mock pages; the user picks one."""

    def __init__(self, palettes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Farbpalette wählen")
        self.palettes, self.choice = palettes, None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Welche Farbpalette soll das PDF bekommen? (Doppelklick übernimmt sofort)"))
        grid = QGridLayout()
        self.cards = []
        for i, pal in enumerate(palettes):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setIcon(QIcon(palette_pixmap(pal)))
            b.setIconSize(QSize(280, 150))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setText(pal["name"].replace("&", "&&"))     # a single & would become a shortcut
            low = pipeline.contrast(pal["ink"], pal["bg"]) < 7
            b.setToolTip(pal["note"] + ("\n⚠ Text auf Hintergrund hat wenig Kontrast." if low else ""))
            b.setStyleSheet("QToolButton { padding:6px; border:2px solid #2a2f39; border-radius:8px; }"
                            "QToolButton:checked { border-color:#e6eaf0; background:#22262e; }")
            b.toggled.connect(lambda on, i=i: on and self._picked(i))
            b.installEventFilter(self)
            note = QLabel(pal["note"])
            note.setWordWrap(True)
            note.setObjectName("dim")
            note.setMaximumWidth(300)
            cell = QVBoxLayout()
            cell.addWidget(b, 0, Qt.AlignHCenter)
            cell.addWidget(note)
            grid.addLayout(cell, i // 2, i % 2)
            self.cards.append(b)
        v.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Übernehmen")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.cards[0].setChecked(True)

    def _picked(self, i):
        self.choice = self.palettes[i]

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonDblClick and obj in self.cards:
            obj.setChecked(True)
            self.accept()
            return True
        return super().eventFilter(obj, ev)


def path_row(edit, dialog):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit)
    b = QPushButton("…")
    b.setFixedWidth(32)
    b.clicked.connect(dialog)
    h.addWidget(b)
    return w


def text_box(height=70):
    t = QPlainTextEdit()
    t.setFixedHeight(height)
    return t


def section_label(text):
    l = QLabel(text)
    l.setObjectName("section")
    return l


class AddLanguageDialog(QDialog):
    """Language code and image handling for a translation that is about to be imported."""

    def __init__(self, parent, path, guess, project):
        super().__init__(parent)
        self.setWindowTitle("Übersetzung hinzufügen")
        self.project = project
        f = QFormLayout(self)
        f.addRow(QLabel(f"<b>{html.escape(path.name)}</b>"))
        self.cb = QComboBox()
        self.cb.setEditable(True)
        for code in LANG_NAMES:
            self.cb.addItem(lang_label(code), code)
        self.cb.setCurrentIndex(self.cb.findData(guess))     # -1 (empty) when the language is unknown
        self.cb.lineEdit().setPlaceholderText("Sprachcode, z.B. pl")
        f.addRow("Sprache", self.cb)
        self.chk = QCheckBox("Bilder aus dem Dokument übernehmen")
        self.chk.setToolTip("Normalerweise weglassen: die Übersetzung bekommt die Bilder des Projekts. Ein PDF, das "
                            "selbst aus diesem Projekt stammt, bringt sonst alle Illustrationen als Bilder mit.")
        f.addRow(self.chk)
        hint = QLabel("Ohne Haken werden die Bilder des Dokuments nicht übernommen. Die Themenbilder des "
                      "Projekts gelten für alle Sprachen; übernommene Bilder kämen zusätzlich in den Text.")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        f.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.setMinimumWidth(420)

    def code(self):
        return clean_lang((self.cb.currentText().split() or [""])[0])

    def keep_images(self):
        return self.chk.isChecked()

    def _ok(self):
        code = self.code()
        if not code:
            QMessageBox.information(self, "Sprache", "Bitte einen Sprachcode angeben (z.B. en, de, pl).")
        elif code in self.project.languages():
            extra = ("\nIst die Hauptsprache falsch eingestellt? Das ändert sich unter „Buch → Sprache“."
                     if code == self.project.primary_lang else "")
            QMessageBox.information(self, "Sprache", f"Die Sprache „{code}“ gibt es schon in diesem Projekt.{extra}")
        else:
            self.accept()


class StripImagesDialog(QDialog):
    """Pick which image references to take out of a translation's Markdown (all or selected)."""

    def __init__(self, parent, md_path, images):
        super().__init__(parent)
        self.setWindowTitle("Bilder aus der Übersetzung entfernen")
        self.images = images
        self.indexes = None            # None = all
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"{len(images)} Bild-Verweis(e) in {md_path.name}. Haken setzen und „Ausgewählte "
                           "entfernen“, oder alle auf einmal entfernen. Die Bilddateien selbst bleiben."))
        self.list = QListWidget()
        self.list.setIconSize(QSize(72, 44))
        for it in images:
            item = QListWidgetItem(f"Zeile {it['line']} · {it['src']}\n{it['heading'] or '(vor der ersten Überschrift)'}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            pm = QPixmap(str(md_path.parent / it["src"])) if not it["src"].startswith(("http", "data:")) else QPixmap()
            if not pm.isNull():
                item.setIcon(QIcon(pm.scaled(72, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.list.addItem(item)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, state in (("Alle markieren", Qt.Checked), ("Keine markieren", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, st=state: [self.list.item(i).setCheckState(st)
                                                         for i in range(self.list.count())])
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        bb = QHBoxLayout()
        b_some = QPushButton("Ausgewählte entfernen")
        b_all = QPushButton("Alle entfernen")
        b_all.setObjectName("primary")
        b_cancel = QPushButton("Abbrechen")
        b_some.clicked.connect(self._some)
        b_all.clicked.connect(self.accept)
        b_cancel.clicked.connect(self.reject)
        for b in (b_some, b_all, b_cancel):
            bb.addWidget(b)
        v.addLayout(bb)
        self.resize(640, 460)

    def _some(self):
        self.indexes = {self.images[i]["index"] for i in range(self.list.count())
                        if self.list.item(i).checkState() == Qt.Checked}
        if not self.indexes:
            QMessageBox.information(self, "Bilder entfernen", "Es ist kein Bild markiert.")
            return
        self.accept()


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, project_dir=None):
        super().__init__()
        self.setWindowTitle("Rulebook PDF")
        self.resize(1500, 920)
        self.settings = QSettings("Illuminator", "illuminator")
        self.project = None
        self.job = None
        self.slots = []
        self.preview_path = None
        self._thumb_cache = {}
        self._view_lang = None        # language whose filler images the list shows (None = main language)
        self._lang_shown = None       # translation shown in the "Sprachen" tab
        self._summary_start = ""

        self._build_toolbar()
        self._build_ui()
        self._set_busy(False)

        start = project_dir or self.settings.value("last_project")
        if start and (Path(start) / "project.json").exists():
            self.load_project(start)
        else:
            self.statusBar().showMessage("Projekt öffnen oder neu anlegen.")

    # ---------- layout ----------
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        for text, slot in [("Neues Projekt…", self.new_project), ("Letzte Projekte", None),
                           ("Öffnen…", self.open_project),
                           ("Speichern", self.save_project), (None, None),
                           ("Quelle öffnen", lambda: self._open_file(self.project and self.project.source_path)),
                           ("PDF öffnen", self._open_pdf),
                           ("Projektordner", lambda: self._open_file(self.project and self.project.root))]:
            if text is None:
                tb.addSeparator()
                continue
            if slot is None:
                self.recent_menu = QMenu(self)
                self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
                btn = QToolButton()
                btn.setText(text)
                btn.setPopupMode(QToolButton.InstantPopup)
                btn.setMenu(self.recent_menu)
                tb.addWidget(btn)
                continue
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
        self.project_label = QLabel()
        self.project_label.setObjectName("dim")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addWidget(self.project_label)

    def _build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)

        # left: settings
        tabs = QTabWidget()
        tabs.setMinimumWidth(470)
        tabs.addTab(self._scroll(self._book_tab()), "Buch")
        tabs.addTab(self._scroll(self._style_tab()), "Stil")
        tabs.addTab(self._scroll(self._lang_tab()), "Sprachen")
        tabs.addTab(self._scroll(self._engine_tab()), "Engine")
        split.addWidget(tabs)

        # right: images + log
        right = QSplitter(Qt.Vertical)
        img_split = QSplitter(Qt.Horizontal)
        self.slot_list = QListWidget()
        self.slot_list.setViewMode(QListWidget.IconMode)
        self.slot_list.setSelectionMode(QListWidget.ExtendedSelection)   # Strg/Umschalt: mehrere Bilder
        self.slot_list.setIconSize(QSize(TW, TH))
        self.slot_list.setGridSize(QSize(TW + 14, TH + 54))
        self.slot_list.setUniformItemSizes(True)
        self.slot_list.setResizeMode(QListWidget.Adjust)
        self.slot_list.setMovement(QListWidget.Static)
        self.slot_list.setWordWrap(True)
        self.slot_list.setSpacing(4)
        self.slot_list.currentRowChanged.connect(self._on_row_changed)
        self.status_delegate = StatusDelegate(self.slot_list)
        self.status_delegate.clicked.connect(self._badge_clicked)
        self.slot_list.setItemDelegate(self.status_delegate)
        self.slot_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.slot_list.customContextMenuRequested.connect(self._list_menu)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.view_row = QWidget()
        vr = QHBoxLayout(self.view_row)
        vr.setContentsMargins(0, 0, 0, 0)
        vr.addWidget(QLabel("Füllbilder für"))
        self.cb_view_lang = QComboBox()
        self.cb_view_lang.setToolTip("Themenbilder (Cover, Kapitel, Abschnitte) sind für alle Sprachen gleich; "
                                     "Füllbilder hat jede Sprache für sich.")
        self.cb_view_lang.activated.connect(lambda _: self._view_changed())
        vr.addWidget(self.cb_view_lang, 1)
        self.view_row.hide()
        lv.addWidget(self.view_row)
        lv.addWidget(self.slot_list, 1)
        img_split.addWidget(left)
        img_split.addWidget(self._detail_panel())
        img_split.setSizes([470, 550])
        right.addWidget(img_split)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("log")
        right.addWidget(self.log_box)
        right.setSizes([700, 160])
        split.addWidget(right)
        split.setSizes([480, 1020])

        # bottom action bar
        bar = QHBoxLayout()
        self.btn_plan = QPushButton("① Prompts planen (KI)")
        self.chk_overwrite = QCheckBox("vorhandene überschreiben")
        self.btn_images = QPushButton("② Fehlende Bilder")
        self.btn_images_all = QPushButton("Alle Bilder neu")
        self.btn_check = QPushButton("Bilder prüfen (KI)")
        self.btn_build = QPushButton("③ PDF bauen")
        self.btn_all = QPushButton("▶ Alles")
        self.btn_cancel = QPushButton("Abbrechen")
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.btn_build.setObjectName("primary")
        self.btn_all.setObjectName("primary")
        self.btn_plan.clicked.connect(self.run_plan)
        self.btn_images.clicked.connect(lambda: self.run_images(False))
        self.btn_images_all.clicked.connect(self._confirm_all_images)
        self.btn_check.clicked.connect(self.run_check)
        self.btn_build.clicked.connect(lambda: self.run_build())
        self.btn_all.clicked.connect(self.run_all)
        self.btn_cancel.clicked.connect(lambda: self.job and self.job.cancel())
        for w in (self.btn_plan, self.chk_overwrite, self.btn_images, self.btn_images_all,
                  self.btn_check, self.btn_build, self.btn_all):
            bar.addWidget(w)
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.btn_cancel)
        outer.addLayout(bar)

        # second row: everything rejected (red X from you, flag from the AI) made again
        bar = QHBoxLayout()
        self.lb_rejected = QLabel()
        self.lb_rejected.setObjectName("dim")
        self.btn_redo_seed = QPushButton("Neue Bilder, neue Seeds")
        self.btn_redo_seed.setToolTip("Alle abgelehnten Bilder (rotes ✗ und KI-Flagge) mit gleichem Prompt und "
                                      "neuem Seed neu generieren. Grün und blau abgehakte bleiben.")
        self.btn_redo_prompt = QPushButton("Neue Prompts + neue Bilder")
        self.btn_redo_prompt.setToolTip("Für alle abgelehnten Bilder (rotes ✗ und KI-Flagge) einen neuen Prompt "
                                        "schreiben lassen und mit neuem Seed neu generieren. Grün und blau "
                                        "abgehakte bleiben.")
        self.btn_redo_seed.clicked.connect(lambda: self.run_redo(False))
        self.btn_redo_prompt.clicked.connect(lambda: self.run_redo(True))
        legend = QLabel("Klick auf das Symbol am Bild: freigeben → ablehnen → zurücksetzen")
        legend.setObjectName("dim")
        for w in (self.lb_rejected, self.btn_redo_seed, self.btn_redo_prompt):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(legend)
        outer.addLayout(bar)
        self.setCentralWidget(root)
        self.action_buttons = [self.btn_plan, self.btn_images, self.btn_images_all, self.btn_build,
                               self.btn_all, self.btn_preview, self.btn_regen, self.btn_replan,
                               self.btn_refine, self.btn_accept, self.btn_check, self.btn_check_one,
                               self.btn_analyse, self.btn_cover_auto, self.btn_remove, self.btn_redo_seed,
                               self.btn_redo_prompt, self.btn_palettes] + self.review_buttons + self.compress_buttons \
            + self.lang_buttons

    def _scroll(self, widget):
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.NoFrame)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        s.setWidget(widget)
        return s

    def _book_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.ed_source = QLineEdit()
        self.ed_output = QLineEdit()
        self.cb_kind = QComboBox()
        for key, label in styles.CONTENT_TYPES.items():
            self.cb_kind.addItem(label, key)
        self.cb_kind.setToolTip("Bestimmt, wie die KI Bild-Prompts schreibt und Bilder bewertet: "
                                "PnP-Regelwerk = Fantasy-/Rollenspiel-Szenen, Anderes Dokument = "
                                "Motive passend zum Thema (Readme, Tutorial, Dokumentation …).")
        self.cb_kind.currentIndexChanged.connect(self._kind_changed)
        f.addRow("Inhalt", self.cb_kind)
        self.ed_source.setToolTip("Markdown direkt, Word/PDF/Text werden wie beim Anlegen eines Projekts in Markdown "
                                  "umgewandelt (Ergebnis im Projektordner).")
        f.addRow("Quelle (md, docx, pdf, txt)", path_row(self.ed_source, self._pick_source))
        f.addRow("PDF-Ausgabe", path_row(self.ed_output, self._pick_output))
        f.addRow(section_label("Cover & Kopfzeile"))
        self.ed_title, self.ed_subtitle, self.ed_kicker = QLineEdit(), QLineEdit(), QLineEdit()
        self.ed_tagline, self.ed_running, self.ed_chlabel = QLineEdit(), QLineEdit(), QLineEdit()
        for label, ed in [("Titel", self.ed_title), ("Untertitel", self.ed_subtitle),
                          ("Kicker (über dem Titel)", self.ed_kicker), ("Tagline", self.ed_tagline),
                          ("Kopfzeile", self.ed_running), ("Kapitel-Label", self.ed_chlabel)]:
            f.addRow(label, ed)
        self.btn_cover_auto = QPushButton("Alles automatisch ausfüllen (KI)")
        self.btn_cover_auto.setToolTip("Die KI liest den Anfang des Regelwerks und schlägt Titel, Untertitel, "
                                       "Kicker, Tagline, Kopfzeile und Kapitel-Label vor.")
        self.btn_cover_auto.clicked.connect(self.run_cover_auto)
        f.addRow(self.btn_cover_auto)
        f.addRow(section_label("Struktur"))
        self.cb_lang = QComboBox()
        self.cb_lang.setEditable(True)
        self.cb_lang.addItems(list(LANG_NAMES))
        self.cb_lang.setToolTip("Sprache des Quelltexts (Hauptsprache): Silbentrennung und Grundlage für die "
                                "Übersetzungen im Tab „Sprachen“.")
        self.cb_level = QComboBox()
        self.cb_level.addItems(["Automatisch", "# Kapitel / ## Abschnitte", "## Kapitel / ### Abschnitte"])
        self.chk_section_images = QCheckBox("Bild für jeden Abschnitt")
        self.chk_texture = QCheckBox("Hintergrundtextur auf allen Seiten")
        self.chk_justify = QCheckBox("Blocksatz mit Silbentrennung")
        self.sp_font = QDoubleSpinBox()
        self.sp_font.setRange(7.5, 13.0)
        self.sp_font.setSingleStep(0.2)
        self.sp_font.setSuffix(" pt")
        f.addRow("Sprache", self.cb_lang)
        f.addRow("Überschriften", self.cb_level)
        f.addRow("", self.chk_section_images)
        f.addRow("", self.chk_texture)
        self.sl_bgdim = QSlider(Qt.Horizontal)
        self.sl_bgdim.setRange(-background.ADJUST_RANGE, background.ADJUST_RANGE)
        self.sl_bgdim.setTickPosition(QSlider.TicksBelow)
        self.sl_bgdim.setTickInterval(50)
        self.sl_bgdim.setPageStep(10)
        self.sl_bgdim.setToolTip(
            "Das Programm berechnet aus dem Hintergrundbild und den Textfarben, wie stark es abgedunkelt werden muss, "
            "damit Text und Überschriften gut lesbar bleiben (Mitte = dieses Optimum). Nach links wird es dunkler, "
            "nach rechts heller und sichtbarer; alle 50 Schritte verdoppelt oder halbiert sich die Helligkeit.")
        f.addRow("Hintergrund-Helligkeit", self.sl_bgdim)
        self.lb_bgdim = QLabel()
        self.lb_bgdim.setWordWrap(True)
        self.lb_bgdim.setObjectName("dim")
        self.lb_bgdim.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        f.addRow(self.lb_bgdim)
        self.lb_bgpreview = QLabel()
        self.lb_bgpreview.setFixedSize(*BG_PREVIEW)
        self.lb_bgpreview.setAlignment(Qt.AlignCenter)
        self.lb_bgpreview.setObjectName("preview")
        f.addRow("", self.lb_bgpreview)
        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.setInterval(120)
        self._bg_timer.timeout.connect(self._update_bg_preview)
        self.sl_bgdim.valueChanged.connect(lambda _: self._bg_timer.start())
        f.addRow("", self.chk_justify)
        f.addRow("Schriftgröße", self.sp_font)
        self.chk_fill = QCheckBox("Leerraum mit Füllbildern füllen")
        self.chk_fill.setToolTip("Große Lücken am Seitenende nach kurzen Abschnitten bekommen ein "
                                 "zusätzliches Bild zum selben Abschnitt.")
        self.sp_fill = QSpinBox()
        self.sp_fill.setRange(30, 200)
        self.sp_fill.setSuffix(" mm")
        f.addRow("", self.chk_fill)
        f.addRow("Füllbild ab Lücke", self.sp_fill)
        f.addRow(section_label("Farben"))
        self.col_bg, self.col_ink, self.col_accent, self.col_accent2 = (ColorButton() for _ in range(4))
        f.addRow("Hintergrund", self.col_bg)
        f.addRow("Text", self.col_ink)
        f.addRow("Akzent (Überschriften)", self.col_accent)
        f.addRow("Akzent 2 (Kapitel, Initialen)", self.col_accent2)
        for button in (self.col_ink, self.col_accent):
            button.changed.connect(self._bg_timer.start)      # the optimum depends on the text colors
        self.btn_palettes = QPushButton("4 Farbpaletten vorschlagen (KI)")
        self.btn_palettes.setToolTip("Die KI schlägt vier Farbpaletten vor, die zum Thema des Regelwerks passen. "
                                     "Du suchst dir eine aus, sie ersetzt die vier Farben oben.")
        self.btn_palettes.clicked.connect(self.run_palettes)
        f.addRow(self.btn_palettes)
        return w

    def _style_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.cb_preset = QComboBox()
        self.cb_preset.addItems([styles.CUSTOM] + list(styles.presets_for("rulebook")))
        self.cb_preset.activated.connect(self._apply_preset)
        f.addRow("Vorlage", self.cb_preset)
        self.ed_wish = text_box(56)
        f.addRow(self._field_head("Stilwunsch an die KI", "wish", self.ed_wish))
        self.ed_wish.setPlaceholderText("z.B. „soll aussehen wie One Piece“ oder „düster wie Dark Souls, aber farbig“")
        self.btn_refine = QPushButton("Mit KI verfeinern")
        self.btn_refine.clicked.connect(self.run_refine)
        f.addRow(self.ed_wish)
        f.addRow(self.btn_refine)
        self.lb_note = QLabel()
        self.lb_note.setWordWrap(True)
        self.lb_note.setObjectName("dim")
        f.addRow(self.lb_note)
        self.ed_style = text_box(110)
        f.addRow(self._field_head("Stil-Suffix (an jeden Prompt angehängt)", "style", self.ed_style))
        f.addRow(self.ed_style)
        self.ed_negative = text_box(70)
        f.addRow(self._field_head("Negativ-Prompt (Standard für alle Bilder)", "negative", self.ed_negative))
        f.addRow(self.ed_negative)
        f.addRow(section_label("Wunsch für alle Bild-Prompts"))
        self.ed_prompt_wish = text_box(90)
        self.ed_prompt_wish.setPlaceholderText(
            "z.B. „Alle Figuren sind Mäuse. Keine echten Waffen: Waffen sind Alltagsdinge – ein Holzschwert ist "
            "ein Zahnstocher, ein besseres Schwert eine Nadel, ein Hammer ein Fingerhut.“")
        self.ed_prompt_wish.setToolTip(
            "Gilt für jeden Bild-Prompt (Cover, Hintergrund, Kapitel, Abschnitte, Füllbilder, neue Prompts, Bilder "
            "neu machen) und für die Bildkontrolle. Schreib es in deiner Sprache, die Prompts bleiben englisch.")
        f.addRow(self.ed_prompt_wish)
        wish_hint = QLabel("Gilt für neue Prompts und die Bildkontrolle. Schon geschriebene Prompts ändern sich erst, "
                           "wenn du „Prompts planen“ mit „vorhandene überschreiben“ laufen lässt.")
        wish_hint.setWordWrap(True)
        wish_hint.setObjectName("dim")
        wish_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        f.addRow(wish_hint)
        self.ed_world = text_box(80)
        self.world_head = self._field_head("Welt (von der KI erkannt, für alle Prompts)", "world", self.ed_world)
        f.addRow(self.world_head)
        f.addRow(self.ed_world)
        self.ed_summary = text_box(90)
        self.ed_summary.setPlaceholderText("Wird beim ersten KI-Aufruf, der das Buch kennen muss, einmal geschrieben.")
        f.addRow(self._field_head("Kurzfassung des Buchs (die KI bekommt diese statt des Texts)", "summary",
                                  self.ed_summary))
        f.addRow(self.ed_summary)
        hint = QLabel("Tipp: Stil ändern → Abschnitt wählen → „Vorschau“. Passt es, mit "
                      "„vorhandene überschreiben“ neu planen und „Alle Bilder neu“.")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        f.addRow(hint)
        return w

    def _field_head(self, title, kind, edit):
        """Section label with a 'Komprimieren' button for the text field below it."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 8, 0, 0)
        label = section_label(title)
        row.label = label
        label.setWordWrap(True)
        label.setMinimumWidth(10)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(label, 1)
        b = QPushButton("Komprimieren")
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        b.setObjectName("small")
        b.setToolTip("Die KI entfernt Doppeltes, Widersprüche und Füllwörter, damit das Bildmodell den Text besser umsetzt")
        b.clicked.connect(lambda: self.run_compress(kind, edit))
        h.addWidget(b)
        s = QPushButton("Vorschlagen")
        s.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        s.setObjectName("small")
        s.setToolTip("Die KI schlägt den Inhalt dieses Felds aus Buch, den anderen Feldern und dem gewählten "
                     "Bildmodell vor")
        s.clicked.connect(lambda: self.run_suggest(kind, edit))
        h.addWidget(s)
        self.compress_buttons = getattr(self, "compress_buttons", []) + [b, s]
        return row

    def _lang_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel("Dasselbe Buch in weiteren Sprachen. Cover, Kapitel- und Abschnittsbilder sowie der "
                      "Hintergrund sind für alle Sprachen gleich: Kapitel und Abschnitte der Übersetzung bekommen "
                      "die Bilder der Hauptsprache nach ihrer Reihenfolge. Füllbilder gibt es pro Sprache, weil der "
                      "übersetzte Text anders umbricht. „PDF bauen“ und „▶ Alles“ setzen alle angehakten "
                      "Sprachen in einem Rutsch.")
        info.setWordWrap(True)
        info.setObjectName("dim")
        info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)    # wrapped text must not widen the tab
        v.addWidget(info)
        self.lang_list = QListWidget()
        self.lang_list.setFixedHeight(120)
        self.lang_list.currentRowChanged.connect(self._on_lang_row)
        self.lang_list.itemChanged.connect(self._on_lang_checked)
        v.addWidget(self.lang_list)
        row = QHBoxLayout()
        self.btn_lang_add = QPushButton("Übersetzung hinzufügen…")
        self.btn_lang_add.setToolTip("Markdown, Word, PDF oder Text. PDF und Word werden wie beim Anlegen eines "
                                     "Projekts in Markdown umgewandelt; ihre Bilder lässt der Import weg.")
        self.btn_lang_add.clicked.connect(self.add_language)
        self.btn_lang_remove = QPushButton("Entfernen")
        self.btn_lang_remove.setToolTip("Nimmt die Sprache aus dem Projekt. Das Markdown bleibt im Ordner, die "
                                        "Füllbilder dieser Sprache wandern nach .build/removed/.")
        self.btn_lang_remove.clicked.connect(self.remove_language)
        row.addWidget(self.btn_lang_add)
        row.addWidget(self.btn_lang_remove)
        row.addStretch(1)
        v.addLayout(row)

        self.lb_lang_main = QLabel("Hauptsprache: ihre Texte, Farben und der Stil stehen in den Tabs „Buch“ und "
                                   "„Stil“. Den Sprachcode ändert man unter „Buch → Sprache“.")
        self.lb_lang_main.setWordWrap(True)
        self.lb_lang_main.setObjectName("dim")
        self.lb_lang_main.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.lb_lang_main)

        self.lang_detail = QWidget()
        f = QFormLayout(self.lang_detail)
        f.setContentsMargins(0, 0, 0, 0)
        self.ed_l_source, self.ed_l_output = QLineEdit(), QLineEdit()
        f.addRow("Markdown", path_row(self.ed_l_source, self._pick_lang_source))
        f.addRow("PDF-Ausgabe", path_row(self.ed_l_output, self._pick_lang_output))
        self.l_fields = {key: QLineEdit() for key in TEXT_KEYS}
        for key, label in [("title", "Titel"), ("subtitle", "Untertitel"), ("kicker", "Kicker (über dem Titel)"),
                           ("tagline", "Tagline"), ("running_title", "Kopfzeile"), ("chapter_label", "Kapitel-Label")]:
            f.addRow(label, self.l_fields[key])
        self.cb_l_level = QComboBox()
        self.cb_l_level.addItems(["Automatisch", "# Kapitel / ## Abschnitte", "## Kapitel / ### Abschnitte"])
        f.addRow("Überschriften", self.cb_l_level)
        self.btn_l_fill = QPushButton("Texte automatisch ausfüllen (KI)")
        self.btn_l_fill.setToolTip("Die KI liest den Anfang der Übersetzung und schreibt Titel, Untertitel, Kicker, "
                                   "Tagline, Kopfzeile und Kapitel-Label in deren Sprache.")
        self.btn_l_fill.clicked.connect(self.run_lang_cover_auto)
        self.btn_l_strip = QPushButton("Bilder aus dem Markdown entfernen…")
        self.btn_l_strip.setToolTip("Nimmt Bild-Verweise aus dem Markdown dieser Übersetzung: alle oder nur "
                                    "ausgewählte (z.B. Bilder, die ein PDF-Import mitgebracht hat).")
        self.btn_l_strip.clicked.connect(self.strip_language_images)
        self.btn_l_open = QPushButton("Markdown öffnen")
        self.btn_l_open.clicked.connect(lambda: self._lang_shown and self._open_file(
            self.project.source_for(self._lang_shown)))
        self.btn_l_build = QPushButton("Nur diese Sprache bauen")
        self.btn_l_build.clicked.connect(lambda: self._lang_shown and self.run_build([self._lang_shown]))
        for b in (self.btn_l_fill, self.btn_l_strip):
            f.addRow(b)
        brow = QWidget()
        h = QHBoxLayout(brow)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.btn_l_open)
        h.addWidget(self.btn_l_build)
        f.addRow(brow)
        self.lb_l_status = QLabel()
        self.lb_l_status.setWordWrap(True)
        self.lb_l_status.setTextFormat(Qt.RichText)
        self.lb_l_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        f.addRow(self.lb_l_status)
        v.addWidget(self.lang_detail)
        v.addStretch(1)
        self.lang_buttons = [self.btn_lang_add, self.btn_lang_remove, self.btn_l_fill, self.btn_l_strip,
                             self.btn_l_build]
        return w

    def _engine_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(section_label("Prompt-Generator"))
        self.cb_llm = QComboBox()
        self.cb_llm.addItem("agy – Antigravity CLI", "agy")
        self.cb_llm.addItem("Claude Code CLI (claude)", "claude")
        self.cb_llm.activated.connect(lambda _: self._llm_changed())
        f.addRow("Programm", self.cb_llm)
        self._llm_shown = "agy"
        self.cb_agy = QComboBox()
        self.cb_agy.setEditable(True)
        self.cb_agy.setMinimumContentsLength(14)
        self.cb_agy.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        btn = QPushButton("Modelle laden")
        btn.clicked.connect(self._load_agy_models)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.cb_agy, 1)
        h.addWidget(btn)
        f.addRow("Modell", row)

        f.addRow(section_label("Bild-Engine"))
        self.cb_engine = QComboBox()
        self.cb_engine.addItem("agy – Antigravity CLI (kein API-Key)", "agy")
        self.cb_engine.addItem("Gemini API (GEMINI_API_KEY)", "gemini")
        self.cb_engine.addItem("ComfyUI (lokal)", "comfy")
        self.cb_engine.activated.connect(lambda _: self._engine_changed())
        f.addRow("Engine", self.cb_engine)
        self.ed_gem_model = QLineEdit()
        self.cb_thinking = QComboBox()
        self.cb_thinking.addItems(["low", "high"])
        self.gem_text_agy = ("Bilder erzeugt und bearbeitet agy mit seinem eingebauten Bildwerkzeug über die "
                             "eigene Anmeldung (ca. 40–100 s pro Bild). Seitenverhältnis: nächstes von 1:1, "
                             "16:9, 9:16, 4:3, 3:4, dann mittig zugeschnitten. Kein Seed, kein Negativ-Prompt.")
        self.chk_quota_wait = QCheckBox("Bei erschöpftem agy-Kontingent warten und automatisch fortsetzen")
        self.chk_quota_wait.setToolTip("Statt abzubrechen wartet das Tool bis zur Reset-Zeit aus agys Log "
                                       "(plus 90 s) und macht dann weiter. Abbrechen geht jederzeit.")
        self.lb_gem = QLabel()
        self.lb_gem.setWordWrap(True)
        self.lb_gem.setObjectName("dim")
        f.addRow(self.lb_gem)
        f.addRow("", self.chk_quota_wait)
        f.addRow("Gemini-Modell", self.ed_gem_model)
        f.addRow("Thinking", self.cb_thinking)
        self.gem_widgets = [self.ed_gem_model, self.cb_thinking]

        comfy_first = f.rowCount()
        f.addRow(section_label("ComfyUI (Bilder)"))
        self.ed_url = QLineEdit()
        self.btn_connect = QPushButton("Verbinden")
        self.btn_connect.clicked.connect(self._connect_comfy)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.ed_url, 1)
        h.addWidget(self.btn_connect)
        f.addRow("URL", row)
        self.lb_comfy = QLabel("nicht verbunden")
        self.lb_comfy.setObjectName("dim")
        f.addRow("", self.lb_comfy)

        self.cb_model = QComboBox()
        self.cb_model.setMinimumContentsLength(14)
        self.cb_model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_model.activated.connect(self._on_model_selected)
        f.addRow("Bildmodell", self.cb_model)
        self.lb_model = QLabel()
        self.lb_model.setWordWrap(True)
        self.lb_model.setTextFormat(Qt.RichText)
        f.addRow("", self.lb_model)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_recommended = QPushButton("Empfohlene Werte")
        self.btn_recommended.setToolTip("Einstellungen auf die Vorgaben für diese Modellfamilie setzen")
        self.btn_recommended.clicked.connect(lambda: self._apply_model_settings(force_recommended=True))
        self.btn_link = QPushButton("In diffusion_models verlinken")
        self.btn_link.clicked.connect(self._link_model)
        h.addWidget(self.btn_recommended)
        h.addWidget(self.btn_link)
        h.addStretch(1)
        f.addRow("", row)
        self.btn_link.hide()
        f.addRow(section_label("Feinabstimmung"))

        self.cb_workflow = QComboBox()
        for k, label in comfy.WORKFLOWS.items():
            self.cb_workflow.addItem(label, k)
        self.cb_workflow.setMinimumContentsLength(14)
        self.cb_workflow.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_workflow.activated.connect(self._workflow_changed)
        f.addRow("Workflow", self.cb_workflow)

        self.cb_unet, self.cb_clip, self.cb_cliptype, self.cb_vae, self.cb_ckpt = (
            QComboBox() for _ in range(5))
        self.cb_sampler, self.cb_scheduler = QComboBox(), QComboBox()
        for cb in (self.cb_unet, self.cb_clip, self.cb_cliptype, self.cb_vae, self.cb_ckpt,
                   self.cb_sampler, self.cb_scheduler):
            cb.setEditable(True)
            cb.setMinimumContentsLength(14)
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.ed_custom = QLineEdit()
        self.engine_rows = {
            "anima": [("Diffusion-Modell", self.cb_unet), ("Text-Encoder", self.cb_clip),
                      ("Encoder-Typ", self.cb_cliptype), ("VAE", self.cb_vae)],
            "checkpoint": [("Checkpoint", self.cb_ckpt)],
            "custom": [("API-Workflow (.json)", path_row(self.ed_custom, self._pick_workflow))],
        }
        self.engine_form = f
        for rows in self.engine_rows.values():
            for label, widget in rows:
                f.addRow(label, widget)
        self.sp_steps = QSpinBox()
        self.sp_steps.setRange(1, 150)
        self.sp_cfg = QDoubleSpinBox()
        self.sp_cfg.setRange(0.0, 30.0)
        self.sp_cfg.setSingleStep(0.5)
        self.sampler_rows = [("Steps", self.sp_steps), ("CFG", self.sp_cfg),
                             ("Sampler", self.cb_sampler), ("Scheduler", self.cb_scheduler)]
        for label, widget in self.sampler_rows:
            f.addRow(label, widget)
        info = QLabel("Eigener Workflow: in ComfyUI über „Export (API)“ speichern. Prompt, Negativ, "
                      "Seed und Bildgröße werden automatisch eingesetzt.")
        info.setWordWrap(True)
        info.setObjectName("dim")
        f.addRow(info)
        self.comfy_rows = range(comfy_first, f.rowCount())
        self.engine_form = f

        f.addRow(section_label("Bildkontrolle (KI)"))
        self.chk_qc = QCheckBox("Generierte Bilder automatisch prüfen")
        self.chk_qc_fix = QCheckBox("Unpassende Bilder korrigieren")
        self.sp_qc_rounds = QSpinBox()
        self.sp_qc_rounds.setRange(1, 3)
        self.sp_qc_rounds.setSuffix(" Versuch(e)")
        self.chk_qc_radical = QCheckBox("Nach 3 Fehlversuchen Konzept radikal neu (bis zu 3×3 Bilder)")
        self.chk_qc_radical.setToolTip(
            "Bis zu 3 Bilder pro Konzept. Passt keins, schreibt die KI den Prompt von Grund auf neu: anderes "
            "Hauptmotiv, anderer Ort, andere Perspektive, aber weiter passend zum Abschnitt. Insgesamt höchstens "
            "3 Konzepte mit je 3 Bildern. Die Zahl der Neuversuche gilt dann nicht.")
        self.chk_qc_radical.toggled.connect(lambda on: self.sp_qc_rounds.setEnabled(not on))
        f.addRow("", self.chk_qc)
        f.addRow("", self.chk_qc_fix)
        f.addRow("Neuversuche", self.sp_qc_rounds)
        f.addRow("", self.chk_qc_radical)
        self.sl_qc_strict = QSlider(Qt.Horizontal)
        self.sl_qc_strict.setRange(1, 3)
        self.sl_qc_strict.setPageStep(1)
        self.sl_qc_strict.setTickPosition(QSlider.TicksBelow)
        self.sl_qc_strict.setTickInterval(1)
        self.lb_qc_strict = QLabel()
        self.lb_qc_strict.setWordWrap(True)
        self.lb_qc_strict.setObjectName("dim")
        self.sl_qc_strict.valueChanged.connect(lambda v: self.lb_qc_strict.setText(STRICTNESS_HELP[v]))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("locker"))
        h.addWidget(self.sl_qc_strict, 1)
        h.addWidget(QLabel("streng"))
        f.addRow("Strenge", row)
        f.addRow("", self.lb_qc_strict)
        self.qc_comfy_widgets = [self.sp_qc_rounds, self.chk_qc_radical]
        qc_info = QLabel("Die KI sieht sich jedes Bild an und prüft, ob es zum Abschnitt passt (Motiv, "
                         "Textartefakte, Anatomie). Dafür liest sie eine Bildkopie im Projektordner – "
                         "mit agy muss der Ordner in agys trustedWorkspaces liegen. Kostet einen KI-Aufruf pro Bild.")
        qc_info.setWordWrap(True)
        qc_info.setObjectName("dim")
        f.addRow(qc_info)
        return w

    def _detail_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 0, 0, 0)
        self.preview_label = ImageView("Kein Bild")
        self.preview_label.setObjectName("preview")
        v.addWidget(self.preview_label, 1)
        self.preview_banner = QLabel("VORSCHAU – noch nicht übernommen")
        self.preview_banner.setObjectName("banner")
        self.preview_banner.setAlignment(Qt.AlignCenter)
        self.preview_banner.hide()
        v.addWidget(self.preview_banner)
        self.slot_title = QLabel()
        self.slot_title.setObjectName("slotTitle")
        self.slot_title.setWordWrap(True)
        self.slot_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.slot_title)
        row = QHBoxLayout()
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(BADGE, BADGE)
        row.addWidget(self.status_icon, 0, Qt.AlignTop)
        self.qc_label = QLabel()
        self.qc_label.setWordWrap(True)
        self.qc_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.qc_label, 1)
        self.review_buttons = []
        for text, verdict, tip in [("✓ Freigeben", APPROVED, "Bild freigeben (grüner Haken) – gilt vor der KI-Prüfung"),
                                   ("✗ Ablehnen", REJECTED, "Bild ablehnen (rotes ✗) – wird mit „Neue Bilder …“ "
                                                            "neu gemacht"),
                                   ("Zurücksetzen", "", "Deine Bewertung entfernen, dann gilt wieder die KI-Prüfung")]:
            b = QPushButton(text)
            b.setObjectName("small")
            b.setToolTip(tip + ". Gilt für alle markierten Bilder.")
            b.clicked.connect(lambda _=False, v=verdict: self._set_review(v))
            row.addWidget(b, 0, Qt.AlignTop)
            self.review_buttons.append(b)
        v.addLayout(row)
        self.slot_prompt = text_box(96)
        self.slot_prompt.setPlaceholderText("Motiv (englisch) – der Stil-Suffix wird angehängt")
        v.addWidget(self.slot_prompt)
        self.slot_negative = text_box(48)
        v.addWidget(self.slot_negative)
        g = QGridLayout()
        self.chk_include_style = QCheckBox("Stil-Suffix anhängen")
        self.sp_seed = QSpinBox()
        self.sp_seed.setRange(1, 2**31 - 1)
        dice = QPushButton("Zufall")
        dice.clicked.connect(lambda: self.sp_seed.setValue(random.randint(1, 2**31 - 1)))
        g.addWidget(self.chk_include_style, 0, 0)
        g.addWidget(QLabel("Seed"), 0, 1, Qt.AlignRight)
        g.addWidget(self.sp_seed, 0, 2)
        g.addWidget(dice, 0, 3)
        v.addLayout(g)
        self.ed_hint = QLineEdit()
        self.ed_hint.setPlaceholderText("Wunsch an die KI für diesen Prompt, z.B. „eher Nacht, zwei Kämpfer“")
        v.addWidget(self.ed_hint)
        row = QHBoxLayout()
        self.btn_replan = QPushButton("Prompt neu (KI)")
        self.btn_preview = QPushButton("Vorschau")
        self.btn_accept = QPushButton("Übernehmen")
        self.btn_analyse = QPushButton("Analysieren (KI)")
        self.btn_analyse.setToolTip("Die KI sieht sich das Bild an und verbessert Prompt und Negativ-Prompt "
                                    "(berücksichtigt den Wunsch oben)")
        self.btn_analyse.clicked.connect(self.run_analyse)
        self.btn_check_one = QPushButton("Prüfen")
        self.btn_check_one.setToolTip("Dieses Bild von der KI prüfen lassen")
        self.btn_check_one.clicked.connect(self.run_check_one)
        self.btn_remove = QPushButton("Bild entfernen")
        self.btn_remove.setToolTip("Entfernt das Bild (oder alle markierten, Strg/Umschalt-Klick in der Liste), "
                                   "damit „Fehlende Bilder“ es mit neuem Seed neu erzeugt. Das Bild wird nach "
                                   ".build/removed/ verschoben, nicht gelöscht.")
        self.btn_remove.clicked.connect(self.run_remove)
        self.btn_regen = QPushButton("Generieren && speichern")
        self.btn_regen.setObjectName("primary")
        self.btn_replan.clicked.connect(self.run_replan)
        self.btn_preview.clicked.connect(self.run_preview)
        self.btn_accept.clicked.connect(self.accept_preview)
        self.btn_regen.clicked.connect(self.run_regen_one)
        for b in (self.btn_replan, self.btn_analyse, self.btn_check_one):
            row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        for b in (self.btn_preview, self.btn_accept, self.btn_regen):
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(self.btn_remove)
        return w

    # ---------- project ----------
    def load_project(self, path):
        self._view_lang = self._lang_shown = None
        try:
            self.project = Project(path)
            self.slots = self.project.sync_manifest(self._view_slots())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Projekt", f"Projekt konnte nicht geladen werden:\n{e}")
            self.project = Project(path)
            self.slots = []
        self.settings.setValue("last_project", str(self.project.root))
        self._remember_recent(self.project.root)
        self.project_label.setText(str(self.project.root))
        self._fill_form()
        self._refresh_slots()
        self._log(f"Projekt geladen: {self.project.root}")

    def _recent_projects(self):
        val = self.settings.value("recent_projects") or []
        if isinstance(val, str):   # QSettings returns a bare str for one-element lists
            val = [val]
        return [str(p) for p in val]

    def _remember_recent(self, root):
        root = str(Path(root).resolve())
        recent = [root] + [p for p in self._recent_projects() if p != root]
        self.settings.setValue("recent_projects", recent[:RECENT_MAX])

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.clear()
        recent = [p for p in self._recent_projects() if (Path(p) / "project.json").exists()]
        if not recent:
            menu.addAction("(noch keine Projekte)").setEnabled(False)
            return
        for p in recent:
            a = menu.addAction(f"{Path(p).name}   ·   {p}")
            a.triggered.connect(lambda _=False, p=p: self.load_project(p))
        menu.addSeparator()
        menu.addAction("Liste leeren", lambda: self.settings.remove("recent_projects"))

    def _fill_form(self):
        c = self.project.config
        self.ed_source.setText(c["source"])
        self.ed_output.setText(c["output"])
        for ed, key in [(self.ed_title, "title"), (self.ed_subtitle, "subtitle"), (self.ed_kicker, "kicker"),
                        (self.ed_tagline, "tagline"), (self.ed_running, "running_title"),
                        (self.ed_chlabel, "chapter_label")]:
            ed.setText(c.get(key, ""))
        self.cb_lang.setCurrentText(c.get("language", "de"))
        self.cb_level.setCurrentIndex(int(c.get("chapter_level") or 0))
        self.ed_summary.setPlainText(c.get("summary", ""))
        self._summary_start = c.get("summary", "")
        lay = c["layout"]
        self.chk_section_images.setChecked(lay.get("section_images", True))
        self.chk_texture.setChecked(lay.get("page_texture", True))
        self.sl_bgdim.setValue(int(lay.get("bg_dim_adjust", 0)))
        self.chk_justify.setChecked(lay.get("justify", True))
        self.sp_font.setValue(lay.get("font_size_pt", 9.6))
        self.chk_fill.setChecked(lay.get("fill_gaps", False))
        self.sp_fill.setValue(int(lay.get("filler_min_mm", 55)))
        qc = c.get("qc", {})
        self.chk_qc.setChecked(qc.get("enabled", False))
        self.chk_qc_fix.setChecked(qc.get("auto_fix", True))
        self.sp_qc_rounds.setValue(int(qc.get("rounds", 1)))
        self.chk_qc_radical.setChecked(qc.get("radical", False))
        self.sl_qc_strict.setValue(pipeline.qc_strictness(self.project))
        self.lb_qc_strict.setText(STRICTNESS_HELP[self.sl_qc_strict.value()])
        t = c["theme"]
        self.col_bg.set_color(t["bg"])
        self.col_ink.set_color(t["ink"])
        self.col_accent.set_color(t["accent"])
        self.col_accent2.set_color(t["accent2"])
        self.cb_kind.blockSignals(True)
        self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(c.get("content_type") or "rulebook")))
        self.cb_kind.blockSignals(False)
        self._refresh_kind_ui()
        preset = c.get("style_preset") or styles.CUSTOM
        self.cb_preset.setCurrentText(preset if preset in styles.presets_for(self._kind()) else styles.CUSTOM)
        self.ed_wish.setPlainText(c.get("style_wish", ""))
        self.ed_style.setPlainText(c.get("style", ""))
        self.ed_negative.setPlainText(c.get("negative", ""))
        self.ed_world.setPlainText(c.get("world", ""))
        self.ed_prompt_wish.setPlainText(c.get("prompt_wish", ""))
        self.cb_llm.setCurrentIndex(max(0, self.cb_llm.findData(c.get("llm", "agy"))))
        self._llm_shown = self.cb_llm.currentData()
        self.cb_agy.clear()
        self.cb_agy.addItems(planner.CLAUDE_MODELS if self._llm_shown == "claude" else [])
        self.cb_agy.setCurrentText(c["claude" if self._llm_shown == "claude" else "agy"].get("model", ""))
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData(c.get("engine", "agy"))))
        self.chk_quota_wait.setChecked(bool(c.get("quota_wait")))
        self.ed_gem_model.setText(c["gemini"].get("model", ""))
        self.cb_thinking.setCurrentText(c["gemini"].get("thinking_level", "low"))
        cc = c["comfy"]
        self.ed_url.setText(cc["url"])
        self.cb_workflow.setCurrentIndex(max(0, self.cb_workflow.findData(cc["workflow"])))
        for cb, key in [(self.cb_unet, "unet"), (self.cb_clip, "clip"), (self.cb_cliptype, "clip_type"),
                        (self.cb_vae, "vae"), (self.cb_ckpt, "checkpoint"), (self.cb_sampler, "sampler"),
                        (self.cb_scheduler, "scheduler")]:
            cb.setCurrentText(cc.get(key, ""))
        self.ed_custom.setText(cc.get("custom_workflow", ""))
        self.sp_steps.setValue(int(cc["steps"]))
        self.sp_cfg.setValue(float(cc["cfg"]))
        self._engine_changed()
        self._select_model_in_picker()
        self._apply_accent(t["accent"])
        self._fill_langs(select=self._lang_shown)

    def _collect(self):
        """Write the form back into project.config (and the selected slot into the manifest)."""
        if not self.project:
            return False
        c = self.project.config
        c["source"], c["output"] = self.ed_source.text().strip(), self.ed_output.text().strip()
        for ed, key in [(self.ed_title, "title"), (self.ed_subtitle, "subtitle"), (self.ed_kicker, "kicker"),
                        (self.ed_tagline, "tagline"), (self.ed_running, "running_title"),
                        (self.ed_chlabel, "chapter_label")]:
            c[key] = ed.text().strip()
        lang = clean_lang(self.cb_lang.currentText()) or c.get("language", "de")
        if lang != c.get("language") and any(t["lang"] == lang for t in c["translations"]):
            self._log(f"Die Hauptsprache kann nicht „{lang}“ werden: das ist schon eine Übersetzung.")
            lang = c["language"]
            self.cb_lang.setCurrentText(lang)
        c["language"] = lang
        c["chapter_level"] = self.cb_level.currentIndex()
        c["summary"] = self.ed_summary.toPlainText().strip()
        c["layout"].update(section_images=self.chk_section_images.isChecked(),
                           page_texture=self.chk_texture.isChecked(), bg_dim_adjust=self.sl_bgdim.value(),
                           justify=self.chk_justify.isChecked(), font_size_pt=round(self.sp_font.value(), 2),
                           fill_gaps=self.chk_fill.isChecked(), filler_min_mm=self.sp_fill.value())
        c.setdefault("qc", {}).update(enabled=self.chk_qc.isChecked(), auto_fix=self.chk_qc_fix.isChecked(),
                                      rounds=self.sp_qc_rounds.value(), radical=self.chk_qc_radical.isChecked(),
                                      strictness=self.sl_qc_strict.value())
        c["theme"].update(bg=self.col_bg.color, ink=self.col_ink.color,
                          accent=self.col_accent.color, accent2=self.col_accent2.color)
        preset = self.cb_preset.currentText()
        c["style_preset"] = "" if preset == styles.CUSTOM else preset
        c["content_type"] = self._kind()
        c["style_wish"] = self.ed_wish.toPlainText().strip()
        c["style"] = self.ed_style.toPlainText().strip()
        c["negative"] = self.ed_negative.toPlainText().strip()
        c["world"] = self.ed_world.toPlainText().strip()
        c["prompt_wish"] = self.ed_prompt_wish.toPlainText().strip()
        self._store_lang_edits()
        c["llm"] = self.cb_llm.currentData()
        c["claude" if c["llm"] == "claude" else "agy"]["model"] = self.cb_agy.currentText().strip()
        planner.configure(c)
        c["engine"] = self.cb_engine.currentData()
        c["quota_wait"] = self.chk_quota_wait.isChecked()
        c["gemini"].update(model=self.ed_gem_model.text().strip() or DEFAULTS["gemini"]["model"],
                           thinking_level=self.cb_thinking.currentText())
        cc = c["comfy"]
        cc.update(url=self.ed_url.text().strip(), workflow=self.cb_workflow.currentData(),
                  unet=self.cb_unet.currentText(), clip=self.cb_clip.currentText(),
                  clip_type=self.cb_cliptype.currentText(), vae=self.cb_vae.currentText(),
                  checkpoint=self.cb_ckpt.currentText(), custom_workflow=self.ed_custom.text().strip(),
                  steps=self.sp_steps.value(), cfg=round(self.sp_cfg.value(), 2),
                  sampler=self.cb_sampler.currentText(), scheduler=self.cb_scheduler.currentText())
        model = models.selected_model(cc)
        if model:
            models.remember(model, cc)
        self._store_slot_edits()
        return True

    def save_project(self):
        if self._collect():
            self.project.save()
            self.statusBar().showMessage("Gespeichert.", 3000)
            self._fill_langs(select=self._lang_shown)
            self._reload_slots()

    def _update_bg_preview(self):
        """Dimmed page background with sample text, as it comes out for the slider position and colors."""
        if not self.project:
            return
        adjust = self.sl_bgdim.value()
        theme = {k: b.color for k, b in (("bg", self.col_bg), ("ink", self.col_ink),
                                         ("accent", self.col_accent), ("accent2", self.col_accent2))}
        try:
            result = background.preview(self.project, adjust, theme)
        except Exception as e:  # noqa: BLE001 - a broken file must not break the form
            self.lb_bgpreview.clear()
            self.lb_bgdim.setText(f"Vorschau nicht möglich: {e}")
            return
        self.sl_bgdim.setEnabled(result is not None)
        if result is None:
            self.lb_bgpreview.clear()
            self.lb_bgdim.setText("Noch kein Hintergrundbild." if not self.project.has_image("background") else
                                  "Dieses Hintergrundbild wurde noch abgedunkelt gespeichert, der Regler wirkt erst "
                                  "bei einem neu erzeugten Bild („Bild entfernen“, dann „Fehlende Bilder“).")
            return
        img, info = result
        qimg = QImage(img.tobytes(), img.width, img.height, 3 * img.width, QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(qimg).scaled(*BG_PREVIEW, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p = QPainter(pm)
        heading = QFont()
        heading.setPointSize(9)
        heading.setBold(True)
        p.setFont(heading)
        p.setPen(QColor(theme["accent"]))
        p.drawText(10, 30, "ÜBERSCHRIFT")
        body = QFont()
        body.setPointSize(7)
        p.setFont(body)
        p.setPen(QColor(theme["ink"]))
        for i, line in enumerate(("Beispieltext, wie er auf der", "Seite über dem Hintergrund steht.",
                                  "Gut lesbar? Dann passt es.")):
            p.drawText(10, 50 + i * 14, line)
        p.end()
        self.lb_bgpreview.setPixmap(pm)
        self.lb_bgdim.setText(f"Berechnetes Optimum: Helligkeit {info['optimal']:.2f}. Jetzt: {info['brightness']:.2f}"
                              + (" (Optimum)." if adjust == 0 else f" ({adjust:+d})."))

    def _view_slots(self):
        """Slots of the list: the shared topic slots plus the fillers of the language shown."""
        lang = self._view_lang if self._view_lang in self.project.languages() else None
        return self.project.slots(lang=lang)

    def _reload_slots(self):
        try:
            self.slots = self.project.sync_manifest(self._view_slots())
        except Exception as e:  # noqa: BLE001
            self._log(f"Quelle konnte nicht gelesen werden: {e}")
            self.slots = []
        self._refresh_slots()

    def new_project(self):
        src, _ = QFileDialog.getOpenFileName(self, "Regelwerk oder Dokument wählen", str(Path.home()),
                                             "Dokumente (*.md *.markdown *.docx *.pdf *.txt)")
        if not src:
            return
        labels = list(styles.CONTENT_TYPES.values())
        current = styles.CONTENT_TYPES[self._kind()]
        choice, ok = QInputDialog.getItem(self, "Inhalt", "Was für ein Dokument ist das?", labels,
                                          labels.index(current), False)
        if not ok:
            return
        kind = next(k for k, v in styles.CONTENT_TYPES.items() if v == choice)
        folder = QFileDialog.getExistingDirectory(self, "Ordner für das neue Projekt (leer)", str(Path(src).parent))
        if not folder:
            return
        root = Path(folder)
        if (root / "project.json").exists():
            QMessageBox.warning(self, "Projekt", "In diesem Ordner gibt es schon ein Projekt.")
            return
        srcp = Path(src)
        agy_model = self.cb_agy.currentText().strip() or DEFAULTS["agy"]["model"]

        def work(ctx):
            md = srcp
            if srcp.suffix.lower() not in (".md", ".markdown"):
                ctx.log("Importiere Dokument – bitte das Ergebnis danach kurz prüfen.")
                md = importer.to_markdown(srcp, root / (srcp.stem + ".md"), ctx, agy_model, kind)
            return create(root, md, content_type=kind).root

        self._start(work, on_done=lambda r: self.load_project(r))

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Projektordner öffnen",
                                                  str(self.project.root if self.project else Path.home()))
        if folder:
            if not (Path(folder) / "project.json").exists():
                QMessageBox.warning(self, "Projekt", "Kein project.json in diesem Ordner.")
                return
            self.load_project(folder)

    # ---------- slots ----------
    def _thumb(self, path):
        key = (str(path), path.stat().st_mtime if path.exists() else 0)
        if key not in self._thumb_cache:
            pm = QPixmap(str(path)) if path.exists() else QPixmap()
            if pm.isNull():
                pm = QPixmap(TW, TH)
                pm.fill(QColor("#1d2027"))
            pm = pm.scaled(TW, TH, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm = pm.copy((pm.width() - TW) // 2, (pm.height() - TH) // 2, TW, TH)
            self._thumb_cache[key] = QIcon(pm)
        return self._thumb_cache[key]

    def _refresh_slots(self):
        # the manifest is authoritative here (a job may just have changed it),
        # so the editor content must not be written back
        self._shown_key = None
        row = self.slot_list.currentRow()
        self.slot_list.blockSignals(True)
        self.slot_list.clear()
        for s in self.slots:
            has = self.project.has_image(s.key)
            prompt = bool(self.project.entry(s.key).get("prompt"))
            mark = "" if has else ("  • fehlt" if prompt else "  • kein Prompt")
            item = QListWidgetItem(self._thumb(self.project.image_path(s.key)),
                                   f"{KIND_LABEL[s.kind]}{mark}\n{s.title}")
            if not has:
                item.setForeground(QColor("#e0a35a" if prompt else "#e06060"))
            self.slot_list.addItem(item)
            self._update_item_status(row=self.slot_list.count() - 1)
        self.slot_list.blockSignals(False)
        if self.slots:
            self.slot_list.setCurrentRow(min(max(row, 0), len(self.slots) - 1))
            self._show_slot(self.slot_list.currentRow())
        self._bg_timer.start()
        n_img = sum(self.project.has_image(s.key) for s in self.slots)
        self.statusBar().showMessage(f"{len(self.slots)} Bildplätze · {n_img} Bilder vorhanden")
        self._update_rejected_count()

    def _update_item_status(self, row):
        """Badge and tooltip of one list item from the manifest."""
        s, item = self.slots[row], self.slot_list.item(row)
        if not item:
            return
        has = self.project.has_image(s.key)
        status = self.project.status(s.key)
        item.setData(STATUS_ROLE, status if has else None)
        tip = s.title
        if has:
            tip += "\nStatus: " + STATUS_TEXT[status]
            qc = self.project.entry(s.key).get("qc")
            if qc and not qc["fits"]:
                tip += "\nKI: " + "\n".join("• " + x for x in qc["problems"])
        item.setToolTip(tip)

    def _update_rejected_count(self):
        if not self.project:
            return
        st = [self.project.status(s.key) for s in self.slots]
        n_x, n_flag = st.count(REJECTED), st.count(AI_FLAG)
        self.lb_rejected.setText(f"Abgelehnt: {n_x + n_flag}  (✗ {n_x} · KI-Flagge {n_flag})  ·  "
                                 f"freigegeben: ✓ {st.count(APPROVED)} · KI ✓ {st.count(AI_OK)}")

    def _current_slot(self):
        r = self.slot_list.currentRow()
        return self.slots[r] if 0 <= r < len(self.slots) else None

    def _on_row_changed(self, row):
        self._store_slot_edits()   # keep edits of the slot we are leaving
        self._show_slot(row)

    def _show_slot(self, row):
        self.preview_path = None
        self.preview_banner.hide()
        s = self._current_slot()
        if not s:
            return
        self._shown_key = s.key
        e = self.project.entry(s.key)
        note = (" · Original, im PDF abgedunkelt (Buch → Hintergrund-Helligkeit)"
                if s.kind == "background" and background.is_raw(self.project) else "")
        self.slot_title.setText(f"<b>{s.title}</b>  <span style='color:#888'>{KIND_LABEL[s.kind]} · {s.key}{note}</span>")
        self.slot_prompt.setPlainText(e.get("prompt", ""))
        self.chk_include_style.setChecked(e.get("include_style", True))
        self.sp_seed.setValue(int(e.get("seed", 1)))
        self.slot_negative.setPlainText(e.get("negative", ""))
        self.slot_negative.setPlaceholderText("Negativ-Prompt nur für dieses Bild – leer = Standard aus „Stil“: "
                                              + self.project.config.get("negative", "")[:120])
        self._show_status(s.key)
        self._set_preview(self.project.image_path(s.key))

    def _show_status(self, key):
        """Review status of the shown slot: the badge plus the user's and the AI's verdict."""
        if not self.project.has_image(key):
            self.status_icon.clear()
            self.qc_label.setText("<span style='color:#6c7480'>Noch kein Bild</span>")
            return
        status = self.project.status(key)
        self.status_icon.setPixmap(badge_pixmap(status))
        qc = self.project.entry(key).get("qc")
        if not qc:
            ai = "<span style='color:#6c7480'>KI-Prüfung: noch nicht geprüft</span>"
        elif qc["fits"]:
            ai = f"<span style='color:{STATUS_COLOR[AI_OK]}'>KI-Prüfung: passt</span>"
        else:
            ai = (f"<span style='color:{STATUS_COLOR[AI_FLAG]}'>KI-Prüfung: passt nicht – "
                  + "; ".join(qc["problems"]) + "</span>")
        review = self.project.review(key)
        if review:
            mine = f"<span style='color:{STATUS_COLOR[review]}'><b>Status: {STATUS_TEXT[review]}</b></span>"
            self.qc_label.setText(mine + " · " + ai)
        else:
            self.qc_label.setText(ai)

    def _store_slot_edits(self):
        key = getattr(self, "_shown_key", None)
        if not key or not self.project:
            return
        e = self.project.entry(key)
        e["prompt"] = self.slot_prompt.toPlainText().strip()
        e["include_style"] = self.chk_include_style.isChecked()
        e["seed"] = self.sp_seed.value()
        neg = self.slot_negative.toPlainText().strip()
        if neg:
            e["negative"] = neg
        else:
            e.pop("negative", None)

    def _on_image_saved(self, key):
        """Refresh one thumbnail (and the big preview) as soon as its image is saved."""
        for row, s in enumerate(self.slots):
            if s.key != key:
                continue
            item = self.slot_list.item(row)
            if item:
                item.setIcon(self._thumb(self.project.image_path(key)))
                item.setText(f"{KIND_LABEL[s.kind]}\n{s.title}")
                item.setForeground(QColor("#cfd5de"))
                self._update_item_status(row)
            if row == self.slot_list.currentRow() and not self.preview_path:
                self._set_preview(self.project.image_path(key))
                self._show_status(key)
            break
        self._update_rejected_count()

    # ---------- review ----------
    def _review_rows(self):
        rows = sorted({i.row() for i in self.slot_list.selectedIndexes()})
        if not rows and self._current_slot():
            rows = [self.slot_list.currentRow()]
        return rows

    def _set_review(self, verdict, rows=None):
        """Set the user's verdict (APPROVED, REJECTED or "" = remove) for the given or the selected rows."""
        if not self.project:
            return
        if self.job and self.job.isRunning():
            self.statusBar().showMessage("Bewerten geht erst, wenn der laufende Auftrag fertig ist.", 5000)
            return
        rows = [r for r in (self._review_rows() if rows is None else rows)
                if self.project.has_image(self.slots[r].key)]
        if not rows:
            return
        for r in rows:
            self.project.set_review(self.slots[r].key, verdict)
            self._update_item_status(r)
        self.project.save_manifest()
        cur = self._current_slot()
        if cur:
            self._show_status(cur.key)
        self._update_rejected_count()

    def _badge_clicked(self, row):
        """Click on the badge: unreviewed / AI → freigegeben → abgelehnt → zurückgesetzt."""
        key = self.slots[row].key
        nxt = {"": APPROVED, APPROVED: REJECTED, REJECTED: ""}[self.project.review(key)]
        self._set_review(nxt, rows=[row])

    def _list_menu(self, pos):
        if not self.project or not self.slot_list.itemAt(pos):
            return
        menu = QMenu(self)
        for text, verdict in [("✓ Freigeben", APPROVED), ("✗ Ablehnen", REJECTED),
                              ("Bewertung zurücksetzen", "")]:
            menu.addAction(text, lambda v=verdict: self._set_review(v))
        menu.exec(self.slot_list.viewport().mapToGlobal(pos))

    def _set_preview(self, path):
        pm = QPixmap(str(path)) if path and Path(path).exists() else QPixmap()
        self.preview_label.set_image(pm, "Noch kein Bild")

    # ---------- jobs ----------
    def _start(self, fn, on_done=None, label=""):
        if self.job and self.job.isRunning():
            return
        self._summary_start = self.project.config.get("summary", "") if self.project else ""
        self.job = Job(fn, self)
        self.job.log.connect(self._log)
        self.job.progress.connect(self._progress)
        self.job.done.connect(lambda r: (self._set_busy(False), on_done and on_done(r)))
        self.job.failed.connect(self._failed)
        self.job.image_saved.connect(self._on_image_saved)
        self._set_busy(True)
        if label:
            self._log(label)
        self.job.start()

    def _failed(self, msg):
        self._set_busy(False)
        self._log("FEHLER: " + msg)
        if msg != "Abgebrochen.":
            QMessageBox.warning(self, "Fehler", msg.split("\n")[0][:600])
        if self.project:
            self._refresh_slots()

    def _set_busy(self, busy):
        for b in getattr(self, "action_buttons", []):
            b.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        if not busy and self.project and self.project.config.get("summary", "") != self._summary_start:
            # a job wrote the summary (first use of an LLM function): show it before the form is collected again
            self.ed_summary.setPlainText(self.project.config.get("summary", ""))
            self._summary_start = self.project.config.get("summary", "")
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("bereit")

    def _progress(self, done, total):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat(f"{done} / {total}")

    def _log(self, text):
        self.log_box.appendPlainText(text)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _ready(self):
        if not self.project:
            QMessageBox.information(self, "Projekt", "Bitte zuerst ein Projekt öffnen oder anlegen.")
            return False
        self._collect()
        self.project.save()
        return True

    def run_plan(self):
        if self._ready():
            force = self.chk_overwrite.isChecked()
            self._start(lambda ctx: pipeline.plan(self.project, ctx, force=force),
                        on_done=lambda _: self._after_change(), label="Plane Prompts mit der KI …")

    def _confirm_all_images(self):
        if QMessageBox.question(self, "Alle Bilder neu",
                                "Alle Bilder mit neuem Seed neu generieren? Vorhandene Bilder werden ersetzt."
                                ) == QMessageBox.Yes:
            self.run_images(True)

    def run_images(self, regenerate):
        if self._ready():
            self._start(lambda ctx: pipeline.images(self.project, ctx, regenerate=regenerate,
                                                    new_seed=regenerate),
                        on_done=lambda _: self._after_change(), label="Generiere Bilder …")

    def run_suggest(self, kind, edit):
        if not self._ready():
            return
        current = {"world": self.ed_world.toPlainText().strip(), "style": self.ed_style.toPlainText().strip(),
                   "negative": self.ed_negative.toPlainText().strip(), "style wish": self.ed_wish.toPlainText().strip()}
        if edit.toPlainText().strip() and QMessageBox.question(
                self, "Vorschlagen", "Der bisherige Text dieses Felds wird durch den Vorschlag der KI ersetzt. "
                "Fortfahren?") != QMessageBox.Yes:
            return

        def done(r):
            if not r["text"]:
                self._log("Die KI lieferte keinen Vorschlag.")
                return
            edit.setPlainText(r["text"])
            self._log(f"Vorschlag ({kind}): {r['text']}" + (f"  – {r['note']}" if r["note"] else ""))
            self._collect()
            self.project.save()
        self._start(lambda ctx: pipeline.suggest_field(self.project, kind, current), on_done=done,
                    label="Die KI macht einen Vorschlag …")

    def run_compress(self, kind, edit):
        text = edit.toPlainText().strip()
        if not text:
            return
        if self._ready():
            def done(r):
                edit.setPlainText(r["compressed"].strip())
                removed = "; ".join(r.get("removed", []))
                self._log(f"Komprimiert ({kind}): {len(text)} → {len(r['compressed'])} Zeichen"
                          + (f" – entfernt: {removed}" if removed else ""))
                self._collect()
                self.project.save()
            self._start(lambda ctx: pipeline.compress_field(self.project, kind, text), on_done=done,
                        label="Die KI komprimiert …")

    def run_analyse(self):
        s = self._current_slot()
        if not (s and self._ready()):
            return
        if not self.project.has_image(s.key):
            QMessageBox.information(self, "Analysieren", "Für diesen Platz gibt es noch kein Bild.")
            return
        wish = self.ed_hint.text().strip()

        def done(r):
            if self._current_slot() is None or self._current_slot().key != s.key:
                return
            self.slot_prompt.setPlainText(r["prompt"].strip())
            self.slot_negative.setPlainText(r["negative"].strip())
            self.chk_include_style.setChecked(True)
            self.qc_label.setText(f"<span style='color:#8ab4f8'>KI: {r['analysis']}</span>")
            self._store_slot_edits()
            self.project.save_manifest()
            self._log(f"Analyse „{s.title}“: {r['analysis']}")
            self._log("Prompt und Negativ-Prompt angepasst – mit „Vorschau“ testen.")
        self._start(lambda ctx: pipeline.analyse(self.project, s.key, wish), on_done=done,
                    label=f"Die KI analysiert „{s.title}“ …")

    def run_check(self):
        if self._ready():
            fix = self.chk_qc_fix.isChecked()
            self._start(lambda ctx: pipeline.check(self.project, ctx, fix=fix),
                        on_done=lambda _: self._refresh_slots(),
                        label="Die KI prüft die Bilder …" + (" (unpassende werden neu generiert)" if fix else ""))

    def run_check_one(self):
        s = self._current_slot()
        if s and self._ready() and self.project.has_image(s.key):
            self._start(lambda ctx: pipeline.check(self.project, ctx, keys={s.key}, fix=False),
                        on_done=lambda _: self._refresh_slots(), label=f"Die KI prüft „{s.title}“ …")

    def run_build(self, langs=None):
        """Build the PDFs of all enabled languages (or only `langs`)."""
        if self._ready():
            langs = langs if isinstance(langs, list) else None
            self._start(lambda ctx: pipeline.build(self.project, ctx, langs), on_done=self._built,
                        label="Baue PDF …" if not langs or len(langs) == 1 else f"Baue {len(langs)} PDFs …")

    def run_all(self):
        if self._ready():
            def work(ctx):
                pipeline.plan(self.project, ctx)
                pipeline.images(self.project, ctx)
                return pipeline.build(self.project, ctx)
            self._start(work, on_done=self._built, label="Kompletter Durchlauf …")

    def _built(self, results):
        self._after_change()
        ok = [r for r in results if r.path]
        if len(results) == 1:
            r = results[0]
            self.statusBar().showMessage(f"PDF fertig: {r.pages} Seiten", 8000)
            if QMessageBox.question(self, "PDF fertig", f"{r.path.name} ({r.pages} Seiten) öffnen?") == QMessageBox.Yes:
                self._open_file(r.path)
            return
        lines = [f"{lang_label(r.lang)}: {r.path.name} ({r.pages} Seiten)" if r.path
                 else f"{lang_label(r.lang)}: FEHLER – {r.error}" for r in results]
        self.statusBar().showMessage(f"{len(ok)} von {len(results)} PDFs fertig", 8000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information if len(ok) == len(results) else QMessageBox.Warning)
        box.setWindowTitle("PDFs fertig")
        box.setText("\n".join(lines))
        b_folder = box.addButton("Ordner öffnen", QMessageBox.AcceptRole)
        box.addButton("Schließen", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_folder:
            self._open_file(ok[0].path.parent)

    def _after_change(self):
        self._fill_form()
        self._reload_slots()

    def run_refine(self):
        wish = self.ed_wish.toPlainText().strip()
        if not wish:
            QMessageBox.information(self, "Stilwunsch", "Bitte zuerst einen Stilwunsch eintragen.")
            return
        if self._ready():
            def done(r):
                self.ed_style.setPlainText(r["style"])
                self.col_accent.set_color(r["accent"])
                self.col_accent2.set_color(r["accent2"])
                self.cb_preset.setCurrentText(styles.CUSTOM)
                self.lb_note.setText(r.get("note", ""))
                self._apply_accent(r["accent"])
                self._log(f"Neuer Stil: {r['style']}")
                self._collect()
                self.project.save()
            self._start(lambda ctx: pipeline.refine_style(self.project, wish), on_done=done,
                        label="Die KI verfeinert den Stilwunsch …")

    def run_cover_auto(self):
        if not self._ready():
            return
        fields = [(self.ed_title, "title"), (self.ed_subtitle, "subtitle"), (self.ed_kicker, "kicker"),
                  (self.ed_tagline, "tagline"), (self.ed_running, "running_title"),
                  (self.ed_chlabel, "chapter_label")]
        current = {key: ed.text().strip() for ed, key in fields}
        if any(current.values()) and QMessageBox.question(
                self, "Cover & Kopfzeile", "Die bisherigen Einträge werden durch die Vorschläge der KI "
                "ersetzt. Fortfahren?") != QMessageBox.Yes:
            return

        def done(r):
            for ed, key in fields:
                ed.setText(r.get(key, ""))
            self._log("Cover & Kopfzeile ausgefüllt: " + " | ".join(r[k] for k in pipeline.COVER_KEYS if r.get(k)))
            self._collect()
            self.project.save()
        self._start(lambda ctx: pipeline.suggest_cover(self.project, current), on_done=done,
                    label="Die KI füllt Cover & Kopfzeile aus …")

    def run_palettes(self):
        if not self._ready():
            return

        def done(palettes):
            dlg = PaletteDialog(palettes, self)
            if dlg.exec() != QDialog.Accepted or not dlg.choice:
                return
            pal = dlg.choice
            for button, key in [(self.col_bg, "bg"), (self.col_ink, "ink"), (self.col_accent, "accent"),
                                (self.col_accent2, "accent2")]:
                button.set_color(pal[key])
            self._apply_accent(pal["accent"])
            self._collect()
            self.project.save()
            self._log(f"Farbpalette „{pal['name']}“ übernommen: {pal['bg']} · {pal['ink']} · "
                      f"{pal['accent']} · {pal['accent2']}" + (f" – {pal['note']}" if pal["note"] else ""))
        self._start(lambda ctx: pipeline.suggest_palettes(self.project), on_done=done,
                    label="Die KI schlägt Farbpaletten vor …")

    def _kind(self):
        return self.cb_kind.currentData() or "rulebook"

    def _refresh_kind_ui(self):
        """Presets, labels and hints that depend on the content type (PnP rulebook / other document)."""
        doc = self._kind() == "document"
        keep = self.cb_preset.currentText()
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        self.cb_preset.addItems([styles.CUSTOM] + list(styles.presets_for(self._kind())))
        self.cb_preset.setCurrentText(keep if keep in styles.presets_for(self._kind()) else styles.CUSTOM)
        self.cb_preset.blockSignals(False)
        self.world_head.label.setText("Thema (von der KI erkannt, für alle Prompts)" if doc
                                      else "Welt (von der KI erkannt, für alle Prompts)")
        self.ed_wish.setPlaceholderText(
            "z.B. „technische Blaupause“ oder „freundliche Flat-Illustrationen in Blau“" if doc else
            "z.B. „soll aussehen wie One Piece“ oder „düster wie Dark Souls, aber farbig“")

    def _kind_changed(self):
        self._refresh_kind_ui()
        if self.project:
            self.project.config["content_type"] = self._kind()
            self._log("Inhalt: " + styles.CONTENT_TYPES[self._kind()] +
                      " – neue Prompts und Bildbewertungen der KI richten sich danach "
                      "(vorhandene Prompts bleiben; zum Ersetzen „Prompts planen“ mit „vorhandene überschreiben“).")

    def _apply_preset(self):
        name = self.cb_preset.currentText()
        if name in styles.presets_for(self._kind()):
            p = styles.presets_for(self._kind())[name]
            self.ed_style.setPlainText(p["style"])
            self.col_accent.set_color(p["accent"])
            self.col_accent2.set_color(p["accent2"])
            self._apply_accent(p["accent"])

    def run_replan(self):
        s = self._current_slot()
        if s and self._ready():
            hint = self.ed_hint.text().strip()
            self._start(lambda ctx: pipeline.replan_one(self.project, s.key, hint),
                        on_done=lambda p: (self.slot_prompt.setPlainText(p), self.chk_include_style.setChecked(True)),
                        label=f"Die KI schreibt neuen Prompt für „{s.title}“ …")

    def run_preview(self):
        s = self._current_slot()
        if s and self._ready():
            prompt, seed = self.slot_prompt.toPlainText(), self.sp_seed.value()

            def done(path):
                self.preview_path = path
                self._set_preview(path)
                self.preview_banner.show()
            self._start(lambda ctx: pipeline.preview(self.project, s.key, ctx, prompt=prompt, seed=seed),
                        on_done=done, label=f"Vorschau für „{s.title}“ …")

    def accept_preview(self):
        s = self._current_slot()
        if not (s and self.preview_path and Path(self.preview_path).exists()):
            QMessageBox.information(self, "Übernehmen", "Erst eine Vorschau erzeugen.")
            return
        self._collect()
        shutil.copy(self.preview_path, self.project.image_path(s.key))
        self.project.entry(s.key)["raw"] = True
        self.project.reset_review(s.key)
        self.project.save()
        self._log(f"Übernommen: {s.title}")
        self._refresh_slots()

    def run_remove(self):
        """Remove the selected images (or the current one) so they can be generated again."""
        rows = sorted({i.row() for i in self.slot_list.selectedIndexes()})
        if not rows and self._current_slot():
            rows = [self.slot_list.currentRow()]
        slots = [self.slots[r] for r in rows if self.project.has_image(self.slots[r].key)]
        if not slots:
            QMessageBox.information(self, "Bild entfernen", "Für die gewählten Plätze gibt es kein Bild.")
            return
        names = "\n".join("• " + s.title for s in slots[:8]) + (f"\n… und {len(slots) - 8} weitere" if len(slots) > 8 else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Bild entfernen")
        box.setText(f"{len(slots)} Bild(er) entfernen?\n\n{names}")
        box.setInformativeText("Die Prompts bleiben. Die Bilder landen in .build/removed/ und bekommen beim "
                               "nächsten Mal einen neuen Seed.")
        b_remove = box.addButton("Entfernen", QMessageBox.AcceptRole)
        b_regen = box.addButton("Entfernen und neu generieren", QMessageBox.AcceptRole)
        box.addButton("Abbrechen", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_remove, b_regen):
            return
        self._store_slot_edits()
        removed = pipeline.remove_images(self.project, [s.key for s in slots])
        self._log(f"{len(removed)} Bild(er) entfernt – „Fehlende Bilder“ erzeugt sie neu.")
        self._refresh_slots()
        if clicked is b_regen and self._ready():
            self.run_images(False)

    def run_redo(self, new_prompts):
        """Make every rejected image (red X, AI flag) again: new seed, optionally a new prompt first."""
        if not self.project:
            return
        slots = [s for s in self.slots if self.project.status(s.key) in NEEDS_REDO]
        title = "Neue Prompts + neue Bilder" if new_prompts else "Neue Bilder, neue Seeds"
        if not slots:
            QMessageBox.information(self, title, "Keine abgelehnten Bilder (rotes ✗ oder KI-Flagge).")
            return
        names = "\n".join("• " + s.title for s in slots[:8]) + (f"\n… und {len(slots) - 8} weitere" if len(slots) > 8 else "")
        what = ("neue Prompts schreiben lassen und mit neuem Seed neu generieren" if new_prompts
                else "mit gleichem Prompt und neuem Seed neu generieren")
        qc = " Danach prüft die KI jedes neue Bild." if self.chk_qc.isChecked() else ""
        if QMessageBox.question(self, title, f"{len(slots)} abgelehnte(s) Bild(er) {what}?{qc}\n\n{names}"
                                ) != QMessageBox.Yes:
            return
        if self._ready():
            self._start(lambda ctx: pipeline.redo_rejected(self.project, ctx, new_prompts=new_prompts),
                        on_done=lambda _: self._after_change(), label=title + " …")

    def run_regen_one(self):
        s = self._current_slot()
        if s and self._ready():
            self._start(lambda ctx: pipeline.images(self.project, ctx, keys={s.key}, regenerate=True),
                        on_done=lambda _: self._refresh_slots(), label=f"Generiere „{s.title}“ …")

    # ---------- languages ----------
    def _fill_langs(self, select=None):
        """Language list of the tab (and the filler switch above the image list) from the project."""
        self.lang_list.blockSignals(True)
        self.lang_list.clear()
        codes = self.project.languages() if self.project else []
        for i, code in enumerate(codes):
            item = QListWidgetItem(lang_label(code) + ("   · Hauptsprache" if i == 0 else ""))
            if i:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if self.project.translation(code).get("enabled", True)
                                   else Qt.Unchecked)
            self.lang_list.addItem(item)
        self.lang_list.blockSignals(False)
        row = codes.index(select) if select in codes else 0
        if codes:
            self.lang_list.blockSignals(True)
            self.lang_list.setCurrentRow(row)
            self.lang_list.blockSignals(False)
        self._show_lang(row)
        self.cb_view_lang.blockSignals(True)
        self.cb_view_lang.clear()
        for i, code in enumerate(codes):
            self.cb_view_lang.addItem(lang_label(code) + (" (Hauptsprache)" if i == 0 else ""), code)
        self.cb_view_lang.setCurrentIndex(max(0, self.cb_view_lang.findData(self._view_lang)))
        self.cb_view_lang.blockSignals(False)
        self.view_row.setVisible(len(codes) > 1)

    def _view_changed(self):
        self._store_slot_edits()
        self._view_lang = self.cb_view_lang.currentData()
        self._reload_slots()

    def _on_lang_row(self, row):
        self._store_lang_edits()
        self._show_lang(row)

    def _on_lang_checked(self, item):
        row = self.lang_list.row(item)
        codes = self.project.languages() if self.project else []
        if 0 < row < len(codes):
            self.project.translation(codes[row])["enabled"] = item.checkState() == Qt.Checked

    def _show_lang(self, row):
        codes = self.project.languages() if self.project else []
        main = row <= 0
        self._lang_shown = None if main or row >= len(codes) else codes[row]
        self.lang_detail.setVisible(self._lang_shown is not None)
        self.lb_lang_main.setVisible(self._lang_shown is None)
        self.btn_lang_remove.setEnabled(self._lang_shown is not None and not (self.job and self.job.isRunning()))
        if not self._lang_shown:
            return
        t = self.project.translation(self._lang_shown)
        self.ed_l_source.setText(t.get("source", ""))
        self.ed_l_output.setText(t.get("output", ""))
        for key, ed in self.l_fields.items():
            ed.setText(t.get(key, ""))
        self.cb_l_level.setCurrentIndex(int(t.get("chapter_level") or 0))
        self.lb_l_status.setText(self._lang_status(self._lang_shown))

    def _store_lang_edits(self):
        code = self._lang_shown
        t = self.project.translation(code) if (code and self.project) else None
        if not t:
            return
        t["source"], t["output"] = self.ed_l_source.text().strip(), self.ed_l_output.text().strip()
        for key, ed in self.l_fields.items():
            t[key] = ed.text().strip()
        t["chapter_level"] = self.cb_l_level.currentIndex()

    def _lang_status(self, code):
        """Do the chapters and sections of the translation line up with the main language's?"""
        try:
            issues = self.project.alignment_issues(code)
            shared = sum(1 for s in self.project.slots() if s.kind != "filler")
        except Exception as e:  # noqa: BLE001
            return f"<span style='color:#e06060'>{html.escape(str(e))}</span>"
        if not issues:
            return (f"<span style='color:#4fd18b'>✓ Gleiche Gliederung wie die Hauptsprache – "
                    f"{shared} Bildplätze werden geteilt.</span>")
        return ("<span style='color:#e0a35a'>Die Gliederung weicht von der Hauptsprache ab. Bilder werden nach "
                "Reihenfolge zugeordnet, Überzähliges bekommt keins:<br>"
                + "<br>".join("• " + html.escape(i) for i in issues[:6]) + "</span>")

    def add_language(self):
        if not self.project:
            QMessageBox.information(self, "Sprachen", "Bitte zuerst ein Projekt öffnen oder anlegen.")
            return
        src, _ = QFileDialog.getOpenFileName(
            self, "Übersetzung wählen (Markdown, Word, PDF, Text)",
            str(self.project.source_path.parent if self.project.source_path else Path.home()), importer.SOURCE_FILTER)
        if not src:
            return
        dlg = AddLanguageDialog(self, Path(src), importer.sniff_language(src), self.project)
        if dlg.exec() != QDialog.Accepted or not self._ready():
            return
        code, keep = dlg.code(), dlg.keep_images()
        model = self.cb_agy.currentText().strip() or DEFAULTS["agy"]["model"]
        kind = self._kind()

        def work(ctx):
            if Path(src).suffix.lower() not in (".md", ".markdown"):
                ctx.log("Importiere Übersetzung – bitte das Ergebnis danach kurz prüfen.")
            importer.import_translation(self.project, code, src, ctx, model, kind, keep_images=keep)
            return code

        def done(code):
            self._fill_langs(select=code)
            self._reload_slots()
            self._log(f"Sprache {lang_label(code)} hinzugefügt. Titel und Kopfzeile lassen sich mit „Texte "
                      "automatisch ausfüllen“ übersetzen.")
        self._start(work, on_done=done, label=f"Füge Übersetzung ({code}) hinzu …")

    def remove_language(self):
        code = self._lang_shown
        if not (code and self.project):
            return
        if QMessageBox.question(
                self, "Sprache entfernen", f"{lang_label(code)} aus dem Projekt nehmen?\n\nDas Markdown bleibt im "
                "Ordner; die Füllbilder dieser Sprache wandern nach .build/removed/.") != QMessageBox.Yes:
            return
        self._collect()
        self.project.remove_translation(code)
        self.project.save()
        self._lang_shown = None
        if self._view_lang == code:
            self._view_lang = None
        self._log(f"Sprache {code} entfernt.")
        self._fill_langs()
        self._reload_slots()

    def strip_language_images(self):
        code = self._lang_shown
        src = self.project.source_for(code) if (code and self.project) else None
        if not (src and src.exists()):
            QMessageBox.information(self, "Bilder entfernen", "Das Markdown dieser Übersetzung wurde nicht gefunden.")
            return
        self._collect()
        images = importer.list_images(src.read_text(encoding="utf-8"))
        if not images:
            QMessageBox.information(self, "Bilder entfernen", "Im Markdown dieser Übersetzung gibt es keine Bild-Verweise.")
            return
        dlg = StripImagesDialog(self, src, images)
        if dlg.exec() != QDialog.Accepted:
            return
        n = importer.strip_translation_images(self.project, code, dlg.indexes)
        self._log(f"{n} Bild-Verweis(e) aus {src.name} entfernt (Sicherung: *.vor-bildentfernung.md).")
        self.lb_l_status.setText(self._lang_status(code))
        self._reload_slots()

    def run_lang_cover_auto(self):
        code = self._lang_shown
        if not (code and self._ready()):
            return
        current = {key: ed.text().strip() for key, ed in self.l_fields.items()}
        if any(current.values()) and QMessageBox.question(
                self, "Texte ausfüllen", "Die bisherigen Einträge dieser Sprache werden durch die Vorschläge der "
                "KI ersetzt. Fortfahren?") != QMessageBox.Yes:
            return

        def done(r):
            if self._lang_shown != code:
                return
            for key, ed in self.l_fields.items():
                ed.setText(r.get(key, ""))
            self._log(f"Texte ({code}): " + " | ".join(r[k] for k in pipeline.COVER_KEYS if r.get(k)))
            self._collect()
            self.project.save()
        self._start(lambda ctx: pipeline.suggest_cover(self.project, current, code), on_done=done,
                    label=f"Die KI füllt Cover & Kopfzeile ({code}) aus …")

    def _pick_lang_source(self):
        p, _ = QFileDialog.getOpenFileName(self, "Markdown der Übersetzung", self.ed_l_source.text(),
                                           "Markdown (*.md *.markdown)")
        if p:
            self.ed_l_source.setText(p)

    def _pick_lang_output(self):
        p, _ = QFileDialog.getSaveFileName(self, "PDF speichern unter", self.ed_l_output.text(), "PDF (*.pdf)")
        if p:
            self.ed_l_output.setText(p)

    def _open_pdf(self):
        if not self.project:
            self._open_file(None)
            return
        codes = self.project.languages()
        if len(codes) == 1:
            self._open_file(self.project.output_path)
            return
        menu = QMenu(self)
        for code in codes:
            menu.addAction(lang_label(code), lambda c=code: self._open_file(self.project.output_for(c)))
        menu.exec(QCursor.pos())

    # ---------- engine helpers ----------
    def _connect_comfy(self, quiet=False):
        client = comfy.Comfy(self.ed_url.text().strip() or "http://127.0.0.1:8188")
        if not client.alive():
            self.lb_comfy.setText("✗ nicht erreichbar – ComfyUI in Pinokio starten")
            if not quiet:
                QMessageBox.warning(self, "ComfyUI", "ComfyUI ist nicht erreichbar.")
            return
        models = client.models()
        for cb, key in [(self.cb_unet, "unet"), (self.cb_clip, "clip"), (self.cb_cliptype, "clip_type"),
                        (self.cb_vae, "vae"), (self.cb_ckpt, "checkpoint"), (self.cb_sampler, "sampler"),
                        (self.cb_scheduler, "scheduler")]:
            current = cb.currentText()
            cb.clear()
            cb.addItems(models[key])
            cb.setCurrentText(current)
        self.comfy_lists = models
        self.lb_comfy.setText(f"✓ verbunden · {len(models['unet'])} Diffusion-Modelle, "
                              f"{len(models['checkpoint'])} Checkpoints")
        self._fill_model_picker()

    # ---------- model picker ----------
    def _fill_model_picker(self):
        cc = self.project.config["comfy"] if self.project else {}
        models_dir = cc.get("models_dir") or models.default_models_dir()
        self.model_infos = models.scan(models_dir, getattr(self, "comfy_lists", {}))
        self.models_dir = models_dir
        self.cb_model.clear()
        for m in self.model_infos:
            prefix = "" if m.family.usable else "✗ "
            if m.loader == "unet-missing":
                prefix = "⚠ "
            self.cb_model.addItem(prefix + m.label)
            if not m.family.usable:
                item = self.cb_model.model().item(self.cb_model.count() - 1)
                item.setForeground(QColor("#6c7480"))
        self._select_model_in_picker()

    def _select_model_in_picker(self):
        if not getattr(self, "model_infos", None):
            return
        kind = self.cb_workflow.currentData()
        name = self.cb_unet.currentText() if kind == "anima" else self.cb_ckpt.currentText()
        for i, m in enumerate(self.model_infos):
            if m.name == name and (m.loader == "checkpoint") == (kind == "checkpoint"):
                self.cb_model.setCurrentIndex(i)
                self._show_model_info(m)
                return
        self.lb_model.setText("<span style='color:#8a93a0'>Kein erkanntes Modell gewählt.</span>")

    def _current_model_info(self):
        i = self.cb_model.currentIndex()
        return self.model_infos[i] if 0 <= i < len(getattr(self, "model_infos", [])) else None

    def _show_model_info(self, m):
        fam = m.family
        lines = [f"<b>{fam.label}</b>{' · Turbo/Lightning' if m.fast else ''}"]
        if fam.note:
            lines.append(fam.note)
        if fam.usable:
            missing = models.missing_components(m, getattr(self, "comfy_lists", {}))
            for fn, url in missing:
                lines.append(f"<span style='color:#e06060'>Fehlt: {fn}</span>"
                             + (f" – <a href='{url}' style='color:#8ab4f8'>herunterladen</a> nach "
                                f"models/{'vae' if 'vae' in fn else 'text_encoders'}/" if url else ""))
            if m.loader == "unet-missing":
                lines.append("<span style='color:#e0a35a'>Liegt nur in checkpoints/. Es ist aber ein reines "
                             "Diffusionsmodell und muss nach diffusion_models/ verlinkt werden.</span>")
            if models.remembered(m.name):
                lines.append("<span style='color:#8a93a0'>Deine letzten Einstellungen für dieses Modell sind gespeichert.</span>")
        color = "#cfd5de" if fam.usable else "#e06060"
        self.lb_model.setText(f"<span style='color:{color}'>" + "<br>".join(lines) + "</span>")
        self.lb_model.setOpenExternalLinks(True)
        self.btn_link.setVisible(m.loader == "unet-missing" and bool(self.models_dir))
        self.btn_recommended.setEnabled(fam.usable and bool(fam.settings))

    def _on_model_selected(self, index):
        m = self._current_model_info()
        if not m:
            return
        self._show_model_info(m)
        if not m.family.usable:
            return
        self._apply_model_settings()

    def _apply_model_settings(self, force_recommended=False):
        m = self._current_model_info()
        if not m or not m.family.usable:
            return
        settings = dict(models.recommended(m))
        mem = None if force_recommended else models.remembered(m.name)
        if mem:
            settings.update({k: v for k, v in mem.items() if v not in (None, "")})
        kind = settings.get("workflow") or ("checkpoint" if m.loader == "checkpoint" else "anima")
        self.cb_workflow.setCurrentIndex(max(0, self.cb_workflow.findData(kind)))
        if kind == "checkpoint":
            self.cb_ckpt.setCurrentText(m.name)
        else:
            self.cb_unet.setCurrentText(m.name)
        for cb, key in [(self.cb_clip, "clip"), (self.cb_cliptype, "clip_type"), (self.cb_vae, "vae"),
                        (self.cb_sampler, "sampler"), (self.cb_scheduler, "scheduler")]:
            if settings.get(key):
                cb.setCurrentText(settings[key])
        if settings.get("steps"):
            self.sp_steps.setValue(int(settings["steps"]))
        if settings.get("cfg") is not None:
            self.sp_cfg.setValue(float(settings["cfg"]))
        self._update_engine_rows()
        self._log(f"Bildmodell: {m.name} ({m.family.label}) – "
                  + ("gespeicherte Einstellungen" if mem else "empfohlene Einstellungen") + " übernommen.")

    def _link_model(self):
        m = self._current_model_info()
        if not m:
            return
        try:
            models.link_into_diffusion_models(self.models_dir, m.name)
        except OSError as e:
            QMessageBox.warning(self, "Verlinken", f"Verlinken fehlgeschlagen: {e}")
            return
        self._log(f"Verlinkt: diffusion_models/{m.name} → checkpoints/{m.name}")
        self._connect_comfy(quiet=True)
        for i, info in enumerate(self.model_infos):
            if info.name == m.name and info.loader == "unet":
                self.cb_model.setCurrentIndex(i)
                self._on_model_selected(i)
                break

    def _llm_changed(self):
        """Swap the model box between the agy and the Claude model of the project."""
        if not self.project:
            return
        c = self.project.config
        c["claude" if self._llm_shown == "claude" else "agy"]["model"] = self.cb_agy.currentText().strip()
        self._llm_shown = self.cb_llm.currentData()
        c["llm"] = self._llm_shown
        planner.configure(c)
        self.cb_agy.clear()
        self.cb_agy.addItems(planner.CLAUDE_MODELS if self._llm_shown == "claude" else [])
        self.cb_agy.setCurrentText(c["claude" if self._llm_shown == "claude" else "agy"].get("model", ""))

    def _load_agy_models(self):
        current = self.cb_agy.currentText()
        models = planner.list_models()
        if not models:
            QMessageBox.warning(self, "Modelle", "Die Modelle konnten nicht geladen werden.")
            return
        self.cb_agy.clear()
        self.cb_agy.addItems(models)
        self.cb_agy.setCurrentText(current)

    def _workflow_changed(self):
        kind = self.cb_workflow.currentData()
        d = WORKFLOW_DEFAULTS.get(kind)
        if d and QMessageBox.question(self, "Workflow", "Allgemeine Sampler-Einstellungen für diesen "
                                      "Workflow übernehmen? (Besser: oben ein Bildmodell wählen.)") == QMessageBox.Yes:
            self.sp_cfg.setValue(d["cfg"])
            self.sp_steps.setValue(d["steps"])
            self.cb_sampler.setCurrentText(d["sampler"])
            self.cb_scheduler.setCurrentText(d["scheduler"])
        self._update_engine_rows()

    def _engine_changed(self):
        eng = self.cb_engine.currentData()
        f = self.engine_form
        for i in self.comfy_rows:
            f.setRowVisible(i, eng == "comfy")
        f.setRowVisible(self.lb_gem, eng != "comfy")
        f.setRowVisible(self.chk_quota_wait, eng == "agy")
        self.lb_gem.setText(self.gem_text_agy if eng == "agy" else
                            "Direkter Aufruf der Gemini Interactions API. Braucht GEMINI_API_KEY (oder ~/gemini.key) "
                            "und `pip install google-genai`. Seitenverhältnis wie bei agy, kein Seed.")
        for w in self.gem_widgets:
            f.setRowVisible(w, eng == "gemini")
        for w in self.qc_comfy_widgets:
            f.setRowVisible(w, eng == "comfy")
        self.chk_qc_fix.setText("Unpassende Bilder korrigieren" if eng == "comfy"
                                else "Unpassende Bilder bearbeiten (höchstens 2 Bearbeitungen)")
        if eng == "comfy":
            self._update_engine_rows()
            self._connect_comfy(quiet=True)

    def _update_engine_rows(self):
        kind = self.cb_workflow.currentData()
        for k, rows in self.engine_rows.items():
            for _, widget in rows:
                self.engine_form.setRowVisible(widget, k == kind)
        for _, widget in self.sampler_rows:
            self.engine_form.setRowVisible(widget, kind != "custom")

    # ---------- misc ----------
    def _pick_source(self):
        """Markdown is taken as it is; Word, PDF and text are converted like when creating a project."""
        p, _ = QFileDialog.getOpenFileName(self, "Quelle (Markdown, Word, PDF, Text)", self.ed_source.text(),
                                           importer.SOURCE_FILTER)
        if not p:
            return
        if Path(p).suffix.lower() in (".md", ".markdown"):
            self._set_source(p)
            return
        if not self.project:
            QMessageBox.information(self, "Quelle", "Bitte zuerst ein Projekt öffnen oder anlegen.")
            return
        srcp = Path(p)
        dest = self.project.root / (srcp.stem + ".md")
        if dest.exists() and QMessageBox.question(
                self, "Quelle", f"{dest.name} gibt es im Projektordner schon und wird durch den Import "
                "überschrieben. Fortfahren?") != QMessageBox.Yes:
            return
        if not self._ready():
            return
        model = self.cb_agy.currentText().strip() or DEFAULTS["agy"]["model"]
        kind = self._kind()

        def work(ctx):
            ctx.log("Importiere Dokument – bitte das Ergebnis danach kurz prüfen.")
            return importer.to_markdown(srcp, dest, ctx, model, kind)
        self._start(work, on_done=lambda md: self._set_source(str(md)))

    def _set_source(self, path):
        """A different source is a different book: the stored summary no longer applies."""
        if self.project and Path(path).resolve() != (self.project.source_path or Path()).resolve():
            self.ed_summary.clear()
        self.ed_source.setText(path)
        if self.project:
            self._collect()
            self.project.save()
            self._reload_slots()

    def _pick_output(self):
        p, _ = QFileDialog.getSaveFileName(self, "PDF speichern unter", self.ed_output.text(), "PDF (*.pdf)")
        if p:
            self.ed_output.setText(p)

    def _pick_workflow(self):
        p, _ = QFileDialog.getOpenFileName(self, "ComfyUI API-Workflow", self.ed_custom.text(), "JSON (*.json)")
        if p:
            self.ed_custom.setText(p)

    def _open_file(self, path):
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "Öffnen", f"Nicht vorhanden: {path}")

    def _apply_accent(self, accent):
        QApplication.instance().setStyleSheet(stylesheet(accent))

    def closeEvent(self, ev):
        if self.job and self.job.isRunning():
            if QMessageBox.question(self, "Beenden", "Ein Auftrag läuft noch. Trotzdem beenden?") != QMessageBox.Yes:
                ev.ignore()
                return
            self.job.cancel()
            self.job.wait(3000)
        if self.project:
            self._collect()
            self.project.save()
        ev.accept()


# ---------------------------------------------------------------- theme
def dark_palette():
    p = QPalette()
    base, window, text = QColor("#15171c"), QColor("#1c1f26"), QColor("#e6eaf0")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#262a33"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, window)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor("#6c7480"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5c6370"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#5c6370"))
    return p


def stylesheet(accent):
    return f"""
    QWidget {{ font-size: 10pt; }}
    QToolBar {{ background:#15171c; border:none; padding:4px; spacing:4px; }}
    QToolBar QToolButton {{ padding:5px 10px; border-radius:5px; }}
    QToolBar QToolButton:hover {{ background:#2a2f39; }}
    QTabWidget::pane {{ border:1px solid #2a2f39; border-radius:6px; top:-1px; }}
    QTabBar::tab {{ padding:7px 16px; background:#1c1f26; border:1px solid #2a2f39; border-bottom:none;
                    border-top-left-radius:6px; border-top-right-radius:6px; margin-right:2px; color:#9aa3b0; }}
    QTabBar::tab:selected {{ background:#22262e; color:{accent}; }}
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background:#15171c; border:1px solid #2e333d; border-radius:5px; padding:4px 6px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color:{accent}; }}
    QPushButton {{ background:#2a2f39; border:1px solid #363c48; border-radius:6px; padding:6px 12px; }}
    QPushButton:hover {{ border-color:{accent}; }}
    QPushButton:disabled {{ color:#5c6370; border-color:#2a2f39; }}
    QPushButton#small {{ padding:2px 8px; font-size:9pt; }}
    QPushButton#primary {{ background:{accent}; color:#0b0c10; font-weight:600; border:none; }}
    QPushButton#primary:disabled {{ background:#2a2f39; color:#5c6370; }}
    QListWidget {{ background:#111318; border:1px solid #2a2f39; border-radius:6px; }}
    QListWidget::item {{ color:#cfd5de; border-radius:6px; border:1px solid transparent; }}
    QListWidget::item:selected {{ background:#22262e; border:1px solid {accent}; color:#fff; }}
    QCheckBox::indicator {{ width:15px; height:15px; border:1px solid #4a5160; border-radius:3px; background:#15171c; }}
    QCheckBox::indicator:checked {{ background:{accent}; border-color:{accent}; }}
    QLabel#section {{ color:{accent}; font-weight:600; padding-top:10px; text-transform:uppercase; }}
    QLabel#dim {{ color:#8a93a0; }}
    QLabel#preview {{ background:#0b0c10; border:1px solid #2a2f39; border-radius:6px; color:#5c6370; }}
    QLabel#banner {{ background:{accent}; color:#0b0c10; font-weight:600; border-radius:4px; padding:3px; }}
    QLabel#slotTitle {{ font-size:11pt; padding-top:4px; }}
    QPlainTextEdit#log {{ font-family:monospace; font-size:9pt; color:#aab1bd; }}
    QProgressBar {{ border:1px solid #2e333d; border-radius:5px; background:#15171c; text-align:center; height:18px; }}
    QProgressBar::chunk {{ background:{accent}; border-radius:4px; }}
    QSplitter::handle {{ background:#1c1f26; }}
    QScrollArea {{ background:transparent; }}
    """


def run(project_dir=None):
    app = QApplication(sys.argv)
    app.setApplicationName("Rulebook PDF")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(stylesheet("#2fe6ff"))
    f = QFont()
    f.setPointSize(10)
    app.setFont(f)
    w = MainWindow(project_dir)
    w.show()
    return app.exec()
