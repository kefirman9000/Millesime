"""
Millésime: drop memory cards, old drives or folders on the window and it copies everything into
one tidy library on your new drive: Photos and Videos by date, Music by artist and album,
Documents by type. Duplicates are skipped, nothing is ever overwritten, and your cards are only
read, never changed.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QByteArray, QLockFile, QModelIndex, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QSystemTrayIcon,
                               QSpinBox, QTableView, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget, QMenu, QDialog, QDialogButtonBox, QPlainTextEdit)
from PySide6.QtGui import QKeySequence, QShortcut

import engine
import updater

APP_NAME = engine.APP_NAME
SPONSOR_URL = "https://github.com/sponsors/kefirman9000"


ICON_FILE = engine.APP_DIR / "icon.png"


def open_path(p: Path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


# --------------------------------------------------------------------------- #
# Light / dark
# --------------------------------------------------------------------------- #
def _system_is_dark(app) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def apply_theme(app, mode: str):
    """mode: 'system' (follow Windows), 'light' or 'dark'."""
    dark = _system_is_dark(app) if mode == "system" else mode == "dark"
    if not dark:
        app.setPalette(app.style().standardPalette())
        return
    p = QPalette()
    base, window, text = QColor(32, 33, 36), QColor(43, 44, 48), QColor(232, 232, 232)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(40, 41, 45))
    p.setColor(QPalette.ToolTipBase, window)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(52, 53, 58))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 90, 90))
    p.setColor(QPalette.Link, QColor(110, 170, 255))
    p.setColor(QPalette.Highlight, QColor(43, 123, 214))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(120, 120, 120))
    app.setPalette(p)


# --------------------------------------------------------------------------- #
# Background work
# --------------------------------------------------------------------------- #
class ScanWorker(QThread):
    found = Signal(list)          # batches of Items, already given their folders
    status = Signal(str)
    sources = Signal(list)        # one summary per dropped card / folder / file

    def __init__(self, paths, settings, dest, aliases):
        super().__init__()
        self.paths, self.settings, self.dest = paths, dict(settings), dest
        self.aliases = dict(aliases)
        self.stop = False

    def run(self):
        albums: dict = {}
        store = engine.CardStore(self.dest)
        summaries = []
        batch, n, last = [], 0, time.monotonic()
        try:
            for p in self.paths:
                if self.stop:
                    break
                stats: dict = {}
                info = {"path": str(p), "name": "", "kinds": {}, "known": 0, "new": 0, "last": None, "tiny": 0}
                summaries.append(info)
                for it in engine.gather([p], self.settings, self.dest, lambda: self.stop, stats):
                    if self.stop:
                        break
                    it.source_id, name, it.source_rel = engine.source_key(it.src)
                    info["name"] = info["name"] or name
                    if info["last"] is None:
                        info["last"] = store.last_backup(it.source_id) or False
                    rec = store.lookup(it)
                    if rec:
                        store.apply(it, rec)     # known file: its date is remembered, nothing is read from it
                        it.note = "Backed up before"
                        info["known"] += 1
                    else:
                        engine.read_info(it, self.settings)
                        info["new"] += 1
                    k = engine.KIND_GROUP[it.kind]
                    info["kinds"][k] = info["kinds"].get(k, 0) + 1
                    batch.append(it)
                    n += 1
                    if time.monotonic() - last > 0.25:
                        engine.plan(batch, self.settings, albums, self.aliases)
                        self.found.emit(batch)
                        batch, last = [], time.monotonic()
                        self.status.emit(f"Reading files… {n:,} found")
                info["tiny"] = stats.get("tiny", 0)
        except Exception as e:
            engine.log_error("Scanning", e)
            self.status.emit(f"Problem while reading the files: {e}")
        if batch:
            engine.plan(batch, self.settings, albums, self.aliases)
            self.found.emit(batch)
        store.close()
        self.sources.emit(summaries)


class CopyWorker(QThread):
    progress = Signal(int, int, object)      # done, total, bytes done
    status = Signal(str)
    row_done = Signal(int)
    file_progress = Signal(str)              # inside a big file: "MVI_0042.MOV · 3.1 of 12.4 GB"

    RETRIES = 2                              # a flaky card reader often works on the second try

    def __init__(self, items, rows, dest, more_coming):
        super().__init__()
        self.items, self.dest = items, dest
        self.queue: deque[int] = deque(rows)   # files dropped while copying are added here
        self.more_coming = more_coming
        self.stop = False
        self.paused = False
        self.error = ""
        self.log_path = None
        self.cancelled = False
        self.activity = "Starting"
        self.last_activity = time.monotonic()
        self.gone_drives: set[str] = set()
        self.leftovers = 0

    def _touch(self, what: str):
        self.activity = what
        self.last_activity = time.monotonic()

    def run(self):
        started = datetime.now()
        engine.keep_awake(True)

        def cancel():
            # also where Pause takes effect, even in the middle of a big file
            while self.paused and not self.stop:
                time.sleep(0.2)
                self.last_activity = time.monotonic()
            return self.stop

        log = store = index = None
        last_emit = [0.0]
        try:
            self._touch("Checking what's already in the library")
            self.status.emit("Checking what's already on the destination drive…")
            index = engine.DestIndex(self.dest)
            index.build(lambda n: (self._touch(f"Checking the library ({n:,} files)"),
                                   self.status.emit(f"Checking what's already on the drive… {n:,} files")), cancel)
            self.leftovers = index.leftovers
            guard = engine.Guard(self.dest)
            log = engine.LogWriter(self.dest, started)
            self.log_path = log.path
            store = engine.CardStore(self.dest)
            done, done_bytes = 0, 0
            while True:
                if cancel():
                    self.cancelled = True
                    break
                if not self.queue:
                    if self.more_coming():
                        self._touch("Waiting for dropped files to be read")
                        self.status.emit("Waiting for the files you dropped to be read…")
                        time.sleep(0.2)
                        continue
                    break
                r = self.queue.popleft()
                it = self.items[r]
                if it.status != "Waiting":
                    continue
                drive = str(engine.volume_root(it.src))
                if drive in self.gone_drives:
                    it.note = "Its card or drive was unplugged. Plug it back in and press Start copying."
                    self.row_done.emit(r)
                    continue
                self._touch(f"Copying {it.src}")
                self.status.emit(f"Copying {it.src.name}  →  {it.folder.replace('/', ' › ')}")

                def on_bytes(what, d, t, name=it.src.name):
                    self.last_activity = time.monotonic()
                    now = time.monotonic()
                    if now - last_emit[0] > 0.3 or d >= t:
                        last_emit[0] = now
                        self.file_progress.emit(f"{what} {name} · {engine.human_size(d)} of {engine.human_size(t)}")

                stop_run = False
                for attempt in range(self.RETRIES + 1):
                    try:
                        engine.process(it, index, cancel, guard, on_bytes)
                        break
                    except engine.Cancelled:
                        it.status, it.note, it.dest = "Waiting", "", ""
                        self.cancelled = stop_run = True
                        break
                    except (engine.DriveFull, engine.DriveGone) as e:
                        it.status, it.dest = "Waiting", ""
                        it.note = str(e)
                        self.error = str(e)
                        stop_run = True
                        break
                    except Exception as e:
                        if engine.is_disk_full(e):
                            it.status, it.dest, it.note = "Waiting", "", "The library drive is full."
                            self.error = "The library drive is full."
                            stop_run = True
                            break
                        if not engine.exists(self.dest):
                            it.status, it.dest, it.note = "Waiting", "", "The library drive was disconnected."
                            self.error = "The library drive was disconnected. Plug it back in and press Start copying."
                            stop_run = True
                            break
                        if engine.source_gone(it):
                            self.gone_drives.add(drive)
                            it.status, it.dest = "Waiting", ""
                            it.note = "Its card or drive was unplugged. Plug it back in and press Start copying."
                            break
                        if (attempt < self.RETRIES and not isinstance(e, (FileNotFoundError, PermissionError))
                                and getattr(e, "errno", None) != 22):    # 22: Windows refused the name; retrying won't help
                            engine.log_error(f"Copying {it.src} (attempt {attempt + 1}, trying again)", e)
                            self.status.emit(f"Couldn't read {it.src.name}, trying again…")
                            time.sleep(2)
                            continue
                        engine.log_error(f"Copying {it.src}", e)
                        it.status, it.note = "Error", str(e)
                if it.status != "Waiting":
                    log.write(it)
                    store.record(it, engine.source_key(it.src)[1] if it.source_id else "")
                    done += 1
                    done_bytes += it.size
                    self.progress.emit(done, done + len(self.queue), done_bytes)
                self.row_done.emit(r)
                if stop_run:
                    break
                if done and done % 200 == 0:
                    index.save(drop_missing=False)
        except engine.Cancelled:
            self.cancelled = True
        except Exception as e:
            engine.log_error("Copying", e)
            self.error = str(e)
        finally:
            engine.keep_awake(False)
            self._touch("Saving the library records")
            for closer in ((lambda: index.save(drop_missing=False)) if index else None,
                           index.close if index else None,
                           store.close if store else None,
                           log.close if log else None):
                if closer:
                    try:
                        closer()
                    except Exception as e:
                        engine.log_error("Finishing up", e)
            if engine.exists(self.dest):
                engine.backup_db(self.dest)
            self._touch("Finished")


class HealthWorker(QThread):
    progress = Signal(int, int, str)

    def __init__(self, lib):
        super().__init__()
        self.lib, self.stop, self.result, self.error, self.cancelled = lib, False, {}, "", False

    def run(self):
        engine.keep_awake(True)
        try:
            self.result = engine.health_check(self.lib, self.progress.emit, lambda: self.stop)
        except engine.Cancelled:
            self.cancelled = True
        except Exception as e:
            engine.log_error("Health check", e)
            self.error = str(e)
        finally:
            engine.keep_awake(False)


class UpdateCheckWorker(QThread):
    def __init__(self):
        super().__init__()
        self.newer, self.local, self.rel, self.error = False, "", {}, ""

    def run(self):
        try:
            self.newer, self.local, self.rel = updater.check()
        except Exception as e:
            self.error = str(e)


class UpdateApplyWorker(QThread):
    status = Signal(str)

    def __init__(self, rel=None, rollback_to=""):
        super().__init__()
        self.rel, self.rollback_to, self.info, self.error = rel, rollback_to, {}, ""

    def run(self):
        try:
            if self.rollback_to:
                self.status.emit(f"Rolling back to {self.rollback_to}…")
                self.info = updater.rollback(self.rollback_to)
            else:
                self.info = updater.apply_update(self.rel, self.status.emit)
        except Exception as e:
            engine.log_error("Updating", e)
            self.error = str(e)


# --------------------------------------------------------------------------- #
# The list of files
# --------------------------------------------------------------------------- #
STATUS_COLORS = {"Copied": "#2e9d4f", "Copied (renamed)": "#2b7bd6", "Duplicate": "#8a8a8a",
                 "Error": "#d23c3c", "Skipped": "#b07a1c"}


class ItemModel(QAbstractTableModel):
    HEAD = ["File", "Kind", "Taken / Album", "Goes to", "Result", "Note"]

    def __init__(self):
        super().__init__()
        self.items: list[engine.Item] = []
        self.keys: set[str] = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEAD)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[s]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        it = self.items[idx.row()]
        c = idx.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return it.src.name
            if c == 1:
                k = engine.KIND_LABEL.get(it.kind, it.kind)
                return k + (" (camera)" if it.kind == "video" and it.camera else "")
            if c == 2:
                if it.kind == "music":
                    return f"{it.artist or 'Unknown Artist'} – {it.album or 'Unknown Album'}"
                if it.kind not in ("raw", "photo", "video"):
                    return ""
                if not it.taken:
                    return "no date"
                t = it.taken.strftime("%Y-%m-%d %H:%M")
                return t + ("  (file date)" if it.date_source == "file date" else "")
            if c == 3:
                return (it.dest or it.folder).replace("/", " \\ ")
            if c == 4:
                return it.status
            if c == 5:
                return it.note
        if role == Qt.ToolTipRole:
            return f"{it.src}\n{engine.human_size(it.size)}" + (
                "\nWith: " + ", ".join(p.name for p in it.sidecars) if it.sidecars else "")
        if role == Qt.ForegroundRole and c == 4 and it.status in STATUS_COLORS:
            return QColor(STATUS_COLORS[it.status])
        return None

    def add(self, batch) -> list[int]:
        new = []
        for b in batch:
            k = os.path.normcase(str(b.src))
            if k not in self.keys:
                self.keys.add(k)
                new.append(b)
        if not new:
            return []
        n = len(self.items)
        self.beginInsertRows(QModelIndex(), n, n + len(new) - 1)
        self.items.extend(new)
        self.endInsertRows()
        return list(range(n, n + len(new)))

    def refresh_row(self, r):
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.HEAD) - 1))

    def refresh_all(self):
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(self.HEAD) - 1))

    def clear(self):
        self.beginResetModel()
        self.items = []
        self.keys = set()
        self.endResetModel()


DROP_TEXT = ("Drop memory cards, old drives or folders anywhere on this window\n\n"
             "They're only read. Nothing on them is changed. You can keep dropping more while it copies.")


class DropZone(QFrame):
    """The dashed box. Drops land anywhere on the window; this shows what's being dragged."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(100)
        lay = QVBoxLayout(self)
        self.label = QLabel(DROP_TEXT)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        f = self.label.font()
        f.setPointSize(f.pointSize() + 3)
        self.label.setFont(f)
        lay.addWidget(self.label)
        self.hot(False)

    def hot(self, on: bool, text: str = ""):
        self.label.setText(text if on and text else DROP_TEXT)
        self.setStyleSheet("DropZone { border: 2px dashed %s; border-radius: 12px; %s }"
                           % ("#2b7bd6" if on else "#888", "background: rgba(43,123,214,0.15);" if on else ""))


def describe_drop(paths: list[Path]) -> str:
    drives = folders = files = 0
    for p in paths:
        if p.is_dir() and Path(p.anchor) == p:
            drives += 1
        elif p.is_dir():
            folders += 1
        else:
            files += 1
    bits = [f"{n:,} {w}{'' if n == 1 else 's'}" for n, w in ((drives, "drive"), (folders, "folder"), (files, "file")) if n]
    return "Drop to add " + ", ".join(bits) if bits else "Drop to add"


class FolderTree(QTreeWidget):
    """'Where files will go'. Drag a folder onto another to move it there; drops from Explorer add files."""
    moved = Signal(str, str)       # folder, new parent folder
    urls_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    @staticmethod
    def key(item) -> str | None:
        if item is None:
            return None
        k = item.data(0, Qt.UserRole)
        return k if isinstance(k, str) else None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.source() is self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)
        target = self.key(self.itemAt(e.position().toPoint()))
        src = self.key(self.currentItem())
        if target is None or not src or target == src or target.startswith(src + "/"):
            e.ignore()
        else:
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.urls_dropped.emit([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()
            return
        target = self.key(self.itemAt(e.position().toPoint()))
        src = self.key(self.currentItem())
        e.ignore()                 # the tree is rebuilt from the files, not moved by Qt
        if target is not None and src and target != src and not target.startswith(src + "/"):
            self.moved.emit(src, target)


def _combo(options: dict, current: str) -> QComboBox:
    c = QComboBox()
    for k, label in options.items():
        c.addItem(label, k)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = engine.load_settings()
        self.scanners: list[ScanWorker] = []
        self.copier: CopyWorker | None = None
        self.healer: HealthWorker | None = None
        self.tray: QSystemTrayIcon | None = None
        self.aliases: dict = engine.load_aliases(self.dest())
        self.lock: QLockFile | None = None
        self._stall_logged = 0.0
        self.source_lines: list[str] = []
        self._paused_at = 0.0
        self._upd_check = None
        self._upd_apply = None
        self._copy_t0 = 0.0
        self.setWindowTitle(f"{APP_NAME} — sort cards and old drives onto a new drive")
        if ICON_FILE.exists():
            self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.resize(1180, 800)
        self.setAcceptDrops(True)        # drop anywhere on the window
        self._menus()

        w = QWidget()
        v = QVBoxLayout(w)

        # destination
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Library folder on the new drive:</b>"))
        self.dest_label = QLabel()
        self.dest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.dest_label, 1)
        b = QPushButton("Choose…")
        b.clicked.connect(self.choose_dest)
        row.addWidget(b)
        self.b_open_dest = QPushButton("Open")
        self.b_open_dest.clicked.connect(lambda: self.dest() and open_path(self.dest()))
        row.addWidget(self.b_open_dest)
        v.addLayout(row)

        # sorting options
        box = QGroupBox("How to sort")
        g = QGridLayout(box)
        inc = QHBoxLayout()
        self.c_photos = QCheckBox("Photos && RAW")
        self.c_videos = QCheckBox("Videos")
        self.c_music = QCheckBox("Music")
        self.c_docs = QCheckBox("Documents")
        self.c_other = QCheckBox("Everything else")
        self.c_other.setToolTip("Any other kind of file goes in Other, in a folder per file type (ZIP, EXE …).")
        self.includes = {"photos": self.c_photos, "videos": self.c_videos, "music": self.c_music,
                         "docs": self.c_docs, "other": self.c_other}
        for k, c in self.includes.items():
            c.setChecked(self.settings.get(k, engine.DEFAULT_SETTINGS[k]))
            c.toggled.connect(self._options_changed)
            inc.addWidget(c)
        inc.addSpacing(16)
        inc.addWidget(QLabel("Skip pictures under"))
        self.tiny_box = QSpinBox()
        self.tiny_box.setRange(0, 2000)
        self.tiny_box.setSuffix(" KB")
        self.tiny_box.setSpecialValueText("off")
        self.tiny_box.setValue(int(self.settings.get("min_photo_kb", 30)))
        self.tiny_box.setToolTip("Icons, thumbnails and web images are tiny; real photos never are. "
                                 "RAW files and album covers are always kept. 0 = keep every picture.")
        self.tiny_box.valueChanged.connect(self._options_changed)
        inc.addWidget(self.tiny_box)
        inc.addStretch(1)
        g.addWidget(QLabel("Include:"), 0, 0)
        g.addLayout(inc, 0, 1, 1, 3)

        self.layout_box = _combo(engine.LAYOUTS, self.settings["layout"])
        self.raw_box = _combo(engine.RAW_SPLIT, self.settings["raw_split"])
        self.camvid_box = _combo(engine.CAMERA_VIDEOS, self.settings["camera_videos"])
        self.camvid_box.setToolTip("Videos from cameras and phones: found in a card's DCIM folder, next to "
                                   "camera photos, or named like one (MVI_, DSC, GOPR, C0001, VID_ …).")
        self.othervid_box = _combo(engine.OTHER_VIDEOS, self.settings["other_videos"])
        self.othervid_box.setToolTip("Any other videos (downloads, movies, screen recordings).")
        self.nodate_box = _combo({"file_date": "Use the file's date (what the camera saved)",
                                  "undated": "Put it in an \"Undated\" folder"}, self.settings["no_date"])
        g.addWidget(QLabel("Photo folders:"), 1, 0)
        g.addWidget(self.layout_box, 1, 1)
        g.addWidget(QLabel("RAW / JPEG:"), 1, 2)
        g.addWidget(self.raw_box, 1, 3)
        g.addWidget(QLabel("Camera videos:"), 2, 0)
        g.addWidget(self.camvid_box, 2, 1)
        g.addWidget(QLabel("Other videos:"), 2, 2)
        g.addWidget(self.othervid_box, 2, 3)
        g.addWidget(QLabel("No date inside:"), 3, 0)
        g.addWidget(self.nodate_box, 3, 1)
        self.c_auto = QCheckBox("Start copying as soon as I drop files")
        self.c_auto.setChecked(self.settings.get("auto_start", False))
        g.addWidget(self.c_auto, 3, 2, 1, 2)
        for c in (self.layout_box, self.raw_box, self.camvid_box, self.othervid_box, self.nodate_box):
            c.currentIndexChanged.connect(self._options_changed)
        self.c_auto.toggled.connect(self._options_changed)
        self.example = QLabel()
        self.example.setStyleSheet("color: gray")
        self.example.setWordWrap(True)
        g.addWidget(self.example, 4, 0, 1, 4)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        v.addWidget(box)

        self.cards_label = QLabel()
        self.cards_label.setWordWrap(True)
        self.cards_label.setStyleSheet("background: rgba(43,123,214,0.12); border-radius: 6px; padding: 6px;")
        self.cards_label.hide()
        v.addWidget(self.cards_label)

        self.drop = DropZone()
        v.addWidget(self.drop)

        row = QHBoxLayout()
        b = QPushButton("Add a folder…")
        b.clicked.connect(self.add_folder)
        row.addWidget(b)
        self.summary = QLabel("")
        row.addWidget(self.summary, 1)
        self.b_clear = QPushButton("Clear list")
        self.b_clear.clicked.connect(self.clear)
        row.addWidget(self.b_clear)
        self.b_start = QPushButton("Start copying")
        f = self.b_start.font()
        f.setBold(True)
        self.b_start.setFont(f)
        self.b_start.clicked.connect(self.start)
        row.addWidget(self.b_start)
        self.b_pause = QPushButton("Pause")
        self.b_pause.clicked.connect(self.toggle_pause)
        self.b_pause.hide()
        row.addWidget(self.b_pause)
        self.b_cancel = QPushButton("Stop")
        self.b_cancel.clicked.connect(self.cancel)
        self.b_cancel.hide()
        row.addWidget(self.b_cancel)
        v.addLayout(row)

        self.model = ItemModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        for col, wd in enumerate((200, 110, 200, 320, 120)):
            self.table.setColumnWidth(col, wd)
        self.table.doubleClicked.connect(self._open_row)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.tree = FolderTree()
        self.tree.setHeaderLabels(["Folder", "Files", "Size"])
        self.tree.setToolTip("Right-click a folder (or press F2) to rename it, move it or skip its files. "
                             "Drag a folder onto another to move it there.")
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.moved.connect(self._tree_moved)
        self.tree.urls_dropped.connect(self.add_paths)
        QShortcut(QKeySequence(Qt.Key_F2), self.tree, activated=lambda: self._rename_folder(self.tree.currentItem()))
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 90)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._open_tree_item)
        self._tree_timer = QTimer(self)
        self._tree_timer.setSingleShot(True)
        self._tree_timer.setInterval(400)
        self._tree_timer.timeout.connect(self._rebuild_tree)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.tree, "Where files will go")
        self.tabs.addTab(self.table, "All files")
        v.addWidget(self.tabs, 1)

        self.bar = QProgressBar()
        self.bar.hide()
        v.addWidget(self.bar)
        self.status = QLabel("")
        v.addWidget(self.status)
        self.setCentralWidget(w)

        self._show_dest()
        self._show_example()
        self._update_buttons()
        self._rebuild_tree()
        self._restore_layout()
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(30000)
        self._watchdog.timeout.connect(self._check_stall)
        self._watchdog.start()
        if self.settings.get("check_updates", True):
            QTimer.singleShot(2500, lambda: self.check_updates(quiet=True))

    # ----------------------------------------------------------------- menus
    def _menus(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Choose library folder…", self)
        a.triggered.connect(self.choose_dest)
        m.addAction(a)
        a = QAction("Add a folder…", self)
        a.triggered.connect(self.add_folder)
        m.addAction(a)
        a = QAction("Open sort logs (what happened to every file)…", self)
        a.triggered.connect(self._open_logs)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Make a desktop shortcut", self)
        a.triggered.connect(self.make_shortcut)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.triggered.connect(self.close)
        m.addAction(a)

        m = self.menuBar().addMenu("&View")
        grp = QActionGroup(self)
        for key, label in (("system", "Match Windows"), ("light", "Light"), ("dark", "Dark")):
            a = QAction(label, self, checkable=True)
            a.setChecked(self.settings.get("theme", "system") == key)
            a.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        self.a_notify = QAction("Notify me when copying finishes", self, checkable=True)
        self.a_notify.setChecked(self.settings.get("notify", True))
        self.a_notify.toggled.connect(lambda on: (self.settings.__setitem__("notify", on),
                                                  engine.save_settings(self.settings)))
        m.addAction(self.a_notify)

        m = self.menuBar().addMenu("&Tools")
        a = QAction("Check the library for damaged files…", self)
        a.triggered.connect(self.health_check)
        m.addAction(a)
        a = QAction("Forget renamed folders…", self)
        a.triggered.connect(self._reset_aliases)
        m.addAction(a)

        m = self.menuBar().addMenu("&Help")
        a = QAction("How it works", self)
        a.triggered.connect(self._how)
        m.addAction(a)
        a = QAction("Report a problem…", self)
        a.triggered.connect(self.report_problem)
        m.addAction(a)
        a = QAction("Open the error log", self)
        a.triggered.connect(lambda: open_path(engine.LOG_FILE) if engine.LOG_FILE.exists()
                            else QMessageBox.information(self, APP_NAME, "No errors have been logged."))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Check for updates…", self)
        a.triggered.connect(lambda: self.check_updates(quiet=False))
        m.addAction(a)
        a = QAction("Roll back to an earlier version…", self)
        a.triggered.connect(self.roll_back)
        m.addAction(a)
        self.a_auto = QAction("Check for updates when the app opens", self, checkable=True)
        self.a_auto.setChecked(self.settings.get("check_updates", True))
        self.a_auto.toggled.connect(lambda on: (self.settings.__setitem__("check_updates", on),
                                                engine.save_settings(self.settings)))
        m.addAction(self.a_auto)
        m.addSeparator()
        a = QAction(f"Support {APP_NAME} (GitHub Sponsors)…", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(SPONSOR_URL)))
        m.addAction(a)
        a = QAction("Project page on GitHub…", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(updater.PAGE_URL)))
        m.addAction(a)
        a = QAction(f"About {APP_NAME}", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, APP_NAME, f"<b>{APP_NAME}</b> {updater.local_version()}<br>Copies memory cards and old "
            f"drives into one tidy library: photos and videos by date, music by artist and album, without "
            f"duplicates and without overwriting anything.<br><br><a href='{updater.PAGE_URL}'>"
            f"{updater.PAGE_URL}</a><br>Enjoying it? <a href='{SPONSOR_URL}'>Support development on "
            f"GitHub Sponsors</a>."))
        m.addAction(a)

    def _how(self):
        QMessageBox.information(self, "How it works", (
            "1. Choose a library folder on the new drive (for example D:\\Library).\n"
            "2. Drop memory cards, old drives or folders on the window. Keep dropping more while it copies.\n"
            "3. Check the list, then press Start copying.\n\n"
            "• Photos and RAW go to Photos \\ year \\ month, by the date the picture was taken.\n"
            "• Camera and phone videos go with their photos, or to Videos, as you choose.\n"
            "• Music goes to Music \\ artist \\ album, read from the songs' tags. Album covers follow.\n"
            "• Documents go to Documents \\ PDFs, Spreadsheets and so on.\n"
            "• A file that's exactly the same as one already in the library (byte for byte) is skipped.\n"
            "• A different file with the same name gets a number added: IMG_0001_1.CR2.\n"
            "• Nothing in the library is ever overwritten, and nothing on your cards is changed or deleted.\n"
            "• Every copy is checked against the original before it's kept."))

    # ------------------------------------------------------------ destination
    def dest(self) -> Path | None:
        d = self.settings.get("destination") or ""
        return Path(d) if d else None

    def choose_dest(self):
        if self.copier:
            return
        start = str(self.dest() or Path.home())
        d = QFileDialog.getExistingDirectory(self, "Choose (or make) the library folder on the new drive", start)
        if d:
            self.settings["destination"] = d
            engine.save_settings(self.settings)
            self.aliases = engine.load_aliases(Path(d))
            self._replan()
            self._show_dest()
            self._show_example()

    def _show_dest(self):
        d = self.dest()
        if not d:
            self.dest_label.setText("<i>not chosen yet — press Choose… and pick a folder on the new drive</i>")
        elif not d.exists():
            self.dest_label.setText(f"{d}   <span style='color:#d23c3c'>(not connected)</span>")
        else:
            try:
                free = engine.human_size(shutil.disk_usage(d).free)
                self.dest_label.setText(f"{d}   <span style='color:gray'>({free} free)</span>")
            except OSError:
                self.dest_label.setText(str(d))
        self.b_open_dest.setEnabled(bool(d and d.exists()))

    # ---------------------------------------------------------------- options
    def _options_changed(self):
        old_inc = {k: self.settings.get(k) for k in self.includes}
        old_nodate = self.settings.get("no_date")
        old_tiny = self.settings.get("min_photo_kb")
        for k, c in self.includes.items():
            self.settings[k] = c.isChecked()
        self.settings.update(layout=self.layout_box.currentData(), raw_split=self.raw_box.currentData(),
                             camera_videos=self.camvid_box.currentData(),
                             other_videos=self.othervid_box.currentData(),
                             no_date=self.nodate_box.currentData(), auto_start=self.c_auto.isChecked(),
                             min_photo_kb=self.tiny_box.value())
        engine.save_settings(self.settings)
        changed_scan = (old_inc != {k: self.settings[k] for k in self.includes}
                        or old_nodate != self.settings["no_date"]
                        or old_tiny != self.settings["min_photo_kb"])
        if changed_scan and self.model.items and not self.copier:
            self.status.setText("Include / date settings changed. Clear the list and drop the folders again "
                                "to use them for these files.")
        self._show_example()
        self._replan()

    def _replan(self):
        """Update where waiting files will go after a folder option changes."""
        if self.copier:
            return
        engine.plan(self.model.items, self.settings, {}, self.aliases)
        self.model.refresh_all()
        self._tree_timer.start()

    def _show_example(self):
        s = self.settings
        root = str(self.dest() or "Library")
        dt = datetime(2019, 7, 4)
        probe = [
            ("photos", engine.Item(src=Path("IMG_0001.CR2"), size=0, kind="raw", taken=dt)),
            ("videos", engine.Item(src=Path("MVI_0042.MP4"), size=0, kind="video", camera=True, taken=dt)),
            ("music", engine.Item(src=Path("01 Song.mp3"), size=0, kind="music", artist="Artist", album="Album")),
            ("docs", engine.Item(src=Path("Taxes.pdf"), size=0, kind="doc")),
        ]
        items = [it for k, it in probe if s.get(k)]
        engine.plan(items, s, {})
        lines = [f"{root}\\{it.folder}\\{it.src.name}".replace("/", "\\") for it in items]
        self.example.setText("Example:  " + "     ".join(lines) if lines else "Nothing is included. Tick a box above.")

    # ------------------------------------------------------------------ files
    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a memory card, drive or folder to sort")
        if d:
            self.add_paths([Path(d)])

    def add_paths(self, paths):
        if self.healer:
            self.status.setText("The health check is running. Drop files when it's finished (or press Stop).")
            return
        dest = self.dest()
        if not dest:
            QMessageBox.information(self, APP_NAME, "First choose the library folder on the new drive "
                                    "(Choose… at the top).")
            self.choose_dest()
            dest = self.dest()
            if not dest:
                return
        if not dest.exists():
            QMessageBox.warning(self, APP_NAME, "The new drive isn't connected. Plug it in first.")
            return
        s = ScanWorker(paths, self.settings, dest, self.aliases)
        s.found.connect(self._found)
        s.sources.connect(self._sources_seen)
        s.status.connect(lambda t: self.copier is None and self.status.setText(t))
        s.finished.connect(lambda: self._scan_done(s))
        self.scanners.append(s)
        if not self.copier:
            self.status.setText("Looking for files…")
        self._update_buttons()
        s.start()
        if self.settings.get("auto_start") and not self.copier:
            self.start()        # files are copied as they're found

    def _found(self, batch):
        rows = self.model.add(batch)
        if self.copier and rows:
            self.copier.queue.extend(rows)      # joins the copy that's already running
        self._update_summary()

    def _sources_seen(self, summaries):
        """One line per card / folder dropped: what was found, and whether it was backed up before."""
        for info in summaries:
            p = Path(info["path"])
            title = info["name"] if Path(p.anchor) == p and info["name"] else (p.name or str(p))
            total = info["known"] + info["new"]
            if not total:
                line = f"<b>{title}</b>: nothing to copy"
            else:
                kinds = ", ".join(f"{n:,} {k}" for k, n in sorted(info["kinds"].items(), key=lambda x: -x[1]))
                line = f"<b>{title}</b>: {total:,} files ({kinds})"
                if info["known"] and info["last"]:
                    line += (f" · backed up before on {info['last']:%b %d, %Y}: {info['new']:,} new, "
                             f"{info['known']:,} already backed up (won't be read again)")
                elif info["new"]:
                    line += " · first backup to this library"
            if info.get("tiny"):
                line += f" · {info['tiny']:,} tiny pictures skipped"
            self.source_lines.append(line)
        if self.source_lines:
            self.cards_label.setText("<br>".join(self.source_lines[-8:]))
            self.cards_label.show()

    def _scan_done(self, s):
        if s in self.scanners:
            self.scanners.remove(s)
        self._update_summary()
        self._update_buttons()
        if self.scanners or self.copier:
            return
        waiting = sum(1 for it in self.model.items if it.status == "Waiting")
        if not waiting:
            self.status.setText("Nothing new to copy there (check the Include boxes above).")
            return
        self.status.setText(f"{waiting:,} files ready. Check the list, then press Start copying.")
        if self.settings.get("auto_start"):
            self.start()

    def _update_summary(self):
        if not self._tree_timer.isActive():     # at most a few rebuilds a second while copying
            big = len(self.model.items) > 50000
            self._tree_timer.start((10000 if big else 1500) if self.copier else (1500 if big else 400))
        items = self.model.items
        if not items:
            self.summary.setText("")
            return
        waiting = [it for it in items if it.status == "Waiting"]
        size = sum(it.size for it in waiting)
        counts: dict[str, int] = {}
        for it in waiting:
            k = {"raw": "photos", "photo": "photos", "video": "videos", "music": "songs", "art": "songs",
                 "doc": "documents", "other": "other"}[it.kind]
            counts[k] = counts.get(k, 0) + 1
        parts = ", ".join(f"{n:,} {k}" for k, n in counts.items())
        skipped = sum(1 for it in items if it.status == "Skipped")
        done = len(items) - len(waiting) - skipped
        self.summary.setText(f"  {len(waiting):,} to copy ({engine.human_size(size)})"
                             + (f": {parts}" if parts else "") + (f" · {done:,} done" if done else "")
                             + (f" · {skipped:,} skipped by you" if skipped else ""))

    def clear(self):
        if self.copier or self.scanners:
            return
        self.model.clear()
        self.source_lines = []
        self.cards_label.hide()
        self._update_summary()
        self.status.setText("")
        self._update_buttons()

    def _open_row(self, idx):
        it = self.model.items[idx.row()]
        d = self.dest()
        if it.dest and d and (d / it.dest).exists():
            open_path((d / it.dest).parent)
        elif it.src.parent.exists():
            open_path(it.src.parent)

    def _open_logs(self):
        d = self.dest()
        p = d / engine.META_NAME / engine.LOGS_NAME if d else None
        if p and p.is_dir():
            open_path(p)
        else:
            QMessageBox.information(self, "Sort logs", "No sort logs in this library yet.")

    # ---------------------------------------------------------------- copying
    def start(self):
        if self.copier or self.healer:
            return
        dest = self.dest()
        if not dest or not dest.exists():
            QMessageBox.warning(self, APP_NAME, "The library folder isn't available (drive not connected, or "
                                "not chosen). Plug the drive in or press Choose….")
            return
        rows = [r for r, it in enumerate(self.model.items) if it.status in ("Waiting", "Error")]
        if not rows and not self.scanners:
            self.status.setText("Nothing waiting to copy.")
            return
        if not self._preflight(dest, rows):
            return
        if not self._take_lock(dest):
            return
        need = sum(self.model.items[r].size for r in rows)
        try:
            free = shutil.disk_usage(dest).free
            if need > free and QMessageBox.question(
                    self, "Not enough space?",
                    f"These files are {engine.human_size(need)} and the drive has {engine.human_size(free)} "
                    "free. Duplicates aren't copied, so it may still fit. Start anyway? (It stops safely "
                    "if the drive fills up.)") != QMessageBox.Yes:
                return
        except OSError:
            pass
        for r in rows:
            it = self.model.items[r]
            it.status, it.dest = "Waiting", ""
            it.note = "Backed up before" if it.known_dest else ""
        engine.plan([self.model.items[r] for r in rows], self.settings, {}, self.aliases)
        self.model.refresh_all()
        c = self.copier = CopyWorker(self.model.items, rows, dest, lambda: bool(self.scanners))
        c.status.connect(self.status.setText)
        c.file_progress.connect(self.status.setText)
        c.row_done.connect(self._row_done)
        c.progress.connect(self._progress)
        c.finished.connect(self._copy_done)
        self.bar.setRange(0, max(1, len(rows)))
        self.bar.setValue(0)
        self.bar.setFormat("Starting…")
        self.bar.show()
        self._copy_t0 = time.monotonic()
        self._update_buttons()
        c.start()

    def _row_done(self, r):
        self.model.refresh_row(r)
        self.table.scrollTo(self.model.index(r, 0))
        self._update_summary()

    def _progress(self, done, total, done_bytes):
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        secs = max(0.1, time.monotonic() - self._copy_t0)
        rate = done_bytes / secs
        left = ""
        if done and done < total:
            remain = (total - done) * secs / done
            left = f" · about {int(remain // 60)} min {int(remain % 60)} s left"
        self.bar.setFormat(f"{done:,} of {total:,} · {engine.human_size(rate)}/s{left}")

    def toggle_pause(self):
        c = self.copier
        if not c:
            return
        c.paused = not c.paused
        if c.paused:
            self._paused_at = time.monotonic()
            self.b_pause.setText("Resume")
            self.status.setText("Paused. Press Resume to carry on (or Stop).")
            self.bar.setFormat("Paused · " + self.bar.format())
        else:
            self._copy_t0 += time.monotonic() - self._paused_at   # don't count the pause in the speed
            self.b_pause.setText("Pause")
            self.status.setText("Carrying on…")

    def cancel(self):
        if self.copier:
            self.copier.paused = False
        if self.healer:
            self.healer.stop = True
        if self.copier:
            self.copier.stop = True
            self.status.setText("Stopping after the current file… (nothing half-copied is left behind)")
        for s in self.scanners:
            s.stop = True

    def _copy_done(self):
        c = self.copier
        self.copier = None
        self._release_lock()
        self.b_pause.setText("Pause")
        self.bar.hide()
        self._update_buttons()
        self._update_summary()
        self._show_dest()
        counts: dict[str, int] = {}
        for it in self.model.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        copied = counts.get("Copied", 0)
        renamed = counts.get("Copied (renamed)", 0)
        dupes = counts.get("Duplicate", 0)
        errors = counts.get("Error", 0)
        head = "Stopped." if c.cancelled else ("Something went wrong." if c.error else "All done.")
        msg = (f"{head}\n\n{copied + renamed:,} copied"
               + (f" ({renamed:,} given a number because a different file had the same name)" if renamed else "")
               + f"\n{dupes:,} skipped because they were already in the library")
        if errors:
            msg += f"\n{errors:,} couldn't be copied (see the Note column; press Start copying to retry them)"
        if c.error:
            msg += f"\n\n{c.error}"
        if c.gone_drives:
            msg += ("\n\nA card or drive was unplugged during copying (" + ", ".join(sorted(c.gone_drives))
                    + "). Its remaining files are still waiting: plug it back in and press Start copying.")
        if c.leftovers:
            msg += (f"\n\n{c.leftovers:,} half-finished copies from an earlier crash or power cut were "
                    "cleaned up (only the program's own temporary files).")
        if c.cancelled or (c.error and any(it.status == "Waiting" for it in self.model.items)):
            msg += "\n\nPress Start copying to carry on where it stopped."
        self.status.setText(msg.replace("\n\n", "  ").replace("\n", " · "))
        self.notify(head.rstrip("."), f"{copied + renamed:,} copied, {dupes:,} already in the library"
                    + (f", {errors:,} problems" if errors else ""), warn=bool(errors or c.error))
        box = QMessageBox(QMessageBox.Information if not (errors or c.error) else QMessageBox.Warning,
                          APP_NAME, msg, parent=self)
        b_open = box.addButton("Open library", QMessageBox.ActionRole)
        b_log = box.addButton("Open log", QMessageBox.ActionRole) if c.log_path else None
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_open and self.dest():
            open_path(self.dest())
        elif b_log is not None and clicked is b_log:
            open_path(c.log_path)

    # ------------------------------------------------------------ reliability
    def _take_lock(self, lib: Path) -> bool:
        """Only one Millésime window works on a library at a time, so its records can't get mixed up."""
        try:
            os.makedirs(engine.L(lib / engine.META_NAME), exist_ok=True)
            lock = QLockFile(str(lib / engine.META_NAME / "millesime.lock"))
            lock.setStaleLockTime(0)          # a crashed window's lock is cleared automatically
            if not lock.tryLock(500):
                QMessageBox.warning(self, APP_NAME, "Another Millésime window (on this or another computer) is "
                                    "working on this library right now. Wait for it to finish, then try again.")
                return False
            self.lock = lock
            return True
        except Exception as e:
            engine.log_error("Locking the library", e)
            return True

    def _release_lock(self):
        if self.lock:
            try:
                self.lock.unlock()
            except Exception:
                pass
            self.lock = None

    def _preflight(self, dest: Path, rows) -> bool:
        """Warnings before a copy starts. Returns False if the user chose not to go ahead."""
        items = [self.model.items[r] for r in rows]
        warnings = []
        if os.name == "nt":
            sysdrive = (os.environ.get("SystemDrive", "C:") + "\\").upper()
            if str(engine.volume_root(dest)).upper() == sysdrive and self.settings.get("ok_sysdrive") != str(dest):
                warnings.append(("The library is on the drive Windows runs from. That isn't much of a backup: if "
                                 "this drive fails, the library goes with it. Choose a folder on the new drive "
                                 "instead (Choose… at the top).", "ok_sysdrive"))
        fs = engine.fs_type(dest).upper()
        if fs.startswith("FAT"):
            big = sum(1 for it in items if it.size >= engine.FAT_LIMIT)
            if big:
                warnings.append((f"The library drive is formatted {fs}, which can't hold files of 4 GB or more. "
                                 f"{big:,} file(s) here are that big (usually long videos) and will show as errors. "
                                 "Reformatting the drive as NTFS (or exFAT) avoids this; it erases the drive, so do "
                                 "it before copying.", ""))
        same = sum(1 for it in items if str(engine.volume_root(it.src)).upper() == str(engine.volume_root(dest)).upper())
        if same:
            warnings.append((f"{same:,} of these files are already on the library drive (just in another folder). "
                             "Copying them uses that space twice.", ""))
        for text, key in warnings:
            if QMessageBox.question(self, "Before copying", text + "\n\nCopy anyway?") != QMessageBox.Yes:
                return False
            if key:
                self.settings[key] = str(dest)
                engine.save_settings(self.settings)
        return True

    def _check_stall(self):
        """If nothing has moved for 5 minutes, write down where it's stuck (for Report a problem)."""
        c = self.copier
        if not c or c.paused:
            return
        idle = time.monotonic() - c.last_activity
        if idle > 300 and time.monotonic() - self._stall_logged > 600:
            self._stall_logged = time.monotonic()
            engine.log_error(f"No progress for {int(idle // 60)} minutes while: {c.activity}",
                             "Where each part of the program is:\n" + engine.thread_dump())
            self.status.setText(f"No progress for {int(idle // 60)} minutes on: {c.activity}. The card may have a "
                                "bad spot. You can press Stop, and Help › Report a problem… makes a report to send.")

    def report_problem(self):
        c, h = self.copier, self.healer
        state = {"library": str(self.dest() or ""), "files in list": len(self.model.items),
                 "copying": bool(c), "scanning": len(self.scanners), "health check": bool(h),
                 "status line": self.status.text()}
        if c:
            state.update({"current step": c.activity, "paused": c.paused,
                          "seconds since last progress": int(time.monotonic() - c.last_activity),
                          "queued": len(c.queue)})
        counts: dict = {}
        for it in self.model.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        state["results so far"] = counts
        errs = [f"{it.src.name}: {it.note}" for it in self.model.items if it.status == "Error"][:30]
        if errs:
            state["file errors"] = "\n  " + "\n  ".join(errs)
        text = engine.make_report(state)

        dlg = QDialog(self)
        dlg.setWindowTitle("Report a problem")
        dlg.resize(820, 600)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("This report shows what the app was doing, its settings and recent errors (your "
                             "Windows user name is hidden). Copy it, or save it as a file, and send it along with "
                             "a sentence about what happened."))
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        lay.addWidget(box, 1)
        btns = QDialogButtonBox()
        b_copy = btns.addButton("Copy to clipboard", QDialogButtonBox.ActionRole)
        b_save = btns.addButton("Save to desktop", QDialogButtonBox.ActionRole)
        btns.addButton(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)

        def copy():
            QApplication.clipboard().setText(text)
            b_copy.setText("Copied ✓")

        def save():
            desk = Path.home() / "Desktop"
            if not desk.is_dir():
                desk = Path.home()
            p = desk / f"Millesime problem report {datetime.now():%Y-%m-%d %H%M}.txt"
            try:
                p.write_text(text, "utf-8")
                b_save.setText("Saved ✓")
                QMessageBox.information(dlg, "Saved", f"Saved as:\n{p}")
            except OSError as e:
                QMessageBox.warning(dlg, "Save", f"Couldn't save it: {e}")

        b_copy.clicked.connect(copy)
        b_save.clicked.connect(save)
        dlg.exec()

    # ---------------------------------------------------- drop anywhere
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                e.acceptProposedAction()
                self.drop.hot(True, describe_drop(paths) + (" (it joins the queue)" if self.copier else ""))
                return
        e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop.hot(False)

    def dropEvent(self, e):
        self.drop.hot(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.add_paths(paths)

    # ------------------------------------------- rename / move / skip by hand
    def _items_under(self, key: str):
        return [it for it in self.model.items if it.status == "Waiting"
                and (not key or it.folder == key or it.folder.startswith(key + "/"))]

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        key = FolderTree.key(item)
        if key is None or self.copier:
            return
        n = len(self._items_under(key))
        m = QMenu(self)
        if key:
            m.addAction("Rename folder…  (F2)", lambda: self._rename_folder(item))
            m.addAction("Move to…", lambda: self._move_items(self._items_under(key), key))
        m.addAction(f"Skip these {n:,} files", lambda: self._set_skipped(self._items_under(key), True))
        m.addSeparator()
        m.addAction("Open folder", lambda: self._open_tree_item(item, 0))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename_folder(self, item):
        key = FolderTree.key(item)
        if not key or self.copier:
            return
        parent, _, name = key.rpartition("/")
        new, ok = QInputDialog.getText(self, "Rename folder",
                                       f"New name for “{name}”.\n\nFiles going here now, and ones added later, "
                                       "go to the new name. Folders already on the drive aren't renamed.",
                                       text=name)
        new = engine.safe_name(new, "")
        if not ok or not new or new == name:
            return
        new_key = f"{parent}/{new}" if parent else new
        for it in self.model.items:        # files moved here by hand follow the rename too
            if it.status == "Waiting" and it.override and (it.override == key or it.override.startswith(key + "/")):
                it.override = new_key + it.override[len(key):]
        self.aliases[key] = new_key
        if self.dest():
            engine.save_aliases(self.dest(), self.aliases)
        self._replan()
        self.status.setText(f"“{name}” renamed to “{new}”. Tools › Forget renamed folders undoes all renames.")

    def _tree_moved(self, src: str, target: str):
        if self.copier:
            return
        base = (f"{target}/" if target else "") + src.rpartition("/")[2]
        for it in self._items_under(src):
            it.override = base + it.folder[len(src):]
        self._replan()
        self.status.setText(f"Moved to {base.replace('/', ' › ')}")

    def _move_items(self, items, key_hint: str = ""):
        if not items or self.copier:
            return
        folders = sorted({it.folder for it in self.model.items if it.status == "Waiting"})
        current = key_hint or items[0].folder
        text, ok = QInputDialog.getItem(self, "Move to…", f"Put {len(items):,} file(s) in this folder "
                                        "(pick one, or type a new one such as Photos\\Events\\Wedding):",
                                        [f.replace("/", "\\") for f in folders], max(0, folders.index(current))
                                        if current in folders else 0, True)
        target = engine.clean_folder_path(text)
        if not ok or not target:
            return
        if key_hint:        # moving a whole folder: keep its sub-folders inside the new place
            for it in items:
                it.override = target + it.folder[len(key_hint):]
        else:
            for it in items:
                it.override = target
        self._replan()

    def _set_skipped(self, items, skip: bool):
        for it in items:
            if skip and it.status == "Waiting":
                it.status, it.note = "Skipped", "Skipped by you (not copied)"
            elif not skip and it.status == "Skipped":
                it.status, it.note = "Waiting", "Backed up before" if it.known_dest else ""
        self._replan()
        self._update_summary()
        self._update_buttons()

    def _selected_items(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.items[r] for r in rows]

    def _table_menu(self, pos):
        if self.copier:
            return
        sel = self._selected_items()
        if not sel:
            return
        m = QMenu(self)
        waiting = [it for it in sel if it.status == "Waiting"]
        skipped = [it for it in sel if it.status == "Skipped"]
        if waiting:
            m.addAction(f"Skip {len(waiting):,} selected", lambda: self._set_skipped(waiting, True))
            m.addAction("Move selected to…", lambda: self._move_items(waiting))
            if any(it.override for it in waiting):
                m.addAction("Put back where the options say",
                            lambda: ([setattr(it, "override", "") for it in waiting], self._replan()))
        if skipped:
            m.addAction(f"Include {len(skipped):,} again", lambda: self._set_skipped(skipped, False))
        m.addSeparator()
        m.addAction("Open where it came from", lambda: open_path(sel[0].src.parent))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _reset_aliases(self):
        if not self.aliases:
            QMessageBox.information(self, "Renamed folders", "No folders have been renamed in this library.")
            return
        lines = "\n".join(f"{k.replace('/', ' › ')}  →  {v.rpartition('/')[2]}" for k, v in list(self.aliases.items())[:15])
        if QMessageBox.question(self, "Forget renamed folders",
                                f"Go back to the normal folder names for new files?\n\n{lines}\n\nFolders already "
                                "on the drive keep their names.") != QMessageBox.Yes:
            return
        self.aliases = {}
        if self.dest():
            engine.save_aliases(self.dest(), self.aliases)
        self._replan()

    # ------------------------------------------------------- folder preview
    def _rebuild_tree(self):
        """A tree of the folders the waiting files will go into, with counts and sizes."""
        expanded = set()

        def walk(item, key=()):
            for i in range(item.childCount()):
                ch = item.child(i)
                k = key + (ch.text(0),)
                if ch.isExpanded():
                    expanded.add(k)
                walk(ch, k)

        had_tree = self.tree.topLevelItemCount() > 0 and self.tree.topLevelItem(0).childCount() > 0
        walk(self.tree.invisibleRootItem())
        self.tree.clear()
        waiting = [it for it in self.model.items if it.status == "Waiting"]
        if not waiting:
            done = len(self.model.items)
            msg = ("Nothing waiting. Drop cards or folders above to see where their files will go."
                   if not done else f"All {done:,} files in the list are done. Drop more to add them.")
            QTreeWidgetItem(self.tree, [msg, "", ""])
            return
        root = self.dest()
        nodes: dict[tuple, list] = {}
        for it in waiting:
            parts = tuple(p for p in it.folder.split("/") if p)
            for i in range(1, len(parts) + 1):
                n = nodes.setdefault(parts[:i], [0, 0])
                n[0] += 1
                n[1] += it.size
        top = QTreeWidgetItem(self.tree, [str(root or "Library"), f"{len(waiting):,}",
                                          engine.human_size(sum(it.size for it in waiting))])
        top.setData(0, Qt.UserRole, "")
        widgets = {(): top}
        for key in sorted(nodes):
            cnt, size = nodes[key]
            w = QTreeWidgetItem(widgets[key[:-1]], [key[-1], f"{cnt:,}", engine.human_size(size)])
            w.setData(0, Qt.UserRole, "/".join(key))
            w.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            w.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            widgets[key] = w
            if (str(root or "Library"),) + key in expanded or (not had_tree and len(key) < 2):
                w.setExpanded(True)
        top.setExpanded(True)

    def _open_tree_item(self, item, _col):
        rel = item.data(0, Qt.UserRole)
        d = self.dest()
        if d:
            p = d / rel if rel else d
            while p != d and not p.exists():
                p = p.parent
            if p.exists():
                open_path(p)

    # ------------------------------------------------------- looks and layout
    def _set_theme(self, key):
        self.settings["theme"] = key
        engine.save_settings(self.settings)
        apply_theme(QApplication.instance(), key)

    def _restore_layout(self):
        try:
            g = self.settings.get("geometry")
            if g:
                self.restoreGeometry(QByteArray.fromHex(g.encode()))
            h = self.settings.get("columns")
            if h:
                self.table.horizontalHeader().restoreState(QByteArray.fromHex(h.encode()))
            t = self.settings.get("tree_columns")
            if t:
                self.tree.header().restoreState(QByteArray.fromHex(t.encode()))
            self.tabs.setCurrentIndex(int(self.settings.get("tab", 0)))
        except Exception as e:
            engine.log_error("Restoring the window layout", e)

    def _save_layout(self):
        try:
            self.settings["geometry"] = bytes(self.saveGeometry().toHex()).decode()
            self.settings["columns"] = bytes(self.table.horizontalHeader().saveState().toHex()).decode()
            self.settings["tree_columns"] = bytes(self.tree.header().saveState().toHex()).decode()
            self.settings["tab"] = self.tabs.currentIndex()
        except Exception as e:
            engine.log_error("Saving the window layout", e)

    def notify(self, title: str, text: str, warn: bool = False):
        """Pop-up and sound when long work finishes, so you can walk away."""
        if not self.settings.get("notify", True):
            return
        try:
            if os.name == "nt":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if warn else winsound.MB_ICONASTERISK)
            else:
                QApplication.beep()
        except Exception:
            pass
        QApplication.alert(self)      # flashes the taskbar button if the window is in the background
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                if not self.tray:
                    self.tray = QSystemTrayIcon(self.windowIcon(), self)
                    self.tray.setToolTip(APP_NAME)
                    self.tray.messageClicked.connect(lambda: (self.showNormal(), self.activateWindow()))
                self.tray.show()
                self.tray.showMessage(f"{APP_NAME}: {title}", text,
                                      QSystemTrayIcon.Warning if warn else QSystemTrayIcon.Information, 8000)
        except Exception as e:
            engine.log_error("Showing a notification", e)

    def make_shortcut(self):
        if os.name != "nt":
            QMessageBox.information(self, "Shortcut", "On a Mac, drag “Start Millesime (Mac).command” to the "
                                    "Dock or desktop (hold Cmd+Option while dragging to make an alias).")
            return
        import subprocess
        launcher = engine.APP_DIR / "Start Millesime (Windows).bat"
        icon = engine.APP_DIR / "icon.ico"

        def q(p):
            return "'" + str(p).replace("'", "''") + "'"
        ps = ("$d=[Environment]::GetFolderPath('Desktop');"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Millesime.lnk'));"
              f"$s.TargetPath={q(launcher)};$s.WorkingDirectory={q(engine.APP_DIR)};"
              f"$s.IconLocation={q(icon)};$s.WindowStyle=7;"
              "$s.Description='Sort cards and old drives onto a new drive';$s.Save()")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                QMessageBox.information(self, "Shortcut", "A Millesime shortcut is now on your desktop.")
            else:
                raise RuntimeError((r.stderr or r.stdout or "").strip() or "PowerShell refused")
        except Exception as e:
            engine.log_error("Making a desktop shortcut", e)
            QMessageBox.warning(self, "Shortcut", f"Couldn't make the shortcut:\n\n{e}")

    # ----------------------------------------------------------- health check
    def health_check(self):
        if self.copier or self.healer:
            return
        lib = self.dest()
        if not lib or not lib.exists():
            QMessageBox.warning(self, APP_NAME, "Choose the library folder (and connect its drive) first.")
            return
        last = engine.last_health_check(lib)
        when = f"It was last checked on {last:%b %d, %Y}." if last else "It hasn't been checked before."
        if QMessageBox.question(
                self, "Check the library",
                f"This re-reads every file in {lib} and compares it with the checksum saved when it was "
                f"copied, to catch files the drive has damaged. {when}\n\nIt only reads the library drive "
                "(never your cards), changes nothing, and can take a while on a big library. You can stop "
                "it any time. Start?") != QMessageBox.Yes:
            return
        if not self._take_lock(lib):
            return
        w = self.healer = HealthWorker(lib)
        w.progress.connect(self._health_progress)
        w.finished.connect(self._health_done)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setFormat("Getting ready…")
        self.bar.show()
        self.status.setText("Checking the library…")
        self._update_buttons()
        w.start()

    def _health_progress(self, done, total, name):
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.bar.setFormat(f"Checked {done:,} of {total:,}")
        if name:
            self.status.setText(f"Checking {name}")

    def _health_done(self):
        w = self.healer
        self.healer = None
        self._release_lock()
        self.bar.hide()
        self._update_buttons()
        if w.cancelled:
            self.status.setText("Health check stopped.")
            return
        if w.error:
            self.status.setText("The health check didn't finish.")
            QMessageBox.warning(self, "Health check", f"The health check didn't finish:\n\n{w.error}")
            return
        r = w.result
        bad = len(r["damaged"]) + len(r["unreadable"])
        msg = f"Checked {r['total']:,} files.\n\n{r['ok']:,} are exactly as they were when copied."
        if r["new"]:
            msg += f"\n{r['new']:,} hadn't been checked before; their checksums are saved for next time."
        if r["edited"]:
            msg += f"\n{r['edited']:,} were edited since (new date or size), so their new checksum was saved."
        if r["damaged"]:
            msg += (f"\n\n{len(r['damaged']):,} have changed without being edited. The drive may be failing: "
                    "copy those again from your cards, and consider replacing the drive.")
        if r["unreadable"]:
            msg += f"\n{len(r['unreadable']):,} couldn't be read."
        if r["missing"]:
            msg += f"\n{len(r['missing']):,} that were in the library before are gone (deleted or moved)."
        if not bad:
            msg += "\n\nNo damage found."
        self.notify("Health check finished", "No damage found." if not bad else f"{bad:,} files need a look.",
                    warn=bool(bad))
        self.status.setText(msg.replace("\n\n", "  ").replace("\n", " · "))
        box = QMessageBox(QMessageBox.Warning if bad else QMessageBox.Information, "Health check", msg, parent=self)
        b_rep = box.addButton("Open report", QMessageBox.ActionRole) if (bad or r["missing"]) and r["report"] else None
        box.addButton(QMessageBox.Close)
        box.exec()
        if b_rep is not None and box.clickedButton() is b_rep:
            open_path(r["report"])

    def _update_buttons(self):
        busy = bool(self.copier) or bool(self.healer)
        scanning = bool(self.scanners)
        waiting = any(it.status in ("Waiting", "Error") for it in self.model.items)
        self.b_start.setEnabled(not busy and not scanning and waiting)
        self.b_start.setVisible(not busy)
        self.b_pause.setVisible(bool(self.copier))
        self.b_cancel.setVisible(busy or scanning)
        self.b_clear.setEnabled(not busy and not scanning)
        for wdg in (self.layout_box, self.raw_box, self.camvid_box, self.othervid_box, self.nodate_box,
                    self.tiny_box, *self.includes.values()):
            wdg.setEnabled(not busy)

    def closeEvent(self, e):
        if self.copier:
            if QMessageBox.question(self, APP_NAME, "Copying is still running. Stop and quit? Files already "
                                    "copied are kept; nothing is left half-copied.") != QMessageBox.Yes:
                e.ignore()
                return
            for s in self.scanners:
                s.stop = True
            self.copier.paused = False
            self.copier.stop = True
            self.copier.wait(15000)
        for s in self.scanners:
            s.stop = True
            s.wait(3000)
        if self.healer:
            self.healer.stop = True
            self.healer.wait(5000)
        self._save_layout()
        self._release_lock()
        if self.tray:
            self.tray.hide()
        engine.save_settings(self.settings)
        e.accept()

    # ---------------------------------------------------------------- updates
    def check_updates(self, quiet: bool):
        """Look for a newer version on GitHub. Nothing is downloaded unless the user says yes."""
        if self._upd_check or self._upd_apply:
            return
        w = self._upd_check = UpdateCheckWorker()

        def done():
            self._upd_check = None
            if w.error:
                if not quiet:
                    QMessageBox.information(self, "Check for updates",
                                            f"Couldn't reach GitHub to check for updates.\n\n{w.error}")
                return
            if not w.newer:
                if not quiet:
                    QMessageBox.information(self, "Check for updates", f"You have the latest version ({w.local}).")
                return
            remote = w.rel.get("version", "")
            if self.copier:
                self.status.setText(f"Version {remote} is available. Use Help > Check for updates when "
                                    "copying is finished.")
                return
            box = QMessageBox(QMessageBox.Question, "Update available",
                              f"{APP_NAME} {remote} is available (you have {w.local}).\n\nDownload and install "
                              "it now? Your settings are kept, the download is checked before installing, and "
                              "the current version is backed up (Help > Roll back) in case you want it back.",
                              parent=self)
            notes = (w.rel.get("notes") or "").strip()
            if notes:
                box.setDetailedText(notes)
            b_yes = box.addButton("Update now", QMessageBox.AcceptRole)
            box.addButton("Not now", QMessageBox.RejectRole)
            b_page = box.addButton("What's new…", QMessageBox.HelpRole)
            box.exec()
            if box.clickedButton() is b_page:
                QDesktopServices.openUrl(QUrl(w.rel.get("page") or updater.RELEASES_URL))
            elif box.clickedButton() is b_yes:
                self._apply_update(w.rel)

        w.finished.connect(done)
        w.start()

    def roll_back(self):
        if self._upd_check or self._upd_apply or self.copier:
            return
        versions = updater.backups()
        if not versions:
            QMessageBox.information(self, "Roll back", "There is no earlier version saved yet. One is saved "
                                    "automatically each time you update.")
            return
        ver, ok = QInputDialog.getItem(self, "Roll back to an earlier version",
                                       f"You have {updater.local_version()}. Go back to:", versions, 0, False)
        if ok and ver:
            self._apply_update(None, rollback_to=ver)

    def _apply_update(self, rel=None, rollback_to=""):
        w = self._upd_apply = UpdateApplyWorker(rel, rollback_to)
        w.status.connect(self.status.setText)

        def done():
            self._upd_apply = None
            if w.error:
                self.status.setText("The update didn't finish.")
                QMessageBox.warning(self, "Update failed", f"The update didn't finish:\n\n{w.error}\n\n"
                                    "Nothing was changed. You can try again later.")
                return
            extra = ""
            if w.info.get("requirements_changed"):
                extra = "\n\nThis update needs new parts, which install the next time you start the app."
            self.status.setText(f"Updated to {w.info.get('version')}. Restart to use it.")
            if QMessageBox.question(self, "Updated", f"Updated to {w.info.get('version')}.{extra}\n\n"
                                    "Restart now?") == QMessageBox.Yes:
                self._restart()

        w.finished.connect(done)
        w.start()

    def _restart(self):
        import subprocess
        engine.save_settings(self.settings)
        launcher = engine.APP_DIR / ("Start Millesime (Windows).bat" if os.name == "nt"
                                     else "Start Millesime (Mac).command")
        try:
            if launcher.exists() and (engine.APP_DIR / ".venv").exists():
                if os.name == "nt":
                    subprocess.Popen(["cmd", "/c", str(launcher)], cwd=str(engine.APP_DIR),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    subprocess.Popen(["/bin/bash", str(launcher)], cwd=str(engine.APP_DIR))
            else:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(engine.APP_DIR))
        except Exception as e:
            QMessageBox.warning(self, "Restart", f"Please start {APP_NAME} again yourself.\n\n{e}")
        QApplication.quit()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kefirman9000.millesime")
        except Exception:
            pass
    engine.enable_crash_log()

    def unexpected(kind, value, tb):
        engine.log_error("Unexpected problem", value.with_traceback(tb) if tb else value)

    sys.excepthook = unexpected                  # nothing fails silently: it all goes in the error log
    import threading
    threading.excepthook = lambda a: engine.log_error(f"Unexpected problem in {a.thread.name if a.thread else 'a thread'}",
                                                     a.exc_value)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))
    settings = engine.load_settings()
    apply_theme(app, settings.get("theme", "system"))
    try:   # follow Windows when it switches between light and dark
        app.styleHints().colorSchemeChanged.connect(
            lambda _s: apply_theme(app, engine.load_settings().get("theme", "system"))
            if engine.load_settings().get("theme", "system") == "system" else None)
    except Exception:
        pass
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
