# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
from typing import Any

try:
    from calibre_plugins.true_covers_kindle.core import atomic_json_load
except ImportError:
    from core import atomic_json_load

try:
    load_translations()
except NameError:
    _ = lambda x: x

try:
    from qt.core import (
        QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QCheckBox,
        QLabel, QLineEdit, QPushButton, QSpinBox, QComboBox,
        QFileDialog, QDialog, QTimer
    )
except ImportError:
    try:
        from PyQt5.Qt import (
            QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QCheckBox,
            QLabel, QLineEdit, QPushButton, QSpinBox, QComboBox,
            QFileDialog, QDialog, QTimer
        )
    except ImportError:
        QWidget = QDialog = object
        QVBoxLayout = QHBoxLayout = QGroupBox = QCheckBox = None
        QLabel = QLineEdit = QPushButton = QSpinBox = QComboBox = None
        QFileDialog = QTimer = None

PREFS_NAMESPACE        = 'plugins/true_covers_kindle'
LEGACY_PREFS_FILENAMES = ('Stoker.json', 'stoker.json')

POLICY_ASK_TIMEOUT       = 'ask_timeout'
POLICY_AUTO_PERSONAL_DOC = 'auto_personal_doc'
POLICY_MARK_ONLY         = 'mark_only'

DEFAULT_WATCH_EXTENSIONS = 'epub, azw3, mobi, pdf, cbz, cbr'
MIN_POLL_INTERVAL_SEC    = 10
MAX_POLL_INTERVAL_SEC    = 3600
DEFAULT_POLL_INTERVAL    = 45
MIN_SETTLE_SEC           = 0
MAX_SETTLE_SEC           = 600
DEFAULT_SETTLE_SEC       = 10
MIN_IDLE_TIMEOUT_SEC     = 5
MAX_IDLE_TIMEOUT_SEC     = 600
DEFAULT_IDLE_TIMEOUT_SEC = 60
COUNTDOWN_TICK_MS        = 1000

DEFAULT_SETTINGS = {
    # Auto-Import (Watch Folder)
    'watch_folder_enabled':          False,
    'watch_folder_path':             '',
    'watch_interval_sec':            DEFAULT_POLL_INTERVAL,
    'watch_settle_sec':              DEFAULT_SETTLE_SEC,
    'watch_extensions':              DEFAULT_WATCH_EXTENSIONS,
    'auto_import_handoff_fix_cover': True,
    # Fix Kindle Cover (EBOK Pipeline)
    'auto_run_on_add':               True,
    'skip_already_fixed':            True,
    'verify_live_cover_cdn':         True,
    'auto_convert_azw3':             True,
    'mark_missing_azw3':             True,
    'embed_cover_metadata':          True,
    'delete_original_format':        True,
    'lookup_asin_api':               True,
    'fallback_to_isbn10':            False,
    'patch_exth_headers':            True,
    # Mark as Personal Document (PDOC Fallback & Unattended Policy)
    'unmatched_handoff_policy':      POLICY_ASK_TIMEOUT,
    'idle_timeout_sec':              DEFAULT_IDLE_TIMEOUT_SEC,
    'mark_unmatched_books':          True,
    'show_summary_dialog':           True,
    'context_menu_initialized':      False,
    'schema_v60_migrated':           False,
    'schema_v61_migrated':           False,
}

LEGACY_KEY_MAP = {
    'ignite_handoff_stoke': 'auto_import_handoff_fix_cover',
}

LEGACY_POLICY_VALUE_MAP = {
    'auto_bank': POLICY_AUTO_PERSONAL_DOC,
}


def migrate_legacy_preferences(prefs: Any) -> bool:
    """One-time non-destructive migration from legacy Stoker preferences if the new config file does not exist yet."""
    file_path = getattr(prefs, 'file_path', '') or ''
    if file_path and os.path.exists(file_path):
        # Normalize any legacy policy value if already copied
        if prefs.get('unmatched_handoff_policy') in LEGACY_POLICY_VALUE_MAP:
            prefs['unmatched_handoff_policy'] = LEGACY_POLICY_VALUE_MAP[prefs['unmatched_handoff_policy']]
        return False

    if not file_path:
        return False

    plugins_dir = os.path.dirname(file_path)
    for legacy_fname in LEGACY_PREFS_FILENAMES:
        legacy_path = os.path.join(plugins_dir, legacy_fname)
        if not os.path.exists(legacy_path):
            continue
        legacy_data = atomic_json_load(legacy_path, default_factory=dict)
        if not isinstance(legacy_data, dict) or not legacy_data:
            continue

        migrated = {}
        for key, val in legacy_data.items():
            target_key = LEGACY_KEY_MAP.get(key, key)
            if target_key not in DEFAULT_SETTINGS:
                continue
            if target_key == 'unmatched_handoff_policy' and val in LEGACY_POLICY_VALUE_MAP:
                val = LEGACY_POLICY_VALUE_MAP[val]
            migrated[target_key] = val

        for target_key, val in migrated.items():
            prefs[target_key] = val
        return True

    return False


try:
    from calibre.utils.config import JSONConfig
    PREFS = JSONConfig(PREFS_NAMESPACE)
    for _k, _v in DEFAULT_SETTINGS.items():
        PREFS.defaults[_k] = _v

    migrate_legacy_preferences(PREFS)

    if not PREFS['schema_v60_migrated']:
        PREFS['fallback_to_isbn10']    = False
        PREFS['auto_convert_azw3']     = True
        PREFS['mark_missing_azw3']     = True
        PREFS['verify_live_cover_cdn'] = True
        PREFS['schema_v60_migrated']   = True

    if not PREFS['schema_v61_migrated']:
        PREFS['show_summary_dialog'] = True
        PREFS['schema_v61_migrated'] = True
except ImportError:
    PREFS = dict(DEFAULT_SETTINGS)


def select_folder_dialog(parent, current_path: str = '') -> str:
    """Open a directory chooser dialog for selecting the Auto-Import watch folder."""
    start_dir = current_path if (current_path and os.path.isdir(current_path)) else os.path.expanduser('~')
    return QFileDialog.getExistingDirectory(
        parent,
        _('Select Watch / Download Folder for Auto-Import (Non-Destructive)'),
        start_dir
    )


class CountdownConfirmDialog(QDialog):
    """Confirmation dialog with an automatic idle countdown timer for unmatched books."""

    def __init__(
        self,
        parent,
        title: str,
        message: str,
        timeout_sec: int = DEFAULT_IDLE_TIMEOUT_SEC,
        default_accept: bool = True
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.remaining = max(MIN_IDLE_TIMEOUT_SEC, int(timeout_sec))
        self.default_accept = default_accept

        layout = QVBoxLayout(self)
        self.lbl_msg = QLabel(message, self)
        self.lbl_msg.setWordWrap(True)
        layout.addWidget(self.lbl_msg)

        self.lbl_timer = QLabel('', self)
        layout.addWidget(self.lbl_timer)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_yes = QPushButton('', self)
        self.btn_yes.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_yes)

        self.btn_no = QPushButton(_('Skip (Keep Marked as no_asin)'), self)
        self.btn_no.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_no)

        layout.addLayout(btn_row)
        self._update_labels()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(COUNTDOWN_TICK_MS)

    def _update_labels(self) -> None:
        if self.default_accept:
            self.btn_yes.setText(_('Mark as Personal Document (PDOC) [{0}s]').format(self.remaining))
            self.lbl_timer.setText(
                _('Idle auto-confirm: Will automatically run Mark as Personal Document (PDOC) in {0} seconds.').format(
                    self.remaining
                )
            )
        else:
            self.btn_yes.setText(_('Mark as Personal Document (PDOC)'))
            self.btn_no.setText(_('Skip (Keep Marked) [{0}s]').format(self.remaining))
            self.lbl_timer.setText(
                _('Idle auto-confirm: Will automatically skip and keep marked in {0} seconds.').format(
                    self.remaining
                )
            )

    def _on_tick(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self.timer.stop()
            if self.default_accept:
                self.accept()
            else:
                self.reject()
            return
        self._update_labels()


class ConfigWidget(QWidget):
    """Configuration widget bound to True Covers for Kindle preferences."""

    def __init__(self, plugin_action=None):
        super().__init__()
        self.plugin_action = plugin_action
        self._bound_checkboxes = {}
        self._build_ui()

    def _add_checkbox(self, layout, pref_key: str, label_text: str, tooltip: str = None):
        cb = QCheckBox(_(label_text), self)
        cb.setChecked(bool(PREFS[pref_key]))
        if tooltip:
            cb.setToolTip(_(tooltip))
        layout.addWidget(cb)
        self._bound_checkboxes[pref_key] = cb
        return cb

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Auto-Import ---
        g_auto_import = QGroupBox(
            _('Auto-Import: Non-Destructive Watch Folder (Leaves Original Files Untouched)'),
            self
        )
        v_auto_import = QVBoxLayout(g_auto_import)

        self._add_checkbox(
            v_auto_import,
            'watch_folder_enabled',
            'Enable Auto-Import background folder monitoring (reads finished files in read-only mode)',
            'Periodically scans your configured folder and imports new books into calibre without moving or modifying the original files.'
        )
        self._add_checkbox(
            v_auto_import,
            'auto_import_handoff_fix_cover',
            'Automatically run Fix Kindle Cover on newly imported books',
            'Immediately runs Fix Kindle Cover on books added by Auto-Import so their Kindle covers are ready.'
        )

        h_folder = QHBoxLayout()
        h_folder.addWidget(QLabel(_('Watch Folder:'), self))
        self.le_folder = QLineEdit(self)
        self.le_folder.setPlaceholderText(_('Select your download or watch folder...'))
        self.le_folder.setText(PREFS['watch_folder_path'] or '')
        h_folder.addWidget(self.le_folder)
        btn_browse = QPushButton(_('Browse...'), self)
        btn_browse.clicked.connect(self._on_browse)
        h_folder.addWidget(btn_browse)
        v_auto_import.addLayout(h_folder)

        h_opts = QHBoxLayout()
        h_opts.addWidget(QLabel(_('Poll interval (sec):'), self))
        self.sb_interval = QSpinBox(self)
        self.sb_interval.setRange(MIN_POLL_INTERVAL_SEC, MAX_POLL_INTERVAL_SEC)
        self.sb_interval.setValue(int(PREFS['watch_interval_sec'] or DEFAULT_POLL_INTERVAL))
        self.sb_interval.setToolTip(_('How often (in seconds) Auto-Import checks the watch folder for new files.'))
        h_opts.addWidget(self.sb_interval)

        h_opts.addWidget(QLabel(_('File settle age (sec):'), self))
        self.sb_settle = QSpinBox(self)
        self.sb_settle.setRange(MIN_SETTLE_SEC, MAX_SETTLE_SEC)
        self.sb_settle.setToolTip(_('Skips files modified within the last N seconds so active downloads finish writing first.'))
        self.sb_settle.setValue(
            int(PREFS['watch_settle_sec'] if PREFS['watch_settle_sec'] is not None else DEFAULT_SETTLE_SEC)
        )
        h_opts.addWidget(self.sb_settle)

        h_opts.addWidget(QLabel(_('Extensions:'), self))
        self.le_exts = QLineEdit(self)
        self.le_exts.setText(PREFS['watch_extensions'] or DEFAULT_WATCH_EXTENSIONS)
        self.le_exts.setToolTip(_('Comma-separated list of ebook file extensions for Auto-Import to scan.'))
        h_opts.addWidget(self.le_exts)
        v_auto_import.addLayout(h_opts)
        layout.addWidget(g_auto_import)

        # --- Fix Kindle Cover ---
        g_fix_cover = QGroupBox(
            _('Fix Kindle Cover: Kindle Edition ASIN Lookup & Cover Embedding (EBOK)'),
            self
        )
        v_fix_cover = QVBoxLayout(g_fix_cover)

        fix_cover_options = (
            (
                'auto_run_on_add',
                'Automatically run Fix Kindle Cover when books are added to calibre',
                'Runs Fix Kindle Cover in the background whenever a new EPUB, MOBI, or AZW3 book is added to your library.'
            ),
            (
                'skip_already_fixed',
                'Smart Skip: Quickly inspect .azw3 files (<2ms) and skip books that already have a fixed cover or personal document tag',
                'Avoids repeating network lookups on books whose AZW3 file already has a verified Kindle ASIN (Amazon\'s ID for the Kindle edition) or Personal Document header.'
            ),
            (
                'verify_live_cover_cdn',
                'Verify the ASIN has a live cover on Amazon\'s server and matches title/author (rejects defunct pages and study guides)',
                'Checks that Amazon\'s cover server actually serves a valid cover image for the ASIN (Amazon\'s ID for the Kindle edition) so the Kindle will not show a blank cover.'
            ),
            (
                'auto_convert_azw3',
                'Automatically convert EPUB/MOBI to AZW3 if AZW3 is missing (keeps original EPUB)',
                'Kindle cover headers require an AZW3 (KF8) file. Converts reflowable formats to AZW3 when needed while keeping your original format.'
            ),
            (
                'mark_missing_azw3',
                'Highlight books still missing an AZW3 format after Fix Kindle Cover (search "marked:no_azw3")',
                'Marks books that could not be converted to AZW3 so you can find them with "marked:no_azw3" in the calibre search bar.'
            ),
            (
                'embed_cover_metadata',
                'Embed calibre cover and metadata into the AZW3 file in-place (writes EXTH 201 cover offset)',
                'Writes the calibre library cover image directly into the AZW3 file container.'
            ),
            (
                'delete_original_format',
                'Remove temporary ORIGINAL_EPUB / ORIGINAL_AZW3 backups created during metadata embedding',
                'Cleans up redundant ORIGINAL_* backup files calibre creates when embedding metadata.'
            ),
            (
                'lookup_asin_api',
                'Look up the Kindle ASIN (Amazon\'s ID for the Kindle edition, starting with B0...) via Goodreads, Amazon, DuckDuckGo, and OpenLibrary',
                'Queries online catalogs to find the 10-character B0... ASIN (Amazon\'s ID for the Kindle edition) when the book does not already have one.'
            ),
            (
                'patch_exth_headers',
                'Stamp AZW3 binary headers (EXTH 113 and 504 with the ASIN, and EXTH 501=EBOK) across all MOBI/KF8 headers',
                'Writes Amazon\'s ID for the Kindle edition (ASIN) and the EBOK book flag into the AZW3 file header so the Kindle displays the cover.'
            ),
            (
                'fallback_to_isbn10',
                'Allow print ISBN-10 fallback during Fix Kindle Cover (not recommended: print ISBNs often fail when a Kindle B0... edition exists)',
                'Uses a 10-digit print ISBN if no B0... Kindle ASIN (Amazon\'s ID for the Kindle edition) is found. Leave disabled unless needed.'
            ),
        )
        for key, text, tip in fix_cover_options:
            self._add_checkbox(v_fix_cover, key, text, tip)
        layout.addWidget(g_fix_cover)

        # --- Mark as Personal Document ---
        g_personal_doc = QGroupBox(
            _('Mark as Personal Document: Fallback for Books Without a Kindle Edition (PDOC)'),
            self
        )
        v_personal_doc = QVBoxLayout(g_personal_doc)

        h_pol = QHBoxLayout()
        h_pol.addWidget(QLabel(_('When Fix Kindle Cover cannot find a Kindle edition ASIN:'), self))
        self.cmb_policy = QComboBox(self)
        self.cmb_policy.setToolTip(
            _('Choose how to handle books, comics, or documents that have no Kindle edition ASIN (Amazon\'s ID for the Kindle edition) on Amazon.')
        )
        self.cmb_policy.addItem(
            _('Ask to run Mark as Personal Document (PDOC) — Auto-Confirm after Idle Timeout'),
            POLICY_ASK_TIMEOUT
        )
        self.cmb_policy.addItem(
            _('No-Confirm: Automatically run Mark as Personal Document (PDOC) with Embedded Cover'),
            POLICY_AUTO_PERSONAL_DOC
        )
        self.cmb_policy.addItem(
            _('No-Confirm: Do Not Convert to Personal Document — Only Highlight with "marked:no_asin"'),
            POLICY_MARK_ONLY
        )
        cur_pol = PREFS['unmatched_handoff_policy'] or POLICY_ASK_TIMEOUT
        cur_pol = LEGACY_POLICY_VALUE_MAP.get(cur_pol, cur_pol)
        idx = self.cmb_policy.findData(cur_pol)
        self.cmb_policy.setCurrentIndex(idx if idx >= 0 else 0)
        h_pol.addWidget(self.cmb_policy, 1)
        v_personal_doc.addLayout(h_pol)

        h_idle = QHBoxLayout()
        h_idle.addWidget(QLabel(_('Idle auto-confirm timeout (sec):'), self))
        self.sb_timeout = QSpinBox(self)
        self.sb_timeout.setRange(MIN_IDLE_TIMEOUT_SEC, MAX_IDLE_TIMEOUT_SEC)
        self.sb_timeout.setValue(int(PREFS['idle_timeout_sec'] or DEFAULT_IDLE_TIMEOUT_SEC))
        self.sb_timeout.setToolTip(_('Seconds to wait before automatically confirming the Mark as Personal Document prompt.'))
        h_idle.addWidget(self.sb_timeout)

        cb_mark_unmatched = self._add_checkbox(
            h_idle,
            'mark_unmatched_books',
            'Always highlight books still missing a Kindle ASIN with "marked:no_asin"',
            'Highlights books without a Kindle ASIN (Amazon\'s ID for the Kindle edition) that were not marked as Personal Documents.'
        )
        h_idle.setStretchFactor(cb_mark_unmatched, 1)
        v_personal_doc.addLayout(h_idle)

        self._add_checkbox(
            v_personal_doc,
            'show_summary_dialog',
            'Show completion summary dialog after manual runs even when all books succeed',
            'Displays a summary of succeeded and failed books when a manual Fix Kindle Cover or Mark as Personal Document job finishes.'
        )
        layout.addWidget(g_personal_doc)
        layout.addStretch(1)

    def _on_browse(self) -> None:
        chosen = select_folder_dialog(self, self.le_folder.text().strip())
        if chosen:
            self.le_folder.setText(chosen)

    def save_settings(self) -> None:
        """Persist current UI control states into PREFS."""
        for key, cb in self._bound_checkboxes.items():
            PREFS[key] = cb.isChecked()
        PREFS['watch_folder_path']        = self.le_folder.text().strip()
        PREFS['watch_interval_sec']       = int(self.sb_interval.value())
        PREFS['watch_settle_sec']         = int(self.sb_settle.value())
        PREFS['watch_extensions']         = self.le_exts.text().strip()
        PREFS['unmatched_handoff_policy'] = self.cmb_policy.currentData()
        PREFS['idle_timeout_sec']         = int(self.sb_timeout.value())
