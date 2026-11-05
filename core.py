# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import re
import json
import gzip
import time
import struct
import difflib
import unicodedata
import threading
import urllib.request
import urllib.parse
from typing import Any, Callable, Optional

KINDLE_VALID_ID_RE = re.compile(r'^(B0[A-Z0-9]{8}|[0-9]{9}[0-9X])$')
ASIN_B0_RE         = re.compile(r'^(B0[A-Z0-9]{8})$', re.I)
EXTRACT_B0_RE      = re.compile(r'\b(B0[A-Z0-9]{8})\b')

AMAZON_IDENTIFIER_KEYS = ('mobi-asin', 'amazon', 'asin')
CDE_TYPE_EBOK          = 'EBOK'
CDE_TYPE_PDOC          = 'PDOC'

# Derivative / companion / summary-mill / box-set blocker regex
DERIVATIVE_SPAM_RE = re.compile(
    r'\b('
    r'study\s*guide|summary|summaries|workbook|sparknotes|cliffs?\s*notes|'
    r'book\s*notes|york\s*notes|monarch\s*notes|brodie\'?s\s*notes|maxnotes|'
    r'barron\'?s|litcharts|course\s*hero|gradesaver|shmoop|bookey|'
    r'literature\s+guides?|learning\s+guides?|reader\'?s\s+guides?|critical\s+guides?|'
    r'implementation\s+guides?|action\s+plans?|cheat\s+sheets?|key\s+insights|'
    r'chapter\s+breakdown|book\s+reviews?|fast[\s\-]*track|reflection\s+prompts|'
    r'guided\s+journal|prompt\s+journal|reading\s+guides?|book\s+club\s+kit|'
    r'character\s+analysis|unofficial|coloring\s+book|activity\s+book|sourcebook|'
    r'box\s*set|boxed\s*set|\d+[\s\-]*book\s+(?:bundle|set|collection)|'
    r'study\s+notes|revision\s+notes|exam\s+prep|chapter\s+analysis|'
    r'companion|analysis\s+of|detailed\s+analysis|key\s+takeaways|'
    r'review\s+and\s+analysis|discussion\s+questions|book\s+club\s+questions|'
    r'novel\s+unit|lesson\s+plans?|trivia|quicklet|instaread|supersummary|'
    r'bookrags|worth\s+books|bright\s+summaries|bookhabits|readtrepreneur|'
    r'snapreads|comprehension\s+guide|student\s+guide|teacher\s+guide|'
    r'conversation\s+starters|bestseller\s+summary|executive\s+summary'
    r')\b',
    re.I
)

AUTHOR_IMPOSTOR_RE = re.compile(
    r'\b(guides?|fans?|fan\s+club|press|publishing|publications|editorial|'
    r'summaries|digest|notes|club|studio|media|group|llc|inc|books|quizmaster|litclub)\b',
    re.I
)

STOP_WORDS = frozenset({'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or', 'is', 'are'})
EDITION_SUBTITLE_WORDS = frozenset({
    'novel', 'memoir', 'edition', 'ed', 'illustrated', 'revised', 'expanded', 'updated',
    'anniversary', 'deluxe', 'special', 'digital', 'complete', 'unabridged',
    'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
    '1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th'
})
EDITION_SUFFIX_RE = re.compile(
    r'(?:,\s*|\s+-\s+|\s+)'
    r'(?:(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|'
    r'revised|expanded|updated|anniversary|deluxe|special|collector\'?s|digital|illustrated|'
    r'complete|unabridged|student|international)\s+)+'
    r'(?:edition|ed\.?)\b.*$',
    re.I
)
SURNAME_PARTICLES = frozenset({'le', 'la', 'de', 'del', 'della', 'di', 'da', 'van', 'von', 'der', 'den', 'st', 'st.', 'mac', 'mc'})


def atomic_json_load(path: str, default_factory: Callable[[], Any] = dict) -> Any:
    """Thread-safe JSON loader returning default_factory() on missing or corrupt files."""
    if path and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            pass
    return default_factory()


def atomic_json_save(path: str, data: Any) -> None:
    """Crash-safe atomic JSON writer using a temporary sibling file and os.replace()."""
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f'{path}.tmp.{os.getpid()}'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        pass


def migrate_legacy_json_file(new_path: str, legacy_path: str) -> bool:
    """Copy a legacy JSON cache or state file to new_path if new_path does not exist yet, keeping the old file intact."""
    if not new_path or not legacy_path:
        return False
    if os.path.exists(new_path) or not os.path.exists(legacy_path):
        return False
    try:
        data = atomic_json_load(legacy_path, default_factory=lambda: None)
        if data is not None:
            atomic_json_save(new_path, data)
            return True
    except OSError:
        pass
    return False


class ExthBinaryCodec:
    """Unified binary parser, inspector, and multi-header patcher for PalmDOC / MOBI6 / KF8 EXTH containers."""

    PALMDOC_MAGIC               = b'BOOKMOBI'
    PALMDOC_MAGIC_OFFSET        = 60
    PALMDOC_NUM_RECORDS_OFFSET  = 76
    PALMDOC_RECORD_TABLE_OFFSET = 78
    PALMDOC_RECORD_ENTRY_BYTES  = 8
    MOBI_MAGIC                  = b'MOBI'
    MOBI_MAGIC_OFFSET           = 16
    MOBI_LEN_OFFSET             = 20
    MOBI_FULL_NAME_OFFSET       = 84
    MOBI_EXTH_FLAGS_OFFSET      = 128
    MIN_MOBI_HEADER_BYTES       = 132
    EXTH_FLAG_PRESENT           = 0x40
    EXTH_MAGIC                  = b'EXTH'
    EXTH_HEADER_BYTES           = 12
    EXTH_ITEM_HEADER_BYTES      = 8
    EXTH_ASIN_PRIMARY           = 113
    EXTH_COVER_OFFSET           = 201
    EXTH_CDE_TYPE               = 501
    EXTH_ASIN_SECONDARY         = 504
    INVALID_OFFSET_SENTINEL     = 0xFFFFFFFF
    INSPECT_HEADER_READ_BYTES   = 65536

    @classmethod
    def parse_record_exth(cls, rec_bytes: bytes) -> Optional[dict[str, Any]]:
        """Parse the EXTH block inside a single MOBI record (Record 0 or KF8 boundary record)."""
        if (
            len(rec_bytes) < cls.MIN_MOBI_HEADER_BYTES
            or rec_bytes[cls.MOBI_MAGIC_OFFSET:cls.MOBI_MAGIC_OFFSET + 4] != cls.MOBI_MAGIC
        ):
            return None
        mobi_len = struct.unpack('>I', rec_bytes[cls.MOBI_LEN_OFFSET:cls.MOBI_LEN_OFFSET + 4])[0]
        exth_flags = struct.unpack('>I', rec_bytes[cls.MOBI_EXTH_FLAGS_OFFSET:cls.MOBI_EXTH_FLAGS_OFFSET + 4])[0]
        if not (exth_flags & cls.EXTH_FLAG_PRESENT):
            return None
        exth_off = cls.MOBI_MAGIC_OFFSET + mobi_len
        if (
            len(rec_bytes) < exth_off + cls.EXTH_HEADER_BYTES
            or rec_bytes[exth_off:exth_off + 4] != cls.EXTH_MAGIC
        ):
            return None
        _exth_len, num_items = struct.unpack('>II', rec_bytes[exth_off + 4:exth_off + cls.EXTH_HEADER_BYTES])
        pos = exth_off + cls.EXTH_HEADER_BYTES
        items = []
        for _ in range(num_items):
            if pos + cls.EXTH_ITEM_HEADER_BYTES > len(rec_bytes):
                break
            r_type, r_len = struct.unpack('>II', rec_bytes[pos:pos + cls.EXTH_ITEM_HEADER_BYTES])
            if r_len < cls.EXTH_ITEM_HEADER_BYTES or pos + r_len > len(rec_bytes):
                break
            items.append((r_type, rec_bytes[pos + cls.EXTH_ITEM_HEADER_BYTES:pos + r_len]))
            pos += r_len
        padded_end = pos + ((4 - ((pos - exth_off) % 4)) % 4)
        return {
            'mobi_len': mobi_len,
            'exth_off': exth_off,
            'exth_raw_end': pos,
            'exth_padded_end': min(len(rec_bytes), padded_end),
            'items': items,
        }

    @classmethod
    def locate_exth_region(cls, data: bytes) -> Optional[dict[str, Any]]:
        """Locate and parse the primary EXTH header block in Record 0 of a PalmDOC/MOBI container."""
        if (
            len(data) < cls.PALMDOC_RECORD_TABLE_OFFSET
            or data[cls.PALMDOC_MAGIC_OFFSET:cls.PALMDOC_MAGIC_OFFSET + 8] != cls.PALMDOC_MAGIC
        ):
            return None
        num_records = struct.unpack('>H', data[cls.PALMDOC_NUM_RECORDS_OFFSET:cls.PALMDOC_RECORD_TABLE_OFFSET])[0]
        table_end = cls.PALMDOC_RECORD_TABLE_OFFSET + num_records * cls.PALMDOC_RECORD_ENTRY_BYTES
        if num_records < 2 or len(data) < table_end:
            return None
        rec0_pos = cls.PALMDOC_RECORD_TABLE_OFFSET
        rec1_pos = cls.PALMDOC_RECORD_TABLE_OFFSET + cls.PALMDOC_RECORD_ENTRY_BYTES
        rec0_off = struct.unpack('>I', data[rec0_pos:rec0_pos + 4])[0]
        rec1_off = struct.unpack('>I', data[rec1_pos:rec1_pos + 4])[0]
        if len(data) < rec0_off + cls.MIN_MOBI_HEADER_BYTES:
            return None
        rec0_slice = data[rec0_off:min(len(data), rec1_off)]
        parsed = cls.parse_record_exth(rec0_slice)
        if not parsed:
            return None
        return {
            'num_records': num_records,
            'rec0_off': rec0_off,
            'rec1_off': rec1_off,
            'exth_off': rec0_off + parsed['exth_off'],
            'exth_end': rec0_off + parsed['exth_raw_end'],
            'items': parsed['items'],
        }

    @classmethod
    def inspect_file(cls, path: str, allow_isbn10: bool = False) -> dict[str, Any]:
        """Inspect the first 64KB of a MOBI/AZW3 file and return EXTH 113, 201, 501, and 504 status flags."""
        result: dict[str, Any] = {
            'valid_mobi': False,
            'has_exth_201': False,
            'cover_offset': None,
            'exth_501': None,
            'exth_113': None,
            'exth_504': None,
            'has_b0_asin': False,
            'is_personal_document': False,
            'is_fixed_kindle_cover': False,
            'is_stamped_and_baked': False,
        }
        if not path or not os.path.exists(path):
            return result
        try:
            with open(path, 'rb') as f:
                header_bytes = f.read(cls.INSPECT_HEADER_READ_BYTES)
            info = cls.locate_exth_region(header_bytes)
            if not info:
                return result

            result['valid_mobi'] = True
            for r_type, r_payload in info['items']:
                if r_type == cls.EXTH_COVER_OFFSET and len(r_payload) == 4:
                    val = struct.unpack('>I', r_payload)[0]
                    if val != cls.INVALID_OFFSET_SENTINEL:
                        result['has_exth_201'] = True
                        result['cover_offset'] = val
                elif r_type == cls.EXTH_CDE_TYPE:
                    result['exth_501'] = r_payload.decode('utf-8', errors='ignore').strip()
                elif r_type == cls.EXTH_ASIN_PRIMARY:
                    result['exth_113'] = r_payload.decode('utf-8', errors='ignore').strip()
                elif r_type == cls.EXTH_ASIN_SECONDARY:
                    result['exth_504'] = r_payload.decode('utf-8', errors='ignore').strip()

            e113 = result['exth_113'] or ''
            e504 = result['exth_504'] or ''
            has_b0 = bool(e113 and e113 == e504 and ASIN_B0_RE.match(e113))
            result['has_b0_asin'] = has_b0

            is_personal_document = bool(
                result['has_exth_201']
                and result['exth_501'] == CDE_TYPE_PDOC
                and e113
                and e113 == e504
                and e113.startswith(CDE_TYPE_PDOC)
            )
            result['is_personal_document'] = is_personal_document

            id_pattern = KINDLE_VALID_ID_RE if allow_isbn10 else ASIN_B0_RE
            is_fixed_kindle_cover = bool(
                result['has_exth_201']
                and result['exth_501'] == CDE_TYPE_EBOK
                and e113
                and e113 == e504
                and id_pattern.match(e113)
            )
            result['is_fixed_kindle_cover'] = is_fixed_kindle_cover
            result['is_stamped_and_baked'] = bool(is_fixed_kindle_cover or is_personal_document)
            return result
        except (OSError, struct.error, ValueError):
            return result

    @classmethod
    def _patch_single_mobi_record(
        cls, rec_bytes: bytes, asin_bytes: bytes, cde_bytes: bytes
    ) -> tuple[bytes, bool]:
        """Rewrite EXTH 113, 504, and 501 in a single MOBI record and adjust its internal full-name offset."""
        parsed = cls.parse_record_exth(rec_bytes)
        if not parsed:
            return rec_bytes, False

        target_tags = (cls.EXTH_ASIN_PRIMARY, cls.EXTH_ASIN_SECONDARY, cls.EXTH_CDE_TYPE)
        kept = [(rt, rp) for (rt, rp) in parsed['items'] if rt not in target_tags]
        kept.append((cls.EXTH_ASIN_PRIMARY, asin_bytes))
        kept.append((cls.EXTH_ASIN_SECONDARY, asin_bytes))
        kept.append((cls.EXTH_CDE_TYPE, cde_bytes))

        body = b''.join(
            struct.pack('>II', rt, cls.EXTH_ITEM_HEADER_BYTES + len(rp)) + rp
            for rt, rp in kept
        )
        unpadded_len = cls.EXTH_HEADER_BYTES + len(body)
        pad_len = (4 - (unpadded_len % 4)) % 4
        new_exth = (
            cls.EXTH_MAGIC
            + struct.pack('>II', unpadded_len, len(kept))
            + body
            + (b'\x00' * pad_len)
        )

        exth_off = parsed['exth_off']
        old_padded_end = parsed['exth_padded_end']
        exth_delta = len(new_exth) - (old_padded_end - exth_off)

        rec_mut = bytearray(rec_bytes[:exth_off])
        if len(rec_mut) >= cls.MOBI_FULL_NAME_OFFSET + 4 and exth_delta != 0:
            full_name_off = struct.unpack('>I', rec_mut[cls.MOBI_FULL_NAME_OFFSET:cls.MOBI_FULL_NAME_OFFSET + 4])[0]
            if full_name_off != cls.INVALID_OFFSET_SENTINEL and full_name_off >= old_padded_end:
                rec_mut[cls.MOBI_FULL_NAME_OFFSET:cls.MOBI_FULL_NAME_OFFSET + 4] = struct.pack(
                    '>I', full_name_off + exth_delta
                )

        new_rec = bytes(rec_mut) + new_exth + rec_bytes[old_padded_end:]
        return new_rec, True

    @classmethod
    def patch_file(cls, path: str, asin_str: str, cdetype: str = CDE_TYPE_EBOK) -> bool:
        """Stamp EXTH 113, EXTH 504, and EXTH 501 across all MOBI/KF8 headers in a file and recompute record offsets."""
        if not path or not os.path.exists(path):
            return False
        with open(path, 'rb') as f:
            data = f.read()

        if (
            len(data) < cls.PALMDOC_RECORD_TABLE_OFFSET
            or data[cls.PALMDOC_MAGIC_OFFSET:cls.PALMDOC_MAGIC_OFFSET + 8] != cls.PALMDOC_MAGIC
        ):
            return False
        num_records = struct.unpack('>H', data[cls.PALMDOC_NUM_RECORDS_OFFSET:cls.PALMDOC_RECORD_TABLE_OFFSET])[0]
        table_end = cls.PALMDOC_RECORD_TABLE_OFFSET + num_records * cls.PALMDOC_RECORD_ENTRY_BYTES
        if num_records < 2 or len(data) < table_end:
            return False

        offsets = [
            struct.unpack(
                '>I',
                data[
                    cls.PALMDOC_RECORD_TABLE_OFFSET + i * cls.PALMDOC_RECORD_ENTRY_BYTES:
                    cls.PALMDOC_RECORD_TABLE_OFFSET + i * cls.PALMDOC_RECORD_ENTRY_BYTES + 4
                ]
            )[0]
            for i in range(num_records)
        ]
        offsets.append(len(data))

        asin_bytes = asin_str.encode('utf-8')
        cde_bytes = cdetype.encode('utf-8')

        new_records = []
        any_patched = False
        for i in range(num_records):
            r_bytes = data[offsets[i]:offsets[i + 1]]
            if (
                len(r_bytes) >= cls.MIN_MOBI_HEADER_BYTES
                and r_bytes[cls.MOBI_MAGIC_OFFSET:cls.MOBI_MAGIC_OFFSET + 4] == cls.MOBI_MAGIC
            ):
                patched_r, ok = cls._patch_single_mobi_record(r_bytes, asin_bytes, cde_bytes)
                if ok:
                    any_patched = True
                    r_bytes = patched_r
            new_records.append(r_bytes)

        if not any_patched:
            return False

        header_prefix = bytearray(data[:table_end])
        gap_before_rec0 = data[table_end:offsets[0]]
        cur_off = offsets[0]
        for i, r_bytes in enumerate(new_records):
            entry_pos = cls.PALMDOC_RECORD_TABLE_OFFSET + i * cls.PALMDOC_RECORD_ENTRY_BYTES
            header_prefix[entry_pos:entry_pos + 4] = struct.pack('>I', cur_off)
            cur_off += len(r_bytes)

        out = bytes(header_prefix) + gap_before_rec0 + b''.join(new_records)
        with open(path, 'wb') as f:
            f.write(out)
        return True


class AsinResolver:
    """Multi-source B0... Kindle Edition ASIN resolver with persistent Amazon cover and identity verification cache."""

    MIN_VALID_JPEG_BYTES          = 1200
    COVER_PROBE_READ_BYTES        = 16384
    MAX_MEMO_ENTRIES              = 2000
    MIN_TITLE_TOKEN_OVERLAP_RATIO = 0.6
    MIN_SURNAME_SIMILARITY_RATIO  = 0.80
    DEFAULT_HTTP_TIMEOUT_SEC      = 8
    DEFAULT_CDN_TIMEOUT_SEC       = 5
    GOODREADS_VERIFY_TIMEOUT_SEC  = 4
    AMAZON_CARD_SLICE_CHARS       = 6000
    DDG_CONTEXT_WINDOW_CHARS      = 250
    MAX_CANDIDATES_AMAZON_DDG     = 6
    MAX_CANDIDATES_CATALOG        = 8
    JPEG_SOI_MAGIC                = b'\xff\xd8'
    GZIP_MAGIC                    = b'\x1f\x8b'

    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._live_verify_memo: dict[str, tuple[bool, int]] = {}
        self._cache, self._verified_asins = self._load_and_sanitize()

    def _load_and_sanitize(self) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = atomic_json_load(self.cache_path, dict)
        cleaned_queries = {}
        cleaned_asins = {}
        if isinstance(raw, dict):
            # Support both v5 flat dicts and v6 structured {'queries': ..., 'verified_asins': ...}
            q_map = raw.get('queries') if isinstance(raw.get('queries'), dict) else raw
            a_map = raw.get('verified_asins') if isinstance(raw.get('verified_asins'), dict) else {}
            for k, v in q_map.items():
                if k == 'verified_asins' or not isinstance(v, dict):
                    continue
                asin_val = v.get('asin')
                if asin_val and ASIN_B0_RE.match(str(asin_val)):
                    au = str(asin_val).upper()
                    cleaned_queries[k] = {
                        'asin': au,
                        'verified': bool(v.get('verified', False)),
                        'ts': v.get('ts', time.time())
                    }
            for asin_k, info in a_map.items():
                if ASIN_B0_RE.match(str(asin_k)) and isinstance(info, dict):
                    cleaned_asins[str(asin_k).upper()] = info
        return cleaned_queries, cleaned_asins

    def _save(self) -> None:
        atomic_json_save(
            self.cache_path,
            {'queries': self._cache, 'verified_asins': self._verified_asins}
        )

    def clear_cache(self) -> None:
        """Clear all in-memory and on-disk cached ASIN query and verification entries."""
        with self._lock:
            self._cache.clear()
            self._verified_asins.clear()
            self._live_verify_memo.clear()
            self._save()

    @staticmethod
    def clean_isbn(raw: Any) -> str:
        """Strip non-ISBN characters and uppercase any trailing X check digit."""
        return re.sub(r'[^0-9Xx]', '', str(raw or '')).upper()

    @staticmethod
    def strip_accents(s: Any) -> str:
        """Normalize Unicode characters and curly/corrupt apostrophes to ASCII equivalents."""
        text = str(s or '')
        text = re.sub(r'(?<=[A-Za-z0-9])[\u2018\u2019\u02bc\u2032\ufffd](?=[A-Za-z0-9])', "'", text)
        text = re.sub(r'[\u2018\u2019\u02bc\u2032]', "'", text)
        norm = unicodedata.normalize('NFKD', text)
        return norm.encode('ascii', 'ignore').decode('ascii')

    @classmethod
    def _tokenize_title_words(cls, title_str: Any) -> list[str]:
        """Extract lowercased alphanumeric title tokens with in-word apostrophes collapsed (so People's == Peoples)."""
        collapsed = re.sub(r"(?<=[a-zA-Z0-9])'(?=[a-zA-Z0-9])", '', cls.strip_accents(title_str).lower())
        return re.findall(r'[a-z0-9]+', collapsed)

    @classmethod
    def _title_extra_token_count(cls, expected_title: Any, candidate_title: Any) -> int:
        """Return the number of extra non-stop, non-edition tokens in candidate_title's core title."""
        exp_variants = cls.normalize_title_variants(cls.strip_accents(expected_title))
        short_exp = exp_variants[0] if exp_variants else cls.strip_accents(expected_title)
        exp_set = {w for w in cls._tokenize_title_words(short_exp) if w not in STOP_WORDS and len(w) > 1}

        cand_variants = cls.normalize_title_variants(cls.strip_accents(candidate_title))
        short_cand = cand_variants[0] if cand_variants else cls.strip_accents(candidate_title)
        cand_tokens = set(cls._tokenize_title_words(short_cand))
        return sum(
            1 for w in cand_tokens
            if w not in exp_set and w not in STOP_WORDS and w not in EDITION_SUBTITLE_WORDS
        )

    @classmethod
    def normalize_title_variants(cls, title: Any) -> list[str]:
        """Produce ordered title search variants from shortest core title to full title."""
        t = str(title or '').strip()
        if not t:
            return []
        t_no_paren = re.sub(r'[\(\[][^)\]]*[\)\]]', ' ', t).strip()
        t_no_illus = re.sub(r',\s*illustrated\b.*$', '', t_no_paren, flags=re.I).strip()
        t_no_ed = EDITION_SUFFIX_RE.sub('', t_no_illus).strip()
        short = re.split(r'\s*(?:;|\:|\u2013|\u2014|\s-\s|,\s+or\b)\s*', t_no_ed, flags=re.I)[0].strip()
        short = EDITION_SUFFIX_RE.sub('', short).strip()
        variants = []
        for cand in (short, t_no_ed, t_no_illus, t_no_paren, t):
            c_clean = re.sub(r'\s+', ' ', cand).strip()
            if c_clean and c_clean not in variants:
                variants.append(c_clean)
        return variants

    @classmethod
    def _split_author_segments(cls, author: Any) -> list[str]:
        """Split an author string into individual normalized 'First Last' author names."""
        a = cls.strip_accents(author).strip()
        if not a:
            return []
        a = re.sub(r'[\(\[][^)\]]*[\)\]]', ' ', a).strip()
        a = re.sub(
            r',\s*(?:pere|fils|jr\.?|sr\.?|ii|iii|iv|trans\.?|translator|ed\.?|editor|illus\.?)\b',
            '',
            a,
            flags=re.I
        ).strip()
        raw_segments = [
            seg.strip()
            for seg in re.split(r'\s*(?:&|\band\b|;)\s*', a, flags=re.I)
            if seg.strip()
        ]
        authors: list[str] = []
        for seg in raw_segments:
            if ',' in seg:
                comma_parts = [p.strip() for p in seg.split(',') if p.strip()]
                if len(comma_parts) == 2:
                    p0_words = comma_parts[0].split()
                    p1_words = comma_parts[1].split()
                    if len(p0_words) == 1 and len(p1_words) >= 1:
                        seg = f'{comma_parts[1]} {comma_parts[0]}'
                        comma_parts = [seg]
                    elif (
                        len(p0_words) == 2
                        and p0_words[0].lower() in SURNAME_PARTICLES
                        and len(p1_words) >= 1
                    ):
                        seg = f'{comma_parts[1]} {comma_parts[0]}'
                        comma_parts = [seg]
                for cp in comma_parts:
                    cp_clean = re.sub(r'^(?:sir|lord|lady|dr\.?|prof\.?)\s+', '', cp, flags=re.I).strip()
                    cp_clean = re.sub(r'\s+', ' ', cp_clean).strip()
                    if cp_clean and cp_clean not in authors:
                        authors.append(cp_clean)
            else:
                seg_clean = re.sub(r'^(?:sir|lord|lady|dr\.?|prof\.?)\s+', '', seg, flags=re.I).strip()
                seg_clean = re.sub(r'\s+', ' ', seg_clean).strip()
                if seg_clean and seg_clean not in authors:
                    authors.append(seg_clean)
        return authors

    @classmethod
    def normalize_primary_author(cls, author: Any) -> str:
        """Extract and normalize the primary author name, converting 'Last, First' to 'First Last'."""
        authors = cls._split_author_segments(author)
        return authors[0] if authors else ''

    @classmethod
    def _normalize_identity(cls, title: Any, author: Any) -> tuple[str, str]:
        """Return (lowercased_short_title, lowercased_primary_author) for cache keys and verification."""
        variants = cls.normalize_title_variants(title)
        short_title = (variants[0] if variants else str(title or '')).lower()
        primary_author = cls.normalize_primary_author(author).lower()
        return short_title, primary_author

    @classmethod
    def is_derivative_or_mismatched_title(
        cls, expected_title: Any, candidate_title: Any, expected_author: Any = ''
    ) -> bool:
        """Return True if candidate_title looks like a study guide/summary/box-set or fails token overlap."""
        exp_t = cls.strip_accents(expected_title).strip()
        cand_t = cls.strip_accents(candidate_title).strip()
        if not cand_t:
            return True

        for m in DERIVATIVE_SPAM_RE.finditer(cand_t):
            kw = m.group(0).lower()
            if kw not in exp_t.lower():
                return True

        prim_auth = cls.normalize_primary_author(expected_author)
        if prim_auth and len(prim_auth) >= 4:
            if (
                re.search(r'\bby\s+' + re.escape(prim_auth), cand_t, re.I)
                and not re.search(r'\bby\s+' + re.escape(prim_auth), exp_t, re.I)
            ):
                return True
            auth_parts = [w for w in re.findall(r'[a-z0-9]+', prim_auth.lower()) if len(w) >= 3]
            if auth_parts:
                surname = auth_parts[-1]
                if (
                    re.search(r'\bby\s+' + re.escape(surname) + r'\b', cand_t, re.I)
                    and not re.search(r'\bby\s+' + re.escape(surname) + r'\b', exp_t, re.I)
                ):
                    return True
            if re.search(re.escape(prim_auth) + r"'?s\s+book\b", cand_t, re.I):
                return True

        exp_variants = cls.normalize_title_variants(exp_t)
        short_exp = exp_variants[0] if exp_variants else exp_t
        exp_tokens = [
            w for w in cls._tokenize_title_words(short_exp)
            if w not in STOP_WORDS and w not in EDITION_SUBTITLE_WORDS and len(w) > 1
        ]
        if not exp_tokens:
            exp_tokens = [w for w in cls._tokenize_title_words(short_exp) if w not in STOP_WORDS and len(w) > 1]
        if not exp_tokens:
            exp_tokens = [w for w in cls._tokenize_title_words(short_exp) if len(w) >= 1]

        cand_variants = cls.normalize_title_variants(cand_t)
        short_cand = cand_variants[0] if cand_variants else cand_t
        short_cand_tokens = set(cls._tokenize_title_words(short_cand))

        if exp_tokens:
            matched = sum(1 for w in exp_tokens if w in short_cand_tokens)
            if matched / len(exp_tokens) < cls.MIN_TITLE_TOKEN_OVERLAP_RATIO:
                return True
            extra_main_tokens = [
                w for w in short_cand_tokens
                if w not in set(exp_tokens) and w not in STOP_WORDS and w not in EDITION_SUBTITLE_WORDS
            ]
            if len(extra_main_tokens) >= 3:
                return True
            if len(exp_tokens) <= 2 and len(extra_main_tokens) >= 2:
                return True
        return False

    @classmethod
    def is_matching_author(cls, expected_author: Any, candidate_author: Any) -> bool:
        """Return True if candidate_author matches any co-author surname in expected_author and is not a summary mill."""
        cand_a = cls.strip_accents(candidate_author).strip()
        exp_a = cls.strip_accents(expected_author).strip()
        if not cand_a:
            return False
        if DERIVATIVE_SPAM_RE.search(cand_a):
            return False
        for m in AUTHOR_IMPOSTOR_RE.finditer(cand_a):
            if m.group(0).lower() not in exp_a.lower():
                return False

        exp_authors = cls._split_author_segments(expected_author)
        if not exp_authors:
            return True
        cand_clean = re.sub(r'["\'].*?["\']', ' ', cand_a)
        cand_authors = cls._split_author_segments(cand_clean)
        if not cand_authors:
            return False

        cand_all_tokens: list[str] = []
        cand_surnames: list[str] = []
        for ca in cand_authors:
            parts = [w for w in re.findall(r'[a-z0-9]+', ca.lower()) if len(w) > 1]
            if parts:
                cand_all_tokens.extend(parts)
                cand_surnames.append(parts[-1])
        if not cand_all_tokens:
            return False

        for ea in exp_authors:
            exp_parts = [w for w in re.findall(r'[a-z0-9]+', ea.lower()) if len(w) > 1]
            if not exp_parts:
                return True
            surname = exp_parts[-1]
            if surname in cand_surnames or surname in cand_all_tokens:
                return True
            if len(surname) >= 5:
                for cs in cand_surnames:
                    if len(cs) >= 5 and difflib.SequenceMatcher(None, surname, cs).ratio() >= cls.MIN_SURNAME_SIMILARITY_RATIO:
                        return True
        return False

    @classmethod
    def isbn13_to_isbn10(cls, isbn: Any) -> Optional[str]:
        """Convert a 978-prefixed ISBN-13 (or existing ISBN-10) to a 10-character ISBN-10 with check digit."""
        s = cls.clean_isbn(isbn)
        if len(s) == 10:
            return s
        if len(s) == 13 and s.startswith('978'):
            core = s[3:12]
            total = sum((10 - i) * int(d) for i, d in enumerate(core))
            rem = (11 - (total % 11)) % 11
            check = 'X' if rem == 10 else str(rem)
            return core + check
        return None

    @classmethod
    def _fetch_text(
        cls,
        url: str,
        accept: str = 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
        timeout: int = DEFAULT_HTTP_TIMEOUT_SEC
    ) -> str:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/131.0.0.0 Safari/537.36'
                ),
                'Accept': accept,
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, identity',
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get('Content-Encoding', '').lower() == 'gzip' or raw[:2] == cls.GZIP_MAGIC:
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            return raw.decode('utf-8', errors='ignore')

    def is_asin_cached_verified(self, asin: Any, expected_title: str = '', expected_author: str = '') -> bool:
        """O(1) disk-backed check so Smart Skip does not re-query the network across calibre restarts."""
        asin_u = str(asin or '').strip().upper()
        if not ASIN_B0_RE.match(asin_u):
            return False
        short_t, prim_a = self._normalize_identity(expected_title, expected_author)
        with self._lock:
            entry = self._verified_asins.get(asin_u)
            if entry and entry.get('short_title') == short_t and entry.get('primary_author') == prim_a:
                return True
        return False

    def verify_asin_live_cover(
        self,
        asin: Any,
        expected_title: str = '',
        expected_author: str = '',
        timeout: int = DEFAULT_CDN_TIMEOUT_SEC
    ) -> tuple[bool, int]:
        """Verify that Amazon's cover server serves a valid JPEG for asin and that Goodreads identity matches."""
        if not asin:
            return False, 0
        asin_u = str(asin).strip().upper()
        short_t, prim_a = self._normalize_identity(expected_title, expected_author)
        memo_key = f'{asin_u}|{short_t}|{prim_a}'

        with self._lock:
            if memo_key in self._live_verify_memo:
                return self._live_verify_memo[memo_key]
            # Check persistent verified_asins table first
            v_entry = self._verified_asins.get(asin_u)
            if v_entry and v_entry.get('short_title') == short_t and v_entry.get('primary_author') == prim_a:
                res = (True, int(v_entry.get('size', 2000)))
                self._live_verify_memo[memo_key] = res
                return res

        live_sz = 0
        for url in (
            f'https://images-na.ssl-images-amazon.com/images/P/{asin_u}.01.LZZZZZZZ.jpg',
            f'https://m.media-amazon.com/images/P/{asin_u}.01.LZZZZZZZ.jpg',
        ):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Accept': 'image/jpeg,image/*,*/*;q=0.8',
                    }
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    img_bytes = resp.read(self.COVER_PROBE_READ_BYTES)
                    if len(img_bytes) >= self.MIN_VALID_JPEG_BYTES and img_bytes[:2] == self.JPEG_SOI_MAGIC:
                        live_sz = len(img_bytes)
                        break
            except Exception:
                continue

        if live_sz <= 0:
            return self._record_memo(memo_key, (False, 0))

        if expected_title:
            try:
                gr_url = f'https://www.goodreads.com/book/auto_complete?format=json&q={urllib.parse.quote(asin_u)}'
                items = json.loads(
                    self._fetch_text(gr_url, accept='application/json', timeout=self.GOODREADS_VERIFY_TIMEOUT_SEC)
                )
                if isinstance(items, list) and items:
                    top = items[0]
                    top_asin = str(top.get('asin') or '').strip().upper()
                    gr_title = str(top.get('title') or '').strip()
                    gr_author = str((top.get('author') or {}).get('name') or '').strip()
                    # Only enforce strict reverse identity if Goodreads actually returned a record for this ASIN
                    # (or if only 1 exact match was returned for the ASIN query)
                    if not top_asin or top_asin == asin_u or len(items) == 1:
                        if self.is_derivative_or_mismatched_title(expected_title, gr_title, expected_author):
                            return self._record_memo(memo_key, (False, 0))
                        if expected_author and gr_author and not self.is_matching_author(expected_author, gr_author):
                            return self._record_memo(memo_key, (False, 0))
            except Exception:
                pass

        with self._lock:
            self._verified_asins[asin_u] = {
                'short_title': short_t,
                'primary_author': prim_a,
                'size': live_sz,
                'ts': time.time(),
            }
            self._save()
        return self._record_memo(memo_key, (True, live_sz))

    def _record_memo(self, key: str, val: tuple[bool, int]) -> tuple[bool, int]:
        with self._lock:
            if len(self._live_verify_memo) >= self.MAX_MEMO_ENTRIES:
                self._live_verify_memo.clear()
            self._live_verify_memo[key] = val
        return val

    def _candidates_amazon_digital(self, title_variants: list[str], primary_author: str) -> list[str]:
        cands = []
        if not title_variants:
            return cands
        short_title = title_variants[0]
        queries = []
        for tv in title_variants[:2]:
            q = f'{tv} {primary_author}'.strip()
            if q not in queries:
                queries.append(q)

        for q in queries:
            try:
                url = f'https://www.amazon.com/s?k={urllib.parse.quote(q)}&i=digital-text'
                html = self._fetch_text(url)
                blocks = re.split(r'(?=data-asin=["\']B0[A-Z0-9]{8}["\'])', html, flags=re.I)
                for blk in blocks:
                    m_asin = re.match(r'data-asin=["\'](B0[A-Z0-9]{8})["\']', blk, re.I)
                    if not m_asin:
                        continue
                    cand_asin = m_asin.group(1).upper()
                    card_slice = blk[:self.AMAZON_CARD_SLICE_CHARS]

                    if re.search(r'puis-sponsored-label|sp-sponsored-result|>Sponsored<', card_slice, re.I):
                        continue

                    h2_match = re.search(r'<h2[^>]*>(.*?)</h2>', card_slice, re.I | re.S)
                    card_title = re.sub(r'<[^>]+>', ' ', h2_match.group(1)).strip() if h2_match else ''
                    plain_card = re.sub(r'<[^>]+>', ' ', card_slice)

                    if card_title and self.is_derivative_or_mismatched_title(short_title, card_title, primary_author):
                        continue
                    if not card_title and DERIVATIVE_SPAM_RE.search(plain_card[:1200]):
                        if not DERIVATIVE_SPAM_RE.search(short_title):
                            continue

                    if primary_author:
                        by_match = re.search(r'\bby\s+([^|\n<>]{2,80})', plain_card, re.I)
                        if by_match:
                            if not self.is_matching_author(primary_author, by_match.group(1)):
                                continue
                        else:
                            # Fallback: verify primary author's surname token appears in the card text
                            exp_tokens = [w for w in re.findall(r'[a-z0-9]+', primary_author.lower()) if len(w) > 1]
                            card_tokens = set(re.findall(r'[a-z0-9]+', plain_card[:2500].lower()))
                            if exp_tokens and exp_tokens[-1] not in card_tokens:
                                continue

                    if cand_asin not in cands:
                        cands.append(cand_asin)
                if cands:
                    break
            except Exception:
                continue
        return cands[:self.MAX_CANDIDATES_AMAZON_DDG]

    def _candidates_duckduckgo(self, title_variants: list[str], primary_author: str) -> list[str]:
        cands = []
        if not title_variants:
            return cands
        short_title = title_variants[0]
        q_list = [
            f'"{short_title}" "{primary_author}" site:amazon.com/dp/B0 -"study guide" -summary -workbook',
            f'"{short_title}" "{primary_author}" amazon kindle edition B0 -"study guide" -summary',
        ]
        for q in q_list:
            for endpoint in (
                f'https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}',
                f'https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(q)}',
            ):
                try:
                    html = self._fetch_text(endpoint)
                    unquoted = urllib.parse.unquote(html)
                    for m in re.finditer(
                        r'amazon\.[a-z.]+/(?:[^"\'\s<>]+/)?(?:dp|gp/product)/(B0[A-Z0-9]{8})',
                        unquoted,
                        re.I
                    ):
                        mu = m.group(1).upper()
                        ctx_start = max(0, m.start() - self.DDG_CONTEXT_WINDOW_CHARS)
                        ctx_end = min(len(unquoted), m.end() + self.DDG_CONTEXT_WINDOW_CHARS)
                        ctx_text = re.sub(r'<[^>]+>', ' ', unquoted[ctx_start:ctx_end])
                        if not DERIVATIVE_SPAM_RE.search(short_title) and DERIVATIVE_SPAM_RE.search(ctx_text):
                            continue
                        if mu not in cands:
                            cands.append(mu)
                    if cands:
                        return cands[:self.MAX_CANDIDATES_AMAZON_DDG]
                except Exception:
                    continue
        return cands[:self.MAX_CANDIDATES_AMAZON_DDG]

    def _candidates_goodreads_kindle(
        self, title_variants: list[str], primary_author: str, isbn: str = ''
    ) -> list[str]:
        cands = []
        short_title = title_variants[0] if title_variants else ''
        queries = []
        c_isbn = self.clean_isbn(isbn)
        if c_isbn and c_isbn not in queries:
            queries.append(c_isbn)
        # Goodreads auto_complete searches book titles first; querying the clean title
        # finds the canonical work (verified by item['author']) without getting crowded
        # out by "Summary: <Title> by <Author>" spam titles.
        for tv in title_variants[:2]:
            if tv and tv not in queries:
                queries.append(tv)
            if primary_author:
                q_with_author = f'{tv} {primary_author}'.strip()
                if q_with_author and q_with_author not in queries:
                    queries.append(q_with_author)

        checked_works = set()
        for q in queries[:6]:
            try:
                url = f'https://www.goodreads.com/book/auto_complete?format=json&q={urllib.parse.quote(q)}'
                items = json.loads(self._fetch_text(url, accept='application/json'))
                if not isinstance(items, list) or not items:
                    continue

                valid_items = []
                for item in items[:5]:
                    item_title = str(item.get('title') or '').strip()
                    item_author = str((item.get('author') or {}).get('name') or '').strip()

                    if short_title and self.is_derivative_or_mismatched_title(short_title, item_title, primary_author):
                        continue
                    if primary_author and item_author and not self.is_matching_author(primary_author, item_author):
                        continue
                    extra_cnt = self._title_extra_token_count(short_title, item_title) if short_title else 0
                    valid_items.append((extra_cnt, len(item_title), item))

                valid_items.sort(key=lambda entry: (entry[0], entry[1]))
                for extra_cnt, _title_len, item in valid_items:
                    if extra_cnt > 0 and cands:
                        break
                    gr_asin = str(item.get('asin') or '').strip().upper()
                    if ASIN_B0_RE.match(gr_asin) and gr_asin not in cands:
                        cands.append(gr_asin)

                    work_id = str(item.get('workId') or '').strip()
                    if work_id and work_id.isdigit() and work_id not in checked_works:
                        checked_works.add(work_id)
                        try:
                            ed_url = (
                                f'https://www.goodreads.com/work/editions/{work_id}'
                                f'?utf8=%E2%9C%93&filter_by%5Bformat%5D=Kindle+Edition'
                            )
                            ed_html = self._fetch_text(ed_url)
                            for m in EXTRACT_B0_RE.findall(ed_html):
                                mu = m.upper()
                                if mu not in cands:
                                    cands.append(mu)
                        except Exception:
                            pass

                    book_url = item.get('bookUrl')
                    if book_url and len(cands) < 4:
                        try:
                            b_html = self._fetch_text(f'https://www.goodreads.com{book_url}')
                            for m in EXTRACT_B0_RE.findall(b_html):
                                mu = m.upper()
                                if mu not in cands:
                                    cands.append(mu)
                        except Exception:
                            pass
                if cands:
                    break
            except Exception:
                continue
        return cands[:self.MAX_CANDIDATES_CATALOG]

    def _candidates_openlibrary(
        self, title_variants: list[str], primary_author: str, isbn: str = ''
    ) -> tuple[list[str], Optional[str]]:
        cands = []
        ol_isbn10 = None
        try:
            c_isbn = self.clean_isbn(isbn)
            if c_isbn:
                url = f'https://openlibrary.org/api/books?bibkeys=ISBN:{urllib.parse.quote(c_isbn)}&format=json&jscmd=data'
                data = json.loads(self._fetch_text(url, accept='application/json'))
                for entry in data.values():
                    ids = entry.get('identifiers') or {}
                    for amz in ids.get('amazon', []):
                        amz_s = str(amz).strip().upper()
                        if ASIN_B0_RE.match(amz_s) and amz_s not in cands:
                            cands.append(amz_s)
                    for i10 in ids.get('isbn_10', []):
                        c10 = self.clean_isbn(i10)
                        if len(c10) == 10 and not ol_isbn10:
                            ol_isbn10 = c10

            for tv in title_variants[:2]:
                q_str = f'title={urllib.parse.quote(tv)}'
                if primary_author:
                    q_str += f'&author={urllib.parse.quote(primary_author)}'
                url = f'https://openlibrary.org/search.json?{q_str}&fields=key,title,id_amazon,isbn&limit=8'
                data = json.loads(self._fetch_text(url, accept='application/json'))
                docs = data.get('docs') or []
                short_title = title_variants[0] if title_variants else ''
                for doc in docs:
                    doc_title = str(doc.get('title') or '').strip()
                    doc_authors = ', '.join(doc.get('author_name') or [])
                    if short_title and doc_title and self.is_derivative_or_mismatched_title(short_title, doc_title, primary_author):
                        continue
                    if primary_author and doc_authors and not self.is_matching_author(primary_author, doc_authors):
                        continue
                    for amz in (doc.get('id_amazon') or []):
                        amz_s = str(amz).strip().upper()
                        if ASIN_B0_RE.match(amz_s) and amz_s not in cands:
                            cands.append(amz_s)
                if not ol_isbn10:
                    for doc in docs:
                        for raw_isbn in (doc.get('isbn') or []):
                            c10 = self.isbn13_to_isbn10(raw_isbn)
                            if c10:
                                ol_isbn10 = c10
                                break
                        if ol_isbn10:
                            break
        except Exception:
            pass
        return cands[:self.MAX_CANDIDATES_CATALOG], ol_isbn10

    def _select_verified_candidate(
        self,
        candidates: list[str],
        tested: set[str],
        cache_key: str,
        short_title: str,
        primary_author: str,
        stage_name: str,
        verify_cdn: bool
    ) -> Optional[tuple[str, str]]:
        """Return (asin, source_label) for the first candidate that passes optional CDN verification."""
        for cand in candidates:
            if cand in tested:
                continue
            tested.add(cand)
            if not verify_cdn:
                self.store_b0_cache(cache_key, cand, verified=False)
                return cand, stage_name
            is_live, _sz = self.verify_asin_live_cover(cand, short_title, primary_author)
            if is_live:
                self.store_b0_cache(cache_key, cand, verified=True)
                return cand, f'{stage_name}_verified'
        return None

    def resolve(
        self,
        identifiers: dict[str, Any],
        title: str,
        author: str,
        isbn: str,
        use_api: bool = True,
        use_isbn10_fallback: bool = False,
        verify_cdn: bool = True,
        force_refresh: bool = False
    ) -> tuple[Optional[str], str]:
        """Resolve a book's Kindle B0... ASIN across metadata, local cache, and online catalogs."""
        title_variants = self.normalize_title_variants(title)
        primary_author = self.normalize_primary_author(author)
        short_title = title_variants[0] if title_variants else (title or '')
        cache_key = f'{self.clean_isbn(isbn)}|{short_title.lower()}|{primary_author.lower()}'

        # Step 1: Check existing B0... ASIN in calibre metadata
        cdn_only_existing_b0: Optional[str] = None
        for key in AMAZON_IDENTIFIER_KEYS:
            raw_val = str(identifiers.get(key) or '').strip().upper()
            m_b0 = EXTRACT_B0_RE.search(raw_val)
            val = m_b0.group(1).upper() if m_b0 else raw_val
            if ASIN_B0_RE.match(val):
                if not verify_cdn:
                    if not force_refresh:
                        return val, 'calibre_metadata_b0'
                else:
                    is_live, _sz = self.verify_asin_live_cover(val, short_title, primary_author)
                    if is_live:
                        self.store_b0_cache(cache_key, val, verified=True)
                        return val, 'verified_existing_b0'
                    if cdn_only_existing_b0 is None and not force_refresh:
                        cdn_ok, cdn_sz = self.verify_asin_live_cover(val, '', '')
                        if cdn_ok:
                            cdn_only_existing_b0 = val
                            cdn_only_existing_sz = cdn_sz

        # Step 2: Check persistent local cache
        if not force_refresh:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached:
                c_val = str(cached.get('asin') or '').strip().upper()
                if ASIN_B0_RE.match(c_val):
                    if not verify_cdn:
                        return c_val, 'local_cache_b0'
                    is_live, _sz = self.verify_asin_live_cover(c_val, short_title, primary_author)
                    if is_live:
                        return c_val, 'local_cache_verified_b0'

        # Step 3: Multi-stage B0... discovery
        ol_isbn10 = None
        if use_api:
            stages = [
                ('goodreads_b0',      lambda: self._candidates_goodreads_kindle(title_variants, primary_author, isbn)),
                ('amazon_digital_b0', lambda: self._candidates_amazon_digital(title_variants, primary_author)),
                ('duckduckgo_b0',     lambda: self._candidates_duckduckgo(title_variants, primary_author)),
            ]
            tested: set[str] = set()
            for stage_name, fn in stages:
                match = self._select_verified_candidate(
                    fn(), tested, cache_key, short_title, primary_author, stage_name, verify_cdn
                )
                if match:
                    return match

            ol_cands, ol_isbn10 = self._candidates_openlibrary(title_variants, primary_author, isbn)
            match = self._select_verified_candidate(
                ol_cands, tested, cache_key, short_title, primary_author, 'openlibrary_b0', verify_cdn
            )
            if match:
                return match

        if cdn_only_existing_b0:
            short_t, prim_a = self._normalize_identity(short_title, primary_author)
            with self._lock:
                self._verified_asins[cdn_only_existing_b0] = {
                    'short_title': short_t,
                    'primary_author': prim_a,
                    'size': cdn_only_existing_sz,
                    'ts': time.time(),
                }
            self.store_b0_cache(cache_key, cdn_only_existing_b0, verified=True)
            return cdn_only_existing_b0, 'verified_existing_b0'

        # Step 4: Optional Print ISBN-10 fallback
        if use_isbn10_fallback:
            for key in ('isbn',) + AMAZON_IDENTIFIER_KEYS:
                val = str(identifiers.get(key) or '').strip()
                c10 = self.isbn13_to_isbn10(val)
                if c10:
                    return c10, 'fallback_isbn10'
            if isbn:
                c10 = self.isbn13_to_isbn10(isbn)
                if c10:
                    return c10, 'fallback_isbn10'
            if ol_isbn10:
                return ol_isbn10, 'openlibrary_isbn10'

        return None, 'unmatched'

    def store_b0_cache(self, key: str, b0_asin: str, verified: bool = True) -> None:
        """Persist a resolved B0... ASIN under the given query key."""
        if b0_asin and ASIN_B0_RE.match(b0_asin):
            with self._lock:
                self._cache[key] = {
                    'asin': b0_asin.upper(),
                    'verified': bool(verified),
                    'ts': time.time()
                }
                self._save()
