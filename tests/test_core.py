# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import struct
import tempfile
import time
import unittest

from core import (
    CDE_TYPE_EBOK,
    CDE_TYPE_PDOC,
    AsinResolver,
    ExthBinaryCodec,
    atomic_json_load,
    atomic_json_save,
    migrate_legacy_json_file,
)
from config import (
    POLICY_AUTO_PERSONAL_DOC,
    migrate_legacy_preferences,
)
from auto_import import AutoImportEngine
from pipelines import (
    LEGACY_PERSONAL_DOC_ID_KEY,
    LEGACY_PERSONAL_DOC_ID_VALUE,
    PERSONAL_DOC_IDENTIFIER_KEY,
    PERSONAL_DOC_IDENTIFIER_VALUE,
    clear_personal_doc_identifier,
    has_personal_doc_identifier,
)


def _build_mobi_record(
    exth_items: list[tuple[int, bytes]],
    full_name: bytes = b'The Count of Monte Cristo'
) -> bytes:
    """Build a synthetic MOBI header record with an EXTH block and trailing full title."""
    mobi_len = 232
    rec_header = bytearray(16 + mobi_len)
    rec_header[16:20] = b'MOBI'
    rec_header[20:24] = struct.pack('>I', mobi_len)
    rec_header[128:132] = struct.pack('>I', ExthBinaryCodec.EXTH_FLAG_PRESENT)

    body = b''.join(struct.pack('>II', rt, 8 + len(rp)) + rp for rt, rp in exth_items)
    unpadded_len = 12 + len(body)
    pad_len = (4 - (unpadded_len % 4)) % 4
    exth_block = b'EXTH' + struct.pack('>II', unpadded_len, len(exth_items)) + body + (b'\x00' * pad_len)

    full_name_off = len(rec_header) + len(exth_block)
    rec_header[84:88] = struct.pack('>I', full_name_off)
    rec_header[88:92] = struct.pack('>I', len(full_name))
    return bytes(rec_header) + exth_block + full_name


def _build_synthetic_azw3(exth_items: list[tuple[int, bytes]], dual_header: bool = True) -> bytes:
    """Build a synthetic PalmDOC/AZW3 binary file with one or two MOBI records."""
    rec0 = _build_mobi_record(exth_items, full_name=b'Primary Title')
    records = [rec0, b'HTML payload record']
    if dual_header:
        rec_kf8 = _build_mobi_record(exth_items, full_name=b'KF8 Boundary Title')
        records.append(rec_kf8)

    num_records = len(records)
    palmdoc = bytearray(78)
    palmdoc[0:32] = b'Synthetic_AZW3'.ljust(32, b'\x00')
    palmdoc[60:68] = b'BOOKMOBI'
    palmdoc[76:78] = struct.pack('>H', num_records)

    table_size = num_records * 8
    cur_off = 78 + table_size
    table = bytearray()
    for idx, r_bytes in enumerate(records):
        table += struct.pack('>I', cur_off) + struct.pack('>I', idx)
        cur_off += len(r_bytes)

    return bytes(palmdoc) + bytes(table) + b''.join(records)


class TestExthBinaryCodec(unittest.TestCase):
    """Tests for PalmDOC/MOBI/KF8 binary EXTH inspection and multi-header patching."""

    def test_inspect_and_patch_ebok_dual_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            azw3_path = os.path.join(tmpdir, 'sample.azw3')
            initial_items = [
                (ExthBinaryCodec.EXTH_COVER_OFFSET, struct.pack('>I', 3)),
                (ExthBinaryCodec.EXTH_ASIN_PRIMARY, b'9780140449266'),
            ]
            with open(azw3_path, 'wb') as f:
                f.write(_build_synthetic_azw3(initial_items, dual_header=True))

            before = ExthBinaryCodec.inspect_file(azw3_path)
            self.assertTrue(before['valid_mobi'])
            self.assertTrue(before['has_exth_201'])
            self.assertEqual(before['cover_offset'], 3)
            self.assertFalse(before['has_b0_asin'])
            self.assertFalse(before['is_fixed_kindle_cover'])
            self.assertFalse(before['is_stamped_and_baked'])

            ok = ExthBinaryCodec.patch_file(azw3_path, 'B000FBJCJE', cdetype=CDE_TYPE_EBOK)
            self.assertTrue(ok)

            after = ExthBinaryCodec.inspect_file(azw3_path)
            self.assertTrue(after['valid_mobi'])
            self.assertTrue(after['has_exth_201'])
            self.assertEqual(after['exth_113'], 'B000FBJCJE')
            self.assertEqual(after['exth_504'], 'B000FBJCJE')
            self.assertEqual(after['exth_501'], CDE_TYPE_EBOK)
            self.assertTrue(after['has_b0_asin'])
            self.assertTrue(after['is_fixed_kindle_cover'])
            self.assertFalse(after['is_personal_document'])
            self.assertTrue(after['is_stamped_and_baked'])

            # Verify that both Record 0 and the secondary KF8 MOBI record were patched
            # and their internal full-name offsets still point to the exact title strings
            with open(azw3_path, 'rb') as f:
                patched_bytes = f.read()
            rec0_off = struct.unpack('>I', patched_bytes[78:82])[0]
            rec1_off = struct.unpack('>I', patched_bytes[86:90])[0]
            rec2_off = struct.unpack('>I', patched_bytes[94:98])[0]

            rec0 = patched_bytes[rec0_off:rec1_off]
            fn0_off = struct.unpack('>I', rec0[84:88])[0]
            fn0_len = struct.unpack('>I', rec0[88:92])[0]
            self.assertEqual(rec0[fn0_off:fn0_off + fn0_len], b'Primary Title')

            rec2 = patched_bytes[rec2_off:]
            parsed_kf8 = ExthBinaryCodec.parse_record_exth(rec2)
            self.assertIsNotNone(parsed_kf8)
            kf8_map = dict(parsed_kf8['items'])
            self.assertEqual(kf8_map[ExthBinaryCodec.EXTH_ASIN_PRIMARY], b'B000FBJCJE')
            self.assertEqual(kf8_map[ExthBinaryCodec.EXTH_ASIN_SECONDARY], b'B000FBJCJE')
            self.assertEqual(kf8_map[ExthBinaryCodec.EXTH_CDE_TYPE], b'EBOK')
            fn2_off = struct.unpack('>I', rec2[84:88])[0]
            fn2_len = struct.unpack('>I', rec2[88:92])[0]
            self.assertEqual(rec2[fn2_off:fn2_off + fn2_len], b'KF8 Boundary Title')

    def test_patch_personal_document_pdoc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            azw3_path = os.path.join(tmpdir, 'doc.azw3')
            initial_items = [(ExthBinaryCodec.EXTH_COVER_OFFSET, struct.pack('>I', 1))]
            with open(azw3_path, 'wb') as f:
                f.write(_build_synthetic_azw3(initial_items, dual_header=False))

            ok = ExthBinaryCodec.patch_file(azw3_path, 'PDOC000042', cdetype=CDE_TYPE_PDOC)
            self.assertTrue(ok)

            info = ExthBinaryCodec.inspect_file(azw3_path)
            self.assertTrue(info['is_personal_document'])
            self.assertFalse(info['is_fixed_kindle_cover'])
            self.assertTrue(info['is_stamped_and_baked'])
            self.assertEqual(info['exth_501'], CDE_TYPE_PDOC)
            self.assertEqual(info['exth_113'], 'PDOC000042')
            self.assertEqual(info['exth_504'], 'PDOC000042')

    def test_inspect_invalid_or_missing_file(self):
        missing = ExthBinaryCodec.inspect_file('/nonexistent/file.azw3')
        self.assertFalse(missing['valid_mobi'])
        self.assertFalse(ExthBinaryCodec.patch_file('/nonexistent/file.azw3', 'B000FBJCJE'))


class TestAsinResolver(unittest.TestCase):
    """Tests for ISBN conversion, title/author anti-spam matching, and persistent cache."""

    def test_isbn13_to_isbn10(self):
        self.assertEqual(AsinResolver.isbn13_to_isbn10('978-0-14-044926-6'), '0140449264')
        self.assertEqual(AsinResolver.isbn13_to_isbn10('043942089X'), '043942089X')
        self.assertIsNone(AsinResolver.isbn13_to_isbn10('9791234567890'))
        self.assertIsNone(AsinResolver.isbn13_to_isbn10('invalid'))

    def test_normalize_title_variants_and_author(self):
        variants = AsinResolver.normalize_title_variants(
            'Frankenstein; or, The Modern Prometheus (Penguin Classics), Illustrated'
        )
        self.assertEqual(variants[0], 'Frankenstein')
        self.assertIn('Frankenstein; or, The Modern Prometheus', variants)

        self.assertEqual(
            AsinResolver.normalize_primary_author('Dumas, Alexandre, Père & Maquet, Auguste'),
            'Alexandre Dumas'
        )
        self.assertEqual(
            AsinResolver.normalize_primary_author('Dr. Gabriel García Márquez (Translator)'),
            'Gabriel Garcia Marquez'
        )

    def test_derivative_spam_and_title_matching(self):
        # Genuine editions match
        self.assertFalse(
            AsinResolver.is_derivative_or_mismatched_title(
                'Dune', 'Dune: Deluxe Edition', 'Frank Herbert'
            )
        )
        # Study guides, summaries, and author-in-title spam are rejected
        self.assertTrue(
            AsinResolver.is_derivative_or_mismatched_title(
                'Dune', 'Summary of Dune by Frank Herbert: Detailed Analysis', 'Frank Herbert'
            )
        )
        self.assertTrue(
            AsinResolver.is_derivative_or_mismatched_title(
                'Dune', 'Dune SparkNotes Literature Guide', 'Frank Herbert'
            )
        )
        self.assertTrue(
            AsinResolver.is_derivative_or_mismatched_title(
                'Dune', 'Dune 6-Book Boxed Set', 'Frank Herbert'
            )
        )
        # If the book's actual expected title contains "Summary", it is not rejected for that keyword
        self.assertFalse(
            AsinResolver.is_derivative_or_mismatched_title(
                'Executive Summary', 'Executive Summary', 'Jane Doe'
            )
        )
        # Possessives, missing apostrophes, and corrupt U+FFFD apostrophes match cleanly
        self.assertFalse(
            AsinResolver.is_derivative_or_mismatched_title(
                "A People's History of the United States",
                'A People\ufffds History of the United States: 1492 - Present',
                'Howard Zinn'
            )
        )
        self.assertFalse(
            AsinResolver.is_derivative_or_mismatched_title(
                'A Peoples History of the United States',
                "A People's History of the United States",
                'Howard Zinn'
            )
        )
        # "by <surname>" in candidate title is rejected as derivative/messy listing
        self.assertTrue(
            AsinResolver.is_derivative_or_mismatched_title(
                "A People's History of the United States",
                'A Young Peoples History of the United States by Zinn, Howard [Hardcover]',
                'Howard Zinn'
            )
        )
        # Exact core title has 0 extra tokens compared to spin-offs
        self.assertEqual(
            AsinResolver._title_extra_token_count(
                "A People's History of the United States",
                'A People\ufffds History of the United States: 1492 - Present'
            ),
            0
        )
        self.assertGreater(
            AsinResolver._title_extra_token_count(
                "A People's History of the United States",
                "Voices of a People's History of the United States"
            ),
            0
        )

    def test_author_matching(self):
        self.assertTrue(AsinResolver.is_matching_author('Alexandre Dumas', 'Dumas, Alexandre'))
        self.assertTrue(AsinResolver.is_matching_author('Fyodor Dostoevsky', 'Fyodor Dostoyevsky'))
        self.assertFalse(AsinResolver.is_matching_author('Frank Herbert', 'BookHabits Summaries'))
        self.assertFalse(AsinResolver.is_matching_author('Frank Herbert', 'SparkNotes Editors'))
        self.assertFalse(AsinResolver.is_matching_author('Frank Herbert', 'Isaac Asimov'))

    def test_cache_persistence_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, 'asin_cache.json')
            resolver = AsinResolver(cache_path)

            asin, src = resolver.resolve(
                identifiers={'mobi-asin': 'B000FBJCJE'},
                title='The Count of Monte Cristo',
                author='Alexandre Dumas',
                isbn='',
                use_api=False,
                verify_cdn=False
            )
            self.assertEqual(asin, 'B000FBJCJE')
            self.assertEqual(src, 'calibre_metadata_b0')

            resolver.store_b0_cache('|dune|frank herbert', 'B00B7NPRY8', verified=True)
            reloaded = AsinResolver(cache_path)
            asin2, src2 = reloaded.resolve(
                identifiers={},
                title='Dune',
                author='Frank Herbert',
                isbn='',
                use_api=False,
                verify_cdn=False
            )
            self.assertEqual(asin2, 'B00B7NPRY8')
            self.assertEqual(src2, 'local_cache_b0')

            reloaded.clear_cache()
            asin3, src3 = reloaded.resolve(
                identifiers={},
                title='Dune',
                author='Frank Herbert',
                isbn='',
                use_api=False,
                verify_cdn=False
            )
            self.assertIsNone(asin3)
            self.assertEqual(src3, 'unmatched')


class TestMigrationsAndIdentifiers(unittest.TestCase):
    """Tests for non-destructive JSON/preference migrations and Personal Document identifier helpers."""

    def test_migrate_legacy_json_file_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_path = os.path.join(tmpdir, 'Stoker_ASIN_Cache.json')
            new_path = os.path.join(tmpdir, 'true_covers_kindle_asin_cache.json')
            payload = {'queries': {'|dune|frank herbert': {'asin': 'B00B7NPRY8', 'verified': True}}}
            atomic_json_save(legacy_path, payload)

            self.assertTrue(migrate_legacy_json_file(new_path, legacy_path))
            self.assertTrue(os.path.exists(legacy_path))
            self.assertEqual(atomic_json_load(new_path), payload)

            # Subsequent calls do not overwrite new_path
            atomic_json_save(new_path, {'updated': True})
            self.assertFalse(migrate_legacy_json_file(new_path, legacy_path))
            self.assertEqual(atomic_json_load(new_path), {'updated': True})

    def test_migrate_legacy_preferences(self):
        class DummyPrefs(dict):
            def __init__(self, file_path):
                super().__init__()
                self.file_path = file_path

        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_prefs_path = os.path.join(tmpdir, 'Stoker.json')
            new_prefs_path = os.path.join(tmpdir, 'true_covers_kindle.json')
            atomic_json_save(
                legacy_prefs_path,
                {
                    'watch_folder_enabled': True,
                    'ignite_handoff_stoke': False,
                    'unmatched_handoff_policy': 'auto_bank',
                    'obsolete_unknown_key': 123,
                }
            )

            prefs = DummyPrefs(new_prefs_path)
            self.assertTrue(migrate_legacy_preferences(prefs))
            self.assertTrue(prefs['watch_folder_enabled'])
            self.assertFalse(prefs['auto_import_handoff_fix_cover'])
            self.assertEqual(prefs['unmatched_handoff_policy'], POLICY_AUTO_PERSONAL_DOC)
            self.assertNotIn('obsolete_unknown_key', prefs)
            self.assertTrue(os.path.exists(legacy_prefs_path))

    def test_personal_doc_identifiers_support_legacy_and_new_markers(self):
        ids_legacy = {LEGACY_PERSONAL_DOC_ID_KEY: LEGACY_PERSONAL_DOC_ID_VALUE}
        self.assertTrue(has_personal_doc_identifier(ids_legacy))
        self.assertTrue(clear_personal_doc_identifier(ids_legacy))
        self.assertFalse(has_personal_doc_identifier(ids_legacy))

        ids_new = {PERSONAL_DOC_IDENTIFIER_KEY: PERSONAL_DOC_IDENTIFIER_VALUE}
        self.assertTrue(has_personal_doc_identifier(ids_new))
        self.assertTrue(clear_personal_doc_identifier(ids_new))
        self.assertFalse(has_personal_doc_identifier(ids_new))


class TestAutoImportEngine(unittest.TestCase):
    """Tests for non-destructive watch-folder discovery and state tracking."""

    def test_discover_settled_files_ignores_partial_downloads_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = os.path.join(tmpdir, 'watch')
            os.makedirs(watch_dir)
            state_path = os.path.join(tmpdir, 'watch_state.json')

            settled_epub = os.path.join(watch_dir, 'book.epub')
            partial_file = os.path.join(watch_dir, 'downloading.epub.part')
            qb_file = os.path.join(watch_dir, 'torrent.azw3.!qB')

            for path in (settled_epub, partial_file, qb_file):
                with open(path, 'wb') as f:
                    f.write(b'ebook content')
                old_time = time.time() - 120
                os.utime(path, (old_time, old_time))

            engine = AutoImportEngine(state_path)
            found = engine.discover_settled_files(watch_dir, {'epub', 'azw3'}, min_settle_sec=10)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][0], os.path.normpath(settled_epub))
            self.assertEqual(found[0][1], 'epub')

            # Source files in watch folder are completely untouched
            self.assertTrue(os.path.exists(settled_epub))
            with open(settled_epub, 'rb') as f:
                self.assertEqual(f.read(), b'ebook content')
            os.close(os.open(settled_epub, os.O_RDONLY))

    def test_forget_book_ids_allows_rediscovery_on_failed_fix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = os.path.join(tmpdir, 'watch')
            os.makedirs(watch_dir)
            state_path = os.path.join(tmpdir, 'watch_state.json')
            settled_epub = os.path.normpath(os.path.join(watch_dir, 'zinn.epub'))
            with open(settled_epub, 'wb') as f:
                f.write(b'ebook content')

            engine = AutoImportEngine(state_path)
            engine._seen[settled_epub] = '13:1000'
            engine._book_id_to_paths[163] = [settled_epub]
            engine.save()

            engine.forget_book_ids([163])
            found = engine.discover_settled_files(watch_dir, {'epub'}, min_settle_sec=0)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][0], settled_epub)


class TestBuildPlugin(unittest.TestCase):
    """Tests for build_plugin.py packaging including LICENSE, THIRD_PARTY_NOTICES.md, and optional licenses/ folder."""

    def test_build_plugin_zip_includes_required_and_optional_license_files(self):
        import zipfile
        from build_plugin import REQUIRED_ROOT_FILES, build_plugin_zip

        with tempfile.TemporaryDirectory() as tmpdir:
            for rel_path in REQUIRED_ROOT_FILES:
                with open(os.path.join(tmpdir, rel_path), 'w', encoding='utf-8') as f:
                    f.write(f'# {rel_path}\n')

            os.makedirs(os.path.join(tmpdir, 'images'))
            with open(os.path.join(tmpdir, 'images', 'icon.png'), 'wb') as f:
                f.write(b'\x89PNG')

            os.makedirs(os.path.join(tmpdir, 'licenses'))
            with open(os.path.join(tmpdir, 'licenses', 'SAMPLE_MIT.txt'), 'w', encoding='utf-8') as f:
                f.write('MIT License text\n')

            out_zip, packed = build_plugin_zip(tmpdir, 'test_plugin.zip')
            self.assertTrue(os.path.isfile(out_zip))
            self.assertIn('LICENSE', packed)
            self.assertIn('THIRD_PARTY_NOTICES.md', packed)
            self.assertIn('images/icon.png', packed)
            self.assertIn('licenses/SAMPLE_MIT.txt', packed)

            with zipfile.ZipFile(out_zip, 'r') as zf:
                names = set(zf.namelist())
                self.assertIn('LICENSE', names)
                self.assertIn('THIRD_PARTY_NOTICES.md', names)
                self.assertIn('licenses/SAMPLE_MIT.txt', names)


if __name__ == '__main__':
    unittest.main()
