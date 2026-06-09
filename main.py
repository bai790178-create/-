import os
import sys
import ctypes


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
VENV_DIR = os.path.join(ROOT_DIR, ".venv314")
PYQT5_DIR = os.path.join(VENV_DIR, "Lib", "site-packages", "PyQt5")
QT5_DIR = os.path.join(PYQT5_DIR, "Qt5")
QT_BIN_DIR = os.path.join(QT5_DIR, "bin")
QT_PLUGIN_DIR = os.path.join(QT5_DIR, "plugins")
QT_PLATFORM_DIR = os.path.join(QT_PLUGIN_DIR, "platforms")

if os.path.isdir(QT_PLUGIN_DIR):
    os.environ.setdefault("QT_PLUGIN_PATH", QT_PLUGIN_DIR)
if os.path.isdir(QT_PLATFORM_DIR):
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", QT_PLATFORM_DIR)
if os.path.isdir(QT_BIN_DIR):
    os.environ["PATH"] = QT_BIN_DIR + os.pathsep + os.environ.get("PATH", "")

LOCK_PATH = os.path.join(ROOT_DIR, "experiments", ".app.lock")


def is_pid_running(pid):
    if pid <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process = kernel32.OpenProcess(0x1000, False, int(pid))
    if not process:
        return False
    kernel32.CloseHandle(process)
    return True


def acquire_single_instance_lock():
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY

    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, flags)
            os.write(fd, str(os.getpid()).encode("ascii"))
            return fd
        except FileExistsError:
            try:
                with open(LOCK_PATH, "r", encoding="utf-8") as handle:
                    existing_pid = int((handle.read() or "0").strip())
            except Exception:
                existing_pid = 0

            if is_pid_running(existing_pid):
                return None

            try:
                os.remove(LOCK_PATH)
            except OSError:
                return None

    return None


def release_single_instance_lock(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    finally:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


def main():
    lock_handle = acquire_single_instance_lock()
    if lock_handle is None:
        return 0

    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)

    from PyQt5.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("超声光栅实验辅助平台")
    window = MainWindow(project_root=ROOT_DIR)
    window.show()
    try:
        return app.exec_()
    finally:
        release_single_instance_lock(lock_handle)


if __name__ == "__main__":
    sys.exit(main())
