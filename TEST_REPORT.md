# INDEPENDENT SOURCE BASELINE CHECK — advance-clipboard

Test environment: G6 Windows / Python 3.13.9, Node 26.7.0, Go 1.26.1 where applicable; source clone isolated. Tests are historical baseline checks, not validation of the future product.

| Check | Status | Duration |
|---|---|---:|
| clipboard_pytests | PASS | 19.64 s |
| clipboard_search_benchmark | PASS | 8.79 s |

## Limitations and observed failures

- **clipboard_pytests**: 165 passed, 18 subtests. One QTimer-deleted RuntimeError was printed in an atexit callback after pytest PASS; potential resource teardown defect remains.

- **clipboard_search_benchmark**: 25,000 rows, 50 iterations: recorded query p95 values below 25 ms on Windows G6. Synthetic DB benchmark, not a real user corpus.
- This test run used offscreen Qt for tests; user-visible Win32 clipboard hotkey/caret automation and large real profile NOT_RUN.

## Engineering instruction
Do not automatically claim production-ready based on these results. Audit capabilities, implement the new product, run independent tests on target hardware, fix observed issues, and record exact PASS/FAIL/NOT_RUN receipts.
