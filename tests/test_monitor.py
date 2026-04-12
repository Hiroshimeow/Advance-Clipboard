import time
import sqlite3
import os
import sys

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

# Import our new Win32 monitor
try:
    from win32_monitor import Win32ClipboardMonitor
except ImportError as e:
    print(f"Error importing: {e}")
    sys.exit(1)


def get_db_count():
    db_path = os.path.join(ROOT_DIR, "clipboard.db")
    if not os.path.exists(db_path):
        return -1
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clips")
        count = cursor.fetchone()[0]

        # Get the latest clip
        cursor.execute(
            "SELECT id, type, content FROM clips ORDER BY updated_at DESC LIMIT 1"
        )
        latest = cursor.fetchone()

        conn.close()
        return count, dict(latest) if latest else None
    except Exception as e:
        print(f"DB Error: {e}")
        return -1, None


def on_change():
    print(
        f"[{time.strftime('%H:%M:%S')}] 🔔 SIGNAL RECEIVED: clipboard_changed (from Win32 message loop)"
    )
    # Print the current clipboard content according to Qt
    clipboard = QApplication.clipboard()
    mime = clipboard.mimeData()
    if mime.hasText():
        print(f"   -> Qt Clipboard says text: {mime.text()[:50]}...")
    elif mime.hasImage():
        print(f"   -> Qt Clipboard says image")
    else:
        print(f"   -> Qt Clipboard empty or unknown format")


def on_hotkey():
    print(
        f"[{time.strftime('%H:%M:%S')}] ⌨️ SIGNAL RECEIVED: hotkey_toggle (Ctrl+Alt+V pressed)"
    )


def main():
    print("=== ADVANCED CLIPBOARD DIAGNOSTICS ===")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Start our new Win32 monitor
    monitor = Win32ClipboardMonitor()
    monitor.clipboard_changed.connect(on_change)
    monitor.hotkey_toggle.connect(on_hotkey)
    monitor.start()

    last_count, last_item = get_db_count()
    print(f"Initial DB clips count: {last_count}")
    print(f"Initial latest item: {last_item['content'][:50] if last_item else 'None'}")
    print("--------------------------------------------------")
    print("INSTRUCTIONS: Please copy some text (Ctrl+C) and press Ctrl+Alt+V")
    print("Waiting for events for 30 seconds...\n")

    def check_db():
        nonlocal last_count, last_item
        current_count, current_item = get_db_count()
        if current_count != last_count or (
            current_item and last_item and current_item["id"] != last_item["id"]
        ):
            print(
                f"[{time.strftime('%H:%M:%S')}] 💾 DB UPDATED: count={current_count}, new item={current_item['content'][:50]}..."
            )
            last_count = current_count
            last_item = current_item

    # Poll DB every second
    timer = QTimer()
    timer.timeout.connect(check_db)
    timer.start(1000)

    # Exit after 30 seconds
    QTimer.singleShot(30000, lambda: [print("\nTest completed."), app.quit()])

    app.exec()
    monitor.stop()


if __name__ == "__main__":
    main()
