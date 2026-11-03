# Changelog

All notable changes to **True Covers for Kindle** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-04-11

### Fixed
- **Immediate Book Details `AZW3` Format Refresh**: Fixed an issue where background `EPUB -> AZW3` conversion during **Auto-Import** or **Fix Kindle Cover** updated the library database on disk, but Calibre's right-hand Book Details panel continued showing only the original format (e.g., `EPUB`) until the user clicked away and back.
- **Prevent Duplicate Auto-Import Background Jobs**: Suppressed the `format_added` database listener while an **Auto-Import** batch is actively importing books so newly added books are not queued twice for **Fix Kindle Cover**.
- **Deterministic Multi-Format Watch Folder Priority**: Sorted discovered files in **Auto-Import** watched folders by format priority (`EPUB` -> `AZW3` -> `MOBI` -> `FB2` -> `DOCX` -> `HTMLZ` -> `RTF` -> `TXT` -> `CBZ` -> `CBR` -> `PDF`) so multi-format book folders always import clean reflowable formats ahead of legacy `MOBI` or `PDF` files.
- **Suppress Windows Console Window During `AZW3` Conversion**: Passed `CREATE_NO_WINDOW` and hidden `STARTUPINFO` flags when invoking `ebook-convert.exe` so background conversions run completely silently without flashing a black command prompt window.

## [1.0.0] - 2026-04-11

### Added
- **Fix Kindle Cover (`EBOK`)**: Multi-source Kindle edition ASIN lookup (Goodreads, Amazon Digital, DuckDuckGo, OpenLibrary) with subtitle/edition stripping and author surname matching, live Amazon CDN cover verification, `AZW3` conversion, `EXTH 201` cover embedding, and binary MOBI/KF8 `EXTH 113`, `504`, and `501=EBOK` header stamping.
- **Auto-Import**: Non-destructive background watched-folder scanner that leaves source files untouched for seeding, imports settled ebooks with sidecar `.opf` and cover images into Calibre, tracks book-to-source-path mappings so books removed from Calibre can be re-imported, and hands imported books off to **Fix Kindle Cover**.
- **Mark as Personal Document (`PDOC`)**: Fallback workflow and one-click recovery prompt for books, comics, magazines, or documents without an Amazon Kindle edition (`EXTH 501=PDOC` with embedded Calibre cover art).
- **Toolbar & Context Menu Integration**: Full Calibre toolbar button, dropdown menu, right-click book context menu, manual ASIN entry dialog, and Library Health Check summary.
