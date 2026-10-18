"""提交前完整性检查：密钥泄漏、产物离线自证、产物结构抽查、必需文件齐全。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

parser = argparse.ArgumentParser(description="检查提交包所需文件与密钥安全")
parser.add_argument("--allow-dev-env", action="store_true", help="开发环境允许本地 .env，仍要求被忽略")
arguments = parser.parse_args()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TEXT_EXT = {".py", ".md", ".json", ".txt", ".html", ".js", ".ps1", ".example", ".yml", ".yaml"}
SKIP_DIRS = {"_verify", "node_modules", "__pycache__", ".git", ".venv", "venv", "schedule-app"}
# 示例产物内联了 3.2 MB 的 mermaid 压缩代码，里面会出现形如 sk-xxx 的变量名，需排除
SKIP_FILES = {
    "sample_en_two_column.dashboard.html",
    "sample_zh_single_column.dashboard.html",
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{'' if ok or not detail else ' — ' + detail}")
    if not ok:
        failures.append(name)


print("=" * 70)
print("  提交前完整性检查")
print("=" * 70)

# ---------------------------------------------------------------- 1. 密钥
print("\n[1] 密钥安全")
leaks: list[str] = []
for path in PROJECT_ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
        continue
    if any(part in SKIP_DIRS for part in path.parts) or path.name in SKIP_FILES:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if re.search(r"sk-[A-Za-z0-9_\-]{20,}", text):
        leaks.append(str(path.relative_to(PROJECT_ROOT)))
check("源码/文档中无真实密钥", not leaks, ", ".join(leaks))
if arguments.allow_dev_env:
    ignored = ".env" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    check("开发用 .env 已由 .gitignore 排除", ignored)
else:
    check("提交包中不含 .env", not (PROJECT_ROOT / ".env").exists())
check(".env.example 存在", (PROJECT_ROOT / ".env.example").exists())

# ---------------------------------------------------------------- 2. 必需文件
print("\n[2] 比赛要求的提交清单")
required = {
    "src/ 源代码目录": "src",
    "README.md": "README.md",
    "requirements.txt": "requirements.txt",
    ".env.example": ".env.example",
    "examples/ 示例输入": "examples",
    "outputs/ 示例产物": "outputs",
}
for label, rel in required.items():
    check(label, (PROJECT_ROOT / rel).exists())

examples_pdf = sorted((PROJECT_ROOT / "examples").glob("*.pdf"))
check(f"examples/ 含示例 PDF（{len(examples_pdf)} 个）", len(examples_pdf) >= 1)
outputs_html = sorted((PROJECT_ROOT / "outputs").glob("*.html"))
check(f"outputs/ 含示例看板（{len(outputs_html)} 份）", len(outputs_html) >= 1)
check("离线渲染库已随包提供", (PROJECT_ROOT / "assets" / "vendor" / "mermaid.min.js").exists())

# ---------------------------------------------------------------- 3. README 覆盖
print("\n[3] README 必需章节（评分项 6）")
readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
need = [
    "项目功能简介",
    "环境依赖",
    "安装方式",
    "API Key 配置方式",
    "运行命令",
    "输入 PDF 方式",
    "输出 HTML 位置",
    "常见错误处理方式",
]
missing = [n for n in need if n not in readme]
check("README 覆盖全部 8 项必需内容", not missing, ", ".join(missing))
check("README 含评审维度映射", "评审维度" in readme and "一键跑通" in readme)
check("README 注明参赛身份页脚", "南京邮电大学 2026 级新生赛" in readme)

# ---------------------------------------------------------------- 4. 产物离线自证
print("\n[4] 产物离线自证（评分项 3）")
for name in ("sample_en_two_column", "sample_zh_single_column"):
    html_path = PROJECT_ROOT / "outputs" / f"{name}.dashboard.html"
    if not html_path.exists():
        check(f"{name} 产物存在", False)
        continue
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    external = re.findall(r'(?:src|href)\s*=\s*["\']https?://', html, re.IGNORECASE)
    check(f"{name}：无外部 src/href 引用", not external, str(external[:3]))
    check(f"{name}：mermaid 已内联", len(html) > 2_000_000)
    check(f"{name}：无 CDN 域名", not re.search(r"cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare", html))
    check(f"{name}：含 payload 数据块", 'id="payload"' in html)

# ---------------------------------------------------------------- 5. 编码
print("\n[5] 文本文件编码")
for rel in ("README.md", "requirements.txt", ".env.example", "docs/常见错误处理.md", "outputs/README.md"):
    path = PROJECT_ROOT / rel
    if not path.exists():
        check(f"{rel} 存在", False)
        continue
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        check(f"{rel} 是合法 UTF-8", False, str(exc))
        continue
    garbage = sum(1 for c in text if "\u3400" <= c <= "\u4dbf" or "\ue000" <= c <= "\uf8ff" or c == "\ufffd")
    check(f"{rel} 编码正常（无乱码）", garbage == 0, f"{garbage} 个异常字符")

# ---------------------------------------------------------------- 6. 产物结构抽查
print("\n[6] 产物结构抽查（新版 UI 骨架）")
# 任取一份 sample 产物（取修改时间最新的一份，代表当前渲染能力），断言
# 新 UI 的四个骨架标记齐全：顶栏 / 角落工具坞 / 节点 tooltip / 侧栏开关。
STRUCTURE_MARKERS = {
    ".topbar 顶栏": 'class="topbar"',
    ".corner-dock 角落工具坞": 'class="corner-dock"',
    "#nodeTip 节点悬停提示": 'id="nodeTip"',
    "#btnToggleSide 侧栏开关": 'id="btnToggleSide"',
}
samples = sorted((PROJECT_ROOT / "outputs").glob("sample_*.dashboard.html"))
if not samples:
    check(
        "outputs/ 含 sample_*.dashboard.html 产物",
        True,
        "跳过：outputs/ 下暂无 sample_* 产物，结构抽查未执行（可用 --mock 重新生成后再跑）",
    )
else:
    newest = max(samples, key=lambda p: p.stat().st_mtime)
    structure_html = newest.read_text(encoding="utf-8", errors="ignore")
    missing = [name for name, marker in STRUCTURE_MARKERS.items() if marker not in structure_html]
    check(
        f"产物结构抽查：{newest.name} 含新版 UI 骨架（{len(samples)} 份 sample 中取最新）",
        not missing,
        "缺少 " + ", ".join(missing) if missing else "",
    )

# ---------------------------------------------------------------- 汇总
print()
print("=" * 70)
if failures:
    print(f"  {len(failures)} 项未通过：")
    for item in failures:
        print(f"    ✗ {item}")
    print("=" * 70)
    raise SystemExit(1)
print("  ✓ 提交前检查全部通过")
print("=" * 70)
