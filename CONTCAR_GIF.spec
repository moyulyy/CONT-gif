# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 (CONTCAR 旋转 GIF 工作台)。

默认生成 **便携式文件夹 (onedir)**:

    dist\\CONTCAR_GIF\\CONTCAR_GIF.exe      <- 双击运行
    dist\\CONTCAR_GIF\\_internal\\...        <- 依赖的库/资源

把整个 `CONTCAR_GIF` 文件夹拷到别的 Windows 电脑即可运行 (不需要 Python)。

为什么不用 onefile: 单文件模式每次启动都要把 300+ MB 解包到 %TEMP%, 还会
触发杀毒软件实时扫描, 结果就是启动慢、卡顿、磁盘/内存占用翻倍。
要看单文件就把环境变量 CONT_ONEFILE 设成 1 (不推荐)。

用法:
    D:\\miniconda3\\envs\\chem_env\\python.exe -m PyInstaller --noconfirm --clean CONTCAR_GIF.spec
或直接双击 build_exe.bat
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("CONT_ONEFILE", "0").strip() == "1"

# 本环境 (conda chem_env) 的 DLL 目录。
# 天坑: 若 PATH 里同时有 base conda 的 D:\miniconda3\Library\bin, PyInstaller
# 解析 pyexpat.pyd 依赖时会把 **base 的** libexpat.dll 打进来, 与本环境的
# pyexpat.pyd 版本不匹配 -> 启动就报 “DLL load failed while importing pyexpat”,
# 而且窗口模式下只弹个框, 看不到任何提示。实测 base=416528 字节 /
# env=280904 字节, 换成 env 的后立刻正常。这里做一道保险。
_ENV_LIB = Path(sys.prefix) / "Library" / "bin"
_ENV_DLLS = Path(sys.prefix) / "DLLs"
_ENV_ROOT = str(Path(sys.prefix).resolve()).lower()


def _is_foreign_conda(src):
    """这个 DLL 是不是从**另一个** conda 环境 (base) 拿来的。

    只看 conda 根目录下的(如 D:\\miniconda3\\Library\\bin), 本环境内的、以及
    系统目录里的 (MSVCP140/api-ms-win-*/ucrtbase 等) 一律不动。
    """
    try:
        p = Path(src).resolve()
    except OSError:
        return False
    low = str(p).lower()
    if low.startswith(_ENV_ROOT):
        return False
    return any(("miniconda" in part or "anaconda" in part)
               for part in (x.lower() for x in p.parts))


def _prefer_env_dlls(analysis):
    out, seen, fixed = [], set(), set()
    for name, src, kind in analysis.binaries:
        if _is_foreign_conda(src):
            base = Path(src).name
            for cand_dir in (_ENV_LIB, _ENV_DLLS):
                cand = cand_dir / base
                if cand.is_file():
                    src = str(cand)
                    fixed.add(base)
                    break
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, src, kind))
    analysis.binaries = out
    if fixed:
        print("[spec] 这些 DLL 从 base conda 改回本环境: " + ", ".join(sorted(fixed)))


# ---------------------------------------------------------------------------
# 瘦身: PyInstaller 的 PySide6 hook 会把**整个** Qt6 DLL 集搬进来, 很多是用不到
# 的 (Qt3D / Charts / Quick3D / Multimedia ...)。下面是运行时确定不需要的部分。
# 保守原则: 只要有可能被 QtWidgets / QtWebEngine / QtQuick 间接用到的一律保留
# (QtQuick* / QtQml* / QtPositioning / QtPdf / QtWebSockets / QtSvg / opengl32sw
#  都不能删, 否则 3D 窗口会黑屏或起不来)。
# ---------------------------------------------------------------------------
_QT_KEEP_ALWAYS = ("Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets",
                   "Qt6WebView")

_QT_DROP_PREFIX = (
    "Qt63D", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs",
    "Qt6Quick3D", "Qt6Multimedia", "Qt6SpatialAudio", "Qt6VirtualKeyboard",
    "Qt6RemoteObjects", "Qt6Scxml", "Qt6Sensors", "Qt6TextToSpeech",
    "Qt6SerialPort", "Qt6Sql", "Qt6Test", "Qt6QuickTest",
    "Qt6StateMachine", "Qt6Labs", "Qt6Concurrent", "Qt6Designer",
    "Qt6Help", "Qt6UiTools", "Qt6Bluetooth", "Qt6Nfc", "Qt6NetworkAuth",
)

# 资源类: 只有打开 DevTools / 非中文界面才需要
_DROP_SUBSTR = (
    "qtwebengine_devtools_resources",   # 83 MB! 仅 DevTools 用
    "v8_context_snapshot.debug.bin",    # 调试用
    ".debug.pak",                       # 调试版资源
    "pil/_avif", "pil/_avif_",          # AVIF 解码器, 程序只用 GIF/PNG
)
_KEEP_QM = {"qt_zh_cn.qm", "qt_zh_tw.qm"}


def _prune(entries):
    out, dropped = [], 0
    for item in entries:
        dest = str(item[0]).replace(chr(92), "/")
        low = dest.lower()
        base = low.rsplit("/", 1)[-1]
        if any(sub in low for sub in _DROP_SUBSTR):
            dropped += 1
            continue
        if base.endswith(".qm") and base not in _KEEP_QM:
            dropped += 1
            continue
        if base.startswith("qt6") and base.endswith(".dll"):
            if not base.startswith(tuple(x.lower() for x in _QT_KEEP_ALWAYS)) \
                    and base.startswith(tuple(x.lower() for x in _QT_DROP_PREFIX)):
                dropped += 1
                continue
        out.append(item)
    return out, dropped


datas = [
    ("3dmol", "3dmol"),        # 本地 3Dmol.js (无网也能用)
    ("assets", "assets"),      # 应用图标
    ("samples", "samples"),    # 样例 CONTCAR (启动即可加载 / 自检用)
]
binaries = []

hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtNetwork",
    "playwright",
    "playwright.sync_api",
    "pyee",
    "greenlet",
]

# 本程序用不到的重量级依赖。特别注意: scipy / array_api_compat 会尝试
# import torch, 不排除就会把整个 PyTorch (数 GB) 拖进包里。
EXCLUDES = [
    "tkinter", "torch", "torchvision", "torchaudio", "torchtext",
    "torchdata", "functorch", "torchgen", "IPython", "jupyter",
    "notebook", "pytest", "sphinx", "PyQt5", "PyQt6", "PySide2",
    # 注意: scipy / pandas / sympy / networkx 是 ase / matplotlib 的可选依赖,
    # 不要排除, 否则运行时会报 "No module named 'scipy'"。
    "cv2", "sklearn", "matplotlib.tests", "numpy.tests",
    # PySide6 里确定用不到、也不会被 QtWebEngine 依赖的模块。
    # 注意: QtQml / QtQuick / QtPositioning / QtPdf / QtWebSockets / QtOpenGL
    # 以及它们的 DLL 都是 QtWebEngine 的运行时依赖, 不要排除。
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtScxml", "PySide6.QtRemoteObjects",
]

# Playwright: 需要它的 driver (node) 才能启动系统浏览器
for pkg in ("playwright",):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_all({pkg}) failed: {exc}")

# ASE 的 IO 格式是运行时动态 import 的, 静态分析看不到, 必须显式收集,
# 否则会报 "No module named 'ase.io.xyz'"。
for pkg in ("ase.io", "ase.data"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_submodules({pkg}) failed: {exc}")

try:
    hiddenimports += collect_submodules("playwright")
except Exception:
    pass

block_cipher = None

a = Analysis(
    ["contcar_gif_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 把可能误取自 base conda 的 DLL 换回本环境的版本 (否则 pyexpat 等加载失败)
_prefer_env_dlls(a)

# 瘦身 (删掉 DevTools 资源 / 多余翻译 / 用不到的 Qt 模块)
a.binaries, _n1 = _prune(a.binaries)
a.datas, _n2 = _prune(a.datas)
print(f"[spec] 瘦身: 去掉 {_n1} 个二进制, {_n2} 个数据文件")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_NAME = "CONTCAR_GIF"

if ONEFILE:
    # ---- 单文件: binaries / datas 直接进 EXE (启动要解包, 慢) ----
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name=_NAME,
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
        argv_emulation=False, target_arch=None, codesign_identity=None,
        entitlements_file=None, icon="assets/app.ico",
    )
else:
    # ---- 便携式文件夹: exe + _internal (启动零解包, 推荐) ----
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=_NAME,
        debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
        icon="assets/app.ico",
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, upx_exclude=[],
        name=_NAME,
        contents_directory="_internal",
    )
