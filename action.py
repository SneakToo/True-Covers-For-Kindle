# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

import os
import re
import threading
from collections import deque

from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import info_dialog, error_dialog, question_dialog, Dispatcher, gprefs
from calibre.gui2.threaded_jobs import ThreadedJob
from calibre.utils.config import config_dir

try:
    load_translations()
except NameError:
    _ = lambda x: x

try:
    from qt.core import QMenu, QTimer, pyqtSignal, QInputDialog, QDialog, QPixmap, QIcon, Qt
except ImportError:
    from PyQt5.Qt import QMenu, QTimer, pyqtSignal, QInputDialog, QDialog, QPixmap, QIcon, Qt

from calibre_plugins.true_covers_kindle.config import (
    PREFS,
    POLICY_ASK_TIMEOUT,
    POLICY_AUTO_PERSONAL_DOC,
    DEFAULT_WATCH_EXTENSIONS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SETTLE_SEC,
    DEFAULT_IDLE_TIMEOUT_SEC,
    MIN_POLL_INTERVAL_SEC,
    MIN_IDLE_TIMEOUT_SEC,
    select_folder_dialog,
    CountdownConfirmDialog,
)
from calibre_plugins.true_covers_kindle.core import (
    KINDLE_VALID_ID_RE, ASIN_B0_RE, EXTRACT_B0_RE,
    ExthBinaryCodec, AsinResolver, migrate_legacy_json_file
)
from calibre_plugins.true_covers_kindle.auto_import import AutoImportEngine
from calibre_plugins.true_covers_kindle.pipelines import (
    STATUS_FIXED,
    STATUS_UNMATCHED,
    STATUS_NO_AZW3,
    STATUS_ERROR,
    PDOC_STATUS_NO_FORMAT,
    FixKindleCoverPipeline,
    PersonalDocumentPipeline,
    book_has_cover,
    get_book_formats,
    is_book_stamped_and_baked,
)

ASIN_CACHE_FILENAME         = 'true_covers_kindle_asin_cache.json'
LEGACY_ASIN_CACHE_FILENAME  = 'Stoker_ASIN_Cache.json'
WATCH_STATE_FILENAME        = 'true_covers_kindle_watch_state.json'
LEGACY_WATCH_STATE_FILENAME = 'Stoker_Watch_State.json'

MARK_NO_ASIN          = 'no_asin'
MARK_NO_AZW3          = 'no_azw3'
MARK_NEEDS_COVER_FIX  = 'needs_cover_fix'
# Legacy session mark name included when clearing marks on processed books
LEGACY_MARK_NEEDS_FIX = 'needs_stoker'
CLEARABLE_MARKS       = (MARK_NO_ASIN, MARK_NO_AZW3, MARK_NEEDS_COVER_FIX, LEGACY_MARK_NEEDS_FIX)

ICON_MAIN_PATH         = 'images/icon.png'
ICON_AUTO_IMPORT_PATH  = 'images/auto_import.png'
ICON_PERSONAL_DOC_PATH = 'images/personal_document.png'
ICON_SIZES             = (16, 24, 32, 64, 128)

LARGE_SELECTION_THRESHOLD        = 25
AUDIT_LOG_MAX_ENTRIES            = 500
# Wait briefly after calibre fires format_added so calibre finishes any queued import/conversion work first
FORMAT_ADDED_CONVERTING_DELAY_MS = 6000
FORMAT_ADDED_DEFAULT_DELAY_MS    = 1800
AUTO_RUN_ADDED_FORMATS           = ('EPUB', 'AZW3', 'MOBI')

LEGACY_PLUGIN_ACTION_NAME = 'Stoker'
TOOLBAR_LAYOUT_KEYS = (
    'action-layout-toolbar',
    'action-layout-toolbar-device',
)
CONTEXT_MENU_LAYOUT_KEYS = (
    'action-layout-context-menu',
    'action-layout-context-menu-split',
    'action-layout-context-menu-cover-browser',
)
ALL_ACTION_LAYOUT_KEYS = (
    'action-layout-toolbar',
    'action-layout-toolbar-device',
    'action-layout-toolbar-child',
    'action-layout-menubar',
    'action-layout-menubar-device',
    'action-layout-searchbar',
    'action-layout-context-menu',
    'action-layout-context-menu-split',
    'action-layout-context-menu-device',
    'action-layout-context-menu-cover-browser',
)


class TrueCoversKindleAction(InterfaceAction):
    """Main calibre GUI interface action for True Covers for Kindle."""

    name = 'True Covers for Kindle'
    action_spec = (
        _('True Covers for Kindle'),
        ICON_MAIN_PATH,
        _('True Covers for Kindle: Fix Kindle Cover, Auto-Import, and Mark as Personal Document'),
        None
    )
    action_type = 'current'
    popup_type = 1
    action_add_menu = True

    _book_added_sig       = pyqtSignal(int, str)
    _auto_import_done_sig = pyqtSignal(list, list, int, bool)
    _pipeline_ui_done_sig = pyqtSignal(list, dict, list, str, str, bool, bool)
    _manual_asin_done_sig = pyqtSignal(int, str, str, bool, bool, int)
    _audit_done_sig       = pyqtSignal(int, int, int, dict)

    def genesis(self):
        """Initialize plugin state, migrate legacy files, load icons, and build menus."""
        self._in_progress_books = set()
        self._auto_import_running = False
        self._lock = threading.Lock()
        self._shutting_down = False
        self._is_library_selected = True
        self._context_menus_hooked = False
        # Bounded ring buffer so long-running calibre sessions never leak memory
        self._audit_history = deque(maxlen=AUDIT_LOG_MAX_ENTRIES)

        self._ensure_toolbar_and_context_menu_installed()

        asin_cache_path = os.path.join(config_dir, ASIN_CACHE_FILENAME)
        migrate_legacy_json_file(
            asin_cache_path,
            os.path.join(config_dir, LEGACY_ASIN_CACHE_FILENAME)
        )
        watch_state_path = os.path.join(config_dir, WATCH_STATE_FILENAME)
        migrate_legacy_json_file(
            watch_state_path,
            os.path.join(config_dir, LEGACY_WATCH_STATE_FILENAME)
        )

        self.asin_resolver = AsinResolver(asin_cache_path)
        self.auto_import_engine = AutoImportEngine(watch_state_path)
        self.fix_cover_pipeline = FixKindleCoverPipeline(self.asin_resolver)
        self.personal_doc_pipeline = PersonalDocumentPipeline()

        self._book_added_sig.connect(self._on_book_format_added_main_thread)
        self._auto_import_done_sig.connect(self._on_auto_import_done_main_thread)
        self._pipeline_ui_done_sig.connect(self._on_pipeline_ui_done_main_thread)
        self._manual_asin_done_sig.connect(self._on_manual_asin_done_main_thread)
        self._audit_done_sig.connect(self._on_audit_done_main_thread)

        self._plugin_icons = self._load_plugin_icons()
        if ICON_MAIN_PATH in self._plugin_icons:
            self.qaction.setIcon(self._plugin_icons[ICON_MAIN_PATH])

        self.menu = self.qaction.menu()
        self.context_submenu = QMenu(_('True Covers for Kindle'), self.gui)
        if ICON_MAIN_PATH in self._plugin_icons:
            self.context_submenu.setIcon(self._plugin_icons[ICON_MAIN_PATH])
            self.context_submenu.menuAction().setIcon(self._plugin_icons[ICON_MAIN_PATH])

        self._build_menu()
        self.menu.aboutToShow.connect(self._update_action_enabled_states)
        self.context_submenu.aboutToShow.connect(self._update_action_enabled_states)
        self.qaction.triggered.connect(self.fix_selected_covers_smart)

        self.watch_timer = QTimer(self.gui)
        self.watch_timer.timeout.connect(self._on_watch_timer_tick)

    def _ensure_toolbar_and_context_menu_installed(self):
        """Migrate legacy 'Stoker' action layout entries and register in toolbar and book context menu on first install."""
        try:
            for key in ALL_ACTION_LAYOUT_KEYS:
                current = list(gprefs.get(key) or ())
                if LEGACY_PLUGIN_ACTION_NAME in current:
                    updated = []
                    for item in current:
                        if item == LEGACY_PLUGIN_ACTION_NAME:
                            if self.name not in updated and self.name not in current:
                                updated.append(self.name)
                        else:
                            updated.append(item)
                    gprefs[key] = tuple(updated)

            if not PREFS['context_menu_initialized']:
                for key in TOOLBAR_LAYOUT_KEYS + CONTEXT_MENU_LAYOUT_KEYS:
                    current = list(gprefs.get(key) or ())
                    if self.name not in current:
                        current.append(self.name)
                        gprefs[key] = tuple(current)
                PREFS['context_menu_initialized'] = True
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not register toolbar/context-menu layout: {err}')

    def _load_plugin_icons(self):
        """Load custom plugin PNG icons from the plugin zip and populate multi-size QIcon pixmaps."""
        icons = {}
        paths = [ICON_MAIN_PATH, ICON_AUTO_IMPORT_PATH, ICON_PERSONAL_DOC_PATH]
        try:
            res = self.load_resources(paths)
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not load plugin icon resources: {err}')
            res = {}

        smooth_mode = getattr(
            getattr(Qt, 'TransformationMode', Qt),
            'SmoothTransformation',
            1
        )
        aspect_mode = getattr(
            getattr(Qt, 'AspectRatioMode', Qt),
            'KeepAspectRatio',
            1
        )

        for path in paths:
            raw_bytes = res.get(path)
            if not raw_bytes:
                continue
            base_pm = QPixmap()
            if not base_pm.loadFromData(raw_bytes) or base_pm.isNull():
                continue
            icon = QIcon()
            for sz in ICON_SIZES:
                scaled_pm = base_pm.scaled(sz, sz, aspect_mode, smooth_mode)
                icon.addPixmap(scaled_pm)
            icons[path] = icon
        return icons

    def _icon_or_fallback(self, resource_path, fallback_name):
        return self._plugin_icons.get(resource_path, fallback_name)

    def _build_menu(self):
        """Build the True Covers for Kindle toolbar dropdown menu and library right-click submenu."""
        self.menu.clear()
        self.context_submenu.clear()
        self._library_only_actions = []
        self._selection_only_actions = []

        fix_cover_icon = self._icon_or_fallback(ICON_MAIN_PATH, 'ok.png')
        personal_doc_icon = self._icon_or_fallback(ICON_PERSONAL_DOC_PATH, 'book.png')
        auto_import_icon = self._icon_or_fallback(ICON_AUTO_IMPORT_PATH, 'folder_saved_search.png')

        menu_schema = [
            # (attr_name, unique_id, text, icon, tooltip, callback, enable_scope)
            # enable_scope: 'selection' (requires >=1 selected book in library),
            #               'library'   (requires library view),
            #               'always'    (always enabled)
            (
                'act_fix_selected',
                'true_covers_kindle_fix_selected',
                'Fix Kindle Cover',
                fix_cover_icon,
                'Finds the Kindle edition ASIN (Amazon\'s ID for the Kindle edition), embeds the cover into AZW3, and stamps EBOK headers',
                self.fix_selected_covers_smart,
                'selection'
            ),
            (
                'act_force_fix_selected',
                'true_covers_kindle_force_fix_selected',
                'Force Fix Kindle Cover for Selected Books (Bypass Cache & Overwrite)',
                'view-refresh.png',
                'Bypasses cached results, re-queries online sources for the Kindle ASIN (Amazon\'s ID for the Kindle edition), and overwrites AZW3 cover headers',
                self.fix_selected_covers_force,
                'selection'
            ),
            (
                'act_fix_entire_library',
                'true_covers_kindle_fix_entire_library',
                'Scan & Fix Kindle Covers in Entire Library',
                'library.png',
                'Scans every book in your library; skips books that already have a fixed cover or Personal Document header and fixes the rest',
                self.fix_entire_library_smart,
                'library'
            ),
            None,
            (
                'act_mark_personal_doc',
                'true_covers_kindle_mark_selected_personal_doc',
                'Mark as Personal Document',
                personal_doc_icon,
                'For books with no Kindle edition on Amazon: converts to AZW3 if needed, embeds the calibre cover, and marks as a Personal Document (PDOC)',
                self.mark_selected_as_personal_document,
                'selection'
            ),
            (
                'act_manual_asin',
                'true_covers_kindle_set_manual_asin',
                'Set Kindle ASIN (or Paste Amazon URL) for Selected Book...',
                'edit_input.png',
                'Manually enter an ASIN (Amazon\'s 10-character ID for the Kindle edition, starting with B0...) or paste an Amazon book link, then embed the cover',
                self.prompt_manual_asin_for_selected,
                'selection'
            ),
            None,
            (
                'act_auto_import_now',
                'true_covers_kindle_auto_import_now',
                'Run Auto-Import Now (Import New Files & Fix Kindle Covers)',
                auto_import_icon,
                'Imports new ebook files from your watched folder non-destructively (leaving original files untouched) and runs Fix Kindle Cover on them',
                self.run_auto_import_now,
                'always'
            ),
            (
                'act_set_watch_folder',
                'true_covers_kindle_set_watch_folder',
                'Set Auto-Import Watch Folder...',
                'document-open.png',
                'Choose the download or watch folder for Auto-Import to monitor non-destructively',
                self.prompt_set_watch_folder,
                'always'
            ),
            None,
            (
                'act_inspect_book',
                'true_covers_kindle_inspect_selected_binary',
                'Inspect Selected Book AZW3 Cover & ASIN Headers...',
                'debug.png',
                'Displays the on-disk AZW3 EXTH binary header records (embedded cover offset, ASIN — Amazon\'s ID for the Kindle edition, and EBOK/PDOC status)',
                self.inspect_selected_book_header,
                'selection'
            ),
            (
                'act_audit_lib',
                'true_covers_kindle_audit_entire_library',
                'Audit Library Only (Highlight Books Needing Cover Fix)',
                'search.png',
                'Read-only binary check across your library; highlights books missing a fixed Kindle cover with marked:needs_cover_fix',
                self.audit_entire_library,
                'library'
            ),
            (
                'act_clear_cache',
                'true_covers_kindle_clear_asin_cache',
                'Clear Cached ASINs',
                'trash.png',
                'Clears cached ASINs (Amazon\'s IDs for Kindle editions) so the next run performs fresh online lookups',
                self.clear_asin_cache_action,
                'always'
            ),
            (
                'act_view_log',
                'true_covers_kindle_view_audit_log',
                'View Session Log...',
                'dialog_information.png',
                'View the detailed log of Auto-Import, Fix Kindle Cover, and Mark as Personal Document operations in this calibre session',
                self.show_audit_log_dialog,
                'always'
            ),
            (
                'act_config',
                'true_covers_kindle_configure_plugin',
                'Configure True Covers for Kindle...',
                'config.png',
                'Configure Auto-Import watch folder, Fix Kindle Cover ASIN lookup, and Mark as Personal Document fallback settings',
                self.open_config,
                'always'
            ),
        ]

        for entry in menu_schema:
            if entry is None:
                self.menu.addSeparator()
                continue
            attr, uid, text, icon, desc, cb, scope = entry
            act = self.create_menu_action(
                self.menu, unique_name=uid, text=_(text),
                icon=icon, description=_(desc), triggered=cb
            )
            setattr(self, attr, act)
            if scope == 'selection':
                self._selection_only_actions.append(act)
            elif scope == 'library':
                self._library_only_actions.append(act)

        # Populate the right-click context submenu with the shared Fix Kindle Cover and Mark as Personal Document actions
        self.context_submenu.addAction(self.act_fix_selected)
        self.context_submenu.addAction(self.act_mark_personal_doc)

    # --- Calibre InterfaceAction Lifecycle & Context Menu Hooks ---

    def gui_layout_complete(self):
        self._hook_context_menu_rebuild()
        self._sync_library_context_menus()
        self._attach_selection_listener()
        self._update_action_enabled_states()

    def initialization_complete(self):
        self._hook_context_menu_rebuild()
        self._sync_library_context_menus()
        self._attach_selection_listener()
        self._attach_db_listener(self.gui.current_db)
        self._sync_missing_library_view_rows()
        self._update_action_enabled_states()
        self.apply_settings()

    def library_changed(self, db):
        self._attach_db_listener(db)
        self._attach_selection_listener()
        self._sync_missing_library_view_rows()
        self._update_action_enabled_states()

    def _sync_missing_library_view_rows(self):
        """Ensure any books added via db.new_api are registered in current_db.data and visible in library_view."""
        try:
            data_view = getattr(self.gui.current_db, 'data', None)
            if data_view is None or not hasattr(data_view, '_map'):
                return
            all_db_ids = set(self.gui.current_db.new_api.all_book_ids())
            known_view_ids = set(data_view._map)
            missing_ids = sorted(all_db_ids - known_view_ids)
            if missing_ids:
                data_view.books_added(missing_ids)
                self.gui.library_view.model().books_added(len(missing_ids))
                try:
                    self.gui.library_view.set_current_row(0)
                    self.gui.refresh_cover_browser()
                except Exception:
                    pass
                self.gui.tags_view.recount()
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not sync missing library view rows: {err}')

    def _refresh_library_rows_and_details(self, book_ids):
        """Refresh book_ids in the library model and force the Book Details sidebar to update immediately."""
        if not book_ids:
            return
        try:
            model = self.gui.library_view.model()
            current_idx = self.gui.library_view.currentIndex()
            current_row = current_idx.row() if current_idx.isValid() else -1
            model.refresh_ids(book_ids, current_row=current_row)
            if current_idx.isValid():
                model.current_changed(current_idx, current_idx)
            self.gui.refresh_cover_browser()
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not refresh book details view: {err}')
        try:
            self.gui.tags_view.recount()
        except Exception:
            pass

    def location_selected(self, loc):
        self._is_library_selected = (loc == 'library')
        self._update_action_enabled_states()

    def _hook_context_menu_rebuild(self):
        """Wrap gui.build_context_menus so changes in Preferences -> Toolbars & menus preserve our focused submenu."""
        if getattr(self, '_context_menus_hooked', False):
            return
        orig_build = getattr(self.gui, 'build_context_menus', None)
        if callable(orig_build):
            def _wrapped_build_context_menus(*args, **kwargs):
                res = orig_build(*args, **kwargs)
                self._sync_library_context_menus()
                return res
            self.gui.build_context_menus = _wrapped_build_context_menus
            self._context_menus_hooked = True

    def _sync_library_context_menus(self):
        """Ensure the library view's book context menu displays the focused 2-item True Covers for Kindle submenu."""
        try:
            hovered_slot = getattr(self.gui, 'context_menu_action_hovered', None)
            if callable(hovered_slot):
                try:
                    self.context_submenu.hovered.disconnect(hovered_slot)
                except (TypeError, RuntimeError):
                    pass
                self.context_submenu.hovered.connect(hovered_slot)

            candidate_menus = []
            lv = getattr(self.gui, 'library_view', None)
            if lv is not None:
                if getattr(lv, 'context_menu', None) is not None:
                    candidate_menus.append(lv.context_menu)
                pin_view = getattr(lv, 'pin_view', None)
                if pin_view is not None and getattr(pin_view, 'context_menu', None) is not None:
                    candidate_menus.append(pin_view.context_menu)
            cf = getattr(self.gui, 'cover_flow', None)
            if cf is not None and getattr(cf, 'context_menu', None) is not None:
                candidate_menus.append(cf.context_menu)

            sub_action = self.context_submenu.menuAction()
            for ctx_menu in candidate_menus:
                try:
                    ctx_menu.aboutToShow.disconnect(self._update_action_enabled_states)
                except (TypeError, RuntimeError):
                    pass
                ctx_menu.aboutToShow.connect(self._update_action_enabled_states)

                actions = list(ctx_menu.actions())
                if self.qaction in actions:
                    if sub_action not in actions:
                        ctx_menu.insertMenu(self.qaction, self.context_submenu)
                    ctx_menu.removeAction(self.qaction)
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not sync library context menu: {err}')

    def _attach_selection_listener(self):
        """Connect to the library view's selectionModel so action enabled states update immediately when selection changes."""
        try:
            lv = getattr(self.gui, 'library_view', None)
            if lv is None:
                return
            sel_model = lv.selectionModel()
            if sel_model is not None:
                try:
                    sel_model.selectionChanged.disconnect(self._update_action_enabled_states)
                except (TypeError, RuntimeError):
                    pass
                sel_model.selectionChanged.connect(self._update_action_enabled_states)
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not attach selection listener: {err}')

    def _has_selected_books(self):
        """Return True if at least one book is currently selected in the library view."""
        try:
            lv = getattr(self.gui, 'library_view', None)
            if lv is None:
                return False
            sel_model = lv.selectionModel()
            if sel_model is None:
                return False
            return bool(sel_model.selectedRows())
        except Exception:
            return False

    def _update_action_enabled_states(self, *args):
        """Enable or disable library-only and selection-dependent actions based on current library selection."""
        in_library = bool(getattr(self, '_is_library_selected', True))
        has_selection = in_library and self._has_selected_books()
        for act in getattr(self, '_library_only_actions', ()):
            act.setEnabled(in_library)
        for act in getattr(self, '_selection_only_actions', ()):
            act.setEnabled(has_selection)

    def shutting_down(self):
        """Stop background timers and mark plugin as shutting down."""
        self._shutting_down = True
        if hasattr(self, 'watch_timer') and self.watch_timer.isActive():
            self.watch_timer.stop()

    def _attach_db_listener(self, db):
        try:
            db.new_api.add_listener(self._db_event_listener, check_already_added=True)
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not attach DB listener: {err}')

    def apply_settings(self):
        """Start or stop the Auto-Import watch timer according to current preferences."""
        if getattr(self, '_shutting_down', False):
            return
        if PREFS['watch_folder_enabled'] and PREFS['watch_folder_path']:
            interval_ms = max(
                MIN_POLL_INTERVAL_SEC,
                int(PREFS['watch_interval_sec'] or DEFAULT_POLL_INTERVAL)
            ) * 1000
            self.watch_timer.start(interval_ms)
        else:
            self.watch_timer.stop()

    def open_config(self):
        """Open the plugin configuration dialog and apply updated settings."""
        self.interface_action_base_plugin.do_user_config(self.gui)
        self.apply_settings()

    def _log_msg(self, log, msg):
        self._audit_history.append(msg)
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    # --- Auto-Import (Background Non-Destructive Watch Folder) ---

    def prompt_set_watch_folder(self):
        """Prompt the user to choose a watch folder for Auto-Import and optionally run an initial scan."""
        chosen = select_folder_dialog(self.gui, PREFS['watch_folder_path'] or '')
        if chosen:
            PREFS['watch_folder_path'] = chosen
            PREFS['watch_folder_enabled'] = True
            self.apply_settings()
            if question_dialog(
                self.gui,
                _('True Covers for Kindle — Auto-Import Watch Folder Configured'),
                _(
                    'Auto-Import Watch Folder set to:\n{0}\n\n'
                    '• Original files in this folder are opened read-only and are NEVER deleted or moved.\n'
                    '• Newly imported books will automatically run through Fix Kindle Cover.\n\n'
                    'Would you like to run Auto-Import on this folder now?'
                ).format(chosen),
                default_yes=True
            ):
                self.run_auto_import_now()

    def run_auto_import_now(self):
        """Trigger an immediate manual Auto-Import scan on the configured watch folder."""
        self._launch_auto_import_job(manual=True)

    def _on_watch_timer_tick(self):
        self._launch_auto_import_job(manual=False)

    def _launch_auto_import_job(self, manual=False):
        if self._shutting_down:
            return
        folder = (PREFS['watch_folder_path'] or '').strip()
        if not folder or not os.path.isdir(folder):
            if manual:
                if question_dialog(
                    self.gui,
                    _('True Covers for Kindle — Auto-Import Watch Folder Not Set'),
                    _('No valid Auto-Import watch folder is configured yet.\n\nWould you like to choose a watch folder now?'),
                    default_yes=True
                ):
                    self.prompt_set_watch_folder()
            return

        with self._lock:
            if self._auto_import_running:
                return
            self._auto_import_running = True

        exts = {
            e.strip().lower().lstrip('.')
            for e in (PREFS['watch_extensions'] or DEFAULT_WATCH_EXTENSIONS).split(',')
            if e.strip()
        }
        settle_sec = 0 if manual else int(
            PREFS['watch_settle_sec'] if PREFS['watch_settle_sec'] is not None else DEFAULT_SETTLE_SEC
        )

        # Check for new settled files before creating a ThreadedJob so periodic background polls
        # do not animate calibre's Jobs indicator when nothing new is in the watch folder.
        if manual:
            self._sync_missing_library_view_rows()
        discovered = self.auto_import_engine.discover_settled_files(folder, exts, min_settle_sec=settle_sec)
        if not discovered:
            with self._lock:
                self._auto_import_running = False
            if manual:
                info_dialog(
                    self.gui,
                    _('True Covers for Kindle — Auto-Import Watch Folder Up to Date'),
                    _(
                        'No new settled ebook files found in:\n{0}\n\n'
                        'All existing files remain untouched in their original folder.'
                    ).format(folder),
                    show=True
                )
            return

        job = ThreadedJob(
            'true_covers_kindle_auto_import_job',
            _('True Covers for Kindle — Auto-Import: Importing {0} file(s) from watch folder').format(len(discovered)),
            self._execute_auto_import_job,
            (discovered, manual),
            {},
            Dispatcher(self._on_job_completed),
            max_concurrent_count=1,
            killable=True
        )
        self.gui.job_manager.run_threaded_job(job)

    def _execute_auto_import_job(self, discovered, manual, notifications=None, abort=None, log=None):
        try:
            db = self.gui.current_db.new_api
            added_ids, retry_fix_ids, skipped_dups = self.auto_import_engine.ingest_files(
                db,
                discovered,
                log_fn=lambda m: self._log_msg(log, m),
                abort=abort,
                needs_fix_fn=lambda bid: not is_book_stamped_and_baked(db, bid, self.asin_resolver)
            )
            if notifications is not None:
                notifications.put((1.0, _('Auto-Import added {0} new book(s)').format(len(added_ids))))
            self._auto_import_done_sig.emit(added_ids, retry_fix_ids, skipped_dups, bool(manual))
        finally:
            with self._lock:
                self._auto_import_running = False

    def _on_auto_import_done_main_thread(self, added_ids, retry_fix_ids, skipped_dups, manual):
        if added_ids:
            try:
                data_view = getattr(self.gui.current_db, 'data', None)
                if data_view is not None and hasattr(data_view, 'books_added'):
                    known = set(getattr(data_view, '_map', ()))
                    new_for_view = [bid for bid in added_ids if bid not in known]
                    if new_for_view:
                        data_view.books_added(new_for_view)
                        self.gui.library_view.model().books_added(len(new_for_view))
                else:
                    self.gui.library_view.model().books_added(len(added_ids))
                self.gui.library_view.set_current_row(0)
                self.gui.refresh_cover_browser()
            except Exception as err:
                self._audit_history.append(f'[WARN] Could not refresh library view after Auto-Import: {err}')
            self.gui.tags_view.recount()
        else:
            self._sync_missing_library_view_rows()

        handoff_fix_ids = list(added_ids) + [bid for bid in retry_fix_ids if bid not in added_ids]
        if handoff_fix_ids:
            if PREFS['auto_import_handoff_fix_cover']:
                self._enqueue_fix_kindle_cover_job(
                    handoff_fix_ids,
                    force_reprocess=False,
                    is_manual=manual,
                    job_title=_(
                        'True Covers for Kindle — Fix Kindle Cover: Processing {0} auto-imported book(s)'
                    ).format(len(handoff_fix_ids))
                )
            elif manual:
                info_dialog(
                    self.gui,
                    _('True Covers for Kindle — Auto-Import Complete'),
                    _('Imported {0} new book(s) (skipped {1} existing duplicate(s)).').format(
                        len(added_ids), skipped_dups
                    ),
                    show=True
                )
        elif manual:
            info_dialog(
                self.gui,
                _('True Covers for Kindle — Auto-Import Scan Complete'),
                _('Found 0 new titles to import ({0} file(s) matched books already in your library).').format(
                    skipped_dups
                ),
                show=True
            )

    # --- Database Event Hook for Manual Calibre Adds (EventType.format_added) ---

    def _db_event_listener(self, event_type, library_id, event_data):
        if self._shutting_down or self._auto_import_running or not PREFS['auto_run_on_add']:
            return
        try:
            evt_name = getattr(event_type, 'name', str(event_type))
            if 'format_added' in evt_name:
                book_id, fmt = event_data[0], str(event_data[1]).upper()
                if fmt in AUTO_RUN_ADDED_FORMATS and not fmt.startswith('ORIGINAL_'):
                    self._book_added_sig.emit(int(book_id), fmt)
        except Exception as err:
            self._audit_history.append(f'[WARN] format_added listener error: {err}')

    def _is_calibre_converting(self):
        try:
            for j in self.gui.job_manager.jobs():
                if not getattr(j, 'is_finished', True):
                    desc = str(getattr(j, 'description', '') or '').lower()
                    if 'convert' in desc and 'true covers' not in desc:
                        return True
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not inspect calibre jobs: {err}')
        return False

    def _on_book_format_added_main_thread(self, book_id, fmt):
        with self._lock:
            if book_id in self._in_progress_books:
                return
        delay_ms = (
            FORMAT_ADDED_CONVERTING_DELAY_MS
            if (fmt != 'AZW3' and self._is_calibre_converting())
            else FORMAT_ADDED_DEFAULT_DELAY_MS
        )
        QTimer.singleShot(
            delay_ms,
            lambda bid=book_id: self._enqueue_fix_kindle_cover_job(
                [bid],
                force_reprocess=False,
                is_manual=False,
                job_title=_('True Covers for Kindle — Fix Kindle Cover: Book #{0}').format(bid)
            )
        )

    # --- Fix Kindle Cover (EBOK Pipeline) + Handoff to Mark as Personal Document (PDOC) ---

    def _get_selected_book_ids(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            info_dialog(
                self.gui,
                _('True Covers for Kindle — No Books Selected'),
                _(
                    'Select one or more books in your library first, or use '
                    '"Scan & Fix Kindle Covers in Entire Library" from the True Covers for Kindle menu.'
                ),
                show=True
            )
            return []
        return list(self.gui.library_view.get_selected_ids())

    def _confirm_large_selection(self, book_ids, action_label):
        """Confirm with the user before starting a batch job when more than LARGE_SELECTION_THRESHOLD books are selected."""
        count = len(book_ids)
        if count <= LARGE_SELECTION_THRESHOLD:
            return True
        return question_dialog(
            self.gui,
            _('True Covers for Kindle — Confirm Large Selection'),
            _(
                'Run "{0}" on {1} selected books?\n\n'
                'This will run as a background job with progress shown in calibre\'s Jobs panel.'
            ).format(action_label, count),
            default_yes=True
        )

    def fix_selected_covers_smart(self):
        """Run Fix Kindle Cover on currently selected books, skipping already-fixed books if Smart Skip is enabled."""
        book_ids = self._get_selected_book_ids()
        if book_ids and self._confirm_large_selection(book_ids, _('Fix Kindle Cover')):
            self._enqueue_fix_kindle_cover_job(
                book_ids,
                force_reprocess=False,
                is_manual=True,
                job_title=_('True Covers for Kindle — Fix Kindle Cover: {0} selected book(s)').format(len(book_ids))
            )

    def fix_selected_covers_force(self):
        """Run Fix Kindle Cover on currently selected books, bypassing cached results and Smart Skip."""
        book_ids = self._get_selected_book_ids()
        if book_ids and self._confirm_large_selection(book_ids, _('Force Fix Kindle Cover')):
            self._enqueue_fix_kindle_cover_job(
                book_ids,
                force_reprocess=True,
                is_manual=True,
                job_title=_('True Covers for Kindle — Force Fix Kindle Cover: {0} book(s)').format(len(book_ids))
            )

    def fix_entire_library_smart(self):
        """Prompt for confirmation and run Fix Kindle Cover across all books in the active calibre library."""
        db = self.gui.current_db.new_api
        all_ids = sorted(list(db.all_book_ids()))
        if not all_ids:
            info_dialog(
                self.gui,
                _('True Covers for Kindle'),
                _('Your current calibre library is empty.'),
                show=True
            )
            return
        if not question_dialog(
            self.gui,
            _('True Covers for Kindle — Scan & Fix Kindle Covers in Entire Library'),
            _(
                'Scan all {0} books in your library?\n\n'
                '• Books that already have a fixed Kindle cover (EBOK + verified B0... ASIN) '
                'or Personal Document header (PDOC) are skipped in <2ms.\n'
                '• Books without a Kindle edition on Amazon will follow your configured '
                'Mark as Personal Document policy.'
            ).format(len(all_ids)),
            default_yes=True
        ):
            return
        self._enqueue_fix_kindle_cover_job(
            all_ids,
            force_reprocess=False,
            is_manual=True,
            job_title=_('True Covers for Kindle — Scanning & Fixing Library ({0} books)').format(len(all_ids))
        )

    def _enqueue_fix_kindle_cover_job(
        self, book_ids, force_reprocess=False, is_manual=False, job_title='True Covers for Kindle'
    ):
        job = ThreadedJob(
            'true_covers_kindle_fix_cover_job',
            job_title,
            self._execute_fix_cover_and_handoff_job,
            (book_ids, force_reprocess, is_manual),
            {},
            Dispatcher(self._on_job_completed),
            max_concurrent_count=1,
            killable=True
        )
        self.gui.job_manager.run_threaded_job(job)

    def _on_job_completed(self, job):
        pass

    def _execute_fix_cover_and_handoff_job(
        self, book_ids, force_reprocess, is_manual, notifications=None, abort=None, log=None
    ):
        db = self.gui.current_db.new_api
        total = max(1, len(book_ids))
        skip_enabled = bool(PREFS['skip_already_fixed']) and not force_reprocess
        policy = PREFS['unmatched_handoff_policy'] or POLICY_ASK_TIMEOUT

        skipped_count = 0
        fixed_count = 0
        info_count = 0
        auto_personal_doc_count = 0
        unmatched_ids = {}
        no_azw3_ids = {}
        error_ids = {}
        refreshed_ids = []

        for idx, book_id in enumerate(book_ids, 1):
            if abort is not None and abort.is_set():
                self._log_msg(log, '[ABORTED] User cancelled True Covers for Kindle batch job.')
                break
            if self._shutting_down:
                break

            with self._lock:
                if book_id in self._in_progress_books:
                    continue
                self._in_progress_books.add(book_id)

            try:
                if skip_enabled and is_book_stamped_and_baked(db, book_id, self.asin_resolver):
                    skipped_count += 1
                    title_fast = str(db.field_for('title', book_id) or f'Book #{book_id}')
                    self._log_msg(
                        log,
                        f'[SKIP] #{book_id} "{title_fast}" — already has fixed Kindle cover (EBOK+B0) or Personal Document (PDOC)'
                    )
                    continue

                mi = db.get_metadata(book_id)
                title = mi.title or f'Book #{book_id}'

                status, detail = self.fix_cover_pipeline.process_book(
                    db, book_id, mi, force_refresh=force_reprocess
                )
                refreshed_ids.append(book_id)

                if status == STATUS_FIXED:
                    fixed_count += 1
                    self._log_msg(log, f'[FIXED] #{book_id} "{title}" — {detail}')
                elif status in (STATUS_NO_AZW3, STATUS_UNMATCHED):
                    if policy == POLICY_AUTO_PERSONAL_DOC:
                        ok_pdoc, _pdoc_code, pdoc_detail = self.personal_doc_pipeline.mark_as_personal_document(
                            db, book_id, mi, log_fn=lambda m: self._log_msg(log, m)
                        )
                        if ok_pdoc:
                            auto_personal_doc_count += 1
                            self._log_msg(
                                log,
                                f'[PERSONAL-DOC] #{book_id} "{title}" — Marked as Personal Document ({pdoc_detail})'
                            )
                        elif status == STATUS_NO_AZW3:
                            no_azw3_ids[book_id] = MARK_NO_AZW3
                            self._log_msg(log, f'[NO_AZW3] #{book_id} "{title}" — {detail}')
                        else:
                            unmatched_ids[book_id] = MARK_NO_ASIN
                            self._log_msg(log, f'[NO_ASIN] #{book_id} "{title}" — {detail}')
                    elif status == STATUS_NO_AZW3:
                        no_azw3_ids[book_id] = MARK_NO_AZW3
                        self._log_msg(log, f'[NO_AZW3] #{book_id} "{title}" — {detail}')
                    else:
                        unmatched_ids[book_id] = MARK_NO_ASIN
                        self._log_msg(log, f'[NO_ASIN] #{book_id} "{title}" — {detail}')
                elif status == STATUS_ERROR:
                    error_ids[book_id] = detail
                    self._log_msg(log, f'[ERROR] #{book_id} "{title}" — {detail}')
                else:
                    info_count += 1
                    self._log_msg(log, f'[INFO] #{book_id} "{title}" — {detail}')

            except Exception as err:
                error_ids[book_id] = str(err)
                self._log_msg(log, f'[ERROR] #{book_id}: {err}')
            finally:
                with self._lock:
                    self._in_progress_books.discard(book_id)

            if notifications is not None:
                notifications.put((idx / total, _('Processed {0}/{1} books').format(idx, total)))

        combined_marks = {}
        if PREFS['mark_unmatched_books']:
            combined_marks.update(unmatched_ids)
        if PREFS['mark_missing_azw3']:
            combined_marks.update(no_azw3_ids)

        handoff_ids = sorted(set(unmatched_ids.keys()) | set(no_azw3_ids.keys()))
        succeeded_count = fixed_count + auto_personal_doc_count + info_count
        failed_count = len(unmatched_ids) + len(no_azw3_ids) + len(error_ids)

        summary_title = _('True Covers for Kindle — Fix Kindle Cover Complete')
        summary_lines = [
            _('Processed {0} book(s):').format(len(book_ids)),
            '',
            _('  • Succeeded: {0}').format(succeeded_count),
            _('      – Fixed Kindle Cover (EBOK + Kindle ASIN): {0}').format(fixed_count),
        ]
        if auto_personal_doc_count:
            summary_lines.append(
                _('      – Marked as Personal Document (PDOC): {0}').format(auto_personal_doc_count)
            )
        if skipped_count:
            summary_lines.append(
                _('  • Skipped (already fixed): {0}').format(skipped_count)
            )
        summary_lines.append(_('  • Failed: {0}').format(failed_count))
        if unmatched_ids or not (no_azw3_ids or error_ids):
            summary_lines.append(
                _('      – No Kindle edition found on Amazon (marked:no_asin): {0}').format(len(unmatched_ids))
            )
        if no_azw3_ids:
            summary_lines.append(
                _('      – Missing AZW3 format (marked:no_azw3): {0}').format(len(no_azw3_ids))
            )
        if error_ids:
            summary_lines.append(
                _('      – Processing errors: {0}').format(len(error_ids))
            )
        summary_body = '\n'.join(summary_lines)

        self._pipeline_ui_done_sig.emit(
            refreshed_ids,
            combined_marks,
            handoff_ids,
            summary_title,
            summary_body,
            bool(is_manual),
            failed_count > 0
        )

    def _on_pipeline_ui_done_main_thread(
        self, refreshed_ids, unmatched_marks, handoff_ids, summary_title, summary_body, is_manual, has_failures
    ):
        try:
            self._sync_missing_library_view_rows()
            if refreshed_ids:
                existing = dict(getattr(self.gui.current_db.data, 'marked_ids', {}) or {})
                changed_marks = False
                for rid in refreshed_ids:
                    if rid not in unmatched_marks and existing.get(rid) in CLEARABLE_MARKS:
                        existing.pop(rid, None)
                        changed_marks = True
                if changed_marks:
                    self.gui.current_db.set_marked_ids(existing)
                    try:
                        cur_search = str(self.gui.search.text() or '').strip()
                        if cur_search.startswith('marked:') and not existing:
                            self.gui.search.set_search_string('')
                    except Exception:
                        pass
                self._refresh_library_rows_and_details(refreshed_ids)

            if unmatched_marks:
                self._merge_marked_ids(unmatched_marks)

            policy = PREFS['unmatched_handoff_policy'] or POLICY_ASK_TIMEOUT
            if handoff_ids:
                handoff_prompt = summary_body + '\n\n' + _(
                    '{0} book(s) failed Fix Kindle Cover (no Kindle edition found on Amazon or missing AZW3 format).\n'
                    'Would you like to run "Mark as Personal Document" on just those {0} book(s) '
                    '(converts to AZW3 if needed, embeds the calibre cover, and stamps EXTH 501=PDOC) '
                    'so their covers display under Personal Documents on your Kindle?'
                ).format(len(handoff_ids))

                if policy == POLICY_ASK_TIMEOUT:
                    timeout_sec = max(
                        MIN_IDLE_TIMEOUT_SEC,
                        int(PREFS['idle_timeout_sec'] or DEFAULT_IDLE_TIMEOUT_SEC)
                    )
                    dlg = CountdownConfirmDialog(
                        self.gui,
                        summary_title,
                        handoff_prompt,
                        timeout_sec=timeout_sec,
                        default_accept=True
                    )
                    accepted_code = int(
                        QDialog.DialogCode.Accepted if hasattr(QDialog, 'DialogCode') else QDialog.Accepted
                    )
                    if dlg.exec() == accepted_code:
                        self._enqueue_personal_document_job(handoff_ids, show_popup=is_manual)
                    return
                elif is_manual:
                    if question_dialog(self.gui, summary_title, handoff_prompt, default_yes=True):
                        self._enqueue_personal_document_job(handoff_ids, show_popup=True)
                        return

            if is_manual and (PREFS['show_summary_dialog'] or has_failures):
                info_dialog(self.gui, summary_title, summary_body, show=True)
        except Exception as err:
            self._audit_history.append(f'[WARN] UI completion update error: {err}')

    # --- Mark as Personal Document (Manual or Handoff PDOC Job) ---

    def mark_selected_as_personal_document(self):
        """Run Mark as Personal Document on currently selected books."""
        book_ids = self._get_selected_book_ids()
        if book_ids and self._confirm_large_selection(book_ids, _('Mark as Personal Document')):
            self._enqueue_personal_document_job(book_ids, show_popup=True)

    def _enqueue_personal_document_job(self, book_ids, show_popup=False):
        job = ThreadedJob(
            'true_covers_kindle_personal_doc_job',
            _('True Covers for Kindle — Mark as Personal Document: {0} item(s)').format(len(book_ids)),
            self._execute_personal_document_job,
            (book_ids, show_popup),
            {},
            Dispatcher(self._on_job_completed),
            max_concurrent_count=1,
            killable=True
        )
        self.gui.job_manager.run_threaded_job(job)

    def _execute_personal_document_job(self, book_ids, show_popup, notifications=None, abort=None, log=None):
        db = self.gui.current_db.new_api
        total = max(1, len(book_ids))
        marked_count = 0
        no_format_count = 0
        error_count = 0
        refreshed_ids = []

        for idx, bid in enumerate(book_ids, 1):
            if abort is not None and abort.is_set():
                self._log_msg(log, '[ABORTED] User cancelled Mark as Personal Document job.')
                break
            if self._shutting_down:
                break

            with self._lock:
                if bid in self._in_progress_books:
                    continue
                self._in_progress_books.add(bid)

            try:
                mi = db.get_metadata(bid)
                title = mi.title or f'Book #{bid}'
                ok, pdoc_status, detail = self.personal_doc_pipeline.mark_as_personal_document(
                    db, bid, mi, log_fn=lambda m: self._log_msg(log, m)
                )
                if ok:
                    marked_count += 1
                    refreshed_ids.append(bid)
                    self._log_msg(log, f'[PERSONAL-DOC] #{bid} "{title}" — {detail}')
                else:
                    if pdoc_status == PDOC_STATUS_NO_FORMAT:
                        no_format_count += 1
                    else:
                        error_count += 1
                    self._log_msg(log, f'[ERROR] #{bid} "{title}" — {detail}')
            except Exception as err:
                error_count += 1
                self._log_msg(log, f'[ERROR] #{bid}: {err}')
            finally:
                with self._lock:
                    self._in_progress_books.discard(bid)

            if notifications is not None:
                notifications.put((idx / total, _('Marked {0}/{1} item(s) as Personal Document').format(idx, total)))

        failed_count = no_format_count + error_count
        summary_lines = [
            _('Processed {0} book(s) with Mark as Personal Document:').format(len(book_ids)),
            '',
            _('  • Succeeded (PDOC + embedded calibre cover): {0}').format(marked_count),
            _('  • Failed: {0}').format(failed_count),
        ]
        if no_format_count:
            summary_lines.append(
                _('      – No convertible format to create AZW3: {0}').format(no_format_count)
            )
        if error_count:
            summary_lines.append(
                _('      – Conversion or header patching errors: {0}').format(error_count)
            )
        summary_body = '\n'.join(summary_lines)

        self._pipeline_ui_done_sig.emit(
            refreshed_ids,
            {},
            [],
            _('True Covers for Kindle — Mark as Personal Document Complete'),
            summary_body,
            bool(show_popup),
            failed_count > 0
        )

    def _merge_marked_ids(self, new_marks):
        try:
            existing = dict(getattr(self.gui.current_db.data, 'marked_ids', {}) or {})
            existing.update(new_marks)
            self.gui.current_db.set_marked_ids(existing)
        except Exception as err:
            self._audit_history.append(f'[WARN] Could not merge marked_ids: {err}')

    # --- Manual ASIN Override, Binary Inspector & Library Audit ---

    def prompt_manual_asin_for_selected(self):
        """Prompt for a manual Kindle ASIN or Amazon URL and run stamping + verification in a background job."""
        book_ids = self._get_selected_book_ids()
        if not book_ids:
            return
        bid = book_ids[0]
        db = self.gui.current_db.new_api
        mi = db.get_metadata(bid)
        existing_ids = dict(mi.get_identifiers() or {})
        cur_asin = existing_ids.get('mobi-asin') or existing_ids.get('amazon') or ''

        raw_text, ok = QInputDialog.getText(
            self.gui,
            _('Set Kindle ASIN — {0}').format(mi.title),
            _(
                'Paste the 10-character Kindle ASIN (Amazon\'s ID for the Kindle edition, e.g., B0XXXXXXXX) '
                'or full Amazon book URL:\n'
                '(True Covers for Kindle will immediately embed the cover and stamp EXTH 113/504/501=EBOK)'
            ),
            text=str(cur_asin)
        )
        if not ok or not str(raw_text).strip():
            return

        raw_str = str(raw_text).strip()
        m_b0 = EXTRACT_B0_RE.search(raw_str.upper())
        if m_b0:
            asin = m_b0.group(1).upper()
        else:
            cleaned = re.sub(r'[^A-Za-z0-9]', '', raw_str).upper()
            if KINDLE_VALID_ID_RE.match(cleaned):
                asin = cleaned
            else:
                error_dialog(
                    self.gui,
                    _('True Covers for Kindle — Invalid ASIN'),
                    _(
                        'Could not find a valid 10-character ASIN (Amazon\'s ID for the Kindle edition, e.g., B0XXXXXXXX) in:\n"{0}"'
                    ).format(raw_str),
                    show=True
                )
                return

        job = ThreadedJob(
            'true_covers_kindle_manual_asin_job',
            _('True Covers for Kindle — Stamping manual ASIN {0} on "{1}"').format(asin, mi.title),
            self._execute_manual_asin_job,
            (bid, asin),
            {},
            Dispatcher(self._on_job_completed),
            max_concurrent_count=1,
            killable=False
        )
        self.gui.job_manager.run_threaded_job(job)

    def _execute_manual_asin_job(self, book_id, asin, notifications=None, abort=None, log=None):
        db = self.gui.current_db.new_api
        mi = db.get_metadata(book_id)
        title = mi.title or f'Book #{book_id}'
        stamped = False
        is_live = False
        img_sz = 0
        try:
            stamped = self.fix_cover_pipeline.stamp_manual_asin(db, book_id, mi, asin)
            is_live, img_sz = self.asin_resolver.verify_asin_live_cover(asin)
            if stamped:
                self._log_msg(log, f'[MANUAL-ASIN] #{book_id} "{title}" — Stamped {asin} (live_cdn={is_live})')
            else:
                self._log_msg(log, f'[MANUAL-ASIN-ERR] #{book_id} "{title}" — Saved ASIN {asin}, but AZW3 stamping failed')
        except Exception as err:
            self._log_msg(log, f'[MANUAL-ASIN-ERR] #{book_id} "{title}": {err}')
        if notifications is not None:
            notifications.put((1.0, _('Completed manual ASIN update for "{0}"').format(title)))
        self._manual_asin_done_sig.emit(int(book_id), title, asin, bool(stamped), bool(is_live), int(img_sz))

    def _on_manual_asin_done_main_thread(self, book_id, title, asin, stamped, is_live, img_sz):
        self._refresh_library_rows_and_details([book_id])
        if not stamped:
            error_dialog(
                self.gui,
                _('True Covers for Kindle — Could Not Stamp AZW3 File'),
                _(
                    'Saved ASIN {0} to metadata for "{1}", but no AZW3 file was available or could be created to stamp headers.'
                ).format(asin, title),
                show=True
            )
            return

        if is_live:
            info_dialog(
                self.gui,
                _('True Covers for Kindle — Cover Verified & Stamped'),
                _(
                    'Successfully stamped ASIN {0} (EBOK), embedded the cover, and updated the AZW3 file for:\n"{1}"\n\n'
                    'Amazon Cover Server Verification: PASSED (Live JPEG found, {2} bytes).\n'
                    'Note: If you already downloaded an older copy of this book to your Kindle, '
                    'delete the old copy on the Kindle first so its cover thumbnail cache refreshes.'
                ).format(asin, title, img_sz),
                show=True
            )
        else:
            if question_dialog(
                self.gui,
                _('True Covers for Kindle — Warning: ASIN Has No Live Cover on Amazon'),
                _(
                    'Stamped ASIN {0} into "{1}", but Amazon\'s Kindle cover server '
                    '(images-na.ssl-images-amazon.com) returned NO cover image for {0}.\n\n'
                    'This happens on some older or discontinued editions and can cause the Kindle '
                    'to display a blank cover under Books.\n\n'
                    'Would you like to run "Mark as Personal Document" (PDOC) on this book instead '
                    'so your Kindle displays the embedded calibre cover?'
                ).format(asin, title),
                default_yes=True
            ):
                self._enqueue_personal_document_job([book_id], show_popup=True)

    def inspect_selected_book_header(self):
        """Inspect and display the on-disk AZW3 EXTH binary header records for the selected book."""
        book_ids = self._get_selected_book_ids()
        if not book_ids:
            return
        bid = book_ids[0]
        db = self.gui.current_db.new_api
        mi = db.get_metadata(bid)
        has_cov = book_has_cover(db, bid)
        fmts = get_book_formats(db, bid)
        azw3_path = db.format_abspath(bid, 'AZW3') if 'AZW3' in fmts else None
        info = ExthBinaryCodec.inspect_file(
            azw3_path, allow_isbn10=bool(PREFS['fallback_to_isbn10'])
        ) if azw3_path else None

        yes_label = _('YES')
        no_label = _('NO')
        missing_label = _('MISSING')
        none_label = _('None')

        lines = [
            _('Book ID: #{0}').format(bid),
            _('Title: {0}').format(mi.title),
            _('Authors: {0}').format(', '.join(mi.authors or [])),
            _('Calibre Formats: {0}').format(', '.join(fmts) if fmts else none_label),
            _('Calibre Library Cover Present: {0}').format(yes_label if has_cov else no_label),
            _('Calibre Identifiers: {0}').format(dict(mi.get_identifiers() or {})),
            '',
            _('--- On-Disk .azw3 Binary EXTH Inspection ---'),
        ]
        if not info or not info['valid_mobi']:
            lines.append(_('No valid .azw3 file present on disk.'))
        else:
            e113 = info['exth_113'] or missing_label
            if info.get('is_personal_document'):
                id_note = _(' (Personal Document Local Key — PDOC)')
                skip_label = _('READY (Marked as Personal Document — PDOC with Embedded Cover)')
            elif info.get('has_b0_asin'):
                is_live, img_sz = self.asin_resolver.verify_asin_live_cover(
                    e113, mi.title or '', ' & '.join(mi.authors or [])
                )
                if is_live:
                    id_note = _(' (LIVE Amazon Cover & Identity Verified — {0} bytes)').format(img_sz)
                    skip_label = _('READY (Fixed Kindle Cover — Live B0... ASIN & Embedded Cover)')
                else:
                    id_note = _(' (DEFUNCT / STUDY GUIDE / NO AMAZON COVER!)')
                    skip_label = _(
                        'DEFUNCT OR MISMATCHED ASIN — Needs Fix Kindle Cover or Mark as Personal Document (PDOC)'
                    )
            else:
                id_note = (
                    _(' (Non-Kindle Print ISBN / UUID — needs B0... ASIN or Mark as Personal Document!)')
                    if info['exth_113'] else ''
                )
                skip_label = _('NEEDS FIX KINDLE COVER OR MARK AS PERSONAL DOCUMENT')

            exth_201_status = (
                _('YES (offset {0})').format(info['cover_offset'])
                if info['has_exth_201']
                else missing_label
            )
            lines.extend([
                _('EXTH 201 (Embedded CoverOffset): {0}').format(exth_201_status),
                _('EXTH 501 (Kindle cdeType):       {0} (EBOK = Books, PDOC = Personal Documents)').format(
                    info['exth_501'] or missing_label
                ),
                _('EXTH 113 (Primary Key/ASIN):     {0}{1}').format(e113, id_note),
                _('EXTH 504 (Secondary Key):        {0}').format(info['exth_504'] or missing_label),
                _('Smart-Skip Status:               {0}').format(
                    skip_label if has_cov else _('MISSING CALIBRE COVER')
                ),
            ])
        info_dialog(
            self.gui,
            _('True Covers for Kindle — Binary Header Inspector — {0}').format(mi.title),
            '\n'.join(lines),
            show=True
        )

    def clear_asin_cache_action(self):
        """Clear all cached ASIN lookups and notify the user."""
        self.asin_resolver.clear_cache()
        info_dialog(
            self.gui,
            _('True Covers for Kindle — ASIN Cache Cleared'),
            _('Cleared all cached ASIN lookups. Your next run will query online sources fresh.'),
            show=True
        )

    def show_audit_log_dialog(self):
        """Display the in-memory session log dialog."""
        if not self._audit_history:
            info_dialog(
                self.gui,
                _('True Covers for Kindle — Session Log'),
                _('No True Covers for Kindle jobs have run during this calibre session yet.'),
                show=True
            )
            return
        text = '\n'.join(self._audit_history)
        info_dialog(
            self.gui,
            _('True Covers for Kindle — Session Log ({0} entries)').format(len(self._audit_history)),
            text,
            show=True
        )

    def audit_entire_library(self):
        """Run a read-only background binary audit across the library to highlight books needing cover fixes."""
        db = self.gui.current_db.new_api
        all_ids = sorted(list(db.all_book_ids()))
        if not all_ids:
            return

        job = ThreadedJob(
            'true_covers_kindle_audit_job',
            _('True Covers for Kindle — Auditing {0} book(s) in library').format(len(all_ids)),
            self._execute_audit_library_job,
            (all_ids,),
            {},
            Dispatcher(self._on_job_completed),
            max_concurrent_count=1,
            killable=True
        )
        self.gui.job_manager.run_threaded_job(job)

    def _execute_audit_library_job(self, all_ids, notifications=None, abort=None, log=None):
        db = self.gui.current_db.new_api
        total = max(1, len(all_ids))
        ebok_count = 0
        pdoc_count = 0
        needs_fix = {}
        allow_isbn10 = bool(PREFS['fallback_to_isbn10'])

        for idx, bid in enumerate(all_ids, 1):
            if abort is not None and abort.is_set():
                self._log_msg(log, '[ABORTED] User cancelled library binary audit.')
                return
            if self._shutting_down:
                return

            if not book_has_cover(db, bid):
                needs_fix[bid] = MARK_NEEDS_COVER_FIX
            else:
                fmts = get_book_formats(db, bid)
                if 'AZW3' not in fmts:
                    needs_fix[bid] = MARK_NEEDS_COVER_FIX
                else:
                    azw3_path = db.format_abspath(bid, 'AZW3')
                    info = ExthBinaryCodec.inspect_file(azw3_path, allow_isbn10=allow_isbn10)
                    if info.get('is_fixed_kindle_cover'):
                        ebok_count += 1
                    elif info.get('is_personal_document'):
                        pdoc_count += 1
                    else:
                        needs_fix[bid] = MARK_NEEDS_COVER_FIX

            if notifications is not None and (idx % 25 == 0 or idx == total):
                notifications.put((idx / total, _('Audited {0}/{1} books').format(idx, total)))

        self._audit_done_sig.emit(len(all_ids), ebok_count, pdoc_count, needs_fix)

    def _on_audit_done_main_thread(self, total_scanned, ebok_count, pdoc_count, needs_fix):
        self._merge_marked_ids(needs_fix)
        if needs_fix:
            self.gui.search.set_search_string(f'marked:{MARK_NEEDS_COVER_FIX}')

        info_dialog(
            self.gui,
            _('True Covers for Kindle — Library Binary Audit Complete'),
            _(
                'Scanned {0} item(s) in your library:\n\n'
                '  • Ready as Kindle Books (EBOK + B0... ASIN): {1}\n'
                '  • Ready as Personal Documents (PDOC + Embedded Cover): {2}\n'
                '  • Needs Fix Kindle Cover or Mark as Personal Document: {3}\n\n'
            ).format(total_scanned, ebok_count, pdoc_count, len(needs_fix))
            + (
                _('Items needing attention are now highlighted (search: marked:{0}).').format(MARK_NEEDS_COVER_FIX)
                if needs_fix
                else _('Every item in your library already has a fixed Kindle cover or Personal Document header!')
            ),
            show=True
        )
