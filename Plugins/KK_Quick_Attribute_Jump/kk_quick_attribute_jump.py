import json
import os

from qgis.PyQt.QtCore import QPoint, QTimer, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QIcon
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsCsException,
    QgsExpression,
    QgsFeatureRequest,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)


SETTING_FILENAME = "KK_Quick_Attribute_Jump_Setting.json"
DEFAULT_SETTINGS = {
    "layer_id": "",
    "layer_name": "",
    "field_name": "",
}
DEFAULT_DISPLAY_SETTINGS = {
    "always_open": False,
    "button_font_size": 20,
    "table_row_height": 20,
    "table_font_size": 12,
    "zoom_without_scale": False,
}
PLUGIN_SETTINGS_FILENAME = "Setting.json"


def _enum_value(owner, group, value):
    enum_group = getattr(owner, group, None)
    if enum_group is not None and hasattr(enum_group, value):
        return getattr(enum_group, value)
    if hasattr(owner, value):
        return getattr(owner, value)
    raise AttributeError(
        "Enum value {}.{} is not available".format(group, value)
    )


class CollapsibleSection(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolButtonStyle(
            _enum_value(Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")
        )
        self.toggle.setArrowType(_enum_value(Qt, "ArrowType", "DownArrow"))
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 4, 8, 8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.content.hide()
        self.toggle.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded):
        self.content.setVisible(expanded)
        self.toggle.setArrowType(
            _enum_value(Qt, "ArrowType", "DownArrow" if expanded else "RightArrow")
        )


class LayerComboBox(QComboBox):
    def __init__(self, refresh_callback, parent=None):
        super().__init__(parent)
        self.refresh_callback = refresh_callback

    def showPopup(self):
        self.refresh_callback()
        super().showPopup()


class CategoryTablePanel(QDockWidget):
    closed = pyqtSignal(object)

    def __init__(
        self,
        iface,
        layer,
        field_name,
        category_value,
        display,
        row_height,
        font_size,
        zoom_without_scale,
        parent=None,
    ):
        super().__init__("{}: {}".format(field_name, display), parent)
        self.iface = iface
        self.layer = layer
        self.features = []
        self.field_name = field_name
        self.category_value = category_value
        self.row_height = row_height
        self.zoom_without_scale = zoom_without_scale
        self.setMinimumHeight(140)
        self.setAttribute(_enum_value(Qt, "WidgetAttribute", "WA_DeleteOnClose"))

        content = QWidget(self)
        layout = QVBoxLayout(content)
        button_layout = QHBoxLayout()
        close_button = QPushButton("閉じる", content)
        close_button.setFixedWidth(100)
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)
        resize_columns_button = QPushButton("列幅を調整", content)
        resize_columns_button.setFixedWidth(100)
        resize_columns_button.clicked.connect(self._resize_columns_to_contents)
        button_layout.addWidget(resize_columns_button)
        button_layout.addStretch(1)
        layout.addLayout(button_layout)
        self.table = QTableWidget(content)
        self.table.setSelectionBehavior(
            _enum_value(QAbstractItemView, "SelectionBehavior", "SelectRows")
        )
        self.table.setSelectionMode(
            _enum_value(QAbstractItemView, "SelectionMode", "SingleSelection")
        )
        self.table.setEditTriggers(
            _enum_value(QAbstractItemView, "EditTrigger", "NoEditTriggers")
        )
        self.table.horizontalHeader().setSectionResizeMode(
            _enum_value(QHeaderView, "ResizeMode", "Interactive")
        )
        layout.addWidget(self.table)
        self.setWidget(content)
        self.set_category(
            layer,
            field_name,
            category_value,
            display,
            row_height,
            font_size,
            zoom_without_scale,
        )
        self.table.itemSelectionChanged.connect(self._zoom_selected_feature)

    def _resize_columns_to_contents(self):
        self.table.resizeColumnsToContents()

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)

    def set_category(
        self,
        layer,
        field_name,
        category_value,
        display,
        row_height,
        font_size,
        zoom_without_scale,
    ):
        self.layer = layer
        self.field_name = field_name
        self.category_value = category_value
        self.row_height = row_height
        self.zoom_without_scale = zoom_without_scale
        self.setWindowTitle("{}: {}".format(field_name, display))
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            self.table.setColumnCount(len(layer.fields()))
            self.table.setHorizontalHeaderLabels(layer.fields().names())
            self.table.verticalHeader().setDefaultSectionSize(row_height)
            self._apply_table_font(font_size)
            self._populate(field_name, category_value)
        finally:
            self.table.blockSignals(False)

    def update_display_settings(self, row_height, font_size, zoom_without_scale):
        self.row_height = row_height
        self.zoom_without_scale = zoom_without_scale
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self._apply_table_font(font_size)

    def _apply_table_font(self, font_size):
        table_font = QFont(self.table.font())
        table_font.setPointSize(font_size)
        self.table.setFont(table_font)
        self.table.horizontalHeader().setFont(table_font)
        for column in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setFont(table_font)

    def _populate(self, field_name, category_value):
        try:
            if category_value is None:
                expression = "{} IS NULL".format(
                    QgsExpression.quotedColumnRef(field_name)
                )
            else:
                expression = "{} = {}".format(
                    QgsExpression.quotedColumnRef(field_name),
                    QgsExpression.quotedValue(category_value),
                )
            parsed_expression = QgsExpression(expression)
            if parsed_expression.hasParserError():
                self.features = []
                self.table.setRowCount(0)
                QMessageBox.warning(
                    self,
                    "属性テーブル",
                    "検索条件を作成できませんでした。\n{}".format(
                        parsed_expression.parserErrorString()
                    ),
                )
                return
            request = QgsFeatureRequest().setFilterExpression(expression)
            rows = list(self.layer.getFeatures(request))
            fields = self.layer.fields()
            self.features = rows
            self.table.setRowCount(len(rows))
            for row_index, feature in enumerate(rows):
                for column_index, field in enumerate(fields):
                    value = feature.attribute(field.name())
                    item = QTableWidgetItem("" if value is None else str(value))
                    if column_index == 0:
                        item.setData(
                            _enum_value(Qt, "ItemDataRole", "UserRole"),
                            feature.id(),
                        )
                    self.table.setItem(row_index, column_index, item)
        except (RuntimeError, TypeError, ValueError) as error:
            self.features = []
            self.table.setRowCount(0)
            QMessageBox.warning(
                self,
                "属性テーブル",
                "地物を取得して表示できませんでした。\n{}".format(error),
            )

    def _zoom_selected_feature(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.features):
            return
        try:
            feature = self.features[row]
            if not feature.hasGeometry():
                return
            canvas = self.iface.mapCanvas()
            extent = feature.geometry().boundingBox()
            if self.layer.crs() != canvas.mapSettings().destinationCrs():
                transform = QgsCoordinateTransform(
                    self.layer.crs(),
                    canvas.mapSettings().destinationCrs(),
                    QgsProject.instance(),
                )
                extent = transform.transformBoundingBox(extent)
            if extent.isEmpty():
                return
            if self.zoom_without_scale:
                canvas.setCenter(extent.center())
                canvas.refresh()
                return
            width = extent.width()
            height = extent.height()
            current = canvas.extent()
            padding_x = width * 0.2 if width else current.width() * 0.025
            padding_y = height * 0.2 if height else current.height() * 0.025
            extent = QgsRectangle(
                extent.xMinimum() - padding_x,
                extent.yMinimum() - padding_y,
                extent.xMaximum() + padding_x,
                extent.yMaximum() + padding_y,
            )
            canvas.setExtent(extent)
            canvas.refresh()
        except (QgsCsException, RuntimeError, TypeError, ValueError) as error:
            QMessageBox.warning(
                self,
                "地図移動",
                "座標を変換できませんでした。\n{}".format(error),
            )


class KKQuickAttributeJump:
    def __init__(self, iface):
        self.iface = iface
        self.panel = None
        self.table_panel = None
        self.action = None
        self.category_dialogs = []
        self.settings = dict(DEFAULT_SETTINGS)
        self.display_settings = dict(DEFAULT_DISPLAY_SETTINGS)
        self._load_settings()
        self._load_plugin_settings()
        QgsProject.instance().readProject.connect(self._on_project_read)

    def initGui(self):
        self._load_plugin_settings()
        self.action = QAction("KK_Quick_Attribute_Jump", self.iface.mainWindow())
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action.setIcon(QIcon(icon_path))
        if self.action.icon().isNull():
            QMessageBox.warning(
                self.iface.mainWindow(),
                "KK_Quick_Attribute_Jump",
                "プラグインアイコンを読み込めませんでした。\n{}".format(icon_path),
            )
        self.action.triggered.connect(self._show_panel)
        self.iface.addPluginToMenu("KK_Quick_Attribute_Jump", self.action)
        self.iface.addToolBarIcon(self.action)
        if self.display_settings["always_open"]:
            QTimer.singleShot(0, self._show_panel)

    def unload(self):
        QgsProject.instance().readProject.disconnect(self._on_project_read)
        if self.action is not None:
            self.iface.removePluginMenu("KK_Quick_Attribute_Jump", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.panel is not None:
            self.iface.mainWindow().removeDockWidget(self.panel)
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
        for dialog in list(self.category_dialogs):
            dialog.close()
        self.category_dialogs.clear()

    def _settings_path(self):
        project_path = QgsProject.instance().fileName()
        if not project_path:
            return None
        return os.path.join(os.path.dirname(project_path), SETTING_FILENAME)

    def _plugin_settings_path(self):
        return os.path.join(os.path.dirname(__file__), PLUGIN_SETTINGS_FILENAME)

    def _load_plugin_settings(self):
        path = self._plugin_settings_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self._show_error("起動設定を読み込めませんでした。\n{}".format(error))
            return
        if not isinstance(data, dict):
            self._show_error("起動設定ファイルの形式が正しくありません。")
            return
        self.display_settings["always_open"] = data.get("always_open") is True
        self.display_settings["button_font_size"] = self._valid_integer(
            data.get("button_font_size", DEFAULT_DISPLAY_SETTINGS["button_font_size"]),
            DEFAULT_DISPLAY_SETTINGS["button_font_size"],
            6,
            72,
        )
        self.display_settings["table_row_height"] = self._valid_integer(
            data.get("table_row_height", DEFAULT_DISPLAY_SETTINGS["table_row_height"]),
            DEFAULT_DISPLAY_SETTINGS["table_row_height"],
            8,
            100,
        )
        self.display_settings["table_font_size"] = self._valid_integer(
            data.get("table_font_size", DEFAULT_DISPLAY_SETTINGS["table_font_size"]),
            DEFAULT_DISPLAY_SETTINGS["table_font_size"],
            6,
            72,
        )
        self.display_settings["zoom_without_scale"] = (
            data.get("zoom_without_scale") is True
        )

    def _save_display_settings(self):
        path = self._plugin_settings_path()
        self.display_settings.update(
            {
                "always_open": self.always_open.isChecked(),
                "button_font_size": self.font_size.value(),
                "table_row_height": self.row_height.value(),
                "table_font_size": self.table_font_size.value(),
                "zoom_without_scale": self.zoom_without_scale.isChecked(),
            }
        )
        try:
            with open(path, "w", encoding="utf-8") as settings_file:
                json.dump(
                    self.display_settings,
                    settings_file,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as error:
            self._show_error("表示設定を保存できませんでした。\n{}".format(error))
            return False
        return True

    def _load_settings(self):
        path = self._settings_path()
        if path is None or not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self._show_error("設定ファイルを読み込めませんでした。\n{}".format(error))
            return False
        if not isinstance(data, dict):
            self._show_error("設定ファイルの形式が正しくありません。")
            return False
        for key in DEFAULT_SETTINGS:
            if key in data and isinstance(data[key], str):
                self.settings[key] = data[key]
        return True

    def _save_layer_settings(self):
        path = self._settings_path()
        if path is None:
            self._show_error("プロジェクトを保存してから設定を保存してください。")
            return
        if self.panel is not None:
            selected_layer_id = self.layer_combo.currentData()
            selected_layer = (
                QgsProject.instance().mapLayer(selected_layer_id)
                if selected_layer_id
                else None
            )
            self.settings.update(
                {
                    "layer_id": selected_layer.id() if selected_layer else "",
                    "layer_name": selected_layer.name() if selected_layer else "",
                    "field_name": self.field_combo.currentData() or "",
                }
            )
        try:
            with open(path, "w", encoding="utf-8") as settings_file:
                json.dump(
                    self.settings,
                    settings_file,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError as error:
            self._show_error("設定ファイルを保存できませんでした。\n{}".format(error))
            return
        QMessageBox.information(self.panel, "レイヤ設定保存", "レイヤ設定を保存しました。")

    def _save_display_settings_clicked(self):
        if not self._save_display_settings():
            return
        QMessageBox.information(self.panel, "表示設定保存", "表示設定を保存しました。")

    def _show_panel(self, *_args):
        if self.panel is None:
            self.panel = self._create_panel()
        self._refresh_layers()
        self._apply_button_style()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def _create_panel(self):
        panel = QDockWidget("KK_Quick_Attribute_Jump", self.iface.mainWindow())
        panel.setObjectName("KKQuickAttributeJumpDockWidget")
        panel.setMinimumWidth(300)
        content = QWidget(panel)
        layout = QVBoxLayout(content)
        layout.setAlignment(_enum_value(Qt, "AlignmentFlag", "AlignTop"))

        self.place_button = QPushButton("場所を探す")
        self.place_button.clicked.connect(self._show_categories)
        layout.addWidget(self.place_button)

        self.layer_section = CollapsibleSection("レイヤ設定")
        self.layer_combo = LayerComboBox(self._refresh_layers)
        self.field_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self.layer_section.content_layout.addWidget(QLabel("検索するレイヤ"))
        self.layer_section.content_layout.addWidget(self.layer_combo)
        self.layer_section.content_layout.addWidget(QLabel("検索する属性"))
        self.layer_section.content_layout.addWidget(self.field_combo)
        layer_save = QPushButton("レイヤ設定保存")
        layer_save.clicked.connect(self._save_layer_settings)
        self.layer_section.content_layout.addWidget(layer_save)
        layout.addWidget(self.layer_section)

        self.display_section = CollapsibleSection("表示設定")
        self.always_open = QCheckBox("常に表示する")
        self.always_open.setChecked(self.display_settings["always_open"])
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setValue(self.display_settings["button_font_size"])
        self.row_height = QSpinBox()
        self.row_height.setRange(8, 100)
        self.row_height.setValue(self.display_settings["table_row_height"])
        self.table_font_size = QSpinBox()
        self.table_font_size.setRange(6, 72)
        self.table_font_size.setValue(self.display_settings["table_font_size"])
        self.zoom_without_scale = QCheckBox("地物にズームするとき縮尺を変えない")
        self.zoom_without_scale.setChecked(
            self.display_settings["zoom_without_scale"]
        )
        self.display_section.content_layout.addWidget(self.always_open)
        for label_text, control in (
            ("ボタンの文字サイズ", self.font_size),
            ("属性テーブルの行の高さ", self.row_height),
            ("属性テーブルの文字の大きさ", self.table_font_size),
        ):
            setting_row = QHBoxLayout()
            setting_row.addWidget(QLabel(label_text))
            setting_row.addWidget(control)
            self.display_section.content_layout.addLayout(setting_row)
        self.display_section.content_layout.addWidget(self.zoom_without_scale)
        display_save = QPushButton("表示設定保存")
        display_save.clicked.connect(self._save_display_settings_clicked)
        self.display_section.content_layout.addWidget(display_save)
        layout.addWidget(self.display_section)
        layout.addStretch(1)

        self.font_size.valueChanged.connect(self._apply_button_style)
        self.row_height.valueChanged.connect(self._apply_table_display_settings)
        self.table_font_size.valueChanged.connect(self._apply_table_display_settings)
        self.zoom_without_scale.toggled.connect(
            self._apply_table_display_settings
        )
        panel.setWidget(content)
        self.iface.addDockWidget(
            _enum_value(Qt, "DockWidgetArea", "RightDockWidgetArea"),
            panel,
        )
        return panel

    def _apply_table_display_settings(self, *_args):
        if self.table_panel is not None:
            self.table_panel.update_display_settings(
                self.row_height.value(),
                self.table_font_size.value(),
                self.zoom_without_scale.isChecked(),
            )

    def _refresh_layers(self, prefer_settings=False):
        if self.panel is None:
            return
        selected_field = (
            self.settings.get("field_name", "")
            if prefer_settings
            else self.field_combo.currentData() or self.settings.get("field_name", "")
        )
        selected_id = (
            self.settings.get("layer_id", "")
            if prefer_settings
            else self.layer_combo.currentData()
        )
        if not selected_id:
            selected_id = self.settings.get("layer_id", "")
        layers = []
        for node in QgsProject.instance().layerTreeRoot().findLayers():
            layer = node.layer()
            if isinstance(layer, QgsVectorLayer) and layer.isValid():
                layers.append(layer)
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for layer in layers:
            self.layer_combo.addItem(layer.name(), layer.id())
        target_index = self.layer_combo.findData(selected_id)
        if target_index < 0:
            target_index = self.layer_combo.findText(self.settings.get("layer_name", ""))
        if target_index >= 0:
            self.layer_combo.setCurrentIndex(target_index)
        self.layer_combo.blockSignals(False)
        self._on_layer_changed(preferred_field=selected_field)

    def _on_layer_changed(self, *_args, preferred_field=None):
        if self.panel is None:
            return
        layer_id = self.layer_combo.currentData()
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        if isinstance(layer, QgsVectorLayer):
            for field in layer.fields():
                self.field_combo.addItem(field.name(), field.name())
            field_name = preferred_field or self.settings.get("field_name", "")
            target = self.field_combo.findData(field_name)
            if target >= 0:
                self.field_combo.setCurrentIndex(target)
        self.field_combo.blockSignals(False)

    def _show_categories(self):
        layer_id = self.layer_combo.currentData()
        field_name = self.field_combo.currentData()
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        if not isinstance(layer, QgsVectorLayer) or not field_name:
            self._show_error("検索するレイヤと属性を選択してください。")
            return

        categories = {}
        try:
            for feature in layer.getFeatures():
                value = feature.attribute(field_name)
                display = "(NULL)" if value is None else str(value)
                key = (type(value).__name__, repr(value))
                categories.setdefault(key, (display, value))
        except (RuntimeError, TypeError, ValueError) as error:
            self._show_error("カテゴリを読み込めませんでした。\n{}".format(error))
            return
        if not categories:
            self._show_error("検索対象の地物がありません。")
            return

        category_dialog = QDialog(self.panel)
        category_dialog.setWindowTitle("場所を探す")
        category_dialog.setWindowFlag(
            _enum_value(Qt, "WindowType", "WindowStaysOnTopHint"),
            True,
        )
        category_dialog.resize(320, 500)
        layout = QVBoxLayout(category_dialog)
        scroll = QScrollArea(category_dialog)
        scroll.setWidgetResizable(True)
        button_panel = QWidget()
        button_layout = QVBoxLayout(button_panel)
        button_layout.setContentsMargins(4, 4, 4, 4)
        for display, value in sorted(
            categories.values(), key=lambda item: item[0].casefold()
        ):
            button = QPushButton(display)
            self._style_button(button)
            button.clicked.connect(
                lambda _checked=False, selected=value, text=display, category=category_dialog: self._show_table(
                    category, layer, field_name, selected, text
                )
            )
            button_layout.addWidget(button)
        button_layout.addStretch(1)
        scroll.setWidget(button_panel)
        layout.addWidget(scroll)
        self.category_dialogs.append(category_dialog)
        category_dialog.finished.connect(
            lambda _result, closed=category_dialog: self._forget_category_dialog(closed)
        )
        button_left = self.place_button.mapToGlobal(QPoint(0, 0))
        category_dialog.move(
            button_left.x() - category_dialog.width(),
            button_left.y(),
        )
        category_dialog.show()

    def _show_table(self, category_dialog, layer, field_name, value, display):
        category_dialog.close()
        if self.table_panel is not None:
            self.table_panel.set_category(
                layer,
                field_name,
                value,
                display,
                self.row_height.value(),
                self.table_font_size.value(),
                self.zoom_without_scale.isChecked(),
            )
            self.table_panel.show()
            self.table_panel.raise_()
            return
        table_panel = CategoryTablePanel(
            self.iface,
            layer,
            field_name,
            value,
            display,
            self.row_height.value(),
            self.table_font_size.value(),
            self.zoom_without_scale.isChecked(),
            self.iface.mainWindow(),
        )
        self.table_panel = table_panel
        self.category_dialogs.append(table_panel)
        table_panel.closed.connect(self._forget_category_dialog)
        main_window = self.iface.mainWindow()
        main_window.addDockWidget(
            _enum_value(Qt, "DockWidgetArea", "BottomDockWidgetArea"),
            table_panel,
        )
        table_panel.show()

    def _forget_category_dialog(self, dialog):
        if dialog in self.category_dialogs:
            self.category_dialogs.remove(dialog)
        if isinstance(dialog, QDockWidget):
            self.iface.mainWindow().removeDockWidget(dialog)
            if dialog is self.table_panel:
                self.table_panel = None

    def _style_button(self, button):
        size = self.font_size.value()
        button.setStyleSheet(
            "QPushButton {{ font-size: {}px; padding: {}px 8px; }}".format(
                size, max(4, size // 3)
            )
        )
        button.setMinimumHeight(size * 2)

    def _apply_button_style(self, *_args):
        if not hasattr(self, "place_button"):
            return
        self._style_button(self.place_button)
        for dialog in self.category_dialogs:
            if isinstance(dialog, CategoryTablePanel):
                continue
            for button in dialog.findChildren(QPushButton):
                self._style_button(button)

    def _on_project_read(self, *_args):
        QTimer.singleShot(0, self._reload_project_settings)

    def _reload_project_settings(self):
        if self._load_settings() and self.panel is not None:
            self._refresh_layers(prefer_settings=True)

    @staticmethod
    def _valid_integer(value, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _show_error(message):
        QMessageBox.warning(None, "KK_Quick_Attribute_Jump", message)
