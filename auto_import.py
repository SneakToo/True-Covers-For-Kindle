# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import time
import threading
from typing import Any, Callable, Iterable, Optional

try:
    from calibre_plugins.true_covers_kindle.core import atomic_json_load, atomic_json_save
except ImportError:
    from core import atomic_json_load, atomic_json_save


class AutoImportEngine:
    """Non-destructive watch-folder ingestion engine using crash-safe atomic JSON state."""

    IGNORED_PATTERNS    = ('.part', '.!qb', '.crdownload', '.tmp', '.aria2', '.ds_store')
    SIDECAR_COVER_NAMES = ('cover.jpg', 'cover.jpeg', 'cover.png', 'folder.jpg')

    def __init__(self, state_path: str):
        self.state_path = state_path
        self._lock = threading.Lock()
        self._seen: dict[str, str] = atomic_json_load(self.state_path, dict)
        self._book_id_to_paths: dict[int, list[str]] = {}

    def save(self) -> None:
        """Persist seen file signatures atomically to disk."""
        with self._lock:
            atomic_json_save(self.state_path, self._seen)

    def forget_book_ids(self, book_ids: Iterable[int]) -> None:
        """Remove watch-folder seen entries for book_ids that failed cover fixing so a later Auto-Import can retry."""
        removed_any = False
        with self._lock:
            for bid in book_ids:
                for path in self._book_id_to_paths.pop(int(bid), ()):
                    if self._seen.pop(path, None) is not None:
                        removed_any = True
            if removed_any:
                atomic_json_save(self.state_path, self._seen)

    def reset_state(self) -> None:
        """Clear all recorded watch-folder file signatures."""
        with self._lock:
            self._seen.clear()
            self._book_id_to_paths.clear()
            atomic_json_save(self.state_path, self._seen)

    def discover_settled_files(
        self,
        folder: str,
        allowed_exts: Iterable[str],
        min_settle_sec: int = 10
    ) -> list[tuple[str, str, str]]:
        """Scan folder for ebook files matching allowed_exts that have settled and have not been imported yet."""
        discovered: list[tuple[str, str, str]] = []
        if not folder or not os.path.isdir(folder):
            return discovered

        allowed_set = {e.lower().lstrip('.') for e in allowed_exts}
        now = time.time()
        with self._lock:
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    lower_name = fname.lower()
                    if any(lower_name.endswith(pat) for pat in self.IGNORED_PATTERNS):
                        continue
                    ext = os.path.splitext(fname)[1].lower().lstrip('.')
                    if ext not in allowed_set:
                        continue
                    full_path = os.path.normpath(os.path.join(root, fname))
                    try:
                        st = os.stat(full_path)
                        if st.st_size <= 0:
                            continue
                        if min_settle_sec > 0 and (now - st.st_mtime) < min_settle_sec:
                            continue
                        sig = f'{st.st_size}:{int(st.st_mtime)}'
                    except OSError:
                        continue

                    if self._seen.get(full_path) != sig:
                        discovered.append((full_path, ext, sig))
        return discovered

    @classmethod
    def _read_metadata_with_sidecars(
        cls,
        full_path: str,
        ext: str,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> tuple[Any, Optional[bytes]]:
        """Read ebook metadata and optional sidecar .opf or cover image without modifying any source files."""
        from calibre.ebooks.metadata.meta import get_metadata

        with open(full_path, 'rb') as stream:
            mi = get_metadata(stream, stream_type=ext, use_libprs_metadata=True)

        parent_dir = os.path.dirname(full_path)
        try:
            dir_files = os.listdir(parent_dir)
        except OSError:
            dir_files = []

        stem = os.path.splitext(os.path.basename(full_path))[0]
        if not getattr(mi, 'title', None) or mi.title == stem:
            opf_file = next((f for f in dir_files if f.lower().endswith('.opf')), None)
            if opf_file:
                try:
                    from calibre.ebooks.metadata.opf2 import OPF
                    opf_path = os.path.join(parent_dir, opf_file)
                    with open(opf_path, 'rb') as opf_stream:
                        opf_mi = OPF(opf_stream, basedir=parent_dir).to_book_metadata()
                    if getattr(opf_mi, 'title', None):
                        mi.title = opf_mi.title
                    if getattr(opf_mi, 'authors', None):
                        mi.authors = opf_mi.authors
                except Exception as opf_err:
                    if log_fn:
                        log_fn(f'[AUTO-IMPORT-WARN] Could not parse sidecar OPF {opf_file}: {opf_err}')

        if not getattr(mi, 'title', None):
            mi.title = stem

        sidecar_cover_bytes = None
        if not getattr(mi, 'cover', None) and not getattr(mi, 'cover_data', (None, None))[1]:
            for cname in cls.SIDECAR_COVER_NAMES:
                match = next((f for f in dir_files if f.lower() == cname), None)
                if match:
                    try:
                        with open(os.path.join(parent_dir, match), 'rb') as cf:
                            sidecar_cover_bytes = cf.read()
                        break
                    except OSError as cov_read_err:
                        if log_fn:
                            log_fn(f'[AUTO-IMPORT-WARN] Could not read sidecar cover {match}: {cov_read_err}')

        return mi, sidecar_cover_bytes

    @staticmethod
    def _find_existing_book_ids(db: Any, mi: Any) -> list[int]:
        """Return existing library book IDs matching mi (via find_identical_books or exact case-insensitive title)."""
        try:
            if hasattr(db, 'find_identical_books'):
                ids = db.find_identical_books(mi)
                if ids:
                    return sorted(int(bid) for bid in ids)
        except Exception:
            pass
        title_norm = str(getattr(mi, 'title', '') or '').strip().lower()
        if not title_norm:
            return []
        matched = []
        try:
            for bid in db.all_book_ids():
                t = str(db.field_for('title', bid) or '').strip().lower()
                if t == title_norm:
                    matched.append(int(bid))
        except Exception:
            pass
        return sorted(matched)

    def ingest_files(
        self,
        db: Any,
        discovered_items: Iterable[tuple[str, str, str]],
        log_fn: Optional[Callable[[str], None]] = None,
        abort: Any = None,
        needs_fix_fn: Optional[Callable[[int], bool]] = None
    ) -> tuple[list[int], list[int], int]:
        """Import discovered ebook files into the calibre database without modifying the source files."""
        added_ids: list[int] = []
        retry_fix_ids: list[int] = []
        skipped_dups = 0

        for full_path, ext, sig in discovered_items:
            if abort is not None and abort.is_set():
                break
            try:
                mi, sidecar_cover = self._read_metadata_with_sidecars(full_path, ext, log_fn=log_fn)
                if db.has_book(mi):
                    existing_ids = self._find_existing_book_ids(db, mi)
                    unfixed_existing = [
                        bid for bid in existing_ids
                        if needs_fix_fn is not None and needs_fix_fn(bid)
                    ]
                    if unfixed_existing:
                        for bid in unfixed_existing:
                            if bid not in retry_fix_ids and bid not in added_ids:
                                retry_fix_ids.append(bid)
                            with self._lock:
                                paths = self._book_id_to_paths.setdefault(bid, [])
                                if full_path not in paths:
                                    paths.append(full_path)
                        if log_fn:
                            log_fn(
                                f'[AUTO-IMPORT-RETRY] Existing library book #{unfixed_existing[0]} '
                                f'"{mi.title}" needs Fix Kindle Cover'
                            )
                    else:
                        skipped_dups += 1
                        if log_fn:
                            log_fn(
                                f'[AUTO-IMPORT-DUP] Skipped existing library title "{mi.title}" '
                                f'({os.path.basename(full_path)})'
                            )
                else:
                    ids, _dups = db.add_books(
                        [(mi, {ext.upper(): full_path})],
                        add_duplicates=False,
                        run_hooks=False
                    )
                    if ids:
                        bid = ids[0]
                        if sidecar_cover:
                            try:
                                db.set_cover({bid: sidecar_cover})
                            except Exception as cov_err:
                                if log_fn:
                                    log_fn(
                                        f'[AUTO-IMPORT-WARN] Could not set sidecar cover on #{bid}: {cov_err}'
                                    )
                        added_ids.append(bid)
                        with self._lock:
                            paths = self._book_id_to_paths.setdefault(bid, [])
                            if full_path not in paths:
                                paths.append(full_path)
                        if log_fn:
                            log_fn(
                                f'[AUTO-IMPORT-ADD] Imported #{bid} "{mi.title}" '
                                f'from {os.path.basename(full_path)}'
                            )
                with self._lock:
                    self._seen[full_path] = sig
            except Exception as err:
                if log_fn:
                    log_fn(f'[AUTO-IMPORT-ERR] {os.path.basename(full_path)}: {err}')

        self.save()
        return added_ids, retry_fix_ids, skipped_dups
