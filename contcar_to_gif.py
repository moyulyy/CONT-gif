#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用 3Dmol.js 把单个 CONTCAR 结构渲染成旋转 GIF 动图 (渲染核心)。

流程:
    ASE 读取 CONTCAR -> 复制成 N 帧 (结构不变, 相机绕指定晶胞轴旋转)
    -> 逐帧转成 XYZ -> 3Dmol.js(浏览器 WebGL) 渲染
    -> Playwright 截取 PNG -> Pillow 合成 GIF

本模块只读取 CONTCAR 一个结构文件, 不读取 XDATCAR / OUTCAR / OSZICAR,
也不做任何曲线分析 —— 只负责「结构 -> 旋转 GIF」的生成逻辑。

运行环境: D:\\miniconda3\\envs\\chem_env

命令行示例:
    python contcar_to_gif.py                          # CONTCAR -> CONTCAR.gif
    python contcar_to_gif.py --view front --rot-axis c --rot-angle 360
    python contcar_to_gif.py --style sphere -w 800 --frames 90 --fps 20

视角 --view: front / back / top / bottom / right / left (基于晶胞矢量),
    也可用 --rot '20x,-20y,0z' 自定义 (会覆盖 --view)。

原子颜色/半径使用内置 VESTA 经典配色, 不需要任何外部 .vesta 文件。

说明:
    * 需要本机已安装 Edge 或 Chrome (Playwright 用 channel 调用), 或已
      `python -m playwright install chromium`。
    * 3Dmol.js 会优先使用同目录的 3dmol/3Dmol-min.js。
"""

import argparse
import base64
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
from ase.io import read, write
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    """PyInstaller 打包后资源所在目录 (sys._MEIPASS), 否则为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return HERE


BUNDLE_DIR = _bundle_dir()
# Windows 控制台默认 GBK, 避免特殊字符导致 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

THREEDMOL_CANDIDATES = [HERE / "3dmol" / "3Dmol-min.js",
                        BUNDLE_DIR / "3dmol" / "3Dmol-min.js"]
THREEDMOL_JS = THREEDMOL_CANDIDATES[0]
THREEDMOL_CDN = "https://3dmol.org/build/3Dmol-min.js"


# --------------------------------------------------------------------------
# 读取轨迹
# --------------------------------------------------------------------------
def parse_index(text):
    """':' -> 全部；'-1' -> 单帧；'0:100:2' -> 切片。"""
    text = text.strip()
    if text.lower() in ("all", ":", "::"):
        return ":"
    if ":" in text:
        return text
    return int(text)


def load_contcar(path):
    """读取单个 CONTCAR 结构 (CONTCAR 就是 POSCAR 格式)。"""
    if not os.path.isfile(path):
        raise SystemExit(f"[错误] 找不到 CONTCAR: {path}")
    atoms = read(path, format="vasp")
    print(f"[信息] {path}: {len(atoms)} 个原子, "
          f"元素 {sorted(set(atoms.get_chemical_symbols()))}")
    return atoms


def build_frames(atoms, n_frames, repeat=None):
    """把单个结构复制成 n 帧。结构本身不变, 动画由相机绕轴旋转产生。"""
    a = atoms
    if repeat and tuple(repeat) != (1, 1, 1):
        a = atoms.repeat(repeat)
    n = max(1, int(n_frames))
    print(f"[信息] 准备渲染 {n} 帧 ({len(a)} 个原子)")
    return [a for _ in range(n)]


def frames_to_xyz(images):
    """每帧转成一段 XYZ 文本。"""
    out = []
    for atoms in images:
        buf = io.StringIO()
        write(buf, atoms, format="xyz")
        out.append(buf.getvalue())
    return out


def _cell_corners(cell):
    """晶胞 8 个顶点 (笛卡尔)。"""
    c = np.asarray(cell, dtype=float).reshape(3, 3)
    return [i * c[0] + j * c[1] + k * c[2]
            for i in (0, 1) for j in (0, 1) for k in (0, 1)]


def fit_sphere(images, include_cell=True):
    """计算取景包围球: 返回 [cx, cy, cz, radius]。

    include_cell=True (默认): 连同**晶胞盒**一起装下 —— 适合表面/体相结构,
        保证“一边空着、格子却被切掉”不会发生。
    include_cell=False: 只按**原子**取景 —— 适合分子/团簇 (晶胞盒很大、
        含大量真空层时, 若把空盒子也算进去, 分子会小得几乎看不见)。
    """
    pts = [np.asarray(a.get_positions(), dtype=float) for a in images]
    stuff = [p for p in pts if len(p)]
    if include_cell:
        corners = np.array(
            [p for a in images for p in _cell_corners(a.get_cell())],
            dtype=float)
        stuff.append(corners)
    allp = np.vstack(stuff)
    lo, hi = allp.min(0), allp.max(0)
    center = (lo + hi) / 2.0
    radius = float(np.linalg.norm(allp - center, axis=1).max())
    return [float(center[0]), float(center[1]), float(center[2]),
            max(radius, 1.0)]


def cell_edges(atoms):
    """返回晶胞 12 条棱 [(x1,y1,z1,x2,y2,z2), ...]。"""
    cell = atoms.get_cell()[:]  # 3x3, 行向量 a,b,c
    corners = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                corners[(i, j, k)] = i * cell[0] + j * cell[1] + k * cell[2]
    edges = []
    for (i, j, k), p in corners.items():
        for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            q = (i + d[0], j + d[1], k + d[2])
            if q in corners:
                r = corners[q]
                edges.append([float(p[0]), float(p[1]), float(p[2]),
                              float(r[0]), float(r[1]), float(r[2])])
    return edges


# --------------------------------------------------------------------------
# VESTA 原子着色 / 半径
# --------------------------------------------------------------------------
# VESTA 默认元素颜色 (RGB 0-255), 找不到 .vesta 文件时使用
VESTA_COLORS = {
    "H":  (255, 204, 204),
    "C":  (128,  73,  41),
    "N":  ( 48,  80, 248),
    "O":  (254,   3,   0),
    "Na": (171,  92, 242),
    "Mg": (138, 255,   0),
    "Al": (191, 166, 166),
    "Si": (240, 200, 160),
    "P":  (255, 128,   0),
    "S":  (255, 255,   0),
    "Cl": ( 31, 240,  31),
    "K":  (143,  64, 212),
    "Ca": ( 61, 255,   0),
    "Ti": (191, 194, 199),
    "Fe": (224, 102,  51),
    "Co": (  0,   0, 175),
    "Ni": (183, 187, 189),
    "Cu": (200, 128,  32),
    "Zn": (125, 128, 176),
}
VESTA_RADII = {
    "H": 0.46, "C": 0.77, "N": 0.75, "O": 0.74, "Na": 1.02,
    "Mg": 0.72, "Al": 0.54, "Si": 1.17, "P": 1.10, "S": 1.04,
    "Cl": 0.99, "K": 1.38, "Ca": 1.00, "Ti": 0.86, "Fe": 0.83,
    "Co": 1.25, "Ni": 1.25, "Cu": 1.17, "Zn": 1.25,
}


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


def make_element_map(images, vesta_colors, vesta_radii, radius_scale,
                     uniform_radius=None, radius_overrides=None):
    """返回 {元素: {'color': '#rrggbb' 或 None, 'radius': r}}。

    radius_overrides: {元素: 绝对半径(Å)} 优先于 vesta_radii*scale。
    """
    elements = sorted({s for s in images[0].get_chemical_symbols()})
    overrides = radius_overrides or {}
    out = {}
    for el in elements:
        color = vesta_colors.get(el)
        if uniform_radius is not None:
            radius = uniform_radius
        elif el in overrides:
            radius = float(overrides[el])
        else:
            radius = vesta_radii.get(el, 0.5) * radius_scale
        out[el] = {"color": color, "radius": round(radius, 3)}
    return out


def resolve_vesta(args):
    """把**内置** VESTA 经典配色/半径写入 args._vesta_colors / args._vesta_radii。

    本项目只读取 CONTCAR, 不读取任何外部 .vesta 文件, 始终使用内置配色。
    保留该函数是为了让 GUI / 命令行共用同一套参数结构。
    """
    if not getattr(args, "use_vesta", True):
        args._vesta_colors = {}
        args._vesta_radii = {}
        return None
    args._vesta_colors = {el: rgb_to_hex(rgb)
                          for el, rgb in VESTA_COLORS.items()}
    args._vesta_radii = dict(VESTA_RADII)
    return None


# --------------------------------------------------------------------------
# 3Dmol.js
# --------------------------------------------------------------------------
def ensure_3dmol_js():
    for cand in THREEDMOL_CANDIDATES:
        if cand.exists():
            return cand.read_text(encoding="utf-8", errors="ignore")
    THREEDMOL_JS.parent.mkdir(parents=True, exist_ok=True)
    print(f"[信息] 下载 3Dmol.js -> {THREEDMOL_JS}")
    try:
        urllib.request.urlretrieve(THREEDMOL_CDN, THREEDMOL_JS)
    except Exception as exc:
        raise SystemExit(
            f"[错误] 无法下载 3Dmol.js ({exc})。\n"
            f"       请手动下载 {THREEDMOL_CDN} 放到 {THREEDMOL_JS}"
        )
    return THREEDMOL_JS.read_text(encoding="utf-8", errors="ignore")


VIEW_PRESETS = {
    "default": "20x,-20y,0z",     # 略带俯角的立体视图 (仅 CLI 兼容用)
    "top":     "0x,0y,0z",         # 俯视 (沿 -z 看, 显示 x-y 面)
    "bottom":  "180x,0y,0z",       # 仰视
    "front":   "90x,0y,0z",        # 正视图 (显示 x-z 面)
    "back":    "-90x,0y,0z",       # 后视图
    "right":   "0x,-90y,-90x,0z",  # 右视图 (看 y-z 面, 且 c 轴竖直向上)
    "left":    "0x,90y,90x,0z",    # 左视图
}

# --------------------------------------------------------------------------
# 基于晶胞矢量的六个标准视角
# --------------------------------------------------------------------------
# 3Dmol 的 getView() 返回 [cx,cy,cz,zoom,qx,qy,qz,qw], 其中四元数旋转模型;
# 屏幕坐标 = R(q) · (原子坐标 - center), 因此屏幕 x = ex·v, 屏幕 y = ey·v。
# 所以只要令四元数对应的旋转矩阵的「三行」为 ex/ey/ez 即可。
#
# 约定 (晶体学惯例, a/b/c 为晶胞矢量):
#   正视图  a → 屏幕 +x,  c → 屏幕 +y   (a-c 面平行屏幕, 看 -b 方向)
#   后视图  a → 屏幕 -x,  c → 屏幕 +y
#   俯视图  a → 屏幕 +x,  b → 屏幕 +y   (a-b 面平行屏幕, 看 -c 方向)
#   仰视图  a → 屏幕 -x,  b → 屏幕 +y
#   右视图  b → 屏幕 +x,  c → 屏幕 +y   (b-c 面平行屏幕)
#   左视图  b → 屏幕 -x,  c → 屏幕 +y
VIEW_NAMES = ["front", "back", "top", "bottom", "right", "left"]


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return np.array([1.0, 0.0, 0.0])
    return v / n


def _quat_from_matrix(m):
    """3x3 正交旋转矩阵 -> 四元数 (x, y, z, w), 与 three.js/3Dmol 约定一致。"""
    m = np.asarray(m, dtype=float)
    t = float(m[0, 0] + m[1, 1] + m[2, 2])
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], dtype=float)
    nrm = float(np.linalg.norm(q))
    if nrm < 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    q /= nrm
    # 规范符号 (w >= 0), 保证插值/比较稳定
    if q[3] < 0:
        q = -q
    return [float(v) for v in q]


def view_quaternion(cell, name: str):
    """根据晶胞矢量计算预设视角的四元数。

    cell: 3x3 矩阵, 行向量为晶胞矢量 a, b, c (与 ASE get_cell() 一致)。
    name: front / back / top / bottom / right / left
    返回 [qx, qy, qz, qw]。
    """
    cell = np.asarray(cell, dtype=float).reshape(3, 3)
    a, b, c = cell[0], cell[1], cell[2]
    if name in ("front", "back"):
        ex, ey = _unit(a), c
    elif name in ("top", "bottom"):
        ex, ey = _unit(a), b
    else:                                   # right / left
        ex, ey = _unit(b), c
    if name in ("back", "bottom", "left"):
        ex = -ex
    # 把 ey 正交化到 ex 上 (处理三斜晶胞), 再补出 ez 构成右手系
    ey = np.asarray(ey, dtype=float) - float(np.dot(ey, ex)) * ex
    ey = _unit(ey)
    ez = _unit(np.cross(ex, ey))
    return _quat_from_matrix(np.array([ex, ey, ez]))


def safe_view_quaternion(cell, name):
    """容错版: 失败时返回 None。"""
    try:
        return view_quaternion(cell, name)
    except Exception:
        return None


def parse_rotation(text):
    """'20x,-20y,0z' -> [[20.0, 'x'], [-20.0, 'y'], [0.0, 'z']]。"""
    out = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)([xyz])", part)
        if not m:
            raise SystemExit(f"[错误] 无法解析 --rot 中的 '{part}' (示例: 20x,-20y,0z)")
        out.append([float(m.group(1)), m.group(2)])
    return out


# --------------------------------------------------------------------------
# 键长 / 键角 / 二面角测量
# --------------------------------------------------------------------------
# 测量项统一用 dict 描述: {"kind": "distance"/"angle"/"dihedral",
#                          "atoms": [i, j, ...], "color": "#rrggbb"}
# 原子下标为 0 基, 与 3Dmol/XYZ 中的顺序一致。
MEASURE_ATOMS = {"distance": 2, "angle": 3, "dihedral": 4}
MEASURE_KIND_BY_COUNT = {2: "distance", 3: "angle", 4: "dihedral"}
# 每组测量一个颜色, 按新增顺序循环取用
MEASURE_PALETTE = [
    "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4",
    "#008080", "#9A6324", "#F032E6", "#000075", "#808000",
    "#46F0F0", "#BCF60C", "#C61A09", "#469990", "#800000",
]


def measure_color(index, palette=None):
    pal = palette or MEASURE_PALETTE
    return pal[int(index) % len(pal)]


def parse_measures(specs, palette=None):
    """把 CLI 的 --measure 字符串解析成测量列表。

    支持 ``"0,1"`` / ``"0,1,2"`` / ``"dihedral:0,1,2,3"`` 等形式,
    未写类型时按原子个数推断 (2=键长, 3=键角, 4=二面角)。
    """
    out = []
    for k, spec in enumerate(specs or []):
        parts = [p for p in re.split(r"[,;:\-\s]+", str(spec).strip()) if p]
        kind = None
        if parts and parts[0].lower() in MEASURE_ATOMS:
            kind = parts.pop(0).lower()
        try:
            idx = [int(p) for p in parts]
        except ValueError:
            raise SystemExit(f"[错误] 无法解析 --measure '{spec}' (示例: 0,1,2)")
        if kind is None:
            kind = MEASURE_KIND_BY_COUNT.get(len(idx))
        need = MEASURE_ATOMS.get(kind or "")
        if need is None or len(idx) < need:
            raise SystemExit(
                f"[错误] --measure '{spec}' 原子数不对: {kind or '未知'}需要 {need} 个原子")
        out.append({"kind": kind, "atoms": idx[:need],
                    "color": measure_color(k, palette)})
    return out or None


def measure_value(atoms, measure):
    """按测量类型返回数值 (键长 Å / 键角 ° / 二面角 °)。"""
    kind = measure.get("kind")
    idx = [int(i) for i in measure.get("atoms", [])]
    need = MEASURE_ATOMS.get(kind)
    if need is None or len(idx) < need:
        raise ValueError(f"测量项不完整: {measure}")
    if kind == "distance":
        return float(atoms.get_distance(idx[0], idx[1]))
    if kind == "angle":
        return float(atoms.get_angle(idx[0], idx[1], idx[2]))
    return float(atoms.get_dihedral(idx[0], idx[1], idx[2], idx[3]))


def atom_tag(atoms, i):
    """原子标签: 元素符号 + 1 基序号, 如 O1 / H12。"""
    try:
        sym = atoms.get_chemical_symbols()[int(i)]
    except Exception:
        sym = "?"
    return f"{sym}{int(i) + 1}"


def measure_text(atoms, measure):
    """生成一行测量文本, 如 'd(O1–H2) = 0.980 Å'。"""
    kind = measure.get("kind")
    idx = [int(i) for i in measure.get("atoms", [])]
    tags = "–".join(atom_tag(atoms, i) for i in idx)
    val = measure_value(atoms, measure)
    if kind == "distance":
        return f"d({tags}) = {val:.3f} Å"
    if kind == "angle":
        return f"∠({tags}) = {val:.2f}°"
    return f"φ({tags}) = {val:.2f}°"


def measure_labels(atoms, measures):
    """把测量列表转成 (文本, 颜色) 列表, 供 GIF 标注使用。"""
    out = []
    for m in measures or []:
        try:
            out.append((measure_text(atoms, m),
                        str(m.get("color") or "#FF3B30")))
        except Exception as exc:  # noqa: BLE001
            out.append((f"测量失败: {exc}", "#FF3B30"))
    return out


_MEASURE_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def load_measure_font(size):
    """尽量找一个能显示中文/数学符号的字体, 失败退回默认字体。"""
    for cand in _MEASURE_FONT_CANDIDATES:
        try:
            if os.path.isfile(cand):
                return ImageFont.truetype(cand, int(size))
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _is_dark_color(color):
    color = str(color or "").strip()
    if color.lower() in ("black", "#000", "#000000", "#1b1b1d", "#141416"):
        return True
    if color.startswith("#") and len(color) == 7:
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            return (0.299 * r + 0.587 * g + 0.114 * b) < 110
        except Exception:
            return False
    return False


def _measure_rgb(color):
    try:
        return tuple(int(str(color).lstrip("#")[i:i + 2], 16)
                     for i in (0, 2, 4))
    except Exception:
        return (255, 59, 48)


def element_legend(images, args):
    """返回结构中出现元素的 [(符号, 颜色), ...] (按首次出现顺序)。"""
    try:
        elem_map = make_element_map(
            images, args._vesta_colors, args._vesta_radii,
            getattr(args, "radius_scale", 0.6), getattr(args, "radius", None),
            getattr(args, "radius_overrides", None))
    except Exception:
        return []
    order = []
    try:
        syms = images[0].get_chemical_symbols()
    except Exception:
        return []
    for sym in syms:
        if sym not in order:
            order.append(sym)
    out = []
    for el in order:
        info = elem_map.get(el)
        if info:
            out.append((el, info.get("color") or "#B8B8B8"))
    return out


def _info_strip(width, lines=None, bg="white", title=None, legend=None):
    """生成底部信息条 (RGBA, 宽度固定为 width)。

    legend: [(元素符号, 颜色), ...]  用实心小球 + 符号展示
    lines:  [(文本, 颜色), ...]      用彩色圆点 + 文本展示
    两者尽量排成一行, 放不下时自动换行; 字号按宽度自动收缩。
    """
    legend = [(str(s), str(c)) for s, c in (legend or [])]
    lines = [(str(t), str(c)) for t, c in (lines or [])]
    flows = []
    if legend:
        flows.append([{"text": s, "color": c, "style": "ball"}
                      for s, c in legend])
    if title:
        flows.append([{"text": str(title), "color": "#007AFF",
                       "style": "text"}])
    if lines:
        flows.append([{"text": t, "color": c, "style": "dot"}
                      for t, c in lines])
    if not flows:
        return None

    margin = max(8, int(round(width * 0.012)))
    pad_y = max(5, int(round(width * 0.007)))
    item_gap = max(10, int(round(width * 0.016)))
    maxw = max(40, width - 2 * margin)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size0 = max(11, int(round(24.0 * max(1.0, width / 1400.0))))

    def layout(size):
        font = load_measure_font(size)
        dot = max(6, int(round(size * 0.62)))
        ball = max(9, int(round(size * 1.15)))
        flows_cells = []
        ok = True
        for flow in flows:
            cells = []
            for c in flow:
                b = probe.textbbox((0, 0), c["text"], font=font)
                tw = b[2] - b[0]
                lead = ball if c["style"] == "ball" else \
                    (dot if c["style"] == "dot" else 0)
                cw = lead + (6 if lead else 0) + tw
                if cw > maxw:
                    ok = False
                cells.append((c, tw, lead, cw))
            flows_cells.append(cells)
        return ok, (size, font, dot, ball, flows_cells)

    chosen = None
    for size in range(size0, 7, -1):
        ok, cand = layout(size)
        if ok:
            chosen = cand
            break
    if chosen is None:
        chosen = layout(max(8, size0 // 2))[1]

    size, font, dot, ball, flows_cells = chosen
    row_h = max(size + 4, ball) + 2 * pad_y

    def pack(cells):
        rows, cur, cur_w = [], [], 0
        for cell in cells:
            cw = cell[3]
            add = cw if not cur else cw + item_gap
            if cur and cur_w + add > maxw:
                rows.append(cur)
                cur, cur_w, add = [], 0, cw
            cur.append(cell)
            cur_w += add
        if cur:
            rows.append(cur)
        return rows

    all_rows = []
    for flow_cells in flows_cells:
        all_rows.extend(pack(flow_cells))
    if not all_rows:
        return None

    row_gap = max(4, int(round(width * 0.006)))
    strip_h = len(all_rows) * row_h + (len(all_rows) - 1) * row_gap + 2 * pad_y

    dark = _is_dark_color(bg)
    panel = (0, 0, 0, 150) if dark else (255, 255, 255, 190)
    edge = (255, 255, 255, 60) if dark else (0, 0, 0, 28)
    ball_edge = (255, 255, 255, 165) if dark else (0, 0, 0, 70)
    text_color = (245, 245, 247, 255) if dark else (28, 28, 30, 255)

    strip = Image.new("RGBA", (width, strip_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    d.rounded_rectangle([0, 0, width - 1, strip_h - 1],
                        radius=max(6, int(round(width * 0.008))),
                        fill=panel, outline=edge, width=1)

    y = pad_y
    for row in all_rows:
        row_w = sum(cell[3] for cell in row) + item_gap * max(0, len(row) - 1)
        x = max(margin, int((width - row_w) / 2.0))
        for cell in row:
            c, tw, lead, cw = cell
            rgb = _measure_rgb(c["color"])
            cy = y + row_h / 2.0
            tx = x
            if c["style"] == "ball":
                d.ellipse([tx, cy - ball / 2.0, tx + ball, cy + ball / 2.0],
                          fill=rgb + (255,), outline=ball_edge, width=1)
                tx += ball + 6
            elif c["style"] == "dot":
                d.ellipse([tx, cy - dot / 2.0, tx + dot, cy + dot / 2.0],
                          fill=rgb + (255,))
                tx += dot + 6
            b = d.textbbox((0, 0), c["text"], font=font)
            d.text((tx, cy - (b[3] + b[1]) / 2.0), c["text"], font=font,
                   fill=text_color if c["style"] in ("text", "ball")
                   else (rgb + (255,)))
            x += cw + item_gap
        y += row_h + row_gap
    return strip


def draw_measure_bottom(im, lines, bg="white", title=None, legend=None):
    """把元素图例 / 测量文本横铺到图像最下方 (不改变上方内容)。"""
    if not lines and not legend:
        return im
    base = im.convert("RGBA")
    strip = _info_strip(base.width, lines=lines, bg=bg, title=title,
                        legend=legend)
    if strip is None:
        return im
    out = Image.new("RGBA", (base.width, base.height + strip.height),
                    (0, 0, 0, 0))
    out.paste(base, (0, 0))
    out.paste(strip, (0, base.height))
    return out.convert("RGB")


def draw_element_legend(im, legend, bg="white"):
    """只在图像底部加一条「元素 -> 颜色」图例。"""
    return draw_measure_bottom(im, None, bg=bg, legend=legend)


def _panel_captions(size, regions, bg):
    """在 RGBA 图层上画半透明小标题 (regions: [(x0, text), ...])。"""
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    if not regions:
        return overlay
    d = ImageDraw.Draw(overlay)
    dark = _is_dark_color(bg)
    panel = (0, 0, 0, 145) if dark else (255, 255, 255, 185)
    text_color = (245, 245, 247, 255) if dark else (28, 28, 30, 255)
    fs = max(11, int(round(size[0] * 0.013)))
    font = load_measure_font(fs)
    pad_x = max(6, int(round(fs * 0.7)))
    pad_y = max(3, int(round(fs * 0.35)))
    margin = max(6, int(round(size[0] * 0.009)))
    for x0, text in regions:
        if not text:
            continue
        b = d.textbbox((0, 0), text, font=font)
        tw = b[2] - b[0]
        th = b[3] - b[1]
        rx0 = x0 + margin
        ry0 = margin
        d.rounded_rectangle([rx0, ry0, rx0 + tw + 2 * pad_x,
                             ry0 + th + 2 * pad_y], radius=6, fill=panel)
        d.text((rx0 + pad_x, ry0 + pad_y - b[1]), text, font=font,
               fill=text_color)
    return overlay


def compose_measure_pair(left, right, lines, bg="white", gap=None,
                         captions=("原始结构", "测量标记"), legend=None):
    """左上: 原始结构 (无标记); 右上: 带网格球结构; 下方: 元素图例 + 测量文本。"""
    left = left.convert("RGB")
    right = right.convert("RGB")
    if gap is None:
        gap = max(8, int(round(left.width * 0.016)))
    tw = left.width + gap + right.width
    th = max(left.height, right.height)
    top = Image.new("RGB", (tw, th), bg)
    top.paste(left, (0, 0))
    top.paste(right, (left.width + gap, 0))

    # 分隔线 (实色, 避免在 RGB 图上用 alpha)
    dark = _is_dark_color(bg)
    sep = (78, 78, 82) if dark else (214, 214, 219)
    d = ImageDraw.Draw(top)
    midx = left.width + gap // 2
    d.line([(midx, 0), (midx, th)], fill=sep, width=1)

    # 两个面板的标题
    if captions:
        regions = [(0, captions[0]), (left.width + gap, captions[1])]
        top = Image.alpha_composite(
            top.convert("RGBA"), _panel_captions(top.size, regions, bg)
        ).convert("RGB")

    return draw_measure_bottom(top, lines, bg=bg, legend=legend)


def style_for(name):
    """基础样式 (不含颜色/半径, 半径由 elem_map 逐元素覆盖)。"""
    if name == "sphere":
        return {"sphere": {}}
    if name == "stick":
        return {"stick": {}}
    if name == "line":
        return {"line": {"linewidth": 2}}
    # 默认 ball-and-stick
    return {"stick": {}, "sphere": {}}


def build_html(js, xyz_frames, edges, width, height, bg, style, elem_map,
               show_cell, cell_color, zoom, rot, views=None, spin=0.0,
               interactive=False, fill=False, orient=None, fit=None, pan=None,
               measures=None):
    views_json = json.dumps(views) if views else "null"
    orient_json = json.dumps(list(orient)) if orient is not None else "null"
    fit_json = json.dumps([float(v) for v in fit]) if fit else "null"
    measures_json = json.dumps(measures) if measures else "null"
    pan_json = json.dumps([float(v) for v in (pan or (0.0, 0.0))])
    # 关键: fill 模式下必须让 html/body 也有 100% 高度, 否则 div 的
    # height:100% 会退化成 auto → 高度 0 → WebGL 画布不可见。
    if fill:
        head_style = (f"html,body{{width:100%;height:100%;margin:0;padding:0;"
                      f"overflow:hidden;background:{bg};}}")
        div_style = "position:absolute;left:0;top:0;width:100%;height:100%;"
    else:
        head_style = f"body{{margin:0;padding:0;overflow:hidden;background:{bg};}}"
        div_style = f"width:{width}px;height:{height}px;position:relative;"
    interact_js = ""
    if interactive:
        interact_js = """
// 正交投影下用「相机距离」控制缩放 (3Dmol 的 show() 会按距离重算正交视锥),
// 这里在捕获阶段拦下滚轮, 直接调 ORTHO_ZOOM, 避免和内置透视缩放混在一起。
(function() {
  var el = document.getElementById('v');
  el.addEventListener('wheel', function(e) {
    e.preventDefault();
    e.stopPropagation();
    var d = e.deltaY;
    if (!d) return;
    ORTHO_ZOOM = Math.max(0.05, Math.min(50.0,
                          ORTHO_ZOOM * (d < 0 ? 1.1 : 1.0 / 1.1)));
    refreshView();
  }, {passive: false, capture: true});
})();
window.setViewArray = function(v) { viewer.setView(v); viewer.render(); };
window.getViewArray = function() { return viewer.getView(); };
// 只改朝向 (保留平移/缩放)
window.setOrientation = function(q) { applyCamera(q); };
// 屏幕平移 (比例): 正值 = 结构向右 / 向上
window.setPan = function(fx, fy) { PAN = [fx, fy]; refreshView(); };
window.resetView = function() { PAN = [PAN0[0], PAN0[1]]; applyCamera(INIT_Q); };
window.setBackground = function(c){ viewer.setBackgroundColor(c, 1.0); viewer.render(); };
// 原子点选: 开启后点击原子会写入 window._lastPick / _pickSeq, 供宿主轮询
window._pickCb = null;
window._pickSeq = 0;
window._lastPick = -1;
window._pickQueue = [];
window.enablePicking = function(on, cb) {
  window._pickCb = cb || null;
  if (!on) window._pickQueue = [];
  viewer.setClickable({}, !!on, function(atom) {
    if (atom && atom.index !== undefined) {
      window._lastPick = atom.index;
      window._pickSeq += 1;
      window._pickQueue.push(atom.index);
      if (window._pickCb) window._pickCb(atom.index);
    }
  });
  viewer.render();
};
"""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>3Dmol frame</title>
<style>{head_style}</style>
<script>{js}</script></head>
<body>
<div id="v" style="{div_style}"></div>
<script>
const FRAMES = {json.dumps(xyz_frames)};
const EDGES  = {json.dumps(edges)};
const STYLE  = {json.dumps(style)};
const ELEM   = {json.dumps(elem_map)};
const ROT    = {json.dumps(rot)};
const VIEWS  = {views_json};
const ORIENT = {orient_json};
const FIT    = {fit_json};
const PAN0   = {pan_json};
const SPIN   = {float(spin)};
const ZOOM   = {float(zoom)};
const SHOW_CELL = {str(bool(show_cell)).lower()};
const MEASURES = {measures_json};

var viewer = $3Dmol.createViewer(document.getElementById('v'),
                                 {{backgroundColor: '{bg}'}});

function addCell() {{
  if (!SHOW_CELL) return;
  for (const e of EDGES) {{
    viewer.addLine({{start:{{x:e[0],y:e[1],z:e[2]}},
                     end:  {{x:e[3],y:e[4],z:e[5]}},
                     color: '{cell_color}', dashed: true, linewidth: 1}});
  }}
}}

// 先给全部原子一个基础样式, 再按元素覆盖颜色/半径 (VESTA 配色)
function applyStyle() {{
  viewer.setStyle({{}}, STYLE);
  for (const el in ELEM) {{
    const info = ELEM[el];
    const st = {{}};
    if (STYLE.sphere) {{
      const s = {{radius: info.radius}};
      if (info.color) s.color = info.color;
      st.sphere = s;
    }}
    if (STYLE.stick) {{
      const s = {{radius: info.radius * 0.35}};
      if (info.color) s.color = info.color;
      st.stick = s;
    }}
    if (STYLE.line) {{
      const s = {{linewidth: 2}};
      if (info.color) s.color = info.color;
      st.line = s;
    }}
    viewer.setStyle({{elem: el}}, st);
  }}
}}

viewer.addModel(FRAMES[0], 'xyz');
applyStyle();
addCell();

// ==================================================================
// 键长 / 键角 / 二面角测量标注
// ==================================================================
// 每个测量组用不同颜色的“网格球”(wireframe 球) 标记参与原子, 并用
// 圆柱画出相应的键和弧线。形状 (shape) 独立于 model, 因此切换帧时
// removeAllModels() 不会把它们清掉。
var MEASURE_SHAPES = [];
var PENDING_SHAPES = [];

function _atomList() {{
  return viewer.getAtomsFromSel({{}});
}}
function _atomPos(i) {{
  var a = _atomList()[i];
  if (!a) return null;
  return {{x: a.x, y: a.y, z: a.z}};
}}
function _atomRadius(i, factor) {{
  var a = _atomList()[i];
  var r = 0.4;
  if (a && typeof ELEM !== 'undefined' && ELEM[a.elem]
      && ELEM[a.elem].radius) r = ELEM[a.elem].radius;
  return r * (factor || 1.2);
}}
function _pushShape(arr, s) {{ if (s) arr.push(s); return s; }}
function _cylTo(arr, p, q, color, radius) {{
  return _pushShape(arr, viewer.addCylinder({{start: p, end: q,
      radius: (radius || 0.05), color: color, fromCap: 2, toCap: 2}}));
}}
// 网格球: 一个半透明实心球 + 一个 wireframe 球
function _meshTo(arr, c, r, color) {{
  _pushShape(arr, viewer.addSphere({{center: c, radius: r, color: color,
                                     opacity: 0.16}}));
  _pushShape(arr, viewer.addSphere({{center: c, radius: r, color: color,
                                     wireframe: true, linewidth: 2}}));
}}
function _clearShapes(arr) {{
  for (var i = 0; i < arr.length; i++) {{
    try {{ viewer.removeShape(arr[i]); }} catch (e) {{}}
  }}
  arr.length = 0;
}}
function _angleArc(a, b, c, color) {{
  var ab = {{x: a.x - b.x, y: a.y - b.y, z: a.z - b.z}};
  var cb = {{x: c.x - b.x, y: c.y - b.y, z: c.z - b.z}};
  var nab = Math.hypot(ab.x, ab.y, ab.z), ncb = Math.hypot(cb.x, cb.y, cb.z);
  if (nab < 1e-6 || ncb < 1e-6) return;
  var u = {{x: ab.x / nab, y: ab.y / nab, z: ab.z / nab}};
  var w = {{x: cb.x / ncb, y: cb.y / ncb, z: cb.z / ncb}};
  var dot = Math.max(-1, Math.min(1, u.x * w.x + u.y * w.y + u.z * w.z));
  var ang = Math.acos(dot);
  var v = {{x: w.x - dot * u.x, y: w.y - dot * u.y, z: w.z - dot * u.z}};
  var nv = Math.hypot(v.x, v.y, v.z);
  if (nv < 1e-6) return;
  v = {{x: v.x / nv, y: v.y / nv, z: v.z / nv}};
  var r = Math.min(0.5, 0.32 * Math.min(nab, ncb));
  var pts = [], steps = 24;
  for (var i = 0; i <= steps; i++) {{
    var t = ang * i / steps, ct = Math.cos(t), st = Math.sin(t);
    pts.push({{x: b.x + r * (u.x * ct + v.x * st),
               y: b.y + r * (u.y * ct + v.y * st),
               z: b.z + r * (u.z * ct + v.z * st)}});
  }}
  _pushShape(MEASURE_SHAPES, viewer.addCurve({{points: pts, radius: 0.035,
      color: color, smooth: 0, fromCap: 2, toCap: 2}}));
}}
function _drawMeasure(m) {{
  if (!m || !m.atoms || !m.atoms.length) return;
  var color = m.color || '#FF3B30';
  var pts = [];
  for (var i = 0; i < m.atoms.length; i++) {{
    var p = _atomPos(m.atoms[i]);
    if (!p) return;
    pts.push(p);
  }}
  for (var i = 0; i < pts.length - 1; i++)
    _cylTo(MEASURE_SHAPES, pts[i], pts[i + 1], color, 0.055);
  for (var i = 0; i < pts.length; i++)
    _meshTo(MEASURE_SHAPES, pts[i], _atomRadius(m.atoms[i]), color);
  if (m.kind === 'angle' && pts.length === 3) _angleArc(pts[0], pts[1], pts[2], color);
}}
function drawMeasures(list) {{
  _clearShapes(MEASURE_SHAPES);
  if (list) {{ for (var i = 0; i < list.length; i++) _drawMeasure(list[i]); }}
  viewer.render();
}}
window.setMeasures = drawMeasures;
window.clearMeasures = function() {{ drawMeasures(null); }};
window.restoreMeasures = function() {{ drawMeasures(MEASURES); }};
// 正在点选、尚未成组的原子: 用高亮网格球提示
window.setPending = function(list) {{
  _clearShapes(PENDING_SHAPES);
  if (list) {{
    for (var i = 0; i < list.length; i++) {{
      var p = _atomPos(list[i]);
      if (p) _meshTo(PENDING_SHAPES, p, _atomRadius(list[i], 1.35), '#FFD60A');
    }}
  }}
  viewer.render();
}};

drawMeasures(MEASURES);

// ==================================================================
// 相机: **正交投影 (orthographic)** —— 远近同大, 无“近大远小”。
// ==================================================================
// 说明: 3Dmol 的 show() 内部会调用 setSlabAndFog(), 它按
//       right = distance * tan(fov) 重算投影, 于是直接改 camera.left/right
//       会被立刻覆盖。因此这里用「相机距离」驱动正交缩放:
//           distance = FIT[3] * ASPECT / (ZOOM * ORTHO_ZOOM * tan(fov))
//       使正交视锥半高 = FIT[3]/(ZOOM*ORTHO_ZOOM), 与相机 z 位置无关,
//       旋转时模型大小恒定 —— 这正是消除“物体转近变大、转远变小”的关键。
viewer.setProjection('orthographic');

var ORTHO_ZOOM = 1.0;
var PAN = [PAN0[0], PAN0[1]];

function fovRadians() {{
  return Math.PI / 180.0 * (viewer.fov || viewer.camera.fov || 20.0);
}}
function viewAspect() {{
  var a = viewer.ASPECT || (viewer.WIDTH && viewer.HEIGHT
                            ? viewer.WIDTH / viewer.HEIGHT : 1.0);
  if (!isFinite(a) || a <= 0) a = 1.0;
  return a;
}}

// 应用朝向四元数; quat 为空时只重算取景 (保留当前朝向, 供滚轮缩放)
function applyCamera(quat) {{
  if (!FIT) {{
    viewer.zoomTo();
    viewer.zoom(ZOOM, 0);
    if (quat) {{
      var v = viewer.getView();
      viewer.setView([v[0], v[1], v[2], v[3], quat[0], quat[1], quat[2], quat[3]]);
    }}
    viewer.show();
    return;
  }}
  if (quat) viewer.rotationGroup.quaternion.set(quat[0], quat[1], quat[2], quat[3]);
  var fov = fovRadians();
  var aspect = viewAspect();
  var dist = FIT[3] * aspect / (ZOOM * ORTHO_ZOOM * Math.tan(fov));
  // 正交取景由「相机到模型的距离」决定 (show() 会据此重算正交视锥)
  viewer.rotationGroup.position.set(0, 0, 0);
  viewer.rotationGroup.position.z = viewer.CAMERA_Z - dist;
  // 正视锥半宽 = dist*tan(fov); PAN 为屏幕比例平移 (正值 = 右 / 上)
  var halfW = dist * Math.tan(fov);
  var halfH = halfW / aspect;
  viewer.modelGroup.position.set(-FIT[0] + PAN[0] * 2.0 * halfW,
                                 -FIT[1] + PAN[1] * 2.0 * halfH,
                                 -FIT[2]);
  // 保证模型完整落在近 / 远裁剪面之间
  viewer.slabNear = -(FIT[3] + 20.0);
  viewer.slabFar = FIT[3] + 20.0;
  viewer.show();   // -> setSlabAndFog(): 重算正交视锥 + 渲染
}}

function refreshView() {{ applyCamera(null); }}

// 自定义欧拉视角 (ROT): 先让 3Dmol 转模型, 再取回四元数
function baseQuat() {{
  if (ORIENT) return ORIENT;
  if (ROT.length) {{
    var v0 = viewer.getView();
    viewer.setView([v0[0], v0[1], v0[2], v0[3], 0, 0, 0, 1]);
    for (const r of ROT) viewer.rotate(r[0], r[1]);
    return viewer.getView().slice(4, 8);
  }}
  return [0, 0, 0, 1];
}}

// 初始朝向: ORIENT (预设/捕获视角) > 逐帧 VIEWS 首帧 > ROT > 单位
var INIT_Q = ORIENT || ((VIEWS && VIEWS[0]) ? VIEWS[0] : null);
if (!INIT_Q && ROT.length) INIT_Q = baseQuat();
if (!INIT_Q) INIT_Q = [0, 0, 0, 1];
applyCamera(INIT_Q);
viewer.render();

window.showFrame = function(i) {{
  viewer.removeAllModels();
  viewer.addModel(FRAMES[i], 'xyz');
  applyStyle();
  // VIEWS: 逐帧朝向四元数 (绕轴旋转用); 取景每帧重算
  if (VIEWS) applyCamera(VIEWS[i]);
  if (SPIN) viewer.rotate(SPIN * i, 'y');
  viewer.render();
  window.currentFrame = i;
}};
window.spin = function(deg) {{ viewer.rotate(deg, 'y'); viewer.render(); }};
window.zoomByFactor = function(f) {{ ORTHO_ZOOM *= f; refreshView(); }};
window.capture = function() {{ return viewer.pngURI(); }};
window.numFrames = FRAMES.length;
window.setView = function(v) {{ viewer.setView(v); viewer.render(); }};
window.getView = function() {{ return viewer.getView(); }};
{interact_js}
window.ready = true;
</script></body></html>"""


def find_system_browser():
    """找一个可用的系统浏览器 (Edge / Chrome) 可执行文件, 找不到返回 None。

    为什么要自己找: Playwright 的 channel="msedge" 靠 ProgramFiles /
    LOCALAPPDATA 等**环境变量**拼路径, 一旦这些变量不在 (某些启动方式),
    就会拼出 “undefined\\Program Files\\...msedge.exe” 而失败。
    对外分发的 exe 不能靠这个, 所以直接查注册表 App Paths + 常见安装路径
    + PATH。Win10/11 自带 Edge, 因此一般都能命中。
    """
    names = ("msedge.exe", "chrome.exe")
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local = os.environ.get("LOCALAPPDATA") or ""
    cands = [
        os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
    ]
    if local:
        cands.append(os.path.join(local, r"Google\Chrome\Application\chrome.exe"))
        cands.append(os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"))
    for p in cands:
        if p and os.path.isfile(p):
            return p
    # 注册表 App Paths 最可靠
    try:
        import winreg
        BS = chr(92)          # 不用字面反斜杠, 免得转义踩坑
        tail = ["Microsoft", "Windows", "CurrentVersion", "App Paths"]
        sub_keys = (BS.join(["SOFTWARE"] + tail),
                    BS.join(["SOFTWARE", "WOW6432Node"] + tail))
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in sub_keys:
                for nm in names:
                    try:
                        with winreg.OpenKey(root, sub + BS + nm) as key:
                            val = winreg.QueryValue(key, None)
                    except OSError:
                        continue
                    if val:
                        val = os.path.expandvars(str(val).strip('"'))
                        if os.path.isfile(val):
                            return val
    except Exception:  # noqa: BLE001
        pass
    for nm in names:
        p = shutil.which(nm)
        if p:
            return p
    return None


def launch_browser(pw, channel, headless):
    tried = []
    last = None
    if channel:
        plans = [("channel=" + channel, {"channel": channel})]
    else:
        plans = []
        exe = find_system_browser()
        if exe:
            print(f"[信息] 找到系统浏览器: {exe}")
            plans.append((exe, {"executable_path": exe}))
        plans.append(("channel=msedge", {"channel": "msedge"}))
        plans.append(("channel=chrome", {"channel": "chrome"}))
        plans.append(("playwright-chromium", {}))
    for label, extra in plans:
        try:
            browser = pw.chromium.launch(headless=headless, **extra)
            print(f"[信息] 已启动浏览器: {label}")
            return browser
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{label}: {exc}".splitlines()[0])
            last = exc
    raise SystemExit(
        "[错误] 无法启动浏览器:\n  " + "\n  ".join(tried) +
        "\n本程序需要系统自带的 Edge (Win10/11 默认都有) 或 Chrome。\n"
        "也可以只把结构导出看曲线, 或执行: python -m playwright install chromium\n"
        f"原始错误: {last}"
    )


def _quat_matrix(q):
    """四元数 (x, y, z, w) -> 3x3 旋转矩阵 (与 three.js / 3Dmol 一致)。"""
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _axis_angle_matrix(axis, ang):
    """Rodrigues: 绕 axis 转 ang 弧度的旋转矩阵。"""
    n = _unit(axis)
    k = np.array([[0.0, -n[2], n[1]], [n[2], 0.0, -n[0]], [-n[1], n[0], 0.0]])
    return np.eye(3) + math.sin(ang) * k + (1.0 - math.cos(ang)) * (k @ k)


# 绕轴旋转可选轴: 晶胞 a/b/c 轴, 或当前视图的屏幕竖直/水平方向
ROT_AXIS_NAMES = {
    "a": "a 轴", "b": "b 轴", "c": "c 轴",
    "screen-v": "屏幕竖直", "screen-h": "屏幕水平",
}


def rotation_views(cell, base_quat, axis_key, total_deg, n, closed=None):
    """绕指定轴匀速旋转, 返回 n 个四元数 (每帧一个)。

    base_quat: 起始朝向 (预设视角 / 捕获视角 A 的四元数)。
    axis_key:  a / b / c  (晶胞轴)  或  screen-v / screen-h (当前视图的
               屏幕竖直 / 水平方向)。
    total_deg: 整段动画总共转过的角度 (360 = 转一圈)。
    closed:    None = 自动判断; True/False = 强制是否无缝循环。

    旋转发生在**模型自身坐标系**里: R(i) = R_base . R_axis(角度_i)。
    不论基准视角如何, 转轴始终是你指定的那根物理轴 —— 它投影到屏幕上
    后方向保持不变, 模型绕它转, 不会出现“转轴在屏幕上乱跑”的抖动。

    采样方式 (修正了旧版的“首帧重复”卡顿):
        * total 是 360° 的整数倍 → 采样 k/n: 0, 360/n, ... (n-1)*360/n,
          **不重复首帧**, GIF 循环时无缝、无停顿；
        * 其它角度 (如 180°) → 采样 k/(n-1): 最后一张正好到达目标角度。
    """
    cell = np.asarray(cell, dtype=float).reshape(3, 3)
    a, b, c = cell[0], cell[1], cell[2]
    if axis_key == "a":
        axis = _unit(a)
    elif axis_key == "b":
        axis = _unit(b)
    elif axis_key == "c":
        axis = _unit(c)
    else:
        axis = None
    rb = _quat_matrix(base_quat)
    if axis is None:
        # rb 的两行 = 模型空间里“屏幕右 / 屏幕上”的方向
        axis = rb[1] if axis_key == "screen-v" else rb[0]
    axis = _unit(axis)

    n = max(1, int(n))
    total = float(total_deg)
    if closed is None:
        closed = (n > 1 and abs(total) > 1e-9
                  and abs(abs(total) % 360.0) < 1e-6)

    out = []
    for k in range(n):
        if n <= 1:
            t = 0.0
        elif closed:
            t = k / n
        else:
            t = k / (n - 1)
        ang = math.radians(total) * t
        r = rb @ _axis_angle_matrix(axis, ang)
        out.append([float(v) for v in _quat_from_matrix(r)])
    return out


def render_pngs(images, args, frames_dir, progress=None, cancel=None,
                clean_dir=None):
    """用 Playwright + 3Dmol.js 逐帧截图。

    clean_dir 不为 None 时, 同时把「无测量标记」的帧存到该目录
    (同一浏览器会话 / 同一相机, 仅临时隐藏测量形状), 并返回
    (marked_paths, clean_paths); 否则只返回 marked_paths。

    说明:
        * 3Dmol 的 pngURI() 输出的是 WebGL canvas 的物理像素, 其尺寸为
          CSS 尺寸 x 设备像素比(DPR, 常见为 1 或 2)。因此这里先向页面查询
          DPR, 再按 DPR 归一化 zoom, 保证不同屏幕下取景一致。
        * args.scale 作为超采样倍率: 实际渲染 CSS 尺寸 = 画布尺寸 x scale,
          合成 GIF 时再缩放回画布尺寸, 以提升清晰度。
        * 若提供 args.view_start (交互窗口捕获的视角 A), 则逐帧改用其
          朝向四元数; 若同时给了 args.rot_axis / args.rot_total, 则在其
          基础上绕指定轴匀速旋转。相机中心/缩放一律由 zoomTo() +
          zoom(args.zoom) 现场计算, 保证与交互窗口取景一致。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("[错误] 未安装 playwright, 请执行: pip install playwright")

    js = ensure_3dmol_js()
    xyz_frames = frames_to_xyz(images)
    edges = cell_edges(images[0])
    style = style_for(args.style)
    elem_map = make_element_map(images, args._vesta_colors, args._vesta_radii,
                                args.radius_scale, args.radius,
                                getattr(args, "radius_overrides", None))
    print("[信息] 元素配色/半径: " + ", ".join(
        f"{el}{m['color'] or '(默认)'}r={m['radius']}"
        for el, m in elem_map.items()))
    rot_text = args.rot if args.rot is not None else VIEW_PRESETS[args.view]
    rot = parse_rotation(rot_text) if rot_text else []
    # 预设的六个标准视角: 根据晶胞矢量计算四元数 (正视图: a-c 面平行屏幕,
    # a -> 屏幕 +x, c -> 屏幕 +y)。自定义 --rot 时不用四元数。
    orient = None
    if args.rot is None and args.view in VIEW_NAMES:
        orient = safe_view_quaternion(images[0].get_cell(), args.view)
        if orient is not None:
            rot = []

    ss = float(getattr(args, "scale", 1.0) or 1.0)
    # 相机取景基准: 显示晶胞盒时连同盒子一起取景, 否则只按原子取景
    # (分子/团簇在超大晶胞里时, 只按原子取景才不会小得看不见)
    fit = fit_sphere(images, include_cell=bool(getattr(args, "cell", True)))
    print("[信息] 取景包围球 中心=(%.2f, %.2f, %.2f) 半径=%.2f Å" % tuple(fit))
    if getattr(args, "pan_x", 0) or getattr(args, "pan_y", 0):
        print(f"[信息] 视角平移 水平={args.pan_x:+.3f} 垂直={args.pan_y:+.3f} (画布比例)")
    # render_width: 左右布局时轨迹只占左侧, 由调用方 (GUI/CLI) 指定
    bw = int(getattr(args, "render_width", 0) or 0) or args.width
    render_w = max(80, int(round(bw * ss)))
    render_h = max(80, int(round((args.height or args.width) * ss)))

    # ---- 逐帧相机朝向: 捕获视角 A / 绕轴旋转 ----
    views = getattr(args, "views", None)      # 外部直接给定的逐帧四元数
    v_start = getattr(args, "view_start", None)
    base_q = None
    if v_start:
        # 只取捕获视角的朝向四元数; 中心/缩放由页内 zoomTo + ZOOM 重算,
        # 与「轨迹浏览」页取景严格一致 (与捕获画布尺寸/DPI 无关)。
        base_q = [float(x) for x in np.asarray(v_start, dtype=float)[4:8]]
        orient = None
        rot = []
    if views is None:
        axis_key = str(getattr(args, "rot_axis", "") or "")
        total = float(getattr(args, "rot_total", 0.0) or 0.0)
        base = base_q if base_q is not None else (list(orient) if orient else None)
        if axis_key and abs(total) > 1e-9 and base is not None:
            views = rotation_views(images[0].get_cell(), base, axis_key,
                                  total, len(xyz_frames))
            orient = None
            rot = []
            print(f"[信息] 绕 {ROT_AXIS_NAMES.get(axis_key, axis_key)} "
                  f"旋转 {total:g}°")
        elif base_q is not None:
            base_q = _unit(base_q).tolist()
            views = [list(base_q) for _ in xyz_frames]
            print("[信息] 使用捕获的固定视角")
    if views is not None:
        orient = None
        rot = []
        views = [list(v) for v in views]
        if len(views) < len(xyz_frames):
            views = views + [views[-1]] * (len(xyz_frames) - len(views))

    html_path = frames_dir / "_viewer.html"
    paths = []
    clean_paths = []
    if clean_dir is not None:
        Path(clean_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = launch_browser(pw, args.browser, not args.show_browser)
        page = browser.new_page(viewport={"width": render_w, "height": render_h},
                                device_scale_factor=1.0)
        # 查询真实像素比 (canvas 物理像素 / CSS 像素)。注意: 3Dmol 的
        # pngURI() 输出 canvas 物理像素, 而 Playwright 的 device_scale_factor
        # 在部分环境下不生效, 因此直接读取实际 canvas 尺寸最可靠。
        try:
            pr = float(page.evaluate(
                "(function(){var c=document.querySelector('#v canvas');"
                "return c ? (c.width/c.clientWidth) : (window.devicePixelRatio||1);})()"))
        except Exception:
            pr = 1.0
        if not pr or pr <= 0:
            pr = 1.0

        # 相机取景完全由页面内的 zoomTo() + zoom(zoom_base) 决定, 与画布
        # 像素比 / CSS 尺寸无关 (见 build_html.applyCamera)。
        zoom_base = args.zoom * ss
        print(f"[信息] 渲染 CSS {render_w}x{render_h}, 像素比={pr:g}, "
              f"实际像素 {int(render_w * pr)}x{int(render_h * pr)}")
        html = build_html(js, xyz_frames, edges, render_w, render_h,
                          args.bg, style, elem_map, args.cell, args.cell_color,
                          zoom_base, rot, views=views, spin=args.spin,
                          orient=orient, fit=fit,
                          pan=(getattr(args, "pan_x", 0.0) or 0.0,
                               getattr(args, "pan_y", 0.0) or 0.0),
                          measures=getattr(args, "measures", None))
        html_path.write_text(html, encoding="utf-8")

        page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded",
                  timeout=120000)
        page.wait_for_function("window.ready === true", timeout=120000)
        page.wait_for_timeout(500)

        n = page.evaluate("window.numFrames")
        for i in range(n):
            if cancel is not None and cancel():
                print("[信息] 已取消, 停止渲染")
                break
            page.evaluate(f"window.showFrame({i})")
            uri = page.evaluate("window.capture()")
            raw = base64.b64decode(uri.split(",", 1)[1])
            p = frames_dir / f"frame_{i:05d}.png"
            p.write_bytes(raw)
            paths.append(p)
            if clean_dir is not None:
                page.evaluate("window.clearMeasures && window.clearMeasures()")
                uri_c = page.evaluate("window.capture()")
                raw_c = base64.b64decode(uri_c.split(",", 1)[1])
                pc = Path(clean_dir) / f"frame_{i:05d}.png"
                pc.write_bytes(raw_c)
                clean_paths.append(pc)
                page.evaluate(
                    "window.restoreMeasures && window.restoreMeasures()")
            if (i + 1) % 10 == 0 or i == n - 1:
                print(f"[信息] 截图 {i + 1}/{n}")
            if progress is not None:
                progress(i + 1, n)
        browser.close()
    if clean_dir is not None:
        return paths, clean_paths
    return paths


def render_frames(images, args, frames_dir, progress=None, cancel=None):
    """渲染帧, 自动处理元素图例与测量版式。

    * 无测量: 单视图, 底部加「元素 -> 颜色」图例。
    * 有测量: 同屏渲染「无标记」与「有网格球」两套帧, 拼成
      左上原始结构 / 右上带标记结构 / 下方元素图例 + 测量文本。

    返回可用于 build_gif() 的 PNG 路径列表。
    """
    bg = getattr(args, "bg", "white")
    legend = element_legend(images, args)
    measures = getattr(args, "measures", None)
    if not measures:
        paths = render_pngs(images, args, frames_dir, progress=progress,
                            cancel=cancel)
        if not legend or not paths:
            return paths
        frames_dir = Path(frames_dir)
        out = []
        for i, p in enumerate(paths):
            im = draw_element_legend(Image.open(p), legend, bg=bg)
            q = frames_dir / f"legend_{i:05d}.png"
            im.save(q)
            out.append(q)
        return out

    frames_dir = Path(frames_dir)
    clean_dir = frames_dir / "_clean"
    clean_dir.mkdir(parents=True, exist_ok=True)

    print("[信息] 测量版式: 同屏渲染「带标记 / 无标记」两套帧…")
    marked, clean = render_pngs(images, args, frames_dir,
                                progress=progress, cancel=cancel,
                                clean_dir=clean_dir)
    if not marked or not clean:
        return []

    lines = measure_labels(images[0], measures)
    out = []
    for i, (cp, mp) in enumerate(zip(clean, marked)):
        im = compose_measure_pair(Image.open(cp), Image.open(mp), lines,
                                  bg=bg, legend=legend)
        p = frames_dir / f"combo_{i:05d}.png"
        im.save(p)
        out.append(p)
    return out


# --------------------------------------------------------------------------
# 合成 GIF
# --------------------------------------------------------------------------
def build_gif(paths, out_path, duration, loop, colors, pingpong, width,
              annotate=None):
    """合成 GIF。annotate(i, PIL.Image) -> PIL.Image 可为每帧叠加标注。

    标注在缩放到目标宽度之后应用, 保证文字清晰、不随超采样模糊。
    """
    frames = [Image.open(p).convert("RGB") for p in paths]
    if width and frames and frames[0].width != width:
        h = round(width * frames[0].height / frames[0].width)
        frames = [f.resize((width, h), Image.LANCZOS) for f in frames]
    if annotate is not None:
        frames = [annotate(i, im) for i, im in enumerate(frames)]
    if pingpong and len(frames) > 2:
        frames = frames + frames[-2:0:-1]

    pal_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=colors)
                  for f in frames]
    save_kwargs = dict(
        save_all=True,
        append_images=pal_frames[1:],
        duration=duration,
        disposal=2,
        optimize=False,
    )
    # loop=None 时不写循环块 → 多数播放器只播一遍; loop=0 → 无限循环
    if loop is not None:
        save_kwargs["loop"] = loop
    pal_frames[0].save(out_path, **save_kwargs)
    return len(pal_frames)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="把单个 CONTCAR 结构渲染成旋转 GIF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("-f", "--file", default="CONTCAR", help="结构文件 (默认 CONTCAR)")
    ap.add_argument("-o", "--out", default="CONTCAR.gif",
                    help="输出 GIF (默认 CONTCAR.gif); 若为 .png 则只存单帧")
    ap.add_argument("--frames", type=int, default=60, help="旋转帧数 (默认 60)")
    ap.add_argument("-r", "--repeat", type=int, nargs=3, metavar=("A", "B", "C"),
                    default=None, help="超胞重复, 如 -r 2 2 1")

    ap.add_argument("--style", default="ballstick",
                    choices=["ballstick", "sphere", "stick", "line"],
                    help="原子显示风格 (默认 ballstick)")
    ap.add_argument("--radius", type=float, default=None,
                    help="统一原子球半径; 默认使用 VESTA 半径 x --radius-scale")
    ap.add_argument("--radius-scale", type=float, default=0.6,
                    help="VESTA 半径的缩放系数 (默认 0.6)")
    ap.add_argument("--no-vesta", dest="use_vesta", action="store_false",
                    default=True, help="不使用内置 VESTA 配色")
    ap.add_argument("--cell", action="store_true", default=True,
                    help="显示晶胞框 (默认显示)")
    ap.add_argument("--no-cell", dest="cell", action="store_false",
                    help="不显示晶胞框")
    ap.add_argument("--cell-color", default="#888888", help="晶胞框颜色")
    ap.add_argument("--bg", default="white", help="背景色 (默认 white)")

    ap.add_argument("-w", "--width", type=int, default=600, help="宽 (像素)")
    ap.add_argument("--height", type=int, default=None,
                    help="高 (像素, 默认同宽)")
    ap.add_argument("--gif-width", type=int, default=None,
                    help="GIF 输出宽度 (默认同 --width)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="超采样倍率, 2 可得到更清晰的大图")
    ap.add_argument("--zoom", type=float, default=1.25, help="初始缩放 (默认 1.25)")
    ap.add_argument("--view", default="front", choices=list(VIEW_PRESETS),
                    help="基准视角 (默认 front)")
    ap.add_argument("--rot", default=None,
                    help="自定义视角旋转, 如 '20x,-20y,0z', 覆盖 --view")
    ap.add_argument("--spin", type=float, default=0.0,
                    help="每帧额外绕 y 轴旋转的角度, 如 2 (默认 0 不转)")
    ap.add_argument("--measure", action="append", default=None, metavar="ATOMS",
                    help="添加一组测量 (可重复), 如 --measure 0,1 "
                         "--measure dihedral:0,1,2,3; 原子下标从 0 开始, "
                         "2/3/4 个原子分别为键长/键角/二面角")
    ap.add_argument("--rot-axis", default="c", choices=list(ROT_AXIS_NAMES),
                    help="绕轴旋转: a/b/c (晶胞轴) 或 screen-v/screen-h (默认 c)")
    ap.add_argument("--rot-angle", type=float, default=360.0, dest="rot_total",
                    help="整段旋转转过的总角度 (默认 360)")
    ap.add_argument("--pan-x", type=float, default=0.0, dest="pan_x",
                    help="视角水平平移 (画布宽度比例, 如 0.1 = 右移 10%%)")
    ap.add_argument("--pan-y", type=float, default=0.0, dest="pan_y",
                    help="视角垂直平移 (画布高度比例, 如 -0.1 = 下移 10%%)")

    ap.add_argument("--fps", type=float, default=20.0, help="GIF 帧率 (默认 20)")
    ap.add_argument("--colors", type=int, default=256, help="GIF 调色板颜色数 (默认 256)")
    ap.add_argument("--pingpong", action="store_true",
                    help="正放+倒放, 循环更顺滑")
    ap.add_argument("--loop", type=int, default=0, help="循环次数, 0=无限")

    ap.add_argument("--browser", default=None,
                    help="浏览器 channel, 如 msedge / chrome (默认自动)")
    ap.add_argument("--show-browser", action="store_true",
                    help="显示浏览器窗口 (调试用)")
    ap.add_argument("--keep-frames", action="store_true",
                    help="保留下载的 PNG 帧 (默认渲染完删除)")
    ap.add_argument("--frames-dir", default=None, help="截图输出目录")
    args = ap.parse_args()

    if args.height is None:
        args.height = args.width
    if args.frames < 1:
        raise SystemExit("[错误] --frames 至少为 1")

    # ---- 内置 VESTA 配色 / 半径 ----
    resolve_vesta(args)
    if args.use_vesta:
        print("[信息] 使用内置 VESTA 经典配色")
    else:
        print("[信息] 已禁用 VESTA 配色, 使用 3Dmol 默认颜色")
    args.color_overrides = {}
    args.radius_overrides = {}
    args.render_width = args.width
    args.views = None          # 交给 render_pngs 根据 view/rot-axis 计算
    args.view_start = None
    args.measures = parse_measures(getattr(args, "measure", None))
    if args.measures:
        print("[信息] 测量标注: " + "; ".join(
            f"{m['kind']} {m['atoms']}" for m in args.measures))

    atoms = load_contcar(args.file)
    images = build_frames(atoms, args.frames, args.repeat)

    # 单帧模式: 直接输出 PNG
    single_png = args.out.lower().endswith(".png")
    if single_png and len(images) > 1:
        print("[信息] 输出为 .png, 只渲染第一帧")
        images = images[:1]

    if args.frames_dir:
        frames_dir = Path(args.frames_dir)
        frames_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        frames_dir = Path(tempfile.mkdtemp(prefix="3dmol-frames-"))
        cleanup = not args.keep_frames

    try:
        paths = render_frames(images, args, frames_dir)

        if single_png:
            Image.open(paths[0]).convert("RGB").save(args.out)
            print(f"[完成] 单帧图片: {args.out}")
        else:
            duration = max(1, int(round(1000.0 / args.fps)))
            # 测量版式已经拼成宽图, 不再缩放, 避免文字变小
            gif_width = None if args.measures else (
                args.gif_width if args.gif_width else args.width)
            n = build_gif(paths, args.out, duration, args.loop,
                          args.colors, args.pingpong, gif_width)
            size = os.path.getsize(args.out) / 1e6
            print(f"[完成] GIF: {args.out}  ({n} 帧, {args.fps:g} fps, {size:.1f} MB)")

        if args.keep_frames:
            print(f"[信息] PNG 帧保存在: {frames_dir}")
    finally:
        if cleanup:
            shutil.rmtree(frames_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
