"""
Millésime engine: finds photos and videos, works out when each was taken, and copies them into
date folders on the destination drive.

Safety rules (these never change):
  * Source files (your memory cards) are only ever read. Nothing is moved, renamed or deleted there.
  * Nothing on the destination is ever overwritten. A different file with the same name is saved
    with a number added (IMG_0001_1.CR2, IMG_0001_2.CR2, ...).
  * A file counts as a duplicate only when it is byte-for-byte identical to one already on the
    destination (same size and same checksum). Same name or same picture at another size is NOT
    a duplicate. Duplicates are skipped, not copied twice.
  * Every copy is written to a temporary name, checked against the original's checksum, and only
    then given its real name. A copy that doesn't match is removed and reported as an error.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "Millésime"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "errors.log"
META_NAME = ".millesime"          # folder on the destination holding the index and sort logs
INDEX_NAME = "index.json"
LOGS_NAME = "logs"
PART_SUFFIX = ".millesime-part"

RAW_EXT = {".3fr", ".ari", ".arw", ".bay", ".cr2", ".cr3", ".crw", ".dcr", ".dcs", ".dng", ".erf",
           ".fff", ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".pef",
           ".raf", ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f", ".gpr"}
PHOTO_EXT = {".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".hif", ".png", ".tif", ".tiff", ".webp",
             ".gif", ".bmp", ".avif", ".jxl"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mts", ".m2ts", ".mkv", ".3gp", ".mpg", ".mpeg",
             ".wmv", ".insv", ".360", ".lrv"}
MUSIC_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma", ".wav", ".aiff",
             ".aif", ".alac", ".ape", ".wv", ".mka", ".dsf", ".dff", ".mpc"}
DOC_GROUPS = {
    "PDFs": {".pdf"},
    "Word & Text": {".doc", ".docx", ".odt", ".rtf", ".pages", ".txt", ".md", ".wpd"},
    "Spreadsheets": {".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Ebooks": {".epub", ".mobi", ".azw", ".azw3", ".fb2", ".djvu"},
}
DOC_EXT = set().union(*DOC_GROUPS.values())
# travel with the file they belong to (same name), never sorted on their own
SIDECAR_EXT = {".xmp", ".thm", ".aae", ".pp3", ".dop", ".on1"}
# never copied, even with "Everything else" on
JUNK_EXT = {".tmp", ".lnk", ".ini", ".db", ".ctg", ".bdm", ".cpi", ".mpl", ".bnp", ".ind", ".inp",
            ".log", ".dat", ".sys", ".lck", ".part", PART_SUFFIX}

# folders that mean "this came off a camera"
CAMERA_DIRS = {"DCIM", "AVCHD", "PRIVATE", "M4ROOT", "MP_ROOT", "CLIP", "STREAM", "XDROOT", "BDMV",
               "100MEDIA"}
# file names cameras and phones give their videos
CAMERA_NAME_RE = re.compile(
    r"^(DSC|DSCF|DSCN|_DSC|MVI_|MOV_?\d|M2U\d|MAH\d|MAQ\d|C\d{4}|GOPR|G[HXPL]\d|DJI_|PXL_|VID_?\d|"
    r"VID_|IMG_|P\d{7}|PICT|HPIM|CIMG|SAM_|SDC|PANA|MP4_|LRV_|\d{5}\.MTS$|\d{5}\.M2TS$|"
    r"\d{8}_\d{6}|VIDEO\d|CLIP\d|MAX_|INSTA|LRV)", re.I)
# pictures that are album covers when they sit next to music
ART_STEMS = {"cover", "folder", "front", "back", "albumart", "albumartsmall", "album", "artwork"}

LIB = {"photo": "Photos", "video": "Videos", "music": "Music", "doc": "Documents", "other": "Other"}

SKIP_DIRS = {"system volume information", "$recycle.bin", ".trashes", ".spotlight-v100",
             ".fseventsd", ".temporaryitems", META_NAME}
SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}

LAYOUTS = {
    "year_month": "Year › Month   (2019 \\ 07 - July)",
    "year_month_day": "Year › Month › Day   (2019 \\ 07 - July \\ 2019-07-04)",
    "year_monthname": "Year › Month   (2019 \\ 2019-07)",
    "year": "Year only   (2019)",
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
UNDATED = "Undated"

RAW_SPLIT = {
    "together": "RAW and JPEG together",
    "split": "RAW and JPEG in separate RAW / JPEG folders",
}
CAMERA_VIDEOS = {
    "photos_sub": "With their photos, in a Videos folder   (Photos \\ 2019 \\ 07 - July \\ Videos)",
    "photos_mixed": "With their photos, mixed in   (Photos \\ 2019 \\ 07 - July)",
    "videos": "In the Videos folder by date   (Videos \\ 2019 \\ 07 - July)",
}
OTHER_VIDEOS = {
    "by_date": "Videos folder by date   (Videos \\ 2019 \\ 07 - July)",
    "flat": "Videos folder, all together   (Videos)",
}

DEFAULT_SETTINGS = {
    "destination": "",
    "layout": "year_month",
    "photos": True,               # photos and RAW
    "videos": True,
    "music": True,
    "docs": False,
    "other": False,
    "raw_split": "together",
    "camera_videos": "photos_sub",
    "other_videos": "by_date",
    "no_date": "file_date",       # or "undated"
    "auto_start": False,
    "check_updates": True,
}


# --------------------------------------------------------------------------- #
# Settings and error log
# --------------------------------------------------------------------------- #
def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
    except Exception:
        pass
    return s


def save_settings(s: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2), "utf-8")
        tmp.replace(SETTINGS_FILE)
    except Exception as e:
        log_error("Saving settings", e)


def log_error(where: str, e: BaseException | str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        detail = "".join(traceback.format_exception(e)) if isinstance(e, BaseException) else str(e)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{detail}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Finding files
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    src: Path
    size: int
    kind: str = ""                 # raw, photo, video, music, art, doc, other
    camera: bool = False           # videos: came off a camera or phone
    sidecars: list[Path] = field(default_factory=list)
    taken: datetime | None = None
    date_source: str = ""          # "photo", "video", "file date", "none"
    artist: str = ""
    album: str = ""
    folder: str = ""               # relative folder on the destination
    status: str = "Waiting"
    dest: str = ""                 # where it ended up (or the existing copy, for duplicates)
    note: str = ""


def kind_of(ext: str) -> str:
    if ext in RAW_EXT:
        return "raw"
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if ext in MUSIC_EXT:
        return "music"
    if ext in DOC_EXT:
        return "doc"
    return "other"


def wanted(kind: str, s: dict) -> bool:
    return {"raw": s.get("photos", True), "photo": s.get("photos", True), "video": s.get("videos", True),
            "music": s.get("music", True), "art": s.get("music", True), "doc": s.get("docs", False),
            "other": s.get("other", False)}[kind]


def _inside(p: Path, root: Path | None) -> bool:
    if not root:
        return False
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def gather(paths: list[Path], settings: dict, dest_root: Path | None = None, cancel=lambda: False):
    """Yield Items for every wanted file under the given files/folders. Sidecars (.xmp etc.) are
    attached to the file they belong to. Anything already inside the destination is skipped."""
    seen: set[str] = set()
    for p in paths:
        p = Path(p)
        if _inside(p, dest_root):
            continue
        if p.is_file():
            try:
                listing = os.listdir(p.parent)
            except OSError:
                listing = [p.name]
            dirs = [(p.parent, [p.name], listing)]
        elif p.is_dir():
            dirs = []
            for root, dnames, fnames in os.walk(p):
                if cancel():
                    return
                dnames[:] = [d for d in dnames if d.lower() not in SKIP_DIRS and not d.startswith("._")]
                if _inside(Path(root), dest_root):
                    dnames[:] = []
                    continue
                dirs.append((Path(root), fnames, fnames))
        else:
            continue
        for d, names, listing in dirs:
            cars_by_key: dict[str, list[str]] = {}
            stems_with_raw: set[str] = set()
            has_music = has_camera_photo = False
            for n in listing:
                low = n.lower()
                stem, ext = os.path.splitext(low)
                if ext in SIDECAR_EXT:
                    # IMG_1.xmp belongs to IMG_1.*, IMG_1.CR2.xmp belongs to IMG_1.CR2
                    cars_by_key.setdefault(stem, []).append(n)
                elif ext in RAW_EXT:
                    stems_with_raw.add(stem)
                    has_camera_photo = True
                elif ext in MUSIC_EXT:
                    has_music = True
                elif ext in PHOTO_EXT and CAMERA_NAME_RE.match(n):
                    has_camera_photo = True
            on_card = any(part.upper() in CAMERA_DIRS for part in d.parts)
            # songs before pictures, so album covers can follow the songs next to them
            for n in sorted(names, key=lambda x: (os.path.splitext(x.lower())[1] in PHOTO_EXT, x.lower())):
                if cancel():
                    return
                low = n.lower()
                if low in SKIP_FILES or n.startswith(("._", "~$")):
                    continue
                stem, ext = os.path.splitext(low)
                if ext in SIDECAR_EXT or ext in JUNK_EXT or low.endswith(PART_SUFFIX):
                    continue
                kind = kind_of(ext)
                if kind == "photo" and has_music and (stem in ART_STEMS or stem.startswith("albumart")):
                    kind = "art"
                if not wanted(kind, settings):
                    continue
                f = d / n
                key = os.path.normcase(os.path.abspath(f))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                cars = list(cars_by_key.get(low, []))
                # IMG_1.xmp next to both IMG_1.CR2 and IMG_1.JPG goes with the RAW
                if ext in RAW_EXT or stem not in stems_with_raw:
                    cars += cars_by_key.get(stem, [])
                it = Item(src=f, size=size, kind=kind, sidecars=[d / c for c in sorted(set(cars))])
                if kind == "video":
                    it.camera = bool(on_card or has_camera_photo or CAMERA_NAME_RE.match(n))
                yield it


# --------------------------------------------------------------------------- #
# When was it taken?
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(rb"((?:19[89]\d|20[0-9]\d)):(0[1-9]|1[0-2]):(0[1-9]|[12]\d|3[01])[ T]"
                      rb"([01]\d|2[0-3]):([0-5]\d):([0-5]\d)")
_SCAN_BYTES = 4 * 1024 * 1024


def _plausible(dt: datetime) -> bool:
    return datetime(1980, 1, 1) <= dt <= datetime.now().replace(year=datetime.now().year + 1)


def _exifread_date(path: Path) -> datetime | None:
    try:
        import exifread  # type: ignore
    except Exception:
        return None
    try:
        with path.open("rb") as f:
            tags = exifread.process_file(f, details=False, stop_tag="DateTimeOriginal")
        for t in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
            v = tags.get(t)
            if v:
                m = _DATE_RE.search(str(v).encode())
                if m:
                    dt = datetime(*map(int, m.groups()))
                    if _plausible(dt):
                        return dt
    except Exception:
        pass
    return None


def _scan_date(path: Path) -> datetime | None:
    """EXIF dates are stored as text ("2019:07:04 15:30:12") near the start of almost every RAW
    and JPEG (CR3, RAF and others included), so this finds them even where EXIF readers can't."""
    try:
        with path.open("rb") as f:
            blob = f.read(_SCAN_BYTES)
    except OSError:
        return None
    found = []
    for m in _DATE_RE.finditer(blob):
        try:
            dt = datetime(*map(int, m.groups()))
        except ValueError:
            continue
        if _plausible(dt):
            found.append(dt)
    if not found:
        return None
    # the original capture date is the earliest; later ones come from editing software
    return min(found)


def _mp4_date_fixed(path: Path) -> datetime | None:
    raw = _mp4_secs(path)
    if not raw:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromtimestamp(raw - 2082844800, tz=timezone.utc).astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None
    return dt if _plausible(dt) else None


def _mp4_secs(path: Path) -> int:
    try:
        with path.open("rb") as f:
            stop_all = path.stat().st_size

            def boxes(start, stop):
                pos = start
                while pos + 8 <= stop:
                    f.seek(pos)
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        return
                    size, kind = struct.unpack(">I4s", hdr)
                    head = 8
                    if size == 1:
                        size = struct.unpack(">Q", f.read(8))[0]
                        head = 16
                    elif size == 0:
                        size = stop - pos
                    if size < head:
                        return
                    yield kind, pos + head, pos + size
                    pos += size

            for kind, s, e in boxes(0, stop_all):
                if kind == b"moov":
                    for k2, s2, _e2 in boxes(s, e):
                        if k2 == b"mvhd":
                            f.seek(s2)
                            ver = f.read(4)[0]
                            if ver == 1:
                                return struct.unpack(">Q", f.read(8))[0]
                            return struct.unpack(">I", f.read(4))[0]
                    return 0
    except Exception:
        return 0
    return 0


def taken_date(path: Path, use_file_date: bool = True) -> tuple[datetime | None, str]:
    ext = path.suffix.lower()
    dt = None
    if ext in VIDEO_EXT:
        if ext in {".mp4", ".mov", ".m4v", ".3gp", ".insv", ".lrv", ".360"}:
            dt = _mp4_date_fixed(path)
        if not dt:
            dt = _scan_date(path)
        if dt:
            return dt, "video"
    else:
        dt = _exifread_date(path) or _scan_date(path)
        if dt:
            return dt, "photo"
    if use_file_date:
        try:
            st = path.stat()
            # cameras write the file's modified time when the picture is taken
            return datetime.fromtimestamp(st.st_mtime), "file date"
        except OSError:
            pass
    return None, "none"


def date_folder(dt: datetime | None, layout: str) -> str:
    if dt is None:
        return UNDATED
    y, m, d = f"{dt.year}", f"{dt.month:02d}", f"{dt.day:02d}"
    if layout == "year":
        return y
    if layout == "year_monthname":
        return f"{y}/{y}-{m}"
    month = f"{m} - {MONTHS[dt.month - 1]}"
    if layout == "year_month_day":
        return f"{y}/{month}/{y}-{m}-{d}"
    return f"{y}/{month}"


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(s: str, fallback: str) -> str:
    """A folder name Windows accepts."""
    s = _BAD_CHARS.sub("_", (s or "").strip())
    s = re.sub(r"\s+", " ", s)[:80].strip(" .")
    if not s or s.upper() in _RESERVED:
        return fallback
    return s


def read_music_tags(path: Path) -> tuple[str, str]:
    """(artist, album) from the song's tags, preferring the album artist so albums stay together."""
    try:
        import mutagen  # type: ignore
        f = mutagen.File(path, easy=True)
    except Exception:
        return "", ""
    if not f or not getattr(f, "tags", None):
        return "", ""

    def first(*keys):
        for k in keys:
            try:
                v = f.tags.get(k)
            except Exception:
                v = None
            if v:
                v = v[0] if isinstance(v, list) else v
                if str(v).strip():
                    return str(v).strip()
        return ""

    artist = first("albumartist", "album artist", "WM/AlbumArtist", "artist", "Author")
    album = first("album", "WM/AlbumTitle")
    if not first("albumartist", "album artist", "WM/AlbumArtist") and first("compilation") in ("1", "True", "true"):
        artist = "Various Artists"
    return artist, album


def read_info(it: Item, settings: dict) -> None:
    """Fill in what a file's folder depends on (the date, or artist and album)."""
    if it.kind in ("raw", "photo", "video"):
        it.taken, it.date_source = taken_date(it.src, settings.get("no_date") != "undated")
    elif it.kind == "music":
        it.artist, it.album = read_music_tags(it.src)
        if not it.album:
            # untagged songs stay together under the name of the folder they were in
            parent = it.src.parent.name
            if parent and parent.lower() not in ("music", "my music", "songs", "itunes", "downloads", "desktop"):
                it.album = parent


def plan(items: list[Item], s: dict, album_of_dir: dict | None = None) -> None:
    """Decide the folder for every waiting item from the current options. Pass the same
    album_of_dir between batches so album covers find the songs they were next to."""
    layout = s.get("layout", "year_month")
    if album_of_dir is None:
        album_of_dir = {}
    for it in items:
        if it.status != "Waiting":
            continue
        if it.kind in ("raw", "photo"):
            f = f"{LIB['photo']}/{date_folder(it.taken, layout)}"
            if s.get("raw_split") == "split":
                f += "/RAW" if it.kind == "raw" else "/JPEG"
        elif it.kind == "video":
            mode = s.get("camera_videos", "photos_sub") if it.camera else None
            if mode == "photos_sub":
                f = f"{LIB['photo']}/{date_folder(it.taken, layout)}/Videos"
            elif mode == "photos_mixed":
                f = f"{LIB['photo']}/{date_folder(it.taken, layout)}"
            elif mode == "videos" or s.get("other_videos", "by_date") == "by_date":
                f = f"{LIB['video']}/{date_folder(it.taken, layout)}"
            else:
                f = LIB["video"]
        elif it.kind == "music":
            f = (f"{LIB['music']}/{safe_name(it.artist, 'Unknown Artist')}/"
                 f"{safe_name(it.album, 'Unknown Album')}")
            album_of_dir.setdefault(it.src.parent, f)
        elif it.kind == "doc":
            ext = it.src.suffix.lower()
            f = f"{LIB['doc']}/" + next(g for g, e in DOC_GROUPS.items() if ext in e)
        elif it.kind == "other":
            ext = it.src.suffix.lstrip(".").upper()
            f = f"{LIB['other']}/{safe_name(ext, 'No extension') if ext else 'No extension'}"
        else:
            continue
        it.folder = f
    for it in items:   # album covers go with the songs they were next to
        if it.status == "Waiting" and it.kind == "art":
            it.folder = album_of_dir.get(it.src.parent, f"{LIB['music']}/Unknown Artist/Unknown Album")


KIND_LABEL = {"raw": "RAW", "photo": "Photo", "video": "Video", "music": "Music", "art": "Album cover",
              "doc": "Document", "other": "Other"}


# --------------------------------------------------------------------------- #
# Checksums and what's already on the destination
# --------------------------------------------------------------------------- #
CHUNK = 4 * 1024 * 1024


def file_hash(path: Path, cancel=lambda: False) -> str:
    h = hashlib.blake2b(digest_size=20)
    with path.open("rb") as f:
        while True:
            if cancel():
                raise Cancelled()
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Cancelled(Exception):
    pass


class DestIndex:
    """Every file on the destination by size, with checksums worked out only when needed and
    remembered in .millesime/index.json so the next run is fast."""

    def __init__(self, root: Path):
        self.root = root
        self.meta = root / META_NAME
        self.file = self.meta / INDEX_NAME
        self.by_size: dict[int, list[str]] = {}
        self.info: dict[str, list] = {}       # rel -> [size, mtime_ns, hash or ""]
        self.taken: set[str] = set()           # lower-cased rel paths (names in use)

    def build(self, progress=lambda n: None, cancel=lambda: False):
        cached: dict[str, list] = {}
        try:
            cached = json.loads(self.file.read_text("utf-8")).get("files", {})
        except Exception:
            pass
        n = 0
        for root, dnames, fnames in os.walk(self.root):
            if cancel():
                raise Cancelled()
            dnames[:] = [d for d in dnames if d.lower() not in SKIP_DIRS]
            for fn in fnames:
                if fn.endswith(PART_SUFFIX):
                    continue
                full = Path(root) / fn
                rel = full.relative_to(self.root).as_posix()
                try:
                    st = full.stat()
                except OSError:
                    continue
                c = cached.get(rel)
                h = c[2] if c and c[0] == st.st_size and c[1] == st.st_mtime_ns else ""
                self._add(rel, st.st_size, st.st_mtime_ns, h)
                n += 1
                if n % 500 == 0:
                    progress(n)
        progress(n)

    def _add(self, rel: str, size: int, mtime_ns: int, h: str):
        self.info[rel] = [size, mtime_ns, h]
        self.by_size.setdefault(size, []).append(rel)
        self.taken.add(rel.lower())

    def add(self, full: Path, h: str):
        st = full.stat()
        self._add(full.relative_to(self.root).as_posix(), st.st_size, st.st_mtime_ns, h)

    def find_identical(self, size: int, src_hash_fn, cancel=lambda: False) -> str | None:
        """Relative path of a byte-identical file already on the destination, or None."""
        cands = self.by_size.get(size)
        if not cands:
            return None
        sh = src_hash_fn()
        for rel in list(cands):
            rec = self.info.get(rel)
            if rec is None:
                continue
            if not rec[2]:
                try:
                    rec[2] = file_hash(self.root / rel, cancel)
                except Cancelled:
                    raise
                except OSError:
                    continue
            if rec[2] == sh:
                if (self.root / rel).exists():
                    return rel
        return None

    def name_free(self, rel: str) -> bool:
        return rel.lower() not in self.taken and not (self.root / rel).exists()

    def save(self):
        try:
            self.meta.mkdir(parents=True, exist_ok=True)
            _hide(self.meta)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "files": self.info}), "utf-8")
            tmp.replace(self.file)
        except Exception as e:
            log_error("Saving the destination index", e)


def _hide(p: Path):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x02)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Copying
# --------------------------------------------------------------------------- #
def _free_name(index: DestIndex, folder: str, main_name: str, sidecars: list[Path]) -> str:
    """A file name for the main file (and matching names for its sidecars) that is not in use."""
    stem, ext = os.path.splitext(main_name)
    n = 0
    while True:
        new_stem = stem if n == 0 else f"{stem}_{n}"
        names = [new_stem + ext] + [_sidecar_name(c, stem, ext, new_stem) for c in sidecars]
        if all(index.name_free(_join(folder, x)) for x in names):
            return new_stem + ext
        n += 1


def _sidecar_name(car: Path, old_stem: str, ext: str, new_stem: str) -> str:
    name = car.name
    if name.lower().startswith((old_stem + ext).lower()):      # IMG_1.CR2.xmp
        return new_stem + ext + name[len(old_stem + ext):]
    return new_stem + name[len(old_stem):]                       # IMG_1.xmp


def _join(folder: str, name: str) -> str:
    return (Path(folder) / name).as_posix() if folder else name


def safe_copy(src: Path, dest: Path, cancel=lambda: False) -> str:
    """Copy src to dest without ever overwriting anything, and check the copy.
    Returns the checksum. Raises on any problem (and leaves nothing half-written behind)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + PART_SUFFIX)
    h = hashlib.blake2b(digest_size=20)
    try:
        with src.open("rb") as fi, open(tmp, "xb") as fo:
            while True:
                if cancel():
                    raise Cancelled()
                b = fi.read(CHUNK)
                if not b:
                    break
                h.update(b)
                fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
        src_hash = h.hexdigest()
        try:
            shutil.copystat(src, tmp)
        except OSError:
            pass
        if file_hash(tmp, cancel) != src_hash:
            raise IOError("The copy didn't match the original (the card or drive may have a read/write "
                          "problem). It was not kept.")
        _place(tmp, dest)
        return src_hash
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _place(tmp: Path, dest: Path):
    """Give the checked copy its real name, refusing to replace anything that's there."""
    if os.name == "nt":
        os.rename(tmp, dest)            # raises FileExistsError instead of overwriting
        return
    try:
        os.link(tmp, dest)              # fails if dest exists
        os.unlink(tmp)
    except FileExistsError:
        raise
    except OSError:
        if dest.exists():
            raise FileExistsError(str(dest))
        os.rename(tmp, dest)


def process(item: Item, index: DestIndex, cancel=lambda: False) -> None:
    """Copy one item (with its sidecars). Sets item.status, item.dest and item.note."""
    root = index.root
    cache: dict[str, str] = {}

    def src_hash():
        if "h" not in cache:
            cache["h"] = file_hash(item.src, cancel)
        return cache["h"]

    same = index.find_identical(item.size, src_hash, cancel)
    if same:
        item.status = "Duplicate"
        item.dest = same
        item.note = "Already on the drive, so it wasn't copied again"
        _copy_dup_sidecars(item, index, same, cancel)
        return

    name = _free_name(index, item.folder, item.src.name, item.sidecars)
    rel = _join(item.folder, name)
    for _ in range(50):
        try:
            h = safe_copy(item.src, root / rel, cancel)
            break
        except FileExistsError:          # appeared meanwhile: never overwrite, pick the next number
            index.taken.add(rel.lower())
            name = _free_name(index, item.folder, item.src.name, item.sidecars)
            rel = _join(item.folder, name)
    else:
        raise IOError("Couldn't find a free file name.")
    index.add(root / rel, h)
    item.dest = rel
    if name != item.src.name:
        item.status = "Copied (renamed)"
        item.note = f"A different file named {item.src.name} was already there, so this one is {name}"
    else:
        item.status = "Copied"
    stem, ext = os.path.splitext(item.src.name)
    new_stem = os.path.splitext(name)[0]
    for car in item.sidecars:
        if cancel():
            raise Cancelled()
        crel = _join(item.folder, _sidecar_name(car, stem, ext, new_stem))
        try:
            ch = safe_copy(car, root / crel, cancel)
            index.add(root / crel, ch)
        except Cancelled:
            raise
        except Exception as e:
            item.note = (item.note + "; " if item.note else "") + f"sidecar {car.name} not copied: {e}"


def _copy_dup_sidecars(item: Item, index: DestIndex, same_rel: str, cancel):
    """A duplicate's sidecar (e.g. Lightroom edits) is copied next to the existing photo if that
    photo doesn't have one with that name yet. Nothing there is ever replaced."""
    if not item.sidecars:
        return
    existing = Path(same_rel)
    stem, ext = os.path.splitext(item.src.name)
    new_stem = existing.stem
    folder = existing.parent.as_posix() if existing.parent != Path(".") else ""
    for car in item.sidecars:
        crel = _join(folder, _sidecar_name(car, stem, ext, new_stem))
        target = index.root / crel
        if target.exists():
            try:
                if target.stat().st_size == car.stat().st_size and file_hash(target) == file_hash(car):
                    continue
            except OSError:
                pass
            item.note += f"; a different {car.name} is already next to it, so this one was left on the card"
            continue
        try:
            ch = safe_copy(car, target, cancel)
            index.add(target, ch)
            item.note += f"; copied its {car.name}"
        except Cancelled:
            raise
        except Exception as e:
            item.note += f"; sidecar {car.name} not copied: {e}"


# --------------------------------------------------------------------------- #
# Sort log on the destination
# --------------------------------------------------------------------------- #
def write_log(root: Path, items: list[Item], started: datetime) -> Path | None:
    try:
        d = root / META_NAME / LOGS_NAME
        d.mkdir(parents=True, exist_ok=True)
        _hide(root / META_NAME)
        p = d / f"{started:%Y-%m-%d_%H%M%S}.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Result", "From", "To", "Taken", "Date from", "Note"])
            for it in items:
                w.writerow([it.status, str(it.src), it.dest,
                            it.taken.strftime("%Y-%m-%d %H:%M:%S") if it.taken else "",
                            it.date_source, it.note])
        return p
    except Exception as e:
        log_error("Writing the sort log", e)
        return None


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def keep_awake(on: bool):
    """Stop Windows going to sleep in the middle of a long copy."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass

