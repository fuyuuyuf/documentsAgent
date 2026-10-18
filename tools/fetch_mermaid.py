"""下载并固化 Mermaid 离线渲染库到 assets/vendor/mermaid.min.js。

为什么需要这个脚本：
    比赛要求"HTML 可本地离线打开，不依赖外部 CDN"。
    因此 mermaid.min.js 必须随项目分发并内嵌进产物。
    本脚本只在**首次准备环境**时运行一次，运行时（main.py）永远不联网。

用法：
    python tools/fetch_mermaid.py                 # 使用默认版本 10.9.1
    python tools/fetch_mermaid.py --version 10.9.1
    python tools/fetch_mermaid.py --force         # 已存在时强制重新下载
    python tools/fetch_mermaid.py --check         # 只检查本地文件是否就绪

说明：
    选择 10.x 而不是 11.x，因为 11.x 只提供 ESM 产物，
    无法用普通 <script> 标签内嵌进单文件 HTML。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Windows 控制台默认 GBK，先切到 UTF-8 避免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "assets" / "vendor"
TARGET = VENDOR_DIR / "mermaid.min.js"

DEFAULT_VERSION = "10.9.1"
MIRRORS = [
    "https://cdn.jsdelivr.net/npm/mermaid@{version}/dist/mermaid.min.js",
    "https://unpkg.com/mermaid@{version}/dist/mermaid.min.js",
    "https://registry.npmmirror.com/mermaid/{version}/files/dist/mermaid.min.js",
]

# 完整性下限：UMD 版 mermaid 10.9.1 约 3.3 MB，低于 1 MB 基本可以断定下错了产物
MIN_BYTES = 1_000_000


def _looks_like_umd_mermaid(text: str) -> bool:
    """只检查 UMD 包装头部。

    压缩后的产物在前几千字符里不会出现 "flowchart" 这类业务词，
    因此判据是：UMD 包装特征 + 把导出挂到 globalThis.mermaid 上。
    """
    head = text[:600].lower()
    has_wrapper = "exports" in head or "define" in head or "globalthis" in head
    exports_mermaid = "mermaid=(" in head.replace(" ", "") or ".mermaid=" in head.replace(" ", "")
    return has_wrapper and exports_mermaid


def check(quiet: bool = False) -> bool:
    if not TARGET.exists():
        if not quiet:
            print(f"[缺失] {TARGET}")
            print("       请运行：python tools/fetch_mermaid.py")
        return False
    size = TARGET.stat().st_size
    text_head = TARGET.read_text(encoding="utf-8", errors="replace")[:4000]
    if size < MIN_BYTES:
        if not quiet:
            print(f"[异常] {TARGET} 仅 {size} 字节，可能下载不完整，请用 --force 重新下载。")
        return False
    if not _looks_like_umd_mermaid(text_head):
        if not quiet:
            print(f"[异常] {TARGET} 看起来不是 mermaid 的 UMD 产物（可能是 ESM 版本，无法内嵌）。")
        return False
    if not quiet:
        digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()[:16]
        print(f"[就绪] {TARGET}")
        print(f"       {size / 1024:.0f} KB, sha256:{digest}…")
    return True


def fetch(version: str, force: bool) -> int:
    if TARGET.exists() and not force and check(quiet=True):
        print("[跳过] 本地已有可用的 mermaid.min.js（如需更新请加 --force）")
        return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for template in MIRRORS:
        url = template.format(version=version)
        print(f"[下载] {url}")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "doc-parser-agent/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{url} → {exc}")
            print(f"       失败：{exc}")
            continue

        if len(payload) < MIN_BYTES:
            errors.append(f"{url} → 内容过小（{len(payload)} 字节）")
            print(f"       失败：内容过小（{len(payload)} 字节）")
            continue

        text = payload.decode("utf-8", errors="replace")
        if not _looks_like_umd_mermaid(text):
            errors.append(f"{url} → 不是 UMD 产物")
            print("       失败：不是可内嵌的 UMD 产物（注意 mermaid 11.x 只有 ESM）")
            continue

        TARGET.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        print(f"[完成] 已保存到 {TARGET}")
        print(f"       {len(payload) / 1024:.0f} KB, sha256:{digest}…")
        return 0

    print("\n[失败] 所有镜像都不可用。请手动获取：")
    print(f"   1) 打开 https://cdn.jsdelivr.net/npm/mermaid@{version}/dist/mermaid.min.js")
    print(f"   2) 另存为 {TARGET}")
    print("   3) 重新运行 python tools/fetch_mermaid.py --check 验证")
    for line in errors:
        print(f"   · {line}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 Mermaid 离线库到 assets/vendor/")
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"mermaid 版本，默认 {DEFAULT_VERSION}")
    parser.add_argument("--force", action="store_true", help="已存在时强制重新下载")
    parser.add_argument("--check", action="store_true", help="只检查本地文件，不下载")
    args = parser.parse_args()

    if args.check:
        return 0 if check() else 1
    return fetch(args.version, args.force)


if __name__ == "__main__":
    sys.exit(main())
