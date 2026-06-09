import time

from PyQt5.QtCore import QThread, pyqtSignal


BACKEND_NAMES = {
    "DSHOW": "DirectShow",
    "MSMF": "Media Foundation",
    "ANY": "Auto",
    "CKSDK": "CK SDK",
}


def cv_backend(cv2, backend_name):
    backend_name = (backend_name or "DSHOW").upper()
    if backend_name == "MSMF":
        return cv2.CAP_MSMF
    if backend_name == "ANY":
        return cv2.CAP_ANY
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

    def __init__(self, parent=None):
        super(CameraWorker, self).__init__(parent)
        self.camera_index = 0
        self.backend_name = "DSHOW"
        self._running = False
        self._capture = None

    def start(self, camera_index=0, backend_name="DSHOW"):
        self.camera_index = int(camera_index)
        self.backend_name = backend_name
        self._running = True
        super(CameraWorker, self).start()

    def stop(self):
        self._running = False
        self.wait(1200)

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

        while self._running:
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self.camera_error.emit("相机帧读取失败。")
                break
            self.frame_ready.emit(frame)
            time.sleep(0.03)

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
            while self._running:
                ok, frame = capture.read(1000)
                if not ok or frame is None:
                    self.camera_error.emit("CK 相机帧读取失败。")
                    break
                self.frame_ready.emit(frame)
                time.sleep(0.03)
        finally:
            capture.close()
            self._running = False
