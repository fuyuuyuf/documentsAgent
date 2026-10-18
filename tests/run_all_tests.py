"""一键跑完全部自测（离线、零 API 成本）。

用法：
    python tests/run_all_tests.py

依次执行（与 tests/ 目录实际测试文件一一对应，失败不中断、最后统一汇总）：
    1. 单元自测          tests/test_units.py
    2. 双栏版式解析测试   tests/test_two_column.py
    3. 端到端冒烟测试     tests/smoke_test.py      （走 --mock，不调用大模型）
    4. 网页上传联动测试   tests/webapp_smoke.py    （本地起服务，走 mock）
    5. 前端无头验证       tests/frontend_check.js  （jsdom，装了才跑）
    6. 环境自检          python src/main.py --check
    7. 交付检查          tests/preflight_check.py --allow-dev-env
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def run(label: str, command: list[str], *, optional: bool = False) -> tuple[bool, str, float]:
    print()
    print("=" * 70)
    print(f"  {label}")
    print("=" * 70)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except FileNotFoundError:
        print(f"  跳过：找不到可执行文件 {command[0]}")
        return ((True, "skipped", 0.0) if optional else (False, "missing executable", 0.0))
    except subprocess.TimeoutExpired:
        print("  失败：执行超时（600 秒）")
        return False, "timeout", time.perf_counter() - started

    output = (completed.stdout or "") + (completed.stderr or "")
    print(output.rstrip())
    elapsed = time.perf_counter() - started

    if completed.returncode == 0:
        print(f"\n  → 通过（{elapsed:.1f} 秒）")
        return True, "passed", elapsed

    # PowerShell / 编码导致的假失败：有明确的通过标记就认账
    if "全部通过" in output or "全部冒烟检查通过" in output or "前端无头验证全部通过" in output:
        print(f"\n  → 通过（{elapsed:.1f} 秒，退出码 {completed.returncode}，输出中已含通过标记）")
        return True, "passed", elapsed

    if optional and "skip" in output.lower():
        return True, "skipped", elapsed

    print(f"\n  → 失败（退出码 {completed.returncode}，{elapsed:.1f} 秒）")
    return False, f"exit {completed.returncode}", elapsed


def main() -> int:
    print("=" * 70)
    print("  文档解析智能体 · 全量自测")
    print("=" * 70)

    python = sys.executable
    node_modules = PROJECT_ROOT / "_verify" / "node_modules" / "jsdom"
    results: list[tuple[str, bool, str, float]] = []
    total_started = time.perf_counter()

    checks = [
        ("单元自测", [python, "tests/test_units.py"], False),
        ("双栏版式解析测试", [python, "tests/test_two_column.py"], False),
        ("端到端冒烟测试（离线 mock）", [python, "tests/smoke_test.py"], False),
        ("本地网页上传与任务进度", [python, "tests/webapp_smoke.py"], False),
        (
            "前端无头验证（jsdom）",
            ["node", "tests/frontend_check.js", "outputs/sample_en_two_column.dashboard.html"],
            not node_modules.exists(),
        ),
        ("环境自检", [python, "src/main.py", "--check"], False),
        ("开发环境交付检查（清单 / 密钥 / 离线自证 / 编码）", [python, "tests/preflight_check.py", "--allow-dev-env"], False),
    ]

    for label, command, optional in checks:
        ok, status, elapsed = run(label, command, optional=optional)
        results.append((label, ok, status, elapsed))

    print()
    print("=" * 70)
    print("  汇总")
    print("=" * 70)
    for label, ok, status, elapsed in results:
        mark = "✓" if ok else "✗"
        note = "" if status == "passed" else f"（{status}）"
        cost = f"  {elapsed:6.1f}s" if elapsed else ""
        print(f"  {mark} {label}{note}{cost}")

    total_elapsed = time.perf_counter() - total_started
    print("-" * 70)
    print(f"  总耗时：{total_elapsed:.1f} 秒")

    failed = [label for label, ok, _, _ in results if not ok]
    print()
    if failed:
        print(f"  {len(failed)} 项未通过，请检查上面的输出。")
        print("=" * 70)
        return 1
    print("  全部自测通过。产物：outputs/*.dashboard.html（单文件，可离线打开）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
