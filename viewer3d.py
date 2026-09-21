#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
交互式 3D 结构浏览器 (基于 QWebEngineView + 本地 3Dmol.js)。

CONTCAR 只有一帧结构, 所以这里不做播放/进度条, 只提供:
    * 实时拖动旋转 / 滚轮缩放 查看结构
    * 「重置视角」把视角恢复到基准
    * 一键把当前相机视角捕获为 GIF 的关键帧 (视角 A)

注意: 由于 QWebEngineView 的限制, 承载它的 Qt 祖先控件不能使用
      QGraphicsDropShadowEffect, 否则 WebEngine 内容无法渲染。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

import contcar_to_gif as core


class StructureViewer(QWidget):
    """内嵌的 3D 结构查看器 (单结构)。"""

    ready = Signal()
    cameraCaptured = Signal(str, list, int)   # slot('A'/'B'), view(8 floats), css_width

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tmpdir = Path(tempfile.mkdtemp(prefix="3dmol-viewer-"))
        self._ready = False
        self._pan = [0.0, 0.0]
        self._pending: List[str] = []
        self._poll_count = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(300)

        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.web = QWebEngineView(self)
        self.web.setMinimumHeight(260)
        self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.web.loadFinished.connect(self._on_load)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        holder = QFrame()
        holder.setObjectName("Card")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(6, 6, 6, 6)
        hl.addWidget(self.web)
        root.addWidget(holder, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.lbl_tip = QLabel("拖动旋转 · 滚轮缩放")
        self.lbl_tip.setObjectName("Chip")
        bar.addWidget(self.lbl_tip)
        bar.addStretch(1)

        self.btn_capA = QPushButton("设为视角 A")
        self.btn_capA.setObjectName("Secondary")
        self.btn_capA.setCursor(Qt.PointingHandCursor)
        self.btn_capA.setToolTip("把当前方向捕获为视角 A, 供「GIF 设置」使用")
        self.btn_capA.clicked.connect(lambda: self.capture_camera("A"))
        bar.addWidget(self.btn_capA)

        self.btn_reset = QPushButton("重置视角")
        self.btn_reset.setObjectName("Ghost")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(lambda: self.run("window.resetView();"))
        bar.addWidget(self.btn_reset)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def load_structure(self, atoms, elem_map, style="ballstick",
                       show_cell=True, cell_color="#888888", bg="white",
                       zoom=1.0, view="front", rot=None,
                       pan=(0.0, 0.0)):
        js = core.ensure_3dmol_js()
        frames = [atoms]
        xyz = core.frames_to_xyz(frames)
        edges = core.cell_edges(atoms)
        style_d = core.style_for(style)
        try:
            fit = core.fit_sphere(frames, include_cell=bool(show_cell))
        except Exception:
            fit = None
        # 预设视角: 根据晶胞矢量算出四元数 (正视图 = a-c 面平行屏幕)
        orient = None
        if rot is None and view in core.VIEW_NAMES:
            try:
                orient = core.view_quaternion(atoms.get_cell(), view)
                rot_list = []
            except Exception:
                orient = None
                rot_list = core.parse_rotation(core.VIEW_PRESETS.get(view, ""))
        elif rot is None:
            rot_list = core.parse_rotation(core.VIEW_PRESETS.get(view, ""))
        else:
            rot_list = core.parse_rotation(rot)
        html = core.build_html(js, xyz, edges, 10, 10, bg, style_d, elem_map,
                               show_cell, cell_color, zoom, rot_list,
                               interactive=True, fill=True, orient=orient,
                               fit=fit, pan=list(pan))
        self._pan = list(pan)
        self._orient = list(orient) if orient else None
        path = self._tmpdir / "viewer.html"
        path.write_text(html, encoding="utf-8")
        self._ready = False
        self.web.load(QUrl.fromLocalFile(str(path.resolve())))

    # ------------------------------------------------------------------
    def _on_load(self, ok):
        if not ok:
            return
        self._poll_count = 0
        QTimer.singleShot(150, self._poll_ready)

    def _poll_ready(self):
        if self._ready:
            return
        self._poll_count += 1

        def cb(val):
            if val:
                self._ready = True
                self.ready.emit()
                for js in self._pending:
                    self.web.page().runJavaScript(js)
                self._pending.clear()
            elif self._poll_count < 100:
                QTimer.singleShot(150, self._poll_ready)

        self.web.page().runJavaScript("window.ready === true", cb)

    # ------------------------------------------------------------------
    def run(self, js, callback=None):
        if self._ready:
            if callback is not None:
                self.web.page().runJavaScript(js, callback)
            else:
                self.web.page().runJavaScript(js)
        else:
            self._pending.append(js)

    def capture_camera(self, slot: str):
        """捕获当前相机视角, 发出 cameraCaptured 信号。"""
        js = ("JSON.stringify({view: viewer.getView(), "
              "w: document.getElementById('v').clientWidth, "
              "h: document.getElementById('v').clientHeight})")

        def cb(s):
            if not s:
                print(f"[viewer] capture {slot}: empty result")
                return
            try:
                d = json.loads(s)
                view = [float(x) for x in d["view"]]
                self.cameraCaptured.emit(slot, view, int(d.get("w", 0)))
            except Exception as exc:  # noqa: BLE001
                print(f"[viewer] capture {slot} failed: {exc}; raw={s[:120]!r}")

        self.run(js, cb)

    def apply_view(self, view):
        self.run(f"window.setView({json.dumps(list(view))});")

    def apply_orientation(self, quat):
        """只改朝向 (保留缩放/中心), 用于预设视角。"""
        if quat:
            self._orient = list(quat)
        self.run(f"window.setOrientation({json.dumps(list(quat))});")

    def apply_pan(self, fx, fy):
        """屏幕平移 (比例): 正值 = 结构向右 / 向上。"""
        self._pan = [float(fx), float(fy)]
        self.run(f"window.setPan({float(fx)}, {float(fy)});")

    def set_background(self, color):
        self.run(f"window.setBackground('{color}');")

    # ------------------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        try:
            self.web.stop()
            self.web.setParent(None)
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        super().closeEvent(event)

    def shutdown(self):
        try:
            self.web.stop()
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)
