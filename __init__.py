# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

from calibre.customize import InterfaceActionBase

try:
    load_translations()
except NameError:
    _ = lambda x: x


class TrueCoversKindlePlugin(InterfaceActionBase):
    """Calibre interface plugin ensuring books downloaded from the Content Server display proper Kindle covers."""

    name                    = 'True Covers for Kindle'
    description             = _(
        'Ensures books downloaded from the calibre Content Server to a Kindle display their proper cover art: '
        '(1) Fix Kindle Cover: Finds the Kindle edition ASIN (Amazon\'s ID for the Kindle edition), verifies its cover, '
        'embeds the cover into AZW3, and stamps EBOK headers; '
        '(2) Auto-Import: Watches a folder, imports new books non-destructively, and runs Fix Kindle Cover; '
        '(3) Mark as Personal Document: Fallback for books, comics, and documents without a Kindle edition on Amazon.'
    )
    supported_platforms     = ['windows', 'osx', 'linux']
    author                  = 'Aidan Welch'
    version                 = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin           = 'calibre_plugins.true_covers_kindle.action:TrueCoversKindleAction'

    def is_customizable(self) -> bool:
        """Return True so the plugin can be configured via Preferences -> Plugins."""
        return True

    def config_widget(self):
        """Instantiate and return the Qt configuration widget for this plugin."""
        from calibre_plugins.true_covers_kindle.config import ConfigWidget
        return ConfigWidget(self.actual_plugin_)

    def save_settings(self, config_widget) -> None:
        """Persist changes from the configuration widget and apply updated runtime settings."""
        config_widget.save_settings()
        if self.actual_plugin_:
            self.actual_plugin_.apply_settings()
