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
import logging
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

# the date reader prints "PNG file does not have exif data" etc. for files without dates: not errors
logging.getLogger("exifread").setLevel(logging.CRITICAL)

APP_NAME = "Millésime"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "errors.log"
META_NAME = ".millesime"          # folder on the destination holding the index and sort logs
INDEX_NAME = "index.json"
LOGS_NAME = "logs"
PART_SUFFIX = ".millesime-part"     # old temporary-copy names (still cleaned up)
PART_PREFIX = ".millesime-"         # temporary copies are now ".millesime-1a2b3c4d.part"
MAX_NAME = 255                      # Windows' limit for one file or folder name


def is_temp_name(name: str) -> bool:
    low = name.lower()
    return low.endswith(PART_SUFFIX) or (low.startswith(PART_PREFIX) and low.endswith(".part"))

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
# system folders skipped when they sit at the top of a dropped drive (E:\Windows, E:\ProgramData …)
SKIP_ROOT_DIRS = {"windows", "program files", "program files (x86)", "programdata", "recovery",
                  "$windows.~bt", "$windows.~ws", "$winreagent", "$sysreset", "msocache", "perflogs",
                  "config.msi", "boot", "intel", "amd", "nvidia"}
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
    "raw_sub": "JPEG in the folder, RAW in a RAW folder inside it",
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
    "only": "all",                # "all" (use the Include boxes), "raw" (RAW files only) or "custom"
    "only_exts": "",              # custom: file types to copy, e.g. "cr2, nef, jpg" ("raw" = every RAW type)
    "min_photo_kb": 30,           # skip pictures smaller than this (icons, thumbnails); 0 = keep all
    "gentle": True,               # low priority + short breaks while copying
    "break_every_min": 10,        # gentle mode: a break every … minutes
    "break_for_s": 30,            # … lasting this many seconds
    "speed_limit_mb": 0,          # MB/s cap on reading + writing (0 = no limit)
    "final_check": True,          # re-read this run's copies from the drive itself at the end
    "hang_s": 120,                # a file that stops responding this long is skipped (0 = never)
    "theme": "system",            # system, light or dark
    "notify": True,
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
@dataclass(slots=True)
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
    mtime_ns: int = 0
    source_id: str = ""            # the card or drive it came from (its volume serial number)
    source_rel: str = ""           # path on that card
    known_dest: str = ""           # backed up from this card before, to here
    override: str = ""             # folder chosen by hand (Move to…), wins over the options


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


ONLY_MODES = {
    "all": "Everything ticked under Include",
    "raw": "RAW files only (CR2, NEF, ARW, DNG …)",
    "custom": "Only these file types:",
}


def parse_exts(text: str) -> set[str]:
    """ "cr2, .NEF  *.jpg raw" -> {".cr2", ".nef", ".jpg", <every RAW type>} """
    out: set[str] = set()
    for tok in re.split(r"[\s,;|]+", (text or "").lower()):
        tok = tok.strip().lstrip("*").lstrip(".")
        if not tok:
            continue
        if tok == "raw":
            out |= RAW_EXT
        elif tok in ("jpg", "jpeg"):
            out |= {".jpg", ".jpeg", ".jpe"}
        else:
            out.add("." + tok)
    return out


def only_filter(s: dict) -> set[str] | None:
    """The file types to copy when "Copy" is set to RAW only / chosen types; None = use Include."""
    mode = s.get("only", "all")
    if mode == "raw":
        return set(RAW_EXT)
    if mode == "custom":
        return parse_exts(s.get("only_exts", ""))
    return None


def _inside(p: Path, root: Path | None) -> bool:
    if not root:
        return False
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def gather(paths: list[Path], settings: dict, dest_root: Path | None = None, cancel=lambda: False,
           stats: dict | None = None):
    """Yield Items for every wanted file under the given files/folders. Sidecars (.xmp etc.) are
    attached to the file they belong to. Anything already inside the destination is skipped."""
    seen: set[str] = set()
    only = only_filter(settings)
    for p in paths:
        p = Path(p)
        if _inside(p, dest_root):
            continue
        if any(part.lower() in SKIP_DIRS for part in p.parts):
            continue
        try:
            is_file, is_dir = p.is_file(), p.is_dir()
        except OSError:                  # Windows refused (system file, Recycle Bin …): skip it
            continue
        if is_file:
            try:
                listing = os.listdir(p.parent)
            except OSError:
                listing = [p.name]
            dirs = [(p.parent, [p.name], listing)]
        elif is_dir:
            dirs = []
            drive_top = os.path.normcase(os.path.normpath(p.anchor)) if p.anchor else None
            for root, dnames, fnames in os.walk(p):
                if cancel():
                    return
                at_drive_top = os.path.normcase(os.path.normpath(root)) == drive_top
                dnames[:] = [d for d in dnames if d.lower() not in SKIP_DIRS and not d.startswith("._")
                             and not (at_drive_top and d.lower() in SKIP_ROOT_DIRS)]
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
                if ext in SIDECAR_EXT or ext in JUNK_EXT or is_temp_name(low):
                    continue
                kind = kind_of(ext)
                if kind == "photo" and has_music and (stem in ART_STEMS or stem.startswith("albumart")):
                    kind = "art"
                if only is not None:
                    if ext not in only:
                        continue
                elif not wanted(kind, settings):
                    continue
                f = d / n
                key = os.path.normcase(os.path.abspath(f))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    st = f.stat()
                except OSError:
                    continue
                size = st.st_size
                min_kb = int(settings.get("min_photo_kb", 0) or 0)
                if kind == "photo" and min_kb and size < min_kb * 1024:
                    if stats is not None:
                        stats["tiny"] = stats.get("tiny", 0) + 1
                    continue
                cars = list(cars_by_key.get(low, []))
                # IMG_1.xmp next to both IMG_1.CR2 and IMG_1.JPG goes with the RAW
                if ext in RAW_EXT or stem not in stems_with_raw:
                    cars += cars_by_key.get(stem, [])
                it = Item(src=f, size=size, kind=kind, sidecars=[d / c for c in sorted(set(cars))],
                          mtime_ns=st.st_mtime_ns)
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
        with open(L(path), "rb") as f:
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
        with open(L(path), "rb") as f:
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
        with open(L(path), "rb") as f:
            stop_all = os.fstat(f.fileno()).st_size

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
        f = mutagen.File(L(path), easy=True)
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


def apply_aliases(folder: str, aliases: dict | None) -> str:
    """Folder names the user changed. Renaming 'Photos/2019' also moves everything under it."""
    if not aliases:
        return folder
    for _ in range(20):          # renames can build on earlier renames
        best = ""
        for k in aliases:
            if (folder == k or folder.startswith(k + "/")) and len(k) > len(best):
                best = k
        if not best or aliases[best] == best:
            break
        folder = aliases[best] + folder[len(best):]
    return folder


def plan(items: list[Item], s: dict, album_of_dir: dict | None = None, aliases: dict | None = None) -> None:
    """Decide the folder for every waiting item from the current options. Pass the same
    album_of_dir between batches so album covers find the songs they were next to."""
    layout = s.get("layout", "year_month")
    if album_of_dir is None:
        album_of_dir = {}
    for it in items:
        if it.status != "Waiting":
            continue
        if it.override:
            it.folder = it.override
            continue
        if it.kind in ("raw", "photo"):
            f = f"{LIB['photo']}/{date_folder(it.taken, layout)}"
            if s.get("raw_split") == "split":
                f += "/RAW" if it.kind == "raw" else "/JPEG"
            elif s.get("raw_split") == "raw_sub" and it.kind == "raw":
                f += "/RAW"
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
        it.folder = apply_aliases(f, aliases)
    for it in items:   # album covers go with the songs they were next to
        if it.status == "Waiting" and it.kind == "art" and not it.override:
            it.folder = album_of_dir.get(it.src.parent,
                                         apply_aliases(f"{LIB['music']}/Unknown Artist/Unknown Album", aliases))


KIND_GROUP = {"raw": "photos", "photo": "photos", "video": "videos", "music": "songs", "art": "songs",
              "doc": "documents", "other": "other files"}
KIND_LABEL = {"raw": "RAW", "photo": "Photo", "video": "Video", "music": "Music", "art": "Album cover",
              "doc": "Document", "other": "Other"}


# --------------------------------------------------------------------------- #
# Long Windows paths, file systems, free space
# --------------------------------------------------------------------------- #
CHUNK = 4 * 1024 * 1024
RESERVE_BYTES = 2 * 1024 ** 3          # always leave this much free on the library drive
FAT_LIMIT = 4 * 1024 ** 3 - 1          # FAT32 can't hold a file this big or bigger


def L(p) -> str:
    """Path string that works past Windows' 260-character limit."""
    s = os.path.abspath(str(p))
    if os.name != "nt" or s.startswith("\\\\?\\") or len(s) < 240:
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def exists(p) -> bool:
    return os.path.exists(L(p))


def fs_type(p: Path) -> str:
    """'NTFS', 'exFAT', 'FAT32' … for the drive p is on ('' if unknown)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        fs = ctypes.create_unicode_buffer(261)
        root = str(volume_root(p))
        root = root if root.endswith("\\") else root + "\\"
        if ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, fs, 261):
            return fs.value
    except Exception:
        pass
    return ""


def free_bytes(p: Path) -> int:
    try:
        return shutil.disk_usage(L(p)).free
    except OSError:
        return -1


class DriveFull(Exception):
    pass


class DriveGone(Exception):
    """The library drive (or the card a file was on) has been unplugged."""


def is_disk_full(e: BaseException) -> bool:
    return isinstance(e, OSError) and (getattr(e, "errno", None) == 28 or getattr(e, "winerror", None) in (39, 112))


# --------------------------------------------------------------------------- #
# The library's own records: a small database in .millesime (crash-safe)
# --------------------------------------------------------------------------- #
DB_NAME = "library.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (rel TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, hash TEXT);
CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, name TEXT, first TEXT, last TEXT);
CREATE TABLE IF NOT EXISTS card_files (card TEXT, rel TEXT, size INTEGER, mtime_ns INTEGER, dest TEXT,
    taken TEXT, date_source TEXT, artist TEXT, album TEXT, PRIMARY KEY (card, rel));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_db(lib: Path):
    """One connection per thread. WAL mode keeps the records intact through crashes and power cuts."""
    import sqlite3
    meta = lib / META_NAME
    os.makedirs(L(meta), exist_ok=True)
    _hide(meta)
    con = sqlite3.connect(L(meta / DB_NAME), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_SCHEMA)
    _migrate_json(con, meta)
    return con


def _migrate_json(con, meta: Path):
    """Bring in the records older versions kept as JSON (the JSON files are left where they are)."""
    if con.execute("SELECT value FROM meta WHERE key='migrated'").fetchone():
        return
    try:
        files = json.loads((meta / INDEX_NAME).read_text("utf-8")).get("files", {})
        con.executemany("INSERT OR IGNORE INTO files VALUES (?,?,?,?)",
                        [(r, v[0], v[1], v[2] or "") for r, v in files.items()])
    except Exception:
        pass
    try:
        cards = json.loads((meta / "cards.json").read_text("utf-8")).get("cards", {})
        for cid, c in cards.items():
            con.execute("INSERT OR IGNORE INTO cards VALUES (?,?,?,?)",
                        (cid, c.get("name", ""), c.get("first", ""), c.get("last", "")))
            con.executemany("INSERT OR IGNORE INTO card_files VALUES (?,?,?,?,?,?,?,?,?)",
                            [(cid, rel, *v[:7]) for rel, v in c.get("files", {}).items()])
    except Exception:
        pass
    con.execute("INSERT OR REPLACE INTO meta VALUES ('migrated', ?)", (datetime.now().isoformat(),))
    con.commit()


def backup_db(lib: Path, keep: int = 5) -> None:
    """A dated copy of the records (one a day, last few kept), in case the drive damages the live one."""
    try:
        import sqlite3
        d = lib / META_NAME / "backups"
        os.makedirs(L(d), exist_ok=True)
        target = d / f"library-{datetime.now():%Y-%m-%d}.db"
        src = open_db(lib)
        dst = sqlite3.connect(L(target))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        olds = sorted(d.glob("library-*.db"))
        for f in olds[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass
    except Exception as e:
        log_error("Backing up the library records", e)


# --------------------------------------------------------------------------- #
# Checksums and what's already in the library
# --------------------------------------------------------------------------- #
class Throttle:
    """Keeps reading and writing under a speed limit, so the drives never run flat out."""

    def __init__(self):
        self.mb_per_s = 0.0
        self._t = time.monotonic()
        self._budget = 0.0

    def spend(self, n: int, cancel=lambda: False):
        if self.mb_per_s <= 0:
            return
        rate = self.mb_per_s * 1024 * 1024
        now = time.monotonic()
        self._budget = min(rate, self._budget + (now - self._t) * rate)   # at most 1 s of burst
        self._t = now
        self._budget -= n
        while self._budget < 0:
            if cancel():
                return
            wait = min(0.25, -self._budget / rate)
            time.sleep(wait)
            now = time.monotonic()
            self._budget += (now - self._t) * rate
            self._t = now


THROTTLE = Throttle()


def file_hash(path: Path, cancel=lambda: False, on_bytes=None) -> str:
    h = hashlib.blake2b(digest_size=20)
    done = 0
    with open(L(path), "rb") as f:
        total = os.fstat(f.fileno()).st_size
        while True:
            if cancel():
                raise Cancelled()
            b = f.read(CHUNK)
            if not b:
                break
            THROTTLE.spend(len(b), cancel)
            h.update(b)
            done += len(b)
            if on_bytes and total > 64 * CHUNK:
                on_bytes(done, total)
    return h.hexdigest()


class Cancelled(Exception):
    pass


def disk_hash(path: Path, cancel=lambda: False) -> str:
    """Checksum read from the drive itself, not from Windows' memory cache, so it proves the copy
    really landed on the disk. Falls back to a normal read where that isn't possible."""
    if os.name != "nt":
        return file_hash(path, cancel)
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    k.VirtualAlloc.restype = ctypes.c_void_p
    GENERIC_READ, SHARE_READ, OPEN_EXISTING = 0x80000000, 0x1, 3
    NO_BUFFERING, SEQUENTIAL = 0x20000000, 0x08000000
    handle = k.CreateFileW(L(path), GENERIC_READ, SHARE_READ, None, OPEN_EXISTING, NO_BUFFERING | SEQUENTIAL, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return file_hash(path, cancel)
    buf = k.VirtualAlloc(None, CHUNK, 0x3000, 0x04)          # page-aligned, as unbuffered reads need
    if not buf:
        k.CloseHandle(handle)
        return file_hash(path, cancel)
    h = hashlib.blake2b(digest_size=20)
    got = wintypes.DWORD(0)
    try:
        while True:
            if cancel():
                raise Cancelled()
            if not k.ReadFile(handle, ctypes.c_void_p(buf), CHUNK, ctypes.byref(got), None):
                raise OSError(ctypes.get_last_error() or 1, "Couldn't read the copy back from the drive")
            if got.value == 0:
                break
            THROTTLE.spend(got.value, cancel)
            h.update(ctypes.string_at(buf, got.value))
            if got.value < CHUNK:
                break
    finally:
        k.VirtualFree(ctypes.c_void_p(buf), 0, 0x8000)
        k.CloseHandle(handle)
    return h.hexdigest()


def walk_files(root: Path, cancel=lambda: False):
    """Yield (relative path, size, mtime_ns) for every file under root, fast (one directory read
    gives the sizes on Windows) and safe with very long paths. Skips the .millesime folder."""
    stack = [""]
    base = str(root)
    while stack:
        if cancel():
            raise Cancelled()
        rel_dir = stack.pop()
        try:
            it = os.scandir(L(os.path.join(base, rel_dir)) if rel_dir else L(base))
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                rel = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    if e.is_dir(follow_symlinks=False):
                        if name.lower() not in SKIP_DIRS:
                            stack.append(rel)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield rel, st.st_size, st.st_mtime_ns


class DestIndex:
    """Every file in the library by size, with checksums worked out only when needed and kept in
    the library's records so later runs are fast."""

    def __init__(self, root: Path):
        self.root = root
        self.meta = root / META_NAME
        self.file = self.meta / INDEX_NAME          # old JSON index (only read, for migrating)
        self.by_size: dict[int, list[str]] = {}
        self.info: dict[str, list] = {}             # rel -> [size, mtime_ns, hash or ""]
        self.taken: set[str] = set()                # lower-cased rel paths (names in use)
        self.missing: list[str] = []                # in the records but no longer on the drive
        self.leftovers = 0                          # half-copies from a crash, cleaned up
        self._dirty: set[str] = set()
        self._con = None

    def build(self, progress=lambda n: None, cancel=lambda: False):
        reconcile_moves(self.root)        # a reorganize that was cut off: records put right first
        self._con = open_db(self.root)
        cached = {r: (s, m, h) for r, s, m, h in self._con.execute("SELECT rel, size, mtime_ns, hash FROM files")}
        n = 0
        for rel, size, mtime in walk_files(self.root, cancel):
            if is_temp_name(rel.rpartition("/")[2]):
                # a half-written copy from a crash or power cut: ours, never a real file
                try:
                    os.unlink(L(self.root / rel))
                    self.leftovers += 1
                except OSError:
                    pass
                continue
            c = cached.pop(rel, None)
            h = c[2] if c and c[0] == size and c[1] == mtime else ""
            self._add(rel, size, mtime, h)
            if not c or h != c[2] or c[0] != size or c[1] != mtime:
                self._dirty.add(rel)
            n += 1
            if n % 1000 == 0:
                progress(n)
        self.missing = sorted(cached)
        progress(n)

    def _add(self, rel: str, size: int, mtime_ns: int, h: str):
        old = self.info.get(rel)
        if old and old[0] != size:
            try:
                self.by_size[old[0]].remove(rel)
            except (KeyError, ValueError):
                pass
        self.info[rel] = [size, mtime_ns, h]
        if not old or old[0] != size:
            self.by_size.setdefault(size, []).append(rel)
        self.taken.add(rel.lower())

    def add(self, full: Path, h: str):
        st = os.stat(L(full))
        rel = full.relative_to(self.root).as_posix()
        self._add(rel, st.st_size, st.st_mtime_ns, h)
        self._dirty.add(rel)

    def set_hash(self, rel: str, h: str):
        self.info[rel][2] = h
        self._dirty.add(rel)

    def find_identical(self, size: int, src_hash_fn, cancel=lambda: False, on_bytes=None) -> str | None:
        """Relative path of a byte-identical file already in the library, or None."""
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
                    self.set_hash(rel, file_hash(self.root / rel, cancel, on_bytes))
                except Cancelled:
                    raise
                except OSError:
                    continue
            if rec[2] == sh and exists(self.root / rel):
                return rel
        return None

    def name_free(self, rel: str) -> bool:
        return rel.lower() not in self.taken and not exists(self.root / rel)

    def save(self, drop_missing: bool = True):
        """Write only what changed since the last save (cheap, even for huge libraries)."""
        try:
            if self._con is None:
                self._con = open_db(self.root)
            rows = [(r, *self.info[r]) for r in self._dirty if r in self.info]
            with self._con:
                self._con.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", rows)
                if drop_missing and self.missing:
                    self._con.executemany("DELETE FROM files WHERE rel=?", [(r,) for r in self.missing])
            self._dirty.clear()
        except Exception as e:
            log_error("Saving the library records", e)

    def close(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None


def _hide(p: Path):
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(L(p), 0x02)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Copying
# --------------------------------------------------------------------------- #
_RANDOMISH = re.compile(r"^[A-Za-z0-9_\-]+$")
SHORT_RANDOM = 16


def is_random_name(stem: str) -> bool:
    """Long strings of random letters and numbers (download IDs, hashes, UUIDs), not real names."""
    if len(stem) < 40 or not _RANDOMISH.match(stem):
        return False
    body = stem.replace("-", "").replace("_", "")
    if re.fullmatch(r"[0-9a-fA-F]+", body):
        return True                                     # hex: hashes, UUIDs
    digits = sum(c.isdigit() for c in body)
    longest_word = max((len(w) for w in re.findall(r"[A-Za-z]+", body)), default=0)
    return digits >= len(body) * 0.2 and longest_word < 12   # mixed letters and numbers, no real words


def tidy_stem(stem: str) -> str:
    return stem[:SHORT_RANDOM] if is_random_name(stem) else stem


def _free_name(index: DestIndex, folder: str, main_name: str, sidecars: list[Path]) -> str:
    """A file name for the main file (and matching names for its sidecars) that is not in use,
    shortened if needed so no name goes over Windows' 255-character limit."""
    stem, ext = os.path.splitext(main_name)
    extra = max([len(ext)] + [len(_sidecar_name(c, stem, ext, "")) for c in sidecars])
    base = tidy_stem(stem)
    n = 0
    while True:
        tag = "" if n == 0 else f"_{n}"
        room = MAX_NAME - extra - len(tag)
        new_stem = (base if len(base) <= room else base[:max(1, room)].rstrip(" .")) + tag
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


STEP = {"now": ""}      # what the copy is doing at this moment, for stall reports


def _drive(p: Path) -> str:
    return str(volume_root(p)).rstrip("\\/") or str(p)


def safe_copy(src: Path, dest: Path, cancel=lambda: False, on_bytes=None, data: bytes | None = None) -> str:
    """Copy src to dest without ever overwriting anything, and check the copy.
    Returns the checksum. Raises on any problem (and leaves nothing half-written behind)."""
    os.makedirs(L(dest.parent), exist_ok=True)
    # a short temporary name, so a long file name never goes over Windows' 255-character limit
    import uuid
    tmp = dest.with_name(f"{PART_PREFIX}{uuid.uuid4().hex[:12]}.part")
    h = hashlib.blake2b(digest_size=20)
    try:
        # opened one at a time, so a stall report shows which drive stopped answering
        STEP["now"] = f"opening the original on {_drive(src)} ({src.name})"
        import io
        fi = io.BytesIO(data) if data is not None else open(L(src), "rb")   # already read once: not again
        STEP["now"] = f"creating the copy on the library drive {_drive(dest)}"
        try:
            fo = open(L(tmp), "xb")
        except BaseException:
            fi.close()
            raise
        with fi, fo:
            STEP["now"] = f"copying {src.name} from {_drive(src)} to {_drive(dest)}"
            total, done = (len(data) if data is not None else os.fstat(fi.fileno()).st_size), 0
            while True:
                if cancel():
                    raise Cancelled()
                b = fi.read(CHUNK)
                if not b:
                    break
                if data is None:
                    THROTTLE.spend(len(b), cancel)
                h.update(b)
                fo.write(b)
                THROTTLE.spend(len(b), cancel)
                done += len(b)
                if on_bytes and total > 16 * CHUNK:
                    on_bytes("Copying", done, total)
            STEP["now"] = f"saving the copy to the library drive {_drive(dest)}"
            fo.flush()
            os.fsync(fo.fileno())
        src_hash = h.hexdigest()
        try:
            shutil.copystat(L(src), L(tmp))
        except OSError:
            pass
        STEP["now"] = f"reading the copy back from {_drive(dest)} to check it"
        check = (lambda d, t: on_bytes("Checking the copy of", d, t)) if on_bytes else None
        if file_hash(tmp, cancel, check) != src_hash:
            raise IOError("The copy didn't match the original (the card or drive may have a read/write "
                          "problem). It was not kept.")
        if cancel():                      # stopped (or given up on) while checking: don't keep it
            raise Cancelled()
        STEP["now"] = f"naming the finished copy on {_drive(dest)}"
        _place(tmp, dest)
        STEP["now"] = ""
        return src_hash
    except BaseException:
        try:
            os.unlink(L(tmp))
        except OSError:
            pass
        raise


def _place(tmp: Path, dest: Path):
    """Give the checked copy its real name, refusing to replace anything that's there."""
    if os.name == "nt":
        os.rename(L(tmp), L(dest))      # raises FileExistsError instead of overwriting
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


class Guard:
    """Checks made before every copy: space left, file-system limits, drive still there."""

    def __init__(self, root: Path):
        self.root = root
        self.fat = fs_type(root).upper().startswith("FAT")
        self._free = free_bytes(root)
        self._checked = 0.0

    def before_copy(self, size: int):
        if not exists(self.root):
            raise DriveGone("The library drive was disconnected.")
        if self.fat and size >= FAT_LIMIT:
            raise IOError("This drive is formatted FAT32, which can't hold files of 4 GB or more. "
                          "Reformat it as NTFS or exFAT to copy this one.")
        now = time.monotonic()
        if now - self._checked > 5 or self._free - size < RESERVE_BYTES * 2:
            self._free = free_bytes(self.root)
            self._checked = now
        if self._free >= 0 and self._free - size < RESERVE_BYTES:
            raise DriveFull(f"The library drive is almost full. {human_size(RESERVE_BYTES)} is always left free "
                            "so the drive keeps working well.")
        self._free -= size


def process(item: Item, index: DestIndex, cancel=lambda: False, guard: Guard | None = None,
            on_bytes=None) -> None:
    """Copy one item (with its sidecars). Sets item.status, item.dest and item.note.
    on_bytes(what, done, total) reports progress inside big files."""
    root = index.root
    cache: dict = {}
    say = (lambda what: (lambda d, t: on_bytes(what, d, t))) if on_bytes else (lambda what: None)

    def src_hash():
        # photos are read into memory once: compared, and then (if new) copied from memory, so the
        # card or old drive is read a single time. Big videos are read again only if they need copying.
        if "h" not in cache:
            if item.size <= MEM_READ_LIMIT:
                data = _read_all(item.src, cancel, say("Reading"))
                cache["data"] = data
                cache["h"] = hashlib.blake2b(data, digest_size=20).hexdigest()
            else:
                cache["h"] = file_hash(item.src, cancel, say("Comparing"))
        return cache["h"]

    if item.known_dest:
        # backed up from this card before, and still in the library: not read again
        rec = index.info.get(item.known_dest)
        if rec and rec[0] == item.size and exists(root / item.known_dest):
            item.status = "Duplicate"
            item.dest = item.known_dest
            item.note = "Already backed up from this card (not read again)"
            return

    same = index.find_identical(item.size, src_hash, cancel, say("Comparing with the library's copy of"))
    if same:
        item.status = "Duplicate"
        item.dest = same
        item.note = "Already on the drive, so it wasn't copied again"
        _copy_dup_sidecars(item, index, same, cancel, guard)
        return

    if guard:
        guard.before_copy(item.size + sum(_size(c) for c in item.sidecars))
    name = _free_name(index, item.folder, item.src.name, item.sidecars)
    rel = _join(item.folder, name)
    for _ in range(50):
        try:
            h = safe_copy(item.src, root / rel, cancel, on_bytes, cache.get("data"))
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
        old_stem = os.path.splitext(item.src.name)[0]
        if is_random_name(old_stem) and not re.search(r"_\d+$", os.path.splitext(name)[0]):
            item.note = f"Its long random name was shortened to {name}"
        elif not re.search(r"_\d+$", os.path.splitext(name)[0]):
            item.note = f"Its name was too long for Windows, so it was shortened to {name}"
        else:
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


MEM_READ_LIMIT = 256 * 1024 * 1024


def _read_all(p: Path, cancel, on_bytes=None) -> bytes:
    STEP["now"] = f"reading the original on {_drive(p)} ({p.name})"
    parts, done = [], 0
    with open(L(p), "rb") as f:
        total = os.fstat(f.fileno()).st_size
        while True:
            if cancel():
                raise Cancelled()
            b = f.read(CHUNK)
            if not b:
                break
            THROTTLE.spend(len(b), cancel)
            parts.append(b)
            done += len(b)
            if on_bytes and total > 16 * CHUNK:
                on_bytes(done, total)
    return b"".join(parts)


def _size(p: Path) -> int:
    try:
        return os.stat(L(p)).st_size
    except OSError:
        return 0


def _copy_dup_sidecars(item: Item, index: DestIndex, same_rel: str, cancel, guard: Guard | None = None):
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
        if exists(target):
            try:
                if _size(target) == _size(car) and file_hash(target) == file_hash(car):
                    continue
            except OSError:
                pass
            item.note += f"; a different {car.name} is already next to it, so this one was left on the card"
            continue
        try:
            if guard:
                guard.before_copy(_size(car))
            ch = safe_copy(car, target, cancel)
            index.add(target, ch)
            item.note += f"; copied its {car.name}"
        except (Cancelled, DriveFull, DriveGone):
            raise
        except Exception as e:
            item.note += f"; sidecar {car.name} not copied: {e}"


def source_gone(it: Item) -> bool:
    """True if the card or drive this file was on has been unplugged."""
    try:
        return not os.path.exists(str(volume_root(it.src)))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Sort log on the destination, written as it goes (survives a crash)
# --------------------------------------------------------------------------- #
class LogWriter:
    def __init__(self, root: Path, started: datetime):
        self.path = None
        self._f = None
        try:
            d = root / META_NAME / LOGS_NAME
            os.makedirs(L(d), exist_ok=True)
            _hide(root / META_NAME)
            self.path = d / f"{started:%Y-%m-%d_%H%M%S}.csv"
            self._f = open(L(self.path), "w", newline="", encoding="utf-8-sig")
            self._w = csv.writer(self._f)
            self._w.writerow(["Result", "From", "To", "Taken", "Date from", "Note"])
            self._last_flush = time.monotonic()
        except Exception as e:
            log_error("Starting the sort log", e)
            self._f = None

    def write(self, it: Item):
        if not self._f:
            return
        try:
            self._w.writerow([it.status, str(it.src), it.dest,
                              it.taken.strftime("%Y-%m-%d %H:%M:%S") if it.taken else "", it.date_source, it.note])
            if time.monotonic() - self._last_flush > 2:
                self._f.flush()
                self._last_flush = time.monotonic()
        except Exception:
            pass

    def close(self):
        if self._f:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None


def write_log(root: Path, items: list[Item], started: datetime) -> Path | None:
    w = LogWriter(root, started)
    for it in items:
        w.write(it)
    w.close()
    return w.path


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def low_priority(on: bool):
    """Gentle mode: this thread's disk and CPU use go to the back of the queue (Windows), so File
    Explorer and other programs stay responsive. Call from the worker thread itself."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        # THREAD_MODE_BACKGROUND_BEGIN / END: low I/O and memory priority for the calling thread
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000 if on else 0x00020000)
    except Exception:
        pass


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


# --------------------------------------------------------------------------- #
# Remembering cards (stored in the library, never on the card)
# --------------------------------------------------------------------------- #
CARDS_NAME = "cards.json"
_vol_cache: dict[str, tuple[str, str]] = {}


def volume_root(p: Path) -> Path:
    p = Path(os.path.abspath(p))
    if os.name == "nt":
        return Path(p.anchor)
    parts = p.parts
    if sys.platform == "darwin" and len(parts) >= 3 and parts[1] == "Volumes":
        return Path(*parts[:3])
    if len(parts) >= 4 and parts[1] in ("media", "run"):
        return Path(*parts[:4])
    if len(parts) >= 3 and parts[1] == "mnt":
        return Path(*parts[:3])
    return Path("/")


def volume_id(root: Path) -> tuple[str, str]:
    """(id, name) of the card or drive. On Windows the id is the volume serial number, which
    stays the same until the card is formatted. Only reads it; nothing is written."""
    key = str(root)
    if key in _vol_cache:
        return _vol_cache[key]
    vid, name = "", ""
    if os.name == "nt":
        try:
            import ctypes
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            serial = ctypes.c_uint32()
            r = str(root) if str(root).endswith("\\") else str(root) + "\\"
            if ctypes.windll.kernel32.GetVolumeInformationW(r, label, 261, ctypes.byref(serial), None, None, fs, 261):
                vid = f"{serial.value:08X}"
                name = label.value
        except Exception:
            pass
        letter = str(root).rstrip("\\")
        name = f"{name} ({letter})" if name else letter
    else:
        name = root.name or str(root)
    if not vid:
        vid = "path:" + str(root)
    _vol_cache[key] = (vid, name)
    return vid, name


def source_key(p: Path) -> tuple[str, str, str]:
    """(card id, card name, path on the card) for a source file."""
    root = volume_root(p)
    vid, name = volume_id(root)
    try:
        rel = Path(os.path.abspath(p)).relative_to(root).as_posix()
    except ValueError:
        rel = Path(p).as_posix()
    return vid, name, rel


class CardStore:
    """What was backed up from each card, kept in the library's records (never on the card).
    Use one CardStore per thread."""

    def __init__(self, lib: Path):
        self.lib = lib
        self._con = None
        self._pending: list[tuple] = []
        self._cards: dict[str, str] = {}
        try:
            self._con = open_db(lib)
        except Exception as e:
            log_error("Opening the library records", e)

    def lookup(self, it: Item) -> list | None:
        if not self._con or not it.source_id:
            return None
        row = self._con.execute("SELECT size, mtime_ns, dest, taken, date_source, artist, album FROM card_files "
                                "WHERE card=? AND rel=?", (it.source_id, it.source_rel)).fetchone()
        # same card, same path, same size and same modified time: the same file as last time
        if row and row[0] == it.size and row[1] == it.mtime_ns:
            return list(row)
        return None

    def apply(self, it: Item, rec: list) -> None:
        it.known_dest = rec[2]
        try:
            it.taken = datetime.fromisoformat(rec[3]) if rec[3] else None
        except ValueError:
            it.taken = None
        it.date_source, it.artist, it.album = rec[4] or "", rec[5] or "", rec[6] or ""

    def record(self, it: Item, name: str) -> None:
        if not it.source_id or it.status not in ("Copied", "Copied (renamed)", "Duplicate") or not it.dest:
            return
        self._cards[it.source_id] = name
        self._pending.append((it.source_id, it.source_rel, it.size, it.mtime_ns, it.dest,
                              it.taken.isoformat(timespec="seconds") if it.taken else "",
                              it.date_source, it.artist, it.album))
        if len(self._pending) >= 500:
            self.save()

    def last_backup(self, source_id: str) -> datetime | None:
        if not self._con:
            return None
        row = self._con.execute("SELECT last FROM cards WHERE id=?", (source_id,)).fetchone()
        try:
            return datetime.fromisoformat(row[0]) if row and row[0] else None
        except ValueError:
            return None

    def save(self) -> None:
        if not self._con or (not self._pending and not self._cards):
            return
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self._con:
                for cid, name in self._cards.items():
                    self._con.execute("INSERT INTO cards VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                      "name=excluded.name, last=excluded.last", (cid, name, now, now))
                self._con.executemany("INSERT OR REPLACE INTO card_files VALUES (?,?,?,?,?,?,?,?,?)", self._pending)
            self._pending.clear()
        except Exception as e:
            log_error("Saving the card list", e)

    def close(self):
        self.save()
        if self._con:
            self._con.close()
            self._con = None


# --------------------------------------------------------------------------- #
# Health check: re-read the library and compare with the checksums stored when files were copied
# --------------------------------------------------------------------------- #
def health_check(lib: Path, progress=lambda done, total, name: None, cancel=lambda: False) -> dict:
    """Only reads the library drive. Returns counts and lists of damaged / missing files.
    A file whose size or modified time changed was edited on purpose, so it isn't called damaged;
    its new checksum is stored. A damaged file keeps its original checksum so it stays flagged."""
    index = DestIndex(lib)
    con = open_db(lib)
    known = {r for (r,) in con.execute("SELECT rel FROM files")}
    con.close()
    index.build(cancel=cancel)
    rels = list(index.info)
    total = len(rels)
    ok = new = edited = 0
    damaged: list[str] = []
    unreadable: list[str] = []
    for i, rel in enumerate(rels):
        if cancel():
            index.save(drop_missing=False)
            index.close()
            raise Cancelled()
        progress(i, total, rel)
        rec = index.info[rel]
        try:
            h = file_hash(lib / rel, cancel)
        except Cancelled:
            index.save(drop_missing=False)
            index.close()
            raise
        except OSError:
            if not exists(lib):
                index.close()
                raise DriveGone("The library drive was disconnected during the check.")
            unreadable.append(rel)
            continue
        if rec[2]:
            if h == rec[2]:
                ok += 1
            else:
                damaged.append(rel)
            continue
        if rel in known:
            edited += 1
        else:
            new += 1
        index.set_hash(rel, h)
        if (i + 1) % 1000 == 0:
            index.save(drop_missing=False)
    missing = list(index.missing)
    index.save()
    index.close()
    progress(total, total, "")
    p = None
    try:
        d = lib / META_NAME / LOGS_NAME
        os.makedirs(L(d), exist_ok=True)
        p = d / f"health-check_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
        with open(L(p), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Problem", "File"])
            for r in damaged:
                w.writerow(["Damaged (changed without being edited)", r])
            for r in unreadable:
                w.writerow(["Couldn't be read", r])
            for r in missing:
                w.writerow(["Missing (was in the library before)", r])
    except Exception as e:
        log_error("Writing the health check report", e)
        p = None
    try:
        (lib / META_NAME / "health.json").write_text(
            json.dumps({"last": datetime.now().isoformat(timespec="seconds"), "damaged": len(damaged)}), "utf-8")
    except Exception:
        pass
    backup_db(lib)
    return {"ok": ok, "new": new, "edited": edited, "damaged": damaged, "unreadable": unreadable,
            "missing": missing, "report": p, "total": total}


def last_health_check(lib: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads((lib / META_NAME / "health.json").read_text("utf-8"))["last"])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Folder names the user changed, remembered per library
# --------------------------------------------------------------------------- #
def load_aliases(lib: Path | None) -> dict:
    if not lib:
        return {}
    try:
        return json.loads((lib / META_NAME / "folders.json").read_text("utf-8")).get("renamed", {})
    except Exception:
        return {}


def save_aliases(lib: Path, aliases: dict) -> None:
    try:
        d = lib / META_NAME
        d.mkdir(parents=True, exist_ok=True)
        _hide(d)
        tmp = d / "folders.tmp"
        tmp.write_text(json.dumps({"renamed": aliases}, indent=1), "utf-8")
        tmp.replace(d / "folders.json")
    except Exception as e:
        log_error("Saving renamed folders", e)


def clean_folder_path(text: str) -> str:
    """'Photos\\Events/ Wedding ' -> 'Photos/Events/Wedding', each part made safe for Windows."""
    parts = [p for p in re.split(r"[\\/]+", text or "") if p.strip()]
    return "/".join(safe_name(p, "_") for p in parts)


# --------------------------------------------------------------------------- #
# Time left: a steady estimate that doesn't panic while things get going
# --------------------------------------------------------------------------- #
PER_FILE_COST = 512 * 1024      # every file costs about this much in opening, saving and naming


def copy_cost(it: Item) -> int:
    """How much work an item is, in bytes. Files backed up before aren't read again, so they're
    nearly free; everything else is read (and usually written) in full."""
    return (0 if it.known_dest else it.size) + PER_FILE_COST


class Eta:
    """Time left, worked out from how fast the last minute or so went, not from the first few
    seconds (when drives spin up and Windows fills its caches) and not from file counts (one 12 GB
    video is not the same as one 30 KB picture).

    Feed it the time spent working (breaks and pauses left out), the work done and the work in all,
    in any unit (bytes here). It says nothing (None) until it has watched for WARMUP_S seconds."""

    WARMUP_S = 10.0       # say "working it out" until this much work time has gone by
    SKIP_S = 3.0          # the very first seconds are ignored when measuring the speed
    WINDOW_S = 60.0       # speed measured over about this much recent time
    SMOOTH = 0.25         # how quickly a new measurement moves the estimate (0..1)

    def __init__(self):
        from collections import deque
        self._samples: deque = deque()
        self._t0: float | None = None
        self._last: tuple[float, float] | None = None    # (time, seconds left) last given
        self.rate = 0.0                                    # work per second, recently

    def update(self, active_s: float, done: float, total: float) -> float | None:
        if self._t0 is None:
            self._t0 = active_s
        t = active_s - self._t0
        if t >= self.SKIP_S:
            self._samples.append((t, done))
            while len(self._samples) > 2 and self._samples[1][0] < t - self.WINDOW_S:
                self._samples.popleft()
        left_work = max(0.0, total - done)
        if left_work <= 0:
            return 0.0
        if t < self.WARMUP_S or len(self._samples) < 2:
            return None
        (ta, da), (tb, db) = self._samples[0], self._samples[-1]
        if tb - ta < 1.0:
            return self._last[1] if self._last else None
        rate = (db - da) / (tb - ta)
        if rate <= 0:
            # nothing finished in the window (one huge file): count down from the last estimate
            return self._predicted(t)
        self.rate = rate
        raw = left_work / rate
        guess = self._predicted(t)
        est = raw if guess is None else guess + self.SMOOTH * (raw - guess)
        self._last = (t, est)
        return max(0.0, est)

    def _predicted(self, t: float) -> float | None:
        if not self._last:
            return None
        return max(0.0, self._last[1] - (t - self._last[0]))


def format_left(secs: float | None) -> str:
    """'about 25 min left', rounded so it doesn't flicker."""
    if secs is None:
        return "working out the time left…"
    if secs < 60:
        return "under a minute left"
    mins = secs / 60
    if mins < 10:
        return f"about {round(mins)} min left"
    if mins < 60:
        return f"about {int(5 * round(mins / 5))} min left"
    h, m = divmod(int(5 * round(mins / 5)), 60)
    return f"about {h} h {m:02d} min left" if m else f"about {h} h left"


# --------------------------------------------------------------------------- #
# How a library is organized: remembered in its records, or worked out from its folders
# --------------------------------------------------------------------------- #
LAYOUT_KEYS = ("layout", "raw_split", "camera_videos", "other_videos", "no_date")
NO_DATE = {"file_date": "Use the file's date (what the camera saved)", "undated": "Put it in an \"Undated\" folder"}
_OPTION_LABELS = {"layout": LAYOUTS, "raw_split": RAW_SPLIT, "camera_videos": CAMERA_VIDEOS,
                  "other_videos": OTHER_VIDEOS, "no_date": NO_DATE}
_OPTION_NAMES = {"layout": "Photo folders", "raw_split": "RAW / JPEG", "camera_videos": "Camera videos",
                 "other_videos": "Other videos", "no_date": "No date inside"}


def profile_of(settings: dict) -> dict:
    return {k: settings.get(k, DEFAULT_SETTINGS[k]) for k in LAYOUT_KEYS}


def profile_diff(a: dict | None, b: dict | None) -> list[str]:
    """Options that are set in both and differ."""
    if not a or not b:
        return []
    return [k for k in LAYOUT_KEYS if k in a and k in b and a[k] != b[k]]


def describe_option(key: str, value: str) -> str:
    label = _OPTION_LABELS.get(key, {}).get(value, value)
    return label.split("   (")[0]


def describe_profile(p: dict, keys=None) -> str:
    return "\n".join(f"{_OPTION_NAMES[k]}: {describe_option(k, p[k])}" for k in (keys or LAYOUT_KEYS) if k in p)


def is_library(p: Path) -> bool:
    m = p / META_NAME
    return exists(m / DB_NAME) or exists(m / INDEX_NAME)


def find_library_near(p: Path) -> tuple[Path, str] | None:
    """A Millésime library at, above or just below the folder picked: (its folder, 'here' / 'above' /
    'below'). Picking D:\\Library\\Photos, or D:\\ when the library is D:\\Library, finds D:\\Library."""
    try:
        if is_library(p):
            return p, "here"
        for parent in list(p.parents)[:6]:
            if is_library(parent):
                return parent, "above"
        found = [c for c in os.scandir(L(p)) if c.is_dir() and not c.name.startswith(".") and is_library(Path(c.path))]
        if len(found) == 1:
            return Path(found[0].path.removeprefix("\\\\?\\")), "below"
    except OSError:
        pass
    return None


def read_profile(lib: Path) -> dict | None:
    """The sort options this library was last filled with (None for libraries from before 1.9)."""
    try:
        con = open_db(lib)
        row = con.execute("SELECT value FROM meta WHERE key='profile'").fetchone()
        con.close()
        if row:
            p = json.loads(row[0])
            return {k: p[k] for k in LAYOUT_KEYS if k in p}
    except Exception as e:
        log_error("Reading how the library is organized", e)
    return None


def save_profile(lib: Path, settings_or_profile: dict) -> None:
    try:
        con = open_db(lib)
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('profile', ?)",
                        (json.dumps(profile_of(settings_or_profile)),))
        con.close()
    except Exception as e:
        log_error("Saving how the library is organized", e)


_YEAR_RE = re.compile(r"^(19[0-9]{2}|20[0-9]{2})$")
_MONTH_RE = re.compile(r"^(0[1-9]|1[0-2]) - ([A-Za-z]+)$")
MEDIA_KINDS = {"raw", "photo", "video"}


def parse_place(folder: str) -> dict | None:
    """Recognize a folder Millésime made, in any of its layouts:
    'Photos/2019/07 - July/2019-07-04/RAW' -> {top: Photos, layout: year_month_day, y: 2019, m: 7, d: 4,
    sub: RAW}. None for folders it didn't make (Photos/Events/Wedding)."""
    parts = folder.split("/") if folder else []
    if not parts or parts[0] not in (LIB["photo"], LIB["video"]):
        return None
    top, rest = parts[0], parts[1:]
    out = {"top": top, "layout": None, "y": None, "m": None, "d": None, "sub": "", "undated": False, "flat": False}
    if top == LIB["video"] and not rest:
        out["flat"] = True
        return out
    if not rest:
        return None
    i = 1
    if rest[0] == UNDATED:
        out["undated"] = True
    elif _YEAR_RE.match(rest[0]):
        y = int(rest[0])
        out["y"], out["layout"] = y, "year"
        if len(rest) > 1:
            mm = _MONTH_RE.match(rest[1])
            if mm and MONTHS[int(mm.group(1)) - 1] == mm.group(2):
                out["m"], out["layout"], i = int(mm.group(1)), "year_month", 2
                if len(rest) > 2:
                    md = re.match(rf"^{y}-{mm.group(1)}-(0[1-9]|[12]\d|3[01])$", rest[2])
                    if md:
                        out["d"], out["layout"], i = int(md.group(1)), "year_month_day", 3
            else:
                mn = re.match(rf"^{y}-(0[1-9]|1[0-2])$", rest[1])
                if mn:
                    out["m"], out["layout"], i = int(mn.group(1)), "year_monthname", 2
    else:
        return None
    sub = rest[i:]
    if len(sub) > 1:
        return None
    if sub:
        allowed = {"RAW", "JPEG", "Videos"} if top == LIB["photo"] else set()
        if sub[0] not in allowed:
            return None
        out["sub"] = sub[0]
    return out


def _unalias(rel: str, aliases: dict | None) -> tuple[str, str]:
    """The folder Millésime would call this (before the user renamed it), and the renamed folder's
    original name if it's inside one."""
    if not aliases:
        return rel, ""
    hit = ""
    for _ in range(10):
        best_k, best_v = "", ""
        for k, v in aliases.items():
            if v != k and (rel == v or rel.startswith(v + "/")) and len(v) > len(best_v):
                best_k, best_v = k, v
        if not best_v:
            break
        rel = best_k + rel[len(best_v):]
        hit = hit or best_k
    return rel, hit


def _split(rel: str) -> tuple[str, str]:
    folder, _, name = rel.rpartition("/")
    return folder, name


def detect_layout(lib: Path, aliases: dict | None = None, limit: int = 50000, cancel=lambda: False) -> dict:
    """Work out the sort options a library was filled with by looking at its folders. Returns
    {"profile": {only the options there was evidence for}, "recognized": n, "custom": n}."""
    votes: dict[str, dict[str, int]] = {k: {} for k in LAYOUT_KEYS}

    def vote(k, v, n=1):
        votes[k][v] = votes[k].get(v, 0) + n

    recognized = custom = 0
    for top in (LIB["photo"], LIB["video"]):
        root = lib / top
        if not exists(root):
            continue
        for rel, _size, _m in walk_files(root, cancel):
            name = rel.rpartition("/")[2]
            kind = kind_of(os.path.splitext(name)[1].lower())
            if kind not in MEDIA_KINDS:
                continue
            generic, _ = _unalias(f"{top}/{rel}", aliases)
            place = parse_place(_split(generic)[0])
            if not place:
                custom += 1
                continue
            recognized += 1
            if place["layout"]:
                vote("layout", place["layout"])
            if place["undated"]:
                vote("no_date", "undated")
            if place["top"] == LIB["photo"]:
                if kind == "video":
                    vote("camera_videos", "photos_sub" if place["sub"] == "Videos" else "photos_mixed")
                elif kind == "raw":
                    vote("raw_split", "raw_in_sub" if place["sub"] == "RAW" else "together")
                elif place["sub"] == "JPEG":
                    vote("raw_split", "split")
                # a JPEG loose in the month folder fits both "together" and "RAW folder inside"
            elif kind == "video":
                if place["flat"]:
                    vote("other_videos", "flat")
                elif CAMERA_NAME_RE.match(name):
                    vote("camera_videos", "videos")
                else:
                    vote("other_videos", "by_date")
            if recognized + custom >= limit:
                break
    rs = votes.pop("raw_split")
    prof = {}
    for k, vs in votes.items():
        if vs:
            prof[k] = max(vs.items(), key=lambda kv: kv[1])[0]
    if rs.get("split"):
        prof["raw_split"] = "split"
    elif rs.get("raw_in_sub", 0) > rs.get("together", 0):
        prof["raw_split"] = "raw_sub"
    elif rs:
        prof["raw_split"] = "together"
    return {"profile": prof, "recognized": recognized, "custom": custom}


# --------------------------------------------------------------------------- #
# Reorganizing a library: moving what's there into a different layout, on the same drive
# --------------------------------------------------------------------------- #
MOVING_NAME = "moving.csv"     # what's being moved right now (so a crash can be put right)
_OS_JUNK = {"thumbs.db", ".ds_store"}   # regenerated by Windows / macOS; never stop a folder being tidied away


def _date_fits(place: dict, dt: datetime | None) -> bool:
    if place["undated"] or place["flat"]:
        return True
    if dt is None:
        return False
    return (dt.year == place["y"] and (place["m"] is None or dt.month == place["m"])
            and (place["d"] is None or dt.day == place["d"]))


def _attach_sidecars(names: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Main files in a folder, and the sidecars that travel with each (IMG_1.xmp / IMG_1.CR2.xmp)."""
    mains = [n for n in names if os.path.splitext(n)[1].lower() not in SIDECAR_EXT]
    cars = [n for n in names if os.path.splitext(n)[1].lower() in SIDECAR_EXT]
    by_name = {n.lower(): n for n in mains}
    by_stem: dict[str, list[str]] = {}
    for n in mains:
        by_stem.setdefault(os.path.splitext(n)[0].lower(), []).append(n)
    out: dict[str, list[str]] = {}
    for c in cars:
        key = os.path.splitext(c)[0].lower()
        owner = by_name.get(key)
        if not owner:
            cands = by_stem.get(key, [])
            raws = [n for n in cands if os.path.splitext(n)[1].lower() in RAW_EXT]
            owner = (raws or cands or [None])[0]
        if owner:
            out.setdefault(owner, []).append(c)
    return mains, out


def _numbered_name(taken: set[str], folder: str, name: str, cars: list[str]) -> str:
    """name, or name_1, name_2 … so that it and its sidecars are all free in the folder."""
    stem, ext = os.path.splitext(name)
    n = 0
    while True:
        new_stem = stem if n == 0 else f"{stem}_{n}"
        names = [new_stem + ext] + [_sidecar_name(Path(c), stem, ext, new_stem) for c in cars]
        if all(_join(folder, x).lower() not in taken for x in names):
            return new_stem + ext
        n += 1


def plan_reorganize(lib: Path, settings: dict, aliases: dict | None = None,
                    progress=lambda done, total, what: None, cancel=lambda: False) -> dict:
    """Work out where every photo and video already in the library would go with these options.
    Nothing is moved. Only files in folders Millésime made are moved; folders made or renamed by
    hand, and files whose date doesn't match their folder, stay where they are."""
    reconcile_moves(lib)
    index = DestIndex(lib)
    index.build(lambda n: progress(0, 0, f"Looking through the library… {n:,} files"), cancel)
    index.close()
    con = open_db(lib)
    known = {d: (t, s) for d, t, s in con.execute(
        "SELECT dest, taken, date_source FROM card_files WHERE dest != '' AND taken != ''")}
    con.close()
    by_folder: dict[str, list[str]] = {}
    for rel in index.info:
        folder, name = _split(rel)
        generic, _ = _unalias(folder, aliases)
        if generic.split("/")[0] in (LIB["photo"], LIB["video"]):
            by_folder.setdefault(folder, []).append(name)
    total = sum(len(v) for v in by_folder.values())
    taken = set(index.taken)
    moves: list[dict] = []
    stats = {"unchanged": 0, "custom": 0, "renamed": 0, "mismatch": 0, "renumbered": 0, "read": 0}
    done = 0
    last_said = 0.0
    for folder, names in sorted(by_folder.items()):
        if time.monotonic() - last_said > 0.3:
            last_said = time.monotonic()
            progress(done, total, f"Working out where everything goes… {folder.replace('/', ' › ')}")
        mains, cars_of = _attach_sidecars(names)
        generic, alias_key = _unalias(folder, aliases)
        place = parse_place(generic)
        for name in sorted(mains):
            if cancel():
                raise Cancelled()
            done += 1 + len(cars_of.get(name, []))
            kind = kind_of(os.path.splitext(name)[1].lower())
            if kind not in MEDIA_KINDS or is_temp_name(name) or name.endswith(".damaged-copy"):
                continue
            if not place:
                stats["custom"] += 1
                continue
            rel = _join(folder, name)
            rec = known.get(rel)
            dt, src = None, ""
            if rec:
                try:
                    dt, src = datetime.fromisoformat(rec[0]), rec[1] or ""
                except ValueError:
                    rec = None
            if not rec:
                if time.monotonic() - last_said > 0.3:
                    last_said = time.monotonic()
                    progress(done, total, f"Reading the date inside {name}")
                dt, src = taken_date(lib / rel, True)
                stats["read"] += 1
            if not _date_fits(place, dt):
                stats["mismatch"] += 1
                continue
            if settings.get("no_date") == "undated" and src == "file date":
                dt = None
            camera = kind == "video" and (place["top"] == LIB["photo"] or bool(CAMERA_NAME_RE.match(name)))
            it = Item(src=Path(name), size=index.info[rel][0], kind=kind, camera=camera, taken=dt)
            plan([it], settings, {}, None)
            new_generic = it.folder
            if new_generic == generic:
                stats["unchanged"] += 1
                continue
            if alias_key and not (new_generic == alias_key or new_generic.startswith(alias_key + "/")):
                stats["renamed"] += 1      # inside a folder the user named: leave it there
                continue
            new_folder = apply_aliases(new_generic, aliases)
            if new_folder == folder:
                stats["unchanged"] += 1
                continue
            cars = cars_of.get(name, [])
            new_name = _numbered_name(taken, new_folder, name, cars)
            if new_name != name:
                stats["renumbered"] += 1
            stem, ext = os.path.splitext(name)
            new_stem = os.path.splitext(new_name)[0]
            pairs = [(rel, _join(new_folder, new_name))]
            pairs += [(_join(folder, c), _join(new_folder, _sidecar_name(Path(c), stem, ext, new_stem))) for c in cars]
            for _a, b in pairs:
                taken.add(b.lower())
            moves.append({"pairs": pairs, "size": it.size})
    progress(total, total, "")
    return {"moves": moves, "files": total, "settings": profile_of(settings), **stats}


def _move_no_overwrite(src: Path, dst: Path) -> None:
    """Move within the library drive. Never replaces anything that's there."""
    os.makedirs(L(dst.parent), exist_ok=True)
    if os.name == "nt":
        os.rename(L(src), L(dst))         # raises FileExistsError instead of overwriting
        return
    if os.path.lexists(dst):
        raise FileExistsError(str(dst))
    try:
        os.link(src, dst)                 # fails if dst appeared meanwhile
        os.unlink(src)
    except FileExistsError:
        raise
    except OSError:                        # exFAT and others have no hard links
        if os.path.lexists(dst):
            raise FileExistsError(str(dst))
        os.rename(src, dst)


def _db_moved(con, pairs: list[tuple[str, str]]) -> None:
    """Keep the checksums and the card records with files that moved."""
    with con:
        for a, b in pairs:
            con.execute("DELETE FROM files WHERE rel=? AND EXISTS (SELECT 1 FROM files WHERE rel=?)", (b, a))
            con.execute("UPDATE files SET rel=? WHERE rel=?", (b, a))
            con.execute("UPDATE card_files SET dest=? WHERE dest=?", (b, a))


def _meta_get(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(con, key: str, value: str | None) -> None:
    with con:
        if value is None:
            con.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def reconcile_moves(lib: Path) -> int:
    """If a reorganize was cut off (crash, power cut, drive pulled), bring the records in line with
    what actually moved. Safe to run any number of times. Returns how many records were fixed."""
    j = lib / META_NAME / MOVING_NAME
    if not exists(j):
        return 0
    fixed = []
    try:
        with open(L(j), newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if len(r) >= 3 and r[0] in ("move", "undo")]
        for _op, a, b in (r[:3] for r in rows):
            if exists(lib / b) and not exists(lib / a):
                fixed.append((a, b))
        con = open_db(lib)
        _db_moved(con, fixed)
        logs = lib / META_NAME / LOGS_NAME
        os.makedirs(L(logs), exist_ok=True)
        undo = bool(rows) and rows[0][0] == "undo"
        name = f"{datetime.now():%Y-%m-%d_%H%M%S}_{'undo' if undo else 'reorganize'}-interrupted.csv"
        os.replace(L(j), L(logs / name))
        if rows and not undo:
            # a cut-off reorganize can still be undone; a cut-off undo can simply be run again
            _meta_set(con, "last_reorganize", name)
        con.close()
    except Exception as e:
        log_error("Finishing an interrupted reorganize", e)
    return len(fixed)


def _remove_empty_dirs(lib: Path, folders: set[str]) -> int:
    """Remove folders left empty by moving (and their empty parents). Never removes a file other
    than Thumbs.db / .DS_Store, which Windows and macOS make again on their own."""
    gone = 0
    todo = set()
    for f in folders:
        parts = f.split("/")
        for i in range(len(parts), 1, -1):          # never the top folders (Photos, Videos)
            todo.add("/".join(parts[:i]))
    for f in sorted(todo, key=lambda x: -x.count("/")):
        p = lib / f
        try:
            left = os.listdir(L(p))
            if left and all(x.lower() in _OS_JUNK for x in left):
                for x in left:
                    os.unlink(L(p / x))
                left = []
            if not left:
                os.rmdir(L(p))
                gone += 1
        except OSError:
            pass
    return gone


def _run_moves(lib: Path, pairs_list: list[list[tuple[str, str]]], op: str, progress, cancel) -> dict:
    """Move groups of files (a photo and its sidecars move together), writing each move down first."""
    meta = lib / META_NAME
    os.makedirs(L(meta), exist_ok=True)
    j = open(L(meta / MOVING_NAME), "a", newline="", encoding="utf-8")
    w = csv.writer(j)
    con = open_db(lib)
    batch: list[tuple[str, str]] = []
    moved = skipped = 0
    problems: list[str] = []
    left_folders: set[str] = set()
    total = len(pairs_list)
    try:
        for i, pairs in enumerate(pairs_list):
            if cancel():
                break
            if i % 25 == 0:
                progress(i, total, pairs[0][0])
            if not exists(lib):
                raise DriveGone("The library drive was disconnected.")
            main_a, main_b = pairs[0]
            if not exists(lib / main_a):
                skipped += 1
                problems.append(f"{main_a}: no longer there")
                continue
            if any(exists(lib / b) for _a, b in pairs):
                skipped += 1
                problems.append(f"{main_a}: something is already at {main_b}")
                continue
            for a, b in pairs:
                if not exists(lib / a):
                    continue
                w.writerow([op, a, b])
                j.flush()
                try:
                    _move_no_overwrite(lib / a, lib / b)
                except OSError as e:
                    problems.append(f"{a}: {e}")
                    continue
                batch.append((a, b))
                left_folders.add(_split(a)[0])
            moved += 1
            if len(batch) >= 200:
                os.fsync(j.fileno())
                _db_moved(con, batch)
                batch = []
        _db_moved(con, batch)
    finally:
        j.close()
        con.close()
    progress(total, total, "")
    return {"moved": moved, "skipped": skipped, "problems": problems, "left": left_folders,
            "cancelled": bool(cancel()) and moved < total}


def run_reorganize(lib: Path, the_plan: dict, before: dict | None = None,
                   progress=lambda done, total, what: None, cancel=lambda: False) -> dict:
    """Carry out a plan from plan_reorganize. Moves stay on the same drive: nothing is copied,
    deleted or overwritten. A record of every move is kept so it can be undone."""
    reconcile_moves(lib)
    res = _run_moves(lib, [m["pairs"] for m in the_plan["moves"]], "move", progress, cancel)
    res["folders_removed"] = _remove_empty_dirs(lib, res["left"])
    logs = lib / META_NAME / LOGS_NAME
    os.makedirs(L(logs), exist_ok=True)
    name = f"{datetime.now():%Y-%m-%d_%H%M%S}_reorganize.csv"
    con = open_db(lib)
    try:
        j = lib / META_NAME / MOVING_NAME
        if exists(j) and res["moved"]:
            os.replace(L(j), L(logs / name))
            _meta_set(con, "last_reorganize", name)
            _meta_set(con, "profile_before", json.dumps(before) if before else None)
            res["record"] = logs / name
        elif exists(j):
            os.unlink(L(j))                 # nothing moved: nothing to keep
        if not res["cancelled"]:
            _meta_set(con, "profile", json.dumps(the_plan["settings"]))
    finally:
        con.close()
    backup_db(lib)
    return res


def last_reorganize(lib: Path) -> tuple[Path, dict | None] | None:
    """The record of the last reorganize that can still be undone, and the options from before it."""
    try:
        con = open_db(lib)
        name, before = _meta_get(con, "last_reorganize"), _meta_get(con, "profile_before")
        con.close()
        p = lib / META_NAME / LOGS_NAME / name if name else None
        if p and exists(p):
            return p, (json.loads(before) if before else None)
    except Exception as e:
        log_error("Looking for the last reorganize", e)
    return None


def undo_reorganize(lib: Path, progress=lambda done, total, what: None, cancel=lambda: False) -> dict:
    """Put every file the last reorganize moved back where it was (and its old name), as long as
    nothing else has taken that place since."""
    reconcile_moves(lib)
    last = last_reorganize(lib)
    if not last:
        return {"moved": 0, "skipped": 0, "problems": ["Nothing to undo."], "left": set(), "cancelled": False}
    record, before = last
    with open(L(record), newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if len(r) >= 3 and r[0] == "move"]
    groups = [[(b, a)] for _op, a, b in reversed([r[:3] for r in rows])]
    res = _run_moves(lib, groups, "undo", progress, cancel)
    res["folders_removed"] = _remove_empty_dirs(lib, res["left"])
    logs = lib / META_NAME / LOGS_NAME
    con = open_db(lib)
    try:
        if exists(lib / META_NAME / MOVING_NAME):
            os.replace(L(lib / META_NAME / MOVING_NAME), L(logs / f"{datetime.now():%Y-%m-%d_%H%M%S}_undo.csv"))
        if not res["cancelled"]:
            _meta_set(con, "last_reorganize", None)
            if before:
                _meta_set(con, "profile", json.dumps(before))
    finally:
        con.close()
    res["before"] = before
    backup_db(lib)
    return res


# --------------------------------------------------------------------------- #
# Problem reports (Help > Report a problem)
# --------------------------------------------------------------------------- #
CRASH_FILE = DATA_DIR / "crash.log"
_crash_handle = None


def enable_crash_log():
    """If the program ever crashes outright, Python writes where it was to data/crash.log."""
    global _crash_handle
    try:
        import faulthandler
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _crash_handle = open(CRASH_FILE, "a", encoding="utf-8")
        _crash_handle.write(f"\n--- started {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
        _crash_handle.flush()
        faulthandler.enable(_crash_handle, all_threads=True)
    except Exception:
        pass


def thread_dump() -> str:
    """Where every part of the program is right now (shows exactly where something is stuck)."""
    import tempfile
    import faulthandler
    try:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as f:
            faulthandler.dump_traceback(f, all_threads=True)
            f.seek(0)
            return f.read()
    except Exception as e:
        return f"(couldn't get the thread dump: {e})"


def _hide_user(text: str) -> str:
    home = str(Path.home())
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    text = text.replace(home, "<home>")
    if user and len(user) > 2:
        text = re.sub(re.escape(user), "<user>", text, flags=re.I)
    return text


def _tail(p: Path, max_chars: int) -> str:
    try:
        t = p.read_text("utf-8", errors="replace")
        return t[-max_chars:] if len(t) > max_chars else t
    except OSError:
        return "(none)"


def make_report(state: dict) -> str:
    import platform
    lines = [f"{APP_NAME} problem report — {datetime.now():%Y-%m-%d %H:%M:%S}"]
    try:
        ver = (APP_DIR / "VERSION").read_text("utf-8").strip()
    except OSError:
        ver = "?"
    lines.append(f"Version: {ver}")
    lines.append(f"System: {platform.platform()} · Python {platform.python_version()}")
    for mod in ("PySide6", "exifread", "mutagen"):
        try:
            m = __import__(mod)
            lines.append(f"{mod}: {getattr(m, '__version__', '?')}")
        except Exception:
            lines.append(f"{mod}: not installed")
    lib = state.get("library")
    if lib:
        lines.append(f"Library drive: {fs_type(Path(lib)) or '?'} · {human_size(max(0, free_bytes(Path(lib))))} free")
    lines.append("")
    lines.append("== What it was doing ==")
    for k, v in state.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("== Settings ==")
    s = load_settings()
    for k in sorted(s):
        if k in ("geometry", "columns", "tree_columns"):
            continue
        lines.append(f"{k}: {s[k]}")
    lines.append("")
    lines.append("== Where each part of the program is right now ==")
    lines.append(thread_dump())
    lines.append("== Recent errors ==")
    lines.append(_tail(LOG_FILE, 20000))
    lines.append("== Crash log ==")
    lines.append(_tail(CRASH_FILE, 6000))
    return _hide_user("\n".join(lines))
