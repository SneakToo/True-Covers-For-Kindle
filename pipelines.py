# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import sys
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterable, Optional

try:
    from calibre_plugins.true_covers_kindle.config import PREFS
    from calibre_plugins.true_covers_kindle.core import (
        ASIN_B0_RE, AMAZON_IDENTIFIER_KEYS, CDE_TYPE_EBOK, CDE_TYPE_PDOC,
        ExthBinaryCodec, AsinResolver
    )
except ImportError:
    from config import PREFS
    from core import (
        ASIN_B0_RE, AMAZON_IDENTIFIER_KEYS, CDE_TYPE_EBOK, CDE_TYPE_PDOC,
        ExthBinaryCodec, AsinResolver
    )

REFLOWABLE_SOURCE_FORMATS = ('EPUB', 'MOBI', 'FB2', 'DOCX', 'HTMLZ', 'RTF', 'TXT')
PERSONAL_DOCUMENT_CONVERTIBLE_FORMATS = (
    'EPUB', 'MOBI', 'CBZ', 'CBR', 'PDF', 'FB2', 'DOCX', 'HTMLZ', 'RTF', 'TXT'
)

CONVERSION_TIMEOUT_SEC = 240

STATUS_FIXED     = 'fixed'
STATUS_UNMATCHED = 'unmatched'
STATUS_NO_AZW3   = 'no_azw3'
STATUS_ERROR     = 'error'
STATUS_INFO      = 'info'

PDOC_STATUS_OK        = 'ok'
PDOC_STATUS_NO_FORMAT = 'no_format'
PDOC_STATUS_ERROR     = 'error'

PERSONAL_DOC_IDENTIFIER_KEY   = 'true_covers_kindle'
PERSONAL_DOC_IDENTIFIER_VALUE = 'personal_document'
# Legacy identifier key/value preserved so books already marked in the library keep working
LEGACY_PERSONAL_DOC_ID_KEY    = 'stoker'
LEGACY_PERSONAL_DOC_ID_VALUE  = 'banked'


def has_personal_doc_identifier(identifiers: Optional[dict[str, Any]]) -> bool:
    """Return True if the metadata identifiers dict marks the book as a Personal Document (including legacy values)."""
    if not identifiers:
        return False
    return (
        identifiers.get(PERSONAL_DOC_IDENTIFIER_KEY) == PERSONAL_DOC_IDENTIFIER_VALUE
        or identifiers.get(LEGACY_PERSONAL_DOC_ID_KEY) == LEGACY_PERSONAL_DOC_ID_VALUE
    )


def clear_personal_doc_identifier(identifiers: dict[str, Any]) -> bool:
    """Remove current and legacy Personal Document marker keys from identifiers dict. Return True if modified."""
    removed_new = identifiers.pop(PERSONAL_DOC_IDENTIFIER_KEY, None) is not None
    removed_old = identifiers.pop(LEGACY_PERSONAL_DOC_ID_KEY, None) is not None
    return removed_new or removed_old


def get_book_formats(db: Any, book_id: int) -> list[str]:
    """Return uppercased list of format extensions available for book_id in db."""
    return [f.upper() for f in (db.formats(book_id) or [])]


def find_ebook_convert_exe() -> str:
    """Locate calibre's ebook-convert executable across platforms."""
    exe_name = 'ebook-convert.exe' if os.name == 'nt' else 'ebook-convert'
    if sys.executable:
        cand = os.path.join(os.path.dirname(sys.executable), exe_name)
        if os.path.exists(cand):
            return cand
    found = shutil.which('ebook-convert')
    if found:
        return found
    for c in (
        r'C:\Program Files\Calibre2\ebook-convert.exe',
        r'C:\Program Files (x86)\Calibre2\ebook-convert.exe',
        '/Applications/calibre.app/Contents/MacOS/ebook-convert',
        '/usr/bin/ebook-convert',
        '/usr/local/bin/ebook-convert',
    ):
        if os.path.exists(c):
            return c
    return 'ebook-convert'


def convert_book_to_azw3(db: Any, book_id: int, src_fmt: str) -> bool:
    """Convert a book format in the calibre database to AZW3 using ebook-convert."""
    src_path = db.format_abspath(book_id, src_fmt)
    if not src_path or not os.path.exists(src_path):
        return False
    ebook_convert = find_ebook_convert_exe()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_azw3 = os.path.join(tmpdir, f'book_{book_id}.azw3')
        subprocess.run(
            [ebook_convert, src_path, out_azw3, '--share-not-sync'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CONVERSION_TIMEOUT_SEC,
            check=False
        )
        if os.path.exists(out_azw3) and os.path.getsize(out_azw3) > 0:
            db.add_format(book_id, 'AZW3', out_azw3, replace=True, run_hooks=False)
            return True
    return False


def ensure_azw3_format(
    db: Any,
    book_id: int,
    fmts: list[str],
    allowed_source_formats: Iterable[str],
    log_fn: Optional[Callable[[str], None]] = None,
    title: str = ''
) -> tuple[list[str], bool, Optional[str]]:
    """Convert the first available format in allowed_source_formats to AZW3 if AZW3 is missing."""
    if 'AZW3' in fmts:
        return fmts, False, None
    src_fmt = next((f for f in allowed_source_formats if f in fmts), None)
    if not src_fmt:
        return fmts, False, None
    if log_fn:
        label = title or f'Book #{book_id}'
        log_fn(f'[CONVERT] #{book_id} "{label}" — converting {src_fmt} -> AZW3...')
    if convert_book_to_azw3(db, book_id, src_fmt):
        return get_book_formats(db, book_id), True, None
    return fmts, True, src_fmt


def purge_original_backups(db: Any, book_id: int) -> None:
    """Remove ORIGINAL_* backup formats created during metadata embedding when configured."""
    if not PREFS['delete_original_format']:
        return
    fmts = get_book_formats(db, book_id)
    origs = [f for f in fmts if f.startswith('ORIGINAL_')]
    if origs:
        db.remove_formats({book_id: origs})


def bake_and_stamp_azw3(
    db: Any,
    book_id: int,
    asin_or_key: str,
    cdetype: str = CDE_TYPE_EBOK,
    embed_cover: bool = True
) -> bool:
    """Embed metadata/cover into AZW3, purge ORIGINAL_* backups, and patch EXTH binary headers."""
    if embed_cover:
        db.embed_metadata([book_id], only_fmts=['AZW3'])
        purge_original_backups(db, book_id)

    azw3_path = db.format_abspath(book_id, 'AZW3')
    if not azw3_path or not os.path.exists(azw3_path):
        return False
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_azw3 = os.path.join(tmpdir, f'patched_{book_id}.azw3')
        shutil.copy2(azw3_path, tmp_azw3)
        if not ExthBinaryCodec.patch_file(tmp_azw3, asin_or_key, cdetype=cdetype):
            return False
        db.add_format(book_id, 'AZW3', tmp_azw3, replace=True, run_hooks=False)
    return True


def book_has_cover(db: Any, book_id: int) -> bool:
    """Return True if the book in calibre has a cover image."""
    try:
        if db.field_for('cover', book_id):
            return True
    except Exception:
        pass
    try:
        return bool(db.cover(book_id))
    except Exception:
        return False


def is_book_stamped_and_baked(
    db: Any,
    book_id: int,
    asin_resolver: Optional[AsinResolver] = None
) -> bool:
    """Fast Smart-Skip check using O(1) field_for() lookups and binary EXTH header inspection."""
    if not book_has_cover(db, book_id):
        return False
    fmts = get_book_formats(db, book_id)
    if 'AZW3' not in fmts:
        return False
    azw3_path = db.format_abspath(book_id, 'AZW3')
    info = ExthBinaryCodec.inspect_file(
        azw3_path, allow_isbn10=bool(PREFS['fallback_to_isbn10'])
    )
    if not info.get('is_stamped_and_baked'):
        return False
    if info.get('is_fixed_kindle_cover'):
        e113 = (info.get('exth_113') or '').upper()
        try:
            ids = dict(db.field_for('identifiers', book_id) or {})
            for k in AMAZON_IDENTIFIER_KEYS:
                meta_val = str(ids.get(k) or '').strip().upper()
                if ASIN_B0_RE.match(meta_val) and meta_val != e113:
                    return False
        except Exception:
            pass
        if PREFS['verify_live_cover_cdn'] and asin_resolver is not None:
            mi_title = str(db.field_for('title', book_id) or '')
            authors_tuple = db.field_for('authors', book_id) or ()
            mi_author = ' & '.join(authors_tuple)
            if asin_resolver.is_asin_cached_verified(e113, mi_title, mi_author):
                return True
            is_live, _sz = asin_resolver.verify_asin_live_cover(e113, mi_title, mi_author)
            if not is_live:
                return False
    return True


def _save_asin_identifiers(db: Any, book_id: int, mi: Any, identifiers: dict[str, Any], asin: str) -> None:
    """Assign a resolved ASIN to both 'amazon' and 'mobi-asin' identifiers, clear PDOC markers, and save metadata."""
    identifiers['amazon'] = asin
    identifiers['mobi-asin'] = asin
    clear_personal_doc_identifier(identifiers)
    mi.set_identifiers(identifiers)
    db.set_metadata(book_id, mi, set_title=False, set_authors=False)


class FixKindleCoverPipeline:
    """Fix Kindle Cover pipeline: resolves a live B0... Kindle ASIN, embeds the cover, and stamps EXTH 501=EBOK."""

    def __init__(self, asin_resolver: AsinResolver):
        self.asin_resolver = asin_resolver

    def stamp_manual_asin(self, db: Any, book_id: int, mi: Any, asin: str) -> bool:
        """Convert to AZW3 if needed, embed the cover, and stamp a user-supplied ASIN into EXTH headers."""
        purge_original_backups(db, book_id)
        existing_ids = dict(mi.get_identifiers() or {})
        _save_asin_identifiers(db, book_id, mi, existing_ids, asin)

        fmts = get_book_formats(db, book_id)
        if 'AZW3' not in fmts and PREFS['auto_convert_azw3']:
            fmts, _attempted, _failed_fmt = ensure_azw3_format(
                db, book_id, fmts, REFLOWABLE_SOURCE_FORMATS, title=mi.title or ''
            )

        if 'AZW3' in fmts:
            return bake_and_stamp_azw3(
                db, book_id, asin, cdetype=CDE_TYPE_EBOK,
                embed_cover=bool(PREFS['embed_cover_metadata'])
            )
        return False

    def process_book(
        self,
        db: Any,
        book_id: int,
        mi: Any,
        force_refresh: bool = False
    ) -> tuple[str, str]:
        """Run Fix Kindle Cover on a single book and return (status, detail_message)."""
        purge_original_backups(db, book_id)
        fmts = get_book_formats(db, book_id)

        # 1. Auto-Convert to AZW3 if missing
        conv_attempted = False
        conv_failed_fmt = None
        if 'AZW3' not in fmts and PREFS['auto_convert_azw3']:
            fmts, conv_attempted, conv_failed_fmt = ensure_azw3_format(
                db, book_id, fmts, REFLOWABLE_SOURCE_FORMATS, title=mi.title or ''
            )

        # 2. Inspect on-disk EXTH 113 if identifiers lacked a B0... ASIN
        identifiers = dict(mi.get_identifiers() or {})
        if 'AZW3' in fmts and not any(
            ASIN_B0_RE.match(str(identifiers.get(k) or '')) for k in AMAZON_IDENTIFIER_KEYS
        ):
            disk_info = ExthBinaryCodec.inspect_file(db.format_abspath(book_id, 'AZW3'))
            if disk_info.get('has_b0_asin'):
                identifiers['mobi-asin'] = disk_info['exth_113']

        book_isbn = getattr(mi, 'isbn', '') or identifiers.get('isbn') or ''
        cleaned_non_b0 = False
        if force_refresh and has_personal_doc_identifier(identifiers):
            clear_personal_doc_identifier(identifiers)
            cleaned_non_b0 = True

        for k in AMAZON_IDENTIFIER_KEYS:
            val = str(identifiers.get(k) or '').strip()
            if val and not ASIN_B0_RE.match(val):
                cleaned_isbn = AsinResolver.clean_isbn(val)
                if len(cleaned_isbn) in (10, 13):
                    if not book_isbn:
                        book_isbn = cleaned_isbn
                    if 'isbn' not in identifiers:
                        identifiers['isbn'] = cleaned_isbn
                if not PREFS['fallback_to_isbn10'] or force_refresh:
                    identifiers.pop(k, None)
                    cleaned_non_b0 = True

        author = ' & '.join(mi.authors) if getattr(mi, 'authors', None) else ''
        asin, source = self.asin_resolver.resolve(
            identifiers=identifiers,
            title=mi.title or '',
            author=author,
            isbn=book_isbn,
            use_api=bool(PREFS['lookup_asin_api']),
            use_isbn10_fallback=bool(PREFS['fallback_to_isbn10']),
            verify_cdn=bool(PREFS['verify_live_cover_cdn']),
            force_refresh=force_refresh
        )

        if asin:
            _save_asin_identifiers(db, book_id, mi, identifiers, asin)
        elif cleaned_non_b0:
            mi.set_identifiers(identifiers)
            db.set_metadata(book_id, mi, set_title=False, set_authors=False)

        # 3. If AZW3 is still missing on disk, explicitly report STATUS_NO_AZW3
        if 'AZW3' not in fmts:
            present_str = ', '.join(fmts) if fmts else 'None'
            if conv_failed_fmt:
                return (
                    STATUS_NO_AZW3,
                    f'Auto-convert from {conv_failed_fmt} -> AZW3 failed (formats: {present_str}; ASIN: {asin or "none"})'
                )
            return (
                STATUS_NO_AZW3,
                f'Missing AZW3 format on disk (formats present: {present_str}; ASIN: {asin or "none"})'
            )

        # 4. Embed cover & stamp binary EXTH 113/504/501=EBOK
        if PREFS['patch_exth_headers'] and asin:
            stamped = bake_and_stamp_azw3(
                db, book_id, asin, cdetype=CDE_TYPE_EBOK,
                embed_cover=bool(PREFS['embed_cover_metadata'])
            )
            if not stamped:
                return STATUS_ERROR, f'Failed to stamp EXTH binary headers into AZW3 for ASIN {asin}'
            conv_note = ' [Auto-converted -> AZW3]' if conv_attempted else ''
            return STATUS_FIXED, f'Embedded cover + stamped ASIN {asin} ({source}){conv_note}'

        if PREFS['embed_cover_metadata']:
            db.embed_metadata([book_id], only_fmts=['AZW3'])
            purge_original_backups(db, book_id)

        if not asin:
            return (
                STATUS_UNMATCHED,
                'AZW3 present & cover embedded, but no live B0... Kindle ASIN found on Amazon cover server'
            )

        return STATUS_INFO, f'Metadata updated with ASIN {asin} ({source})'


class PersonalDocumentPipeline:
    """Mark as Personal Document pipeline: embeds the calibre cover into AZW3 and stamps EXTH 501=PDOC."""

    @staticmethod
    def mark_as_personal_document(
        db: Any,
        book_id: int,
        mi: Any,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> tuple[bool, str, str]:
        """Convert to AZW3 if needed, embed the calibre cover, and stamp EXTH 501=PDOC with a local document key.

        Returns (succeeded, status_code, detail_message).
        """
        title = mi.title or f'Book #{book_id}'
        purge_original_backups(db, book_id)
        fmts = get_book_formats(db, book_id)

        if 'AZW3' not in fmts:
            fmts, _attempted, _failed_fmt = ensure_azw3_format(
                db, book_id, fmts, PERSONAL_DOCUMENT_CONVERTIBLE_FORMATS, log_fn=log_fn, title=title
            )

        if 'AZW3' not in fmts:
            return False, PDOC_STATUS_NO_FORMAT, 'No convertible format found to produce AZW3'

        identifiers = dict(mi.get_identifiers() or {})
        identifiers.pop('amazon', None)
        identifiers.pop('mobi-asin', None)
        clear_personal_doc_identifier(identifiers)
        identifiers[PERSONAL_DOC_IDENTIFIER_KEY] = PERSONAL_DOC_IDENTIFIER_VALUE
        mi.set_identifiers(identifiers)
        db.set_metadata(book_id, mi, set_title=False, set_authors=False)

        pdoc_key = f'{CDE_TYPE_PDOC}{int(book_id):06d}'
        if not bake_and_stamp_azw3(db, book_id, pdoc_key, cdetype=CDE_TYPE_PDOC, embed_cover=True):
            return False, PDOC_STATUS_ERROR, 'Failed to stamp EXTH 501=PDOC binary header into AZW3'
        return True, PDOC_STATUS_OK, f'Embedded cover + stamped EXTH 501=PDOC ({pdoc_key})'
