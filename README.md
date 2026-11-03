# Advance Clipboard

**A Windows clipboard workspace for the things you copy—and the things you keep copying.**

Advance Clipboard brings text, images, reusable snippets, and explicitly launched actions into a two-column PyQt6 desktop app. History keeps recent copies within reach; Pinned keeps the useful ones from disappearing into yesterday. Open it with `Ctrl+Alt+V`, find a clip, and paste it back into your previous application.

Built with Python, PyQt6, SQLite, and native Win32 clipboard notifications. No browser engine or embedding model required. Your clipboard does not need a spaceship.

## What it does

- **Capture text and images**, with content-hash deduplication and persistent local history.
- **Organize reusable clips** with pinning, tags, collapsible groups, manual ordering, previews, and text editing.
- **Search both columns** using literal keywords or dedicated tag, image, action, and shortcut filters.
- **Paste by selection or shortcut**: click, Enter, six position-based Alt shortcuts, or a persistent Ctrl binding assigned to a particular clip.
- **Attach explicit actions**: run a script, execute inline Python, launch a command, or open a web URL. Running processes expose STOP and a live output log.
- **Keep local recovery copies** through checksummed JSON backups alongside SQLite storage.
- **Use one primary instance**: later launches activate the existing application instead of competing for the clipboard listener and global hotkey.

The interface currently mixes English and Vietnamese labels. This guide uses the visible labels where they help you find a command.

## Install and run on Windows

### Requirements

- Windows 10 or 11 desktop session. Clipboard monitoring, global hotkeys, and paste/focus handling depend on Win32.
- Python **3.11–3.13** (`>=3.11,<3.14`).
- **PyQt6**, the sole declared third-party runtime dependency. The pip requirements specify `PyQt6>=6.4.0`.
- A Python SQLite build with **FTS5 and the trigram tokenizer**. The current database schema creates that index during initialization; it is not an optional search extra.
- A writable checkout: the application stores its database, images, backups, and diagnostic files beside the source.

### Option A: uv

From your checked-out repository in PowerShell:

```powershell
Set-Location 'C:\src\advance-clipboard' # Replace with your checkout path.
uv sync
uv run main.py
```

`main.py` also declares its Python range and PyQt6 dependency as inline script metadata, so `uv run main.py` can resolve its script environment without a separate manual PyQt6 installation. Initial dependency resolution may require network access.

### Option B: Python and pip

```powershell
Set-Location 'C:\src\advance-clipboard' # Replace with your checkout path.
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

These commands use the virtual environment directly; activating it is optional.

### Included launchers

After dependencies are available, `launch.ps1` and `launch.bat` change to the repository directory, prefer `uv run main.py`, and fall back to `python main.py` on PATH. The fallback Python must already have PyQt6 installed.

```powershell
.\launch.ps1
# Or, without invoking a PowerShell script:
.\launch.bat
```

The PowerShell launcher is subject to your machine's execution policy; the batch launcher is an alternative without changing that policy. Startup leaves the popup hidden. Press **Ctrl+Alt+V** to show it. If the app is already running, another launch activates the primary window.

## A practical workflow

1. **Copy normally** in another application. Text and image captures enter History.
2. **Open the workspace** with `Ctrl+Alt+V` and type a few identifying words. Search filters both columns.
3. **Paste a result** by clicking it or selecting it with Up/Down and pressing Enter. The app attempts to restore the previous target window before pasting.
4. **Keep the good stuff**: use the star to pin a clip, then right-click to add a tag or place it in a group. Expand long content or open a preview; use **Fix** to edit text.
5. **Give a frequent clip a stable binding** through **Gán phím tắt (Ctrl+key)...**. For example, assign `Q` to a reusable reply and press `Ctrl+Q` while the popup is active—even when that clip is outside the current search results.
6. **Make a task explicit**: use **Gán Action...** to attach a trusted script or command. Select the clip and press `Ctrl+Enter`, or choose RUN. Copying and pasting it remain ordinary clipboard operations, not execution triggers.

Use the row's Copy control when you want to update the clipboard without immediately pasting. Pin/unpin, delete, ordering, expansion, previews, tags, and groups are available through row tools or the context menu as appropriate. The **X buttons beside search clear the query**, not your history; triple-clicking the search field also clears it.

## Keyboard reference

Only `Ctrl+Alt+V` is registered as a desktop-wide hotkey. The remaining bindings belong to the clipboard popup/main window and its search field, not to every application or every editor/dialog inside this app.

| Shortcut | Behavior |
| --- | --- |
| `Ctrl+Alt+V` | Toggle the clipboard popup globally. |
| `Esc` | Hide the popup. |
| `Up` / `Down` | Move the selection in the active column while searching. |
| `Enter` | Paste the selected clip into the previous target application. |
| `Alt+1`, `Alt+2`, `Alt+3` | Paste the first, second, or third History clip in the current list. |
| `Alt+4`, `Alt+5`, `Alt+6` | Paste the first, second, or third Pinned clip in the current list. |
| Assigned `Ctrl+0`–`Ctrl+9`, `Ctrl+A`–`Ctrl+Z` | Paste the clip bound to that key, independently of search results and list position. |
| `Ctrl+Enter` | Run the selected clip's configured action; otherwise open its content if it is an HTTP(S) URL. |

### Position shortcuts versus persistent bindings

Alt shortcuts follow the current filtered list order, skipping group headers and clips inside collapsed groups. They do nothing if the requested position does not exist. These are clip positions in the loaded list, not screen coordinates or fixed database IDs.

Persistent Ctrl bindings are stored by clip ID. They survive searching, reordering, pinning, and content edits; deleting the clip removes its binding. The assignment dialog accepts one letter or digit, detects an existing assignment, and lets you cancel or reassign it.

**Choose Ctrl bindings carefully.** In the search field, a matching assigned binding takes precedence over ordinary text editing. Assigning `C`, `V`, or `A`, for example, makes `Ctrl+C`, `Ctrl+V`, or `Ctrl+A` paste that bound clip instead of copying, pasting into, or selecting all text in the search box. Unassigned combinations fall through to normal editing. `Ctrl+Enter` is handled as the action command before ordinary Enter-to-paste; Alt quick-paste and Ctrl clip bindings are handled before normal search-field keys.

Running-action clips are brought near the **fourth clip position onward** in their column, or earlier when fewer clips precede them. This is a display-order rule, **not an `Alt+4` action shortcut**. `Alt+4` always means the first Pinned clip, and it pastes—it does not run anything.

## Search reference

Ordinary searches split on whitespace and require **all terms**, matching across content, tags, and group names. Results use deterministic relevance tiers—such as exact tag/content matches before broader matches—with recency breaking ties within a tier. This is lexical search, not semantic similarity.

| Query | Meaning |
| --- | --- |
| `deploy server` | Find clips matching both literal terms across content, tag, or group fields. |
| `100%` or `snake_case` | Search literal percent signs or underscores; they are not SQL wildcards. |
| `tag` or `tags` | Show clips with a nonempty tag. |
| `tag work`, `tags work`, `tag:work` | Search within tags. |
| `type image`, `type: image`, `type:image` | Show image clips. `img` can replace `image`. |
| `image:`, `img:`, `images:`, `type:images` | Additional whole-query image-filter aliases. |
| `action` or `actions` | Show clips with a configured action. |
| `action deploy`, `actions:deploy` | Match terms against clip content/tag/group or the action target/type. |
| `ctrl`, `shortcut`, `shortcuts` | Show clips with an assigned Ctrl binding. |
| `ctrl Q`, `shortcut:Q` | Match binding keys or associated clip content/tag/group. |

The `action`/`actions` and `ctrl`/`shortcut`/`shortcuts` prefixes accept a space or colon before their optional terms, just like the tag filter.

These are **dedicated query modes, not a composable filter language**. Use the image filter as a complete query; do not expect `type:image tag:work` to combine two filters. Quotes do not introduce phrase syntax, and Boolean operators are not exposed. Action arguments and output logs are not searched; ordinary free-text search does not include action targets.

## Actions: useful tools, explicit permission

A clip can hold one action configuration. Use **Gán Action...** in its context menu to configure it, RUN or `Ctrl+Enter` to invoke it, and STOP / Show log to inspect or stop a running process.

| Stored action type | Execution behavior |
| --- | --- |
| `script_file` | Requires an absolute script path. Accepts `.py`, `.ps1`, `.bat`, `.cmd`, and `.sh`; actual execution still depends on the OS/interpreter. Runs with the script's directory as its working directory. |
| `python_inline` | Executes configured Python code using `python -u -c`, via the resolved Python runner, with the application's working directory. |
| `command` | Launches the target executable and separate argument list directly, without an implicit shell, using the application's working directory. |
| `url` | Opens a URL beginning with `http://` or `https://` in the default browser. |

### Runner details that matter

- **Python files** prefer `uv run <absolute-script-path> [arguments]` when uv is on PATH. Otherwise the resolver looks for nearby `.venv` interpreters, then falls back to the application's Python interpreter. Inline Python uses the same runner selection with `python -u -c` under uv.
- **PowerShell files** use `pwsh` or `powershell` with `-NoProfile -ExecutionPolicy Bypass -File`, not uv. That policy bypass applies to the launched script process; it does not make the script trustworthy.
- Other accepted script extensions are launched directly. Accepting `.sh`, for example, does not supply a Unix shell on Windows.
- The dialog splits its arguments field on whitespace. It does **not** implement shell quoting, so an argument containing spaces cannot be represented reliably there. Prefer a small wrapper script for complex invocation.
- Command targets should be executables, with arguments in the arguments field. Shell built-ins, pipes, and redirection require explicitly invoking a shell; they are not automatically interpreted.
- Process actions are asynchronous and tracked with stdout/stderr logs. Despite its “Background / Detached process” wording, the background option does **not** currently create a separately detached process with a distinct lifecycle.
- STOP attempts to terminate the process tree on Windows. It is not an undo operation, and the UI does not impose a default execution timeout.

A configured action takes precedence over URL content when `Ctrl+Enter` is pressed. Without a configured action, HTTP(S) clip content can be opened explicitly with `Ctrl+Enter` or the URL context-menu command. Merely copying, pasting, previewing, or searching a clip does not dispatch its action.

**Actions are not sandboxed.** Scripts and commands run with your user account's permissions and can read files, access the network, or change system state. Review targets and arguments before running them, including configurations recovered from a backup. URL opening delegates trust decisions to your browser.

## Engineering overview

```text
Win32 clipboard notifications + global hotkey
                    |
          Qt clipboard payload copy
                    |
       bounded, single ingest worker
          /                     \
    PNG image files          SQLite + FTS5
                                  |
                    search worker + ranking
                                  |
                   PyQt6 list models/delegates
                                  |
                     focus restore + paste

SQLite snapshot -> backup subprocess -> checksummed JSON
Explicit RUN    -> action runner     -> process / browser
```

### Design choices and tradeoffs

- **Native event delivery:** `core/clipboard_monitor.py` uses a message-only Win32 window, `AddClipboardFormatListener`, and `RegisterHotKey`, rather than polling the clipboard or installing a general keyboard hook. `core/single_instance.py` coordinates ownership across launches.
- **Keep Qt-owned data on the right thread:** the UI thread copies clipboard payloads; `core/clipboard_ingest.py` serializes image encoding, atomic image writes, and database insertion on one worker. Its queue is bounded, so overload can drop captures rather than accumulate unlimited pending work.
- **SQLite as the durable center:** `storage/db.py` uses thread-local connections, WAL mode, foreign keys, and transactions. Shortcut/action records reference clip IDs and are removed with deleted clips. WAL helps concurrent reads and writes; it is not a substitute for backup.
- **Hybrid lexical retrieval, not an AI service:** the schema includes an external-content **FTS5 trigram index** over content, tags, and groups, maintained by triggers. `storage/__init__.py` combines selective FTS candidate probes, indexed SQL/escaped `LIKE` paths, and deterministic relevance tiers. Short terms and broader searches need non-FTS paths; results are not simply an FTS relevance score.
- **Separate search from painting:** `ui/clipboard_browser_controller.py` debounces searches, runs retrieval off the UI thread, and rejects stale result generations. List models and delegates avoid constructing a full widget tree for every clip. Paging/candidate limits are a deliberate bound on work, not a promise of exhaustive unlimited ranking in one request.
- **Separate backups from interaction:** `storage/backup.py` schedules JSON export in a subprocess, reducing UI contention from serialization. Recovery checks checksums and can fall back to an older valid backup.
- **Focus is a protocol, not a wish:** `main.py` remembers the previous target, attempts foreground restoration, and avoids pasting after a known target mismatch. Edit controls can receive `WM_PASTE`; other targets use keyboard paste. These mitigations still need real-application testing, especially around Explorer rename fields, elevated windows, and custom editors.

### Source map

| Path | Responsibility |
| --- | --- |
| `main.py` | Application lifecycle, window wiring, clipboard/paste coordination, and action dispatch. |
| `core/` | Win32 monitoring and paste helpers, single-instance coordination, ingest, and action execution. |
| `storage/` | SQLite schema/repositories, search, backup, and recovery. |
| `ui/` | Browser controller, list models/delegates, previews, dialogs, and context menus. |
| `tests/` | Storage, search, migration, ingestion, shortcut, action, UI, and Win32 behavior tests; search benchmark. |
| `pyproject.toml`, `requirements.txt` | Python range and runtime dependency declarations. |

## Local data, privacy, and recovery

Paths below are relative to the repository/application directory, not a central cloud account or an encrypted vault.

| Path | Contents |
| --- | --- |
| `storage/clipboard.db` | Clip text, image filename references, tags/groups, pinned order, Ctrl bindings, and action configurations. SQLite may also create `-wal` and `-shm` sidecars. |
| `images/` | Captured images stored as hash-named PNG files. |
| `backups/` | JSON recovery snapshots of clip records. Automatic snapshots currently omit Ctrl bindings and action configurations; preserve the SQLite database to retain those. |
| `logs/Advance Clipboard.debug.log` | Diagnostic logging. |
| `logs/Advance Clipboard.fault.log` | Python fault diagnostics. |

These runtime paths are ignored by Git, but **Git ignore rules are not access controls**. Clipboard content and JSON backups are not encrypted by the application. There is no built-in password-manager exclusion, secret-redaction policy, or encrypted vault. Treat this directory as sensitive, restrict access using Windows permissions, and avoid copying secrets while recording is active. Review diagnostic files before sharing them.

The core capture/search/storage workflow is local and does not require a cloud or AI service. That is not a blanket network guarantee: dependency installation, browser URL opening, and user-configured scripts/commands can access the network. Filesystem sync software can also copy the runtime directory elsewhere.

### Capture and backup boundaries

- Text above **2 MiB of UTF-8 data** and images above **33,177,600 pixels** (`7680 × 4320` pixels in total, not a fixed aspect-ratio constraint) are not saved to history. The original system clipboard payload is left available.
- The ingest queue holds up to **16** pending requests; an overloaded queue may reject a capture. Clipboard history is not a guaranteed recording of every intermediate copy.
- Backups use a **30-second debounce** and a **five-minute cooldown**, with a forced attempt on exit. They retain up to **ten snapshot files**; changes since the last successful snapshot may be absent.
- A **SHA-256 checksum** detects snapshot corruption. It does not encrypt or authenticate the data against deliberate modification.
- Startup can attempt recovery from a valid backup when storage is empty or invalid, with older valid snapshots as fallbacks. This is best-effort recovery, not a guarantee against data loss.
- JSON stores **image filenames, not image bytes**. Preserve `images/` separately. For an external backup, stop the app first and copy the database, any remaining SQLite sidecars, `images/`, and `backups/` together.
- Deleting a clip from the live database does not erase copies already present in older backups. This is not a secure-erasure tool.

## Development and verification

Use a supported Python version and the installation steps above. No separate search server, model download, or third-party database service is needed.

### Isolated storage/search tests

These modules create temporary databases and do not need the real system clipboard:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fts5_search tests.test_storage_search_ranking
```

They cover FTS migration/index maintenance and search ordering/filter behavior. This is a useful first check, not proof of Windows paste/focus correctness.

### Broader tests and benchmark

**Run the full suite only in a disposable checkout with no personal runtime data.** Some tests use the default storage singleton and clear history/pinned records; GUI-related imports can also create runtime directories and logs. Do not run them against a working clipboard history you want to keep.

From that disposable checkout, after installing dependencies:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe tests\benchmark_search.py
```

`tests/benchmark_search.py` builds a temporary synthetic database and measures history/pinned search with its own regression threshold. Its numbers describe that workload and machine, not end-to-end popup latency. The test collection includes mocked/synthetic behavior checks; a green unit suite is not evidence that every application accepts a paste correctly.

For a Windows smoke test, use non-sensitive sample text and images: capture both, search and pin them, verify Alt mappings, assign an unused Ctrl key, run a harmless action such as inline `print('Hello from a clip')`, and restart to check persistence. Exercise paste separately in Notepad, Explorer rename/address fields, and your actual editor. Check hotkey conflicts and focus behavior in the desktop session where you intend to use the app.

## Current limitations

- Windows-specific desktop behavior; this is not a cross-platform clipboard backend or a packaged installer/startup service.
- Text and image storage, not full HTML/RTF/file-list fidelity or clipboard-format preservation.
- No semantic search, OCR, automatic transformations, encrypted storage, or application-specific capture exclusions.
- Fixed global hotkey registration can conflict with other software. Foreground restrictions and target-specific controls can prevent a successful paste.
- Action argument parsing is intentionally simple; action execution is trusted local code, not a sandbox or detached job manager.
- Backups live beside the application by default. Keep a separate protected copy if the disk or checkout matters.

## License

[MIT](LICENSE) — see the license file for terms and copyright notice.
