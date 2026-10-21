# MASTER MISSION — ADVANCE CLIPBOARD: A FAST, PRIVATE, INTELLIGENT CLIPBOARD WORKSPACE

## Rebuild, extend and optimize a dependable Windows clipboard companion

Bạn là Principal Desktop Engineer, Data/Search Engineer, Interaction Designer và Independent QA/Release Owner. Hãy dùng source hiện có làm bằng chứng, sau đó **refactor và nâng cấp Advance Clipboard toàn diện**. Mục tiêu: một clipboard workspace **nhẹ, nhanh, đáng tin, riêng tư, thông minh và tùy biến cao**; không đơn thuần là cửa sổ liệt kê lịch sử sao chép. Người dùng cần lưu, tìm, tổ chức, preview, chuyển đổi, tự động hóa và tái sử dụng nhiều loại dữ liệu trong công việc hằng ngày mà không phải đánh đổi hiệu năng, tính chính xác hay an toàn thông tin.

Bạn hoàn toàn tự do chọn Python/PyQt, C#/WinUI, Rust/Tauri, Go hoặc bất kỳ runtime/UI/storage phù hợp; SQLite FTS5, lexical, semantic hay hybrid retrieval cũng chỉ là lựa chọn kỹ thuật. Được dùng libraries trưởng thành, viết lại toàn bộ code hoặc tạo hệ plugin mới nếu mang lại lợi ích đo được. **Không bị giới hạn vào README/FUTURE.md hiện tại.** Tuy nhiên không được âm thầm làm mất những năng lực thật đang sử dụng.

### Four invariant product outcomes
1. **Windows-native correctness**: capture đúng clipboard formats và clipboard owner lifetime, paste đúng nơi người dùng chọn, hotkey ổn định, foreground/focus và IME hợp lệ, không nhân bản thao tác hoặc ghi đè dữ liệu người dùng ngoài ý muốn.
2. **Rapid large-scale UX**: popup xuất hiện nhanh, search/filter/pagination mượt với hàng chục/nghìn đến hàng trăm nghìn clips, idle CPU/RAM thấp, không chặn desktop.
3. **Data privacy and durability**: local-first, chính sách không capture secrets/password manager, backup/restore/versioning, clip purge/retention, migration, crash recovery, optional encryption.
4. **Honest verification**: unit and DB tests plus real Windows clipboard/UI/keyboard/foreground E2E; performance on reproducible workloads, no fake PASS from mocked clipboard.

### Inventory first
Read the true code, tests and Git history: clipboard_monitor/clipboard_ingest, single_instance, Win32 API, SQLite WAL/FTS5, ranking and filters, hashes/dedup, pinned clips/tags/groups, preview, context menu, keyboard navigation, backup worker and checksum restoration, UI models/delegates, tests and benchmark_search.py. Build a capability parity matrix from executable behavior; note which old tests are synthetic vs physical clipboard. Exclude old .plan handoffs; historical notes may be inspected in Git but do not constrain new architecture.

### Discover the next-generation product
- Rich formats: text (Unicode, HTML/RTF), images, files/paths, code, URLs, multi-item clips with type-aware preview and conversion; preserve original payload fidelity and format priority without making SQLite grow unbounded.
- Thoughtful search: exact phrase, lexical FTS, tags, groups, types, source application, dates, favorites, usage frequency and multilingual/code-aware ranking. Optionally semantic search/model-assisted organization only if measured benefit exceeds latency/CPU/storage/token cost.
- Intelligent workflow: dynamic templates/snippets, paste transformations, variable placeholders, clipboard chains, QR, OCR from images, format cleanup, shortcuts, optional macros and skill/plugin recipes; verify transform output before destructive replacement.
- Adaptive organization: auto-clustering, related clips, dedup/supersession, reusable clip sets by project, useful history and corrections, transparent ranking explanation and user control, without inventing an intrusive always-on AI dependency.
- UX: instant keyboard-first palette, reliable global hotkey, fast previews of long texts/images, pin/unpin, tags/groups, multi-monitor/DPI, accessible rows, predictable selection/paste, configurable theme, optional tray and startup, explicit bulk actions.
- Security & privacy: configurable app denylist, pause recording, incognito mode, sensitive-content filters, one-time clips/expiry, encrypted export/backup, per-app clipboard behavior, no automatic cloud transfer or exfiltration. Passwords/OTPs deserve minimum retention and clear user controls.
- Reliability: single instance, bounded multi-format ingest queue, retry when clipboard busy, safe image writes, transaction consistency, race-free concurrent copy/paste, crash-safe backups, atomic restores and resilient migration from old DB.
- Extensible integrations with Windows utilities/MCP/desktop pet/screen assistant where demonstrably useful, but core remains functional offline without any AI model.

This is a horizon, not a feature mandate. Innovate freely and prefer small robust designs when they win in real use.

### Verification and engineering
- Characterize Windows clipboard formats and OLE owner lifecycle, file copy/drag, UTF-16/UTF-8, emoji/IME, large text/image, file paths, mixed monitors, hotkey conflict, window focus and clipboard restoration.
- Test paste EXACTLY ONCE at intended target; if target changed or unsafe, refuse clearly rather than guessing. Avoid wrong-window injection and unintended AutoEnter.
- Database: SQLite WAL, migration, FTS correctness, dedup/rank, long content and large image quarantine, 0/10k/25k/100k items, power-loss/force-kill/backup corruption, restore idempotence and key retention.
- GUI performance: p50/p95 popup latency, search 25k+ rows, memory, CPU idle, import/export throughput, image preview lag and regression under concurrent capture.
- Security negatives: password manager sources, clipboard blocked/canceled, disallowed apps, private/incognito clips, restore encrypted backup with wrong credentials, no cloud/privacy leak.
- If agent is offline Linux, complete portable code/database tests and provide Windows E2E scripts; mark native Win32 GUI/clipboard proof NOT_RUN until real Windows verification. Do not confuse synthetic frame/UI harness with hardware pass.

### Deliverable and operating boundaries
Create **one independent new Git repo** with source/tests, install and stable Windows launcher, data migration strategy and release packaging. Implement at least:
Install → clipboard listener → copy text/image → indexed history → instant filter/search → preview/pin/group → paste back once → restart → preserved history → backup/restore integrity.

Run real tests with logs and quantified performance; preserve useful legacy capabilities or document tested replacements. Include ARCHITECTURE, CAPABILITY_PARITY, DATABASE_SCHEMA_MIGRATION, PRIVACY_MODEL, WINDOWS_E2E, TEST_RECEIPTS, BENCHMARKS, UX and INSTALL/ROLLBACK. Export full-repo ZIP with .git and runnable release artifact, both extraction-verified and containing no actual user clipboard database/backups or credentials.

**Do not stop at speculation or a list of future features. Build something fast, safe, ergonomic and clearly superior by evidence; creative autonomy is yours.**