# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtWidgets import QTabWidget, QWidget, QScrollArea, QApplication
from PyQt5.QtGui import QPalette

from constants import KEYCODE_BTN_RATIO
from flowlayout import FlowLayout
from keycodes import KEYCODES_SPECIAL, KEYCODES_BASIC, KEYCODES_MODIFIERS, KEYCODES_NUMPAD, KEYCODES_SHIFTED, \
    KEYCODES_ISO, KEYCODES_COMBOMOD, KEYCODES_QUANTUM, KEYCODES_MACRO, KEYCODES_LAYERS, \
    KEYCODES_MOUSE, KEYCODES_MEDIA, \
    KEYCODES_USER, Keycode, \
    KEYCODES_TAP_DANCE, KEYCODES_MIDI, KEYCODES_DIAL, KEYCODES_LIGHTS
from square_button import SquareButton
from util import tr, KeycodeDisplay


class Tab(QObject):

    keycode_changed = pyqtSignal(int)

    def __init__(self, parent, label, keycodes, word_wrap=False, prefix_buttons=None):
        super().__init__()

        self.label = label
        self.keycodes = keycodes
        self.word_wrap = word_wrap

        self.container = QScrollArea(parent)
        self.layout = FlowLayout()
        if prefix_buttons:
            for btn in prefix_buttons:
                self.layout.addWidget(btn)

        self.container.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.container.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.container.setWidgetResizable(True)

        w = QWidget()
        w.setLayout(self.layout)
        self.container.setWidget(w)

        self.buttons = []

    def recreate_buttons(self):
        for btn in self.buttons:
            btn.hide()
            btn.deleteLater()
        self.buttons = []

        for keycode in self.keycodes:
            btn = SquareButton()
            btn.setWordWrap(self.word_wrap)
            btn.setRelSize(KEYCODE_BTN_RATIO)
            btn.setToolTip(Keycode.tooltip(keycode.code))
            btn.clicked.connect(lambda st, k=keycode: self.keycode_changed.emit(k.code))
            btn.keycode = keycode
            self.layout.addWidget(btn)
            self.buttons.append(btn)

        self.relabel_buttons()
        self.container.setVisible(len(self.buttons) > 0)

    def relabel_buttons(self):
        for widget in self.buttons:
            qmk_id = widget.keycode.qmk_id
            if qmk_id in KeycodeDisplay.keymap_override:
                label = KeycodeDisplay.keymap_override[qmk_id]
                highlight_color = QApplication.palette().color(QPalette.Link).getRgb()
                widget.setStyleSheet("QPushButton {color: rgb%s;}" % str(highlight_color))
            else:
                label = widget.keycode.label
                widget.setStyleSheet("QPushButton {}")
            widget.setText(label.replace("&", "&&"))


class TabbedKeycodes(QTabWidget):

    keycode_changed = pyqtSignal(int)
    anykey = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.target = None
        self.is_tray = False

        # create the "Any" keycode button
        any_btn = SquareButton()
        any_btn.setText("Any")
        any_btn.setRelSize(KEYCODE_BTN_RATIO)
        any_btn.clicked.connect(lambda: self.anykey.emit())

        self.tabs = [
            Tab(self, "基础键", KEYCODES_SPECIAL + KEYCODES_BASIC, prefix_buttons=[any_btn]),
            Tab(self, "修饰键", KEYCODES_MODIFIERS),
            Tab(self, "数字键", KEYCODES_NUMPAD),
            Tab(self, "符号键", KEYCODES_SHIFTED + KEYCODES_ISO),
            Tab(self, "鼠标键", KEYCODES_MOUSE),
            Tab(self, "媒体键", KEYCODES_MEDIA),
            Tab(self, "组合键", KEYCODES_COMBOMOD),
            Tab(self, "切层键", KEYCODES_LAYERS),
            Tab(self, "QMK功能", KEYCODES_QUANTUM),
            Tab(self, "灯光键", KEYCODES_LIGHTS),
            Tab(self, "MIDI", KEYCODES_MIDI),
            Tab(self, "滚轮功能", KEYCODES_DIAL),
            Tab(self, "宏功能", KEYCODES_MACRO),
            Tab(self, "按键复用", KEYCODES_TAP_DANCE),
            Tab(self, "自定义", KEYCODES_USER),
        ]

        for tab in self.tabs:
            tab.keycode_changed.connect(lambda kc: self.keycode_changed.emit(kc))

        self.recreate_keycode_buttons()
        KeycodeDisplay.notify_keymap_override(self)

    def recreate_keycode_buttons(self):
        while self.count() > 0:
            self.removeTab(0)

        for tab in self.tabs:
            tab.recreate_buttons()
            if tab.buttons:
                self.addTab(tab.container, tr("TabbedKeycodes", tab.label))

    def on_keymap_override(self):
        for tab in self.tabs:
            tab.relabel_buttons()

    @classmethod
    def set_tray(cls, tray):
        cls.tray = tray

    @classmethod
    def open_tray(cls, target):
        cls.tray.show()
        if cls.tray.target is not None and cls.tray.target != target:
            cls.tray.target.deselect()
        cls.tray.target = target

    @classmethod
    def close_tray(cls):
        if cls.tray.target is not None:
            cls.tray.target.deselect()
        cls.tray.target = None
        cls.tray.hide()

    def make_tray(self):
        self.is_tray = True
        TabbedKeycodes.set_tray(self)

        self.keycode_changed.connect(self.on_tray_keycode_changed)
        self.anykey.connect(self.on_tray_anykey)

    def on_tray_keycode_changed(self, kc):
        if self.target is not None:
            self.target.on_keycode_changed(kc)

    def on_tray_anykey(self):
        if self.target is not None:
            self.target.on_anykey()
