# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aidan Welch

__license__ = 'GPL v3'
__copyright__ = '2026, Aidan Welch'

"""Build script that packages True Covers for Kindle into a calibre-installable .zip archive."""

import argparse
import os
import sys
import zipfile
from typing import Optional

DEFAULT_OUTPUT_FILENAME = 'True_Covers_for_Kindle.zip'

REQUIRED_ROOT_FILES = (
    '__init__.py',
    'action.py',
    'auto_import.py',
    'config.py',
    'core.py',
    'pipelines.py',
    'plugin-import-name-true_covers_kindle.txt',
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'README.md',
)

INCLUDED_DIRECTORIES = (
    'images',
    'licenses',
)

IGNORED_FILENAMES = {'.ds_store', 'thumbs.db', 'desktop.ini'}
IGNORED_SUFFIXES  = ('.pyc', '.pyo', '.tmp', '.swp')


def collect_plugin_files(repo_root: str) -> list[str]:
    """Return a sorted list of relative POSIX file paths to include in the plugin archive."""
    missing = [
        rel_path for rel_path in REQUIRED_ROOT_FILES
        if not os.path.isfile(os.path.join(repo_root, rel_path))
    ]
    if missing:
        raise FileNotFoundError(
            f'Missing required plugin file(s) in {repo_root}: {", ".join(missing)}'
        )

    collected = list(REQUIRED_ROOT_FILES)

    for folder_name in INCLUDED_DIRECTORIES:
        folder_abs = os.path.join(repo_root, folder_name)
        if not os.path.isdir(folder_abs):
            continue
        for current_root, dirs, files in os.walk(folder_abs):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
            for filename in sorted(files):
                lower_name = filename.lower()
                if lower_name in IGNORED_FILENAMES or lower_name.endswith(IGNORED_SUFFIXES):
                    continue
                abs_path = os.path.join(current_root, filename)
                rel_path = os.path.relpath(abs_path, repo_root).replace('\\', '/')
                if rel_path not in collected:
                    collected.append(rel_path)

    return collected


def build_plugin_zip(
    repo_root: str,
    output_path: str = DEFAULT_OUTPUT_FILENAME
) -> tuple[str, list[str]]:
    """Create the calibre plugin .zip archive at output_path and return (abs_output_path, archived_files)."""
    files_to_pack = collect_plugin_files(repo_root)
    abs_output = (
        output_path if os.path.isabs(output_path)
        else os.path.join(repo_root, output_path)
    )

    with zipfile.ZipFile(abs_output, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files_to_pack:
            src_path = os.path.join(repo_root, *rel_path.split('/'))
            zf.write(src_path, rel_path)

    return abs_output, files_to_pack


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point for building the True Covers for Kindle plugin archive."""
    parser = argparse.ArgumentParser(
        description='Package True Covers for Kindle into a calibre plugin .zip archive.'
    )
    parser.add_argument(
        '-o', '--output',
        default=DEFAULT_OUTPUT_FILENAME,
        help=f'Output .zip file path (default: {DEFAULT_OUTPUT_FILENAME})'
    )
    args = parser.parse_args(argv)

    repo_root = os.path.dirname(os.path.abspath(__file__))
    try:
        abs_output, packed_files = build_plugin_zip(repo_root, args.output)
    except Exception as err:
        print(f'Error building plugin archive: {err}', file=sys.stderr)
        return 1

    size_bytes = os.path.getsize(abs_output)
    print(f'Built {abs_output} ({size_bytes:,} bytes, {len(packed_files)} files):')
    for rel_path in packed_files:
        print(f'  - {rel_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
