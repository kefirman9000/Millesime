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

## Copy only RAW files (or chosen types)

Set **Copy:** (under Include) to **RAW files only**, or to **Only these file types:** and type the
endings you want, e.g. `cr2, nef, jpg` (`raw` means every RAW type). Then drop a whole drive on the
window: Millésime digs through every folder on it, picks out just those files, and skips any that
are already in your library, so you can drop the same drive again later and only new files get
copied. Sidecars such as `.xmp` still travel with their photo. Set it back to **Everything ticked
under Include** to use the Include boxes again.

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
| **RAW / JPEG** | together; JPEGs in the month folder with RAW files in a `RAW` folder inside it; or separate `RAW` and `JPEG` folders inside each month |
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

## Changing your mind: reorganize the library

Decided you want **Year › Month › Day** after all, or RAW files in their own folder? Change the
options at the top, then **Tools › Reorganize the library to match these options…**. It shows
exactly what would move first (**Show Details** lists every file), then moves the photos and videos
already in the library into the new folders.

- **Moves stay on the same drive**, so it's quick: nothing is copied, deleted or overwritten. A file
  that would land on a different file with the same name gets a number (`IMG_0001_1.CR2`).
- **Sidecars move with their photo**, and the library's records (checksums, which cards were backed
  up) follow, so health checks and repeat backups keep working.
- **Your own folders are left alone:** folders you made yourself (`Photos\Events\Wedding`), folders
  you renamed (`2019-07-04 Smith Wedding`), and files whose date doesn't match their folder (moved
  there by hand) stay where they are.
- Folders left empty are tidied away.
- **Tools › Undo the last reorganize** puts every file back where it was, with its old name.
- If it's interrupted (crash, power cut, drive pulled out), every file is either moved or not, and
  the records are put right the next time the library is opened.

## Libraries it already knows

Millésime remembers how each library is organized. When you choose a library folder (or press
**Start copying**) and the options don't match how the files there are organized, it says so and
offers to **use the library's way**, **reorganize the library** to the new options, or copy anyway.
A strip under the options says the same while they differ.

Libraries made by older versions are recognized from their folders (`Photos\2019\07 - July\RAW` and
so on). If you pick a folder inside a library (`D:\Library\Photos`) or the drive it's on (`D:\`), it
offers to use the library itself. **Tools › How is this library organized?** shows what it found.

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

## Reliability and big jobs

- **A time-left estimate you can trust.** It's worked out by size (one 12 GB video isn't the same
  as one small picture) from how fast the last minute went, leaving out checking the library,
  breaks and pauses. For the first 10 seconds, while the drives get going, it says "working out the
  time left…" rather than guessing.
- **Progress inside big files.** The status line shows how far through a big file it is
  (`Copying MVI_0042.MOV · 3.1 GB of 12.4 GB`, then `Checking the copy of …`), so a long video never
  looks stuck.
- **Gentle mode** (on by default, **View** menu): copying runs at low priority so File Explorer and
  other programs stay responsive, and it takes regular breaks so the drives can catch up.
- **View › Gentle and safe copying settings…**
  - **Breaks:** how often (default every 10 minutes) and how long (default 30 seconds).
  - **Speed limit:** cap reading and writing at, say, 30–60 MB/s so a USB hard drive never runs
    flat out. Changes apply straight away, even mid-copy.
  - **Final check** (on by default): when a run ends, every new copy is read back *from the drive
    itself* (not from Windows' memory) and compared with the original. A copy that doesn't match
    is set aside as `.damaged-copy` (never deleted) and copied again next time.
  - **Skip a file that stops responding** for 2 minutes (adjustable). If a second file stops
    responding too, copying stops completely to protect the drives, and says which drive it was.
- **Each file is read once.** Photos that need comparing with the library are read into memory,
  compared, and copied from there, so cards and old drives aren't read twice.
- **Run report.** At the end, **Show Details** in the summary lists the time taken, average speed,
  breaks, retries, unusually slow files (and which drive they came from), files that stopped
  responding, and the final check. It's saved in the sort logs too, so over a few runs you can see
  whether a drive is starting to struggle.
- **Unplugged drives.** If the library drive is unplugged, copying stops cleanly and says so. If a
  card is pulled out, its remaining files wait while the others carry on. Plug it back in and
  press **Start copying** to finish.
- **Full drives.** It always leaves 2 GB free and stops cleanly before the drive fills.
- **Flaky card readers.** A file that can't be read is tried again twice before it's marked as an
  error.
- **Long random names.** Files named with long strings of random letters and numbers (download
  IDs, hashes) are shortened to their first 16 characters, e.g. `a0d206116902beee.png`. Real names
  are never changed, and any name too long for Windows is shortened to fit. The Note column and
  the log say when this happens.
- **Very long paths.** Files deep in long-named folders (common in Music) are handled past
  Windows' usual 260-character limit.
- **Checks before copying.** It warns if the library is on the drive Windows runs from (not much of
  a backup), if the drive is FAT32 and can't hold files of 4 GB or more, or if files are already on
  the library drive.
- **One window per library.** A second Millésime window can't copy into the same library at the
  same time.
- **Crash-safe records.** What's in the library, its checksums and which cards were backed up are
  kept in a small database in `.millesime` that survives crashes and power cuts, with a dated
  backup copy (the last 5 are kept). The sort log is written as it goes. Half-finished copies left
  by a crash are cleaned up on the next run (only the program's own temporary files).
- **Large libraries.** Only what changed is saved, so libraries with hundreds of thousands of files
  stay quick.

## When something goes wrong

**Help › Report a problem…** makes a report: version, settings, what it was doing, recent errors
and, if something is frozen, exactly where each part of the program is stuck. Your Windows user
name is hidden. **Copy to clipboard** or **Save to desktop**, then send it with a sentence about
what happened. If nothing moves for 5 minutes, that "where it's stuck" snapshot is saved
automatically, and if the program ever crashes outright, `data/crash.log` records where.
**Help › Open the error log** shows the raw log.

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
