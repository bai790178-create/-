from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from camera.picture_settings import default_picture_settings, normalize_picture_settings


class PictureSettingsPage(QWidget):
    settings_changed = pyqtSignal(object)
    driver_settings_requested = pyqtSignal()

    def __init__(self, settings, parent=None):
        super(PictureSettingsPage, self).__init__(parent)
        self._loading = False
        self.controls = {}
        self._build_ui()
        self.set_settings(settings)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        preview_box = QGroupBox("画面预览")
        preview_box.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(16, 26, 16, 16)
        self.preview_label = QLabel("打开相机后可实时预览设置效果")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(760, 560)
        preview_layout.addWidget(self.preview_label, 1)
        root.addWidget(preview_box, 2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        exposure = self._group("曝光与采集")
        self._add_check(exposure, "自动曝光", "auto_exposure")
        self._add_double(exposure, "曝光时间 (ms)", "exposure_time_ms", 0.01, 1000.0, 0.1, 3)
        self._add_double(exposure, "增益", "gain", 0.0, 100.0, 1.0, 1)
        self._add_check(exposure, "限制帧率", "fps_enabled")
        self._add_double(exposure, "目标帧率 (fps)", "fps", 1.0, 500.0, 1.0, 1)
        side_layout.addWidget(exposure)

        white_balance = self._group("白平衡")
        self._add_check(white_balance, "自动白平衡", "auto_balance")
        self._add_double(white_balance, "红色系数", "balance_r", 0.1, 4.0, 0.01, 2)
        self._add_double(white_balance, "绿色系数", "balance_g", 0.1, 4.0, 0.01, 2)
        self._add_double(white_balance, "蓝色系数", "balance_b", 0.1, 4.0, 0.01, 2)
        side_layout.addWidget(white_balance)

        image = self._group("亮度与色调")
        self._add_int(image, "亮度", "brightness", 0, 100)
        self._add_double(image, "Gamma", "gamma", 0.1, 4.0, 0.05, 2)
        self._add_int(image, "数字偏移", "digital_shift", -100, 100)
        side_layout.addWidget(image)

        detail = self._group("细节处理")
        self._add_check(detail, "启用锐化", "acuity_enabled")
        self._add_int(detail, "锐化强度", "acuity", 0, 10)
        self._add_check(detail, "启用降噪", "denoise_enabled")
        self._add_int(detail, "降噪强度", "denoise", 0, 10)
        side_layout.addWidget(detail)

        note = QLabel("曝光、增益和帧率会写入相机；其余设置实时作用于预览和拍摄帧。不同驱动支持范围可能不同。")
        note.setObjectName("mutedText")
        note.setWordWrap(True)
        side_layout.addWidget(note)

        buttons = QVBoxLayout()
        self.driver_settings_btn = QPushButton("相机驱动高级设置")
        self.driver_settings_btn.setObjectName("secondaryButton")
        self.reset_btn = QPushButton("恢复中性默认值")
        self.reset_btn.setObjectName("secondaryButton")
        buttons.addWidget(self.driver_settings_btn)
        buttons.addWidget(self.reset_btn)
        side_layout.addLayout(buttons)

        self.status_label = QLabel("相机未连接；参数已保存，打开相机后自动应用。")
        self.status_label.setObjectName("statusPill")
        self.status_label.setWordWrap(True)
        side_layout.addWidget(self.status_label)
        side_layout.addStretch(1)

        scroll.setWidget(side)
        root.addWidget(scroll, 1)

        self.driver_settings_btn.clicked.connect(self.driver_settings_requested.emit)
        self.reset_btn.clicked.connect(lambda: self.set_settings(default_picture_settings(), emit=True))

    def _group(self, title):
        group = QGroupBox(title)
        group.setObjectName("paramsPanel")
        form = QFormLayout(group)
        form.setContentsMargins(14, 24, 14, 14)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        group.settings_form = form
        return group

    def _add_check(self, group, label, key):
        control = QCheckBox()
        control.stateChanged.connect(self._emit_settings)
        self.controls[key] = control
        group.settings_form.addRow(label, control)

    def _add_int(self, group, label, key, minimum, maximum):
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.valueChanged.connect(self._emit_settings)
        self.controls[key] = control
        group.settings_form.addRow(label, control)

    def _add_double(self, group, label, key, minimum, maximum, step, decimals):
        control = QDoubleSpinBox()
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setDecimals(decimals)
        control.valueChanged.connect(self._emit_settings)
        self.controls[key] = control
        group.settings_form.addRow(label, control)

    def settings(self):
        values = {}
        for key, control in self.controls.items():
            if isinstance(control, QCheckBox):
                values[key] = control.isChecked()
            else:
                values[key] = control.value()
        return normalize_picture_settings(values)

    def set_settings(self, settings, emit=False):
        values = normalize_picture_settings(settings)
        self._loading = True
        try:
            for key, control in self.controls.items():
                control.blockSignals(True)
                if isinstance(control, QCheckBox):
                    control.setChecked(values[key])
                else:
                    control.setValue(values[key])
                control.blockSignals(False)
        finally:
            self._loading = False
        self._update_enabled_state(values)
        if emit:
            self.settings_changed.emit(values)

    def set_camera_connected(self, connected):
        if connected:
            self.status_label.setText("相机已连接；参数调整会实时应用。")
        else:
            self.status_label.setText("相机未连接；参数已保存，打开相机后自动应用。")

    def _emit_settings(self):
        if self._loading:
            return
        values = self.settings()
        self._update_enabled_state(values)
        self.settings_changed.emit(values)

    def _update_enabled_state(self, values):
        self.controls["exposure_time_ms"].setEnabled(not values["auto_exposure"])
        for key in ("balance_r", "balance_g", "balance_b"):
            self.controls[key].setEnabled(not values["auto_balance"])
        self.controls["fps"].setEnabled(values["fps_enabled"])
        self.controls["acuity"].setEnabled(values["acuity_enabled"])
        self.controls["denoise"].setEnabled(values["denoise_enabled"])
