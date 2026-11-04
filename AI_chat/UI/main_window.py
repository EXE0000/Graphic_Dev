# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
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
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSpacerItem, QStatusBar, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(700, 549)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.verticalLayout_7 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.tabWidget = QTabWidget(self.centralwidget)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tabWidget.setStyleSheet(u"")
        self.page_chat = QWidget()
        self.page_chat.setObjectName(u"page_chat")
        self.verticalLayout_3 = QVBoxLayout(self.page_chat)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.verticalLayout_3.setContentsMargins(16, 16, 16, 16)
        self.textBrowser = QWebEngineView(self.page_chat)
        self.textBrowser.setObjectName(u"textBrowser")
        self.textBrowser.setUrl(QUrl(u"about:blank"))

        self.verticalLayout_3.addWidget(self.textBrowser)

        self.user_text = QTextEdit(self.page_chat)
        self.user_text.setObjectName(u"user_text")
        self.user_text.setMaximumSize(QSize(16777215, 120))

        self.verticalLayout_3.addWidget(self.user_text)

        self.attach_bar = QWidget(self.page_chat)
        self.attach_bar.setObjectName(u"attach_bar")
        self.attach_bar.setVisible(False)
        self.attach_layout = QHBoxLayout(self.attach_bar)
        self.attach_layout.setObjectName(u"attach_layout")
        self.attach_layout.setContentsMargins(0, 0, 0, 0)

        self.verticalLayout_3.addWidget(self.attach_bar)

        self.verticalLayout_2 = QVBoxLayout()
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.attach_button = QPushButton(self.page_chat)
        self.attach_button.setObjectName(u"attach_button")

        self.horizontalLayout_2.addWidget(self.attach_button)

        self.clear_button = QPushButton(self.page_chat)
        self.clear_button.setObjectName(u"clear_button")

        self.horizontalLayout_2.addWidget(self.clear_button)

        self.send_button = QPushButton(self.page_chat)
        self.send_button.setObjectName(u"send_button")

        self.horizontalLayout_2.addWidget(self.send_button)


        self.verticalLayout_2.addLayout(self.horizontalLayout_2)

        self.horizontalLayout_8 = QHBoxLayout()
        self.horizontalLayout_8.setObjectName(u"horizontalLayout_8")
        self.search_check = QCheckBox(self.page_chat)
        self.search_check.setObjectName(u"search_check")

        self.horizontalLayout_8.addWidget(self.search_check)

        self.reasoning_check = QCheckBox(self.page_chat)
        self.reasoning_check.setObjectName(u"reasoning_check")

        self.horizontalLayout_8.addWidget(self.reasoning_check)

        self.webSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_8.addItem(self.webSpacer)

        self.web_open_button = QPushButton(self.page_chat)
        self.web_open_button.setObjectName(u"web_open_button")

        self.horizontalLayout_8.addWidget(self.web_open_button)


        self.verticalLayout_2.addLayout(self.horizontalLayout_8)


        self.verticalLayout_3.addLayout(self.verticalLayout_2)

        self.verticalLayout_3.setStretch(0, 1)
        icon = QIcon(QIcon.fromTheme(QIcon.ThemeIcon.UserAvailable))
        self.tabWidget.addTab(self.page_chat, icon, "")
        self.page_api_config = QWidget()
        self.page_api_config.setObjectName(u"page_api_config")
        self.verticalLayout_6 = QVBoxLayout(self.page_api_config)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.verticalLayout_6.setContentsMargins(16, 16, 16, 16)
        self.config_scroll = QScrollArea(self.page_api_config)
        self.config_scroll.setObjectName(u"config_scroll")
        self.config_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll_contents = QWidget()
        self.config_scroll_contents.setObjectName(u"config_scroll_contents")
        self.verticalLayout_5 = QVBoxLayout(self.config_scroll_contents)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.api_key_label = QLabel(self.config_scroll_contents)
        self.api_key_label.setObjectName(u"api_key_label")
        self.api_key_label.setMinimumSize(QSize(92, 0))
        self.api_key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_3.addWidget(self.api_key_label)

        self.api_key_text = QLineEdit(self.config_scroll_contents)
        self.api_key_text.setObjectName(u"api_key_text")
        self.api_key_text.setEchoMode(QLineEdit.EchoMode.Password)

        self.horizontalLayout_3.addWidget(self.api_key_text)

        self.api_key_test = QPushButton(self.config_scroll_contents)
        self.api_key_test.setObjectName(u"api_key_test")

        self.horizontalLayout_3.addWidget(self.api_key_test)


        self.verticalLayout_5.addLayout(self.horizontalLayout_3)

        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.api_url_label = QLabel(self.config_scroll_contents)
        self.api_url_label.setObjectName(u"api_url_label")
        self.api_url_label.setMinimumSize(QSize(92, 0))
        self.api_url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_4.addWidget(self.api_url_label)

        self.api_url_text = QLineEdit(self.config_scroll_contents)
        self.api_url_text.setObjectName(u"api_url_text")
        self.api_url_text.setEchoMode(QLineEdit.EchoMode.Normal)

        self.horizontalLayout_4.addWidget(self.api_url_text)


        self.verticalLayout_5.addLayout(self.horizontalLayout_4)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.prompt_label = QLabel(self.config_scroll_contents)
        self.prompt_label.setObjectName(u"prompt_label")
        self.prompt_label.setMinimumSize(QSize(92, 0))
        self.prompt_label.setMaximumSize(QSize(16777215, 40))
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout.addWidget(self.prompt_label)

        self.prompt_text = QPlainTextEdit(self.config_scroll_contents)
        self.prompt_text.setObjectName(u"prompt_text")
        self.prompt_text.setMaximumSize(QSize(16777215, 80))

        self.horizontalLayout.addWidget(self.prompt_text)


        self.verticalLayout_5.addLayout(self.horizontalLayout)

        self.horizontalLayout_7 = QHBoxLayout()
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.search_provider_label = QLabel(self.config_scroll_contents)
        self.search_provider_label.setObjectName(u"search_provider_label")
        self.search_provider_label.setMinimumSize(QSize(92, 0))
        self.search_provider_label.setMaximumSize(QSize(92, 16777215))
        self.search_provider_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_7.addWidget(self.search_provider_label)

        self.search_provider_combo = QComboBox(self.config_scroll_contents)
        self.search_provider_combo.setObjectName(u"search_provider_combo")
        self.search_provider_combo.setMinimumSize(QSize(0, 0))

        self.horizontalLayout_7.addWidget(self.search_provider_combo)


        self.verticalLayout_5.addLayout(self.horizontalLayout_7)

        self.horizontalLayout_6 = QHBoxLayout()
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.search_key_label = QLabel(self.config_scroll_contents)
        self.search_key_label.setObjectName(u"search_key_label")
        self.search_key_label.setMinimumSize(QSize(92, 0))
        self.search_key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_6.addWidget(self.search_key_label)

        self.search_key_text = QLineEdit(self.config_scroll_contents)
        self.search_key_text.setObjectName(u"search_key_text")
        self.search_key_text.setEchoMode(QLineEdit.EchoMode.Password)

        self.horizontalLayout_6.addWidget(self.search_key_text)


        self.verticalLayout_5.addLayout(self.horizontalLayout_6)

        self.horizontalLayout_9 = QHBoxLayout()
        self.horizontalLayout_9.setObjectName(u"horizontalLayout_9")
        self.image_model_label = QLabel(self.config_scroll_contents)
        self.image_model_label.setObjectName(u"image_model_label")
        self.image_model_label.setMinimumSize(QSize(92, 0))
        self.image_model_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_9.addWidget(self.image_model_label)

        self.image_model_text = QLineEdit(self.config_scroll_contents)
        self.image_model_text.setObjectName(u"image_model_text")

        self.horizontalLayout_9.addWidget(self.image_model_text)

        self.image_models_button = QPushButton(self.config_scroll_contents)
        self.image_models_button.setObjectName(u"image_models_button")
        self.image_models_button.setMaximumSize(QSize(90, 16777215))

        self.horizontalLayout_9.addWidget(self.image_models_button)

        self.image_size_combo = QComboBox(self.config_scroll_contents)
        self.image_size_combo.addItem("")
        self.image_size_combo.addItem("")
        self.image_size_combo.addItem("")
        self.image_size_combo.addItem("")
        self.image_size_combo.setObjectName(u"image_size_combo")
        self.image_size_combo.setMaximumSize(QSize(150, 16777215))

        self.horizontalLayout_9.addWidget(self.image_size_combo)


        self.verticalLayout_5.addLayout(self.horizontalLayout_9)

        self.horizontalLayout_10 = QHBoxLayout()
        self.horizontalLayout_10.setObjectName(u"horizontalLayout_10")
        self.image_url_label = QLabel(self.config_scroll_contents)
        self.image_url_label.setObjectName(u"image_url_label")
        self.image_url_label.setMinimumSize(QSize(92, 0))
        self.image_url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_10.addWidget(self.image_url_label)

        self.image_url_text = QLineEdit(self.config_scroll_contents)
        self.image_url_text.setObjectName(u"image_url_text")

        self.horizontalLayout_10.addWidget(self.image_url_text)


        self.verticalLayout_5.addLayout(self.horizontalLayout_10)

        self.horizontalLayout_11 = QHBoxLayout()
        self.horizontalLayout_11.setObjectName(u"horizontalLayout_11")
        self.image_key_label = QLabel(self.config_scroll_contents)
        self.image_key_label.setObjectName(u"image_key_label")
        self.image_key_label.setMinimumSize(QSize(92, 0))
        self.image_key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.horizontalLayout_11.addWidget(self.image_key_label)

        self.image_key_text = QLineEdit(self.config_scroll_contents)
        self.image_key_text.setObjectName(u"image_key_text")
        self.image_key_text.setEchoMode(QLineEdit.EchoMode.Password)

        self.horizontalLayout_11.addWidget(self.image_key_text)


        self.verticalLayout_5.addLayout(self.horizontalLayout_11)

        self.verticalLayout_4 = QVBoxLayout()
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.horizontalLayout_5 = QHBoxLayout()
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.model_select_label = QLabel(self.config_scroll_contents)
        self.model_select_label.setObjectName(u"model_select_label")

        self.horizontalLayout_5.addWidget(self.model_select_label)

        self.model_select_name = QLabel(self.config_scroll_contents)
        self.model_select_name.setObjectName(u"model_select_name")

        self.horizontalLayout_5.addWidget(self.model_select_name)

        self.clear_models_button = QPushButton(self.config_scroll_contents)
        self.clear_models_button.setObjectName(u"clear_models_button")
        self.clear_models_button.setMinimumSize(QSize(90, 0))

        self.horizontalLayout_5.addWidget(self.clear_models_button)

        self.add_models_button = QPushButton(self.config_scroll_contents)
        self.add_models_button.setObjectName(u"add_models_button")
        self.add_models_button.setMinimumSize(QSize(90, 0))

        self.horizontalLayout_5.addWidget(self.add_models_button)


        self.verticalLayout_4.addLayout(self.horizontalLayout_5)

        self.models_table = QTableWidget(self.config_scroll_contents)
        if (self.models_table.columnCount() < 3):
            self.models_table.setColumnCount(3)
        __qtablewidgetitem = QTableWidgetItem()
        self.models_table.setHorizontalHeaderItem(0, __qtablewidgetitem)
        __qtablewidgetitem1 = QTableWidgetItem()
        self.models_table.setHorizontalHeaderItem(1, __qtablewidgetitem1)
        __qtablewidgetitem2 = QTableWidgetItem()
        self.models_table.setHorizontalHeaderItem(2, __qtablewidgetitem2)
        self.models_table.setObjectName(u"models_table")
        self.models_table.setMinimumSize(QSize(0, 220))

        self.verticalLayout_4.addWidget(self.models_table)


        self.verticalLayout_5.addLayout(self.verticalLayout_4)

        self.config_scroll.setWidget(self.config_scroll_contents)

        self.verticalLayout_6.addWidget(self.config_scroll)

        icon1 = QIcon(QIcon.fromTheme(QIcon.ThemeIcon.SystemLogOut))
        self.tabWidget.addTab(self.page_api_config, icon1, "")
        self.page_personalize = QWidget()
        self.page_personalize.setObjectName(u"page_personalize")
        self.verticalLayout_9 = QVBoxLayout(self.page_personalize)
        self.verticalLayout_9.setObjectName(u"verticalLayout_9")
        self.verticalLayout_9.setContentsMargins(16, 16, 16, 16)
        self.verticalLayout_8 = QVBoxLayout()
        self.verticalLayout_8.setObjectName(u"verticalLayout_8")
        self.ensure_delete_conversation_again_checkbox = QCheckBox(self.page_personalize)
        self.ensure_delete_conversation_again_checkbox.setObjectName(u"ensure_delete_conversation_again_checkbox")

        self.verticalLayout_8.addWidget(self.ensure_delete_conversation_again_checkbox)

        self.verticalSpacer_personalize = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout_8.addItem(self.verticalSpacer_personalize)


        self.verticalLayout_9.addLayout(self.verticalLayout_8)

        icon2 = QIcon(QIcon.fromTheme(QIcon.ThemeIcon.AddressBookNew))
        self.tabWidget.addTab(self.page_personalize, icon2, "")
        self.page_about = QWidget()
        self.page_about.setObjectName(u"page_about")
        icon3 = QIcon(QIcon.fromTheme(QIcon.ThemeIcon.HelpAbout))
        self.tabWidget.addTab(self.page_about, icon3, "")

        self.verticalLayout.addWidget(self.tabWidget)


        self.verticalLayout_7.addLayout(self.verticalLayout)

        MainWindow.setCentralWidget(self.centralwidget)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)

        self.tabWidget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"MainWindow", None))
        self.user_text.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u8bf7\u8f93\u5165\u6587\u672c", None))
        self.attach_button.setText(QCoreApplication.translate("MainWindow", u"\u6dfb\u52a0\u9644\u4ef6", None))
        self.clear_button.setText(QCoreApplication.translate("MainWindow", u"\u6e05\u7a7a", None))
        self.send_button.setText(QCoreApplication.translate("MainWindow", u"\u53d1\u9001", None))
        self.search_check.setText(QCoreApplication.translate("MainWindow", u"\u8054\u7f51\u641c\u7d22", None))
#if QT_CONFIG(tooltip)
        self.reasoning_check.setToolTip(QCoreApplication.translate("MainWindow", u"\u5173\u95ed\u540e\u76f4\u63a5\u4f5c\u7b54\uff0c\u901f\u5ea6\u66f4\u5feb\uff1b\u90e8\u5206\u6a21\u578b\u4e0d\u652f\u6301\u5173\u95ed\u601d\u8003", None))
#endif // QT_CONFIG(tooltip)
        self.reasoning_check.setText(QCoreApplication.translate("MainWindow", u"\u6df1\u5ea6\u601d\u8003\uff08\u90e8\u5206\u6a21\u578b\u4e0d\u53ef\u63a7\uff09", None))
#if QT_CONFIG(tooltip)
        self.web_open_button.setToolTip(QCoreApplication.translate("MainWindow", u"\u5728\u6d4f\u89c8\u5668\u91cc\u6253\u5f00\u5b8c\u6574\u804a\u5929\u754c\u9762\uff08\u8bbe\u7f6e\u4ecd\u56de\u5230\u5ba2\u6237\u7aef\u4fee\u6539\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.web_open_button.setText(QCoreApplication.translate("MainWindow", u"Web \u9875\u9762\uff08\u5168\u91cf\u529f\u80fd\uff09", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.page_chat), QCoreApplication.translate("MainWindow", u"Chat", None))
        self.api_key_label.setText(QCoreApplication.translate("MainWindow", u"API KEY:", None))
        self.api_key_test.setText(QCoreApplication.translate("MainWindow", u"\u68c0\u6d4b", None))
        self.api_url_label.setText(QCoreApplication.translate("MainWindow", u"API \u5730\u5740:", None))
        self.prompt_label.setText(QCoreApplication.translate("MainWindow", u"\u7cfb\u7edf\u63d0\u793a\u8bcd:", None))
        self.prompt_text.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u7559\u7a7a\u5219\u4f7f\u7528\u5185\u7f6e\u7684\u9ed8\u8ba4\u63d0\u793a\u8bcd", None))
        self.search_provider_label.setText(QCoreApplication.translate("MainWindow", u"\u641c\u7d22\u63d0\u4f9b\u5546:", None))
        self.search_key_label.setText(QCoreApplication.translate("MainWindow", u"\u641c\u7d22 KEY:", None))
        self.image_model_label.setText(QCoreApplication.translate("MainWindow", u"\u56fe\u7247\u6a21\u578b:", None))
        self.image_model_text.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u7559\u7a7a\u5219\u4e0d\u542f\u7528\u6587\u751f\u56fe\uff08\u5982 gpt-image-1 / wanx2.1-t2i-turbo / flux.1-schnell\uff09", None))
#if QT_CONFIG(tooltip)
        self.image_models_button.setToolTip(QCoreApplication.translate("MainWindow", u"\u4ece\u56fe\u7247\u63a5\u53e3\u62c9\u53d6\u53ef\u7528\u6a21\u578b", None))
#endif // QT_CONFIG(tooltip)
        self.image_models_button.setText(QCoreApplication.translate("MainWindow", u"\u9009\u62e9\u2026", None))
        self.image_size_combo.setItemText(0, QCoreApplication.translate("MainWindow", u"1024x1024", None))
        self.image_size_combo.setItemText(1, QCoreApplication.translate("MainWindow", u"1024x1792", None))
        self.image_size_combo.setItemText(2, QCoreApplication.translate("MainWindow", u"1792x1024", None))
        self.image_size_combo.setItemText(3, QCoreApplication.translate("MainWindow", u"768x1344", None))

        self.image_url_label.setText(QCoreApplication.translate("MainWindow", u"\u56fe\u7247 API:", None))
        self.image_url_text.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u7559\u7a7a\u5219\u6cbf\u7528\u4e0a\u9762\u7684 API \u5730\u5740", None))
        self.image_key_label.setText(QCoreApplication.translate("MainWindow", u"\u56fe\u7247 KEY:", None))
        self.image_key_text.setPlaceholderText(QCoreApplication.translate("MainWindow", u"\u7559\u7a7a\u5219\u6cbf\u7528\u4e0a\u9762\u7684 KEY", None))
        self.model_select_label.setText(QCoreApplication.translate("MainWindow", u"\u5f53\u524d\u5df2\u9009\u6a21\u578b:", None))
        self.model_select_name.setText("")
        self.clear_models_button.setText(QCoreApplication.translate("MainWindow", u"\u5168\u90e8\u5220\u9664", None))
        self.add_models_button.setText(QCoreApplication.translate("MainWindow", u"\u6dfb\u52a0\u6a21\u578b", None))
        ___qtablewidgetitem = self.models_table.horizontalHeaderItem(0)
        ___qtablewidgetitem.setText(QCoreApplication.translate("MainWindow", u"\u6a21\u578b\u5217\u8868", None))
        ___qtablewidgetitem1 = self.models_table.horizontalHeaderItem(1)
        ___qtablewidgetitem1.setText(QCoreApplication.translate("MainWindow", u"\u9009\u4e2d", None))
        ___qtablewidgetitem2 = self.models_table.horizontalHeaderItem(2)
        ___qtablewidgetitem2.setText(QCoreApplication.translate("MainWindow", u"\u5220\u9664", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.page_api_config), QCoreApplication.translate("MainWindow", u"API \u914d\u7f6e", None))
        self.ensure_delete_conversation_again_checkbox.setText(QCoreApplication.translate("MainWindow", u"\u5220\u9664\u5bf9\u8bdd\u65f6\u5f39\u51fa\u201c\u786e\u8ba4\u5220\u9664\u201d\u5f39\u7a97", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.page_personalize), QCoreApplication.translate("MainWindow", u"\u4e2a\u6027\u5316", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.page_about), QCoreApplication.translate("MainWindow", u"\u5173\u4e8e", None))
    # retranslateUi

