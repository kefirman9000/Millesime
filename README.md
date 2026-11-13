# Millésime

*millésime (n.): the year a wine was made. This one files your photos by theirs.*

Drag memory-card folders (or any folders of old photos) onto the window and Millésime copies
every photo and video onto your new drive in **Year › Month** folders, by the date each picture
was actually taken. It never copies the same file twice and never overwrites anything.

```
Photos Drive/
├── 2014/
│   ├── 06 - June/
│   └── 12 - December/
├── 2019/
│   ├── 07 - July/
│   │   ├── IMG_0001.CR2
│   │   ├── IMG_0001.xmp        ← sidecars travel with their photo
│   │   ├── IMG_0001_1.CR2      ← a different picture that was also called IMG_0001.CR2
│   │   └── MVI_0042.MP4
│   └── …
└── Undated/                    ← only if you choose it, for files with no date at all
```

## How to use it

1. Double-click **Start Millesime (Windows).bat** (Mac: **Start Millesime (Mac).command**).
   The first start sets itself up (about a minute; needs Python from python.org).
2. Press **Choose…** and pick the new hard drive (or a folder on it).
3. Drop memory-card folders or files on the window, or use **Add a folder…**. Drop as many as you
   like; they all go in one list.
4. Check the list (when each file was taken and where it will go), then press **Start copying**.
   Tick **Start copying as soon as I drop files** to skip this step.

When it finishes you get a summary: how many were copied, how many were given a number because
of a name clash, and how many were skipped as duplicates. Double-click any row to open its folder.

## The safety rules

- **Your cards are only read.** Nothing on them is moved, renamed or deleted. Format them in the
  camera yourself once you're happy.
- **Nothing on the drive is ever overwritten.** A different file with the same name is saved with
  a number: `IMG_0001_1.CR2`, `IMG_0001_2.CR2`, …
- **Duplicates are exact copies only.** A file is skipped only if it's byte-for-byte identical to
  one already anywhere on the destination (same size and same checksum), even under another name.
  The same picture saved at another size or edited is *not* a duplicate and is copied.
- **Every copy is checked.** It's written under a temporary name, compared with the original's
  checksum, and only then given its real name. A bad copy (failing card, full drive) is removed and
  shown as an error; press **Start copying** again to retry those.
- **Stopping is safe.** Press **Stop** (or close the window) any time; nothing is left
  half-copied. Drop the same cards again later and it carries on, skipping what's already there.

## Dates

The date comes from inside the file: the EXIF "date taken" of RAW and JPEG/HEIC files (Canon
CR2/CR3, Nikon NEF, Sony ARW, Fuji RAF, Olympus ORF, Panasonic RW2, DNG and most others), and the
recording date of MP4/MOV videos. If a file has no date inside it, you choose under
**If a file has no date inside it**: use the file's own date (what the camera saved on the card,
the default) or put it in an **Undated** folder. Rows using the file's date are marked
"(file date)" in the list.

**Folders** offers Year › Month (`2019\07 - July`), Year › Month › Day, Year › `2019-07`, or
Year only. **Include** chooses RAW, other photos, and videos.

## Good to know

- Sidecar files (`.xmp`, `.thm`, `.aae` …) are copied with the photo they belong to and renamed
  to match if the photo gets a number. If a photo is a duplicate but its `.xmp` isn't on the drive
  yet, the `.xmp` is added next to the existing copy.
- Junk such as `Thumbs.db`, `.DS_Store`, `._` files and `System Volume Information` is ignored.
- What's on the destination is remembered in a hidden `.millesime` folder on the drive, so later
  runs start quickly. A log of what happened to every file (a spreadsheet-ready CSV) is saved
  there too: **File › Open sort logs**.
- The computer is kept awake while copying.
- The drive can be one you've already put photos on by hand; they count for duplicate checks.

## Updates

Millésime checks GitHub for a newer **release** when it opens and **only downloads it if you say
yes**. Each download is checked against its SHA-256 checksum before anything is installed, and the
version you had is backed up first (**Help › Roll back to an earlier version…**). Turn the
automatic check off in the Help menu. Your settings and the private Python environment are kept.

## Support

If Millésime saves you time, you can support its development through
[GitHub Sponsors](https://github.com/sponsors/kefirman9000). Thank you!

## Sister apps

- [Sortilège](https://github.com/kefirman9000/sortilege) sorts photos by what's in them and who's
  in them.
