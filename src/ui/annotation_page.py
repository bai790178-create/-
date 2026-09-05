import json
import os
from datetime import datetime

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QKeySequence, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QScrollArea,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from annotation.fringe_annotation import estimate_spacing
from calibration import PIXEL_SCALE_UM_PER_PX


class AnnotationCanvas(QWidget):
    annotation_changed = pyqtSignal()
    finish_requested = pyqtSignal()

    def __init__(self, parent=None):
        super(AnnotationCanvas, self).__init__(parent)
        self.setMinimumSize(720, 540)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.image_pixmap = None
        self.image_size = None
        self.mode = "roi"
        self.roi = None
        self.centerlines = []
        self.current_points = []
        self.drag_start = None
        self.drag_current = None

    def set_frame(self, frame):
        if frame is None:
            self.image_pixmap = None
            self.image_size = None
            self.update()
            return
        rgb = frame
        try:
            import cv2

            if len(frame.shape) == 3:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        except Exception:
            if len(frame.shape) == 3:
                rgb = frame[:, :, ::-1]

        if len(rgb.shape) == 2:
            height, width = rgb.shape
            image = QImage(rgb.data, width, height, width, QImage.Format_Grayscale8).copy()
        else:
            height, width, channels = rgb.shape
            image = QImage(rgb.data, width, height, channels * width, QImage.Format_RGB888).copy()
        self.image_pixmap = QPixmap.fromImage(image)
        self.image_size = (width, height)
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.drag_start = None
        self.drag_current = None
        self.update()

    def set_annotation(self, roi, centerlines):
        self.roi = dict(roi) if roi else None
        self.centerlines = [
            {
                "order": int(line["order"]),
                "points": [[float(point[0]), float(point[1])] for point in line.get("points", [])],
            }
            for line in (centerlines or [])
        ]
        self.current_points = []
        self.update()
        self.annotation_changed.emit()

    def clear_annotation(self):
        self.roi = None
        self.centerlines = []
        self.current_points = []
        self.drag_start = None
        self.drag_current = None
        self.update()
        self.annotation_changed.emit()

    def finish_current_line(self, order):
        if len(self.current_points) < 2:
            return False
        if any(int(line["order"]) == int(order) for line in self.centerlines):
            raise ValueError("条纹序号 {} 已存在。".format(order))
        self.centerlines.append({
            "order": int(order),
            "points": [[round(point[0], 2), round(point[1], 2)] for point in self.current_points],
        })
        self.current_points = []
        self.centerlines.sort(key=lambda line: line["order"])
        self.update()
        self.annotation_changed.emit()
        return True

    def undo(self):
        if self.current_points:
            self.current_points.pop()
        elif self.centerlines:
            self.centerlines.pop()
        elif self.roi:
            self.roi = None
        self.update()
        self.annotation_changed.emit()

    def mousePressEvent(self, event):
        self.setFocus()
        point = self._widget_to_image(event.pos())
        if point is None:
            return
        if self.mode == "roi" and event.button() == Qt.LeftButton:
            self.drag_start = point
            self.drag_current = point
            self.update()
            return
        if self.mode == "centerline":
            if event.button() == Qt.LeftButton:
                self.current_points.append(point)
                self.update()
                self.annotation_changed.emit()
                return
            if event.button() == Qt.RightButton:
                self.annotation_changed.emit()
                return
        super(AnnotationCanvas, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.mode == "roi" and self.drag_start is not None:
            point = self._widget_to_image(event.pos(), clamp=True)
            if point is not None:
                self.drag_current = point
                self.update()
            return
        super(AnnotationCanvas, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.mode == "roi" and event.button() == Qt.LeftButton and self.drag_start is not None:
            point = self._widget_to_image(event.pos(), clamp=True)
            if point is not None:
                x0, y0 = self.drag_start
                x1, y1 = point
                x = int(round(min(x0, x1)))
                y = int(round(min(y0, y1)))
                width = int(round(abs(x1 - x0)))
                height = int(round(abs(y1 - y0)))
                if width >= 4 and height >= 4:
                    self.roi = {"x": x, "y": y, "width": width, "height": height}
            self.drag_start = None
            self.drag_current = None
            self.update()
            self.annotation_changed.emit()
            return
        super(AnnotationCanvas, self).mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_requested.emit()
            return
        super(AnnotationCanvas, self).keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        display = self._display_rect()
        if self.image_pixmap is None or display.isNull():
            painter.setPen(Qt.lightGray)
            painter.drawText(self.rect(), Qt.AlignCenter, "打开相机拍摄，或导入一张真实条纹图")
            painter.end()
            return

        painter.drawPixmap(display, self.image_pixmap)
        if self.roi:
            painter.setPen(QPen(Qt.yellow, 2))
            painter.drawRect(self._image_rect_to_widget(self.roi))
        if self.drag_start is not None and self.drag_current is not None:
            painter.setPen(QPen(Qt.yellow, 2, Qt.DashLine))
            painter.drawRect(self._image_points_to_widget_rect(self.drag_start, self.drag_current))

        colors = [Qt.green, Qt.cyan, Qt.magenta, Qt.white]
        for index, line in enumerate(self.centerlines):
            points = [self._image_to_widget(point) for point in line.get("points", [])]
            painter.setPen(QPen(colors[index % len(colors)], 2))
            self._draw_polyline(painter, points)
            if points:
                painter.drawText(points[0] + QPoint(6, -6), str(line["order"]))

        current = [self._image_to_widget(point) for point in self.current_points]
        painter.setPen(QPen(Qt.red, 2))
        self._draw_polyline(painter, current)
        for point in current:
            painter.drawEllipse(point, 3, 3)
        painter.end()

    def _draw_polyline(self, painter, points):
        for index in range(1, len(points)):
            painter.drawLine(points[index - 1], points[index])

    def _display_rect(self):
        if not self.image_size:
            return QRect()
        image_width, image_height = self.image_size
        scale = min(float(self.width()) / image_width, float(self.height()) / image_height)
        width = int(round(image_width * scale))
        height = int(round(image_height * scale))
        return QRect(int((self.width() - width) / 2), int((self.height() - height) / 2), width, height)

    def _widget_to_image(self, point, clamp=False):
        display = self._display_rect()
        if display.isNull():
            return None
        if not display.contains(point):
            if not clamp:
                return None
            x = max(display.left(), min(display.right(), point.x()))
            y = max(display.top(), min(display.bottom(), point.y()))
        else:
            x, y = point.x(), point.y()
        image_width, image_height = self.image_size
        image_x = (x - display.left()) * image_width / float(max(1, display.width()))
        image_y = (y - display.top()) * image_height / float(max(1, display.height()))
        return [image_x, image_y]

    def _image_to_widget(self, point):
        display = self._display_rect()
        image_width, image_height = self.image_size
        x = display.left() + int(round(float(point[0]) * display.width() / image_width))
        y = display.top() + int(round(float(point[1]) * display.height() / image_height))
        return QPoint(x, y)

    def _image_rect_to_widget(self, roi):
        first = [roi["x"], roi["y"]]
        second = [roi["x"] + roi["width"], roi["y"] + roi["height"]]
        return QRect(self._image_to_widget(first), self._image_to_widget(second)).normalized()

    def _image_points_to_widget_rect(self, first, second):
        return QRect(self._image_to_widget(first), self._image_to_widget(second)).normalized()


class AnnotationPage(QWidget):
    open_camera_requested = pyqtSignal()
    message = pyqtSignal(str)

    def __init__(self, project_root, params_provider=None, parent=None):
        super(AnnotationPage, self).__init__(parent)
        self.project_root = project_root
        self.dataset_root = os.path.join(project_root, "annotation_dataset")
        self.params_provider = params_provider
        self.live_frame = None
        self.live_raw_frame = None
        self.captured_frame = None
        self.captured_raw_frame = None
        self.source = "camera"
        self.source_path = ""
        self.sample_id = ""
        self.shortcuts = []
        self._build_ui()
        self._connect_actions()
        self._build_shortcuts()
        self._new_sample_id()
        self._update_measurement()
        self._update_status_controls()

    def _build_ui(self):
        self.setObjectName("annotationPage")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)

        preview_group = QGroupBox("采集与标注画布")
        preview_group.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview_group)
        self.canvas = AnnotationCanvas()
        preview_layout.addWidget(self.canvas, 1)
        self.canvas_help = QLabel("先圈出有效 ROI；再切换到中心线模式，沿同一种亮纹或暗纹中心点击。")
        self.canvas_help.setWordWrap(True)
        preview_layout.addWidget(self.canvas_help)
        splitter.addWidget(preview_group)

        side = QWidget()
        side.setMinimumWidth(330)
        side.setMaximumWidth(430)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)

        capture_group = QGroupBox("图像采集")
        capture_layout = QVBoxLayout(capture_group)
        capture_row = QHBoxLayout()
        self.open_camera_btn = QPushButton("打开当前相机")
        self.capture_btn = QPushButton("拍摄定格")
        self.import_btn = QPushButton("导入图片")
        self.capture_btn.setEnabled(False)
        self.open_camera_btn.setObjectName("secondaryButton")
        self.capture_btn.setObjectName("primaryButton")
        self.import_btn.setObjectName("secondaryButton")
        self.open_camera_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.capture_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton))
        self.import_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_camera_btn.setToolTip("打开主界面当前选择的相机")
        self.capture_btn.setToolTip("定格当前画面，开始标注")
        self.import_btn.setToolTip("导入一张原始相机图进行标注")
        capture_row.addWidget(self.open_camera_btn)
        capture_row.addWidget(self.capture_btn)
        capture_row.addWidget(self.import_btn)
        capture_layout.addLayout(capture_row)
        capture_row_2 = QHBoxLayout()
        self.live_btn = QPushButton("继续预览")
        self.open_annotation_btn = QPushButton("打开已有标注")
        self.live_btn.setEnabled(False)
        self.live_btn.setObjectName("ghostButton")
        self.open_annotation_btn.setObjectName("ghostButton")
        self.live_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.open_annotation_btn.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        capture_row_2.addWidget(self.live_btn)
        capture_row_2.addWidget(self.open_annotation_btn)
        capture_layout.addLayout(capture_row_2)
        self.capture_status = QLabel("等待相机或导入图片")
        self.capture_status.setWordWrap(True)
        capture_layout.addWidget(self.capture_status)

        label_group = QGroupBox("样本标签")
        label_form = QFormLayout(label_group)
        self.status_group, status_row = self._choice_buttons([
            ("✓ 可测量", "valid"),
            ("? 不确定", "uncertain"),
            ("× 无条纹", "no_fringe"),
        ], "valid")
        self.stripe_type_group, stripe_row = self._choice_buttons([
            ("亮纹中心", "bright"),
            ("暗纹中心", "dark"),
        ], "bright")
        self.quality_group, quality_row = self._choice_buttons([
            ("清晰", "good"),
            ("一般", "medium"),
            ("较差", "poor"),
        ], "good")
        self.pixel_scale = QLabel("{:.1f} μm/px（固定）".format(PIXEL_SCALE_UM_PER_PX))
        label_form.addRow("样本状态", status_row)
        label_form.addRow("标哪一种", stripe_row)
        label_form.addRow("图像质量", quality_row)
        label_form.addRow("像素标定", self.pixel_scale)

        tools_group = QGroupBox("标注工具")
        tools_layout = QVBoxLayout(tools_group)
        mode_row = QHBoxLayout()
        self.roi_mode_btn = QPushButton("① 圈有效区域")
        self.line_mode_btn = QPushButton("② 标条纹中心")
        self.roi_mode_btn.setCheckable(True)
        self.line_mode_btn.setCheckable(True)
        self.roi_mode_btn.setProperty("toolChoice", True)
        self.line_mode_btn.setProperty("toolChoice", True)
        self.roi_mode_btn.setChecked(True)
        self.roi_mode_btn.setToolTip("快捷键 R：拖动鼠标圈出可信条纹区域")
        self.line_mode_btn.setToolTip("快捷键 L：沿一条条纹中心依次点击")
        mode_row.addWidget(self.roi_mode_btn)
        mode_row.addWidget(self.line_mode_btn)
        tools_layout.addLayout(mode_row)
        order_row = QHBoxLayout()
        order_row.addWidget(QLabel("当前条纹序号"))
        self.next_order = QSpinBox()
        self.next_order.setRange(0, 999)
        self.finish_line_btn = QPushButton("✓ 完成本条（Enter）")
        self.finish_line_btn.setObjectName("primaryButton")
        self.finish_line_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        order_row.addWidget(self.next_order)
        order_row.addWidget(self.finish_line_btn)
        tools_layout.addLayout(order_row)
        edit_row = QHBoxLayout()
        self.undo_btn = QPushButton("↶ 撤销（Ctrl+Z）")
        self.clear_btn = QPushButton("清空全部")
        self.undo_btn.setObjectName("secondaryButton")
        self.clear_btn.setObjectName("ghostButton")
        self.undo_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.clear_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        edit_row.addWidget(self.undo_btn)
        edit_row.addWidget(self.clear_btn)
        tools_layout.addLayout(edit_row)

        result_group = QGroupBox("自动计算")
        result_form = QFormLayout(result_group)
        self.line_count_label = QLabel("0")
        self.spacing_label = QLabel("--")
        self.uncertainty_label = QLabel("--")
        self.orientation_label = QLabel("--")
        self.progress_label = QLabel("① 请先拍摄定格或导入一张原图")
        self.progress_label.setObjectName("annotationProgress")
        self.progress_label.setWordWrap(True)
        result_form.addRow("已完成中心线", self.line_count_label)
        result_form.addRow("像素间距", self.spacing_label)
        result_form.addRow("拟合不确定度", self.uncertainty_label)
        result_form.addRow("条纹方向", self.orientation_label)
        result_form.addRow("下一步", self.progress_label)

        save_group = QGroupBox("保存")
        save_layout = QVBoxLayout(save_group)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("可选：记录失焦、过曝、断裂等情况")
        self.notes.setMaximumHeight(70)
        self.save_btn = QPushButton("④ 保存训练样本（Ctrl+S）")
        self.save_btn.setObjectName("accentButton")
        self.save_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_btn.setMinimumHeight(42)
        self.save_btn.setEnabled(False)
        self.save_status = QLabel("数据将保存到 annotation_dataset")
        self.save_status.setWordWrap(True)
        save_layout.addWidget(self.notes)
        save_layout.addWidget(self.save_btn)
        save_layout.addWidget(self.save_status)

        for group in (capture_group, label_group, tools_group, result_group, save_group):
            side_layout.addWidget(group)
        side_layout.addStretch(1)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setMinimumWidth(350)
        side_scroll.setWidget(side)
        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1030, 360])
        root.addWidget(splitter)
        self._apply_annotation_style()

    def _connect_actions(self):
        self.open_camera_btn.clicked.connect(lambda: self.open_camera_requested.emit())
        self.capture_btn.clicked.connect(self.capture_current_frame)
        self.live_btn.clicked.connect(self.continue_live_preview)
        self.import_btn.clicked.connect(self.import_image)
        self.open_annotation_btn.clicked.connect(self.open_annotation)
        self.roi_mode_btn.clicked.connect(lambda _checked=False: self._set_mode("roi"))
        self.line_mode_btn.clicked.connect(lambda _checked=False: self._set_mode("centerline"))
        self.finish_line_btn.clicked.connect(self.finish_current_line)
        self.undo_btn.clicked.connect(self.canvas.undo)
        self.clear_btn.clicked.connect(self.clear_annotation)
        self.save_btn.clicked.connect(self.save_annotation)
        self.canvas.annotation_changed.connect(self._update_measurement)
        self.canvas.finish_requested.connect(self.finish_current_line)
        self.status_group.buttonClicked.connect(self._update_status_controls)

    def _build_shortcuts(self):
        shortcuts = [
            ("R", self.canvas, lambda: self._set_mode("roi"), Qt.WidgetShortcut),
            ("L", self.canvas, lambda: self._set_mode("centerline"), Qt.WidgetShortcut),
            ("Ctrl+Z", self.canvas, self.canvas.undo, Qt.WidgetShortcut),
            ("Ctrl+S", self, self.save_annotation, Qt.WidgetWithChildrenShortcut),
        ]
        for key, parent, callback, context in shortcuts:
            shortcut = QShortcut(QKeySequence(key), parent)
            shortcut.setContext(context)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def _choice_buttons(self, choices, checked_value):
        group = QButtonGroup(self)
        group.setExclusive(True)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, value in choices:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("choiceButton", True)
            button.setProperty("value", value)
            button.setChecked(value == checked_value)
            button.setMinimumHeight(34)
            group.addButton(button)
            layout.addWidget(button)
        return group, row

    def _apply_annotation_style(self):
        self.setStyleSheet(
            """
            #annotationPage QPushButton[choiceButton="true"],
            #annotationPage QPushButton[toolChoice="true"] {
                padding: 7px 9px;
                border: 1px solid #b8cad1;
                border-radius: 7px;
                background: #f7fafb;
                color: #274955;
                font-weight: 600;
            }
            #annotationPage QPushButton[choiceButton="true"]:hover,
            #annotationPage QPushButton[toolChoice="true"]:hover {
                border-color: #2f8fa3;
                background: #eaf6f8;
            }
            #annotationPage QPushButton[choiceButton="true"]:checked,
            #annotationPage QPushButton[toolChoice="true"]:checked {
                border: 2px solid #087f8c;
                background: #d8f2f3;
                color: #075c65;
            }
            #annotationProgress {
                padding: 8px;
                border-radius: 6px;
                background: #eef6f8;
                color: #24515d;
                font-weight: 600;
            }
            """
        )

    def set_live_frame(self, frame, raw_frame=None):
        self.live_frame = frame
        self.live_raw_frame = raw_frame
        if self.captured_frame is None:
            self.canvas.set_frame(frame)
            if frame is not None:
                height, width = frame.shape[:2]
                self.capture_status.setText("实时预览：{} × {}，点击“拍摄定格”后开始标注。".format(width, height))
                self.capture_btn.setEnabled(True)
        self._update_progress()

    def capture_current_frame(self):
        if self.live_frame is None:
            QMessageBox.information(self, "没有相机画面", "请先打开相机，等待实时画面出现。")
            return
        self.captured_frame = self.live_frame.copy()
        self.captured_raw_frame = self.live_raw_frame.copy() if self.live_raw_frame is not None else None
        self.source = "camera"
        self.source_path = ""
        self.canvas.set_frame(self.captured_frame)
        self.canvas.clear_annotation()
        self._new_sample_id()
        height, width = self.captured_frame.shape[:2]
        self.capture_status.setText("已定格相机画面：{} × {}。".format(width, height))
        self.live_btn.setEnabled(self.live_frame is not None)
        self._set_mode("roi")
        self.message.emit("标注页已拍摄并定格当前帧。")
        self._update_progress()

    def continue_live_preview(self):
        self.captured_frame = None
        self.captured_raw_frame = None
        self.source_path = ""
        self.canvas.clear_annotation()
        self.canvas.set_frame(self.live_frame)
        self.capture_status.setText("已恢复实时预览。")
        self.live_btn.setEnabled(False)
        self._update_progress()

    def import_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入待标注图像",
            self.project_root,
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        if not path:
            return
        frame = self._read_image(path)
        if frame is None:
            QMessageBox.warning(self, "导入失败", "无法读取所选图像。")
            return
        self.captured_frame = frame
        self.captured_raw_frame = None
        self.source = "import"
        self.source_path = path
        self.canvas.set_frame(frame)
        self.canvas.clear_annotation()
        self._new_sample_id()
        height, width = frame.shape[:2]
        self.capture_status.setText("已导入：{}（{} × {}）".format(os.path.basename(path), width, height))
        self.live_btn.setEnabled(self.live_frame is not None)
        self._set_mode("roi")
        self._update_progress()

    def open_annotation(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开已有标注",
            self.dataset_root,
            "Annotation (annotation.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            image_path = data.get("image", "image.png")
            if not os.path.isabs(image_path):
                image_path = os.path.join(os.path.dirname(path), image_path)
            frame = self._read_image(image_path)
            if frame is None:
                raise ValueError("标注对应的图像不存在或无法读取。")
            self.captured_frame = frame
            raw_path = data.get("raw_image")
            if raw_path and not os.path.isabs(raw_path):
                raw_path = os.path.join(os.path.dirname(path), raw_path)
            self.captured_raw_frame = self._read_image(raw_path) if raw_path else None
            self.source = data.get("source", "saved_annotation")
            self.source_path = image_path
            self.sample_id = data.get("sample_id") or os.path.basename(os.path.dirname(path))
            self.canvas.set_frame(frame)
            self.canvas.set_annotation(data.get("roi"), data.get("centerlines"))
            self._set_group_value(self.status_group, data.get("status", "valid"))
            self._set_group_value(self.stripe_type_group, data.get("stripe_type", "bright"))
            self._set_group_value(self.quality_group, data.get("label_quality", "good"))
            self.notes.setPlainText(data.get("notes", ""))
            next_order = max([int(line["order"]) for line in self.canvas.centerlines] or [-1]) + 1
            self.next_order.setValue(next_order)
            self.capture_status.setText("已打开标注：{}".format(self.sample_id))
            self.live_btn.setEnabled(self.live_frame is not None)
            self._update_status_controls()
            self._update_measurement()
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def finish_current_line(self):
        try:
            if not self.canvas.finish_current_line(self.next_order.value()):
                QMessageBox.information(self, "中心线未完成", "每条中心线至少需要两个点。")
                return
            self.next_order.setValue(self.next_order.value() + 1)
        except ValueError as exc:
            QMessageBox.warning(self, "序号重复", str(exc))

    def clear_annotation(self):
        self.canvas.clear_annotation()
        self.next_order.setValue(0)

    def save_annotation(self):
        frame = self.captured_frame
        if frame is None:
            QMessageBox.information(self, "没有定格图像", "请先拍摄定格或导入一张图片。")
            return
        status = self._group_value(self.status_group, "valid")
        if status != "no_fringe" and self.canvas.current_points:
            QMessageBox.information(self, "中心线未完成", "请先点击“完成本条”，或撤销当前未完成中心线。")
            return
        if status == "valid":
            if not self.canvas.roi:
                QMessageBox.information(self, "缺少 ROI", "可测量样本必须先圈出有效条纹区域。")
                return
            if len(self.canvas.centerlines) < 4:
                QMessageBox.information(self, "中心线不足", "可测量样本至少标注4条同类型条纹中心线，建议6～10条。")
                return

        saved_roi = None if status == "no_fringe" else self.canvas.roi
        saved_centerlines = [] if status == "no_fringe" else self.canvas.centerlines
        measurement = estimate_spacing(saved_centerlines)
        if status == "valid" and measurement is None:
            QMessageBox.warning(self, "无法计算间距", "当前中心线无法形成有效间距，请检查序号和标注位置。")
            return

        sample_dir = self._unique_sample_dir(self.sample_id)
        os.makedirs(sample_dir, exist_ok=False)
        image_path = os.path.join(sample_dir, "image.png")
        self._write_image(image_path, frame)
        raw_image_name = None
        if self.captured_raw_frame is not None:
            raw_image_name = "raw.png"
            self._write_image(os.path.join(sample_dir, raw_image_name), self.captured_raw_frame)
        height, width = frame.shape[:2]
        pixel_scale = PIXEL_SCALE_UM_PER_PX
        annotation = {
            "schema_version": 1,
            "sample_id": os.path.basename(sample_dir),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": self.source,
            "source_path": self.source_path,
            "image": "image.png",
            "raw_image": raw_image_name,
            "resolution": [width, height],
            "status": status,
            "stripe_type": self._group_value(self.stripe_type_group, "bright"),
            "label_quality": self._group_value(self.quality_group, "good"),
            "pixel_scale_um_per_px": pixel_scale,
            "pixel_scale_fixed": True,
            "roi": saved_roi,
            "centerlines": saved_centerlines,
            "measurement": measurement,
            "spacing_um": (
                round(float(measurement["spacing_px"]) * pixel_scale, 4)
                if measurement is not None
                else None
            ),
            "experiment_params": self.params_provider() if self.params_provider else {},
            "notes": self.notes.toPlainText().strip(),
        }
        annotation_path = os.path.join(sample_dir, "annotation.json")
        with open(annotation_path, "w", encoding="utf-8") as handle:
            json.dump(annotation, handle, ensure_ascii=False, indent=2)

        self.sample_id = os.path.basename(sample_dir)
        self.save_status.setText("已保存：{}".format(annotation_path))
        self.message.emit("标注样本已保存：{}".format(annotation_path))
        QMessageBox.information(self, "保存成功", "图像与标注已保存到：\n{}".format(sample_dir))

    def _set_mode(self, mode):
        if self.captured_frame is None or self._group_value(self.status_group, "valid") == "no_fringe":
            return
        self.canvas.set_mode(mode)
        self.canvas.setFocus()
        self.roi_mode_btn.setChecked(mode == "roi")
        self.line_mode_btn.setChecked(mode == "centerline")
        if mode == "roi":
            self.canvas_help.setText("按住左键拖动，圈出条纹清晰、可用于测量的区域。")
        else:
            self.canvas_help.setText("沿同一种条纹中心左键点击至少两个点，然后点击“完成本条”。")
        self._update_status_controls()

    def _update_measurement(self):
        self.line_count_label.setText(str(len(self.canvas.centerlines)))
        try:
            measurement = estimate_spacing(self.canvas.centerlines)
        except ValueError as exc:
            self.spacing_label.setText(str(exc))
            self.uncertainty_label.setText("--")
            self.orientation_label.setText("--")
            self._update_status_controls()
            return
        if measurement is None:
            self.spacing_label.setText("--")
            self.uncertainty_label.setText("--")
            self.orientation_label.setText("--")
            self._update_status_controls()
            return
        self.spacing_label.setText("{:.3f} px".format(measurement["spacing_px"]))
        self.uncertainty_label.setText("± {:.3f} px".format(measurement["spacing_uncertainty_px"]))
        self.orientation_label.setText("{:.2f}°".format(measurement["orientation_deg"]))
        self._update_status_controls()

    def _update_status_controls(self, *_args):
        status = self._group_value(self.status_group, "valid")
        enabled = self.captured_frame is not None and status != "no_fringe"
        for button in self.stripe_type_group.buttons():
            button.setEnabled(enabled)
        self.roi_mode_btn.setEnabled(enabled)
        self.line_mode_btn.setEnabled(enabled)
        self.finish_line_btn.setEnabled(enabled and self.canvas.mode == "centerline")
        self.undo_btn.setEnabled(enabled and bool(self.canvas.current_points or self.canvas.centerlines or self.canvas.roi))
        self.clear_btn.setEnabled(enabled and bool(self.canvas.current_points or self.canvas.centerlines or self.canvas.roi))
        self._update_progress()

    def _update_progress(self):
        status = self._group_value(self.status_group, "valid")
        ready = False
        if self.captured_frame is None:
            text = "① 请先拍摄定格或导入一张原图"
        elif status == "no_fringe":
            text = "✓ 负样本无需画线，可以直接保存"
            ready = True
        elif status == "uncertain":
            text = "可选：圈出疑似区域或标出可见中心线，然后保存"
            ready = not self.canvas.current_points
        elif not self.canvas.roi:
            text = "② 点击“圈有效区域”，拖动鼠标框选条纹"
        elif self.canvas.current_points:
            text = "③ 当前中心线尚未完成，请点击“完成本条”"
        elif len(self.canvas.centerlines) < 4:
            text = "③ 已完成 {}/4 条，建议最终标6～10条".format(len(self.canvas.centerlines))
        else:
            text = "✓ 标注完整，可以保存；当前共{}条中心线".format(len(self.canvas.centerlines))
            ready = True
        self.progress_label.setText(text)
        self.save_btn.setEnabled(ready)
        self.save_btn.setToolTip(text)

    def _new_sample_id(self):
        self.sample_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _unique_sample_dir(self, sample_id):
        base = sample_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = os.path.join(self.dataset_root, base)
        suffix = 1
        while os.path.exists(candidate):
            suffix += 1
            candidate = os.path.join(self.dataset_root, "{}_{}".format(base, suffix))
        return candidate

    def _read_image(self, path):
        try:
            import cv2
            import numpy as np

            return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

    def _write_image(self, path, frame):
        try:
            import cv2

            encoded = cv2.imencode(".png", frame)[1]
            encoded.tofile(path)
        except Exception as exc:
            raise RuntimeError("保存标注图像失败：{}".format(exc))

    def _group_value(self, group, default=None):
        button = group.checkedButton()
        return button.property("value") if button is not None else default

    def _set_group_value(self, group, value):
        for button in group.buttons():
            if button.property("value") == value:
                button.setChecked(True)
                return
