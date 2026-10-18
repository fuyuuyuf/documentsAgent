"""证据溯源校验（反幻觉层）：把模型给出的每一条引用拿回论文原文定位、纠错、留痕。

对应评分项"降低幻觉 / 证据可溯源"，本模块做四件事：

  1. 遍历分析结果里**所有**携带 evidence 的位置：``research_problem``、六个分节条目、
     ``glossary``，逐条构造 ``path``（如 ``contributions[2].evidence``）便于审计；
  2. 用 :class:`~src.textutils.CitationVerifier` 把每条引用拿回论文正文定位，
     记录 found / page / score / method / mismatch，并写回条目的 ``verify`` 字段（前端契约）；
  3. 做确定性修复：纠正错误页码、补全缺失页码、清空占位引用、裁剪超长引文，
     在 ``strict`` 模式下清空未定位到的引用（但**绝不删除条目本身**）；
  4. 产出 :class:`VerificationReport` 审计报告（统计 + suspects 逐条明细）与一行中文摘要。

硬性约束：
  * 不修改传入的 ``analysis``（内部先深拷贝）与 ``paper``（只读；尤其不往 ``paper.stats`` 里塞东西）；
  * ``CitationVerifier`` 只在入口构造一次——它内部要为全文建索引，逐条重建会慢一个数量级；
  * 只用标准库 + 同目录的 ``textutils`` / ``parse``；``parse`` 只在类型检查与 CLI 里延迟导入，
    这样 ``import src.verify`` 不会硬依赖 PyMuPDF。

已知假设（``CitationVerifier.verify()`` 的返回契约）：返回 dict 含
``{found, page, score, method, mismatch}``，**不包含命中位置**；因此裁剪超长引文时
本模块用 ``getattr`` 保守探测其内部规范化正文 ``_norm`` 来取"与原文逐字一致"的窗口，
探测失败则退回 :func:`~src.textutils.middle_ellipsis`。探测失败不影响任何计数。
命中后的"简要上下文"来自新增的 ``CitationVerifier.window()``（只读方法，
定位策略与 ``verify()`` 一致），任何异常都只会让 context 缺席，不影响溯源结果。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 允许 `python src/verify.py ...` 直接运行（脚本方式下相对导入会失效，这里补一个包上下文）
if __name__ == "__main__" and __package__ in (None, ""):  # pragma: no cover - 仅脚本入口
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"

from .textutils import CitationVerifier, middle_ellipsis, normalize, truncate

if TYPE_CHECKING:  # 仅类型检查时导入：避免 parse → … 的循环导入与 PyMuPDF 硬依赖
    from .parse import PaperText


__all__ = [
    "VerificationReport",
    "verify_analysis",
    "format_suspects",
    "is_placeholder_evidence",
    "EVIDENCE_SECTIONS",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
#: 需要逐条检查 evidence 的分节键；与 prompts.py 的 schema、diagram.py 的分区一一对应
EVIDENCE_SECTIONS: tuple[str, ...] = (
    "contributions",
    "method",
    "argument_flow",
    "evidence_and_results",
    "conclusions",
    "limitations",
)

#: "看着像引用、其实没有信息量"的占位写法（比对时统一小写并去掉首尾标点）
PLACEHOLDER_EVIDENCE: frozenset[str] = frozenset(
    {
        "",
        "无",
        "n/a",
        "na",
        "none",
        "null",
        "未知",
        "占位",
        "todo",
        "tbd",
        "-",
        "—",
        "未提及",
        "论文未提及",
        "未说明",
        "论文未说明",
        "论文未明确说明",
        "not mentioned",
        "not specified",
    }
)

#: 前缀型占位写法，例如 "占位1" / "TODO：待补"
PLACEHOLDER_PREFIXES: tuple[str, ...] = ("占位", "todo", "tbd", "待补", "略")

#: 短于该长度的"引用"无法定位，直接按空白处理（与 CitationVerifier 的 6 字下限一致）
MIN_EVIDENCE_CHARS = 6
#: 超过该长度的引文会被裁成最佳匹配窗口（导图节点放不下，也不利于人工核对）
MAX_EVIDENCE_CHARS = 300
#: 匹配度低于该阈值时不敢改写模型标注的页码（弱匹配更可能是碰巧）
MIN_PAGE_FIX_SCORE = 0.6
#: suspects 里引文摘要的截断长度
SUSPECT_QUOTE_CHARS = 60

#: 前端契约要求的 verify 字段（docs/frontend-contract.md）。
#: context 只在定位成功后写入：命中位置前后各一段原文，前端以可折叠形式展示。
VERIFY_KEYS: tuple[str, ...] = ("found", "page", "score", "method", "mismatch", "context")

#: suspects 的 action 取值（固定四种，避免前端/文档对不上）
ACTION_KEPT = "保留"
ACTION_PAGE_FIXED = "已修正页码"
ACTION_NOT_FOUND = "未定位到"
ACTION_CLEARED = "已清空"

#: 判断占位写法时需要忽略的首尾标点
_TRIM_PUNCT = " \t\r\n.。，,;；:：!！?？\"'“”‘’()（）[]【】《》<>-—_…、"


# ---------------------------------------------------------------------------
# 审计报告
# ---------------------------------------------------------------------------
@dataclass
class VerificationReport:
    """一次溯源校验的审计结果（可安全序列化进 payload / 调试输出）。"""

    total_claims: int = 0
    """检查过的证据条数（真正送进 CitationVerifier 的非空引用）。"""

    verified: int = 0
    """found=True 的条数。"""

    not_found: int = 0
    """found=False 且引用非空的条数（疑似改写或编造）。"""

    mismatched_pages: int = 0
    """模型标注页码与实际命中页码不一致的条数（含已纠正与因匹配度过低而保留的）。"""

    page_fixed: int = 0
    """被自动纠正/补全页码的条数。"""

    empty_evidence: int = 0
    """空白或占位引用（"无" / "N/A" / "论文未明确说明" / 短于 6 字）的条数。"""

    suspects: list[dict] = field(default_factory=list)
    """逐条问题明细：path / quote / page_claimed / page_found / score / method / action (+note)。"""

    def summary(self) -> str:
        """一行中文摘要，直接打进控制台 / README 调试输出。"""
        head = (
            f"证据 {self.total_claims} 条：已溯源 {self.verified}、未定位 {self.not_found}、"
            f"页码纠正 {self.page_fixed}、页码不符 {self.mismatched_pages}、"
            f"空白/占位 {self.empty_evidence}"
        )
        tail = f"；可疑明细 {len(self.suspects)} 条" if self.suspects else "；未发现可疑引用"
        return head + tail

    def __str__(self) -> str:  # 方便 print(report)
        return self.summary()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def verify_analysis(
    analysis: dict,
    paper: PaperText,
    *,
    strict: bool = False,
    verbose: bool = False,
) -> tuple[dict, VerificationReport]:
    """就地在副本上校验 / 修正 analysis，返回 (修正后的分析副本, 审计报告)。

    传入的 ``analysis`` 与 ``paper`` 都不会被修改：
      * analysis 先做深拷贝，所有修复只作用于副本；
      * paper 只读（``full_text`` / ``page_spans``），不会写入任何统计字段。

    :param strict: 未在原文定位到的引用会被清空为 ``""`` 并标记 ``verify["dropped"]=True``（条目不删）。
    :param verbose: 打印逐条进度到 stderr，便于比赛现场排查。
    """
    report = VerificationReport()
    if not isinstance(analysis, dict):
        # 结构完全不合法时不做任何猜测，交给上层校验器处理
        return {}, report

    working: dict = copy.deepcopy(analysis)

    paper_text = getattr(paper, "full_text", "") or ""
    page_spans = list(getattr(paper, "page_spans", None) or [])
    # 只构造一次：CitationVerifier 内部会建规范化索引与压缩索引，逐条重建会非常慢
    verifier = CitationVerifier(paper_text, page_spans)

    log = _make_log(verbose)
    log(
        f"开始溯源校验：正文 {len(paper_text)} 字符 / {len(page_spans)} 个页面区间"
        f"{'（strict 模式）' if strict else ''}"
    )

    for container, slot in _iter_evidence_slots(working):
        _check_slot(container, slot, verifier, report, strict=strict, log=log)

    log(report.summary())
    return working, report


def format_suspects(report: VerificationReport, limit: int = 10) -> str:
    """把 suspects 渲染成人类可读的中文清单，供控制台 / README 调试输出使用。"""
    suspects = list(getattr(report, "suspects", None) or [])
    if not suspects:
        return "引用校验：未发现可疑引用。"
    limit = max(0, int(limit))
    lines = [f"引用校验明细（共 {len(suspects)} 条，最多展示 {limit} 条）："]
    for index, item in enumerate(suspects[:limit], start=1):
        path = str(item.get("path") or "(未知位置)")
        action = str(item.get("action") or "")
        quote = truncate(str(item.get("quote") or ""), 42) or "(空)"
        method = str(item.get("method") or "none")
        score = float(item.get("score") or 0.0)
        lines.append(f"  {index}. {path} → {action}{_describe_pages(item)}：{quote}")
        lines.append(f"      · method={method} score={score:.2f}")
        if item.get("note"):
            lines.append(f"      · {item['note']}")
    if len(suspects) > limit:
        lines.append(f"  … 其余 {len(suspects) - limit} 条见 report.suspects")
    return "\n".join(lines)


def is_placeholder_evidence(quote: Any) -> bool:
    """判断 evidence 是否是"没有信息量"的空白/占位值。

    规则（与评分要求一致）：非字符串、空串、纯标点、
    :data:`PLACEHOLDER_EVIDENCE` 里的固定写法、``占位1`` 之类的前缀写法，
    以及 normalize() 之后短于 :data:`MIN_EVIDENCE_CHARS` 的字符串。

    注意：只用于 ``evidence`` 字段；``detail`` 里合法出现的"论文未明确说明"不受影响。
    """
    if not isinstance(quote, str):
        return True
    cleaned = normalize(quote)
    if len(cleaned) < MIN_EVIDENCE_CHARS:
        return True
    folded = cleaned.lower().strip(_TRIM_PUNCT)
    if not folded or folded in PLACEHOLDER_EVIDENCE:
        return True
    return any(folded.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES)


# ---------------------------------------------------------------------------
# 遍历与单条处理
# ---------------------------------------------------------------------------
def _iter_evidence_slots(analysis: dict) -> Iterator[tuple[dict, str]]:
    """产出 (证据所在字典, 位置标签)，位置标签形如 ``contributions[2]`` / ``research_problem``。"""
    problem = analysis.get("research_problem")
    if isinstance(problem, dict):
        yield problem, "research_problem"

    for key in EVIDENCE_SECTIONS:
        items = analysis.get(key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                yield item, f"{key}[{index}]"

    glossary = analysis.get("glossary")
    if isinstance(glossary, list):
        for index, item in enumerate(glossary):
            if isinstance(item, dict):
                yield item, f"glossary[{index}]"


def _check_slot(
    container: dict,
    slot: str,
    verifier: CitationVerifier,
    report: VerificationReport,
    *,
    strict: bool,
    log: Callable[[str], None],
) -> None:
    """检查单个 evidence 槽位并按需修复（container 已经是 analysis 的副本，可放心改）。"""
    if "evidence" not in container:
        # 该条目压根没有引用字段（例如第二轮补充的术语卡），不算"空白引用"，也不进统计
        return

    raw = container.get("evidence")
    quote = raw if isinstance(raw, str) else ""
    cleaned = normalize(quote)
    claimed = _as_page(container.get("page"))

    # ---- 1) 空白 / 占位引用：清空，不留假证据 ----
    if is_placeholder_evidence(quote):
        report.empty_evidence += 1
        container["evidence"] = ""
        if claimed is None and "page" in container:
            container["page"] = None  # 顺手把 ""/垃圾页码归一成 null，符合前端契约
        if cleaned:
            # 纯空白只计数；"无"/"N/A"/"论文未明确说明"这类伪装成引用的必须留痕
            report.suspects.append(
                _suspect(slot, cleaned, claimed, None, 0.0, "empty", ACTION_CLEARED, "占位引用，已清空")
            )
            log(f"{slot}: 占位引用已清空 → {truncate(cleaned, 30)}")
        return

    # ---- 2) 回原文定位 ----
    result = verifier.verify(quote, claimed)
    measured = _as_page(result.get("page"))
    entry = {
        "found": bool(result.get("found")),
        "page": measured,
        "score": round(float(result.get("score") or 0.0), 3),
        "method": str(result.get("method") or "none"),
        "mismatch": bool(claimed and measured and claimed != measured),
    }
    report.total_claims += 1

    action = ACTION_KEPT
    notes: list[str] = []

    if not entry["found"]:
        # ---- 3a) 未定位到：模型改写或编造 ----
        report.not_found += 1
        action = ACTION_NOT_FOUND
        notes.append(f"原文未定位到（method={entry['method']}、score={entry['score']:.2f}）")
        if strict:
            container["evidence"] = ""
            entry["dropped"] = True  # 前端/调试可据此提示"strict 模式已丢弃"
            action = ACTION_CLEARED
    else:
        report.verified += 1
        # 命中后附带简要上下文（命中位置前后各一段原文），前端可折叠展示
        context = _lookup_context(quote, verifier)
        if context:
            entry["context"] = context
        if measured is None:
            # 极少数情况：压缩匹配命中了但页码无法回溯，保守不改页码
            notes.append("已定位但无法回溯页码，保留模型标注页码")
        elif claimed is None:
            container["page"] = measured
            report.page_fixed += 1
            action = ACTION_PAGE_FIXED
            notes.append(f"补全页码 p.{measured}")
        elif claimed != measured:
            report.mismatched_pages += 1
            if entry["score"] >= MIN_PAGE_FIX_SCORE:
                # 只有匹配足够强才敢改写模型给的页码
                container["page"] = measured
                report.page_fixed += 1
                action = ACTION_PAGE_FIXED
                notes.append(f"页码 p.{claimed} → p.{measured}")
            else:
                notes.append(f"匹配度 {entry['score']:.2f} < {MIN_PAGE_FIX_SCORE}，保留模型页码 p.{claimed}")

    # ---- 4) 超长引文裁剪（strict 下已清空的直接跳过）----
    if container.get("evidence") and len(cleaned) > MAX_EVIDENCE_CHARS:
        container["evidence"] = _best_window(cleaned, verifier)
        notes.append(f"引文 {len(cleaned)} 字过长，已裁到 {MAX_EVIDENCE_CHARS} 字窗口")

    # ---- 5) 写回前端契约字段：found / page / score / method / mismatch（+ strict 丢弃标记）----
    container["verify"] = entry

    if action == ACTION_KEPT and not notes:
        log(f"{slot}: 命中 p.{measured}（{entry['method']}）")
        return

    report.suspects.append(
        _suspect(slot, cleaned, claimed, measured, entry["score"], entry["method"], action, "；".join(notes))
    )
    log(f"{slot}: {action} —— {'；'.join(notes)}")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _suspect(
    slot: str,
    quote: str,
    claimed: int | None,
    measured: int | None,
    score: float,
    method: str,
    action: str,
    note: str = "",
) -> dict:
    """构造一条 suspects 明细（字段顺序固定，便于打印与前端展示）。"""
    item = {
        "path": f"{slot}.evidence",
        "quote": truncate(normalize(quote), SUSPECT_QUOTE_CHARS),
        "page_claimed": claimed,
        "page_found": measured,
        "score": round(float(score or 0.0), 3),
        "method": method,
        "action": action,
    }
    if note:
        item["note"] = note
    return item


def _lookup_context(quote: str, verifier: CitationVerifier) -> str:
    """提取引文命中位置在原文中的简要上下文（供前端折叠展示）。

    上下文只是增强信息：任何异常都返回空串，绝不影响溯源计数与主流程。
    """
    try:
        context = verifier.window(quote)
    except Exception:  # noqa: BLE001 - 上下文失败绝不阻断溯源
        return ""
    return context if isinstance(context, str) else ""


def _best_window(quote: str, verifier: CitationVerifier, limit: int = MAX_EVIDENCE_CHARS) -> str:
    """把超长引文裁成 limit 字以内的"最佳匹配窗口"。

    ``CitationVerifier.verify()`` 不返回命中位置，这里保守探测其内部规范化正文 ``_norm``：
    命中则返回与原文**逐字一致**的窗口（比中间省略更好用）；探测失败退回 middle_ellipsis。
    """
    cleaned = normalize(quote)
    if len(cleaned) <= limit:
        return cleaned

    corpus = getattr(verifier, "_norm", "") or ""
    if isinstance(corpus, str) and corpus:
        pos = corpus.find(cleaned)
        if pos < 0:
            anchor = cleaned[:120]
            pos = corpus.find(anchor) if anchor else -1
        if pos >= 0:
            window = corpus[pos : pos + limit]
            if len(window) < limit:  # 命中位置太靠后，窗口不足则取全文尾部
                window = corpus[max(0, len(corpus) - limit) :]
            if window.strip():
                return window.strip()
    return middle_ellipsis(cleaned, limit)


def _as_page(value: Any) -> int | None:
    """把模型给的页码归一成 int；无法识别的（None/""/"第三页"/0/布尔）一律返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            number = int(text)
            return number if number > 0 else None
    return None


def _describe_pages(item: dict) -> str:
    """把 page_claimed / page_found 渲染成 "（p.3 → p.5）" 这样的短说明。"""
    claimed = item.get("page_claimed")
    found = item.get("page_found")
    if claimed and found:
        if int(claimed) == int(found):
            return f"（p.{found}）"
        return f"（p.{claimed} → p.{found}）"
    if found:
        return f"（实测 p.{found}）"
    if claimed:
        return f"（模型标注 p.{claimed}）"
    return ""


def _make_log(verbose: bool) -> Callable[[str], None]:
    """极简日志：verbose=False 时是空操作，避免污染主流程输出。"""

    def log(message: str) -> None:
        if verbose:
            _safe_print(f"[verify] {message}", stream=sys.stderr)

    return log


def _safe_print(text: str, stream: Any = None) -> None:
    """Windows 控制台编码兜底：打印失败也绝不让校验流程崩掉。"""
    stream = stream or sys.stdout
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        print(text.encode(encoding, "replace").decode(encoding, "replace"), file=stream, flush=True)


# ---------------------------------------------------------------------------
# 命令行入口（仅在直接运行时执行）
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    """``python -m src.verify paper.pdf analysis.json``：离线复核一份已有分析结果。"""
    parser = argparse.ArgumentParser(
        prog="src.verify",
        description="对已有分析 JSON 做证据溯源校验（不调用大模型、不联网）",
    )
    parser.add_argument("pdf", help="论文 PDF 路径")
    parser.add_argument("analysis", help="分析结果 JSON 路径")
    parser.add_argument("--strict", action="store_true", help="清空未在原文定位到的引用")
    parser.add_argument("--verbose", action="store_true", help="打印逐条进度到 stderr")
    parser.add_argument("--limit", type=int, default=10, help="suspects 展示条数（默认 10）")
    parser.add_argument("--out", default="", help="把修正后的 analysis 另存到该路径（可选）")
    args = parser.parse_args(argv)

    from .parse import parse_pdf  # 延迟导入：import src.verify 时不该硬依赖 PyMuPDF

    try:
        paper = parse_pdf(args.pdf)
        analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _safe_print(f"读取失败：{exc}", stream=sys.stderr)
        return 2

    fixed, report = verify_analysis(analysis, paper, strict=args.strict, verbose=args.verbose)
    _safe_print(report.summary())
    _safe_print(format_suspects(report, limit=args.limit))
    if args.out:
        Path(args.out).write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
        _safe_print(f"修正后的分析已写入：{args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 仅在命令行直接运行时执行
    raise SystemExit(_main())
