# True Covers for Kindle

**True Covers for Kindle** is a calibre interface plugin that ensures books downloaded from the calibre Content Server to a Kindle display their proper cover art. When you download an `AZW3` book wirelessly through the calibre Content Server using the Kindle's experimental browser, the Kindle normally ignores the cover image inside the file unless the file header contains a valid Kindle edition ID or is marked as a Personal Document. **True Covers for Kindle** prepares your `AZW3` files automatically in the calibre library so that whenever you download them to your Kindle, their covers appear right away.

---

## Why Kindle Covers Go Missing (and How This Plugin Fixes Them)

Every Kindle edition sold on Amazon has a unique 10-character product code called an **ASIN** (Amazon's ID for the Kindle edition, which always starts with `B0`, such as `B000FC1MCS`).

When a Kindle opens a book marked as a regular retail book (`EBOK`), it checks the `AZW3` file's internal header for that `B0...` ASIN and looks up the cover on Amazon's cover server:

- **If the file has no `B0...` ASIN** (or only has a 10-digit print ISBN or calibre's internal book ID), the Kindle shows a generic blank cover.
- **If the `B0...` ASIN belongs to an older or discontinued listing** whose cover image was removed from Amazon's servers, the Kindle still shows a blank cover.
- **If the book has no Kindle edition on Amazon at all** (such as independent releases, fan fiction, comics, magazines, or personal documents), there is no `B0...` ASIN to look up. However, if the file is marked as a **Personal Document** (`PDOC`) with the cover embedded inside the `AZW3` file, the Kindle reads the embedded cover directly from the file.

**True Covers for Kindle** handles all three cases automatically inside your calibre library.

---

## Features & Functions

### 1. Fix Kindle Cover

**What it does:**
1. **Ensures an `AZW3` file exists:** If a book only has a reflowable format (`EPUB`, `MOBI`, `FB2`, `DOCX`, `HTMLZ`, `RTF`, or `TXT`), the plugin converts it to `AZW3` while keeping your original format intact.
2. **Finds and verifies the Kindle edition ASIN:** Looks up the book's 10-character `B0...` ASIN (Amazon's ID for the Kindle edition) across Goodreads, Amazon's Kindle store, DuckDuckGo, and OpenLibrary, filters out study guides and summaries, and checks Amazon's cover server to confirm a real cover image is actually hosted for that ASIN.
3. **Embeds the cover and stamps the `AZW3` headers:** Writes your calibre library cover into the `AZW3` file (`EXTH 201`) and stamps the verified `B0...` ASIN into both the primary and secondary header slots (`EXTH 113` and `EXTH 504`) along with the retail book flag (`EXTH 501 = EBOK`) across all MOBI and KF8 headers in the file.

**When to use it:**
- Runs automatically whenever you add a new `EPUB`, `MOBI`, or `AZW3` book to calibre (when enabled).
- Run it manually on one book, a selection of books, or your entire library from the toolbar menu or right-click menu.
- If automatic lookup picks the wrong edition or cannot find one, use **Set Kindle ASIN (or Paste Amazon URL) for Selected Book...** in the toolbar menu to paste an ASIN or Amazon store link directly.

**Settings that affect it (`Preferences` → `Plugins` → `True Covers for Kindle`):**
- **Automatically run Fix Kindle Cover when books are added to calibre:** Processes newly added books in the background.
- **Smart Skip:** Inspects existing `.azw3` headers in under 2 milliseconds and skips books that already have a verified `B0...` ASIN + embedded cover (`EBOK`) or Personal Document header (`PDOC`). Use **Force Fix Kindle Cover for Selected Books** from the toolbar menu when you want to bypass Smart Skip and the local cache.
- **Verify the ASIN has a live cover on Amazon's server and matches title/author:** Confirms Amazon's image server actually returns a valid JPEG cover for the `B0...` ASIN and rejects summary/study-guide listings.
- **Automatically convert EPUB/MOBI to AZW3 if AZW3 is missing:** Creates an `AZW3` format using calibre's `ebook-convert` while keeping the source format.
- **Highlight books still missing an AZW3 format (`marked:no_azw3`):** Highlights books that could not be converted so you can filter them in calibre's search bar.
- **Embed calibre cover and metadata into the AZW3 file in-place:** Writes `EXTH 201` into the `AZW3` container.
- **Remove temporary `ORIGINAL_EPUB` / `ORIGINAL_AZW3` backups:** Cleans up backup files created during metadata embedding.
- **Look up the Kindle ASIN via Goodreads, Amazon, DuckDuckGo, and OpenLibrary:** Enables online lookup when the book metadata does not already have a `B0...` ASIN.
- **Stamp AZW3 binary headers (`EXTH 113`, `504`, and `501=EBOK`):** Patches the binary MOBI/KF8 headers in the `AZW3` file.
- **Allow print ISBN-10 fallback (disabled by default):** Allows using a 10-digit print ISBN when no `B0...` Kindle ASIN exists. Leave disabled unless needed, as Kindles frequently show blank covers for print ISBNs when a `B0...` Kindle edition exists.

---

### 2. Auto-Import

**What it does:**
- Monitors a configured download or watch folder on a background timer (or on demand via **Run Auto-Import Now**).
- Discovers newly finished ebook files once they have stopped changing on disk, reads their metadata (along with any sidecar `.opf` file or `cover.jpg` / `cover.png` / `folder.jpg` in the same folder), and imports copies into your calibre library.
- **Strictly non-destructive to your watched folder:** Auto-Import opens files in the watched folder in read-only mode. It **never** moves, renames, modifies, or deletes any files or folders in your watched directory, so torrent downloads stay completely intact and can continue seeding without interruption.
- Automatically skips unfinished partial downloads (`.part`, `.!qb`, `.crdownload`, `.tmp`, `.aria2`) and tracks imported file signatures (`size:mtime`) so files are not re-imported on later scans. If a previously imported book still needs a cover fix, running Auto-Import again will re-queue that book for **Fix Kindle Cover**.
- Hands newly imported books directly to **Fix Kindle Cover**.

**When to use it:**
- Use Auto-Import if you download books into a staging or torrent folder and want calibre to import copies and prepare their Kindle covers automatically without touching the original files.

**Settings that affect it:**
- **Enable Auto-Import background folder monitoring:** Turns periodic background scanning on or off.
- **Automatically run Fix Kindle Cover on newly imported books:** Immediately runs **Fix Kindle Cover** on books added by Auto-Import.
- **Watch Folder:** The directory Auto-Import scans.
- **Poll interval (sec):** How often (10–3600 seconds, default `45` seconds) Auto-Import checks the watch folder. Background checks that find no new settled files return silently without spinning calibre's Jobs indicator.
- **File settle age (sec):** How long (0–600 seconds, default `10` seconds) a file must go without modification before Auto-Import imports it, preventing incomplete downloads from being read mid-write.
- **Extensions:** Comma-separated list of file extensions to scan (default: `epub, azw3, mobi, pdf, cbz, cbr`).

---

### 3. Mark as Personal Document

**What it does:**
- Converts the book to `AZW3` if needed (supporting `EPUB`, `MOBI`, `CBZ`, `CBR`, `PDF`, `FB2`, `DOCX`, `HTMLZ`, `RTF`, and `TXT`).
- Embeds the cover image from your calibre library directly into the `AZW3` file (`EXTH 201`).
- Stamps the `AZW3` header with `EXTH 501 = PDOC` and a local document key (`PDOC000123`), and records `true_covers_kindle:personal_document` in the book's calibre Identifiers.

**When to use it (and when it runs automatically):**
- Use it manually from the right-click menu or toolbar menu on any book, comic, magazine, fanfic, or document that does not have a Kindle edition on Amazon.
- When **Fix Kindle Cover** finishes and one or more books have no live Kindle edition on Amazon, the plugin follows your configured fallback policy:
  1. **Ask to run Mark as Personal Document (PDOC) — Auto-Confirm after Idle Timeout (default):** Shows a summary of the books that had no Kindle edition and offers to run **Mark as Personal Document** on just those books, automatically confirming after the idle countdown (default `60` seconds) if you step away.
  2. **No-Confirm: Automatically run Mark as Personal Document (PDOC) with Embedded Cover:** Immediately marks unmatched books as Personal Documents during **Fix Kindle Cover** without stopping to ask.
  3. **No-Confirm: Do Not Convert to Personal Document — Only Highlight with `marked:no_asin`:** Leaves unmatched books untouched and highlights them in calibre so you can inspect them later by searching `marked:no_asin`.

**What the book looks like on the Kindle afterward:**
- Because the file is stamped with `EXTH 501 = PDOC`, the Kindle reads the cover embedded inside the `AZW3` file rather than querying Amazon's servers, so your calibre cover art displays properly in the library grid and lockscreen.
- On the Kindle, the item is categorized under **Personal Documents** (or **Docs**) rather than **Books**. Reading progress, highlights, bookmarks, and fonts work the same way as any other `AZW3` book.

**Settings that affect it:**
- **When Fix Kindle Cover cannot find a Kindle edition ASIN:** Selects between the three fallback policies above.
- **Idle auto-confirm timeout (sec):** Countdown duration (`5`–`600` seconds, default `60` seconds) before the confirmation dialog automatically proceeds.
- **Always highlight books still missing a Kindle ASIN with `marked:no_asin`:** Highlights any unmatched books that were not marked as Personal Documents.
- **Show completion summary dialog after manual runs even when all books succeed:** Controls whether a completion summary dialog pops up after manual jobs when there are no failures.

---

## Using the Right-Click Context Menu

You can run **Fix Kindle Cover** or **Mark as Personal Document** directly from the calibre library view by right-clicking one book or a multi-book selection:

1. Select one or more books in the calibre library list (or cover grid).
2. Right-click the selection and hover over **True Covers for Kindle**.
3. Click **Fix Kindle Cover** or **Mark as Personal Document**.

Both actions run as background jobs in calibre's **Jobs** panel (bottom-right corner) so the calibre window never freezes.
- When nothing is selected, the book-specific menu actions are disabled.
- If you select more than **25 books**, the plugin asks for confirmation before starting the batch job.
- When the batch finishes, a summary dialog reports how many books succeeded, how many were skipped, and how many failed (with the reason, such as no Kindle edition found on Amazon or missing `AZW3` format). If any books failed **Fix Kindle Cover**, the summary dialog offers to run **Mark as Personal Document** on just those books right away.

**Setup Note:**
On first install, **True Covers for Kindle** automatically adds itself to both your main toolbar and the library view's right-click context menu (and automatically replaces any older `Stoker` menu entry if you are upgrading). If you ever customize your menus and want to re-add it manually, open **Preferences** → **Toolbars & menus** → **The context menu for books in the calibre library**, move **True Covers for Kindle** to the right-hand column, and click **Apply**.

---

## Additional Inspection & Maintenance Tools

From the **True Covers for Kindle** toolbar dropdown menu, you can also run:

- **Inspect Selected Book AZW3 Cover & ASIN Headers...:** Displays the exact binary `EXTH` records inside a book's `.azw3` file (`EXTH 201` cover offset, `EXTH 501` `EBOK`/`PDOC` flag, `EXTH 113` primary ASIN/key, `EXTH 504` secondary key, and live Amazon cover server verification status).
- **Audit Library Only (Highlight Books Needing Cover Fix):** Performs a fast, read-only scan of your entire library and highlights any books missing a fixed Kindle cover or Personal Document header using the search filter `marked:needs_cover_fix`.
- **Clear Cached ASINs:** Clears the local ASIN lookup cache (`true_covers_kindle_asin_cache.json`) so subsequent runs query online sources fresh.
- **View Session Log...:** Shows a detailed log of all **Auto-Import**, **Fix Kindle Cover**, and **Mark as Personal Document** operations performed during the current calibre session.

---

## Requirements, Installation & Configuration

### Requirements
- **calibre 6.0.0 or newer** (Windows, macOS, or Linux).
- **calibre Content Server** enabled (`Connect/share` → `Start Content server`) for downloading prepared `AZW3` books to your Kindle via its browser.
- An active internet connection when looking up `B0...` ASINs or verifying covers on Amazon's cover server (**Mark as Personal Document** works completely offline).

### Installation

#### From a Release `.zip` File
1. Download `True_Covers_for_Kindle.zip` (do not unzip it).
2. Open calibre and go to **Preferences** → **Plugins** (under *Advanced*).
3. Click **Load plugin from file** at the bottom right and select `True_Covers_for_Kindle.zip`.
4. Confirm the installation prompt and restart calibre.

### Building from Source

To build `True_Covers_for_Kindle.zip` from a clone of this repository (including the runtime modules, `images/`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `licenses/` if present), run:

```bash
python build_plugin.py
```

You can then install the generated `True_Covers_for_Kindle.zip` in calibre via **Preferences** → **Plugins** → **Load plugin from file**, or build and install directly from the repository folder using calibre's command-line tool:

```bash
calibre-customize -b .
```

To run the unit test suite:

```bash
python -m unittest discover -s tests -v
```

### Configuration
Open **Preferences** → **Plugins** → search for **True Covers for Kindle** → click **Customize plugin**, or click the **True Covers for Kindle** toolbar menu and choose **Configure True Covers for Kindle...**.

---

## Limitations & Known Issues

- **Target format is `AZW3` (`KF8`):** This plugin patches `AZW3` files for downloading from the calibre Content Server. Make sure you choose the **AZW3** download link when browsing the Content Server on your Kindle.
- **Books, comics, or magazines without a Kindle edition on Amazon:** Indie releases, self-published titles, PDFs, comics (`CBZ`/`CBR`), and magazines that have no `B0...` Kindle edition on Amazon cannot use `EBOK` cover lookup. Use **Mark as Personal Document** (`PDOC`) for these items so the Kindle displays the cover embedded in the `AZW3` file under **Personal Documents**.
- **Kindle thumbnail cache when re-downloading:** If you previously downloaded an unfixed copy of a book to your Kindle, the Kindle caches the blank thumbnail locally. Delete the old copy from your Kindle before downloading the fixed `AZW3` file from the Content Server so the Kindle refreshes the cover thumbnail.
- **Online lookup rate limits or regional catalog differences:** Online lookups query public Goodreads, Amazon Digital, DuckDuckGo, and OpenLibrary endpoints. If a lookup is rate-limited or matches a regional edition without a cover on Amazon's cover server, you can paste the exact `B0...` ASIN or Amazon URL using **Set Kindle ASIN (or Paste Amazon URL) for Selected Book...** or use **Mark as Personal Document**.

---

## Acknowledgments & Related Projects

Based on the code provenance audit documented in [`PROVENANCE.md`](PROVENANCE.md), **True Covers for Kindle** contains no copied or bundled code from other projects. We gratefully acknowledge the following open-source projects, runtime frameworks, and prior art in the calibre and Kindle community:

- **[calibre](https://calibre-ebook.com/) ([`kovidgoyal/calibre`](https://github.com/kovidgoyal/calibre))** — Copyright (C) Kovid Goyal, licensed under [`GPL-3.0`](https://www.gnu.org/licenses/gpl-3.0.html). Provides the host application, plugin architecture (`InterfaceActionBase` / `InterfaceAction`), database API (`db.new_api`), background job runner (`ThreadedJob`), and `ebook-convert` tool used at runtime.
- **[Qt](https://www.qt.io/) / [PyQt](https://www.riverbankcomputing.com/software/pyqt/)** — Copyright (C) The Qt Company Ltd. / Riverbank Computing Limited, licensed under `GPL-3.0` / `LGPL-3.0`. Bundled with calibre and used at runtime for menus, dialogs, timers, and the configuration UI.
- **[Kindle Cover Fixer (`scotjam/calibre_kindle_cover_fixer`)](https://github.com/scotjam/calibre_kindle_cover_fixer)** — Copyright (C) scotjam, licensed under `GPL-3.0`. An independent calibre plugin addressing a related problem (fixing sideloaded Kindle covers via metadata embedding, online ASIN lookup, and `KFX` `PDOC` conversion). No code from `scotjam/calibre_kindle_cover_fixer` was used in this plugin; it is credited here as a related open-source project.
- **[Kindle Cover Fixer (`weto91/kindle-cover-fixer`)](https://github.com/weto91/kindle-cover-fixer)** — Copyright (C) weto91, licensed under `GPL-3.0`. A standalone C#/.NET Windows utility that copies cover thumbnails over USB to a Kindle's `system\thumbnails\` directory. No code was used; mentioned as a related project.
- **[DualMetaFix (`DualMetaFix.py`)](https://github.com/bookfere/Fix-Kindle-Ebook-Cover/blob/master/DualMetaFix.py)** (also included in **[Kindle Comic Converter (`ciromattia/kcc`)](https://github.com/ciromattia/kcc)**) — Copyright (C) 2013 Kevin Hendricks, Copyright (C) 2014 Pawel Jastrzebski, licensed under `GPL-3.0-or-later`. Prior art that documented the binary MOBI/KF8 technique of stamping `EXTH 113` (primary ASIN), `EXTH 504` (secondary ASIN), and `EXTH 501` (`EBOK`) across both headers while adjusting the full-name offset at byte `84`. `ExthBinaryCodec` in [`core.py`](core.py) is an independent implementation of this technique.
- **[Quality Check calibre plugin (`kiwidude68/calibre_plugins`)](https://github.com/kiwidude68/calibre_plugins/tree/main/quality_check)** — Copyright (C) 2011 Grant Drake, Copyright (C) 2012 Kovid Goyal, licensed under `GPL-3.0`. Prior art for inspecting MOBI `EXTH 113` / `501` / `504` headers in calibre and highlighting matching books with calibre's `set_marked_ids` (`marked:...`) search marks.

---

## License

**True Covers for Kindle** is free and open-source software licensed under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`).

- Full license text: [`LICENSE`](LICENSE)
- Third-party notices and provenance details: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`PROVENANCE.md`](PROVENANCE.md)
