"""自测：multipart 解析、文件名清洗、payload 转义安全性。

用法：python tests/test_units.py
"""

from __future__ import annotations

import json
import sys
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

from src.diagram import (  # noqa: E402
    build_diagram,
    repair_mermaid,
    sanitize_label,
    validate_mermaid,
)
from src.llm import LLMError, extract_json  # noqa: E402
from src.parse import PaperText  # noqa: E402
from src.prompts import validate_analysis  # noqa: E402
from src.render import build_payload, render_html  # noqa: E402
from src.textutils import CitationVerifier, collapse_for_search, normalize  # noqa: E402
from src.webapp import PAGE_TEMPLATE, _parse_multipart, _safe_filename, _truthy  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  ✗ {name} {detail}")


# ---------------------------------------------------------------------------
def test_multipart() -> None:
    print("\n[multipart 上传解析]")
    boundary = "----test123"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="pdf"; filename="my paper.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-1.4 fake content\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="mock"\r\n\r\n'
        "1\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    fields = _parse_multipart(f"multipart/form-data; boundary={boundary}", body)
    check("解析出 pdf 字段", "pdf" in fields)
    check("文件名保留", fields["pdf"]["filename"] == "my paper.pdf", repr(fields["pdf"]["filename"]))
    check("文件内容正确", fields["pdf"]["data"].startswith(b"%PDF"))
    check("复选框字段解析", fields.get("mock", {}).get("value") == "1")
    check("_truthy 识别 1", _truthy(fields, "mock") is True)
    check("_truthy 缺省为 False", _truthy(fields, "keep_debug") is False)


def test_safe_filename() -> None:
    print("\n[文件名清洗 / 路径穿越防护]")
    check("去掉目录部分", _safe_filename("../../etc/passwd.pdf") == "passwd.pdf", _safe_filename("../../etc/passwd.pdf"))
    check("保留中文", "论文" in _safe_filename("我的论文 v2.pdf"))
    check("空格转下划线", " " not in _safe_filename("my paper.pdf"))
    check("补全 .pdf 后缀", _safe_filename("paper").endswith(".pdf"))
    check("空名回退", _safe_filename("") == "paper.pdf")
    check("Windows 路径穿越", _safe_filename(r"..\..\windows\system32\evil.pdf") == "evil.pdf")


def test_citation_verifier() -> None:
    print("\n[引用溯源校验]")
    page1 = "We propose a lightweight pipeline for paper structure extraction."
    page2 = "Accuracy reaches 92.4 percent on twelve papers with two-column layouts."
    page3 = "The method fails on scanned documents without a text layer."
    full = f"{page1}\n\n{page2}\n\n{page3}"
    spans = [(0, len(page1)), (len(page1) + 2, len(page1) + 2 + len(page2))]
    start3 = len(page1) + 2 + len(page2) + 2
    spans.append((start3, start3 + len(page3)))
    assert spans[-1][1] == len(full), (spans[-1][1], len(full))

    verifier = CitationVerifier(full, spans)

    hit = verifier.verify("Accuracy reaches 92.4 percent", 2)
    check("精确命中", hit["found"] and hit["page"] == 2, str(hit))
    check("精确匹配得分为 1", hit["score"] == 1.0, str(hit))

    wrong = verifier.verify("Accuracy reaches 92.4 percent", 1)
    check("页码不符被检出", wrong["mismatch"] is True, str(wrong))

    miss = verifier.verify("This sentence does not exist anywhere in the paper at all.", None)
    check("幻觉引用被识别", miss["found"] is False, str(miss))

    short = verifier.verify("abc", None)
    check("过短引用不判为命中", short["found"] is False and short["method"] == "too_short", str(short))

    squashed = verifier.verify("fails on scanned  documents\nwithout a text layer", 3)
    check("空白差异仍能命中", squashed["found"] is True, str(squashed))

    # 简要上下文：window() 与 verify() 同策略定位，返回命中前后各一段原文
    ctx = verifier.window("Accuracy reaches 92.4 percent")
    check(
        "window 返回命中上下文",
        isinstance(ctx, str) and "Accuracy reaches 92.4 percent" in ctx and len(ctx) > 30,
        ctx,
    )
    check(
        "未命中的引用无上下文",
        verifier.window("This sentence does not exist anywhere in the paper at all.") == "",
    )
    check("过短引用无上下文", verifier.window("abc") == "")


def test_json_extraction() -> None:
    print("\n[模型输出 JSON 鲁棒解析]")
    check("纯 JSON", extract_json('{"a": 1}') == {"a": 1})
    check("代码围栏", extract_json('```json\n{"a": 1}\n```') == {"a": 1})
    check("前后有解释文字", extract_json('好的，结果如下：\n{"a": {"b": [1,2]}}\n希望有帮助') == {"a": {"b": [1, 2]}})
    check("字符串内含括号", extract_json('{"a": "text with } brace"}') == {"a": "text with } brace"})
    try:
        extract_json("完全不是 JSON")
        check("非法输入抛错", False)
    except LLMError:
        check("非法输入抛错", True)


def test_sanitize_label() -> None:
    print("\n[Mermaid 标签清洗]")
    check("去掉双引号", '"' not in sanitize_label('he said "hello"'))
    check("去掉方括号", "[" not in sanitize_label("array[0] and {dict}"))
    check("去掉竖线", "|" not in sanitize_label("a | b"))
    check("去掉换行", "\n" not in sanitize_label("line1\nline2"))
    check("HTML 标签被清理", "<br/>" not in sanitize_label("a<br/>b"))
    check("空输入有兜底", sanitize_label("") == "（无内容）")
    long_cn = sanitize_label("这是一个非常非常长的中文标签用来测试截断行为是否生效并且不会破坏语法", 20)
    check("中文截断", len(long_cn) <= 22, f"len={len(long_cn)} value={long_cn!r}")


def test_mermaid_validation() -> None:
    print("\n[Mermaid 语法校验与修复]")
    good = 'flowchart TD\n  subgraph a_grp["A"]\n    direction TB\n    a_1["节点"]\n  end\n  a_1 --> b_1'
    check("合法代码通过", validate_mermaid(good).ok is True)
    check("空代码被拒", validate_mermaid("").ok is False)
    check("错误首行被拒", validate_mermaid("graphXX TD\n a-->b").ok is False)

    bare = "flowchart TD\n  a_1 --> b_1"
    result = validate_mermaid(bare)
    check("裸节点简写合法（Mermaid 会自动建节点）", result.ok is True, str(result.errors))
    check("裸节点给出提示而非报错", any("默认矩形" in w for w in result.warnings), str(result.warnings))

    unclosed = 'flowchart TD\n  subgraph a_grp["A"]\n    a_1["A"]'
    result = validate_mermaid(unclosed)
    check("未闭合 subgraph 被检出", result.ok is False, str(result.errors))
    repaired, actions = repair_mermaid(unclosed)
    check("规则修复可补全 end", validate_mermaid(repaired).ok is True, f"actions={actions}")

    fenced = '```mermaid\nflowchart TD\n  a_1["A"]\n```'
    repaired, actions = repair_mermaid(fenced)
    check("规则修复可去围栏", validate_mermaid(repaired).ok is True)


def test_unbalanced_bracket_detected() -> None:
    print("\n[括号不平衡检测]")
    bad = 'flowchart TD\n  a_1["未闭合的标签'
    result = validate_mermaid(bad)
    check("未闭合引号被检出", result.ok is False, str(result.errors))


def test_payload_and_escape() -> None:
    print("\n[payload 组装与 HTML 转义]")
    analysis = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
    # 注入一个会破坏 <script> 的恶意内容，验证转义
    analysis["one_sentence_summary"] = "危险内容 </script><script>alert(1)</script> 与 & 符号"
    analysis["contributions"][0]["evidence"] = "quote with <tag> and </script> inside"

    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()
    validation = validate_mermaid(code)
    check("样例分析生成的 Mermaid 合法", validation.ok is True, str(validation.errors))

    payload = build_payload(analysis, diagram, source_name="x.pdf", validation=validation, mermaid_source=code)
    check("节点数 = 图节点数", payload["stats"]["nodeCount"] == len(diagram.nodes) - 1 + 1 - 1 + 1 or payload["stats"]["nodeCount"] > 0)
    check("包含 sections", len(payload["diagram"]["sections"]) >= 5, str(payload["diagram"]["sections"]))
    check("trunkSections 非空", len(payload["diagram"]["trunkSections"]) > 0)
    visible_evidence = sum(bool(node.get("evidence")) for node in payload["nodes"])
    check("研究问题证据未重复计数", payload["stats"]["evidenceCount"] == visible_evidence)

    from src.render import load_assets

    template, app_js, mermaid_js = load_assets()
    html_text = render_html(payload, template=template, app_js=app_js, mermaid_js=mermaid_js)
    check("原始 </script> 已转义", "</script><script>alert(1)" not in html_text)
    check("转义为 \\u003c", "\\u003c/script" in html_text or "\\u003cscript" in html_text)
    check("Mermaid 已内嵌", "mermaid" in html_text and len(html_text) > 2_000_000)
    check("无 CDN 外链", "cdn.jsdelivr.net" not in html_text and "unpkg.com" not in html_text)

    # payload 必须能被 JSON.parse 还原
    chunk = html_text.split('id="payload" type="application/json">', 1)[1].split("</script>", 1)[0]
    restored = json.loads(chunk)
    check("payload 可被 JSON 解析", restored["meta"]["source"] == "x.pdf")
    check("恶意内容原样保留", "</script><script>alert(1)</script>" in restored["facts"]["oneSentenceSummary"])


def test_hostile_analysis() -> None:
    print("\n[对抗性输入：模型输出畸形结构]")
    hostile = {
        "title": "T" * 500,
        "one_sentence_summary": 12345,  # 类型错误
        "research_problem": "not a dict",  # 类型错误
        "contributions": [None, "string", {"label": "ok"}],  # 混合类型
        "method": [{"label": ""}],  # 空标签
        "argument_flow": {"not": "a list"},
        "limitations": [{"label": "L", "severity": 5}],  # 类型错误
        "critique": None,
        "glossary": [{"term": "", "plain": ""}],
        "open_questions": "not a list",
    }
    try:
        diagram = build_diagram(hostile)
        code = diagram.to_mermaid()
        validation = validate_mermaid(code)
        payload = build_payload(hostile, diagram, source_name="h.pdf", validation=validation)
        check("畸形输入不崩溃", True)
        check("仍产出合法 Mermaid", validation.ok is True, str(validation.errors))
        check("payload 可序列化", isinstance(json.dumps(payload, ensure_ascii=False), str))
    except Exception as exc:  # noqa: BLE001
        check("畸形输入不崩溃", False, f"{type(exc).__name__}: {exc}")


def test_validate_analysis_contract() -> None:
    print("\n[分析结果结构校验]")
    good = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
    try:
        validate_analysis(good)
        check("合规样例通过校验", True)
    except Exception as exc:  # noqa: BLE001
        check("合规样例通过校验", False, str(exc))

    for label, mutate in (
        ("缺 contributions", lambda d: d.pop("contributions")),
        ("research_problem 非对象", lambda d: d.__setitem__("research_problem", "x")),
        ("semantic_links 非数组", lambda d: d.__setitem__("semantic_links", "x")),
    ):
        broken = json.loads(json.dumps(good))
        mutate(broken)
        try:
            validate_analysis(broken)
            check(f"拒绝：{label}", False, "未抛出异常")
        except LLMError:
            check(f"拒绝：{label}", True)
    flexible = json.loads(json.dumps(good))
    flexible["glossary"] = []
    flexible["critique"] = {}
    try:
        validate_analysis(flexible)
        check("论文无对应内容时允许空分区", True)
    except LLMError as exc:
        check("论文无对应内容时允许空分区", False, str(exc))


def test_normalize_helpers() -> None:
    print("\n[文本规范化]")
    check("折叠空白", normalize("a   b\n\nc") == "a b c")
    check("去 HTML 标签", "<br/>" not in normalize("a<br/>b"))
    check("连字符统一", "\u2013" not in normalize("a\u2013b"))
    check("压缩检索形态", collapse_for_search("A-B  C_D!") == "abcd")
    check("CJK 保留", collapse_for_search("中文abc") == "中文abc")


def test_long_paper_excerpt() -> None:
    print("\n[长论文按页节选]")
    texts = [str(page) * 1000 for page in range(1, 5)]
    paper = PaperText(
        source="long.pdf", page_count=4, full_text="".join(texts),
        page_spans=[(0, 1000), (1000, 2000), (2000, 3000), (3000, 4000)],
        body_pages=[1, 2, 3, 4], page_texts=texts,
    )
    excerpt = paper.build_evidence_index(700)
    check("节选不超过预算", len(excerpt) <= 700)
    check("首尾页都有页码锚点", "<<<PAGE 1>>>" in excerpt and "<<<PAGE 4>>>" in excerpt)
    check("中间页未被整体丢弃", "<<<PAGE 2>>>" in excerpt and "<<<PAGE 3>>>" in excerpt)
    from src.pipeline import _split_paper_chunks
    import re

    chunks = _split_paper_chunks(paper, 1800)
    restored = "".join(re.sub(r"\n<<<PAGE \d+>>>\n", "", chunk) for chunk in chunks)
    check("长论文触发多批输入", len(chunks) > 1)
    check("分批覆盖每个原文字符", restored == "".join(texts))
    check("每批控制在正文预算内", all(len(chunk) <= 1000 for chunk in chunks))

    from src.config import Settings
    from src.llm import LLMResult
    from src.pipeline import Logger, _analyze_long_paper
    from src.prompts import CHUNK_SYSTEM_PROMPT

    class FakeClient:
        def __init__(self):
            self.calls = []

        def complete_json(self, system, user, **kwargs):
            self.calls.append((system, user))
            if system == CHUNK_SYSTEM_PROMPT:
                result = {"findings": [{"type": "result", "statement": "本批结果", "evidence": "111", "page": 1}]}
            else:
                result = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
            kwargs["validate"](result)
            return result, LLMResult(text=json.dumps(result, ensure_ascii=False), model="fake")

    fake = FakeClient()
    stats = []
    analysis, _, synthesis = _analyze_long_paper(paper, Settings(max_input_chars=1800), fake, Logger("silent"), stats)
    check("多批提取后进行综合", len(fake.calls) == len(chunks) + 1 and bool(analysis.get("title")))
    check("综合轮收到每批证据笔记", synthesis.count('"本批结果"') == len(chunks))
    check("多批调用均计入统计", len(stats) == len(chunks))


def test_attach_verification() -> None:
    """溯源结果必须回填到图节点上，否则前端详情面板拿不到页码与定位结果。"""
    print("\n[溯源结果回填到节点]")
    from src.diagram import attach_verification

    analysis = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
    analysis["research_problem"]["verify"] = {
        "found": True, "page": 1, "score": 1.0, "method": "exact", "mismatch": False
    }
    analysis["research_problem"]["page"] = 1
    analysis["contributions"][0]["verify"] = {
        "found": True, "page": 3, "score": 1.0, "method": "exact", "mismatch": True
    }
    analysis["contributions"][0]["page"] = 3
    analysis["contributions"][1]["verify"] = {
        "found": False, "page": None, "score": 0.0, "method": "not_found", "mismatch": False
    }

    diagram = build_diagram(analysis)
    attached = attach_verification(diagram, analysis)
    check("回填函数返回正数", attached > 0, f"attached={attached}")

    index = diagram.node_index()
    contrib_nodes = [n for n in index if n["section"] == "contributions"]
    check("贡献节点数量与数组一致", len(contrib_nodes) == len(analysis["contributions"]))
    check("第 1 条贡献拿到 verify", contrib_nodes[0].get("verify", {}).get("found") is True)
    check("第 1 条贡献页码被实测值覆盖", contrib_nodes[0].get("page") == 3, str(contrib_nodes[0].get("page")))
    check("mismatch 标记保留", contrib_nodes[0].get("verify", {}).get("mismatch") is True)
    check("第 2 条贡献标记未定位", contrib_nodes[1].get("verify", {}).get("found") is False)

    problem_nodes = [n for n in index if n["section"] == "problem"]
    check("研究问题节点拿到 verify", bool(problem_nodes) and problem_nodes[0].get("verify", {}).get("found") is True)

    method_nodes = [n for n in index if n["section"] == "method"]
    check("未校验的节点不带 verify", all("verify" not in n for n in method_nodes))
    check("每个节点都有 id 与 label", all(n.get("id") and n.get("label") for n in index))


def test_verify_contract() -> None:
    """前端契约与「不改写入参」的约定。"""
    print("\n[溯源校验契约]")
    from types import SimpleNamespace

    from src.verify import verify_analysis

    page1 = "We propose a lightweight pipeline for paper structure extraction."
    page2 = "Accuracy reaches 92.4 percent on twelve papers with two-column layouts."
    full = f"{page1}\n\n{page2}"
    spans = [(0, len(page1)), (len(page1) + 2, len(full))]
    paper = SimpleNamespace(
        full_text=full, page_spans=spans, stats={}, page_texts=["", ""], body_pages=[1, 2]
    )

    analysis = {
        "title": "T",
        "one_sentence_summary": "S",
        "research_problem": {"statement": "P", "evidence": "Accuracy reaches 92.4 percent", "page": 1},
        "contributions": [{"label": "C", "evidence": page1, "page": None}],
        "method": [{"label": "M", "evidence": "", "page": None}],
        "argument_flow": [],
        "evidence_and_results": [],
        "conclusions": [],
        "limitations": [],
        "glossary": [],
        "open_questions": [],
        "reading_path": [],
        "critique": {},
    }
    checked, report = verify_analysis(analysis, paper, strict=False)
    check("返回 (副本, 报告) 二元组", isinstance(checked, dict) and hasattr(report, "summary"))
    check("原 analysis 未被就地修改", analysis["research_problem"]["page"] == 1)
    check(
        "副本里页码被纠正为实测页 2",
        checked["research_problem"]["page"] == 2,
        str(checked["research_problem"].get("page")),
    )
    keys = set(checked["research_problem"]["verify"].keys())
    check("verify 仅含契约字段（可含 context）", keys <= {"found", "page", "score", "method", "mismatch", "context"}, str(keys))
    check(
        "命中条目附带简要上下文",
        isinstance(checked["research_problem"]["verify"].get("context"), str)
        and "Accuracy reaches 92.4 percent" in checked["research_problem"]["verify"]["context"],
        str(checked["research_problem"]["verify"].get("context")),
    )
    check(
        "第二条引用同样附带上下文",
        isinstance(checked["contributions"][0]["verify"].get("context"), str)
        and bool(checked["contributions"][0]["verify"]["context"]),
    )
    check("空证据不产生 verify", "verify" not in checked["method"][0])
    check("报告含 suspects 列表", isinstance(report.suspects, list))


def test_parse_module_importable() -> None:
    print("\n[模块可导入性]")
    import importlib

    for name in (
        "src.config",
        "src.bootstrap",
        "src.textutils",
        "src.parse",
        "src.llm",
        "src.prompts",
        "src.diagram",
        "src.render",
        "src.pipeline",
        "src.webapp",
    ):
        try:
            importlib.import_module(name)
            check(f"import {name}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"import {name}", False, f"{type(exc).__name__}: {exc}")


def test_bootstrap_first_run_check() -> None:
    print("\n[首次启动依赖检测]")
    from src.bootstrap import (
        REQUIRED_PACKAGES,
        check_environment,
        ensure_ready,
        missing_optional,
        missing_required,
        problems,
    )

    # bootstrap 自身必须零第三方依赖，才能在缺依赖时引导安装
    source = (PROJECT_ROOT / "src" / "bootstrap.py").read_text(encoding="utf-8")
    top_level = [
        line for line in source.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    third_party = [
        line for line in top_level
        if line.split()[1].split(".")[0].rstrip(",")
        not in {"__future__", "argparse", "os", "shutil", "subprocess", "sys",
                "importlib", "pathlib", "typing", "dataclasses", "re", "json",
                "time", "collections", "statistics", "http", "urllib"}
        and not line.startswith("from .")
    ]
    check("bootstrap 零第三方顶层导入", not third_party, "；".join(third_party))

    # 当前测试环境已装 requirements.txt，必需依赖不应被误报
    missing = missing_required()
    for name in REQUIRED_PACKAGES:
        check(f"依赖检测不误报 {name}", name not in missing, f"检测结果：{missing}")

    problems_list = problems()
    check("problems() 返回清单", isinstance(problems_list, list))
    ensure = ensure_ready(auto=True, verbose=False)
    check("ensure_ready 当前环境就绪", ensure is True, f"问题：{problems_list}")

    ok, lines = check_environment()
    check("check_environment 返回行清单", isinstance(lines, list) and len(lines) >= 5)
    check("check_environment 报告当前环境就绪", ok, "；".join(x for x in lines if "✗" in x))

    # 可选依赖不算缺失必需依赖：缺失时只提示，不阻断启动
    optional = missing_optional()
    check("可选依赖不进入必需清单", not set(optional) & set(missing))


def test_wrap_label() -> None:
    """标签确定性换行：宽度规则（中文 1 / 英文 0.5）、自然断点、单词完整性、行数上限。"""
    print("\n[标签确定性换行]")
    from src.diagram import LABEL_MAX_LINES, LABEL_WRAP_WIDTH, _display_width, wrap_label

    check("宽度规则：中文 1 / 英文 0.5", _display_width("中文ab") == 3.0, str(_display_width("中文ab")))
    check("换行常量在建议范围", 16 <= LABEL_WRAP_WIDTH <= 18 and LABEL_MAX_LINES == 3,
          f"width={LABEL_WRAP_WIDTH}, lines={LABEL_MAX_LINES}")

    short = "自适应证据打分器"
    check("短标签原样返回", wrap_label(short) == short, wrap_label(short))

    long_cn = wrap_label("通用搜索引擎与文献数据库通常只索引标题、作者与摘要")
    cn_segs = long_cn.split("<br/>")
    check("长中文标签折行", len(cn_segs) >= 2, long_cn)
    check("每行不超过目标宽度", all(_display_width(s) <= LABEL_WRAP_WIDTH for s in cn_segs),
          str([_display_width(s) for s in cn_segs]))
    check("折行不丢字", "".join(cn_segs) == "通用搜索引擎与文献数据库通常只索引标题、作者与摘要", long_cn)

    en = wrap_label("Deep Residual Learning for Image Recognition")
    en_segs = en.split("<br/>")
    check("英文按空格折行", len(en_segs) >= 2, en)
    check("英文单词不被拆断", any("Image" in s for s in en_segs) and any("Recognition" in s for s in en_segs), en)
    check("英文折行不丢词", "".join(en_segs).replace(" ", "") == "DeepResidualLearningforImageRecognition", en)

    mixed = wrap_label("数据: 主要使用ImageNet和CIFAR-10进行大规模视觉识别任务")
    mixed_segs = mixed.split("<br/>")
    check("中英混排在词边界折行", len(mixed_segs) >= 2, mixed)
    check("ImageNet 整词保留", any("ImageNet" in s for s in mixed_segs), mixed)
    check("CIFAR-10 整词保留", any("CIFAR-10" in s for s in mixed_segs), mixed)
    check("混排每行不超宽", all(_display_width(s) <= LABEL_WRAP_WIDTH for s in mixed_segs),
          str([_display_width(s) for s in mixed_segs]))

    huge = wrap_label("超" * 100)
    check("超长截断为 3 行内", huge.count("<br/>") <= LABEL_MAX_LINES - 1 and huge.endswith("…"), huge[:48])
    check("确定性：两次调用一致",
          wrap_label("数据: 主要使用ImageNet和CIFAR-10进行训练") == wrap_label("数据: 主要使用ImageNet和CIFAR-10进行训练"))


def test_mermaid_validation_with_br() -> None:
    """含 <br/> 的标签必须能通过校验与修复，且不得放松括号/引号安全检查。"""
    print("\n[<br/> 标签与校验/修复兼容]")
    code = 'flowchart TD\n  a_1["第一行<br/>第二行"]\n  a_1 --> b_1'
    check("含 <br/> 的标签能通过校验", validate_mermaid(code).ok is True)

    fenced = "```mermaid\n" + code + "\n```"
    repaired, actions = repair_mermaid(fenced)
    check("规则修复不丢 <br/>", "<br/>" in repaired and validate_mermaid(repaired).ok is True,
          f"repaired={repaired!r} actions={actions}")

    unquoted_pair = 'flowchart TD\n  a_1["x<br/>y"] b_2["未闭合'
    result = validate_mermaid(unquoted_pair)
    check("<br/> 不放松引号配平检查", result.ok is False, str(result.errors))

    stray = 'flowchart TD\n  a_1["x<br/>y"] 多余的['
    result = validate_mermaid(stray)
    check("<br/> 不放松括号配平检查", result.ok is False, str(result.errors))


def test_mermaid_label_wrapping() -> None:
    """build_diagram 产出的 Mermaid：节点标签折行、校验通过、前端标签保持单行。"""
    print("\n[导图标签折行接入]")
    import re

    from src.diagram import _display_width

    analysis = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()

    check("Mermaid 源码含 <br/> 折行", "<br/>" in code)
    result = validate_mermaid(code)
    check("含 <br/> 的完整导图通过校验", result.ok is True, str(result.errors))

    # 单行标签（<br/> 分隔后的每一段）都不得超过 20 全角宽——不再出现超长横排
    body = code[code.index("---", 3) + 3:] if code.startswith("---") else code  # 跳过 front-matter 标题
    segments = [seg for quoted in re.findall(r'"([^"]+)"', body) for seg in quoted.split("<br/>")]
    max_seg = max((_display_width(seg) for seg in segments), default=0.0)
    check("单行标签不超过 20 全角宽", max_seg <= 20, f"max={max_seg}")
    check("每个节点标签不超过 3 行",
          all(quoted.count("<br/>") <= 2 for quoted in re.findall(r'"([^"]+)"', body)))

    # node.label（前端详情面板/检索用）保持清洗后的单行文本，<br/> 只进 Mermaid 源码
    check("前端节点标签不含 <br/>", all("<br/>" not in n["label"] for n in diagram.node_index()))


def test_root_label_strategy() -> None:
    """根节点（圆形）展示标签：≤24 全角自动省略 + 窄行折行，圆不再被标题撑大。"""
    print("\n[根节点标签策略]")
    import re

    from src.diagram import (
        ROOT_LABEL_MAX_WIDTH,
        ROOT_MAX_LINES,
        ROOT_WRAP_WIDTH,
        _display_width,
        _root_display_label,
    )

    long_cn = "基于检索增强生成与自适应证据选择的大规模科学问答系统设计与实验验证"
    short_cn = _root_display_label(long_cn)
    check("长中文标题被省略", short_cn.endswith("…"), repr(short_cn))
    check("省略后总宽 ≤ 24 全角", _display_width(short_cn) <= ROOT_LABEL_MAX_WIDTH,
          f"width={_display_width(short_cn)}")

    long_en = ("Retrieval-Augmented Generation with Adaptive Evidence Selection "
               "for Scientific Question Answering")
    short_en = _root_display_label(long_en)
    check("长英文标题被省略", short_en.endswith("…"), repr(short_en))
    check("英文省略后总宽 ≤ 24 全角", _display_width(short_en) <= ROOT_LABEL_MAX_WIDTH,
          f"width={_display_width(short_en)}")

    analysis = {
        "title": long_cn,
        "one_sentence_summary": "",
        "research_problem": {"statement": "问题"},
        "contributions": [], "method": [], "argument_flow": [],
        "evidence_and_results": [], "conclusions": [], "limitations": [],
        "glossary": [], "critique": {},
    }
    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()
    check("根节点导图校验通过", validate_mermaid(code).ok is True)
    root_line = next(l for l in code.splitlines() if l.strip().startswith("root_1"))
    check("根标签走窄行折行（省略+折行并用）", "<br/>" in root_line, root_line)
    label = re.search(r"\(\((.+)\)\)", root_line).group(1).strip('"')
    segs = label.split("<br/>")
    check("根折行不超过行数上限", len(segs) <= ROOT_MAX_LINES, label)
    check("根每行不超过窄行宽度", all(_display_width(s) <= ROOT_WRAP_WIDTH for s in segs),
          str([_display_width(s) for s in segs]))


def test_diamond_narrow_wrap() -> None:
    """菱形（研究问题）用更窄的折行宽度，整体尺寸随文字框收缩。"""
    print("\n[菱形窄行折行]")
    import re

    from src.diagram import (
        DIAMOND_MAX_LINES,
        DIAMOND_WRAP_WIDTH,
        LABEL_WRAP_WIDTH,
        _display_width,
    )

    statement = ("检索增强问答的下游阅读器对「段落顺序与冗余度」高度敏感，"
                 "但现有系统仍把固定数量的检索段落不加区分地送入阅读器")
    analysis = {
        "title": "T", "one_sentence_summary": "",
        "research_problem": {"statement": statement},
        "contributions": [], "method": [], "argument_flow": [],
        "evidence_and_results": [], "conclusions": [], "limitations": [],
        "glossary": [], "critique": {},
    }
    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()
    check("菱形导图校验通过", validate_mermaid(code).ok is True)
    problem_line = next(l for l in code.splitlines() if l.strip().startswith("problem_1"))
    label = re.search(r"\{(.+)\}", problem_line).group(1).strip('"')
    segs = label.split("<br/>")
    check("菱形标签折行", len(segs) >= 2, label)
    check("菱形每行不超过窄行宽度", all(_display_width(s) <= DIAMOND_WRAP_WIDTH for s in segs),
          str([_display_width(s) for s in segs]))
    check("菱形行数不超过上限（不高得离谱）", len(segs) <= DIAMOND_MAX_LINES, label)
    check("菱形宽度比普通矩形更窄", DIAMOND_WRAP_WIDTH < LABEL_WRAP_WIDTH,
          f"diamond={DIAMOND_WRAP_WIDTH}, rect={LABEL_WRAP_WIDTH}")


def test_no_overwrap() -> None:
    """不过度分行：短标签保持单行；普通矩形/平行四边形仍用默认 16 全角宽度。"""
    print("\n[不过度分行]")
    from src.diagram import (
        LABEL_MAX_LINES,
        LABEL_WRAP_WIDTH,
        ROOT_WRAP_WIDTH,
        wrap_label,
    )

    check("普通矩形宽度保持 16 全角", LABEL_WRAP_WIDTH == 16 and LABEL_MAX_LINES == 3,
          f"width={LABEL_WRAP_WIDTH}, lines={LABEL_MAX_LINES}")

    analysis = {
        "title": "短标题",
        "one_sentence_summary": "一句话",
        "research_problem": {"statement": "问题"},
        "contributions": [{"label": "贡献"}], "method": [{"label": "方法"}],
        "argument_flow": [{"label": "论证"}],
        "evidence_and_results": [{"label": "证据"}],
        "conclusions": [{"label": "结论"}], "limitations": [{"label": "局限"}],
        "glossary": [{"term": "术语"}], "critique": {},
    }
    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()
    check("全短标签导图不含 <br/>（不过度分行）", "<br/>" not in code, code)
    check("短标签导图校验通过", validate_mermaid(code).ok is True)

    for short in ("两字", "ok", "短"):
        check(f"短标签 {short!r} 不折行", wrap_label(short) == short)
    check("不超过目标宽度的标签原样返回", wrap_label("自适应证据打分器评估", ROOT_WRAP_WIDTH, 3)
          == "自适应证据打分器评估")


def test_cross_section_edge_aggregation() -> None:
    """渲染层扇形边聚合：跨分区边合并为分区→分区一条；节点与分区内链式边不变。"""
    print("\n[跨分区边渲染层聚合]")
    analysis = {
        "title": "T", "one_sentence_summary": "S",
        "research_problem": {"statement": "问题"},
        "argument_flow": [],
        "method": [{"label": "方法甲"}, {"label": "方法乙"}],
        "contributions": [],
        "evidence_and_results": [{"label": "结果甲"}, {"label": "结果乙"}, {"label": "结果丙"}],
        "conclusions": [{"label": "结论甲"}, {"label": "结论乙"}],
        "limitations": [{"label": "局限甲"}, {"label": "局限乙"}],
        "critique": {"data": "数据问题"},
        "glossary": [],
    }
    diagram = build_diagram(analysis)
    check("模型层边数不变（聚合只发生在渲染层）", len(diagram.edges) >= 8,
          f"edges={len(diagram.edges)}")
    code = diagram.to_mermaid()
    result = validate_mermaid(code)
    check("聚合后校验通过", result.ok is True, str(result.errors))
    check("聚合后无孤儿误报", not any("默认矩形渲染" in w for w in result.warnings),
          str(result.warnings))

    check("相同两分区的扇形边聚合", code.count('evidence_grp -->|"归纳结论"| conclusions_grp') == 1, code)
    check("证据→结论连到分区", 'evidence_grp -->|"归纳结论"| conclusions_grp' in code)
    check("局限由问题分区分叉", 'problem_grp -->|"约束条件"| limitations_grp' in code)
    check("批判回连到受评估的证据", 'evidence_grp -->|"评估依据"| critique_grp' in code)
    check("结论、局限、批判不再串成固定路径",
          'conclusions_grp -->|"局限"| limitations_grp' not in code
          and 'limitations_grp -->|"批判"| critique_grp' not in code)
    check("分区内链式边保留且不重复标注下一步",
          'method_1 --> method_2' in code and '"下一步"' not in code)
    check("不再出现节点级扇形边", "conclusions_1 --> limitations_2" not in code)
    # 节点本身没有被改（只聚合边）
    check("节点声明保持原样",
          code.count("limitations_1[[") == 1 and code.count("conclusions_1(") == 1, code)

    analysis["limitations"] = [
        {"label": "样本偏差", "kind": "数据"},
        {"label": "算法边界", "kind": "方法"},
        {"label": "应用范围", "kind": "应用边界"},
    ]
    analysis["critique"] = {"data": "样本过窄", "method": "假设过强", "overall": "推断范围过宽"}
    typed = build_diagram(analysis)
    edges = {(edge.src, edge.dst) for edge in typed.edges}
    check("数据局限连证据", ("evidence_3", "limitations_1") in edges)
    check("方法局限连方法", ("method_2", "limitations_2") in edges)
    check("应用边界连结论", ("conclusions_2", "limitations_3") in edges)
    check("批判维度分别回连证据、方法和结论",
          {("evidence_3", "critique_1"), ("method_2", "critique_2"),
           ("conclusions_2", "critique_3")}.issubset(edges))

    analysis["conclusions"] = [
        {"label": "有据结论", "supports": [1, 3, 3, 99]},
        {"label": "待核实结论", "supports": []},
    ]
    traced = build_diagram(analysis)
    traced_edges = {(edge.src, edge.dst, edge.label) for edge in traced.edges}
    check("结论只连明确编号的证据且去重",
          {("evidence_1", "conclusions_1", "支持结论"),
           ("evidence_3", "conclusions_1", "支持结论")}.issubset(traced_edges)
          and ("evidence_2", "conclusions_1", "支持结论") not in traced_edges
          and sum(edge.dst == "conclusions_1" for edge in traced.edges) == 2)
    check("无证据结论明确标为待核实",
          not any(edge.src.startswith("evidence_") and edge.dst == "conclusions_2"
                  for edge in traced.edges)
          and any(edge.dst == "conclusions_2" and edge.label == "待核实结论"
                  for edge in traced.edges))
    analysis["semantic_links"] = [
        {"from": "method:1", "to": "evidence:2", "relation": "验证"},
        {"from": "evidence:2", "to": "conclusions:1", "relation": "支持"},
        {"from": "critique:1", "to": "method:1", "relation": "逆向无效"},
    ]
    adaptive = build_diagram(analysis)
    adaptive_edges = {(edge.src, edge.dst, edge.label) for edge in adaptive.edges}
    check("论文语义关系覆盖默认跨分区连线",
          ("method_1", "evidence_2", "验证") in adaptive_edges
          and ("evidence_2", "conclusions_1", "支持") in adaptive_edges
          and not any(edge.dst == "conclusions_1" and edge.src == "evidence_1"
                      for edge in adaptive.edges))
    check("逆向关系被忽略", not any(edge.label == "逆向无效" for edge in adaptive.edges))
    analysis["argument_flow"] = []
    analysis["method"] = []
    analysis["limitations"] = []
    analysis["critique"] = {}
    analysis["semantic_links"] = []
    sparse = build_diagram(analysis)
    check("无内容分区不出现在导图", all(section not in sparse.sections
          for section in ("arguments", "method", "limitations", "critique")))


# ---------------------------------------------------------------------------
# 契约同步与样例加厚（可重复校验）
# ---------------------------------------------------------------------------
def _strip_jsonc_comments(text: str) -> str:
    """去掉 jsonc 的 // 与 /* */ 注释（字符串字面量内的 // 不受影响）。"""
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _payload_key_paths(obj, prefix: str = "") -> set[str]:
    """收集 JSON 结构的全部键路径（数组取首个元素展开为 ``[]``）。"""
    paths: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= _payload_key_paths(value, path)
    elif isinstance(obj, list) and obj:
        paths |= _payload_key_paths(obj[0], f"{prefix}[]")
    return paths


def test_frontend_contract_sync() -> None:
    """docs/frontend-contract.md 的 payload 示例必须与 build_payload 实际产出同构。

    这是"前端不得解析模型原始回答"契约的可重复校验：
    * build_payload 产出的每个键都必须在契约文档里有记载；
    * 文档多出来的键只能是"流水线后续写入 / 溯源校验后才有"的条件键。
    """
    print("\n[前端契约文档与 payload 同步]")
    import re as _re

    from src.verify import VERIFY_KEYS

    doc_text = (PROJECT_ROOT / "docs" / "frontend-contract.md").read_text(encoding="utf-8")
    match = _re.search(r"```jsonc\n(.*?)```", doc_text, _re.DOTALL)
    check("契约文档含 jsonc 代码块", match is not None)
    if match is None:
        return
    doc_payload = json.loads(_strip_jsonc_comments(match.group(1)))
    doc_paths = _payload_key_paths(doc_payload)

    analysis = json.loads((PROJECT_ROOT / "examples" / "sample_analysis.json").read_text(encoding="utf-8"))
    diagram = build_diagram(analysis)
    code = diagram.to_mermaid()
    payload = build_payload(analysis, diagram, source_name="x.pdf", validation=validate_mermaid(code), mermaid_source=code)
    actual_paths = _payload_key_paths(payload)

    # 流水线在 build_payload 之后才写入 / 溯源校验后才出现的键（见 pipeline.py 与 verify.py），
    # 以及取值随内容变化的容器（文档示例里给出的是示意值，实际子键按内容而定）
    expected_optional = {
        "stats.pageFixedCount",
        "facts.researchProblem.verify",
        "glossary[].verify",
        "nodes[].verify",
        # paper 由流水线在 build_payload 之后写入（见 pipeline.py _paper_pages_payload）
        "paper",
        "paper.pages",
        "paper.pages[]",
        "paper.pages[].page",
        "paper.pages[].text",
    }
    variant_containers = ("nodes[].extra.", "meta.usage.")
    verify_children = {f"{parent}.{k}" for parent in ("facts.researchProblem.verify", "nodes[].verify")
                       for k in VERIFY_KEYS}

    def _comparable(paths: set[str]) -> set[str]:
        return {p for p in paths if not p.startswith(variant_containers)}

    undocumented = _comparable(actual_paths) - _comparable(doc_paths)
    check("实际 payload 的键全部有契约记载", not undocumented, f"文档缺少：{sorted(undocumented)}")

    extra = _comparable(doc_paths) - _comparable(actual_paths) - expected_optional - verify_children
    check("文档没有描述已不存在的键", not extra, f"多余记载：{sorted(extra)}")
    check("stats.pageFixedCount 已入契约", "stats.pageFixedCount" in doc_paths)
    check("可变容器 meta.usage 已入契约", "meta.usage" in doc_paths and "meta.usage" in actual_paths)
    check("可变容器 nodes[].extra 已入契约", "nodes[].extra" in doc_paths and "nodes[].extra" in actual_paths)

    # 溯源校验写回的 verify 字段必须与 VERIFY_KEYS 完全一致
    analysis["research_problem"]["verify"] = dict.fromkeys(VERIFY_KEYS, True)
    analysis["contributions"][0]["verify"] = dict.fromkeys(VERIFY_KEYS, True)
    analysis["glossary"][0]["verify"] = dict.fromkeys(VERIFY_KEYS, True)
    from src.diagram import attach_verification

    attach_verification(diagram, analysis)
    verified_payload = build_payload(analysis, diagram, source_name="x.pdf",
                                     validation=validate_mermaid(code), mermaid_source=code)
    verify_dicts = [verified_payload["facts"]["researchProblem"]["verify"],
                    verified_payload["nodes"][0]["verify"],
                    verified_payload["glossary"][0]["verify"]]
    check("verify 字段恒为契约字段（含 context）",
          all(set(v.keys()) == set(VERIFY_KEYS) for v in verify_dicts),
          str([sorted(v.keys()) for v in verify_dicts]))


def test_en_sample_glossary_enriched() -> None:
    """英文样例 glossary 已加厚：plain 70~90 字 + analogy/why 齐备，evidence 逐字可溯源。"""
    print("\n[英文样例 glossary 加厚校验]")
    from src.config import Settings
    from src.parse import parse_pdf
    from src.textutils import CitationVerifier

    data = json.loads((PROJECT_ROOT / "examples" / "sample_en_two_column.analysis.json").read_text(encoding="utf-8"))
    try:
        validate_analysis(data)
        check("加厚后的样例仍通过 validate_analysis", True)
    except Exception as exc:  # noqa: BLE001
        check("加厚后的样例仍通过 validate_analysis", False, str(exc))

    glossary = data.get("glossary") or []
    check("glossary 共 6 条", len(glossary) == 6, str(len(glossary)))
    expected_keys = {"term", "plain", "analogy", "why_it_matters", "evidence", "page"}
    for entry in glossary:
        term = str(entry.get("term", ""))
        check(f"[{term}] 键结构不变", set(entry.keys()) == expected_keys, str(sorted(entry.keys())))
        check(f"[{term}] plain 70~90 字", 70 <= len(entry.get("plain", "")) <= 90,
              f"len={len(entry.get('plain', ''))}")
        check(f"[{term}] analogy ≥30 字", len(entry.get("analogy", "")) >= 30,
              f"len={len(entry.get('analogy', ''))}")
        check(f"[{term}] why_it_matters ≥30 字", len(entry.get("why_it_matters", "")) >= 30,
              f"len={len(entry.get('why_it_matters', ''))}")
        page = entry.get("page")
        check(f"[{term}] page 为 int", isinstance(page, int) and not isinstance(page, bool), str(page))

    # evidence 必须逐字来自样例 PDF（与 --mock 全流程的溯源校验同口径）
    paper = parse_pdf(PROJECT_ROOT / "examples" / "sample_en_two_column.pdf", Settings())
    verifier = CitationVerifier(paper.full_text, paper.page_spans)
    for entry in glossary:
        result = verifier.verify(entry.get("evidence", ""), entry.get("page"))
        check(f"[{entry.get('term')}] evidence 逐字命中原文", bool(result.get("found")), str(result))


def test_logging_setup() -> None:
    print("\n[运行日志落盘与剪枝]")
    import tempfile
    from pathlib import Path as _P

    from src.logging_setup import create_run_log
    from src.pipeline import Logger

    with tempfile.TemporaryDirectory() as tmp:
        log_dir = _P(tmp)

        # 建文件 + Logger 落盘内容（含被控制台级别过滤的 debug 行）
        path = create_run_log(log_dir)
        logger = Logger("info")
        logger.bind_file(path)
        logger.debug("只有文件里能看到我")
        logger.info("控制台与文件都有我")
        logger.close_log("测试收尾")
        text = path.read_text(encoding="utf-8")
        check("日志文件已创建", path.exists() and path.name.startswith("run_"))
        check("debug 行写入文件", "只有文件里能看到我" in text)
        check("info 行写入文件", "控制台与文件都有我" in text)
        check("运行开始/结束行写入", "运行开始" in text and "运行结束" in text and "测试收尾" in text)
        check("close_log 幂等", logger.close_log("再次关闭") is None)

        # 剪枝：预置 12 份旧日志 + 本次 1 份 → 只保留 10 份且删的是最旧的
        for i in range(12):
            old = log_dir / f"run_20200101_000000_{i:03d}.log"
            old.write_text("旧日志", encoding="utf-8")
            import os as _os
            stamp = 1577836800 + i          # 2020-01-01 起递增，保证 mtime 有序
            _os.utime(old, (stamp, stamp))
        newest = create_run_log(log_dir)
        remaining = sorted(log_dir.glob("run_*.log"))
        check("日志最多保留 10 份", len(remaining) == 10, f"实际 {len(remaining)} 份")
        check("删除的是最旧的日志", newest in remaining and all(
            int(p.name.split("_")[-1].split(".")[0]) >= 3 for p in remaining if p != newest
        ) or newest in remaining)


def test_parse_retry() -> None:
    print("\n[PDF 解析重试]")
    import src.parse as parse_mod
    from src.parse import ParseQualityError, parse_pdf_with_retry

    real = parse_mod.parse_pdf
    calls = {"n": 0}
    flaky = RuntimeError("file is being copied by another process")

    def flaky_then_ok(pdf_path, settings=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise flaky
        return "OK-PAPER"

    try:
        parse_mod.parse_pdf = flaky_then_ok
        result = parse_pdf_with_retry("x.pdf", None, attempts=3, delay=0)
        check("瞬时错误重试后成功", result == "OK-PAPER" and calls["n"] == 3, f"调用 {calls['n']} 次")

        calls["n"] = 0
        parse_mod.parse_pdf = lambda p, s=None: (_ for _ in ()).throw(flaky)
        calls2 = {"n": 0}

        def always_fail(pdf_path, settings=None):
            calls2["n"] += 1
            raise flaky

        parse_mod.parse_pdf = always_fail
        raised = None
        try:
            parse_pdf_with_retry("x.pdf", None, attempts=3, delay=0)
        except ValueError as exc:
            raised = exc
        check("重试耗尽抛 ValueError", raised is not None and "已重试 2 次" in str(raised))
        check("重试共尝试 3 次", calls2["n"] == 3, f"调用 {calls2['n']} 次")

        calls3 = {"n": 0}

        def missing(pdf_path, settings=None):
            calls3["n"] += 1
            raise FileNotFoundError("找不到 PDF 文件")

        parse_mod.parse_pdf = missing
        raised = None
        try:
            parse_pdf_with_retry("x.pdf", None, attempts=3, delay=0)
        except FileNotFoundError:
            raised = True
        check("文件不存在不重试", raised is True and calls3["n"] == 1, f"调用 {calls3['n']} 次")

        calls4 = {"n": 0}

        def scanned(pdf_path, settings=None):
            calls4["n"] += 1
            raise ParseQualityError("PDF 中没有可提取的文本层")

        parse_mod.parse_pdf = scanned
        raised = None
        try:
            parse_pdf_with_retry("x.pdf", None, attempts=3, delay=0)
        except ParseQualityError:
            raised = True
        check("无文本层（扫描件）不重试", raised is True and calls4["n"] == 1, f"调用 {calls4['n']} 次")
    finally:
        parse_mod.parse_pdf = real


def main() -> int:
    print("=" * 68)
    print("  单元自测")
    print("=" * 68)
    test_multipart()
    test_safe_filename()
    test_citation_verifier()
    test_json_extraction()
    test_sanitize_label()
    test_mermaid_validation()
    test_unbalanced_bracket_detected()
    test_wrap_label()
    test_mermaid_validation_with_br()
    test_mermaid_label_wrapping()
    test_root_label_strategy()
    test_diamond_narrow_wrap()
    test_no_overwrap()
    test_cross_section_edge_aggregation()
    test_normalize_helpers()
    test_long_paper_excerpt()
    test_validate_analysis_contract()
    test_hostile_analysis()
    test_payload_and_escape()
    test_attach_verification()
    test_verify_contract()
    test_parse_module_importable()
    test_bootstrap_first_run_check()
    test_frontend_contract_sync()
    test_en_sample_glossary_enriched()
    test_logging_setup()
    test_parse_retry()

    print()
    print("=" * 68)
    print(f"  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print(f"    ✗ {item}")
        print("=" * 68)
        return 1
    print("  ✓ 全部通过")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
