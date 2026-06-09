import os
from datetime import datetime
from collections import deque

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from analysis.stripe_analyzer import StripeAnalyzer
from camera.camera_worker import BACKEND_NAMES, CameraWorker, discover_cameras
from storage.experiment_store import ExperimentStore


class MainWindow(QMainWindow):
    def __init__(self, project_root):
        super(MainWindow, self).__init__()
        self.project_root = project_root
        self.assets_dir = os.path.join(project_root, "assets")
        self.experiments_dir = os.path.join(project_root, "experiments")
        self.analyzer = StripeAnalyzer()
        self.store = ExperimentStore(self.experiments_dir)
        self.camera_worker = None
        self.current_frame = None
        self.current_result = None
        self.realtime_enabled = False
        self.last_analysis_ms = 0
        self.available_cameras = []
        self.realtime_results = deque(maxlen=7)
        self.stable_spacing_px = None

        self.setWindowTitle("超声光栅实验辅助平台")
        self.resize(1420, 900)
        self._build_ui()
        self._apply_style()
        self._connect_actions()
        self._log("程序启动。")
        self.refresh_cameras()
        if self.analyzer.dependency_error:
            self._log("OpenCV/NumPy 未就绪，请运行 setup_env.ps1 初始化项目环境。")

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("mainSurface")
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)
        root.addWidget(self._build_titlebar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("contentSplitter")
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([980, 390])
        root.addWidget(splitter, 1)

        bottom = QSplitter(Qt.Horizontal)
        bottom.setObjectName("bottomSplitter")
        bottom.addWidget(self._build_scan_panel())
        bottom.addWidget(self._build_log_panel())
        bottom.setStretchFactor(0, 2)
        bottom.setStretchFactor(1, 1)
        bottom.setSizes([930, 430])
        root.addWidget(bottom)
        self.setCentralWidget(central)
        self._build_menu()

    def _build_titlebar(self):
        box = QFrame()
        box.setObjectName("titleBar")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        mark = QLabel("4f")
        mark.setObjectName("titleMark")
        mark.setFixedSize(48, 48)
        mark.setAlignment(Qt.AlignCenter)
        title = QLabel("超声光栅实验辅助平台")
        title.setObjectName("appTitle")
        subtitle = QLabel("USB 相机采集、条纹中心距识别、实验参数与记录管理")
        subtitle.setObjectName("mutedText")
        title_group = QVBoxLayout()
        title_group.setSpacing(3)
        title_group.addWidget(title)
        title_group.addWidget(subtitle)
        self.camera_status = QLabel("相机状态：未连接")
        self.resolution_status = QLabel("分辨率：--")
        self.analysis_status = QLabel("分析：待机")
        for label in (self.camera_status, self.resolution_status, self.analysis_status):
            label.setObjectName("statusPill")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumWidth(118)
        layout.addWidget(mark)
        layout.addLayout(title_group, 1)
        layout.addWidget(self.camera_status)
        layout.addWidget(self.resolution_status)
        layout.addWidget(self.analysis_status)
        return box

    def _build_left_panel(self):
        panel = QGroupBox("图像预览")
        panel.setObjectName("previewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 26, 16, 16)
        layout.setSpacing(12)
        self.preview_label = QLabel("导入图片或打开 USB 相机")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(760, 520)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.preview_label, 1)

        camera_bar = QHBoxLayout()
        camera_bar.setSpacing(8)
        self.backend_select = QComboBox()
        self.backend_select.addItem("DirectShow", "DSHOW")
        self.backend_select.addItem("Media Foundation", "MSMF")
        self.backend_select.addItem("自动", "ANY")
        self.backend_select.addItem("CK SDK", "CKSDK")
        camera_label = QLabel("相机选择")
        camera_label.setObjectName("fieldLabel")
        camera_bar.addWidget(camera_label)
        self.camera_select = QComboBox()
        self.camera_select.addItem("相机 0", 0)
        self.refresh_camera_btn = QPushButton("刷新相机")
        self.refresh_camera_btn.setObjectName("ghostButton")
        camera_bar.addWidget(self.backend_select)
        camera_bar.addWidget(self.camera_select, 1)
        camera_bar.addWidget(self.refresh_camera_btn)
        layout.addLayout(camera_bar)
        return panel

    def _build_control_panel(self):
        panel = QGroupBox("执行控制")
        panel.setObjectName("controlPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(14, 26, 14, 14)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)

        self.open_camera_btn = QPushButton("打开相机")
        self.stop_camera_btn = QPushButton("关闭相机")
        self.capture_btn = QPushButton("拍照")
        self.import_btn = QPushButton("导入光栅图")
        self.demo_btn = QPushButton("载入示意图")
        self.realtime_btn = QPushButton("开始实时分析")
        self.save_btn = QPushButton("保存实验")
        self.open_camera_btn.setObjectName("primaryButton")
        self.realtime_btn.setObjectName("primaryButton")
        self.save_btn.setObjectName("accentButton")
        self.stop_camera_btn.setObjectName("secondaryButton")
        self.capture_btn.setObjectName("secondaryButton")
        self.import_btn.setObjectName("secondaryButton")
        self.demo_btn.setObjectName("secondaryButton")
        self._decorate_button(self.open_camera_btn, QStyle.SP_MediaPlay)
        self._decorate_button(self.stop_camera_btn, QStyle.SP_MediaStop)
        self._decorate_button(self.capture_btn, QStyle.SP_DialogYesButton)
        self._decorate_button(self.import_btn, QStyle.SP_DialogOpenButton)
        self._decorate_button(self.demo_btn, QStyle.SP_FileDialogDetailedView)
        self._decorate_button(self.realtime_btn, QStyle.SP_BrowserReload)
        self._decorate_button(self.save_btn, QStyle.SP_DialogSaveButton)

        layout.addWidget(self.open_camera_btn, 0, 0)
        layout.addWidget(self.stop_camera_btn, 0, 1)
        layout.addWidget(self.capture_btn, 0, 2)
        layout.addWidget(self.import_btn, 1, 0)
        layout.addWidget(self.demo_btn, 1, 1)
        layout.addWidget(self.realtime_btn, 1, 2)
        layout.addWidget(self.analyze_btn, 2, 0, 1, 2)
        layout.addWidget(self.save_btn, 2, 2)
        return panel

    def _build_right_panel(self):
        panel = QWidget()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        params = QGroupBox("实验参数")
        params.setObjectName("paramsPanel")
        form = QFormLayout(params)
        form.setContentsMargins(14, 24, 14, 14)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.analysis_type = QComboBox()
        self.analysis_type.addItems(["光栅条纹分析", "频率稳定性估算", "材料状态评估"])
        self.pixel_scale = QLineEdit("0.65")
        self.material = QComboBox()
        self.material.addItems(["明胶", "琼脂", "卡拉胶", "结冷胶"])
        self.concentration = QLineEdit("4.0")
        self.frequency = QLineEdit("1.200")
        self.temperature = QLineEdit("18.0")
        self.duration = QLineEdit("30")
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(86)
        form.addRow("分析类型", self.analysis_type)
        form.addRow("像素当量 (um/px)", self.pixel_scale)
        form.addRow("材料类型", self.material)
        form.addRow("质量浓度 (%)", self.concentration)
        form.addRow("超声频率 (MHz)", self.frequency)
        form.addRow("样品温度 (°C)", self.temperature)
        form.addRow("观测时间 (min)", self.duration)
        form.addRow("实验备注", self.notes)
        results = QGroupBox("分析结果")
        results.setObjectName("resultsPanel")
        result_layout = QFormLayout(results)
        result_layout.setContentsMargins(14, 24, 14, 14)
        result_layout.setHorizontalSpacing(12)
        result_layout.setVerticalSpacing(10)
        self.result_spacing = QLabel("--")
        self.result_spacing.setObjectName("heroResult")
        self.result_spacing.setAlignment(Qt.AlignCenter)
        self.result_spacing.setMinimumHeight(52)
        self.result_bright = QLabel("--")
        self.result_dark = QLabel("--")
        self.result_clarity = QLabel("--")
        self.result_confidence = QLabel("--")
        self.result_state = QLabel("待机")
        self.result_state.setWordWrap(True)
        for label in (self.result_bright, self.result_dark, self.result_clarity, self.result_confidence, self.result_state):
            label.setObjectName("resultValue")
        result_layout.addRow("条纹中心距", self.result_spacing)
        result_layout.addRow("亮纹中心距", self.result_bright)
        result_layout.addRow("暗纹中心距", self.result_dark)
        result_layout.addRow("清晰度", self.result_clarity)
        result_layout.addRow("置信度", self.result_confidence)
        result_layout.addRow("状态", self.result_state)
        self.analyze_btn = QPushButton("分析当前图像")
        self.analyze_btn.setObjectName("accentButton")
        self._decorate_button(self.analyze_btn, QStyle.SP_ComputerIcon)
        self._decorate_button(self.refresh_camera_btn, QStyle.SP_BrowserReload)
        layout.addWidget(results)
        layout.addWidget(params)
        layout.addWidget(self._build_control_panel())
        layout.addStretch(1)
        return panel

    def _build_scan_panel(self):
        panel = QGroupBox("中心/ROI 强度扫描")
        panel.setObjectName("scanPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 24, 14, 14)
        self.scan_label = QLabel("导入图像后显示投影曲线")
        self.scan_label.setObjectName("scanLabel")
        self.scan_label.setMinimumHeight(170)
        self.scan_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.scan_label)
        return panel

    def _build_log_panel(self):
        panel = QGroupBox("运行日志")
        panel.setObjectName("logPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 24, 14, 14)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(170)
        layout.addWidget(self.log_view)
        return panel

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("文件")
        import_action = QAction("导入光栅图", self)
        import_action.triggered.connect(self.import_image)
        file_menu.addAction(import_action)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        file_menu.addAction(quit_action)

    def _connect_actions(self):
        self.open_camera_btn.clicked.connect(self.open_camera)
        self.stop_camera_btn.clicked.connect(self.stop_camera)
        self.capture_btn.clicked.connect(self.capture_frame)
        self.import_btn.clicked.connect(self.import_image)
        self.demo_btn.clicked.connect(self.load_demo)
        self.analyze_btn.clicked.connect(self.analyze_current_frame)
        self.realtime_btn.clicked.connect(self.toggle_realtime_analysis)
        self.save_btn.clicked.connect(self.save_experiment)
        self.refresh_camera_btn.clicked.connect(self.refresh_cameras)
        self.backend_select.currentIndexChanged.connect(self.refresh_cameras)

    def _decorate_button(self, button, standard_pixmap):
        button.setIcon(self.style().standardIcon(standard_pixmap))
        button.setCursor(Qt.PointingHandCursor)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 14px;
                color: #17313d;
            }
            QMainWindow, #mainSurface {
                background: #f4f8fa;
            }
            QMenuBar {
                background: #f4f8fa;
                color: #17313d;
                padding: 4px 8px;
            }
            QMenuBar::item {
                background: transparent;
                border-radius: 5px;
                padding: 5px 10px;
            }
            QMenuBar::item:selected {
                background: #d8edf1;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #cfdde2;
                border-radius: 8px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #0f6572;
                font-weight: 700;
            }
            #titleBar {
                background: #0e1b24;
                border: 1px solid #1d4653;
                border-radius: 8px;
                margin-top: 0;
            }
            #titleMark {
                background: #20a6b8;
                color: white;
                font-size: 18px;
                font-weight: 800;
                border-radius: 8px;
            }
            #appTitle {
                color: #eefcff;
                font-size: 22px;
                font-weight: 800;
            }
            #mutedText {
                color: #99b8c0;
            }
            #fieldLabel {
                color: #435a64;
                font-weight: 700;
            }
            #statusPill {
                background: #132d38;
                border: 1px solid #20a6b8;
                border-radius: 8px;
                color: #c9fbff;
                font-size: 13px;
                padding: 7px 10px;
            }
            QSplitter::handle {
                background: #cbdde3;
                border-radius: 2px;
            }
            QSplitter::handle:horizontal {
                width: 8px;
            }
            QLineEdit, QComboBox, QPlainTextEdit {
                background: #fbfdff;
                border: 1px solid #b9cbd1;
                border-radius: 6px;
                padding: 7px 8px;
                selection-background-color: #20a6b8;
            }
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
                border: 1px solid #20a6b8;
                background: #ffffff;
            }
            QComboBox::drop-down {
                border: 0;
                width: 26px;
            }
            QPushButton {
                min-height: 36px;
                border-radius: 7px;
                font-weight: 700;
                padding: 7px 12px;
            }
            #primaryButton {
                background: #1b8fa0;
                border: 1px solid #126f7d;
                color: #ffffff;
            }
            #primaryButton:hover {
                background: #157e8d;
            }
            #accentButton {
                background: #e7a83d;
                border: 1px solid #c88b25;
                color: #1f2b32;
            }
            #accentButton:hover {
                background: #d89b32;
            }
            #secondaryButton, #ghostButton {
                background: #e9f3f5;
                border: 1px solid #accbd2;
                color: #164f5a;
            }
            #secondaryButton:hover, #ghostButton:hover {
                background: #d8edf1;
            }
            #previewPanel, #scanPanel {
                background: #0e1b24;
                border: 1px solid #1f4c58;
            }
            #previewPanel::title, #scanPanel::title {
                color: #b9f8ff;
                background: #0e1b24;
            }
            #previewLabel {
                background: #071017;
                color: #d7f8fb;
                border: 1px solid #20a6b8;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 700;
            }
            #scanLabel {
                background: #071017;
                color: #d7f8fb;
                border: 1px solid #20a6b8;
                border-radius: 8px;
                font-weight: 700;
            }
            #resultsPanel {
                background: #0e1b24;
                border: 1px solid #20a6b8;
            }
            #resultsPanel::title {
                color: #b9f8ff;
                background: #0e1b24;
            }
            #controlPanel {
                background: #eef5f7;
                border: 1px solid #c5d7dd;
            }
            #logView {
                background: #f9fcfd;
                color: #314b59;
            }
            #heroResult {
                background: #071017;
                border: 1px solid #20a6b8;
                border-radius: 8px;
                color: #36d8e8;
                font-size: 24px;
                font-weight: 800;
                padding: 10px;
            }
            #resultValue {
                color: #d7f8fb;
                font-weight: 700;
            }
            #resultsPanel QLabel {
                color: #d7f8fb;
            }
            """
        )

    def open_camera(self):
        if self.camera_worker and self.camera_worker.isRunning():
            self._log("相机已经打开。")
            return
        camera_index = self.camera_select.currentData()
        if camera_index is None:
            camera_index = 0
        backend_name = self.backend_select.currentData() or "DSHOW"
        self.camera_worker = CameraWorker()
        self.camera_worker.frame_ready.connect(self.on_frame_ready)
        self.camera_worker.camera_error.connect(self.on_camera_error)
        self.camera_worker.start(camera_index, backend_name)
        self.camera_status.setText("相机状态：连接中")
        self._log("正在打开 USB 相机 {}（{}）。".format(camera_index, BACKEND_NAMES.get(backend_name, backend_name)))

    def refresh_cameras(self):
        if self.camera_worker and self.camera_worker.isRunning():
            self._log("相机运行中，请先关闭相机再刷新设备列表。")
            return
        current_index = self.camera_select.currentData() if hasattr(self, "camera_select") else 0
        backend_name = self.backend_select.currentData() if hasattr(self, "backend_select") else "DSHOW"
        self.available_cameras = discover_cameras(backend_name=backend_name)
        detected = {}
        for camera in self.available_cameras:
            detected[camera["index"]] = camera
        self.camera_select.blockSignals(True)
        self.camera_select.clear()

        for index in range(10):
            if index in detected:
                camera = detected[index]
                if camera["width"] and camera["height"]:
                    label = "相机 {}  (已检测 {} x {})".format(index, camera["width"], camera["height"])
                else:
                    label = "{}  (已检测)".format(camera["name"])
            else:
                label = "相机 {}  (手动尝试)".format(index)
            self.camera_select.addItem(label, index)

        restored = False
        for row in range(self.camera_select.count()):
            if self.camera_select.itemData(row) == current_index:
                self.camera_select.setCurrentIndex(row)
                restored = True
                break
        if not restored:
            self.camera_select.setCurrentIndex(0)

        self.camera_select.blockSignals(False)
        if self.available_cameras:
            indexes = ", ".join(str(camera["index"]) for camera in self.available_cameras)
            self._log("已通过 {} 检测到相机编号：{}。".format(BACKEND_NAMES.get(backend_name, backend_name), indexes))
        else:
            self._log("未通过 {} 自动检测到可用相机，仍可手动尝试编号。".format(BACKEND_NAMES.get(backend_name, backend_name)))

    def stop_camera(self):
        if self.camera_worker:
            self.camera_worker.stop()
            self.camera_worker = None
        self.camera_status.setText("相机状态：已关闭")
        self.realtime_enabled = False
        self._reset_realtime_smoothing()
        self.realtime_btn.setText("开始实时分析")
        self._log("相机已关闭。")

    def on_frame_ready(self, frame):
        self.current_frame = frame
        self.camera_status.setText("相机状态：实时预览")
        self._show_frame(frame)
        if self.realtime_enabled:
            ms = int(datetime.now().timestamp() * 1000)
            if ms - self.last_analysis_ms >= 400:
                self.last_analysis_ms = ms
                self.analyze_current_frame(silent=True, realtime=True)

    def on_camera_error(self, message):
        self.camera_status.setText("相机状态：错误")
        self._log(message)
        QMessageBox.warning(self, "相机错误", message)

    def capture_frame(self):
        if self.current_frame is None:
            self._log("当前没有可拍照帧。")
            return
        self._log("已拍照并分析当前帧。")
        self.analyze_current_frame()

    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入光栅图", self.assets_dir, "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._load_image_path(path)

    def load_demo(self):
        path = os.path.join(self.assets_dir, "demo-effective.png")
        if not os.path.exists(path):
            QMessageBox.warning(self, "缺少素材", "未找到 demo-effective.png。")
            return
        self._load_image_path(path)

    def _load_image_path(self, path):
        result = self.analyzer.analyze_file(path, self._analysis_options())
        self.current_frame = self.analyzer.read_image(path)
        if self.current_frame is not None:
            self._show_frame(self.current_frame)
        if self.current_frame is None:
            self.preview_label.setPixmap(QPixmap(path).scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._display_result(result)
        self._log("已导入图像：{}".format(os.path.basename(path)))

    def analyze_current_frame(self, silent=False, realtime=False):
        if self.current_frame is None:
            if not silent:
                self._log("当前没有可分析图像。")
            return
        result = self.analyzer.analyze_frame(self.current_frame, self._analysis_options())
        if realtime:
            result = self._stabilize_realtime_result(result)
        self._display_result(result)
        if not silent:
            self._log("已完成当前图像分析。")

    def toggle_realtime_analysis(self):
        self.realtime_enabled = not self.realtime_enabled
        if self.realtime_enabled:
            self._reset_realtime_smoothing()
            self.realtime_btn.setText("停止实时分析")
            self.analysis_status.setText("分析：实时")
            self._log("实时分析已启动，默认每 400ms 分析一帧。")
        else:
            self.realtime_btn.setText("开始实时分析")
            self.analysis_status.setText("分析：待机")
            self._log("实时分析已停止。")

    def _reset_realtime_smoothing(self):
        self.realtime_results.clear()
        self.stable_spacing_px = None

    def _stabilize_realtime_result(self, result):
        if result.stripe_spacing_px is None or result.confidence < 0.35:
            if self.stable_spacing_px is not None:
                result.raw_spacing_px = result.stripe_spacing_px
                result.stable_spacing_px = self.stable_spacing_px
                result.stripe_spacing_px = self.stable_spacing_px
                result.stripe_spacing_um = self._round_result_um(self.stable_spacing_px)
                result.status = "hold"
                result.message = "低置信度帧，保持上一稳定值。"
            return result

        raw_spacing = float(result.stripe_spacing_px)
        result.raw_spacing_px = raw_spacing
        self.realtime_results.append(raw_spacing)
        values = sorted(self.realtime_results)
        median = values[len(values) // 2]

        if self.stable_spacing_px is None:
            self.stable_spacing_px = median
        else:
            if abs(raw_spacing - self.stable_spacing_px) / max(self.stable_spacing_px, 1.0) > 0.28:
                median = self.stable_spacing_px
            self.stable_spacing_px = self.stable_spacing_px * 0.72 + median * 0.28

        result.stable_spacing_px = round(self.stable_spacing_px, 3)
        result.stripe_spacing_px = result.stable_spacing_px
        result.stripe_spacing_um = self._round_result_um(self.stable_spacing_px)
        return result

    def _round_result_um(self, spacing_px):
        pixel_scale = self._float_value(self.pixel_scale.text(), 1.0)
        return round(float(spacing_px) * pixel_scale, 3)

    def save_experiment(self):
        params = self._params()
        folder = self.store.create_experiment(params)
        if self.current_frame is not None:
            self.store.save_image("main.png", self.current_frame)
        if self.current_result is not None:
            self.store.save_analysis(self.current_result)
        self.store.append_log("实验保存完成。")
        self._log("实验已保存：{}".format(folder))
        QMessageBox.information(self, "保存完成", "实验已保存到：\n{}".format(folder))

    def _display_result(self, result):
        self.current_result = result
        spacing = "--" if result.stripe_spacing_px is None else "{} px / {} um".format(result.stripe_spacing_px, result.stripe_spacing_um)
        self.result_spacing.setText(spacing)
        self.result_bright.setText("--" if result.bright_spacing_px is None else "{} px".format(result.bright_spacing_px))
        self.result_dark.setText("--" if result.dark_spacing_px is None else "{} px".format(result.dark_spacing_px))
        self.result_clarity.setText("{} / 100".format(result.clarity_score))
        self.result_confidence.setText("{} / 1.0".format(result.confidence))
        self.result_state.setText("{}：{}".format(result.status, result.message))
        self.analysis_status.setText("分析：" + result.status)
        self._draw_profile(result.profile)

    def _draw_profile(self, profile):
        if not profile:
            self.scan_label.setText("未生成强度扫描曲线")
            return
        from PyQt5.QtGui import QPainter, QPen

        image = QImage(max(720, self.scan_label.width()), 170, QImage.Format_RGB32)
        image.fill(Qt.black)
        painter = QPainter(image)
        painter.setPen(QPen(Qt.darkGray, 1))
        for x in range(20, image.width(), 60):
            painter.drawLine(x, 12, x, image.height() - 12)
        painter.setPen(QPen(Qt.green, 2))
        usable_w = image.width() - 32
        usable_h = image.height() - 32
        last = None
        for idx, value in enumerate(profile):
            x = 16 + int(idx * usable_w / max(1, len(profile) - 1))
            y = 16 + int((1.0 - float(value)) * usable_h)
            if last is not None:
                painter.drawLine(last[0], last[1], x, y)
            last = (x, y)
        painter.end()
        self.scan_label.setPixmap(QPixmap.fromImage(image))

    def _show_frame(self, frame):
        try:
            if self.analyzer.cv2 is not None and len(frame.shape) == 3:
                rgb = self.analyzer.cv2.cvtColor(frame, self.analyzer.cv2.COLOR_BGR2RGB)
            else:
                rgb = frame
            h, w, channels = rgb.shape
            image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
            self.preview_label.setPixmap(QPixmap.fromImage(image).scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.resolution_status.setText("分辨率：{} x {}".format(w, h))
        except Exception as exc:
            self._log("图像显示失败：" + str(exc))

    def _analysis_options(self):
        return {"pixel_scale": self._float_value(self.pixel_scale.text(), 1.0)}

    def _params(self):
        return {
            "analysis_type": self.analysis_type.currentText(),
            "pixel_scale": self._float_value(self.pixel_scale.text(), 1.0),
            "material": self.material.currentText(),
            "concentration": self._float_value(self.concentration.text(), 0.0),
            "frequency_mhz": self._float_value(self.frequency.text(), 0.0),
            "temperature_c": self._float_value(self.temperature.text(), 0.0),
            "duration_min": self._float_value(self.duration.text(), 0.0),
            "camera_index": self.camera_select.currentData(),
            "camera_backend": self.backend_select.currentData(),
            "notes": self.notes.toPlainText(),
        }

    def _float_value(self, text, default):
        try:
            return float(text)
        except Exception:
            return default

    def _log(self, message):
        self.log_view.appendPlainText("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), message))

    def closeEvent(self, event):
        self.stop_camera()
        event.accept()
