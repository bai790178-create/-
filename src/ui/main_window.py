import os
from datetime import datetime
from collections import deque

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPainter, QPen, QPixmap
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from analysis.stripe_analyzer import StripeAnalyzer
from camera.camera_worker import BACKEND_NAMES, CameraWorker, discover_cameras
from storage.experiment_store import ExperimentStore


class RoiPreviewLabel(QLabel):
    roi_changed = pyqtSignal(object)

    def __init__(self, text=""):
        super(RoiPreviewLabel, self).__init__(text)
        self.image_size = None
        self.roi = None
        self.drag_start = None
        self.drag_current = None

    def set_image_size(self, width, height):
        size = (int(width), int(height))
        if self.image_size != size:
            self.image_size = size
            self.roi = None
        self.update()

    def set_roi(self, roi):
        self.roi = roi
        self.update()

    def clear_roi(self):
        self.drag_start = None
        self.drag_current = None
        if self.roi is not None:
            self.roi = None
            self.roi_changed.emit(None)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.image_size:
            pos = event.pos()
            if self._display_rect().contains(pos):
                self.drag_start = pos
                self.drag_current = pos
                self.update()
                return
        super(RoiPreviewLabel, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            self.drag_current = event.pos()
            self.update()
            return
        super(RoiPreviewLabel, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_start is not None:
            rect = QRect(self.drag_start, event.pos()).normalized()
            rect = rect.intersected(self._display_rect())
            self.drag_start = None
            self.drag_current = None
            roi = self._label_rect_to_roi(rect)
            self.roi = roi
            self.roi_changed.emit(roi)
            self.update()
            return
        super(RoiPreviewLabel, self).mouseReleaseEvent(event)

    def paintEvent(self, event):
        super(RoiPreviewLabel, self).paintEvent(event)
        rect = None
        if self.drag_start is not None and self.drag_current is not None:
            rect = QRect(self.drag_start, self.drag_current).normalized().intersected(self._display_rect())
        elif self.roi:
            rect = self._roi_to_label_rect(self.roi)
        if rect is None or rect.width() < 2 or rect.height() < 2:
            return
        painter = QPainter(self)
        painter.setPen(QPen(Qt.yellow, 2))
        painter.drawRect(rect)
        painter.end()

    def _display_rect(self):
        if not self.image_size:
            return QRect()
        image_w, image_h = self.image_size
        if image_w <= 0 or image_h <= 0:
            return QRect()
        scale = min(float(self.width()) / image_w, float(self.height()) / image_h)
        display_w = int(round(image_w * scale))
        display_h = int(round(image_h * scale))
        left = int((self.width() - display_w) / 2)
        top = int((self.height() - display_h) / 2)
        return QRect(left, top, display_w, display_h)

    def _label_rect_to_roi(self, rect):
        if not self.image_size or rect.width() < 4 or rect.height() < 4:
            return None
        display = self._display_rect()
        if display.width() <= 0 or display.height() <= 0:
            return None
        image_w, image_h = self.image_size
        x = int(round((rect.left() - display.left()) * image_w / float(display.width())))
        y = int(round((rect.top() - display.top()) * image_h / float(display.height())))
        w = int(round(rect.width() * image_w / float(display.width())))
        h = int(round(rect.height() * image_h / float(display.height())))
        x = max(0, min(image_w - 1, x))
        y = max(0, min(image_h - 1, y))
        w = max(1, min(image_w - x, w))
        h = max(1, min(image_h - y, h))
        return {"x": x, "y": y, "width": w, "height": h}

    def _roi_to_label_rect(self, roi):
        if not self.image_size or not roi:
            return None
        display = self._display_rect()
        if display.width() <= 0 or display.height() <= 0:
            return None
        image_w, image_h = self.image_size
        x = display.left() + int(round(float(roi.get("x", 0)) * display.width() / image_w))
        y = display.top() + int(round(float(roi.get("y", 0)) * display.height() / image_h))
        w = int(round(float(roi.get("width", 0)) * display.width() / image_w))
        h = int(round(float(roi.get("height", 0)) * display.height() / image_h))
        return QRect(x, y, w, h)


class MainWindow(QMainWindow):
    def __init__(self, project_root, bundle_root=None):
        super(MainWindow, self).__init__()
        self.project_root = project_root
        self.bundle_root = bundle_root or project_root
        self.assets_dir = os.path.join(self.bundle_root, "assets")
        self.experiments_dir = os.path.join(project_root, "experiments")
        self.analyzer = StripeAnalyzer()
        self.store = ExperimentStore(self.experiments_dir)
        self.camera_worker = None
        self.accept_camera_frames = False
        self.current_frame = None
        self.current_result = None
        self.realtime_enabled = False
        self.last_analysis_ms = 0
        self.available_cameras = []
        self.realtime_results = deque(maxlen=7)
        self.stable_spacing_px = None
        self.realtime_outlier_count = 0
        self.realtime_hold_count = 0
        self.contrast_dark_frame = None
        self.contrast_background_frame = None
        self.contrast_stripe_frame = None
        self.contrast_dark_subtracted_frame = None
        self.contrast_corrected_frame = None
        self.contrast_result = None
        self.contrast_realtime_enabled = False
        self.last_contrast_analysis_ms = 0
        self.current_roi = None
        self.updating_roi = False

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

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        tabs.addTab(self._build_analysis_page(), "条纹分析")
        tabs.addTab(self._build_contrast_page(), "衬比度计算")
        root.addWidget(tabs, 1)
        self.setCentralWidget(central)
        self._build_menu()

    def _build_analysis_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

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
        return page

    def _build_contrast_page(self):
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        preview = QGroupBox("衬比度预览")
        preview.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(16, 26, 16, 16)
        main_preview = QHBoxLayout()
        main_preview.setSpacing(10)
        self.contrast_original_preview_label = QLabel("打开相机后显示原图")
        self.contrast_corrected_preview_label = QLabel("完成采集后显示背景校正增强图")
        for label in (self.contrast_original_preview_label, self.contrast_corrected_preview_label):
            label.setObjectName("previewLabel")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumSize(370, 520)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            main_preview.addWidget(label, 1)
        preview_layout.addLayout(main_preview, 1)
        thumbs = QGroupBox("参考图像")
        thumbs.setObjectName("scanPanel")
        thumb_layout = QGridLayout(thumbs)
        thumb_layout.setContentsMargins(12, 22, 12, 12)
        thumb_layout.setHorizontalSpacing(10)
        thumb_layout.setVerticalSpacing(8)
        self.contrast_dark_image = self._build_contrast_thumbnail("暗场图")
        self.contrast_background_image = self._build_contrast_thumbnail("背景图")
        self.contrast_stripe_image = self._build_contrast_thumbnail("原图")
        self.contrast_corrected_image = self._build_contrast_thumbnail("原图减暗场")
        thumb_layout.addWidget(self.contrast_dark_image, 0, 0)
        thumb_layout.addWidget(self.contrast_background_image, 0, 1)
        thumb_layout.addWidget(self.contrast_stripe_image, 0, 2)
        thumb_layout.addWidget(self.contrast_corrected_image, 0, 3)
        preview_layout.addWidget(thumbs)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        capture = QGroupBox("衬比度采集")
        capture.setObjectName("controlPanel")
        capture_layout = QGridLayout(capture)
        capture_layout.setContentsMargins(14, 26, 14, 14)
        capture_layout.setHorizontalSpacing(10)
        capture_layout.setVerticalSpacing(10)
        self.capture_dark_btn = QPushButton("拍暗场图")
        self.capture_background_btn = QPushButton("拍背景图")
        self.capture_stripe_contrast_btn = QPushButton("拍条纹图并计算")
        self.realtime_contrast_btn = QPushButton("开始实时衬比度")
        self.save_contrast_btn = QPushButton("保存衬比度结果")
        self.clear_contrast_btn = QPushButton("清空")
        self.capture_stripe_contrast_btn.setObjectName("accentButton")
        self.realtime_contrast_btn.setObjectName("primaryButton")
        self.save_contrast_btn.setObjectName("accentButton")
        for button in (self.capture_dark_btn, self.capture_background_btn, self.clear_contrast_btn):
            button.setObjectName("secondaryButton")
        self._decorate_button(self.capture_dark_btn, QStyle.SP_DialogYesButton)
        self._decorate_button(self.capture_background_btn, QStyle.SP_DialogYesButton)
        self._decorate_button(self.capture_stripe_contrast_btn, QStyle.SP_ComputerIcon)
        self._decorate_button(self.realtime_contrast_btn, QStyle.SP_BrowserReload)
        self._decorate_button(self.save_contrast_btn, QStyle.SP_DialogSaveButton)
        self._decorate_button(self.clear_contrast_btn, QStyle.SP_DialogResetButton)
        capture_layout.addWidget(self.capture_dark_btn, 0, 0)
        capture_layout.addWidget(self.capture_background_btn, 0, 1)
        capture_layout.addWidget(self.capture_stripe_contrast_btn, 1, 0, 1, 2)
        capture_layout.addWidget(self.realtime_contrast_btn, 2, 0, 1, 2)
        capture_layout.addWidget(self.save_contrast_btn, 3, 0, 1, 2)
        capture_layout.addWidget(self.clear_contrast_btn, 4, 0, 1, 2)

        status = QGroupBox("采集状态")
        status.setObjectName("paramsPanel")
        status_layout = QFormLayout(status)
        status_layout.setContentsMargins(14, 24, 14, 14)
        self.contrast_dark_status = QLabel("--")
        self.contrast_background_status = QLabel("--")
        self.contrast_stripe_status = QLabel("--")
        for label in (self.contrast_dark_status, self.contrast_background_status, self.contrast_stripe_status):
            label.setObjectName("resultValue")
        status_layout.addRow("暗场图", self.contrast_dark_status)
        status_layout.addRow("背景图", self.contrast_background_status)
        status_layout.addRow("条纹图", self.contrast_stripe_status)

        results = QGroupBox("衬比度结果")
        results.setObjectName("resultsPanel")
        result_layout = QFormLayout(results)
        result_layout.setContentsMargins(14, 24, 14, 14)
        self.contrast_gamma_label = QLabel("--")
        self.contrast_gamma_label.setObjectName("heroResult")
        self.contrast_gamma_label.setAlignment(Qt.AlignCenter)
        self.contrast_gamma_label.setMinimumHeight(58)
        self.contrast_i_max_label = QLabel("--")
        self.contrast_i_min_label = QLabel("--")
        self.contrast_roi_label = QLabel("--")
        self.contrast_uncertainty_label = QLabel("--")
        self.contrast_pair_label = QLabel("--")
        self.contrast_quality_label = QLabel("--")
        self.contrast_state_label = QLabel("待机")
        self.contrast_state_label.setWordWrap(True)
        for label in (self.contrast_i_max_label, self.contrast_i_min_label, self.contrast_roi_label, self.contrast_uncertainty_label, self.contrast_pair_label, self.contrast_quality_label, self.contrast_state_label):
            label.setObjectName("resultValue")
        result_layout.addRow("γ", self.contrast_gamma_label)
        result_layout.addRow("Imax", self.contrast_i_max_label)
        result_layout.addRow("Imin", self.contrast_i_min_label)
        result_layout.addRow("不确定度", self.contrast_uncertainty_label)
        result_layout.addRow("有效峰谷", self.contrast_pair_label)
        result_layout.addRow("质量", self.contrast_quality_label)
        result_layout.addRow("采样区域", self.contrast_roi_label)
        result_layout.addRow("状态", self.contrast_state_label)

        side_layout.addWidget(capture)
        side_layout.addWidget(status)
        side_layout.addWidget(results)
        side_layout.addStretch(1)
        root.addWidget(preview, 3)
        root.addWidget(side, 1)
        self._update_contrast_statuses()
        return page

    def _build_contrast_thumbnail(self, text):
        label = QLabel(text)
        label.setObjectName("scanLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumWidth(160)
        label.setFixedHeight(118)
        label.setMaximumHeight(118)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return label

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
        self.preview_label = RoiPreviewLabel("导入图片或打开 USB 相机")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(760, 520)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.roi_changed.connect(self._on_roi_changed)
        layout.addWidget(self.preview_label, 1)

        camera_bar = QHBoxLayout()
        camera_bar.setSpacing(8)
        self.backend_select = QComboBox()
        self.backend_select.addItem("DirectShow", "DSHOW")
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
        self.enhance_btn = QPushButton("增强清晰度")
        self.open_camera_btn.setObjectName("primaryButton")
        self.realtime_btn.setObjectName("primaryButton")
        self.save_btn.setObjectName("accentButton")
        self.stop_camera_btn.setObjectName("secondaryButton")
        self.capture_btn.setObjectName("secondaryButton")
        self.import_btn.setObjectName("secondaryButton")
        self.demo_btn.setObjectName("secondaryButton")
        self.enhance_btn.setObjectName("secondaryButton")
        self._decorate_button(self.open_camera_btn, QStyle.SP_MediaPlay)
        self._decorate_button(self.stop_camera_btn, QStyle.SP_MediaStop)
        self._decorate_button(self.capture_btn, QStyle.SP_DialogYesButton)
        self._decorate_button(self.import_btn, QStyle.SP_DialogOpenButton)
        self._decorate_button(self.demo_btn, QStyle.SP_FileDialogDetailedView)
        self._decorate_button(self.enhance_btn, QStyle.SP_FileDialogContentsView)
        self._decorate_button(self.realtime_btn, QStyle.SP_BrowserReload)
        self._decorate_button(self.save_btn, QStyle.SP_DialogSaveButton)

        layout.addWidget(self.open_camera_btn, 0, 0)
        layout.addWidget(self.stop_camera_btn, 0, 1)
        layout.addWidget(self.capture_btn, 0, 2)
        layout.addWidget(self.import_btn, 1, 0)
        layout.addWidget(self.demo_btn, 1, 1)
        layout.addWidget(self.enhance_btn, 1, 2)
        layout.addWidget(self.realtime_btn, 2, 0)
        layout.addWidget(self.analyze_btn, 2, 1)
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
        self.roi_label = QLabel("未选择")
        self.roi_label.setObjectName("resultValue")
        self.clear_roi_btn = QPushButton("清除 ROI")
        self.clear_roi_btn.setObjectName("secondaryButton")
        self._decorate_button(self.clear_roi_btn, QStyle.SP_DialogResetButton)
        form.addRow("分析类型", self.analysis_type)
        form.addRow("像素当量 (um/px)", self.pixel_scale)
        form.addRow("分析 ROI", self.roi_label)
        form.addRow("", self.clear_roi_btn)
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
        self.result_uncertainty = QLabel("--")
        self.result_method = QLabel("--")
        self.result_clarity = QLabel("--")
        self.result_confidence = QLabel("--")
        self.result_state = QLabel("待机")
        self.result_state.setWordWrap(True)
        for label in (self.result_bright, self.result_dark, self.result_uncertainty, self.result_method, self.result_clarity, self.result_confidence, self.result_state):
            label.setObjectName("resultValue")
        result_layout.addRow("条纹中心距", self.result_spacing)
        result_layout.addRow("亮纹中心距", self.result_bright)
        result_layout.addRow("暗纹中心距", self.result_dark)
        result_layout.addRow("间距不确定度", self.result_uncertainty)
        result_layout.addRow("测量方式", self.result_method)
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
        self.log_view.setMaximumBlockCount(600)
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
        self.enhance_btn.clicked.connect(self.enhance_current_frame)
        self.analyze_btn.clicked.connect(self.analyze_current_frame)
        self.realtime_btn.clicked.connect(self.toggle_realtime_analysis)
        self.save_btn.clicked.connect(self.save_experiment)
        self.refresh_camera_btn.clicked.connect(self.refresh_cameras)
        self.backend_select.currentIndexChanged.connect(self.refresh_cameras)
        self.clear_roi_btn.clicked.connect(lambda: self.clear_roi())
        self.capture_dark_btn.clicked.connect(self.capture_contrast_dark)
        self.capture_background_btn.clicked.connect(self.capture_contrast_background)
        self.capture_stripe_contrast_btn.clicked.connect(self.capture_contrast_stripe)
        self.realtime_contrast_btn.clicked.connect(self.toggle_realtime_contrast)
        self.save_contrast_btn.clicked.connect(self.save_contrast_result)
        self.clear_contrast_btn.clicked.connect(self.clear_contrast)

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
        self.accept_camera_frames = True
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
        self.accept_camera_frames = False
        if self.camera_worker:
            self.camera_worker.stop()
            self.camera_worker = None
        self.camera_status.setText("相机状态：已关闭")
        self.realtime_enabled = False
        self.contrast_realtime_enabled = False
        self._reset_realtime_smoothing()
        self.realtime_btn.setText("开始实时分析")
        self.realtime_contrast_btn.setText("开始实时衬比度")
        self._log("相机已关闭。")

    def on_frame_ready(self, frame):
        worker = self.sender()
        try:
            if not self.accept_camera_frames:
                return
            self.current_frame = frame
            self.camera_status.setText("相机状态：实时预览")
            self._show_frame(frame)
            if hasattr(self, "contrast_original_preview_label"):
                self._show_frame_on_label(frame, self.contrast_original_preview_label)
            if self.realtime_enabled:
                ms = int(datetime.now().timestamp() * 1000)
                if ms - self.last_analysis_ms >= 400:
                    self.last_analysis_ms = ms
                    self.analyze_current_frame(silent=True, realtime=True)
            if self.contrast_realtime_enabled:
                ms = int(datetime.now().timestamp() * 1000)
                if ms - self.last_contrast_analysis_ms >= 500:
                    self.last_contrast_analysis_ms = ms
                    self.analyze_realtime_contrast(frame)
        finally:
            if hasattr(worker, "mark_frame_consumed"):
                worker.mark_frame_consumed()

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
        self.accept_camera_frames = False
        if self.camera_worker and self.camera_worker.isRunning():
            self.stop_camera()
        self.current_frame = self.analyzer.read_image(path)
        self.clear_roi(reanalyze=False)
        result = self.analyzer.analyze_file(path, self._analysis_options())
        shown = False
        if self.current_frame is not None:
            shown = self._show_frame(self.current_frame)
            if hasattr(self, "contrast_original_preview_label"):
                self._show_frame_on_label(self.current_frame, self.contrast_original_preview_label)
        if not shown:
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

    def clear_roi(self, reanalyze=True):
        self.current_roi = None
        if hasattr(self, "roi_label"):
            self.roi_label.setText("未选择")
        if hasattr(self, "preview_label") and hasattr(self.preview_label, "clear_roi"):
            self.updating_roi = True
            self.preview_label.clear_roi()
            self.updating_roi = False
        self._reset_realtime_smoothing()
        if reanalyze and self.current_frame is not None:
            self.analyze_current_frame(silent=True)
            self._log("已清除 ROI，恢复默认分析区域。")

    def _on_roi_changed(self, roi):
        if self.updating_roi:
            return
        self.current_roi = roi
        self._reset_realtime_smoothing()
        if roi:
            self.roi_label.setText("{} , {} / {} x {}".format(roi["x"], roi["y"], roi["width"], roi["height"]))
            self._log("已选择 ROI：x={}, y={}, w={}, h={}。".format(roi["x"], roi["y"], roi["width"], roi["height"]))
        else:
            self.roi_label.setText("未选择")
        if self.current_frame is not None:
            self.analyze_current_frame(silent=True)

    def enhance_current_frame(self):
        if self.current_frame is None:
            self._log("当前没有可增强的图像。")
            return

        enhanced = self.analyzer.enhance_frame(self.current_frame)
        if enhanced is None:
            self._log("图像增强失败，请确认 OpenCV/NumPy 已正确安装。")
            return

        self.current_frame = enhanced
        self._reset_realtime_smoothing()
        self._show_frame(self.current_frame)
        self.analyze_current_frame(silent=True)
        self._log("已增强当前图像清晰度。")

    def capture_contrast_dark(self):
        frame = self._copy_current_frame_for_contrast("暗场图")
        if frame is None:
            return
        self.contrast_dark_frame = frame
        self.contrast_dark_subtracted_frame = None
        self.contrast_corrected_frame = None
        self.contrast_result = None
        self._show_frame_on_label(self.contrast_dark_frame, self.contrast_dark_image)
        self._reset_contrast_thumbnail(self.contrast_corrected_image, "原图减暗场")
        self._reset_single_contrast_preview(self.contrast_corrected_preview_label, "完成采集后显示背景校正增强图")
        self._update_contrast_statuses()
        self._display_contrast_result(None)
        self._log("已采集衬比度暗场图。")

    def capture_contrast_background(self):
        frame = self._copy_current_frame_for_contrast("背景图")
        if frame is None:
            return
        self.contrast_background_frame = frame
        self.contrast_dark_subtracted_frame = None
        self.contrast_corrected_frame = None
        self.contrast_result = None
        self._show_frame_on_label(self.contrast_background_frame, self.contrast_background_image)
        self._reset_contrast_thumbnail(self.contrast_corrected_image, "原图减暗场")
        self._reset_single_contrast_preview(self.contrast_corrected_preview_label, "完成采集后显示背景校正增强图")
        self._update_contrast_statuses()
        self._display_contrast_result(None)
        self._log("已采集衬比度背景图。")

    def capture_contrast_stripe(self):
        frame = self._copy_current_frame_for_contrast("条纹图")
        if frame is None:
            return
        self._calculate_contrast_from_frame(frame)
        self._log("衬比度计算：{}".format(self.contrast_result.get("message", self.contrast_result.get("status", ""))))

    def analyze_realtime_contrast(self, frame):
        self._calculate_contrast_from_frame(frame)

    def _calculate_contrast_from_frame(self, frame):
        self.contrast_stripe_frame = frame.copy()
        self.contrast_dark_subtracted_frame = None
        self.contrast_corrected_frame = None
        self._show_frame_on_label(self.contrast_stripe_frame, self.contrast_original_preview_label)
        self._show_frame_on_label(self.contrast_stripe_frame, self.contrast_stripe_image)
        self.contrast_result = self.analyzer.calculate_calibrated_contrast(
            self.contrast_stripe_frame,
            self.contrast_background_frame,
            self.contrast_dark_frame,
            self._analysis_options(),
        )
        self.contrast_dark_subtracted_frame = self.analyzer.dark_subtracted_contrast_image(
            self.contrast_stripe_frame,
            self.contrast_dark_frame,
        )
        if self.contrast_dark_subtracted_frame is not None:
            self._show_frame_on_label(self.contrast_dark_subtracted_frame, self.contrast_corrected_image)
        else:
            self._reset_contrast_thumbnail(self.contrast_corrected_image, "原图减暗场")
        if self.contrast_result.get("status") == "ok":
            self.contrast_corrected_frame = self.analyzer.corrected_contrast_image(
                self.contrast_stripe_frame,
                self.contrast_background_frame,
                self.contrast_dark_frame,
            )
            if self.contrast_corrected_frame is not None:
                self._show_frame_on_label(self.contrast_corrected_frame, self.contrast_corrected_preview_label)
        else:
            self._reset_single_contrast_preview(self.contrast_corrected_preview_label, "完成采集后显示背景校正增强图")
        self._update_contrast_statuses()
        self._display_contrast_result(self.contrast_result)

    def clear_contrast(self):
        self.contrast_realtime_enabled = False
        self.realtime_contrast_btn.setText("开始实时衬比度")
        self.contrast_dark_frame = None
        self.contrast_background_frame = None
        self.contrast_stripe_frame = None
        self.contrast_dark_subtracted_frame = None
        self.contrast_corrected_frame = None
        self.contrast_result = None
        self._reset_contrast_thumbnail(self.contrast_dark_image, "暗场图")
        self._reset_contrast_thumbnail(self.contrast_background_image, "背景图")
        self._reset_contrast_thumbnail(self.contrast_stripe_image, "原图")
        self._reset_contrast_thumbnail(self.contrast_corrected_image, "原图减暗场")
        self._reset_contrast_main_previews()
        self._update_contrast_statuses()
        self._display_contrast_result(None)
        self._log("已清空衬比度采集数据。")

    def _copy_current_frame_for_contrast(self, name):
        if self.current_frame is None:
            self._log("当前没有可用于{}的相机帧。".format(name))
            return None
        return self.current_frame.copy()

    def _update_contrast_statuses(self):
        if not hasattr(self, "contrast_dark_status"):
            return
        self.contrast_dark_status.setText(self._contrast_frame_status(self.contrast_dark_frame))
        self.contrast_background_status.setText(self._contrast_frame_status(self.contrast_background_frame))
        self.contrast_stripe_status.setText(self._contrast_frame_status(self.contrast_stripe_frame))

    def _contrast_frame_status(self, frame):
        if frame is None:
            return "未采集"
        h, w = frame.shape[:2]
        return "已采集 {} x {}".format(w, h)

    def _display_contrast_result(self, result):
        if not hasattr(self, "contrast_gamma_label"):
            return
        if not result:
            self.contrast_gamma_label.setText("--")
            self.contrast_i_max_label.setText("--")
            self.contrast_i_min_label.setText("--")
            self.contrast_roi_label.setText("--")
            self.contrast_uncertainty_label.setText("--")
            self.contrast_pair_label.setText("--")
            self.contrast_quality_label.setText("--")
            self.contrast_state_label.setText("待机")
            return

        gamma = result.get("gamma")
        if gamma is not None:
            self.contrast_gamma_label.setText("{:.5f} ({:.2f}%)".format(gamma, gamma * 100.0))
            gamma_std = result.get("gamma_std")
            self.contrast_uncertainty_label.setText("--" if gamma_std is None else "± {:.5f} ({:.2f}%)".format(gamma_std, gamma_std * 100.0))
            self.contrast_i_max_label.setText("--" if result.get("i_max") is None else "{:.4f}".format(result.get("i_max")))
            self.contrast_i_min_label.setText("--" if result.get("i_min") is None else "{:.4f}".format(result.get("i_min")))
            self.contrast_roi_label.setText("{} x {}".format(result.get("roi_width"), result.get("roi_height")))
            self.contrast_pair_label.setText("{} / {}".format(result.get("valid_pair_count", 0), result.get("total_pair_count", 0)))
            self.contrast_quality_label.setText(result.get("quality_status", "--"))
        else:
            self.contrast_gamma_label.setText("--")
            self.contrast_i_max_label.setText("--")
            self.contrast_i_min_label.setText("--")
            self.contrast_roi_label.setText("--")
            self.contrast_uncertainty_label.setText("--")
            self.contrast_pair_label.setText("--")
            self.contrast_quality_label.setText("--")
        self.contrast_state_label.setText("{}：{}".format(result.get("status", ""), result.get("message", "")))

    def _reset_contrast_thumbnail(self, label, text):
        if not hasattr(self, "contrast_dark_image"):
            return
        label.clear()
        label.setText(text)
        label.setAlignment(Qt.AlignCenter)

    def _reset_contrast_main_previews(self):
        if not hasattr(self, "contrast_original_preview_label"):
            return
        self._reset_single_contrast_preview(self.contrast_original_preview_label, "打开相机后显示原图")
        self._reset_single_contrast_preview(self.contrast_corrected_preview_label, "完成采集后显示背景校正增强图")

    def _reset_single_contrast_preview(self, label, text):
        label.clear()
        label.setText(text)
        label.setAlignment(Qt.AlignCenter)

    def toggle_realtime_contrast(self):
        if not self.contrast_realtime_enabled:
            if self.contrast_dark_frame is None or self.contrast_background_frame is None:
                self._log("实时衬比度需要先采集暗场图和背景图。")
                return
            if self.current_frame is None:
                self._log("当前没有可用于实时衬比度分析的相机帧。")
                return
            self.contrast_realtime_enabled = True
            self.last_contrast_analysis_ms = 0
            self.realtime_contrast_btn.setText("停止实时衬比度")
            self.contrast_state_label.setText("实时衬比度分析中")
            self._log("实时衬比度已启动，默认每 500ms 分析一帧。")
        else:
            self.contrast_realtime_enabled = False
            self.realtime_contrast_btn.setText("开始实时衬比度")
            self._log("实时衬比度已停止。")

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
        self.realtime_outlier_count = 0
        self.realtime_hold_count = 0

    def _stabilize_realtime_result(self, result):
        if result.stripe_spacing_px is None or result.confidence < 0.35:
            self.realtime_hold_count += 1
            if self.realtime_hold_count >= 8:
                self._reset_realtime_smoothing()
                return result
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
        self.realtime_hold_count = 0
        self.realtime_results.append(raw_spacing)
        values = sorted(self.realtime_results)
        median = values[len(values) // 2]

        if self.stable_spacing_px is None:
            self.stable_spacing_px = median
            self.realtime_outlier_count = 0
        else:
            if abs(raw_spacing - self.stable_spacing_px) / max(self.stable_spacing_px, 1.0) > 0.28:
                self.realtime_outlier_count += 1
                if self.realtime_outlier_count < 3:
                    median = self.stable_spacing_px
                else:
                    self.realtime_results.clear()
                    self.realtime_results.append(raw_spacing)
                    self.stable_spacing_px = raw_spacing
                    median = raw_spacing
                    self.realtime_outlier_count = 0
            else:
                self.realtime_outlier_count = 0
            self.stable_spacing_px = self.stable_spacing_px * 0.72 + median * 0.28

        result.stable_spacing_px = round(self.stable_spacing_px, 3)
        result.stripe_spacing_px = result.stable_spacing_px
        result.stripe_spacing_um = self._round_result_um(self.stable_spacing_px)
        return result

    def _round_result_um(self, spacing_px):
        pixel_scale = self._float_value(self.pixel_scale.text(), 1.0)
        return round(float(spacing_px) * pixel_scale, 3)

    def save_contrast_result(self):
        if not self.contrast_result or self.contrast_result.get("gamma") is None:
            self._log("当前没有可保存的衬比度结果。")
            QMessageBox.warning(self, "无法保存", "请先完成暗场、背景和条纹图采集并计算衬比度。")
            return
        try:
            params = self._params()
            params["record_type"] = "contrast"
            folder = self.store.create_experiment(params)
            self.store.save_contrast(self.contrast_result)
            if self.contrast_dark_frame is not None:
                self.store.save_image("contrast_dark.png", self.contrast_dark_frame)
            if self.contrast_background_frame is not None:
                self.store.save_image("contrast_background.png", self.contrast_background_frame)
            if self.contrast_stripe_frame is not None:
                self.store.save_image("contrast_stripe.png", self.contrast_stripe_frame)
            if self.contrast_dark_subtracted_frame is not None:
                self.store.save_image("contrast_dark_subtracted.png", self.contrast_dark_subtracted_frame)
            if self.contrast_corrected_frame is not None:
                self.store.save_image("contrast_corrected.png", self.contrast_corrected_frame)
            self.store.append_log("衬比度结果保存完成。")
            self._log("衬比度结果已保存：{}".format(folder))
            QMessageBox.information(self, "保存完成", "衬比度结果已保存到：\n{}".format(folder))
        except Exception as exc:
            self._log("衬比度结果保存失败：{}".format(exc))
            QMessageBox.critical(self, "保存失败", str(exc))

    def save_experiment(self):
        params = self._params()
        folder = self.store.create_experiment(params)
        if self.current_frame is not None:
            self.store.save_image("main.png", self.current_frame)
        if self.current_result is not None:
            self.store.save_analysis(self.current_result)
        if self.contrast_result is not None:
            self.store.save_contrast(self.contrast_result)
            if self.contrast_dark_frame is not None:
                self.store.save_image("contrast_dark.png", self.contrast_dark_frame)
            if self.contrast_background_frame is not None:
                self.store.save_image("contrast_background.png", self.contrast_background_frame)
            if self.contrast_stripe_frame is not None:
                self.store.save_image("contrast_stripe.png", self.contrast_stripe_frame)
            if self.contrast_dark_subtracted_frame is not None:
                self.store.save_image("contrast_dark_subtracted.png", self.contrast_dark_subtracted_frame)
            if self.contrast_corrected_frame is not None:
                self.store.save_image("contrast_corrected.png", self.contrast_corrected_frame)
        self.store.append_log("实验保存完成。")
        self._log("实验已保存：{}".format(folder))
        QMessageBox.information(self, "保存完成", "实验已保存到：\n{}".format(folder))

    def _display_result(self, result):
        self.current_result = result
        spacing = "--" if result.stripe_spacing_px is None else "{} px / {} um".format(result.stripe_spacing_px, result.stripe_spacing_um)
        self.result_spacing.setText(spacing)
        self.result_bright.setText("--" if result.bright_spacing_px is None else "{} px".format(result.bright_spacing_px))
        self.result_dark.setText("--" if result.dark_spacing_px is None else "{} px".format(result.dark_spacing_px))
        self.result_uncertainty.setText("--" if result.spacing_uncertainty_px is None else "± {} px".format(result.spacing_uncertainty_px))
        self.result_method.setText(self._measurement_method_label(result.measurement_method))
        self.result_clarity.setText("{} / 100".format(result.clarity_score))
        self.result_confidence.setText("{} / 1.0".format(result.confidence))
        self.result_state.setText("{}：{}".format(result.status, result.message))
        self.analysis_status.setText("分析：" + result.status)
        self._draw_profile(result.profile)

    def _measurement_method_label(self, method):
        if method == "band_center":
            return "亮带中线"
        if method == "period_fallback":
            return "周期估计"
        return "--"

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

    def _show_frame_on_label(self, frame, label):
        try:
            if self.analyzer.cv2 is not None and len(frame.shape) == 3:
                rgb = self.analyzer.cv2.cvtColor(frame, self.analyzer.cv2.COLOR_BGR2RGB)
            else:
                rgb = frame
            if len(rgb.shape) == 2:
                h, w = rgb.shape
                image = QImage(rgb.data, w, h, w, QImage.Format_Grayscale8)
                label.setPixmap(QPixmap.fromImage(image).scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return True
            h, w, channels = rgb.shape
            image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888)
            label.setPixmap(QPixmap.fromImage(image).scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            return True
        except Exception as exc:
            self._log("图像显示失败：" + str(exc))
            return False

    def _show_frame(self, frame):
        try:
            if self.analyzer.cv2 is not None and len(frame.shape) == 3:
                rgb = self.analyzer.cv2.cvtColor(frame, self.analyzer.cv2.COLOR_BGR2RGB)
            else:
                rgb = frame
            if len(rgb.shape) == 2:
                h, w = rgb.shape
                image = QImage(rgb.data, w, h, w, QImage.Format_Grayscale8)
                self.preview_label.setPixmap(QPixmap.fromImage(image).scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._set_preview_image_size(w, h)
                self.resolution_status.setText("分辨率：{} x {}".format(w, h))
                return True
            h, w, channels = rgb.shape
            image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888)
            self.preview_label.setPixmap(QPixmap.fromImage(image).scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._set_preview_image_size(w, h)
            self.resolution_status.setText("分辨率：{} x {}".format(w, h))
            return True
        except Exception as exc:
            self._log("图像显示失败：" + str(exc))
            return False

    def _set_preview_image_size(self, width, height):
        if not hasattr(self.preview_label, "set_image_size"):
            return
        old_size = self.preview_label.image_size
        self.updating_roi = True
        self.preview_label.set_image_size(width, height)
        self.updating_roi = False
        if old_size is not None and old_size != self.preview_label.image_size and self.current_roi is not None:
            self.current_roi = None
            self.roi_label.setText("未选择")
            self._reset_realtime_smoothing()

    def _analysis_options(self):
        options = {"pixel_scale": self._float_value(self.pixel_scale.text(), 1.0)}
        if self.current_roi:
            options["roi"] = self.current_roi
        return options

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
            "roi": self.current_roi,
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
