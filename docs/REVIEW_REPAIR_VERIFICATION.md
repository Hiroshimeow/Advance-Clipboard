# Review repair verification

## Scope

Fixes on top of `730f66e`, branch `fix/review-data-actions`:

- Worker backups and repository exports share one consistent SQLite read snapshot including actions, arguments, background flags and shortcuts.
- `INSERT OR IGNORE` metadata resolves the exact duplicate ID, never a stale lastrowid. Duplicate-only metadata updates still advance backup/search revisions.
- Inline Python uses `uv run python -c`; file scripts retain `uv run <script>` semantics and interpreter fallback.
- Shutdown runs from Qt aboutToQuit, tolerates deleted Qt wrappers, drains accepted ingestion work, flushes pending deletes before backup, and remains retryable on persistence failures.
- Revision/CAS backup acknowledgement preserves mutations made during snapshot/write.
- Tests use deterministic timestamps and Windows-safe script literals. Sandbox runner redirects database, image, backup and log paths before test import/construction.

## Fresh verification

Environment: Windows, Python 3.12.12, existing PyQt6 environment, Qt offscreen.

```powershell
python -m tests.run_isolated
# Ran 208 tests in 15.761s — OK
```

The command above uses `python` from an activated/synced environment, not the Windows Store alias. The actual verification used the explicit existing virtualenv interpreter.

Regression evidence includes temporary-database backup→restore metadata roundtrip, concurrent snapshot consistency, duplicate import ID routing and dirty marking, real inline/script subprocesses, blocked/queued ingestion drain, pending-delete retry, failed-final-backup retry, Qt cleanup and image-path sandbox assertions. The original baseline reproduced inline/Alt failures and a Windows script fixture error; added regressions were observed failing before fixes.

No Explorer F2/address-bar interaction or live clipboard/paste automation was performed; headless tests do not prove those native interactions. The sandbox runner substitutes final UI backup dispatch locally; separate worker tests cover real backup serialization with temporary databases. An ingestion processor that never returns can block graceful shutdown: accepted jobs are not silently abandoned after a fixed three-second timeout.

## Delivery safety

Push only a new branch to GitHub. Do not push to the checked-out local `origin/develop`, change receive policy, overwrite main/develop or deploy/restart the user's application. No real clipboard database or image collection is part of the repair commit.
