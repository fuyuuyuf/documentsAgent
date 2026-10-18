"""流水线编排：PDF → 文本提取 → 大模型结构化分析 → 证据溯源 → Mermaid → HTML。

设计目标（对应"一键跑通"30 分）：
  一条命令跑完全程，中途不需要任何人工输入；
  每个阶段都有明确的失败出口与降级策略，绝不静默产出坏结果。
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Settings
from .diagram import (
    Diagram,
    ValidationResult,
    attach_verification,
    build_diagram,
    repair_mermaid,
    validate_mermaid,
)
from .llm import LLMClient, LLMError, LLMResult, estimate_tokens
from .logging_setup import create_run_log
from .parse import PaperText, parse_pdf_with_retry
from .prompts import (
    AUDIENCE_SYSTEM_PROMPT,
    CHUNK_SYSTEM_PROMPT,
    REPAIR_HINT,
    SYSTEM_PROMPT,
    build_analysis_prompt,
    build_audience_prompt,
    build_chunk_prompt,
    build_synthesis_prompt,
    validate_analysis,
    validate_audience,
    validate_chunk_findings,
)
from .render import build_payload, load_assets, write_dashboard
from .textutils import CitationVerifier, has_cjk, normalize, truncate

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3, "silent": 4}


class Logger:
    """极简日志器：支持 --verbose / --quiet，不依赖 logging 配置。

    可选 ``bind_file()`` 把全部日志（含被控制台级别过滤的 debug 行）同步
    落盘到 log/ 目录的运行日志文件；每行即时 flush，进程崩溃也不丢内容。
    """

    def __init__(self, level: str = "info", stream=None):
        self.level = LEVELS.get(level, 1)
        self.stream = stream or sys.stderr
        self.records: list[tuple[str, str]] = []
        self._log_file = None

    # ---- 运行日志落盘（src/logging_setup.py 负责建文件与剪枝） ----
    def bind_file(self, path) -> None:
        """绑定本次运行的日志文件；幂等，重复绑定先关旧文件。"""
        self.close_log()
        self._log_file = open(path, "a", encoding="utf-8", errors="replace")
        self._log_file.write(f"===== 运行开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        self._log_file.flush()

    def close_log(self, footer: str = "") -> None:
        """写结束行并关闭日志文件；未绑定或已关闭时静默返回。"""
        handle, self._log_file = self._log_file, None
        if not handle:
            return
        try:
            if footer:
                handle.write(f"===== 运行结束 {datetime.now().strftime('%H:%M:%S')} · {footer} =====\n")
            handle.close()
        except OSError:
            pass

    def _emit(self, tag: str, level: str, message: str) -> None:
        self.records.append((level, message))
        if self._log_file:
            try:
                stamp = datetime.now().strftime("%H:%M:%S")
                self._log_file.write(f"{stamp} {tag} {message}\n")
                self._log_file.flush()
            except (OSError, ValueError):
                self._log_file = None        # 磁盘满 / 文件被删等：放弃落盘，不影响主流程
        if LEVELS[level] < self.level:
            return
        try:
            print(f"{tag} {message}", file=self.stream, flush=True)
        except (UnicodeEncodeError, OSError):
            # Windows 控制台编码兜底，绝不让日志把主流程搞崩
            try:
                print(f"{tag} {message}".encode("utf-8", "replace").decode("utf-8", "replace"), file=self.stream)
            except Exception:
                pass

    def debug(self, message: str) -> None:
        self._emit("[debug]", "debug", message)

    def info(self, message: str) -> None:
        self._emit("[  ok ]", "info", message)

    def step(self, message: str) -> None:
        self._emit("[step ]", "info", message)

    def warn(self, message: str) -> None:
        self._emit("[warn]", "warn", message)

    def error(self, message: str) -> None:
        self._emit("[fail]", "error", message)


# ---------------------------------------------------------------------------
# 溯源校验的轻量实现（verify.py 存在时自动改用更完整的版本）
# ---------------------------------------------------------------------------
@dataclass
class LocalVerifyReport:
    total_claims: int = 0
    verified: int = 0
    not_found: int = 0
    mismatched_pages: int = 0
    page_fixed: int = 0
    empty_evidence: int = 0
    suspects: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"证据 {self.total_claims} 条：已溯源 {self.verified}、"
            f"未定位 {self.not_found}、页码纠正 {self.page_fixed}"
        )


_PLACEHOLDER_EVIDENCE = {
    "",
    "无",
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "—",
    "未知",
    "占位",
    "未提及",
    "论文未提及",
    "论文未明确说明",
    "论文未说明",
    "not mentioned",
    "not specified",
}


def _is_placeholder_evidence(quote: str) -> bool:
    cleaned = normalize(quote).lower().strip(" .。，,;；:：")
    if not cleaned or cleaned in _PLACEHOLDER_EVIDENCE:
        return True
    # 纯占位标记，例如 "占位1" "todo"
    return cleaned in {"todo", "tbd"} or cleaned.startswith("占位")


def verify_against_paper(analysis: dict, paper: PaperText, *, strict: bool = False) -> LocalVerifyReport:
    """把每条 evidence 拿回原文定位，纠正页码、标注可疑引用。

    这是"降低幻觉"的硬手段：模型自己说页码不算数，必须在原文里找得到。
    """
    verifier = CitationVerifier(paper.full_text, paper.page_spans)
    report = LocalVerifyReport()

    def handle(container: dict, path: str) -> None:
        if not isinstance(container, dict):
            return
        quote = container.get("evidence")
        if not isinstance(quote, str) or not quote.strip():
            return
        if _is_placeholder_evidence(quote):
            report.empty_evidence += 1
            container["evidence"] = ""
            if container.get("page") in (None, ""):
                container.pop("page", None)
            return

        report.total_claims += 1
        claimed = container.get("page")
        result = verifier.verify(quote, claimed)
        entry = {
            "found": bool(result.get("found")),
            "page": result.get("page"),
            "score": result.get("score", 0.0),
            "method": result.get("method", "none"),
            "mismatch": bool(result.get("mismatch")),
        }

        action = "保留"
        if entry["found"]:
            report.verified += 1
            measured = result.get("page")
            if measured and claimed and int(measured) != int(claimed):
                report.mismatched_pages += 1
                container["page"] = int(measured)
                entry["page_claimed"] = int(claimed)
                entry["mismatch"] = True
                action = "已修正页码"
                report.page_fixed += 1
            elif measured and not claimed:
                container["page"] = int(measured)
                action = "已补全页码"
                report.page_fixed += 1
            elif entry["mismatch"]:
                report.mismatched_pages += 1
                action = "页码存疑"
        else:
            report.not_found += 1
            action = "未定位到"
            if strict:
                container["evidence"] = ""
                entry["dropped"] = True
                action = "已清空（strict）"

        if entry["found"]:
            # 命中后附带简要上下文（与 verify.py 同口径；失败只让 context 缺席）
            try:
                context = verifier.window(quote)
                if context:
                    entry["context"] = context
            except Exception:  # noqa: BLE001 - 上下文失败绝不阻断溯源
                pass

        container["verify"] = entry
        if action != "保留":
            report.suspects.append(
                {
                    "path": path,
                    "quote": truncate(normalize(quote), 60),
                    "action": action,
                    "score": entry["score"],
                    "method": entry["method"],
                }
            )

    handle(analysis.get("research_problem") or {}, "research_problem.evidence")
    for key in ("contributions", "method", "argument_flow", "evidence_and_results", "conclusions", "limitations"):
        items = analysis.get(key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                handle(item, f"{key}[{index}].evidence")
    glossary = analysis.get("glossary")
    if isinstance(glossary, list):
        for index, item in enumerate(glossary):
            if isinstance(item, dict):
                handle(item, f"glossary[{index}].evidence")
    return report


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------
@dataclass
class PipelineResult:
    html_path: Path
    payload: dict
    analysis: dict
    diagram: Diagram
    validation: ValidationResult
    paper: PaperText
    report: object
    repair_rounds: int = 0
    warnings: list[str] = field(default_factory=list)
    debug_files: list[Path] = field(default_factory=list)
    seconds: float = 0.0

    def summary(self) -> str:
        stats = self.payload.get("stats", {})
        return (
            f"{self.paper.page_count} 页 / {self.paper.char_count} 字符 → "
            f"{stats.get('nodeCount', 0)} 节点 → {self.html_path.name}"
        )

    def verification_counts(self) -> dict:
        """统一取溯源口径；payload.stats 已在校验后被覆盖，两者一致。"""
        counts = _report_counts(self.report)
        if counts["claims"]:
            return counts
        stats = self.payload.get("stats", {}) or {}
        return {
            "claims": int(stats.get("evidenceCount", 0) or 0),
            "verified": int(stats.get("verifiedCount", 0) or 0),
            "not_found": int(stats.get("unverifiedCount", 0) or 0),
            "page_fixed": int(stats.get("pageFixedCount", 0) or 0),
            "mismatched": int(stats.get("mismatchCount", 0) or 0),
            "suspects": 0,
        }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_pipeline(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    *,
    settings: Settings | None = None,
    logger: Logger | None = None,
    audience_pass: bool = True,
    keep_debug: bool | None = None,
) -> PipelineResult:
    settings = settings or Settings.from_env()
    logger = logger or Logger("debug" if settings.verbose else "info")
    started = time.time()
    stage_seconds: dict[str, float] = {}
    llm_stats: list[dict] = []
    logger.bind_file(create_run_log())           # 本次运行日志落盘（log/ 目录，最多保留 10 份）

    pdf_path = Path(pdf_path).expanduser()
    if not pdf_path.exists():
        logger.close_log("运行失败 · 找不到输入文件")
        raise FileNotFoundError(f"找不到输入文件：{pdf_path}")

    keep_debug = settings.keep_debug if keep_debug is None else keep_debug
    output = Path(output_path).expanduser() if output_path else _default_output(pdf_path, settings)
    warnings: list[str] = []

    # ---------------- 1. PDF 文本提取 ----------------
    logger.step(f"[1/5] 解析 PDF：{pdf_path.name}")
    t_stage = time.perf_counter()
    paper = parse_pdf_with_retry(pdf_path, settings, logger=logger)
    stage_seconds["parse"] = time.perf_counter() - t_stage
    warnings.extend(paper.stats.get("warnings", []) or [])
    logger.info(
        f"提取 {paper.page_count} 页 / 正文 {paper.char_count} 字符 / "
        f"识别 {len(paper.headings)} 个标题"
        + (f" / 多栏页 {len(paper.two_column_pages)}" if paper.two_column_pages else "")
    )
    for message in warnings:
        logger.warn(message)

    long_form = not settings.mock and paper.char_count > settings.max_input_chars
    index_text = "" if long_form else _build_analysis_index(paper, settings.max_input_chars, logger)

    # ---------------- 2. 大模型结构化分析 ----------------
    # 离线模式会自动寻找 <论文名>.analysis.json，因此这里把输入路径透传下去
    settings.mock_pdf = str(pdf_path)
    client = LLMClient(settings)
    # 退避重试的进度接到日志上（mock 模式不会触发，但属性两条路径一致）
    client.log = logger.warn
    analysis_prompt = build_analysis_prompt(paper, index_text) if not long_form else ""
    if settings.mock:
        source = (client.mock_payload or {}).get("_mock_source", "(builtin)")
        logger.warn(f"离线演示模式（--mock）：不会调用任何大模型 API，导图内容来自 {source}")
    else:
        logger.step(f"[2/5] 调用大模型分析：{settings.model} @ {settings.base_url}（key={settings.masked_key()}）")
        logger.debug(
            f"分析轮输入 {len(SYSTEM_PROMPT) + len(analysis_prompt)} 字符"
            f"（≈{estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(analysis_prompt)} tok）"
        )

    t_stage = time.perf_counter()
    if long_form:
        analysis, first_result, analysis_prompt = _analyze_long_paper(
            paper, settings, client, logger, llm_stats
        )
    else:
        analysis, first_result = client.complete_json(
            SYSTEM_PROMPT,
            analysis_prompt,
            validate=validate_analysis,
            repair_hint=REPAIR_HINT,
        )
    stage_seconds["analysis"] = time.perf_counter() - t_stage
    _record_llm_call(llm_stats, "analysis", SYSTEM_PROMPT, analysis_prompt, first_result)
    if first_result.repaired:
        logger.warn("首次输出结构不合法，已自动重问并修复")
    logger.info(
        f"分析完成：{len(analysis.get('contributions') or [])} 项贡献 / "
        f"{len(analysis.get('method') or [])} 步方法 / "
        f"{len(analysis.get('glossary') or [])} 个术语"
        + (f" / tokens={client.total_tokens}" if client.total_tokens else "")
    )

    # ---------------- 2b. 受众导读（可选第二轮，失败不影响主流程） ----------------
    # mock 模式同样执行：离线响应由本地样例拼装，保证两条路径走完全相同的编排代码。
    if audience_pass:
        t_stage = time.perf_counter()
        try:
            terms, required_terms = _collect_terms(analysis)
            if terms:
                logger.step(
                    f"[2b] 生成通俗解读：{len(terms)} 个概念（第二轮只发送分析 JSON + 少量摘录，不重发正文）"
                )
                audience_prompt = build_audience_prompt(terms, analysis, paper)
                logger.debug(
                    f"解读轮输入 {len(AUDIENCE_SYSTEM_PROMPT) + len(audience_prompt)} 字符"
                    f"（≈{estimate_tokens(AUDIENCE_SYSTEM_PROMPT) + estimate_tokens(audience_prompt)} tok）"
                )
                audience, audience_result = client.complete_json(
                    AUDIENCE_SYSTEM_PROMPT,
                    audience_prompt,
                    validate=lambda data: validate_audience(data, required_terms=required_terms),
                )
                _record_llm_call(llm_stats, "audience", AUDIENCE_SYSTEM_PROMPT, audience_prompt, audience_result)
                analysis = _merge_audience(analysis, audience)
                glossary_now = [it for it in analysis.get("glossary") or [] if isinstance(it, dict)]
                logger.info(
                    f"通俗解读完成：覆盖 {len(audience.get('glossary') or [])}/{len(terms)} 个概念"
                    f" / 解读平均 {sum(len(str(it.get('plain') or '')) for it in glossary_now) // max(1, len(glossary_now))} 字"
                    + (f" / 整体叙述 {len(str(audience.get('story') or ''))} 字" if audience.get("story") else "")
                )
            else:
                logger.debug("分析结果中没有可解读的概念，跳过通俗解读轮")
        except LLMError as exc:
            logger.warn(f"通俗解读生成失败，沿用第一轮结果：{truncate(str(exc), 160)}")
        except Exception as exc:  # noqa: BLE001 - 可选环节，绝不阻断主流程
            logger.warn(f"通俗解读生成异常，已跳过：{truncate(str(exc), 160)}")
        finally:
            stage_seconds["audience"] = time.perf_counter() - t_stage

    # ---------------- 3. 证据溯源校验 ----------------
    logger.step("[3/5] 证据溯源校验（在原文中定位每一条引用）")
    t_stage = time.perf_counter()
    # _verify 返回校验后的副本（verify_analysis 不改写入参），必须用副本继续后续步骤
    analysis, report = _verify(analysis, paper, settings, logger)
    stage_seconds["verify"] = time.perf_counter() - t_stage
    counts = _report_counts(report)

    # ---------------- 4. Mermaid 生成与校验 ----------------
    logger.step("[4/5] 生成 Mermaid 并做语法预校验")
    t_stage = time.perf_counter()
    diagram = build_diagram(analysis)
    # 溯源校验发生在建图之后，必须回填，否则前端详情面板拿不到页码与定位结果
    attached = attach_verification(diagram, analysis)
    logger.debug(f"已把溯源结果回填到 {attached} 个图节点")
    mermaid_code = diagram.to_mermaid()
    validation = validate_mermaid(mermaid_code)
    repair_rounds = 0

    if not validation.ok:
        logger.warn(f"预校验未通过：{'；'.join(validation.errors)}")
        repaired, actions = repair_mermaid(mermaid_code)
        recheck = validate_mermaid(repaired)
        if recheck.ok:
            logger.info(f"规则化修复成功：{'、'.join(actions) or '已重排'}")
            mermaid_code, validation, repair_rounds = repaired, recheck, 1
        else:
            logger.warn("规则化修复不足，尝试让大模型二次修复")
            llm_fixed, ok = _llm_repair_mermaid(client, mermaid_code, recheck.errors, logger, stats=llm_stats)
            if ok:
                mermaid_code = llm_fixed
                validation = validate_mermaid(mermaid_code)
                repair_rounds = 2
                logger.info("大模型修复成功")
            else:
                # 最坏情况：保留问题代码，但前端有兜底视图，页面不会白屏
                mermaid_code = repaired
                validation = recheck
                warning = "Mermaid 语法仍存在问题，看板已启用兜底视图（节点列表 + 源码）"
                warnings.append(warning)
                logger.error(warning)
    else:
        logger.info(validation.summary())
    stage_seconds["diagram"] = time.perf_counter() - t_stage

    # ---------------- 5. 渲染单文件 HTML ----------------
    logger.step("[5/5] 渲染离线交互式 HTML 看板")
    t_stage = time.perf_counter()
    payload = build_payload(
        analysis,
        diagram,
        source_name=pdf_path.name,
        page_count=paper.page_count,
        body_pages=len(paper.body_pages),
        char_count=paper.char_count,
        two_column_pages=paper.two_column_pages,
        model=("mock-offline" if settings.mock else settings.model),
        mode=("mock" if settings.mock else "api"),
        pipeline_seconds=time.time() - started,
        usage={"total_tokens": client.total_tokens} if client.total_tokens else {},
        validation=validation,
        repairs=[f"第 {repair_rounds} 轮修复"] if repair_rounds else [],
        warnings=warnings,
        mermaid_source=mermaid_code,
    )
    # 原文全文随 payload 一起进看板（前端「原文」标签页按页折叠展示）
    payload["paper"] = _paper_pages_payload(paper)
    # 把溯源校验的真实数字覆盖进 stats，让看板与 CLI 口径一致。
    # verifiedNodeCount 由 build_payload 统一写入，这里只覆盖报告侧数字。
    payload.setdefault("stats", {}).update(
        {
            "evidenceCount": counts["claims"],
            "verifiedCount": counts["verified"],
            "unverifiedCount": counts["not_found"],
            "mismatchCount": counts["mismatched"],
            "pageFixedCount": counts["page_fixed"],
        }
    )
    template, app_js, mermaid_js = load_assets()
    html_path = write_dashboard(payload, output, template=template, app_js=app_js, mermaid_js=mermaid_js)
    stage_seconds["render"] = time.perf_counter() - t_stage
    logger.info(f"看板已生成：{html_path}（{html_path.stat().st_size / 1024:.0f} KB，可离线双击打开）")

    debug_files: list[Path] = []
    if keep_debug:
        debug_files = _dump_debug(analysis, paper, mermaid_code, payload, html_path)

    elapsed = time.time() - started
    logger.info(f"全部完成，用时 {elapsed:.1f} 秒")
    # 可观测性：token 用量与各阶段耗时。默认各一行摘要，-v 时另有每次调用的输入/输出规模。
    if llm_stats:
        logger.info(_llm_summary_line(llm_stats, client.total_tokens, settings.mock))
    logger.info(_timing_summary_line(stage_seconds, elapsed))

    result = PipelineResult(
        html_path=html_path,
        payload=payload,
        analysis=analysis,
        diagram=diagram,
        validation=validation,
        paper=paper,
        report=report,
        repair_rounds=repair_rounds,
        warnings=warnings,
        debug_files=debug_files,
        seconds=elapsed,
    )
    logger.close_log(f"运行成功 · 输出 {html_path.name} · 用时 {elapsed:.1f} 秒")
    return result


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _paper_pages_payload(paper: PaperText) -> dict:
    """组装 payload 的 ``paper`` 字段：按正文页顺序附上每页原文。

    前端「原文」标签页据此按页折叠展示提取到的论文正文（保留换行与 ``## `` 标题标记，
    排版由前端负责）。只读操作，不改动 paper；页面文本缺失时该页直接跳过。
    """
    pages: list[dict] = []
    for page_no in paper.body_pages:
        text = paper.page_texts[page_no - 1] if page_no - 1 < len(paper.page_texts) else ""
        text = text.strip()
        if text:
            pages.append({"page": page_no, "text": text})
    return {"pages": pages}


def _default_output(pdf_path: Path, settings: Settings) -> Path:
    if settings.output_html:
        return Path(settings.output_html).expanduser()
    from .config import PROJECT_ROOT

    return PROJECT_ROOT / "outputs" / f"{pdf_path.stem}.dashboard.html"


def _verify(analysis: dict, paper: PaperText, settings: Settings, logger: Logger):
    """溯源校验。返回 ``(校验后的 analysis, 报告)``。

    注意：``verify.py::verify_analysis`` 为了保证不改写入参，返回的是 **副本**。
    调用方必须使用返回的副本，否则"页码已纠正""引用已清空"这些结果都拿不到，
    前端节点详情里也就没有溯源信息（这是一个真实踩过的坑）。
    内置的 ``verify_against_paper`` 是就地修改，这里统一转成同样的返回形式。
    """
    try:
        from .verify import verify_analysis  # type: ignore

        checked, report = verify_analysis(analysis, paper, strict=settings.strict_citation)
        if not isinstance(checked, dict):
            raise TypeError("verify_analysis 未返回分析字典")
        logger.info(report.summary() if hasattr(report, "summary") else "证据溯源完成")
        _log_suspects(report, logger)
        return checked, report
    except ImportError:
        report = verify_against_paper(analysis, paper, strict=settings.strict_citation)
        logger.info(report.summary())
        _log_suspects(report, logger)
        return analysis, report
    except Exception as exc:  # noqa: BLE001 - 校验失败不应中断主流程
        logger.warn(f"溯源校验模块异常，已退回内置校验：{truncate(str(exc), 160)}")
        report = verify_against_paper(analysis, paper, strict=settings.strict_citation)
        logger.info(report.summary())
        return analysis, report


def _report_counts(report) -> dict:
    """从任意校验报告实现里取出统一口径的计数。"""
    claims = int(getattr(report, "total_claims", 0) or 0)
    return {
        "claims": claims,
        "verified": int(getattr(report, "verified", 0) or 0),
        "not_found": int(getattr(report, "not_found", 0) or 0),
        "page_fixed": int(getattr(report, "page_fixed", 0) or 0),
        "mismatched": int(getattr(report, "mismatched_pages", 0) or 0),
        "suspects": len(getattr(report, "suspects", None) or []),
    }


def _log_suspects(report, logger: Logger) -> None:
    suspects = getattr(report, "suspects", None) or []
    if not suspects:
        return
    logger.warn(f"有 {len(suspects)} 条引用需要留意：")
    for item in suspects[:5]:
        quote = item.get("quote") or item.get("evidence") or ""
        action = item.get("action", "")
        path = item.get("path", "")
        logger.warn(f"    · {path} → {action}：{truncate(str(quote), 46)}")
    if len(suspects) > 5:
        logger.warn(f"    · 其余 {len(suspects) - 5} 条见调试输出")


# ---------------------------------------------------------------------------
# 第一轮输入构造与可观测性
# ---------------------------------------------------------------------------
# 摘要/结论等"含金量高"的章节标题；命中页在正文超限时优先完整保留
_PRIORITY_HEADINGS_RE = re.compile(
    r"abstract|summary|conclusion|discussion|limitation|implication|摘要|总结|结论|讨论|局限|展望",
    re.IGNORECASE,
)
_EXCERPT_MARKER = "\n[本页仅展示节选，部分内容省略]\n"


def _split_paper_chunks(paper: PaperText, char_limit: int) -> list[str]:
    """按页顺序分批；单页过长时原样续切，保证正文字符不丢失。"""
    budget = max(1000, char_limit - 7000)
    chunks: list[str] = []
    current = ""
    for page in paper.body_pages:
        body = paper.page_texts[page - 1] if page - 1 < len(paper.page_texts) else ""
        offset = 0
        while offset < len(body):
            marker = f"\n<<<PAGE {page}>>>\n"
            if len(current) + len(marker) >= budget:
                if current:
                    chunks.append(current)
                current = ""
            room = budget - len(current) - len(marker)
            if room <= 0:
                raise ValueError("分批输入预算不足，至少需要容纳页码标记。")
            piece = body[offset:offset + room]
            current += marker + piece
            offset += len(piece)
            if offset < len(body):
                chunks.append(current)
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _analyze_long_paper(
    paper: PaperText, settings: Settings, client: LLMClient, logger: Logger,
    llm_stats: list[dict],
) -> tuple[dict, LLMResult, str]:
    """逐批读完整篇正文，必要时分层压缩证据笔记，最后综合分析。"""
    chunks = _split_paper_chunks(paper, settings.max_input_chars)
    logger.step(f"[2/5] 长文分批分析：正文 {paper.char_count} 字符，分 {len(chunks)} 批完整读取")
    notes: list[dict] = []
    for index, body in enumerate(chunks, start=1):
        prompt = build_chunk_prompt(paper, body, index, len(chunks))
        logger.info(f"读取第 {index}/{len(chunks)} 批（{len(body)} 字符）")
        result, response = client.complete_json(
            CHUNK_SYSTEM_PROMPT, prompt, validate=validate_chunk_findings, max_tokens=3000
        )
        notes.append(result)
        _record_llm_call(llm_stats, f"分批{index}", CHUNK_SYSTEM_PROMPT, prompt, response)

    # 对极长论文按组递归归纳，避免最后一轮再次超过输入预算。
    max_prompt_chars = max(5000, settings.max_input_chars - len(SYSTEM_PROMPT))
    prompt = build_synthesis_prompt(paper, notes)
    level = 0
    while len(prompt) > max_prompt_chars and len(notes) > 1:
        level += 1
        if level > 12:
            raise LLMError("长文证据笔记无法压入综合输入预算，请调大 MAX_INPUT_CHARS。")
        condensed: list[dict] = []
        for start in range(0, len(notes), 3):
            group = notes[start:start + 3]
            summary_prompt = (
                "请将以下连续批次的发现去重归纳为 8-18 条关键 findings；"
                "保留有辨别力的原文摘录与页码，不新增事实。只输出 JSON。\n"
                + json.dumps(group, ensure_ascii=False, separators=(",", ":"))
            )
            summary, response = client.complete_json(
                CHUNK_SYSTEM_PROMPT, summary_prompt,
                validate=validate_chunk_findings, max_tokens=3000,
            )
            condensed.append(summary)
            _record_llm_call(llm_stats, f"归纳{level}", CHUNK_SYSTEM_PROMPT, summary_prompt, response)
        notes = condensed
        prompt = build_synthesis_prompt(paper, notes)
        logger.info(f"证据笔记归纳第 {level} 轮：剩余 {len(notes)} 组")
    if len(prompt) > max_prompt_chars:
        raise LLMError("长文综合输入仍超过预算，请调大 MAX_INPUT_CHARS。")
    analysis, result = client.complete_json(
        SYSTEM_PROMPT, prompt, validate=validate_analysis, repair_hint=REPAIR_HINT,
    )
    return analysis, result, prompt


def _build_analysis_index(paper: PaperText, char_limit: int, logger: Logger) -> str:
    """构造第一轮发送给模型的正文索引。

    正文不超限时沿用 ``parse.build_evidence_index`` 的原文；超限触发截断时，
    改用"优先截断"策略：摘要 / 结论 / 讨论等章节所在页（外加首页与末页）
    尽量完整保留，其余页均分剩余预算做前后节选，并在日志里说明取舍。
    """
    index_text = paper.build_evidence_index(char_limit)
    if len(index_text) >= paper.char_count:
        return index_text

    pages: list[tuple[int, str]] = []
    for page in paper.body_pages:
        text = paper.page_texts[page - 1] if page - 1 < len(paper.page_texts) else ""
        if text.strip():
            pages.append((page, text.strip()))
    if not pages:
        return index_text

    heading_pages = {h.page for h in paper.headings if _PRIORITY_HEADINGS_RE.search(h.text or "")}
    priority = [item for item in pages if item[0] in heading_pages or item[0] in (pages[0][0], pages[-1][0])]
    rest = [item for item in pages if item not in priority]

    budget = max(
        0,
        char_limit
        - sum(len(f"\n<<<PAGE {page}>>>\n") for page, _ in pages)
        - len(_EXCERPT_MARKER) * len(pages)
        - len(pages),
    )
    priority_quotas: dict[int, int] = {}
    if priority:
        priority_full = sum(len(text) for _, text in priority)
        if priority_full <= budget // 2:
            priority_quotas = {page: len(text) for page, text in priority}  # 全文保留
        else:
            share = budget // 2 // len(priority)
            priority_quotas = {page: min(len(text), share) for page, text in priority}
    rest_budget = max(0, budget - sum(priority_quotas.values()))
    per_rest = rest_budget // len(rest) if rest else 0

    parts: list[str] = []
    for page, text in pages:
        quota = min(priority_quotas.get(page, per_rest), len(text))
        if quota >= len(text):
            excerpt = text
        elif quota >= 80:
            front = max(1, round(quota * 0.7))
            excerpt = text[:front] + _EXCERPT_MARKER + text[-(quota - front):]
        else:
            excerpt = text[:quota] + _EXCERPT_MARKER
        parts.append(f"\n<<<PAGE {page}>>>\n{excerpt}\n")
    result = "".join(parts).strip()[:char_limit]

    full_pages = [page for page, text in priority if priority_quotas.get(page, 0) >= len(text)]
    priority_pages = [page for page, _ in priority]
    if full_pages:
        detail = f"摘要/结论相关页 {full_pages} 整页保留"
    elif priority_pages:
        detail = f"预算紧张，摘要/结论相关页 {priority_pages} 已获得高于其他页的配额"
    else:
        detail = "未识别到摘要/结论标题，全部页面均衡节选"
    logger.warn(
        f"正文较长（{paper.char_count} 字符 > 预算 {char_limit}），已启用优先截断：{detail}，"
        f"其余 {len(rest)} 页均衡节选；共发送 {len(result)} 字符"
    )
    return result


def _record_llm_call(stats: list[dict], stage: str, system: str, user: str, result: LLMResult) -> None:
    """记录一次 LLM 调用的输入/输出规模与 token 口径（实测优先，缺失则估算）。"""
    usage = result.usage or {}
    in_tokens = int(usage.get("prompt_tokens") or 0)
    out_tokens = int(usage.get("completion_tokens") or 0)
    measured = bool(in_tokens or out_tokens)
    if not measured:
        in_tokens = estimate_tokens(system) + estimate_tokens(user)
        out_tokens = estimate_tokens(result.text)
    stats.append(
        {
            "stage": stage,
            "in_chars": len(system) + len(user),
            "out_chars": len(result.text),
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "measured": measured,
        }
    )


_STAGE_LABELS = {"analysis": "分析轮", "audience": "解读轮", "mermaid_fix": "修复轮"}
_STAGE_ORDER = [("parse", "解析"), ("analysis", "分析"), ("audience", "解读"),
                ("verify", "溯源"), ("diagram", "导图"), ("render", "渲染")]


def _llm_summary_line(stats: list[dict], total_tokens: int, mock: bool) -> str:
    """默认日志的一行 token 摘要。"""
    parts = [
        f"{_STAGE_LABELS.get(item['stage'], item['stage'])}"
        f" 输入≈{item['in_tokens']}/输出≈{item['out_tokens']} tok"
        for item in stats
    ]
    estimated = sum(item["in_tokens"] + item["out_tokens"] for item in stats)
    if total_tokens:
        detail = f"合计≈{estimated} tok（API 实测 {total_tokens} tok，其余为估算）"
    elif mock:
        detail = f"合计≈{estimated} tok（估算值；离线演示未调用 API）"
    else:
        detail = f"合计≈{estimated} tok（估算值，API 未返回 usage）"
    return "LLM tokens：" + "；".join(parts) + "；" + detail


def _timing_summary_line(stage_seconds: dict[str, float], elapsed: float) -> str:
    """默认日志的一行各阶段耗时摘要。"""
    parts = [f"{name} {stage_seconds[key]:.1f}s" for key, name in _STAGE_ORDER if key in stage_seconds]
    for key, value in stage_seconds.items():
        if key not in {order_key for order_key, _ in _STAGE_ORDER}:
            parts.append(f"{key} {value:.1f}s")
    parts.append(f"总计 {elapsed:.1f}s")
    return "各阶段耗时：" + "｜".join(parts)


def _collect_terms(analysis: dict, limit: int = 12) -> tuple[list[str], list[str]]:
    """收集需要通俗解读的概念。

    返回 ``(terms, required_terms)``：
    - terms：全部待解读概念 = 分析 glossary 里的术语 + 方法/论证环节中的英文标签，上限 limit；
    - required_terms：其中来自 glossary 的术语，作为第二轮"必须全覆盖"的数量下限
      （数量由分析内容决定，概念越多下限越高，防止模型只挑一两个敷衍）。
    """
    required: list[str] = []
    glossary = analysis.get("glossary")
    if isinstance(glossary, list):
        for item in glossary:
            if isinstance(item, dict):
                term = str(item.get("term") or "").strip()
                if term and term not in required:
                    required.append(term)
    terms = list(required)
    for key in ("method", "argument_flow"):
        for item in analysis.get(key) or []:
            if isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                if label and len(label) <= 20 and label not in terms and has_cjk(label) is False:
                    terms.append(label)
    terms = terms[:limit]
    return terms, [term for term in required if term in terms]


def _merge_audience(analysis: dict, audience: dict) -> dict:
    """第二轮结果覆盖第一轮的通俗解释，保留原有证据与页码。

    ``story``（"这篇论文在做一件什么事"的整体叙述）写入 ``one_sentence_summary``：
    payload 字段名与结构形状不变，只是把一句话总结升级为更充实的整体叙述。
    """
    merged = dict(analysis)
    story = str(audience.get("story") or "").strip()
    if story:
        merged["one_sentence_summary"] = story
    incoming = audience.get("glossary") or []
    by_term = {
        str(item.get("term", "")).strip(): item for item in incoming if isinstance(item, dict) and item.get("term")
    }
    existing = analysis.get("glossary") or []
    result: list[dict] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        replacement = by_term.pop(term, None)
        if replacement:
            updated = dict(item)
            for src, dst in (("plain", "plain"), ("analogy", "analogy"), ("why_it_matters", "why_it_matters")):
                if str(replacement.get(src) or "").strip():
                    updated[dst] = str(replacement[src]).strip()
            result.append(updated)
        else:
            result.append(item)
    for leftover in by_term.values():
        result.append(
            {
                "term": leftover.get("term"),
                "plain": leftover.get("plain", ""),
                "analogy": leftover.get("analogy", ""),
                "why_it_matters": leftover.get("why_it_matters", ""),
                "page": leftover.get("page"),
            }
        )
    merged["glossary"] = result
    return merged


def _llm_repair_mermaid(
    client: LLMClient, code: str, errors: list[str], logger: Logger, *, stats: list[dict] | None = None
) -> tuple[str, bool]:
    """二次调用大模型修复 Mermaid；输出仍要过本地校验，通过才采纳。"""
    system = "你是 Mermaid 语法修复专家。只输出修复后的 Mermaid 代码，不要任何解释文字或代码围栏。"
    user = (
        "下面这段 Mermaid flowchart 代码存在语法错误，请修复它。\n\n"
        "必须遵守：\n"
        "1. 首行保持 `flowchart TD`；\n"
        "2. 保持原有的节点 id、层级顺序与中文标签语义不变，不要删节点；\n"
        "3. 标签里的括号、引号、竖线等特殊字符要么删除、要么整体用双引号包裹；\n"
        "4. subgraph 必须成对闭合；\n"
        "5. 只输出代码本身。\n\n"
        f"校验器报告的错误：\n- " + "\n- ".join(errors) + "\n\n"
        f"原始代码：\n{code}\n"
    )
    try:
        result = client.complete(system, user, temperature=0.0, max_tokens=4096)
        if stats is not None:
            _record_llm_call(stats, "mermaid_fix", system, user, result)
        candidate = _strip_fence(result.text)
        check = validate_mermaid(candidate)
        if check.ok:
            return candidate, True
        logger.warn(f"大模型修复后仍未通过校验：{'；'.join(check.errors[:2])}")
        return candidate, False
    except LLMError as exc:
        logger.warn(f"大模型修复调用失败：{truncate(str(exc), 160)}")
        return code, False


def _strip_fence(text: str) -> str:
    match = re.search(r"```(?:mermaid)?\s*(.*?)```", text or "", re.DOTALL)
    body = match.group(1) if match else (text or "")
    return body.strip()


def _dump_debug(
    analysis: dict, paper: PaperText, mermaid_code: str, payload: dict, html_path: Path
) -> list[Path]:
    out_dir = html_path.parent / f"{html_path.stem}.debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def dump(name: str, content: str) -> None:
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)

    dump("01_parsed.json", json.dumps(paper.to_debug_dict(), ensure_ascii=False, indent=2))
    dump("02_analysis.json", json.dumps(analysis, ensure_ascii=False, indent=2))
    dump("03_diagram.mmd", mermaid_code)
    dump("04_payload.json", json.dumps(payload, ensure_ascii=False, indent=2))
    dump("05_body.txt", paper.full_text)
    return written


def load_analysis_file(path: str | Path) -> dict:
    """读取一份已保存的分析 JSON（用于复现 / 离线调试）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("分析 JSON 的顶层必须是对象。")
    return data


def render_from_analysis(
    analysis_path: str | Path,
    pdf_path: str | Path,
    output_path: str | Path,
    *,
    settings: Settings | None = None,
    logger: Logger | None = None,
) -> PipelineResult:
    """跳过 LLM 调用，直接用已有分析 JSON 重新渲染看板（便于调前端）。"""
    settings = settings or Settings.from_env()
    logger = logger or Logger("info")
    logger.bind_file(create_run_log())
    analysis = load_analysis_file(analysis_path)
    paper = parse_pdf_with_retry(pdf_path, settings, logger=logger)
    analysis, report = _verify(analysis, paper, settings, logger)
    diagram = build_diagram(analysis)
    attach_verification(diagram, analysis)
    mermaid_code = diagram.to_mermaid()
    validation = validate_mermaid(mermaid_code)
    payload = build_payload(
        analysis,
        diagram,
        source_name=Path(pdf_path).name,
        page_count=paper.page_count,
        body_pages=len(paper.body_pages),
        char_count=paper.char_count,
        two_column_pages=paper.two_column_pages,
        model="(离线渲染)",
        mode="offline",
        validation=validation,
        mermaid_source=mermaid_code,
    )
    # 原文全文随 payload 一起进看板（前端「原文」标签页按页折叠展示）
    payload["paper"] = _paper_pages_payload(paper)
    # 与主流程同口径：渲染前用报告侧数字覆盖统计（verifiedNodeCount 由 build_payload 写入）
    payload.setdefault("stats", {}).update(
        {
            "evidenceCount": report.total_claims,
            "verifiedCount": report.verified,
            "unverifiedCount": report.not_found,
            "mismatchCount": report.mismatched_pages,
            "pageFixedCount": report.page_fixed,
        }
    )
    template, app_js, mermaid_js = load_assets()
    html_path = write_dashboard(payload, output_path, template=template, app_js=app_js, mermaid_js=mermaid_js)
    logger.info(f"已从 {Path(analysis_path).name} 渲染：{html_path}")
    logger.close_log(f"离线渲染成功 · 输出 {html_path.name}")
    return PipelineResult(
        html_path=html_path,
        payload=payload,
        analysis=analysis,
        diagram=diagram,
        validation=validation,
        paper=paper,
        report=report,
    )


# check_environment 已移至 src/bootstrap.py：自检必须在缺依赖时也能运行，
# 不能反过来先被缺失的依赖卡在导入阶段。此处保留再导出以兼容旧引用。
from .bootstrap import check_environment  # noqa: E402,F401
