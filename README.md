# Millésime

*millésime (n.): the year a wine was made. This one files your photos by theirs.*

Drag memory cards, old drives or folders onto the window and Millésime copies everything into
one tidy library on your new drive: photos and videos by the date they were taken, music by
artist and album, documents by type. It never copies the same file twice and never overwrites
anything.

```
D:\Library\
├── Photos\
│   └── 2019\
│       └── 07 - July\
│           ├── IMG_0001.CR2
│           ├── IMG_0001.xmp        ← sidecars travel with their photo
│           ├── IMG_0001_1.CR2      ← a different picture that was also called IMG_0001.CR2
│           └── Videos\
│               └── MVI_0042.MOV    ← camera videos with the photos from that month
├── Videos\
│   └── 2015\01 - January\Movie.mkv ← other videos
├── Music\
│   └── Artist\
│       └── Album\
│           ├── 01 Song.mp3
│           └── cover.jpg           ← album covers stay with their songs
├── Documents\                      ← if you include them
│   ├── PDFs\  ├── Word & Text\  ├── Spreadsheets\  ├── Presentations\  └── Ebooks\
└── Other\                          ← "Everything else", one folder per file type (ZIP, EXE …)
```

## How to use it

1. Double-click **Start Millesime (Windows).bat** (Mac: **Start Millesime (Mac).command**).
   The first start sets itself up (about a minute; needs Python from python.org).
2. Press **Choose…** and pick (or make) a library folder on the new drive, e.g. `D:\Library`.
3. Drop memory cards, old drives or folders on the window, or use **Add a folder…**.
4. Check the list, then press **Start copying**. Or tick **Start copying as soon as I drop files**
   to have it start straight away.
5. **Keep dropping** cards and folders while it copies. They join the queue and are copied next.

The window shows an example of where each kind of file will go, so you can see the effect of
each option before you copy anything.

Drop anywhere on the window. While you drag, it says what you're about to add ("Drop to add
1 drive, 2 folders"). After each drop, a line above the list sums up what was found on that card
or folder (photos, videos, songs …), whether it was backed up before, and how many tiny pictures
were skipped.

Before you copy, the **Where files will go** tab shows the folders your files will land in as a
tree, with how many files and how much space each takes, e.g. `Photos › 2019 › 07 - July (212)`.
The **All files** tab lists every file. Double-click a folder to open it.

## Naming, moving and skipping

You're never asked to name anything, but you can, right in the **Where files will go** tree:

- **Rename a folder:** right-click › Rename (or press **F2**). Choose **Year › Month › Day** folders
  and rename a day to `2019-07-04 Smith Wedding`, or rename a whole month. The new name is
  remembered for this library, so files from that date added later go there too. Folders already
  on the drive aren't renamed. **Tools › Forget renamed folders** goes back to the normal names.
- **Move a folder:** drag it onto another folder in the tree, or right-click › Move to… and pick or
  type a place such as `Photos\Events\Wedding`.
- **Skip files:** right-click a folder › Skip these files. They're shown as *Skipped* and not
  copied.

In the **All files** list, select rows and right-click to skip them, include them again, move them
somewhere else, or put them back where the options say.

**Skip pictures under 30 KB** (next to Include) leaves out icons, thumbnails and web images,
which real photos never are. RAW files and album covers are always kept. Set it to 0 to keep
every picture.

While copying you can **Pause** (it stops right away, even in the middle of a big video) and
**Resume**, or **Stop**. When it finishes you get a Windows notification and a sound, so you can
walk away during long copies (turn it off under **View**).

## How to sort

| Option | Choices |
|---|---|
| **Include** | Photos & RAW, Videos, Music, Documents, Everything else |
| **Photo folders** | Year › Month (`2019\07 - July`), Year › Month › Day, Year › `2019-07`, Year only |
| **RAW / JPEG** | together, or in separate `RAW` and `JPEG` folders inside each month |
| **Camera videos** | with their photos in a `Videos` folder (default), mixed in with the photos, or in the Videos library by date |
| **Other videos** | Videos library by date, or all together in `Videos` |
| **No date inside** | use the file's own date (what the camera saved), or an `Undated` folder |

**Camera videos** are recognized by where they were (a card's `DCIM`, `AVCHD` or `PRIVATE`
folder, or next to camera photos) or by the names cameras and phones give them: `MVI_`, `DSC`,
`DSCF`, `C0001`, `GOPR`/`GX01`, `DJI_`, `VID_`, `PXL_`, `IMG_….MOV`, `00001.MTS` and others. Any
other video (downloads, films, screen recordings) counts as an "other video".

**Music** is sorted by the album artist and album written inside each song (MP3, FLAC, M4A, OGG,
WMA, WAV and more), so an album with guest artists stays in one folder. Songs without tags stay
together under `Unknown Artist` and the name of the folder they came from. `cover.jpg`,
`folder.jpg` and similar pictures next to songs go with the album, not into Photos.

## The safety rules

- **Your cards and old drives are only read.** Nothing on them is moved, renamed, deleted or written.
- **Nothing in the library is ever overwritten.** A different file with the same name is saved
  with a number: `IMG_0001_1.CR2`, `IMG_0001_2.CR2`, …
- **Duplicates are exact copies only.** A file is skipped only if it's byte-for-byte identical to
  one already anywhere in the library (same size and same checksum), even under another name.
  The same picture at another size, or edited, is *not* a duplicate and is copied.
- **Every copy is checked.** It's written under a temporary name, compared with the original's
  checksum, and only then given its real name. A bad copy (failing card, full drive) is removed and
  shown as an error; press **Start copying** again to retry those.
- **Stopping is safe.** Press **Stop** (or close the window) any time; nothing is left
  half-copied. Drop the same cards again later and it carries on, skipping what's already there.

## Backing up the same cards again

Millésime remembers every card it has backed up (in the library's hidden `.millesime` folder,
**never on the card**). When you drop a card again, a note above the list says when it was last
backed up and how many files are new since then. Files it already backed up (same place on the
card, same size, same date) are skipped **without being opened at all**, so a repeat backup only
reads the card's list of files plus the new ones. Cards are recognised by their volume serial
number, which only changes if the card is formatted.

Your cards are never written to: no index files, no markers, nothing. They are only read.

## Health check

**Tools › Check the library for damaged files…** re-reads every file in the library and compares
it with the checksum saved when it was copied. It reports files that have changed without being
edited (a sign the drive is failing), files that can't be read, and files that have gone missing.
Files you edited on purpose (new date or size) aren't called damaged; their new checksum is saved.
It only reads the library drive, changes nothing, can be stopped at any time, and saves a report in
the sort logs. Running it every few months is a good habit for a backup drive.

## Dates

The date comes from inside the file: the EXIF "date taken" of RAW and JPEG/HEIC files (Canon
CR2/CR3, Nikon NEF, Sony ARW, Fuji RAF, Olympus ORF, Panasonic RW2, DNG and most others), and the
recording date of MP4/MOV videos. Rows that fell back to the file's own date are marked
"(file date)" in the list. Old cameras with a dead clock battery can put photos in years like
2000 or 2009; sort the list by the **Taken** column to spot them.

## Good to know

- Sidecar files (`.xmp`, `.thm`, `.aae` …) are copied with their photo and renamed to match if
  the photo gets a number. If a photo is a duplicate but its `.xmp` isn't in the library yet, the
  `.xmp` is added next to the existing copy.
- Junk such as `Thumbs.db`, `.DS_Store`, `._` files, shortcuts and `System Volume Information`
  is ignored.
- What's in the library is remembered in a hidden `.millesime` folder there, so later runs start
  quickly. A log of what happened to every file (a spreadsheet-ready CSV) is saved there too:
  **File › Open sort logs**.
- The computer is kept awake while copying.
- Format the new drive as NTFS (or exFAT if a Mac uses it too), not FAT32, which can't hold files
  over 4 GB.

## Looks

- **View › Match Windows / Light / Dark.** By default it follows Windows, including when Windows
  switches on its own.
- **File › Make a desktop shortcut** puts a Millésime icon on your desktop.
- The window remembers its size, position, column widths and which tab you were on.

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
- [Solfège](https://github.com/kefirman9000/solfege) sorts music.
