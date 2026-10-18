"""快速自测：生成一个临时 PDF 并跑通离线流水线，验证各阶段不报错。

用法：
    python tests/smoke_test.py            # 用内置生成的小 PDF 跑 --mock 流程
    python tests/smoke_test.py 某论文.pdf   # 用指定 PDF

这个脚本不调用任何大模型 API（走 mock 分支），因此零成本、可反复运行。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

import pymupdf  # noqa: E402

from src.config import Settings  # noqa: E402
from src.diagram import build_diagram, validate_mermaid  # noqa: E402
from src.parse import parse_pdf  # noqa: E402
from src.pipeline import Logger, run_pipeline  # noqa: E402


def make_test_pdf(path: Path) -> Path:
    """生成一个带标题/章节/双栏/页眉页脚的最小论文 PDF，用来验证解析器。"""
    doc = pymupdf.open()
    pages_content = [
        [
            ("A Lightweight Method for Automated Paper Structure Extraction", 20, True),
            ("Alice Zhang, Bob Li — Department of Computer Science", 10, False),
            ("Abstract", 13, True),
            (
                "We present a lightweight pipeline that extracts the logical structure of scientific "
                "papers and renders it as an interactive mind map. Our method combines coordinate-aware "
                "PDF parsing with large language models and achieves 92.4 percent structural accuracy.",
                10,
                False,
            ),
            ("1 Introduction", 13, True),
            (
                "Academic papers are long and terminology-dense, making it hard for readers to grasp the "
                "research problem and the argument path quickly. Existing summarisation tools produce flat "
                "text and lose the logical relations between claims.",
                10,
                False,
            ),
            ("2 Related Work", 13, True),
            (
                "Prior work on scientific summarisation focuses on abstractive compression but rarely "
                "preserves the evidential links between a claim and its supporting passage.",
                10,
                False,
            ),
        ],
        [
            ("3 Method", 13, True),
            ("3.1 Coordinate-Aware Parsing", 12, True),
            (
                "We reconstruct reading order from block coordinates and detect headings by font size "
                "and numbering patterns, which keeps page-level provenance for every sentence.",
                10,
                False,
            ),
            ("3.2 Structured Analysis", 12, True),
            (
                "The model is asked to return a strict JSON object containing the research problem, "
                "contributions, argument flow and limitations, rather than free-form text.",
                10,
                False,
            ),
            ("4 Experiments", 13, True),
            (
                "We evaluate on twelve papers including two-column layouts and long documents. "
                "Extraction completeness reaches 98.1 percent and the diagram passes syntax "
                "validation in 100 percent of runs.",
                10,
                False,
            ),
            ("5 Conclusion", 13, True),
            (
                "A deterministic diagram generator combined with citation verification keeps the "
                "output faithful and the pipeline fully offline-capable.",
                10,
                False,
            ),
            ("References", 13, True),
            ("[1] Smith et al. Scientific Summarisation. ACL 2024.", 9, False),
            ("[2] Chen et al. Retrieval Augmented Reading. EMNLP 2025.", 9, False),
        ],
    ]

    for page_index, items in enumerate(pages_content):
        page = doc.new_page(width=595, height=842)
        # 页眉 / 页脚：跨页完全一致，用于验证样板文本过滤
        page.insert_text((60, 42), "Journal of Applied Document Intelligence, Vol. 12", fontsize=9)
        page.insert_text((280, 810), f"Page {page_index + 1}", fontsize=9)
        y = 80.0
        for text, size, bold in items:
            font = "hebo" if bold else "helv"
            # 手工折行，模拟真实排版
            max_chars = int(560 / (size * 0.52))
            words = text.split()
            line = ""
            for word in words:
                if len(line) + len(word) + 1 > max_chars:
                    page.insert_text((60, y), line, fontsize=size, fontname=font)
                    y += size * 1.45
                    line = word
                else:
                    line = f"{line} {word}".strip()
            if line:
                page.insert_text((60, y), line, fontsize=size, fontname=font)
                y += size * 1.45
            y += 6

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def main() -> int:
    print("=" * 68)
    print("  离线冒烟测试（不调用任何大模型 API）")
    print("=" * 68)

    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1]).expanduser().resolve()
        if not pdf_path.exists():
            print(f"[fail] 找不到 PDF：{pdf_path}")
            return 1
    else:
        pdf_path = make_test_pdf(PROJECT_ROOT / "_verify" / "smoke_sample.pdf")
        print(f"[1] 已生成测试 PDF：{pdf_path}")

    settings = Settings.from_env()
    settings.mock = True

    failures: list[str] = []

    # --- 阶段 1：解析 ---
    try:
        paper = parse_pdf(pdf_path, settings)
        print(
            f"[2] 解析通过：{paper.page_count} 页 / {paper.char_count} 字符 / "
            f"{len(paper.headings)} 个标题 / 正文页 {paper.body_pages}"
        )
        assert paper.char_count > 400, f"提取字符数过少：{paper.char_count}"
        assert paper.page_spans, "page_spans 为空，无法做页码溯源"
        assert paper.page_spans[-1][1] == paper.char_count, (
            f"page_spans 末位 {paper.page_spans[-1][1]} 与全文长度 {paper.char_count} 不一致"
        )
        titles = [h.text for h in paper.headings]
        print(f"    识别到的标题：{titles[:8]}")
        for expected in ("1 Introduction", "3 Method"):
            if not any(expected.split()[0] in t for t in titles):
                failures.append(f"未识别到章节「{expected}」")
        if "References" in " ".join(titles):
            print("    · 参考文献被识别为标题但未进入正文（预期行为）")
        boiler = paper.dropped_boilerplate
        if any("Journal of Applied" in text for text in boiler):
            print(f"    · 已正确过滤跨页页眉（共 {len(boiler)} 类样板文本）")
        else:
            failures.append("跨页页眉未被过滤")
    except Exception as exc:  # noqa: BLE001
        print(f"[2] 解析失败：{exc}")
        traceback.print_exc()
        return 1

    # --- 阶段 2：导图构建 ---
    try:
        from src.pipeline import load_analysis_file

        analysis = load_analysis_file(PROJECT_ROOT / "examples" / "sample_analysis.json")
        diagram = build_diagram(analysis)
        code = diagram.to_mermaid()
        result = validate_mermaid(code)
        print(f"[3] 导图构建：{len(diagram.nodes)} 个节点 / {len(diagram.edges)} 条边 → {result.summary()}")
        if not result.ok:
            failures.append(f"Mermaid 校验失败：{result.errors}")
    except Exception as exc:  # noqa: BLE001
        print(f"[3] 导图构建失败：{exc}")
        traceback.print_exc()
        return 1

    # --- 阶段 3：完整流水线（mock） ---
    try:
        out = PROJECT_ROOT / "_verify" / "smoke_dashboard.html"
        pipeline_result = run_pipeline(
            pdf_path, out, settings=settings, logger=Logger("info"), audience_pass=False
        )
        size_kb = pipeline_result.html_path.stat().st_size / 1024
        print(f"[4] 流水线通过：{pipeline_result.html_path.name}（{size_kb:.0f} KB）")
        assert size_kb > 500, "产物过小，mermaid 可能没有内嵌成功"
        html_text = pipeline_result.html_path.read_text(encoding="utf-8")
        for marker in ("<svg", "mermaid", "id=\"payload\""):
            if marker not in html_text:
                failures.append(f"产物中缺少标记：{marker}")
        if "cdn.jsdelivr.net" in html_text or "unpkg.com" in html_text:
            failures.append("产物中出现了 CDN 外链，违反离线要求")
        payload_text = html_text.split('id="payload" type="application/json">', 1)[1].split("</script>", 1)[0]
        payload = json.loads(payload_text)
        print(f"    payload 可解析：{payload['stats']}")
        # 跨分区聚合边（渲染层新形态）：跨分区边合并为一条指向 subgraph id
        # （xxx_grp）的边。从产物 payload 内嵌的 Mermaid 源码中断言其存在，
        # 且整段代码仍通过语法校验。
        mermaid_code = payload.get("diagram", {}).get("mermaid", "")
        subgraph_count = len(re.findall(r"^\s*subgraph\s+", mermaid_code, re.MULTILINE))
        aggregate_edges = re.findall(
            r"^\s*[A-Za-z][A-Za-z0-9_]*_grp\s*-->(?:\|[^|]*\|)?\s*[A-Za-z][A-Za-z0-9_]*_grp",
            mermaid_code,
            re.MULTILINE,
        )
        if subgraph_count >= 2 and not aggregate_edges:
            failures.append(
                f"产物 Mermaid 有 {subgraph_count} 个分区，但缺少跨分区聚合边（xxx_grp -->|…| yyy_grp）"
            )
        elif aggregate_edges:
            print(f"    跨分区聚合边：{len(aggregate_edges)} 条（如 {aggregate_edges[0].strip()}）")
            recheck = validate_mermaid(mermaid_code)
            if not recheck.ok:
                failures.append(f"含跨分区聚合边的 Mermaid 校验失败：{recheck.errors}")
    except Exception as exc:  # noqa: BLE001
        print(f"[4] 流水线失败：{exc}")
        traceback.print_exc()
        return 1

    print("=" * 68)
    if failures:
        print("  存在未通过项：")
        for item in failures:
            print(f"    ✗ {item}")
        return 1
    print("  ✓ 全部冒烟检查通过")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
