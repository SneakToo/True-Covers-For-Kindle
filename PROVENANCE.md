# Code Provenance & License Audit (`PROVENANCE.md`)

This document reports the results of the code provenance audit for **True Covers for Kindle** (formerly *Stoker*). Because the plugin was originally written with AI assistance, every module, class, function, regular expression, binary offset constant, and network endpoint in this repository (including the pre-rename state) was compared against known open-source Kindle cover tools, calibre plugins, and calibre's own source tree.

---

## Executive Summary

- **No copied or vendored third-party code was found** in any file in this repository (`__init__.py`, `action.py`, `auto_import.py`, `config.py`, `core.py`, `pipelines.py`, or `images/*`).
- **Comparison with Kindle Cover Fixer**:
  - [`scotjam/calibre_kindle_cover_fixer`](https://github.com/scotjam/calibre_kindle_cover_fixer) (**GPL-3.0**) is an independent calibre plugin addressing a related problem (fixing sideloaded Kindle covers on modern firmware). While both plugins use calibre's `InterfaceAction` plugin architecture, listen for newly added books, embed metadata/covers via `db.embed_metadata`, cache ASIN lookups in JSON, and offer a Personal Document (`PDOC`) fallback, their implementations and technical mechanisms are completely different (**similar approach only**).
  - [`weto91/kindle-cover-fixer`](https://github.com/weto91/kindle-cover-fixer) (**GPL-3.0**) is a standalone C#/.NET Windows desktop utility that copies thumbnail files over USB to `system\thumbnails\`. It shares no code, language, or architecture with this plugin (**no match found**).
- **Bundled / Vendored Dependencies**: **None.** The plugin uses only the Python standard library and calibre's built-in runtime APIs (`calibre.*` and Qt/`PyQt`).
- **GPL v3 Compatibility**: No GPL v3–incompatible code was found. All external runtime dependencies (calibre, Qt/PyQt) and the closest related open-source projects (`scotjam/calibre_kindle_cover_fixer`, `DualMetaFix.py`, `Quality Check`) are licensed under **GPL v3** (or GPL v3+ / LGPL v3).

---

## 1. Comparison with "Kindle Cover Fixer" Projects

### 1.1 `scotjam/calibre_kindle_cover_fixer` (Calibre Plugin)
- **Repository**: <https://github.com/scotjam/calibre_kindle_cover_fixer> (v0.1.13 also inspected from local calibre plugin archive)
- **License**: GNU General Public License v3.0 (`GPL-3.0`)
- **Files in `scotjam/calibre_kindle_cover_fixer`**: `__init__.py`, `ui.py`, `config.py`, `asin.py`, `asin_browser.py`, `asin_cache.py`, `column.py`, `plog.py`, `zip_release.sh`
- **Confidence**: **Similar approach only** (no copied or adapted code blocks)

| Area | `scotjam/calibre_kindle_cover_fixer` | `True Covers for Kindle` (this repo) | Assessment |
| :--- | :--- | :--- | :--- |
| **Target format & cover mechanism** | Targets **KFX** over USB or email (`sync_to_device`, `send_by_mail`); configures jhowell's `KFX Output` plugin (`cde_type_pdoc=True`) and requires Kindle Previewer 3. Does **not** parse or patch binary MOBI/AZW3 headers. | Targets **AZW3 (KF8)** in the calibre library for Content Server downloads; converts via `ebook-convert --share-not-sync` and directly patches binary PalmDOC/MOBI/KF8 `EXTH` records (`113`, `201`, `501`, `504`) via `ExthBinaryCodec`. | **No match found** (different mechanisms and target formats). |
| **ASIN lookup (`asin.py`, `asin_browser.py` vs `core.py:AsinResolver`)** | Calls calibre's `identify()` metadata plugins or launches a minimised `QtWebEngineView` (`AsinBrowser`) running injected JavaScript (`_GOOGLE_JS`, `_AMAZON_JS`) on Google and Amazon search pages. | Uses `urllib.request` in `AsinResolver` (no `QtWebEngineView` or `identify()`) across 4 stages: Goodreads JSON autocomplete + work editions, Amazon Digital search (`&i=digital-text`), DuckDuckGo HTML/Lite, and OpenLibrary APIs, plus live Amazon CDN cover verification and `DERIVATIVE_SPAM_RE` filtering. | **Similar approach only** (both extract `data-asin` and `/dp/<ASIN>` from Amazon search HTML, but one uses JS DOM queries in `QtWebEngineView` while the other uses Python regexes over `urllib` responses). |
| **ASIN cache (`asin_cache.py` vs `core.py:AsinResolver`)** | Uses calibre's `JSONConfig('plugins/kindle_cover_fixer_cache')` storing a flat `map` dict keyed by `'isbn:' + isbn` or `'ta:%s\|%s' % (title, authors)`. | Uses custom crash-safe `atomic_json_load` / `atomic_json_save` storing `{'queries': ..., 'verified_asins': ...}` in `true_covers_kindle_asin_cache.json` with timestamps, verification flags, and JPEG byte sizes. | **Similar approach only** (caching lookups by ISBN/title/author is standard). |
| **Database event listener (`ui.py` vs `action.py`)** | Listens to `EventType.book_created`, emits `_Bridge.book_added`, debounces with a 4000ms single-shot `QTimer`, and spawns a `threading.Thread` (`_worker`). | Listens to `format_added` (`'format_added' in evt_name`), emits `_book_added_sig(int, str)`, delays with `QTimer.singleShot(1800/6000)`, and enqueues a calibre `ThreadedJob`. | **Similar approach only** (standard Qt thread-marshalling required because calibre DB listeners fire off the GUI thread). |
| **Large selection prompt (`ui.py:process_selected` vs `action.py:_confirm_large_selection`)** | Prompts with `question_dialog` when `len(ids) > 25` to ask whether to include online ASIN lookup or only embed metadata. | Prompts with `question_dialog` when `len(book_ids) > LARGE_SELECTION_THRESHOLD` (`25`) before starting a batch job. | **Similar approach only**. |
| **Session log (`plog.py` vs `action.py:_audit_history`)** | Module-level `_BUF = deque(maxlen=4000)` with a custom `QPlainTextEdit` dialog (`Copy`, `Clear`, `Close` buttons). | Instance attribute `self._audit_history = deque(maxlen=500)` displayed via calibre's standard `info_dialog`. | **Similar approach only**. |
| **Book status tracking (`column.py` vs `action.py` / `pipelines.py`)** | Creates a persistent custom column `#kindle_cover` (`ASIN+Embed`, `ASIN`, `Embed`). | Uses fast `<2ms` on-disk `EXTH` header inspection (`ExthBinaryCodec.inspect_file`), `identifiers['true_covers_kindle'] = 'personal_document'`, and temporary search marks (`marked:no_asin`, `marked:no_azw3`, `marked:needs_cover_fix`). | **No match found**. |

### 1.2 `weto91/kindle-cover-fixer` (Windows Desktop App)
- **Repository**: <https://github.com/weto91/kindle-cover-fixer>
- **License**: GNU General Public License v3.0 (`GPL-3.0`)
- **Confidence**: **No match found**
- **Comparison**: Written in C# (`.NET` / WPF); scans a USB-mounted Kindle's `system\thumbnails` directory and uploads cover images named by Calibre UUID. No code, structure, or algorithms are shared.

---

## 2. File & Function Audit Against Other Projects

Every module and major component in this repository was checked against other open-source Kindle tools, calibre plugins, and calibre's core library.

| File & Component | Likely Source / Related Projects Checked | Source License | Confidence | Notes & Comparison |
| :--- | :--- | :--- | :--- | :--- |
| `__init__.py` (`TrueCoversKindlePlugin`) | Calibre `InterfaceActionBase` plugin boilerplate ([`kovidgoyal/calibre`](https://github.com/kovidgoyal/calibre), [`kiwidude68/calibre_plugins`](https://github.com/kiwidude68/calibre_plugins), [`scotjam/calibre_kindle_cover_fixer`](https://github.com/scotjam/calibre_kindle_cover_fixer)) | `GPL-3.0` | **Similar approach only** | Standard 30-line class definition (`name`, `description`, `supported_platforms`, `author`, `version`, `minimum_calibre_version`, `actual_plugin`, `is_customizable`, `config_widget`, `save_settings`) mandated by calibre's plugin loader. |
| `core.py` (`ExthBinaryCodec`: `parse_record_exth`, `locate_exth_region`, `inspect_file`, `_patch_single_mobi_record`, `patch_file`) | 1. [`DualMetaFix.py`](https://github.com/bookfere/Fix-Kindle-Ebook-Cover/blob/master/DualMetaFix.py) (Kevin Hendricks 2013 / Pawel Jastrzebski 2014; also in [`ciromattia/kcc`](https://github.com/ciromattia/kcc))<br>2. [`kiwidude68/calibre_plugins` (`quality_check/mobi6.py`)](https://github.com/kiwidude68/calibre_plugins/blob/main/quality_check/mobi6.py) (Kovid Goyal 2012 / Grant Drake 2011)<br>3. [`jkirchhefer/Fix-Sideloaded-Kindle-Covers` (`flip_cdetype.py`)](https://github.com/jkirchhefer/Fix-Sideloaded-Kindle-Covers)<br>4. [`xyz-rainbow/kindle-covers` (`fix_kindle_covers.py`)](https://github.com/xyz-rainbow/kindle-covers)<br>5. [`outrera/kindle-cover-fix` (`fix_kindle_covers.py`)](https://github.com/outrera/kindle-cover-fix)<br>6. Calibre's `calibre.ebooks.metadata.mobi` | 1. `GPL-3.0-or-later`<br>2. `GPL-3.0`<br>3. Unlicensed (public repo)<br>4. `MIT`<br>5. Unlicensed<br>6. `GPL-3.0` | **Similar approach only** | All tools operating on MOBI/AZW3 headers use the same public PalmDOC/MOBI/EXTH binary layout (`BOOKMOBI` at byte `60`, record count at `76`, record table at `78`, `MOBI` at `+16`, header length at `+20`, full-name offset at `+84`, EXTH flags `0x40` at `+128`, `EXTH` records packed as `>II` type/length pairs) and the well-known Kindle technique of stamping `EXTH 113` (primary ASIN), `EXTH 504` (secondary ASIN), and `EXTH 501` (`EBOK`/`PDOC`) across both the MOBI6 and KF8 headers while shifting the full-name offset at byte `84`.<br><br>`ExthBinaryCodec` is a clean-room implementation: unlike `DualMetaFix.py` (which uses `mmap` and trims trailing null bytes to keep `rec0` length fixed) or `quality_check/mobi6.py` (which subclasses `MetadataUpdater`), `ExthBinaryCodec` scans all records for `b'MOBI'` at offset `16`, rebuilds modified records in memory with `(4 - (unpadded_len % 4)) % 4` padding, and recalculates the PalmDOC record offset table. |
| `core.py` (`AsinResolver.verify_asin_live_cover` — Amazon cover CDN check) | Public Amazon Product Image CDN URL pattern (`https://images-na.ssl-images-amazon.com/images/P/{ASIN}.01.LZZZZZZZ.jpg` and `https://m.media-amazon.com/images/P/{ASIN}.01.LZZZZZZZ.jpg`); minimum JPEG size heuristic (`>= 1200` bytes vs 1x1 GIF placeholder) | N/A (public URL convention) | **Similar approach only** | Checking `.01.LZZZZZZZ.jpg` on Amazon's image servers and rejecting 43-byte 1x1 transparent GIFs (`b'\xff\xd8'` magic + byte size threshold) is a long-standing public convention used across library/catalog scripts. |
| `core.py` (`AsinResolver._candidates_goodreads_kindle`, `_candidates_amazon_digital`, `_candidates_duckduckgo`, `_candidates_openlibrary`) | Public web endpoints (Goodreads `/book/auto_complete?format=json&q=`, Goodreads `/work/editions/`, Amazon `/s?k=...&i=digital-text`, DuckDuckGo HTML/Lite, OpenLibrary `/api/books` and `/search.json`) | N/A (public web endpoints) | **No match found** | Multi-stage fallback pipeline, HTML/JSON parsing, and reverse title/author verification against Goodreads autocomplete are custom-written for this plugin. |
| `core.py` (`DERIVATIVE_SPAM_RE`, `AUTHOR_IMPOSTOR_RE`, `is_derivative_or_mismatched_title`, `is_matching_author`, `isbn13_to_isbn10`) | Original project code (and standard Modulo-11 ISBN-10 check-digit math) | N/A | **No match found** | Custom heuristic filters designed to reject summary mills, study guides, companions, and box sets when resolving Kindle ASINs. |
| `core.py` (`atomic_json_load`, `atomic_json_save`, `migrate_legacy_json_file`) | Standard Python atomic write idiom (`json.dump` to `.tmp.<pid>` + `os.replace`) | N/A | **No match found** | Standard crash-safe file persistence pattern using Python's `os` and `json` modules. |
| `auto_import.py` (`AutoImportEngine`) | Calibre metadata & database APIs (`calibre.ebooks.metadata.meta.get_metadata`, `calibre.ebooks.metadata.opf2.OPF`, `db.new_api.has_book`, `db.new_api.add_books`, `db.new_api.set_cover`) | `GPL-3.0` (calibre API) | **No match found** | Non-destructive watch-folder scanner that tracks `{st_size}:{int(st_mtime)}` signatures and reads sidecar `.opf` and cover files without modifying the watched folder. |
| `pipelines.py` (`FixKindleCoverPipeline`, `PersonalDocumentPipeline`, `convert_book_to_azw3`, `bake_and_stamp_azw3`, `is_book_stamped_and_baked`) | Calibre CLI (`ebook-convert --share-not-sync`) and `db.new_api` (`embed_metadata`, `add_format`, `remove_formats`, `field_for`, `set_metadata`) | `GPL-3.0` (calibre API) | **No match found** | Original pipeline logic combining `ebook-convert`, `db.embed_metadata`, `ORIGINAL_*` backup cleanup, and `ExthBinaryCodec.patch_file`. |
| `config.py` (`PREFS`, `migrate_legacy_preferences`, `CountdownConfirmDialog`, `ConfigWidget`) | Calibre `JSONConfig` and Qt (`QWidget`, `QDialog`, `QTimer`) | `GPL-3.0` (calibre API) | **No match found** | Original configuration UI, legacy `Stoker.json` preference migration, and idle-countdown confirmation dialog. |
| `action.py` (`TrueCoversKindleAction`) | Calibre `InterfaceAction`, `ThreadedJob`, `Dispatcher`, `gprefs` action-layout registration, and `Quality Check` (`kiwidude68/calibre_plugins`) search-mark pattern (`set_marked_ids` / `marked:...`) | `GPL-3.0` | **Similar approach only** | Uses calibre's documented `InterfaceAction`, `ThreadedJob`, and `set_marked_ids` APIs. Context-menu hook (`_hook_context_menu_rebuild` / `_sync_library_context_menus`) and batch summary/handoff workflows are original to this plugin. |

---

## 3. Bundled or Vendored Third-Party Dependencies

**None.**

- The repository contains **no** bundled or vendored third-party Python packages, scripts, or icon assets.
- At runtime, the plugin imports only:
  1. **Python Standard Library** (`collections`, `difflib`, `gzip`, `json`, `os`, `re`, `shutil`, `struct`, `subprocess`, `sys`, `tempfile`, `threading`, `time`, `unicodedata`, `urllib.parse`, `urllib.request`) — Python Software Foundation License (`PSF-2.0`, GPL-compatible, not distributed in the plugin zip).
  2. **calibre** (`calibre.customize`, `calibre.ebooks.metadata`, `calibre.gui2`, `calibre.utils.config`) — `GPL-3.0` (provided by the host calibre application, not distributed in the plugin zip).
  3. **Qt / PyQt** (`qt.core` / `PyQt5.Qt`) — `GPL-3.0` / `LGPL-3.0` (provided by the host calibre application, not distributed in the plugin zip).

---

## 4. License Compatibility & Items Requiring User Decision

- **Incompatible Copied Code**: **None found.** Because no third-party code was copied into this repository—and because calibre and the closest related open-source projects in this space (`scotjam/calibre_kindle_cover_fixer`, `DualMetaFix.py`, `Quality Check`) are all licensed under **GPL v3**—there are no license conflicts with licensing **True Covers for Kindle** under `GPL-3.0-or-later`.
