# Advance Clipboard Manager

A lightweight Windows clipboard manager with SQLite storage, pinned clips, groups, tags, image support, and fast local search. The app is intentionally dependency-light and runs without WebEngine or local embedding models.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![PyQt6](https://img.shields.io/badge/PyQt6-6.4+-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

## Features

### Clipboard Manager

- History and pinned lists for recent clips and favorites.
- Text and image clip support.
- Duplicate detection by content hash.
- Tags for pinned items.
- Collapsible groups for pinned clips.
- Click or press Enter to paste into the active window.
- SQLite storage with WAL mode.
- One primary process owns the global hotkey and clipboard listener; later launches activate the existing window.
- Text captures above 2 MiB UTF-8 and images above 8K UHD (7680x4320) remain on the system clipboard but are not saved to history.
- JSON backups debounce for 30 seconds, run no more than once per five minutes, retain ten valid files, and use checksum validation.
- Recovery from valid backup if the database is missing or corrupt.

### Search

- Debounced search across history and pinned clips.
- SQL lexical matching with local dependency-free hybrid ranking.
- Tag search via `tag <keyword>` or `tags <keyword>`.
- Search is asynchronous so typing does not block the UI.
- Triple-click or clear buttons reset the search box.
- Performance gate: `uv run python tests/benchmark_search.py` builds a temporary 25,000-row database and requires common one- and two-term searches to remain at or below 25 ms p95. A failure is evidence for a separate FTS5 migration design; it does not trigger an in-place search rewrite.

### Pinned Clip Editor

- Pinned clips can be opened in a small native popup window.
- The popup can be moved and resized by the OS window frame.
- The title uses the clip tag when present.
- In-popup search jumps between highlighted matches.

## Installation

```bash
git clone https://github.com/Hiroshimeow/Advance-Clipboard.git
cd Advance-Clipboard

# uv
uv sync
uv run main.py

# pip
pip install -r requirements.txt
python main.py
```

## Usage

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+V` | Toggle clipboard manager |
| `Esc` | Hide window |
| `Enter` | Paste selected item |
| `Click item` | Paste item |

### Item Actions

| Button | Action |
| --- | --- |
| `Copy` | Copy to clipboard without pasting |
| `Star` | Pin or unpin item |
| `Delete` | Delete item |
| `Up/Down` | Move pinned item order |
| `Expand` | Show more clip content inline |

### Context Menu

Right-click a pinned clip to:

- add it to an existing group,
- create a new group,
- remove it from a group,
- add or edit a tag.

## Architecture

```text
Clipboard Monitor -> SQLite Storage -> Search/Ranking -> PyQt6 UI
                         |
                         -> JSON Backup
```

### Data Flow

- Read: UI requests paged history or pinned rows from SQLite.
- Write: Qt copies clipboard-owned text/image data on the UI thread, then one bounded background ingest worker serializes image encoding, atomic file writes, and SQLite insertion.
- Search: SQL filtering plus local lightweight ranking, with asynchronous UI updates.
- Backup: SQLite data is exported to JSON on debounce or app exit.
- Recovery: valid backup JSON can repopulate SQLite when needed.

## File Structure

```text
advance-clipboard/
|-- core/                 # Win32 clipboard monitor and paste helpers
|-- storage/              # SQLite storage, backup, and search services
|-- ui/                   # PyQt6 views, rows, delegates, widgets
|-- tests/                # Unit and UI behavior tests
|-- main.py               # Application entry point and main window
|-- pyproject.toml        # uv project config
|-- requirements.txt      # pip dependency list
|-- FUTURE.md             # Roadmap
`-- README.md
```

Runtime artifacts such as `storage/clipboard.db`, `images/`, `backups/`, and `logs/` are ignored by git.

## Requirements

- Python 3.11+
- Windows 10/11
- PyQt6

## License

MIT License
