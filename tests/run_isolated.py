"""Run unittest verification without touching application data.

Usage: python -m tests.run_isolated [tests.test_clip_actions ...]
All application paths are redirected before importing any test module.
"""
import atexit
import gc
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="clipboard-tests-") as directory:
        sandbox = Path(directory)
        import storage
        import storage.db as db
        import storage.backup as backup
        db.close_connection()
        db.DB_FILE = str(sandbox / "clipboard.db")
        storage.DB_FILE = db.DB_FILE
        storage._storage = None
        storage.ClipboardStorage._need_backup = False
        backup.BACKUP_DIR = str(sandbox / "backups")
        # widgets imports clip_row at module end, which captures IMAGE_DIR.
        # Give widgets its sandbox identity before executing any UI imports so
        # every alias (delegate, row, preview, context menu) starts isolated.
        import ui
        widgets_spec = importlib.util.spec_from_file_location("ui.widgets", root / "ui" / "widgets.py")
        widgets = importlib.util.module_from_spec(widgets_spec)
        widgets.__file__ = str(sandbox / "ui" / "widgets.py")
        sys.modules["ui.widgets"] = widgets
        ui.widgets = widgets
        exec(compile((root / "ui" / "widgets.py").read_bytes(), str(root / "ui" / "widgets.py"), "exec"), widgets.__dict__)
        # main derives import-time log/image/legacy paths from __file__.
        # Execute the original source with a sandbox file identity, not a copy.
        spec = importlib.util.spec_from_file_location("main", root / "main.py")
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(sandbox / "main.py")
        sys.modules["main"] = module
        exec(compile((root / "main.py").read_bytes(), str(root / "main.py"), "exec"), module.__dict__)
        # The worker uses its own module constants in a new interpreter. Keep
        # lifecycle verification local rather than allowing real backup workers.
        def local_backup(db_file=None):
            return bool(backup.create_backup(storage.get_storage().get_all_clips()))
        module.create_backup_in_subprocess = local_backup
        loader = unittest.defaultTestLoader
        suite = (loader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1
                 else loader.discover(str(root / "tests"), top_level_dir=str(root)))
        try:
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        finally:
            from PyQt6.QtWidgets import QApplication
            from PyQt6 import sip
            app = QApplication.instance()
            # Closed/deleted Qt clients can still be retained by Python cycles
            # and atexit; topLevelWidgets alone does not find those producers.
            clients = [obj for obj in gc.get_objects() if isinstance(obj, module.ClientApp)]
            for client in clients:
                client._cleanup_on_exit()
                atexit.unregister(client._cleanup_on_exit)
            if app:
                for widget in app.topLevelWidgets():
                    if not sip.isdeleted(widget):
                        widget.close()
            clients.clear()
            if storage._storage:
                storage._storage.set_backup_callback(None)
            storage._storage = None
            storage.ClipboardStorage._need_backup = False
            db.close_connection()
            # sqlite Connection objects may be in reference cycles after worker
            # thread-local teardown; collect them before Windows removes the DB.
            gc.collect()
            import faulthandler
            faulthandler.disable()
            module._fault_log_handle.close()
            for handler in list(module.logger.handlers):
                handler.close()
                module.logger.removeHandler(handler)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
