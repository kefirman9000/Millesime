"""
Millésime: drop memory-card folders on the window and it copies every photo and video onto your
destination drive in Year / Month folders. Duplicates are skipped, nothing is ever overwritten,
and your cards are only read, never changed.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QTableView, QVBoxLayout, QWidget)

import engine
import updater

APP_NAME = engine.APP_NAME
SPONSOR_URL = "https://github.com/sponsors/kefirman9000"


def open_path(p: Path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


# --------------------------------------------------------------------------- #
# Background work
# --------------------------------------------------------------------------- #
class ScanWorker(QThread):
    found = Signal(list)          # batches of Items
    status = Signal(str)

    def __init__(self, paths, settings, dest):
        super().__init__()
        self.paths, self.settings, self.dest = paths, dict(settings), dest
        self.stop = False

    def run(self):
        exts = engine.kinds_from_settings(self.settings)
        use_file_date = self.settings.get("no_date") != "undated"
        layout = self.settings.get("layout", "year_month")
        batch, n, last = [], 0, time.monotonic()
        try:
            for it in engine.gather(self.paths, exts, self.dest, lambda: self.stop):
                if self.stop:
                    break
                it.taken, it.date_source = engine.taken_date(it.src, use_file_date)
                it.folder = engine.folder_for(it.taken, layout)
                batch.append(it)
                n += 1
                if time.monotonic() - last > 0.25:
                    self.found.emit(batch)
                    batch, last = [], time.monotonic()
                    self.status.emit(f"Reading dates… {n:,} files")
        except Exception as e:
            engine.log_error("Scanning", e)
            self.status.emit(f"Problem while reading the files: {e}")
        if batch:
            self.found.emit(batch)


class CopyWorker(QThread):
    progress = Signal(int, int, object)      # done, total, bytes done
    status = Signal(str)
    row_done = Signal(int)

    def __init__(self, items, rows, dest):
        super().__init__()
        self.items, self.rows, self.dest = items, rows, dest
        self.stop = False
        self.error = ""
        self.log_path = None
        self.cancelled = False

    def run(self):
        started = datetime.now()
        engine.keep_awake(True)
        cancel = lambda: self.stop
        done_items = []
        try:
            self.status.emit("Checking what's already on the destination drive…")
            index = engine.DestIndex(self.dest)
            index.build(lambda n: self.status.emit(f"Checking what's already on the drive… {n:,} files"), cancel)
            total = len(self.rows)
            done_bytes = 0
            for k, r in enumerate(self.rows):
                if self.stop:
                    self.cancelled = True
                    break
                it = self.items[r]
                self.status.emit(f"Copying {it.src.name}  →  {it.folder}")
                try:
                    engine.process(it, index, cancel)
                except engine.Cancelled:
                    it.status, it.note = "Waiting", ""
                    self.cancelled = True
                    break
                except Exception as e:
                    engine.log_error(f"Copying {it.src}", e)
                    it.status, it.note = "Error", str(e)
                done_items.append(it)
                done_bytes += it.size
                self.row_done.emit(r)
                self.progress.emit(k + 1, total, done_bytes)
                if (k + 1) % 200 == 0:
                    index.save()
            index.save()
        except engine.Cancelled:
            self.cancelled = True
        except Exception as e:
            engine.log_error("Copying", e)
            self.error = str(e)
        finally:
            engine.keep_awake(False)
            if done_items:
                self.log_path = engine.write_log(self.dest, done_items, started)


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
                 "Error": "#d23c3c"}


class ItemModel(QAbstractTableModel):
    HEAD = ["File", "Taken", "Goes to", "Result", "Note"]

    def __init__(self):
        super().__init__()
        self.items: list[engine.Item] = []

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
                if not it.taken:
                    return "no date"
                t = it.taken.strftime("%Y-%m-%d %H:%M")
                return t + ("  (file date)" if it.date_source == "file date" else "")
            if c == 2:
                return (it.dest or it.folder).replace("/", " \\ ").replace(os.sep, " \\ ")
            if c == 3:
                return it.status
            if c == 4:
                return it.note
        if role == Qt.ToolTipRole:
            return f"{it.src}\n{engine.human_size(it.size)}" + (
                "\nWith: " + ", ".join(p.name for p in it.sidecars) if it.sidecars else "")
        if role == Qt.ForegroundRole and c == 3 and it.status in STATUS_COLORS:
            return QColor(STATUS_COLORS[it.status])
        return None

    def add(self, batch):
        if not batch:
            return
        n = len(self.items)
        self.beginInsertRows(QModelIndex(), n, n + len(batch) - 1)
        self.items.extend(batch)
        self.endInsertRows()

    def refresh_row(self, r):
        self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.HEAD) - 1))

    def refresh_all(self):
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(self.HEAD) - 1))

    def clear(self):
        self.beginResetModel()
        self.items = []
        self.endResetModel()


class DropZone(QFrame):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(130)
        lay = QVBoxLayout(self)
        self.label = QLabel("Drop memory card folders, or files, here\n\n"
                            "They're only read. Nothing on the card is changed.")
        self.label.setAlignment(Qt.AlignCenter)
        f = self.label.font()
        f.setPointSize(f.pointSize() + 3)
        self.label.setFont(f)
        lay.addWidget(self.label)
        self._style(False)

    def _style(self, hot):
        self.setStyleSheet("DropZone { border: 2px dashed %s; border-radius: 12px; %s }"
                           % ("#2b7bd6" if hot else "#888", "background: rgba(43,123,214,0.12);" if hot else ""))

    def dragEnterEvent(self, e):
        if self.isEnabled() and e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, e):
        self._style(False)

    def dropEvent(self, e):
        self._style(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = engine.load_settings()
        self.scanners: list[ScanWorker] = []
        self.copier: CopyWorker | None = None
        self._upd_check = None
        self._upd_apply = None
        self._copy_t0 = 0.0
        self.setWindowTitle(f"{APP_NAME} — sort photos onto a drive by date")
        self.resize(1100, 720)
        self._menus()

        w = QWidget()
        v = QVBoxLayout(w)

        # destination
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Destination drive / folder:</b>"))
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

        # options
        row = QHBoxLayout()
        row.addWidget(QLabel("Folders:"))
        self.layout_box = QComboBox()
        for k, label in engine.LAYOUTS.items():
            self.layout_box.addItem(label, k)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(self.settings["layout"])))
        self.layout_box.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.layout_box)
        row.addSpacing(16)
        row.addWidget(QLabel("Include:"))
        self.c_raw = QCheckBox("RAW")
        self.c_photos = QCheckBox("JPEG / HEIC / other photos")
        self.c_videos = QCheckBox("Videos")
        for c, k in ((self.c_raw, "raw"), (self.c_photos, "photos"), (self.c_videos, "videos")):
            c.setChecked(self.settings.get(k, True))
            c.toggled.connect(self._options_changed)
            row.addWidget(c)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("If a file has no date inside it:"))
        self.nodate_box = QComboBox()
        self.nodate_box.addItem("Use the file's date (what the camera saved)", "file_date")
        self.nodate_box.addItem("Put it in an \"Undated\" folder", "undated")
        self.nodate_box.setCurrentIndex(max(0, self.nodate_box.findData(self.settings["no_date"])))
        self.nodate_box.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.nodate_box)
        row.addSpacing(16)
        self.c_auto = QCheckBox("Start copying as soon as I drop files")
        self.c_auto.setChecked(self.settings.get("auto_start", False))
        self.c_auto.toggled.connect(self._options_changed)
        row.addWidget(self.c_auto)
        row.addStretch(1)
        v.addLayout(row)

        self.drop = DropZone()
        self.drop.dropped.connect(self.add_paths)
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
        for col, wd in enumerate((220, 190, 300, 120)):
            self.table.setColumnWidth(col, wd)
        self.table.doubleClicked.connect(self._open_row)
        v.addWidget(self.table, 1)

        self.bar = QProgressBar()
        self.bar.hide()
        v.addWidget(self.bar)
        self.status = QLabel("")
        v.addWidget(self.status)
        self.setCentralWidget(w)

        self._show_dest()
        self._update_buttons()
        if self.settings.get("check_updates", True):
            QTimer.singleShot(2500, lambda: self.check_updates(quiet=True))

    # ----------------------------------------------------------------- menus
    def _menus(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Choose destination…", self)
        a.triggered.connect(self.choose_dest)
        m.addAction(a)
        a = QAction("Add a folder…", self)
        a.triggered.connect(self.add_folder)
        m.addAction(a)
        a = QAction("Open sort logs (what happened to every file)…", self)
        a.triggered.connect(self._open_logs)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.triggered.connect(self.close)
        m.addAction(a)

        m = self.menuBar().addMenu("&Help")
        a = QAction("How it works", self)
        a.triggered.connect(self._how)
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
            self, APP_NAME, f"<b>{APP_NAME}</b> {updater.local_version()}<br>Copies photos and videos from "
            f"memory cards onto a drive in date folders, without duplicates and without overwriting "
            f"anything.<br><br><a href='{updater.PAGE_URL}'>{updater.PAGE_URL}</a><br>"
            f"Enjoying it? <a href='{SPONSOR_URL}'>Support development on GitHub Sponsors</a>."))
        m.addAction(a)

    def _how(self):
        QMessageBox.information(self, "How it works", (
            "1. Choose the destination drive (or a folder on it).\n"
            "2. Drop memory card folders or files on the window.\n"
            "3. Check the list, then press Start copying.\n\n"
            "• Files go into folders by the date the picture was taken (read from inside the photo or "
            "video), e.g. 2019 \\ 07 - July.\n"
            "• A file that's exactly the same as one already on the drive (byte for byte) is skipped.\n"
            "• A different picture with the same name is saved with a number added: IMG_0001_1.CR2.\n"
            "• Nothing on the drive is ever overwritten, and nothing on your cards is changed or deleted.\n"
            "• Every copy is checked against the original before it's kept.\n"
            "• .xmp and other sidecar files travel with their photo.\n"
            "• A log of what happened to every file is saved on the drive (File > Open sort logs)."))

    # ------------------------------------------------------------ destination
    def dest(self) -> Path | None:
        d = self.settings.get("destination") or ""
        return Path(d) if d else None

    def choose_dest(self):
        if self.copier:
            return
        start = str(self.dest() or Path.home())
        d = QFileDialog.getExistingDirectory(self, "Where should the photos go?", start)
        if d:
            self.settings["destination"] = d
            engine.save_settings(self.settings)
            self._show_dest()
            self._relayout()

    def _show_dest(self):
        d = self.dest()
        if not d:
            self.dest_label.setText("<i>not chosen yet — press Choose…</i>")
        elif not d.exists():
            self.dest_label.setText(f"{d}   <span style='color:#d23c3c'>(not connected)</span>")
        else:
            try:
                free = engine.human_size(__import__("shutil").disk_usage(d).free)
                self.dest_label.setText(f"{d}   <span style='color:gray'>({free} free)</span>")
            except OSError:
                self.dest_label.setText(str(d))
        self.b_open_dest.setEnabled(bool(d and d.exists()))

    # ---------------------------------------------------------------- options
    def _options_changed(self):
        old = (self.settings.get("raw"), self.settings.get("photos"), self.settings.get("videos"),
               self.settings.get("no_date"))
        self.settings.update(layout=self.layout_box.currentData(), raw=self.c_raw.isChecked(),
                             photos=self.c_photos.isChecked(), videos=self.c_videos.isChecked(),
                             no_date=self.nodate_box.currentData(), auto_start=self.c_auto.isChecked())
        engine.save_settings(self.settings)
        new = (self.settings["raw"], self.settings["photos"], self.settings["videos"], self.settings["no_date"])
        if new != old and self.model.items and not self.copier:
            self.status.setText("Settings changed. Drop the folders again to use them for this list.")
        self._relayout()

    def _relayout(self):
        """Update where waiting files will go after the folder layout changes."""
        if self.copier:
            return
        layout = self.settings.get("layout", "year_month")
        for it in self.model.items:
            if it.status == "Waiting":
                it.folder = engine.folder_for(it.taken, layout)
        self.model.refresh_all()

    # ------------------------------------------------------------------ files
    def add_folder(self):
        if self.copier:
            return
        d = QFileDialog.getExistingDirectory(self, "Choose a memory card or folder to sort")
        if d:
            self.add_paths([Path(d)])

    def add_paths(self, paths):
        if self.copier:
            self.status.setText("Copying is running. Drop more when it's finished.")
            return
        dest = self.dest()
        if not dest:
            QMessageBox.information(self, APP_NAME, "First choose the destination drive (Choose… at the top).")
            self.choose_dest()
            dest = self.dest()
            if not dest:
                return
        known = {str(it.src) for it in self.model.items}
        s = ScanWorker(paths, self.settings, dest)
        s.found.connect(lambda batch: (self.model.add([b for b in batch if str(b.src) not in known]),
                                       self._update_summary()))
        s.status.connect(self.status.setText)
        s.finished.connect(lambda: self._scan_done(s))
        self.scanners.append(s)
        self.status.setText("Looking for photos and videos…")
        self._update_buttons()
        s.start()

    def _scan_done(self, s):
        if s in self.scanners:
            self.scanners.remove(s)
        self._update_summary()
        self._update_buttons()
        if self.scanners:
            return
        waiting = sum(1 for it in self.model.items if it.status == "Waiting")
        if not waiting:
            self.status.setText("No photos or videos found there (check the Include boxes above).")
            return
        self.status.setText(f"{waiting:,} files ready. Check the list, then press Start copying.")
        if self.settings.get("auto_start"):
            self.start()

    def _update_summary(self):
        items = self.model.items
        if not items:
            self.summary.setText("")
            return
        waiting = [it for it in items if it.status == "Waiting"]
        size = sum(it.size for it in waiting)
        years = sorted({it.taken.year for it in waiting if it.taken})
        span = f" · {years[0]}–{years[-1]}" if len(years) > 1 else (f" · {years[0]}" if years else "")
        undated = sum(1 for it in waiting if it.date_source in ("file date", "none"))
        extra = f" · {undated:,} without a date inside" if undated else ""
        done = len(items) - len(waiting)
        self.summary.setText(f"  {len(waiting):,} to copy ({engine.human_size(size)}){span}{extra}"
                             + (f" · {done:,} done" if done else ""))

    def clear(self):
        if self.copier or self.scanners:
            return
        self.model.clear()
        self._update_summary()
        self.status.setText("")
        self._update_buttons()

    def _open_row(self, idx):
        it = self.model.items[idx.row()]
        d = self.dest()
        if it.dest and d and (d / it.dest).exists():
            open_path((d / it.dest).parent)
        else:
            open_path(it.src.parent)

    def _open_logs(self):
        d = self.dest()
        p = d / engine.META_NAME / engine.LOGS_NAME if d else None
        if p and p.is_dir():
            open_path(p)
        else:
            QMessageBox.information(self, "Sort logs", "No sort logs on this destination yet.")

    # ---------------------------------------------------------------- copying
    def start(self):
        if self.copier or self.scanners:
            return
        dest = self.dest()
        if not dest or not dest.exists():
            QMessageBox.warning(self, APP_NAME, "The destination drive isn't connected (or not chosen). "
                                "Plug it in or press Choose….")
            return
        rows = [r for r, it in enumerate(self.model.items) if it.status in ("Waiting", "Error")]
        if not rows:
            self.status.setText("Nothing waiting to copy.")
            return
        need = sum(self.model.items[r].size for r in rows)
        try:
            import shutil
            free = shutil.disk_usage(dest).free
            if need > free:
                if QMessageBox.question(
                        self, "Not enough space?",
                        f"These files are {engine.human_size(need)} and the drive has {engine.human_size(free)} "
                        "free. Duplicates aren't copied, so it may still fit. Start anyway? (It stops safely "
                        "if the drive fills up.)") != QMessageBox.Yes:
                    return
        except OSError:
            pass
        for r in rows:
            it = self.model.items[r]
            it.status, it.note, it.dest = "Waiting", "", ""
        self.model.refresh_all()
        c = self.copier = CopyWorker(self.model.items, rows, dest)
        c.status.connect(self.status.setText)
        c.row_done.connect(self._row_done)
        c.progress.connect(self._progress)
        c.finished.connect(self._copy_done)
        self.bar.setRange(0, len(rows))
        self.bar.setValue(0)
        self.bar.show()
        self._copy_t0 = time.monotonic()
        self._update_buttons()
        c.start()

    def _row_done(self, r):
        self.model.refresh_row(r)
        self.table.scrollTo(self.model.index(r, 0))

    def _progress(self, done, total, done_bytes):
        self.bar.setValue(done)
        secs = max(0.1, time.monotonic() - self._copy_t0)
        rate = done_bytes / secs
        left = ""
        if done and done < total:
            remain = (total - done) * secs / done
            left = f" · about {int(remain // 60)} min {int(remain % 60)} s left"
        self.bar.setFormat(f"{done:,} of {total:,} · {engine.human_size(rate)}/s{left}")

    def cancel(self):
        if self.copier:
            self.copier.stop = True
            self.status.setText("Stopping after the current file… (nothing half-copied is left behind)")
        for s in self.scanners:
            s.stop = True

    def _copy_done(self):
        c = self.copier
        self.copier = None
        self.bar.hide()
        self._update_buttons()
        self._update_summary()
        self._show_dest()
        done = [it for it in self.model.items if it.status != "Waiting"]
        counts = {}
        for it in done:
            counts[it.status] = counts.get(it.status, 0) + 1
        copied = counts.get("Copied", 0)
        renamed = counts.get("Copied (renamed)", 0)
        dupes = counts.get("Duplicate", 0)
        errors = counts.get("Error", 0)
        head = "Stopped." if c.cancelled else ("Something went wrong." if c.error else "All done.")
        msg = (f"{head}\n\n{copied + renamed:,} copied"
               + (f" ({renamed:,} given a number because a different file had the same name)" if renamed else "")
               + f"\n{dupes:,} skipped because they were already on the drive")
        if errors:
            msg += f"\n{errors:,} couldn't be copied (see the Note column; press Start copying to retry them)"
        if c.error:
            msg += f"\n\n{c.error}"
        if c.cancelled:
            msg += "\n\nPress Start copying to carry on where it stopped."
        self.status.setText(msg.replace("\n\n", "  ").replace("\n", " · "))
        box = QMessageBox(QMessageBox.Information if not (errors or c.error) else QMessageBox.Warning,
                          APP_NAME, msg, parent=self)
        b_open = box.addButton("Open destination", QMessageBox.ActionRole)
        b_log = box.addButton("Open log", QMessageBox.ActionRole) if c.log_path else None
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is b_open and self.dest():
            open_path(self.dest())
        elif b_log is not None and box.clickedButton() is b_log:
            open_path(c.log_path)

    def _update_buttons(self):
        busy = bool(self.copier)
        scanning = bool(self.scanners)
        waiting = any(it.status in ("Waiting", "Error") for it in self.model.items)
        self.b_start.setEnabled(not busy and not scanning and waiting)
        self.b_start.setVisible(not busy)
        self.b_cancel.setVisible(busy or scanning)
        self.b_clear.setEnabled(not busy and not scanning)
        self.drop.setEnabled(not busy)
        for wdg in (self.layout_box, self.c_raw, self.c_photos, self.c_videos, self.nodate_box):
            wdg.setEnabled(not busy)

    def closeEvent(self, e):
        if self.copier:
            if QMessageBox.question(self, APP_NAME, "Copying is still running. Stop and quit? Files already "
                                    "copied are kept; nothing is left half-copied.") != QMessageBox.Yes:
                e.ignore()
                return
            self.copier.stop = True
            self.copier.wait(15000)
        for s in self.scanners:
            s.stop = True
            s.wait(3000)
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
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
