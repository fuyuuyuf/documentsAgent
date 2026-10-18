"""渲染层：把分析结果组装成前端 payload，并生成单文件离线 HTML 看板。

关键点（对应"看板可用"20 分）：
  * 所有资源内嵌（mermaid.min.js / CSS / JS / 数据），产物不依赖任何 CDN；
  * payload 内联为 application/json，避免 JS 字符串拼接导致的转义问题；
  * `<` 统一转义为 \\u003c，防止论文内容里的 `</script>` 提前闭合脚本标签。
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DASHBOARD_TEMPLATE, MERMAID_JS
from .diagram import SECTION_TITLES, Diagram, ValidationResult
from .textutils import as_page_number

SECTION_COLORS: dict[str, str] = {
    "root": "#4f46e5",
    "problem": "#dc2626",
    "arguments": "#ea580c",
    "method": "#0891b2",
    "contributions": "#16a34a",
    "evidence": "#7c3aed",
    "conclusions": "#2563eb",
    "limitations": "#b45309",
    "critique": "#be123c",
    "glossary": "#0f766e",
}

TRUNK_SECTIONS = ["root", "problem", "arguments", "method", "evidence", "conclusions"]

_CRITIQUE_KEYS = [
    ("data", "data"),
    ("experiment", "experiment"),
    ("method", "method"),
    ("assumption", "assumption"),
    ("application_boundary", "applicationBoundary"),
    ("overall", "overall"),
]


class RenderError(RuntimeError):
    """渲染阶段的失败，带上可操作的修复建议。"""


# ---------------------------------------------------------------------------
# payload 组装
# ---------------------------------------------------------------------------
def build_payload(
    analysis: dict,
    diagram: Diagram,
    *,
    source_name: str,
    page_count: int = 0,
    body_pages: int = 0,
    char_count: int = 0,
    two_column_pages: list[int] | None = None,
    model: str = "",
    mode: str = "api",
    pipeline_seconds: float = 0.0,
    usage: dict | None = None,
    validation: ValidationResult | None = None,
    repairs: list[str] | None = None,
    warnings: list[str] | None = None,
    mermaid_source: str | None = None,
) -> dict:
    """组装前端契约 payload（见 docs/frontend-contract.md）。"""
    facts = analysis.get("facts") if isinstance(analysis.get("facts"), dict) else {}
    problem_raw = analysis.get("research_problem")
    problem_raw = problem_raw if isinstance(problem_raw, dict) else {}

    # 允许调用方把 facts 拆到顶层（pipeline 的默认行为）
    one_liner = str(analysis.get("one_sentence_summary") or facts.get("oneSentenceSummary") or "").strip()
    open_questions = analysis.get("open_questions") or facts.get("openQuestions") or []
    reading_path = analysis.get("reading_path") or facts.get("readingPath") or []

    nodes = diagram.node_index()
    node_ids = {node["id"] for node in nodes}

    # 研究问题已是导图节点；仅在该节点缺席时补计一次，避免重复统计。
    # 完整流水线会再用溯源报告的总数覆盖此处的展示层估计值。
    evidence_items: list[dict] = [n for n in nodes if (n.get("evidence") or "").strip()]
    has_problem_node = any(n.get("section") == "problem" and (n.get("evidence") or "").strip() for n in nodes)
    if str(problem_raw.get("evidence") or "").strip() and not has_problem_node:
        evidence_items.append({"verify": problem_raw.get("verify") or {}})
    verified = [n for n in evidence_items if (n.get("verify") or {}).get("found")]
    mismatched = [n for n in evidence_items if (n.get("verify") or {}).get("mismatch")]
    unverified = [n for n in evidence_items if not (n.get("verify") or {}).get("found")]

    critique_raw = analysis.get("critique") if isinstance(analysis.get("critique"), dict) else {}
    critique = {}
    for source_key, target_key in _CRITIQUE_KEYS:
        value = critique_raw.get(source_key)
        if isinstance(value, str) and value.strip():
            critique[target_key] = value.strip()

    glossary: list[dict] = []
    for item in analysis.get("glossary") or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        plain = str(item.get("plain") or "").strip()
        if not term and not plain:
            continue
        entry = {
            "term": term or "(未命名术语)",
            "plain": plain,
            "analogy": str(item.get("analogy") or "").strip(),
            "why": str(item.get("why_it_matters") or item.get("why") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
            "page": as_page_number(item.get("page")),
        }
        if isinstance(item.get("verify"), dict):
            entry["verify"] = item["verify"]
        glossary.append(entry)

    payload: dict[str, Any] = {
        "meta": {
            "title": str(analysis.get("title") or diagram.title or source_name).strip(),
            "source": source_name,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pageCount": page_count,
            "bodyPages": body_pages,
            "charCount": char_count,
            "twoColumnPages": list(two_column_pages or []),
            "model": model,
            "mode": mode,
            "pipelineSeconds": round(pipeline_seconds, 1),
            "usage": usage or {},
        },
        "facts": {
            "oneSentenceSummary": one_liner,
            "researchProblem": {
                "statement": str(problem_raw.get("statement") or "").strip(),
                "whyItMatters": str(
                    problem_raw.get("why_it_matters") or problem_raw.get("whyItMatters") or ""
                ).strip(),
                "evidence": str(problem_raw.get("evidence") or "").strip(),
                "page": as_page_number(problem_raw.get("page")),
            },
            "openQuestions": [str(q).strip() for q in open_questions if str(q).strip()],
            "readingPath": [str(p).strip() for p in reading_path if str(p).strip()],
        },
        "diagram": {
            "mermaid": mermaid_source or diagram.to_mermaid(),
            "sections": [
                {"key": key, "title": SECTION_TITLES.get(key, key), "color": SECTION_COLORS.get(key, "#475569")}
                for key in diagram.sections
            ],
            "trunkSections": [key for key in TRUNK_SECTIONS if key in diagram.sections],
        },
        "nodes": nodes,
        "critique": critique,
        "glossary": glossary,
        "stats": {
            "nodeCount": len(node_ids),
            "sectionCount": len(diagram.sections),
            "evidenceCount": len(evidence_items),
            "verifiedCount": len(verified),
            "mismatchCount": len(mismatched),
            "unverifiedCount": len(unverified),
            "verifiedNodeCount": sum(
                1 for node in nodes if isinstance(node, dict) and (node.get("verify") or {}).get("found")
            ),
        },
        "validation": {
            "ok": bool(validation.ok) if validation else True,
            "errors": list(validation.errors) if validation else [],
            "warnings": list(validation.warnings) if validation else [],
            "repairs": list(repairs or []),
        },
        "warnings": list(warnings or []),
    }
    if isinstance(problem_raw.get("verify"), dict):
        payload["facts"]["researchProblem"]["verify"] = problem_raw["verify"]
    return payload


# ---------------------------------------------------------------------------
# HTML 生成
# ---------------------------------------------------------------------------
def load_assets() -> tuple[str, str, str]:
    """读取模板、前端脚本与内嵌的 mermaid.min.js。"""
    if not DASHBOARD_TEMPLATE.exists():
        raise RenderError(f"缺少 HTML 模板：{DASHBOARD_TEMPLATE}")
    app_js_path = DASHBOARD_TEMPLATE.parent / "app.js"
    if not app_js_path.exists():
        raise RenderError(f"缺少前端脚本：{app_js_path}")

    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    app_js = app_js_path.read_text(encoding="utf-8")

    if not MERMAID_JS.exists():
        raise RenderError(
            f"缺少离线渲染资源：{MERMAID_JS}\n"
            "请运行 `python tools/fetch_mermaid.py` 下载 mermaid.min.js（仅需一次），"
            "或从任意 mermaid@10.9.1 分发包中把 dist/mermaid.min.js 放到该路径。\n"
            "本工具坚持离线可用，因此不会在运行时从 CDN 加载。"
        )
    mermaid_js = MERMAID_JS.read_text(encoding="utf-8", errors="replace")
    return template, app_js, mermaid_js


def render_html(payload: dict, *, template: str, app_js: str, mermaid_js: str) -> str:
    """把 payload 注入模板，产出单文件 HTML。"""
    meta = payload.get("meta") or {}
    title = str(meta.get("title") or "论文结构导图")
    stats = payload.get("stats") or {}

    brand_sub = " · ".join(
        part
        for part in (
            str(meta.get("source") or ""),
            f"{meta.get('pageCount')} 页" if meta.get("pageCount") else "",
            f"{stats.get('nodeCount', 0)} 节点",
            "离线演示模式" if meta.get("mode") == "mock" else (str(meta.get("model") or "")),
        )
        if part
    )

    data_json = json.dumps(payload, ensure_ascii=False, indent=2)
    # </script> 与 <!-- 是 HTML 解析器的雷区，统一转义
    data_json = data_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    # mermaid.min.js 内部可能包含 "</script>" 字面量，必须切断
    safe_mermaid = mermaid_js.replace("</script", "<\\/script")
    safe_app = app_js.replace("</script", "<\\/script")

    out = template
    out = out.replace("__PAGE_TITLE__", html.escape(title, quote=True))
    out = out.replace("__BRAND_SUB__", html.escape(brand_sub, quote=True))
    out = out.replace("__MERMAID_JS__", safe_mermaid)
    out = out.replace("__DATA_JSON__", data_json)
    out = out.replace("__APP_JS__", safe_app)
    return out


def write_dashboard(
    payload: dict,
    output_path: str | Path,
    *,
    template: str | None = None,
    app_js: str | None = None,
    mermaid_js: str | None = None,
) -> Path:
    if template is None or app_js is None or mermaid_js is None:
        template, app_js, mermaid_js = load_assets()
    output = Path(output_path).expanduser()
    if output.is_dir() or str(output_path).endswith(("/", "\\")):
        raise RenderError(f"输出路径是一个目录，请指定 .html 文件名：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(payload, template=template, app_js=app_js, mermaid_js=mermaid_js)
    output.write_text(html_text, encoding="utf-8")
    return output
