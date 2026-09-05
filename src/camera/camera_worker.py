import math
import time
from queue import Empty, Queue

from PyQt5.QtCore import QThread, pyqtSignal

from camera.picture_settings import normalize_picture_settings


BACKEND_NAMES = {
    "DSHOW": "DirectShow",
    "CKSDK": "CK SDK",
}


def cv_backend(cv2, backend_name):
    backend_name = (backend_name or "DSHOW").upper()
    return cv2.CAP_DSHOW


def discover_cameras(max_index=10, backend_name="DSHOW"):
    if backend_name == "CKSDK":
        from camera.ck_camera import discover_ck_cameras
        return discover_ck_cameras()

    cameras = []
    try:
        import cv2
    except Exception:
        return cameras

    backend = cv_backend(cv2, backend_name)
    for index in range(max_index):
        capture = cv2.VideoCapture(index, backend)
        if not capture.isOpened():
            capture.release()
            continue

        ok, frame = capture.read()
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()

        if ok and frame is not None:
            cameras.append({
                "index": index,
                "name": "相机 {}".format(index),
                "backend": backend_name,
                "width": width,
                "height": height,
            })

    return cameras


class CameraWorker(QThread):
    frame_ready = pyqtSignal(object)
    camera_error = pyqtSignal(str)
    settings_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super(CameraWorker, self).__init__(parent)
        self.camera_index = 0
        self.backend_name = "DSHOW"
        self._running = False
        self._capture = None
        self._frame_pending = False
        self._settings = normalize_picture_settings(None)
        self._commands = Queue()

    def start(self, camera_index=0, backend_name="DSHOW", settings=None):
        self.camera_index = int(camera_index)
        self.backend_name = backend_name
        self._settings = normalize_picture_settings(settings)
        self._running = True
        self._frame_pending = False
        super(CameraWorker, self).start()

    def stop(self):
        self._running = False
        self.wait(1200)
        self._frame_pending = False

    def mark_frame_consumed(self):
        self._frame_pending = False

    def update_settings(self, settings):
        self._commands.put(("settings", normalize_picture_settings(settings)))

    def open_driver_settings(self):
        self._commands.put(("driver_settings", None))

    def run(self):
        if self.backend_name == "CKSDK":
            self._run_ck_camera()
            return

        try:
            import cv2
        except Exception as exc:
            self.camera_error.emit("缺少 OpenCV，无法打开相机：" + str(exc))
            self._running = False
            return

        backend = cv_backend(cv2, self.backend_name)
        self._capture = cv2.VideoCapture(self.camera_index, backend)
        if not self._capture.isOpened():
            self.camera_error.emit(
                "无法打开 USB 相机 {}（{}），请检查设备、驱动或相机编号。".format(
                    self.camera_index,
                    BACKEND_NAMES.get(self.backend_name, self.backend_name),
                )
            )
            self._running = False
            return

        self._apply_opencv_settings(self._capture, self._settings)
        while self._running:
            self._process_commands(self._capture, "DSHOW")
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self.camera_error.emit("相机帧读取失败。")
                break
            if not self._frame_pending:
                self._frame_pending = True
                self.frame_ready.emit(frame)
            time.sleep(self._frame_delay())

        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._running = False

    def _run_ck_camera(self):
        try:
            from camera.ck_camera import CKCameraCapture
        except Exception as exc:
            self.camera_error.emit("无法加载 CK 相机 SDK：" + str(exc))
            self._running = False
            return

        capture = CKCameraCapture(self.camera_index)
        try:
            capture.open()
        except Exception as exc:
            self.camera_error.emit(str(exc))
            self._running = False
            return

        try:
            capture.apply_settings(self._settings)
            while self._running:
                self._process_commands(capture, "CKSDK")
                ok, frame = capture.read(1000)
                if not ok or frame is None:
                    self.camera_error.emit("CK 相机帧读取失败。")
                    break
                if not self._frame_pending:
                    self._frame_pending = True
                    self.frame_ready.emit(frame)
                time.sleep(self._frame_delay())
        finally:
            capture.close()
            self._frame_pending = False
            self._running = False

    def _process_commands(self, capture, backend_name):
        latest_settings = None
        open_driver_settings = False
        while True:
            try:
                command, value = self._commands.get_nowait()
            except Empty:
                break
            if command == "settings":
                latest_settings = value
            elif command == "driver_settings":
                open_driver_settings = True

        if latest_settings is not None:
            self._settings = latest_settings
            if backend_name == "CKSDK":
                capture.apply_settings(self._settings)
            else:
                self._apply_opencv_settings(capture, self._settings)
            self.settings_message.emit("画面硬件参数已应用。")

        if open_driver_settings:
            if backend_name == "CKSDK":
                capture.show_settings()
            else:
                try:
                    import cv2

                    capture.set(cv2.CAP_PROP_SETTINGS, 1)
                except Exception as exc:
                    self.settings_message.emit("无法打开相机驱动设置：" + str(exc))

    def _apply_opencv_settings(self, capture, settings):
        try:
            import cv2

            auto_value = 0.75 if settings["auto_exposure"] else 0.25
            capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, auto_value)
            if not settings["auto_exposure"]:
                exposure_seconds = max(0.0001, settings["exposure_time_ms"] / 1000.0)
                capture.set(cv2.CAP_PROP_EXPOSURE, max(-13, min(0, round(math.log(exposure_seconds, 2)))))
            capture.set(cv2.CAP_PROP_GAIN, settings["gain"])
            if hasattr(cv2, "CAP_PROP_AUTO_WB"):
                capture.set(cv2.CAP_PROP_AUTO_WB, int(bool(settings["auto_balance"])))
            if settings["fps_enabled"]:
                capture.set(cv2.CAP_PROP_FPS, settings["fps"])
        except Exception as exc:
            self.settings_message.emit("相机不支持部分硬件参数：" + str(exc))

    def _frame_delay(self):
        if self._settings["fps_enabled"]:
            return max(0.001, 1.0 / self._settings["fps"])
        return 0.03
