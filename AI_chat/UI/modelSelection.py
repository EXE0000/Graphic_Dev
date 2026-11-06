# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'modelSelection.ui'
##
## Created by: Qt User Interface Compiler version 6.12.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QSpacerItem, QVBoxLayout,
    QWidget)

class Ui_ModelDialog(object):
    def setupUi(self, ModelDialog):
        if not ModelDialog.objectName():
            ModelDialog.setObjectName(u"ModelDialog")
        ModelDialog.resize(659, 479)
        ModelDialog.setMinimumSize(QSize(480, 420))
        ModelDialog.setSizeGripEnabled(True)
        self.verticalLayout_2 = QVBoxLayout(ModelDialog)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.search_edit = QLineEdit(ModelDialog)
        self.search_edit.setObjectName(u"search_edit")
        self.search_edit.setClearButtonEnabled(True)

        self.horizontalLayout.addWidget(self.search_edit)

        self.count_label = QLabel(ModelDialog)
        self.count_label.setObjectName(u"count_label")
        self.count_label.setMinimumSize(QSize(150, 0))
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignTrailing|Qt.AlignmentFlag.AlignVCenter)

        self.horizontalLayout.addWidget(self.count_label)


        self.verticalLayout.addLayout(self.horizontalLayout)

        self.empty_label = QLabel(ModelDialog)
        self.empty_label.setObjectName(u"empty_label")
        self.empty_label.setEnabled(True)
        self.empty_label.setMinimumSize(QSize(0, 17))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignLeading|Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)

        self.verticalLayout.addWidget(self.empty_label)

        self.model_list = QListWidget(ModelDialog)
        self.model_list.setObjectName(u"model_list")
        self.model_list.setMinimumSize(QSize(0, 0))
        self.model_list.setAlternatingRowColors(True)
        self.model_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.model_list.setSpacing(2)
        self.model_list.setUniformItemSizes(True)

        self.verticalLayout.addWidget(self.model_list)

        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.select_all_btn = QPushButton(ModelDialog)
        self.select_all_btn.setObjectName(u"select_all_btn")
        self.select_all_btn.setMinimumSize(QSize(96, 0))
        self.select_all_btn.setAutoDefault(False)

        self.horizontalLayout_3.addWidget(self.select_all_btn)

        self.clear_btn = QPushButton(ModelDialog)
        self.clear_btn.setObjectName(u"clear_btn")
        self.clear_btn.setMinimumSize(QSize(96, 0))
        self.clear_btn.setAutoDefault(False)

        self.horizontalLayout_3.addWidget(self.clear_btn)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_3.addItem(self.horizontalSpacer)

        self.cancel_btn = QPushButton(ModelDialog)
        self.cancel_btn.setObjectName(u"cancel_btn")
        self.cancel_btn.setMinimumSize(QSize(96, 0))

        self.horizontalLayout_3.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton(ModelDialog)
        self.ok_btn.setObjectName(u"ok_btn")
        self.ok_btn.setMinimumSize(QSize(96, 0))

        self.horizontalLayout_3.addWidget(self.ok_btn)


        self.verticalLayout.addLayout(self.horizontalLayout_3)


        self.verticalLayout_2.addLayout(self.verticalLayout)


        self.retranslateUi(ModelDialog)

        self.ok_btn.setDefault(True)


        QMetaObject.connectSlotsByName(ModelDialog)
    # setupUi

    def retranslateUi(self, ModelDialog):
        ModelDialog.setWindowTitle(QCoreApplication.translate("ModelDialog", u"\u6a21\u578b\u9009\u62e9", None))
        self.search_edit.setPlaceholderText(QCoreApplication.translate("ModelDialog", u"\u641c\u7d22\u6a21\u578b\u540d\uff0c\u4f8b\u5982 qwen / max", None))
        self.count_label.setText("")
        self.empty_label.setText(QCoreApplication.translate("ModelDialog", u"\u300c\u6ca1\u6709\u5339\u914d\u7684\u6a21\u578b\u300d", None))
        self.select_all_btn.setText(QCoreApplication.translate("ModelDialog", u"\u5168\u9009\u5f53\u524d\u7ed3\u679c", None))
        self.clear_btn.setText(QCoreApplication.translate("ModelDialog", u"\u6e05\u7a7a\u52fe\u9009", None))
        self.cancel_btn.setText(QCoreApplication.translate("ModelDialog", u"\u53d6\u6d88", None))
        self.ok_btn.setText(QCoreApplication.translate("ModelDialog", u"\u6dfb\u52a0", None))
    # retranslateUi

