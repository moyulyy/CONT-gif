#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
iOS / macOS 风格 Qt 组件库。

供主界面与交互窗口复用:
    LIGHT / DARK 主题字典, QSS 样式表, apply_theme()
    自绘控件: Switch(滑动开关) / SegmentedControl(分段控件) / ComboBox
    容器: Card(卡片) / SliderField / 若干小工具函数
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, Property, QPropertyAnimation, QRectF,
                            QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)


# ==========================================================================
# 主题
# ==========================================================================
LIGHT = {
    "window": "#F2F2F7", "container": "#F2F2F7", "sidebar": "#EFEFF4",
    "card": "#FFFFFF", "card_border": "#E6E6EB",
    "text": "#1C1C1E", "text_dim": "#8A8A8E", "text_soft": "#3A3A3C",
    "field": "#F2F2F7", "field_border": "#E3E3E8", "field_focus": "#007AFF",
    "accent": "#007AFF", "accent_hover": "#2A90FF", "accent_press": "#0063D1",
    "accent_disabled": "#B9DAFF",
    "ghost": "#6E6E73", "ghost_hover": "#EDEDF2",
    "nav_hover": "#E4E4EA", "nav_checked": "#007AFF",
    "seg_track": "#E9E9EB", "seg_sel": "#FFFFFF", "seg_sel_text": "#1C1C1E",
    "switch_on": "#34C759", "switch_off": "#E4E4E9", "knob": "#FFFFFF",
    "track": "#E3E3E8", "chunk": "#007AFF",
    "log_bg": "#1C1C1E", "log_text": "#D7D7DC",
    "close": "#FF5F57", "close_h": "#E14840",
    "min": "#FEBC2E", "min_h": "#E0A324",
    "max": "#28C840", "max_h": "#20A834",
    "menu_bg": "#FFFFFF", "shadow": (0, 10, 40, 70),
    "list_bg": "#FFFFFF", "list_sel": "#007AFF",
    "ok": "#34C759", "warn": "#FF9500", "bad": "#FF3B30",
    "viewer_bg": "#FFFFFF",
}
DARK = {
    "window": "#1B1B1D", "container": "#1B1B1D", "sidebar": "#252528",
    "card": "#2C2C2E", "card_border": "#3A3A3C",
    "text": "#F2F2F7", "text_dim": "#98989D", "text_soft": "#D1D1D6",
    "field": "#3A3A3C", "field_border": "#48484A", "field_focus": "#0A84FF",
    "accent": "#0A84FF", "accent_hover": "#3D9BFF", "accent_press": "#0069D9",
    "accent_disabled": "#2A4A6B",
    "ghost": "#98989D", "ghost_hover": "#3A3A3C",
    "nav_hover": "#3A3A3C", "nav_checked": "#0A84FF",
    "seg_track": "#3A3A3C", "seg_sel": "#6C6C70", "seg_sel_text": "#FFFFFF",
    "switch_on": "#30D158", "switch_off": "#48484A", "knob": "#FFFFFF",
    "track": "#3A3A3C", "chunk": "#0A84FF",
    "log_bg": "#141416", "log_text": "#C7C7CC",
    "close": "#FF5F57", "close_h": "#E14840",
    "min": "#FEBC2E", "min_h": "#E0A324",
    "max": "#28C840", "max_h": "#20A834",
    "menu_bg": "#2C2C2E", "shadow": (0, 10, 40, 120),
    "list_bg": "#2C2C2E", "list_sel": "#0A84FF",
    "ok": "#30D158", "warn": "#FFD60A", "bad": "#FF453A",
    "viewer_bg": "#FFFFFF",
}

THEME = dict(LIGHT)

QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    outline: none;
}
#Window {
    background: $container;
    border: 1px solid $card_border;
    border-radius: 14px;
}
#Window[Maxed="true"] {
    border: none;
    border-radius: 0;
}
#TitleBar { background: transparent; }
#AppName { color: $text; font-size: 13px; font-weight: 600; }
#Sidebar { background: $sidebar; border-top-left-radius: 13px; border-bottom-left-radius: 13px; }
#SidebarBrand { color: $text; font-size: 15px; font-weight: 700; }
#SidebarSub { color: $text_dim; font-size: 11px; }
#NavItem {
    background: transparent; border: none; border-radius: 8px;
    color: $text_soft; font-size: 13px; font-weight: 500;
    text-align: left; padding: 8px 12px;
}
#NavItem:hover { background: $nav_hover; }
#NavItem:checked { background: $nav_checked; color: #FFFFFF; font-weight: 600; }
#H1 { color: $text; font-size: 22px; font-weight: 700; }
#Sub { color: $text_dim; font-size: 12px; }
#Card {
    background: $card; border: 1px solid $card_border; border-radius: 12px;
}
#CardTitle { color: $text_dim; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
#FieldLabel { color: $text_soft; font-size: 13px; }
#Hint { color: $text_dim; font-size: 11px; }
#Value { color: $accent; font-size: 12px; font-weight: 600; }
#Footer { color: $text_dim; font-size: 11px; }
#Chip { background: $field; border: 1px solid $field_border; border-radius: 8px;
        color: $text_soft; font-size: 11px; padding: 2px 8px; }
#Preview { background: $field; border: 1px solid $field_border; border-radius: 10px;
           color: $text_dim; font-size: 12px; }

QLineEdit, QSpinBox, QDoubleSpinBox {
    background: $field; border: 1px solid $field_border; border-radius: 8px;
    padding: 6px 10px; color: $text; selection-background-color: $accent;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid $field_focus; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: $text_dim; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; border: none; }

QComboBox {
    background: $field; border: 1px solid $field_border; border-radius: 8px;
    padding: 5px 10px; color: $text; min-width: 90px;
}
QComboBox::drop-down { border: none; width: 26px; background: transparent; }
QComboBox::down-arrow { image: none; width: 0px; height: 0px; }
QComboBox QAbstractItemView {
    background: $menu_bg; border: 1px solid $card_border; border-radius: 8px;
    selection-background-color: $accent; selection-color: #FFFFFF; padding: 4px;
    outline: none; color: $text;
}

QPushButton#Primary {
    background: $accent; color: #FFFFFF; border: none; border-radius: 9px;
    padding: 8px 20px; font-size: 13px; font-weight: 600;
}
QPushButton#Primary:hover { background: $accent_hover; }
QPushButton#Primary:pressed { background: $accent_press; }
QPushButton#Primary:disabled { background: $accent_disabled; }

QPushButton#Secondary {
    background: $card; color: $accent; border: 1px solid $card_border;
    border-radius: 9px; padding: 8px 16px; font-size: 13px; font-weight: 600;
}
QPushButton#Secondary:hover { background: $field; }
QPushButton#Secondary:pressed { background: $field_border; }
QPushButton#Secondary:disabled { color: $text_dim; }

QPushButton#Danger {
    background: $card; color: $bad; border: 1px solid $card_border;
    border-radius: 9px; padding: 8px 16px; font-size: 13px; font-weight: 600;
}
QPushButton#Danger:hover { background: $field; }
QPushButton#Danger:disabled { color: $text_dim; }

QPushButton#Ghost {
    background: transparent; color: $ghost; border: none; border-radius: 8px;
    padding: 8px 12px; font-size: 13px;
}
QPushButton#Ghost:hover { background: $ghost_hover; }

QPushButton#Browse {
    background: $field; color: $text_soft; border: 1px solid $field_border;
    border-radius: 8px; padding: 6px 14px; font-size: 12px;
}
QPushButton#Browse:hover { background: $ghost_hover; }

QPushButton#TrafficClose { background: $close; border: none; border-radius: 6px; }
QPushButton#TrafficClose:hover { background: $close_h; }
QPushButton#TrafficMin { background: $min; border: none; border-radius: 6px; }
QPushButton#TrafficMin:hover { background: $min_h; }
QPushButton#TrafficMax { background: $max; border: none; border-radius: 6px; }
QPushButton#TrafficMax:hover { background: $max_h; }

QWidget#SegTrack { background: $seg_track; border-radius: 9px; }
QPushButton#Segment {
    background: transparent; border: none; border-radius: 7px;
    color: $text_soft; font-size: 12px; padding: 5px 10px;
}
QPushButton#Segment:hover { color: $text; }
QPushButton#Segment:checked { background: $seg_sel; color: $seg_sel_text; font-weight: 600; }

QSlider::groove:horizontal { height: 4px; background: $track; border-radius: 2px; }
QSlider::sub-page:horizontal { height: 4px; background: $chunk; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -7px 0; border-radius: 8px;
    background: #FFFFFF; border: 1px solid $field_border;
}
QSlider::handle:horizontal:hover { border: 1px solid $chunk; }

QProgressBar {
    background: $track; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent;
}
QProgressBar::chunk { background: $chunk; border-radius: 4px; }

QPlainTextEdit {
    background: $log_bg; color: $log_text; border: none; border-radius: 10px;
    padding: 8px; font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
}
QScrollArea { background: transparent; border: none; }
#PageInner { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { height: 10px; background: transparent; }
QScrollBar::handle:vertical { background: $track; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $text_dim; }
QScrollBar::handle:horizontal { background: $track; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QListWidget {
    background: $list_bg; border: 1px solid $field_border; border-radius: 8px;
    color: $text; padding: 4px; outline: none;
}
QListWidget::item { padding: 4px 6px; border-radius: 6px; }
QListWidget::item:selected { background: $list_sel; color: #FFFFFF; }

QToolTip {
    background: $card; color: $text; border: 1px solid $card_border;
    border-radius: 6px; padding: 4px 8px;
}
QMessageBox { background: $card; }
QMessageBox QLabel { color: $text; }
QMessageBox QPushButton {
    background: $accent; color: #FFFFFF; border: none; border-radius: 8px;
    padding: 6px 18px; min-width: 60px;
}
QMessageBox QPushButton:hover { background: $accent_hover; }
"""


def build_qss(theme):
    out = QSS
    for key in sorted(theme, key=len, reverse=True):
        val = theme[key]
        if isinstance(val, tuple):
            continue
        out = out.replace("$" + key, val)
    return out


def apply_theme(app, theme):
    global THEME
    THEME = dict(theme)
    app.setStyleSheet(build_qss(THEME))


# ==========================================================================
# 自绘控件
# ==========================================================================
class Switch(QAbstractButton):
    """iOS 风格滑动开关。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(QSize(46, 28))
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self):
        return self._pos

    def _set_offset(self, v):
        self._pos = float(v)
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked, animate=True):  # noqa: N802
        if animate:
            super().setChecked(checked)
        else:
            self.blockSignals(True)
            super().setChecked(checked)
            self.blockSignals(False)
            self._pos = 1.0 if checked else 0.0
            self.update()

    def apply_theme(self, theme):
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        off = QColor(THEME["switch_off"])
        on = QColor(THEME["switch_on"])
        r = self._pos
        track = QColor(
            int(off.red() + (on.red() - off.red()) * r),
            int(off.green() + (on.green() - off.green()) * r),
            int(off.blue() + (on.blue() - off.blue()) * r),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        d = h - 4
        x = 2 + r * (w - d - 4)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(QRectF(x, 3.0, d, d))
        p.setBrush(QColor(THEME["knob"]))
        p.drawEllipse(QRectF(x, 2.0, d, d))


class SegmentedControl(QWidget):
    """iOS 分段选择器。"""

    changed = Signal(str)

    def __init__(self, items, value=None, parent=None):
        super().__init__(parent)
        self.setObjectName("SegTrack")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        for key, label in items:
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(btn)
            lay.addWidget(btn)
            self._buttons[key] = btn
        if value is not None:
            self.set_value(value)

    def set_value(self, key):
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def value(self):
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return None


class ComboBox(QComboBox):
    """带自绘箭头的下拉框 (macOS 风格)。"""

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(THEME["text_dim"]), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cx = self.width() - 15
        cy = self.height() / 2 - 1
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 2)
        path.lineTo(cx, cy + 2.5)
        path.lineTo(cx + 4, cy - 2)
        p.drawPath(path)
        p.end()


class Card(QFrame):
    """圆角卡片, 带标题。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(18, 14, 18, 16)
        self.vbox.setSpacing(10)
        if title:
            lab = QLabel(title.upper())
            lab.setObjectName("CardTitle")
            self.vbox.addWidget(lab)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.vbox.addLayout(self.body)

    def add(self, widget):
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout):
        self.body.addLayout(layout)
        return layout


def hline(theme_key="card_border"):
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {THEME[theme_key]}; border: none;")
    return line


def field_label(text, width=96):
    lab = QLabel(text)
    lab.setObjectName("FieldLabel")
    lab.setMinimumWidth(width)
    return lab


def hint_label(text):
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


class SliderField(QWidget):
    """标签 + 滑杆 + 数值。"""

    def __init__(self, label, minv, maxv, value, steps=100, fmt="{:.2f}", width=96):
        super().__init__()
        self._min, self._max, self._steps = minv, maxv, steps
        self._fmt = fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lab = field_label(label, width)
        lay.addWidget(lab)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, steps)
        self.slider.setValue(int(round((value - minv) / (maxv - minv) * steps)))
        self.slider.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.slider, 1)
        self.value_label = QLabel(self._fmt.format(value))
        self.value_label.setObjectName("Value")
        self.value_label.setMinimumWidth(46)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.value_label)
        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, _):
        self.value_label.setText(self._fmt.format(self.value()))

    def value(self):
        return round(self._min + (self._max - self._min) * self.slider.value() / self._steps, 3)

    def set_value(self, v):
        self.slider.setValue(int(round((v - self._min) / (self._max - self._min) * self._steps)))
