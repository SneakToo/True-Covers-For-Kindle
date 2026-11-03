# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import re
import time
import hashlib
import threading
from typing import Any, Callable, Iterable, Optional

try:
    from calibre_plugins.true_covers_kindle.core import atomic_json_load, atomic_json_save
except ImportError:
    from core import atomic_json_load, atomic_json_save

CONTENT_HASH_PREFIX = 'sha256:'
CONTENT_HASH_CHUNK_BYTES = 65536
TITLE_ALNUM_RE = re.compile(r'[^a-z0-9]+')
PLACEHOLDER_TITLE_RE = re.compile(
    r'^(?:untitled|unknown|(?:isbn[:\s-]*)?\d{9,13}[\dxX]?)(?:\.[a-z0-9]{2,5})?$|'
    r'^.+\.(?:pdf|epub|mobi|azw3|azw|fb2|cbz|cbr|docx|txt|rtf)$',
    re.IGNORECASE
)
STOPWORDS = frozenset({'the', 'and', 'for', 'from', 'with', 'into', 'novel', 'edition', 'book'})
MATCH_IDENTIFIER_KEYS = ('isbn', 'mobi-asin', 'asin', 'amazon', 'goodreads')


class AutoImportEngine:
    """Non-destructive watch-folder ingestion engine using crash-safe atomic JSON state."""

    IGNORED_PATTERNS    = ('.part', '.!qb', '.crdownload', '.tmp', '.aria2', '.ds_store')
    SIDECAR_COVER_NAMES = ('cover.jpg', 'cover.jpeg', 'cover.png', 'folder.jpg')
    FORMAT_PRIORITY     = {
        'epub': 0,
        'azw3': 1,
        'mobi': 2,
        'fb2': 3,
        'docx': 4,
        'htmlz': 5,
        'rtf': 6,
        'txt': 7,
        'cbz': 8,
        'cbr': 9,
        'pdf': 10,
    }

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

    @staticmethod
    def _compute_file_content_id(full_path: str, file_size: Optional[int] = None) -> Optional[str]:
        """Compute a fast, non-destructive content fingerprint (size + partial SHA-256) for rename detection."""
        try:
            size = int(file_size if file_size is not None else os.path.getsize(full_path))
            if size <= 0:
                return None
            hasher = hashlib.sha256()
            hasher.update(f'{size}:'.encode('ascii'))
            with open(full_path, 'rb') as f:
                hasher.update(f.read(CONTENT_HASH_CHUNK_BYTES))
                if size > CONTENT_HASH_CHUNK_BYTES * 2:
                    f.seek(size // 2)
                    hasher.update(f.read(CONTENT_HASH_CHUNK_BYTES))
                if size > CONTENT_HASH_CHUNK_BYTES:
                    f.seek(max(0, size - CONTENT_HASH_CHUNK_BYTES))
                    hasher.update(f.read(CONTENT_HASH_CHUNK_BYTES))
            return f'{CONTENT_HASH_PREFIX}{size}:{hasher.hexdigest()}'
        except OSError:
            return None

    @staticmethod
    def _parse_seen_value(val: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        """Split a stored seen value ('size:mtime' or 'size:mtime#book_id') into (sig, book_id)."""
        if not val:
            return None, None
        if '#' in val:
            sig_part, bid_part = val.rsplit('#', 1)
            try:
                return sig_part, int(bid_part)
            except ValueError:
                return sig_part, None
        return val, None

    @staticmethod
    def _format_seen_value(sig: str, book_id: Optional[int]) -> str:
        """Format a seen signature with an optional calibre library book_id."""
        if book_id is not None:
            return f'{sig}#{int(book_id)}'
        return sig

    def discover_settled_files(
        self,
        folder: str,
        allowed_exts: Iterable[str],
        min_settle_sec: int = 10,
        existing_book_ids: Optional[set[int]] = None
    ) -> list[tuple[str, str, str]]:
        """Scan folder for ebook files matching allowed_exts that have settled and have not been imported yet."""
        discovered: list[tuple[str, str, str]] = []
        if not folder or not os.path.isdir(folder):
            return discovered

        allowed_set = {e.lower().lstrip('.') for e in allowed_exts}
        now = time.time()
        state_updated = False
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

                    stored_val = self._seen.get(full_path)
                    stored_sig, stored_bid = self._parse_seen_value(stored_val)

                    if existing_book_ids is not None and stored_bid is not None and stored_bid not in existing_book_ids:
                        # The book previously imported from this file was removed from the calibre library.
                        self._seen.pop(full_path, None)
                        content_id = self._compute_file_content_id(full_path, st.st_size)
                        if content_id:
                            self._seen.pop(content_id, None)
                        stored_val, stored_sig, stored_bid = None, None, None
                        state_updated = True

                    if stored_sig == sig:
                        if existing_book_ids is not None and stored_bid is None:
                            # Legacy seen entry without a linked book_id: verify once against the library.
                            discovered.append((full_path, ext, sig))
                            continue
                        content_id = self._compute_file_content_id(full_path, st.st_size)
                        if content_id and self._seen.get(content_id) != stored_val:
                            self._seen[content_id] = stored_val or sig
                            state_updated = True
                        continue

                    content_id = self._compute_file_content_id(full_path, st.st_size)
                    cid_val = self._seen.get(content_id) if content_id else None
                    cid_sig, cid_bid = self._parse_seen_value(cid_val)
                    if existing_book_ids is not None and cid_bid is not None and cid_bid not in existing_book_ids:
                        if content_id:
                            self._seen.pop(content_id, None)
                        cid_val, cid_sig, cid_bid = None, None, None
                        state_updated = True

                    renamed_from = []
                    renamed_bid: Optional[int] = cid_bid
                    for old_path, old_val in list(self._seen.items()):
                        if old_path.startswith(CONTENT_HASH_PREFIX):
                            continue
                        old_sig, old_bid = self._parse_seen_value(old_val)
                        if (
                            old_sig == sig
                            and os.path.splitext(old_path)[1].lower().lstrip('.') == ext
                            and not os.path.exists(old_path)
                        ):
                            if existing_book_ids is not None and old_bid is not None and old_bid not in existing_book_ids:
                                self._seen.pop(old_path, None)
                                state_updated = True
                                continue
                            renamed_from.append(old_path)
                            if renamed_bid is None and old_bid is not None:
                                renamed_bid = old_bid

                    if cid_val is not None or renamed_from:
                        for old_path in renamed_from:
                            self._seen.pop(old_path, None)
                        new_stored = self._format_seen_value(sig, renamed_bid)
                        self._seen[full_path] = new_stored
                        if content_id:
                            self._seen[content_id] = new_stored
                        state_updated = True
                        if existing_book_ids is not None and renamed_bid is None:
                            discovered.append((full_path, ext, sig))
                        continue

                    discovered.append((full_path, ext, sig))

            if state_updated:
                atomic_json_save(self.state_path, self._seen)
        discovered.sort(
            key=lambda item: (
                os.path.dirname(item[0]).lower(),
                self.FORMAT_PRIORITY.get(item[1], 99),
                os.path.basename(item[0]).lower(),
            )
        )
        return discovered

    @staticmethod
    def _significant_words(text: str) -> set[str]:
        """Extract lowercase alphanumeric words of 3+ characters (excluding common stopwords)."""
        words = TITLE_ALNUM_RE.sub(' ', (text or '').lower()).split()
        return {w for w in words if len(w) >= 3 and w not in STOPWORDS}

    @classmethod
    def _is_placeholder_title(cls, title: Optional[str]) -> bool:
        """Return True if title is missing or looks like a raw ISBN or filename with extension."""
        cleaned = (title or '').strip()
        if not cleaned:
            return True
        return bool(PLACEHOLDER_TITLE_RE.match(cleaned))

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
        if cls._is_placeholder_title(getattr(mi, 'title', None)) or mi.title == stem:
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

        # Reconcile working/codename titles (e.g. "Tremble for My Country" inside an EPUB named
        # "Between the World and Me - Ta-Nehisi Coates.epub") using sibling formats with the same stem.
        stem_words = cls._significant_words(stem)
        title_words = cls._significant_words(getattr(mi, 'title', '') or '')
        if stem_words and not cls._is_placeholder_title(getattr(mi, 'title', None)) and not (title_words & stem_words):
            for fname in dir_files:
                sib_stem, sib_dot_ext = os.path.splitext(fname)
                sib_ext = sib_dot_ext.lower().lstrip('.')
                if sib_stem.lower() == stem.lower() and sib_ext != ext.lower() and sib_ext in cls.FORMAT_PRIORITY:
                    try:
                        sib_path = os.path.join(parent_dir, fname)
                        with open(sib_path, 'rb') as sib_stream:
                            sib_mi = get_metadata(sib_stream, stream_type=sib_ext, use_libprs_metadata=True)
                        sib_title = getattr(sib_mi, 'title', '') or ''
                        if cls._significant_words(sib_title) & stem_words:
                            mi.title = sib_title
                            break
                    except Exception:
                        continue

        if cls._is_placeholder_title(getattr(mi, 'title', None)):
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
    def _extract_comparable_identifiers(mi: Any) -> dict[str, str]:
        """Extract normalized ISBN/ASIN/Amazon identifiers from a Metadata object."""
        result: dict[str, str] = {}
        raw_idents: dict[str, Any] = {}
        if hasattr(mi, 'get_identifiers'):
            try:
                raw_idents = dict(mi.get_identifiers() or {})
            except Exception:
                raw_idents = {}
        elif getattr(mi, 'identifiers', None):
            try:
                raw_idents = dict(mi.identifiers or {})
            except Exception:
                raw_idents = {}
        for k, v in raw_idents.items():
            key_low = str(k or '').strip().lower()
            val_norm = re.sub(r'[\s-]+', '', str(v or '').strip()).upper()
            if key_low in MATCH_IDENTIFIER_KEYS and val_norm:
                result[key_low] = val_norm
        isbn_attr = re.sub(r'[\s-]+', '', str(getattr(mi, 'isbn', '') or '').strip()).upper()
        if isbn_attr and isbn_attr != 'NONE' and 'isbn' not in result:
            result['isbn'] = isbn_attr
        return result

    @classmethod
    def _find_existing_book_ids(
        cls,
        db: Any,
        mi: Any,
        full_path: str = '',
        ext: str = ''
    ) -> list[int]:
        """Return existing library book IDs matching mi by identity, identifiers, normalized title, or content hash."""
        try:
            if hasattr(db, 'find_identical_books'):
                ids = db.find_identical_books(mi)
                if ids:
                    return sorted(int(bid) for bid in ids)
        except Exception:
            pass

        title_raw = str(getattr(mi, 'title', '') or '').strip().lower()
        title_alnum = TITLE_ALNUM_RE.sub(' ', title_raw).strip()
        mi_idents = cls._extract_comparable_identifiers(mi)
        matched: set[int] = set()
        try:
            all_ids = list(db.all_book_ids())
        except Exception:
            all_ids = []

        for bid in all_ids:
            try:
                if title_raw:
                    t = str(db.field_for('title', bid) or '').strip().lower()
                    if t and (t == title_raw or (title_alnum and TITLE_ALNUM_RE.sub(' ', t).strip() == title_alnum)):
                        matched.add(int(bid))
                        continue
                if mi_idents:
                    book_idents = db.field_for('identifiers', bid) or {}
                    for k, v in book_idents.items():
                        key_low = str(k or '').strip().lower()
                        val_norm = re.sub(r'[\s-]+', '', str(v or '').strip()).upper()
                        if key_low in mi_idents and val_norm and mi_idents[key_low] == val_norm:
                            matched.add(int(bid))
                            break
                        if key_low in ('asin', 'mobi-asin', 'amazon') and val_norm:
                            if any(
                                mi_idents.get(mk) == val_norm
                                for mk in ('asin', 'mobi-asin', 'amazon')
                            ):
                                matched.add(int(bid))
                                break
            except Exception:
                continue
        if matched:
            return sorted(matched)

        # Fallback for books renamed inside calibre after import: compare byte size + partial SHA-256
        # of the watched source file against existing format files in the calibre library.
        if full_path and os.path.isfile(full_path) and hasattr(db, 'format_abspath'):
            try:
                src_size = os.path.getsize(full_path)
            except OSError:
                src_size = 0
            if src_size > 0:
                src_cid: Optional[str] = None
                target_fmt = ext.upper().lstrip('.') if ext else ''
                for bid in all_ids:
                    try:
                        raw_fmts = db.formats(bid) or ()
                        fmts = [str(f).upper() for f in raw_fmts if f]
                        if target_fmt and target_fmt in fmts:
                            check_fmts = [target_fmt] + [f for f in fmts if f != target_fmt]
                        else:
                            check_fmts = fmts
                        for fmt in check_fmts:
                            fmt_path = db.format_abspath(bid, fmt)
                            if not fmt_path or not os.path.isfile(fmt_path):
                                continue
                            if os.path.getsize(fmt_path) != src_size:
                                continue
                            if src_cid is None:
                                src_cid = cls._compute_file_content_id(full_path, src_size)
                            if src_cid and cls._compute_file_content_id(fmt_path, src_size) == src_cid:
                                matched.add(int(bid))
                                break
                    except Exception:
                        continue

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
        seen_stems_in_batch: dict[tuple[str, str], int] = {}

        for full_path, ext, sig in discovered_items:
            if abort is not None and abort.is_set():
                break
            try:
                was_previously_seen = False
                with self._lock:
                    prev_sig, _prev_bid = self._parse_seen_value(self._seen.get(full_path))
                    was_previously_seen = (prev_sig == sig)

                stem_key = (
                    os.path.normcase(os.path.dirname(full_path)),
                    os.path.splitext(os.path.basename(full_path))[0].strip().lower()
                )
                if stem_key in seen_stems_in_batch:
                    skipped_dups += 1
                    matched_bid = seen_stems_in_batch[stem_key]
                    stored_entry = self._format_seen_value(sig, matched_bid)
                    content_id = self._compute_file_content_id(full_path)
                    with self._lock:
                        self._seen[full_path] = stored_entry
                        if content_id:
                            self._seen[content_id] = stored_entry
                    if log_fn:
                        log_fn(
                            f'[AUTO-IMPORT-DUP] Skipped alternate format {os.path.basename(full_path)} '
                            f'for book #{matched_bid}'
                        )
                    continue

                mi, sidecar_cover = self._read_metadata_with_sidecars(full_path, ext, log_fn=log_fn)
                existing_ids = self._find_existing_book_ids(db, mi, full_path=full_path, ext=ext)
                linked_bid: Optional[int] = existing_ids[0] if existing_ids else None
                if existing_ids or db.has_book(mi):
                    if linked_bid is not None:
                        seen_stems_in_batch[stem_key] = linked_bid
                    unfixed_existing = [
                        bid for bid in existing_ids
                        if not was_previously_seen and needs_fix_fn is not None and needs_fix_fn(bid)
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
                        linked_bid = bid
                        seen_stems_in_batch[stem_key] = bid
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
                stored_entry = self._format_seen_value(sig, linked_bid)
                content_id = self._compute_file_content_id(full_path)
                with self._lock:
                    self._seen[full_path] = stored_entry
                    if content_id:
                        self._seen[content_id] = stored_entry
            except Exception as err:
                if log_fn:
                    log_fn(f'[AUTO-IMPORT-ERR] {os.path.basename(full_path)}: {err}')

        self.save()
        return added_ids, retry_fix_ids, skipped_dups
