"""首次启动依赖检测与自动修复。

本模块不导入任何第三方库 —— 这是它的职责所在：在主程序因缺依赖崩溃之前，
先把环境补齐或给出可操作的中文提示。``src/main.py`` 与启动脚本在每次启动时
都会先经过这里；环境就绪时只做几次轻量检查，秒级通过。

用法：

    python src/bootstrap.py            # 检测；缺依赖时询问并自动安装
    python src/bootstrap.py --auto     # 检测；缺依赖时直接自动安装，不询问
    python src/bootstrap.py --quiet    # 就绪时零输出（脚本调用用）

也可在代码中调用::

    from src.bootstrap import ensure_ready
    if not ensure_ready(auto=True):
        ...  # 修复失败，退出
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
FETCH_MERMAID = PROJECT_ROOT / "tools" / "fetch_mermaid.py"

MIN_PYTHON = (3, 10)
REQUIRED_PACKAGES = ("pymupdf", "openai")
OPTIONAL_PACKAGES = ("dotenv",)  # python-dotenv 的导入名
REQUIRED_ASSETS = (
    PROJECT_ROOT / "assets" / "vendor" / "mermaid.min.js",
    PROJECT_ROOT / "assets" / "templates" / "dashboard.html",
    PROJECT_ROOT / "assets" / "templates" / "app.js",
)
PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，避免中文输出触发 UnicodeEncodeError。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def python_version_ok() -> bool:
    return sys.version_info >= MIN_PYTHON


def missing_required() -> list[str]:
    """缺失的必需依赖（缺任何一个都无法跑通主流程）。"""
    return [name for name in REQUIRED_PACKAGES if find_spec(name) is None]


def missing_optional() -> list[str]:
    """缺失的可选依赖（有内置退化实现，不阻断主流程）。"""
    return [name for name in OPTIONAL_PACKAGES if find_spec(name) is None]


def missing_assets() -> list[Path]:
    """缺失的离线资源文件。"""
    return [path for path in REQUIRED_ASSETS if not path.exists()]


def problems() -> list[str]:
    """当前环境的问题清单（中文、可读）；空列表表示一切就绪。"""
    found: list[str] = []
    if not python_version_ok():
        found.append(
            f"Python 版本过低（{sys.version.split()[0]}），"
            f"需要 {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上"
        )
    for name in missing_required():
        found.append(f"缺少必需依赖：{name}")
    for name in missing_optional():
        found.append("缺少可选依赖：python-dotenv（仅影响 .env 自动加载，不阻断）")
    for path in missing_assets():
        found.append(f"缺少离线资源：{path.name}（期望路径 {path}）")
    return found


def _pip_install() -> bool:
    if not REQUIREMENTS.exists():
        print(f"  ✗ 找不到 {REQUIREMENTS}，无法自动安装。请从完整发布包中恢复该文件。")
        return False
    base = [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    print("  → 正在安装依赖（首次安装可能需要几分钟）...")
    try:
        if subprocess.call(base) == 0:
            return True
        print("  → 默认 PyPI 源安装失败，改用清华镜像重试一次 ...")
        return subprocess.call(base + ["-i", PYPI_MIRROR]) == 0
    except OSError as exc:
        print(f"  ✗ 无法启动 pip：{exc}")
        return False


def _fetch_mermaid() -> bool:
    if not FETCH_MERMAID.exists():
        print(f"  ✗ 找不到 {FETCH_MERMAID}，无法自动下载。")
        return False
    print("  → 正在下载离线 Mermaid 运行库（约 3.3 MB，仅需一次）...")
    try:
        return subprocess.call([sys.executable, str(FETCH_MERMAID)]) == 0
    except OSError as exc:
        print(f"  ✗ 无法启动下载脚本：{exc}")
        return False


def _print_manual_fixes() -> None:
    print("\n可手动修复：")
    print(f"  1) 安装依赖：{Path(sys.executable).name} -m pip install -r requirements.txt")
    print(f"  2) 下载离线渲染库：{Path(sys.executable).name} tools{os.sep}fetch_mermaid.py")
    print("     （或从 https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js")
    print("      手动下载，另存为 assets/vendor/mermaid.min.js）")


def repair(auto: bool = False) -> bool:
    """尝试自动修复问题；返回修复后是否全部就绪。问题清单由调用方打印。"""
    if not python_version_ok():
        print(
            f"  ✗ Python {sys.version.split()[0]} 过低，请安装 "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ 后重试（此项无法自动修复）。"
        )
        return False

    need_install = bool(missing_required())
    need_mermaid = (REQUIRED_ASSETS[0] in missing_assets())
    need_manual = any(
        path in missing_assets() for path in (REQUIRED_ASSETS[1], REQUIRED_ASSETS[2])
    )

    if need_install:
        if auto or _stdin_is_tty():
            if not auto:
                try:
                    answer = input("是否现在自动安装缺失依赖？[Y/n] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print()
                    answer = "n"
                if answer not in ("", "y", "yes"):
                    _print_manual_fixes()
                    return False
            if not _pip_install():
                print("  ✗ 依赖自动安装失败。")
                _print_manual_fixes()
                return False
        else:
            print("  （非交互环境，跳过自动安装）")
            _print_manual_fixes()
            return False

    if need_mermaid:
        if not _fetch_mermaid():
            print("  ✗ 离线渲染库自动下载失败，请检查网络后重试，或手动下载。")
            _print_manual_fixes()
            return False

    if need_manual:
        print("  ✗ 模板或前端脚本缺失（正常发布包不会缺），请重新获取完整项目目录。")
        return False

    remaining = problems()
    if remaining:
        for item in remaining:
            print(f"  ✗ {item}")
        return False
    return True


def ensure_ready(auto: bool = False, verbose: bool = True) -> bool:
    """启动时的依赖检测入口。

    就绪返回 True（verbose 时打印一行确认）；发现问题则尝试自动修复，
    修复失败返回 False 并已打印中文修复建议。
    """
    found = problems()
    if not found:
        if verbose:
            print("✓ 依赖检测通过，环境就绪。")
        return True
    print("首次启动依赖检测发现问题：")
    for item in found:
        print(f"  · {item}")
    if repair(auto=auto):
        if verbose:
            print("✓ 依赖已修复，环境就绪。")
        return True
    print("✗ 环境未就绪，请按上方提示处理后重试。")
    return False


def check_environment() -> tuple[bool, list[str]]:
    """--check 自检：依赖、离线资源、API 配置。只报告，不修改环境。

    从 src/pipeline.py 移入本模块：自检的意义就在于报告缺失项，
    不能反过来先被缺失的依赖卡在导入阶段。
    """
    lines: list[str] = []
    ok = True

    lines.append(f"Python: {sys.version.split()[0]}")
    if not python_version_ok():
        ok = False
        lines.append(f"  ✗ 需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
    try:
        import pymupdf  # noqa: F401

        lines.append(f"PyMuPDF: {getattr(pymupdf, '__version__', 'unknown')} ✓")
    except ImportError:
        ok = False
        lines.append("PyMuPDF: 未安装 ✗  → pip install pymupdf")

    try:
        import openai

        lines.append(f"openai: {openai.__version__} ✓")
    except ImportError:
        ok = False
        lines.append("openai: 未安装 ✗  → pip install openai")

    try:
        import dotenv  # noqa: F401

        lines.append("python-dotenv: 已安装 ✓")
    except ImportError:
        lines.append("python-dotenv: 未安装（可选，仅影响 .env 自动加载）")

    from .config import DASHBOARD_TEMPLATE, MERMAID_JS, PROJECT_ROOT

    for label, path in (
        ("HTML 模板", DASHBOARD_TEMPLATE),
        ("前端脚本", DASHBOARD_TEMPLATE.parent / "app.js"),
        ("Mermaid 离线库", MERMAID_JS),
    ):
        if path.exists():
            size = path.stat().st_size
            lines.append(f"{label}: {path.name} ({size / 1024:.0f} KB) ✓")
        else:
            ok = False
            lines.append(f"{label}: 缺失 ✗  → 期望路径 {path}")

    from .config import Settings

    settings = Settings.from_env()
    problems_ = settings.validate()
    if problems_:
        lines.append("API 配置: 不完整（仍可用 --mock 离线演示）")
        for problem in problems_:
            lines.append(f"    · {problem}")
    else:
        lines.append(
            f"API 配置: {settings.model} @ {settings.base_url}"
            f"（key={settings.masked_key()}）✓"
        )

    examples_dir = PROJECT_ROOT / "examples"
    pdfs = sorted(examples_dir.glob("*.pdf")) if examples_dir.exists() else []
    suffix = f" → {', '.join(p.name for p in pdfs[:4])}" if pdfs else ""
    lines.append(
        f"示例 PDF: {len(pdfs)} 个"
        + suffix
        + ("" if pdfs else "（可用 python examples/make_samples.py 生成）")
    )

    lines.append(f"工作目录: {PROJECT_ROOT}")
    lines.append(f"磁盘可用: {shutil.disk_usage(PROJECT_ROOT).free / 1024 ** 3:.1f} GB")
    return ok, lines


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="首次启动依赖检测与自动修复（无第三方依赖，可独立运行）",
    )
    parser.add_argument("--auto", action="store_true", help="缺依赖时直接自动安装，不询问")
    parser.add_argument("--quiet", action="store_true", help="就绪时零输出")
    args = parser.parse_args()

    _force_utf8_stdout()
    if ensure_ready(auto=args.auto, verbose=not args.quiet):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
