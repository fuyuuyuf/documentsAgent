"""命令行入口：一键把论文 PDF 转成交互式思维导图看板。

用法示例：
    python src/main.py examples/sample_en_two_column.pdf
    python src/main.py 论文.pdf -o outputs/我的报告.html
    python src/main.py 论文.pdf --mock          # 离线演示，不调用大模型
    python src/main.py --check                  # 环境自检
    python src/main.py --web                    # 启动网页上传界面
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

# --- 让 `python src/main.py` 与 `python -m src.main` 都能正确导入同目录模块 ---
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 控制台编码兜底与 TTY 检测只保留 bootstrap.py 一份实现，这里直接复用
from src.bootstrap import _force_utf8_stdout, _stdin_is_tty  # noqa: E402
from src.config import PROJECT_ROOT, Settings  # noqa: E402

VERSION = "1.1.0"

BANNER = r"""
  文档解析智能体 · 论文阅读助手
  PDF  →  文本提取  →  大模型结构分析  →  Mermaid 导图  →  交互式 HTML 看板
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="把一篇论文 PDF 一键转成交互式思维导图看板（本地运行，产物可离线打开）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python src/main.py examples/sample_en_two_column.pdf\n"
            "  python src/main.py 论文.pdf -o outputs/报告.html --keep-debug\n"
            "  python src/main.py 论文.pdf --mock\n"
            "  python src/main.py --check\n"
            "  python src/main.py --web\n"
        ),
    )
    parser.add_argument("pdf", nargs="?", help="输入论文 PDF 路径")
    parser.add_argument("-o", "--output", help="输出 HTML 路径（默认 outputs/<论文名>.dashboard.html）")

    group = parser.add_argument_group("运行模式")
    group.add_argument("--mock", action="store_true", help="离线演示模式：不调用大模型 API，使用本地样例分析 JSON")
    group.add_argument("--mock-file", help="离线模式使用的分析 JSON 路径")
    group.add_argument("--check", action="store_true", help="环境自检：依赖 / 离线资源 / API 配置")
    group.add_argument("--web", action="store_true", help="启动网页上传界面（需要本机可用端口）")
    group.add_argument("--port", type=int, default=8000, help="--web 的监听端口，默认 8000")
    group.add_argument("--host", default="127.0.0.1", help="--web 的监听地址，默认 127.0.0.1")

    group = parser.add_argument_group("调试与复现")
    group.add_argument("--keep-debug", action="store_true", help="额外导出中间产物（解析结果 / 分析 JSON / .mmd）")
    group.add_argument("--render-from", help="跳过大模型，直接用已有的分析 JSON 重新渲染看板")
    group.add_argument("--no-audience", action="store_true", help="跳过「通俗解读」第二轮大模型调用")
    group.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    group.add_argument("-q", "--quiet", action="store_true", help="只输出最终结果路径")
    group.add_argument("--version", action="version", version=f"文档解析智能体 {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)

    from src.bootstrap import check_environment, ensure_ready

    # ---------------- 自检 ----------------
    if args.check:
        print(BANNER)
        ok, lines = check_environment()
        print("环境自检结果：")
        for line in lines:
            print(f"  {line}")
        print()
        if ok:
            print("✓ 基础环境就绪，可以运行：python src/main.py examples/sample_en_two_column.pdf")
            return 0
        print("✗ 存在问题，请按上面的提示修复后再运行。")
        return 1

    # ---------------- 首次启动依赖检测 ----------------
    # 缺依赖时交互式询问并自动安装；stdin 不是终端（脚本 / CI）时退化为提示。
    # 环境就绪时只做几次轻量检查，秒级通过。
    if not ensure_ready(auto=not _stdin_is_tty(), verbose=not args.quiet):
        return 1

    # 依赖就绪后才能导入这些模块（pipeline → parse → pymupdf）
    from src.llm import LLMError  # noqa: E402
    from src.pipeline import (  # noqa: E402
        Logger,
        render_from_analysis,
        run_pipeline,
    )
    from src.render import RenderError  # noqa: E402

    # ---------------- 网页界面 ----------------
    if args.web:
        try:
            from src.webapp import serve
        except ImportError as exc:
            print(f"[fail] 无法加载网页界面模块：{exc}", file=sys.stderr)
            return 1
        print(BANNER)
        return serve(host=args.host, port=args.port)

    # ---------------- 参数检查 ----------------
    if not args.pdf:
        print(BANNER)
        build_parser().print_help()
        print("\n提示：先跑一次离线演示验证环境 → python src/main.py examples/sample_en_two_column.pdf --mock")
        return 2

    pdf_path = Path(args.pdf).expanduser()
    if not pdf_path.exists():
        print(f"[fail] 找不到文件：{pdf_path}", file=sys.stderr)
        return 1
    if pdf_path.suffix.lower() != ".pdf":
        print(f"[fail] 输入必须是 PDF 文件，当前为：{pdf_path.name}", file=sys.stderr)
        return 1

    # ---------------- 配置 ----------------
    settings = Settings.from_env()
    settings.verbose = args.verbose or settings.verbose
    settings.mock = args.mock or settings.mock
    if args.mock_file:
        settings.mock_file = args.mock_file
    if args.keep_debug:
        settings.keep_debug = True
    if args.output:
        settings.output_html = args.output

    log_level = "debug" if args.verbose else ("error" if args.quiet else "info")
    logger = Logger(log_level)

    if not args.quiet:
        print(BANNER)

    for message in settings.warnings:
        logger.warn(message)

    # ---------------- 执行 ----------------
    try:
        if args.render_from:
            result = render_from_analysis(
                args.render_from,
                pdf_path,
                args.output or (PROJECT_ROOT / "outputs" / f"{pdf_path.stem}.dashboard.html"),
                settings=settings,
                logger=logger,
            )
        else:
            if not settings.mock:
                problems = settings.validate()
                if problems:
                    print("\n[fail] 缺少大模型配置，无法进行真实分析：", file=sys.stderr)
                    for problem in problems:
                        print(f"       · {problem}", file=sys.stderr)
                    print(
                        "\n解决办法（任选其一）：\n"
                        "  1) 复制 .env.example 为 .env，填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL；\n"
                        "  2) 先跑离线演示：python src/main.py "
                        f"{args.pdf} --mock\n",
                        file=sys.stderr,
                    )
                    return 1
            result = run_pipeline(
                pdf_path,
                args.output,
                settings=settings,
                logger=logger,
                audience_pass=not args.no_audience,
            )
    except LLMError as exc:
        logger.error(str(exc))
        print(
            "\n提示：若暂时没有可用的 API Key，可先用离线演示模式验证工具本身：\n"
            f"      python src/main.py {args.pdf} --mock\n",
            file=sys.stderr,
        )
        return 1
    except RenderError as exc:
        logger.error(str(exc))
        return 1
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1
    except KeyboardInterrupt:
        print("\n[中断] 用户取消了运行。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，给出可操作的报错
        logger.error(f"未预期的异常：{type(exc).__name__}: {exc}")
        if args.verbose:
            traceback.print_exc()
        else:
            print("      加 -v 参数可查看完整堆栈。", file=sys.stderr)
        return 1
    finally:
        logger.close_log()      # 幂等：成功路径已在 run_pipeline 内写好结束行

    # ---------------- 汇总 ----------------
    if args.quiet:
        print(result.html_path)
        return 0

    stats = result.payload.get("stats", {})
    report_summary = getattr(result.report, "summary", None)
    print()
    print("─" * 68)
    print("  运行完成")
    print("─" * 68)
    print(f"  输入论文    : {result.paper.source}")
    print(f"  页数 / 字符 : {result.paper.page_count} 页 / {result.paper.char_count} 字符")
    print(f"  导图节点    : {stats.get('nodeCount', 0)} 个（{stats.get('sectionCount', 0)} 个分区）")
    print(
        f"  证据溯源    : {stats.get('verifiedCount', 0)}/{stats.get('evidenceCount', 0)} 条已定位"
        + (f"，{stats.get('pageFixedCount', 0)} 条页码已纠正" if stats.get("pageFixedCount") else "")
        + (f"，{stats.get('mismatchCount', 0)} 条页码不符" if stats.get("mismatchCount") else "")
    )
    if callable(report_summary):
        print(f"  校验摘要    : {report_summary()}")
    print(f"  Mermaid     : {result.validation.summary()}")
    print(f"  输出看板    : {result.html_path}")
    print(f"  文件大小    : {result.html_path.stat().st_size / 1024:.0f} KB（单文件，含离线 Mermaid，可直接双击打开）")
    if result.debug_files:
        print(f"  调试产物    : {result.debug_files[0].parent}")
    if result.warnings:
        print("  注意事项    :")
        for message in result.warnings:
            print(f"      · {message}")
    print(f"  总耗时      : {result.seconds:.1f} 秒")
    print("─" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
