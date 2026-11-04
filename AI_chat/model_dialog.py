from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QDialog, QListWidgetItem

from UI.modelSelection import Ui_ModelDialog

class ModelPickerDialog(QDialog, Ui_ModelDialog):
    def __init__(self, models, existing=None, parent=None, single=False, title=None,
                 checked=None):
        super().__init__(parent=parent)
        self.setupUi(self)
        self._existing = set(existing or ())
        self._single = single
        self._checked = set(checked or ())
        if title:
            self.setWindowTitle(title)
        if single:
            self.select_all_btn.hide()
            self.clear_btn.hide()
            self.ok_btn.setText("选择")

        self.model_list.setStyleSheet("""
            QListView::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #c9ced6;
                border-radius: 4px;
                background: #ffffff;
            }
            QListView::indicator:checked {
                background: #0a84ff;
                border-color: #0a84ff;
            }
            QListView::item {
                padding-left: 8px;
                padding-top: 2px;
                padding-bottom: 2px;
            }
        """)

        self._fill(models)

        self.search_edit.textChanged.connect(self._filter)
        self.select_all_btn.clicked.connect(self._check_visible)
        self.clear_btn.clicked.connect(self._uncheck_all)
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.model_list.itemChanged.connect(self._on_item_changed)
        self.model_list.itemDoubleClicked.connect(self._toggle_item)

        self.search_edit.setFocus()
        self._refresh_count()
        self._update_hint()

    def _on_item_changed(self, item):
        if self._single and item.checkState() == Qt.CheckState.Checked:
            self.model_list.blockSignals(True)
            for other in self._iter_items():
                if other is not item and other.checkState() == Qt.CheckState.Checked:
                    other.setCheckState(Qt.CheckState.Unchecked)
            self.model_list.blockSignals(False)
        self._refresh_count()

    def _fill(self, models):
        for name in models:
            already = name in self._existing
            item = QListWidgetItem(name + ("（已添加）" if already else ""))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)

            if already and not self._single:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(Qt.GlobalColor.gray)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if name in self._checked
                    else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(0, 26))
            self.model_list.addItem(item)

    def _iter_items(self):
        for i in range(self.model_list.count()):
            yield self.model_list.item(i)

    def _visible_items(self):
        return [it for it in self._iter_items() if not it.isHidden()]

    def _name_of(self, item):
        return item.data(Qt.ItemDataRole.UserRole) or item.text()

    def checked_models(self):
        return [self._name_of(it) for it in self._iter_items()
                if it.checkState() == Qt.CheckState.Checked]

    def _filter(self, text):
        key = text.strip().lower()
        for item in self._iter_items():
            item.setHidden(bool(key) and key not in item.text().lower())
        self.model_list.scrollToTop()
        self._refresh_count()
        self._update_hint()

    def _check_visible(self):
        for item in self._visible_items():
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)

    def _uncheck_all(self):
        for item in self._iter_items():
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _toggle_item(self, item):
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _update_hint(self):
        if self.model_list.count() == 0:
            self.empty_label.setText("没有可用的模型")
        elif not self._visible_items():
            self.empty_label.setText("没有匹配的模型")
        else:
            self.empty_label.setText("")

    def _refresh_count(self):
        self.count_label.setText(
            f"{len(self._visible_items())}/{self.model_list.count()}"
            f" · 已选 {len(self.checked_models())}"
        )
