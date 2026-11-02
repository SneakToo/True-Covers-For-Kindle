# Third-Party Notices (`THIRD_PARTY_NOTICES.md`)

**True Covers for Kindle** is licensed under the GNU General Public License v3.0 or later (`GPL-3.0-or-later`). See [`LICENSE`](LICENSE) for the full license text.

---

## 1. Bundled or Vendored Third-Party Code

**None.**

Based on the code provenance audit documented in [`PROVENANCE.md`](PROVENANCE.md), this repository does not bundle, vendor, or copy code or assets from any third-party library or project. All source files (`__init__.py`, `action.py`, `auto_import.py`, `config.py`, `core.py`, `pipelines.py`) and custom vector/raster icons (`images/icon.*`, `images/auto_import.*`, `images/personal_document.*`) are original to this project.

---

## 2. Host Application & Runtime Dependencies (Not Bundled)

When loaded as a calibre plugin, **True Covers for Kindle** runs inside the host calibre environment and imports APIs provided by the following components (none of which are distributed in the plugin archive):

| Component | Link | Copyright Holder | License | Files Referencing Runtime API |
| :--- | :--- | :--- | :--- | :--- |
| **calibre** | <https://calibre-ebook.com/><br><https://github.com/kovidgoyal/calibre> | Copyright (C) Kovid Goyal | `GPL-3.0` | `__init__.py`, `action.py`, `auto_import.py`, `config.py`, `pipelines.py` |
| **Qt / PyQt** (bundled with calibre) | <https://www.qt.io/><br><https://www.riverbankcomputing.com/software/pyqt/> | Copyright (C) The Qt Company Ltd. / Riverbank Computing Limited | `GPL-3.0` / `LGPL-3.0` | `action.py`, `config.py` |
| **Python Standard Library** | <https://www.python.org/> | Copyright (C) Python Software Foundation | `PSF-2.0` | `__init__.py`, `action.py`, `auto_import.py`, `config.py`, `core.py`, `pipelines.py` |

---

## 3. Related Open-Source Projects & Prior Art (No Code Copied)

The following open-source projects were reviewed during the provenance check (`PROVENANCE.md`) and are acknowledged as related tools or prior art in the Kindle cover / MOBI metadata ecosystem:

| Project | Link | Copyright Holder | License | Relationship to True Covers for Kindle |
| :--- | :--- | :--- | :--- | :--- |
| **Kindle Cover Fixer** (`scotjam/calibre_kindle_cover_fixer`) | <https://github.com/scotjam/calibre_kindle_cover_fixer> | Copyright (C) scotjam / Anonymous | `GPL-3.0` | Independent calibre plugin for fixing Kindle covers via `db.embed_metadata`, online ASIN lookup, and KFX `PDOC` conversion. No code was copied (`PROVENANCE.md` §1.1). |
| **Kindle Cover Fixer** (`weto91/kindle-cover-fixer`) | <https://github.com/weto91/kindle-cover-fixer> | Copyright (C) weto91 | `GPL-3.0` | Standalone C#/.NET Windows utility that transfers cover thumbnails to `system\thumbnails\` over USB. No code was copied (`PROVENANCE.md` §1.2). |
| **DualMetaFix** | <https://github.com/bookfere/Fix-Kindle-Ebook-Cover/blob/master/DualMetaFix.py><br><https://github.com/ciromattia/kcc> | Copyright (C) 2013 Kevin Hendricks<br>Copyright (C) 2014 Pawel Jastrzebski | `GPL-3.0-or-later` | Prior art establishing the technique of stamping `EXTH 113` (primary ASIN), `EXTH 504` (secondary ASIN), and `EXTH 501` (`EBOK`) across both MOBI6 and KF8 headers and adjusting the MOBI full-name offset at byte `84`. `ExthBinaryCodec` in `core.py` is an independent implementation (`PROVENANCE.md` §2). |
| **Quality Check (calibre plugin)** | <https://github.com/kiwidude68/calibre_plugins/tree/main/quality_check> | Copyright (C) 2011 Grant Drake<br>Copyright (C) 2012 Kovid Goyal | `GPL-3.0` | Prior art for inspecting MOBI `EXTH 113` / `501` / `504` headers in calibre and highlighting matching books using calibre's `set_marked_ids` (`marked:...`) search marks (`PROVENANCE.md` §2). |
