import os
import sys
import ctypes
import shutil


if getattr(sys, "frozen", False):
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    ROOT_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT_DIR = BUNDLE_DIR

SRC_DIR = os.path.join(BUNDLE_DIR, "src")
VENV_DIR = os.path.join(ROOT_DIR, ".venv314")
PYQT5_DIR = os.path.join(VENV_DIR, "Lib", "site-packages", "PyQt5")
if getattr(sys, "frozen", False):
    PYQT5_DIR = os.path.join(BUNDLE_DIR, "PyQt5")
QT5_DIR = os.path.join(PYQT5_DIR, "Qt5")
QT_BIN_DIR = os.path.join(QT5_DIR, "bin")
QT_PLUGIN_DIR = os.path.join(QT5_DIR, "plugins")
QT_PLATFORM_DIR = os.path.join(QT_PLUGIN_DIR, "platforms")


def _is_ascii_path(path):
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _writable_ascii_runtime_dir():
    candidates = []
    drive = os.path.splitdrive(ROOT_DIR)[0] or os.environ.get("SystemDrive", "C:")
    if drive:
        candidates.append(os.path.join(drive + os.sep, "UltrasonicGratingQtRuntime"))
    candidates.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp", "UltrasonicGratingQtRuntime"))

    for candidate in candidates:
        if not _is_ascii_path(candidate):
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".write-test")
            with open(probe, "w", encoding="ascii") as handle:
                handle.write("ok")
            os.remove(probe)
            return candidate
        except OSError:
            pass
    return None


def _qt_plugin_dir():
    if not os.path.isdir(QT_PLATFORM_DIR):
        return QT_PLUGIN_DIR, QT_PLATFORM_DIR
    if _is_ascii_path(QT_PLUGIN_DIR):
        return QT_PLUGIN_DIR, QT_PLATFORM_DIR

    runtime_dir = _writable_ascii_runtime_dir()
    if runtime_dir is None:
        return QT_PLUGIN_DIR, QT_PLATFORM_DIR

    staged_plugin_dir = os.path.join(runtime_dir, "plugins")
    staged_platform_dir = os.path.join(staged_plugin_dir, "platforms")
    os.makedirs(staged_platform_dir, exist_ok=True)
    for filename in os.listdir(QT_PLATFORM_DIR):
        if filename.lower().endswith(".dll"):
            source = os.path.join(QT_PLATFORM_DIR, filename)
            target = os.path.join(staged_platform_dir, filename)
            try:
                if not os.path.exists(target) or os.path.getmtime(source) > os.path.getmtime(target):
                    shutil.copy2(source, target)
            except OSError:
                return QT_PLUGIN_DIR, QT_PLATFORM_DIR
    return staged_plugin_dir, staged_platform_dir


QT_RUNTIME_PLUGIN_DIR, QT_RUNTIME_PLATFORM_DIR = _qt_plugin_dir()

if os.path.isdir(QT_RUNTIME_PLUGIN_DIR):
    os.environ["QT_PLUGIN_PATH"] = QT_RUNTIME_PLUGIN_DIR
if os.path.isdir(QT_RUNTIME_PLATFORM_DIR):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = QT_RUNTIME_PLATFORM_DIR
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

    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtWidgets import QApplication
    from ui.main_window import MainWindow

    if os.path.isdir(QT_RUNTIME_PLUGIN_DIR):
        QCoreApplication.addLibraryPath(QT_RUNTIME_PLUGIN_DIR)
    app = QApplication(sys.argv)
    app.setApplicationName("超声光栅实验辅助平台")
    window = MainWindow(project_root=ROOT_DIR, bundle_root=BUNDLE_DIR)
    window.show()
    try:
        return app.exec_()
    finally:
        release_single_instance_lock(lock_handle)


if __name__ == "__main__":
    sys.exit(main())
