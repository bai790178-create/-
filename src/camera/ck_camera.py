import ctypes
import os
import sys
from pathlib import Path


SDK_DLL_NAME = "CKCameraDLL_X64.dll"
PACKAGED_SDK_DIR = "sdk"
REFERENCE_SDK_DIR = Path("D:/纹影声场可视化及声悬浮实验平台教学及数据处理软件")
_DLL_DIRECTORY_HANDLES = []


class CKDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("acProductSeries", ctypes.c_char * 32),
        ("acProductName", ctypes.c_char * 32),
        ("acFriendlyName", ctypes.c_char * 32),
        ("acLinkName", ctypes.c_char * 32),
        ("acDriverName", ctypes.c_char * 32),
        ("acDriverVersion", ctypes.c_char * 32),
        ("acSensorType", ctypes.c_char * 32),
        ("acPortType", ctypes.c_char * 32),
        ("uInstance", ctypes.c_uint),
        ("acSn", ctypes.c_char * 33),
        ("VendorID", ctypes.c_ushort),
        ("DeviceID", ctypes.c_ushort),
        ("DeviceVersionID", ctypes.c_ushort),
        ("SymbolicName", ctypes.c_char * 128),
        ("Name", ctypes.c_char * 64),
    ]


class CKImageInfo(ctypes.Structure):
    _fields_ = [
        ("iWidth", ctypes.c_uint),
        ("iHeight", ctypes.c_uint),
        ("TotalBytes", ctypes.c_uint),
        ("uiMediaType", ctypes.c_uint),
        ("ExpTime", ctypes.c_double),
        ("ExpLineTime", ctypes.c_double),
        ("Gain", ctypes.c_uint),
    ]


def _decode_bytes(value):
    raw = bytes(value).split(b"\0", 1)[0]
    for encoding in ("gbk", "utf-8", "latin1"):
        try:
            return raw.decode(encoding)
        except Exception:
            pass
    return ""


def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _sdk_candidates():
    env_dir = os.environ.get("CK_CAMERA_SDK_DIR")
    if env_dir:
        yield Path(env_dir)

    app_dir = _app_dir()
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        yield Path(bundle_dir) / PACKAGED_SDK_DIR
        yield Path(bundle_dir)
    yield app_dir / PACKAGED_SDK_DIR
    yield app_dir
    yield Path.cwd() / PACKAGED_SDK_DIR
    yield Path.cwd()
    yield REFERENCE_SDK_DIR


def _resolved_sdk_candidates():
    seen = set()
    for candidate in _sdk_candidates():
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        yield resolved


def _sdk_dir():
    for candidate in _resolved_sdk_candidates():
        if (candidate / SDK_DLL_NAME).exists():
            return candidate
    return None


def _sdk_search_message():
    paths = [str(candidate) for candidate in _resolved_sdk_candidates()]
    return "；".join(paths)


def _add_sdk_dll_directories(sdk_dir):
    dll_dirs = [sdk_dir]
    try:
        dll_dirs.extend(path.parent for path in sdk_dir.rglob("*.dll"))
    except OSError:
        pass

    seen = set()
    for dll_dir in dll_dirs:
        key = str(dll_dir).lower()
        if key in seen:
            continue
        seen.add(key)
        if hasattr(os, "add_dll_directory"):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")


def _load_sdk():
    sdk_dir = _sdk_dir()
    if sdk_dir is None:
        raise RuntimeError(
            "未找到 CK 相机 SDK。请将 {} 放到程序 sdk 文件夹，或设置 CK_CAMERA_SDK_DIR。已查找：{}".format(
                SDK_DLL_NAME,
                _sdk_search_message(),
            )
        )
    _add_sdk_dll_directories(sdk_dir)
    sdk_dll = sdk_dir / SDK_DLL_NAME
    try:
        return ctypes.WinDLL(str(sdk_dll))
    except OSError as exc:
        raise RuntimeError(
            "CK 相机 SDK 加载失败：{}。请确认 sdk 文件夹内包含相机运行所需的全部 DLL。SDK 路径：{}".format(
                exc,
                sdk_dir,
            )
        )


def _configure_sdk(dll):
    dll.CameraEnumerateDevice.argtypes = [ctypes.POINTER(ctypes.c_int)]
    dll.CameraEnumerateDevice.restype = ctypes.c_int
    dll.CameraInit.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    dll.CameraInit.restype = ctypes.c_int
    dll.CameraPlay.argtypes = [ctypes.c_void_p]
    dll.CameraPlay.restype = ctypes.c_int
    dll.CameraGetImageBufferEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(CKImageInfo), ctypes.c_uint]
    dll.CameraGetImageBufferEx.restype = ctypes.c_void_p
    dll.CameraUnInit.argtypes = [ctypes.c_void_p]
    dll.CameraUnInit.restype = ctypes.c_int


def discover_ck_cameras():
    try:
        dll = _load_sdk()
        _configure_sdk(dll)
        count = ctypes.c_int(0)
        ret = dll.CameraEnumerateDevice(ctypes.byref(count))
        if ret != 0 or count.value <= 0:
            return []

        cameras = []
        for index in range(count.value):
            cameras.append({
                "index": index,
                "name": "CK SDK 相机 {}".format(index),
                "backend": "CKSDK",
                "width": 0,
                "height": 0,
            })
        return cameras
    except Exception:
        return []


class CKCameraCapture(object):
    def __init__(self, camera_index=0):
        self.camera_index = int(camera_index)
        self.dll = None
        self.handle = ctypes.c_void_p()

    def open(self):
        self.dll = _load_sdk()
        _configure_sdk(self.dll)
        count = ctypes.c_int(0)
        ret = self.dll.CameraEnumerateDevice(ctypes.byref(count))
        if ret != 0 or count.value <= self.camera_index:
            raise RuntimeError("未检测到 CK 相机 {}，错误码 {}。".format(self.camera_index, ret))

        ret = self.dll.CameraInit(ctypes.byref(self.handle), self.camera_index)
        if ret != 0:
            raise RuntimeError("CK 相机初始化失败，错误码 {}。".format(ret))
        ret = self.dll.CameraPlay(self.handle)
        if ret != 0:
            self.close()
            raise RuntimeError("CK 相机启动采集失败，错误码 {}。".format(ret))

    def read(self, timeout_ms=1000):
        if not self.handle:
            return False, None
        import numpy as np

        info = CKImageInfo()
        ptr = self.dll.CameraGetImageBufferEx(self.handle, ctypes.byref(info), int(timeout_ms))
        if not ptr or info.iWidth <= 0 or info.iHeight <= 0 or info.TotalBytes <= 0:
            return False, None

        width = int(info.iWidth)
        height = int(info.iHeight)
        total = int(info.TotalBytes)
        channels = max(1, total // max(1, width * height))
        buffer_type = ctypes.c_ubyte * total
        data = np.frombuffer(buffer_type.from_address(ptr), dtype=np.uint8).copy()

        if channels >= 4:
            frame = data.reshape((height, width, channels))[:, :, :3]
        elif channels == 3:
            frame = data.reshape((height, width, 3))
        else:
            frame = data.reshape((height, width))
        return True, frame

    def close(self):
        if self.dll is not None and self.handle:
            self.dll.CameraUnInit(self.handle)
        self.handle = ctypes.c_void_p()
        self.dll = None
