"""PDF 解析：坐标级文本提取、页眉页脚过滤、标题与章节识别、分栏检测。

设计要点（对应评分项"解析准确"与"稳定鲁棒"）：
  * 只用 PyMuPDF 一种后端，避免多后端字体/顺序差异带来的不确定性；
  * 以"块"为单位保留 bbox / 字号 / 页码，为后续证据溯源提供坐标；
  * 页眉页脚通过"跨页重复"启发式删除，而不是硬编码位置；
  * 记录正文区间内每个字符属于第几页（page_spans），使引用可回溯页码。
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .textutils import collapse_for_search, has_cjk, normalize

try:  # PyMuPDF >= 1.24 推荐 import pymupdf
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover - 兼容旧包名
    import fitz as pymupdf  # type: ignore

_NUMBERED_HEADING_RE = re.compile(
    r"^(?:(?:第\s*[0-9一二三四五六七八九十]+\s*[章节部分])|"
    r"(?:\d{1,2}(?:\.\d{1,2}){0,2})|"
    r"(?:[IVXLC]{1,5}\.)|"
    r"(?:[一二三四五六七八九十]+\s*[、.．]))\s*[\)\.、．]?\s*\S"
)
_CANONICAL_HEADINGS = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "method",
    "methods",
    "methodology",
    "approach",
    "experiments",
    "experiment",
    "experimental setup",
    "evaluation",
    "results",
    "results and discussion",
    "discussion",
    "conclusion",
    "conclusions",
    "limitations",
    "future work",
    "references",
    "acknowledgments",
    "acknowledgements",
    "appendix",
    "摘要",
    "引言",
    "绪论",
    "相关工作",
    "背景",
    "方法",
    "实验",
    "实验结果",
    "讨论",
    "结论",
    "局限",
    "展望",
    "参考文献",
    "致谢",
    "附录",
}
_STOP_SECTIONS = {"references", "acknowledgments", "acknowledgements", "参考文献", "致谢"}
_APPENDIX_SECTIONS = {"appendix", "附录"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Block:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool
    is_heading: bool = False
    heading_level: int = 0
    section: str = ""
    is_boilerplate: bool = False
    is_footnote: bool = False

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def top(self) -> float:
        return self.bbox[1]

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0


@dataclass
class Heading:
    text: str
    page: int
    level: int


@dataclass
class PaperText:
    """一篇论文的解析结果。"""

    source: str
    page_count: int
    full_text: str
    page_spans: list[tuple[int, int]]
    body_pages: list[int]
    page_texts: list[str] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    title: str = ""
    abstract: str = ""
    two_column_pages: list[int] = field(default_factory=list)
    dropped_boilerplate: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.full_text)

    @property
    def column_ratio(self) -> float:
        if not self.body_pages:
            return 0.0
        return len(self.two_column_pages) / len(self.body_pages)

    def outline_text(self, limit: int = 60) -> str:
        lines = [f"{'  ' * max(0, h.level - 1)}- {h.text}  (p.{h.page})" for h in self.headings[:limit]]
        return "\n".join(lines)

    def build_evidence_index(self, char_limit: int) -> str:
        """构造带页码锚点的正文；超限时从各页均衡取样，保留结论页。"""
        pages: list[tuple[int, str]] = []
        for page in self.body_pages:
            text = self.page_texts[page - 1] if page - 1 < len(self.page_texts) else ""
            if text.strip():
                pages.append((page, text.strip()))
        if not pages:
            return ""

        full = "".join(f"\n<<<PAGE {page}>>>\n{text}\n" for page, text in pages).strip()
        if len(full) <= char_limit:
            return full

        marker = "\n[本页仅展示节选，部分内容省略]\n"
        headers = sum(len(f"\n<<<PAGE {page}>>>\n") for page, _ in pages)
        budget = max(0, char_limit - headers - len(marker) * len(pages) - len(pages))
        low, high = 0, max(len(text) for _, text in pages)
        while low < high:
            middle = (low + high + 1) // 2
            if sum(min(len(text), middle) for _, text in pages) <= budget:
                low = middle
            else:
                high = middle - 1

        parts: list[str] = []
        for page, text in pages:
            quota = min(len(text), low)
            if quota >= len(text):
                excerpt = text
            elif quota >= 80:
                front = max(1, round(quota * 0.7))
                excerpt = text[:front] + marker + text[-(quota - front):]
            else:
                excerpt = text[:quota] + marker
            parts.append(f"\n<<<PAGE {page}>>>\n{excerpt}\n")
        return "".join(parts).strip()[:char_limit]

    def to_debug_dict(self) -> dict:
        return {
            "source": self.source,
            "page_count": self.page_count,
            "body_pages": self.body_pages,
            "char_count": self.char_count,
            "title": self.title,
            "two_column_pages": self.two_column_pages,
            "headings": [{"text": h.text, "page": h.page, "level": h.level} for h in self.headings],
            "dropped_boilerplate": self.dropped_boilerplate[:40],
            "stats": self.stats,
        }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
class ParseQualityError(ValueError):
    """确定性业务错误：PDF 能打开但内容不满足解析前提（如纯扫描件无文本层）。

    继承 ValueError 保持既有捕获兼容；``parse_pdf_with_retry`` 对它不重试。
    """


def parse_pdf(pdf_path: str | Path, settings: Settings | None = None) -> PaperText:
    settings = settings or Settings.from_env()
    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到 PDF 文件：{path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"输入文件不是 PDF：{path.name}")

    doc = _open_pdf(path)
    try:
        pages: list[Page] = [_extract_page(doc, i) for i in range(doc.page_count)]
    finally:
        doc.close()

    if not pages or all(not p.blocks for p in pages):
        raise ParseQualityError(
            "PDF 中没有可提取的文本层（可能是纯扫描件）。"
            "请先用 OCR 工具（如 ocrmypdf）处理，或更换为文字版 PDF。"
        )

    # 先确定阅读顺序（含分栏重排），再做样板过滤——顺序错了样板检测也会跟着错
    # 正文字号提前估计：重排阶段判脚注（栏底小字号块）要用
    body_size = _estimate_body_size(pages)
    reordered_pages = _apply_reading_order(pages, body_size)
    _mark_headings(pages, body_size)

    title = _detect_title(pages)
    # 标题常被拆成多个块，这些块会被误判成章节标题，从大纲里剔除
    _suppress_title_headings(pages, title)

    _mark_boilerplate(pages)
    two_column_pages = _detect_two_column(pages)
    abstract = _detect_abstract(pages)

    body_pages, page_texts, full_text, page_spans, assemble_stats = _assemble(pages)

    dropped = sorted({b.text.strip() for p in pages for b in p.blocks if b.is_boilerplate and b.text.strip()})
    paper = PaperText(
        source=str(path),
        page_count=len(pages),
        full_text=full_text,
        page_spans=page_spans,
        body_pages=body_pages,
        page_texts=page_texts,
        headings=_collect_headings(pages),
        title=title,
        abstract=abstract,
        two_column_pages=two_column_pages,
        dropped_boilerplate=dropped,
        stats={
            "body_font_size": round(body_size, 2),
            "body_chars": len(full_text),
            "raw_chars": sum(len(p.raw_text) for p in pages),
            "boilerplate_blocks": sum(1 for p in pages for b in p.blocks if b.is_boilerplate),
            "boilerplate_lines": assemble_stats["boilerplate_lines"],
            "stripped_lines": assemble_stats["dropped_lines"],
            "truncated_from": assemble_stats["truncated_from"],
            "truncated_pages": assemble_stats["truncated_pages"],
            "headings": len(_collect_headings(pages)),
            "reordered_pages": reordered_pages,
        },
    )
    _check_quality(paper, settings)
    return paper


@dataclass
class Page:
    number: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)

    @property
    def raw_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)

    @property
    def body_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text and not b.is_boilerplate)


def _open_pdf(path: Path):
    try:
        return pymupdf.open(str(path))
    except Exception as exc:  # 加密或损坏
        raise ValueError(f"无法打开 PDF（可能已加密或文件损坏）：{path.name} —— {exc}") from exc


def parse_pdf_with_retry(
    pdf_path: str | Path,
    settings: Settings | None = None,
    *,
    logger=None,
    attempts: int = 3,
    delay: float = 0.8,
) -> PaperText:
    """带重试的解析入口：解析出错时自动重试，默认共尝试 3 次（间隔 0.8s 起步）。

    只重试瞬时类错误（文件被占用 / IO 抖动 / PDF 库内部异常等——文件还在
    拷贝或同步时很常见）。两类错误不重试：``FileNotFoundError``（文件不
    存在）与 ``ParseQualityError``（无文本层等确定性业务错误——该检查发生在
    整页提取之后，重试一次要多付一次完整解析的时间）。
    重试仍失败时抛 ``ValueError``，由调用方给出中文修复建议。
    """
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return parse_pdf(pdf_path, settings)
        except (FileNotFoundError, ParseQualityError):
            raise
        except Exception as exc:  # noqa: BLE001 - 瞬时类错误统一重试
            last_exc = exc
            if attempt >= attempts:
                break
            if logger is not None:
                logger.warn(
                    f"PDF 解析出错（第 {attempt} 次尝试）：{exc} —— "
                    f"{delay:.1f} 秒后重试（剩余 {attempts - attempt} 次）"
                )
            time.sleep(delay)
    raise ValueError(
        f"PDF 解析失败（已重试 {max(1, attempts) - 1} 次）：{last_exc}"
    ) from last_exc


def _extract_page(doc, index: int) -> Page:
    page = doc.load_page(index)
    rect = page.rect
    result = Page(number=index + 1, width=float(rect.width), height=float(rect.height))

    data: dict
    try:
        # 关键：这里刻意**不**用 sort=True。
        # PyMuPDF 的 sort=True 会跨栏按 y 排序，双栏论文会被拆成"左一行、右一行"交错，
        # 阅读顺序彻底错乱。我们取内容流顺序，再用 _order_blocks 做列感知重排。
        data = page.get_text("dict")
    except Exception:
        # 极端情况下退回纯文本模式，保证不崩
        text = page.get_text("text", sort=True) or ""
        for line in text.splitlines():
            if line.strip():
                result.blocks.append(
                    Block(
                        page=index + 1,
                        text=line.strip(),
                        bbox=(0.0, 0.0, float(rect.width), float(rect.height)),
                        size=10.0,
                        bold=False,
                    )
                )
        return result

    for raw_block in data.get("blocks", []):
        if raw_block.get("type") != 0:  # 跳过图片块
            continue
        lines = raw_block.get("lines", [])
        text_parts: list[str] = []
        sizes: list[float] = []
        bold_flags: list[bool] = []
        for line in lines:
            line_text = ""
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text:
                    continue
                line_text += span_text
                size = float(span.get("size") or 0.0)
                if size > 0:
                    sizes.append(size)
                flags = int(span.get("flags") or 0)
                # PyMuPDF flags bit 4 (16) = bold
                bold_flags.append(bool(flags & 16) or "bold" in str(span.get("font", "")).lower())
            line_text = line_text.strip()
            if line_text:
                text_parts.append(line_text)
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        size = statistics.median(sizes) if sizes else 0.0
        bold = bool(bold_flags) and sum(bold_flags) >= max(1, len(bold_flags) // 2)
        bbox = raw_block.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        result.blocks.append(
            Block(
                page=index + 1,
                text=text,
                bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                size=float(size),
                bold=bold,
            )
        )
    # 保留内容流顺序，交给 _apply_reading_order 统一做列感知重排
    return result


# ---------------------------------------------------------------------------
# 阅读顺序（列感知重排 v2）
# ---------------------------------------------------------------------------
# 阈值集中在这里，方便按真实论文调参：
_SPANNING_RATIO = 0.6      # 块宽 >= 内容宽度 60% → 视为通栏块（标题/摘要/图表说明）
_PROMOTE_RATIO = 0.3       # 完全骑跨栏边界的块，宽度 >= 内容宽度 30% → 提升为通栏
_MIN_GAP_RATIO = 0.02      # 分栏空隙最小宽度（占内容宽度）
_MIN_SIDE_CHARS = 0.15     # 每侧字符量下限：块数不足时用它防误分（如居中的作者行）
_FOOTNOTE_SIZE_GAP = 1.0   # 脚注字号至少比正文小 1pt
_FOOTNOTE_TOP_RATIO = 0.6  # 脚注只可能出现在页面下部（y >= 60% 页高）
_FOOTNOTE_MARKER_RE = re.compile(r"^\s*(?:\d{1,2}[^\d\s]|[^\w\s])")
_PAGE_NUMBER_RE = re.compile(
    r"(?:page\s*)?\d{1,4}(?:\s*/\s*\d{1,4})?|\d{4,8}\s*[-–—]\s*\d{1,4}",
    re.IGNORECASE,
)


def _apply_reading_order(pages: list[Page], body_size: float = 0.0) -> int:
    """对每页做列感知重排，返回被重排的页数。"""
    changed = 0
    for page in pages:
        before = [id(b) for b in page.blocks]
        page.blocks = _order_blocks(page.blocks, page.width, page.height, body_size)
        if [id(b) for b in page.blocks] != before:
            changed += 1
    return changed


def _order_blocks(
    blocks: list[Block],
    page_width: float = 0.0,
    page_height: float = 0.0,
    body_size: float = 0.0,
) -> list[Block]:
    """把内容流顺序修正为人类阅读顺序。

    为什么不直接用 PyMuPDF 的 ``sort=True``：它会跨栏按 y 全局排序，
    双栏论文会被拆成"左栏一行、右栏一行"交错，阅读顺序彻底错乱。

    v2 思路（针对双栏论文的三类难点）：
      1. **通栏块先挑出来当分隔带**：横跨页面主体宽度的标题、摘要、图表说明
         不参与分栏，而是把页面切成若干水平"带"，两栏内容在各带内各自成流；
      2. **列边界从块分布里估计**：在 x 覆盖率里找栏间空隙（不假设对称双栏），
         递归分侧还能认出 3 栏；找不到可靠空隙就整页单栏处理，绝不硬猜；
      3. **栏底小字号脚注后置**：脚注（常在左右栏最下方）移到页尾，
         避免把"左栏正文 → 脚注 → 右栏正文"的顺序拦腰截断。

    任何一步出异常都退回旧的贪心分列实现，保证主流程不崩。
    """
    if len(blocks) < 2:
        return list(blocks)
    try:
        reordered = _reflow_with_bands(blocks, page_height, body_size)
    except Exception:
        reordered = None
    if reordered is None:
        try:
            reordered = _order_blocks_legacy(blocks)
        except Exception:
            return list(blocks)
    return reordered


def _sort_blocks_y(blocks: list[Block]) -> list[Block]:
    # list.sort 稳定 → 同 y 保持内容流顺序
    return sorted(blocks, key=lambda b: b.y0)


def _reflow_with_bands(blocks: list[Block], page_height: float, body_size: float) -> list[Block]:
    content_l = min(b.x0 for b in blocks)
    content_r = max(b.x1 for b in blocks)
    content_w = content_r - content_l
    if content_w <= 1.0:
        return _sort_blocks_y(blocks)

    # 1. 通栏块（标题/摘要/跨栏图表说明）不参与分栏，充当"列分隔带"
    spanning = [b for b in blocks if (b.x1 - b.x0) >= content_w * _SPANNING_RATIO]
    flow = [b for b in blocks if (b.x1 - b.x0) < content_w * _SPANNING_RATIO]

    # 2. 用块分布估计列边界，把两栏内容各自归列
    columns, promoted = _detect_columns(flow, content_l, content_r)
    separators = sorted(spanning + promoted, key=lambda b: (b.y0, b.x0))

    # 3. 按 y 从上到下走分隔带：带与带之间的两栏内容先左后右，带自身原位输出。
    #    中心恰好落在分隔带 y 范围内的块按中心归入上/下区域，绝不吸进分隔带——
    #    否则右栏首段会被居中标题/摘要"吞掉"，打断两栏各自成流。
    ordered: list[Block] = []
    sep_ids = {id(b) for b in separators}
    pending = [b for b in flow if id(b) not in sep_ids]  # 提升块已是分隔带，别再进区域流
    for sep in separators:
        sep_center = (sep.y0 + sep.y1) / 2.0
        above: list[Block] = []
        rest: list[Block] = []
        for b in pending:
            center_y = (b.y0 + b.y1) / 2.0
            if center_y < sep.y0:
                above.append(b)
            elif center_y <= sep.y1:
                # 与分隔带同高（如图注旁的碎屑）：按中心归入上/下
                if center_y < sep_center:
                    above.append(b)
                else:
                    rest.append(b)
            else:
                rest.append(b)
        pending = rest
        ordered.extend(_order_region(above, columns))
        ordered.append(sep)
    ordered.extend(_order_region(pending, columns))

    # 4. 栏底脚注后置到页尾，保持"左栏 → 右栏"正文流不被打断
    footnotes = _extract_column_footnotes(columns, page_height, body_size)
    if footnotes:
        for b in footnotes:
            b.is_footnote = True
        footnote_ids = {id(b) for b in footnotes}
        ordered = [b for b in ordered if id(b) not in footnote_ids]
        ordered.extend(footnotes)
    return ordered


def _order_region(region: list[Block], columns: list[list[Block]]) -> list[Block]:
    """对一个水平带内的块排序：确认是平行双栏则先左栏后右栏，否则按 y 排。"""
    if not region:
        return []
    if len(columns) <= 1:
        return _sort_blocks_y(region)
    region_ids = {id(b) for b in region}
    grouped = [[b for b in col if id(b) in region_ids] for col in columns]
    grouped = [g for g in grouped if g]
    if len(grouped) <= 1 or not _columns_run_in_parallel(grouped):
        # 顺序堆叠的内容（如居中作者行）不能按栏切开，整体按 y 排
        return _sort_blocks_y(region)
    ordered: list[Block] = []
    for group in grouped:
        ordered.extend(_sort_blocks_y(group))
    return ordered


def _columns_run_in_parallel(groups: list[list[Block]]) -> bool:
    """判断几组块是否是"平行栏"：至少两列在 y 方向上大范围重叠。

    真双栏的左右两列几乎全程并排；而误分出来的上下堆叠内容（诗行、居中标题）
    在 y 上几乎不重叠，此时必须按 y 排序而不是按栏拼接。
    """
    if len(groups) < 2:
        return False
    extents = [(min(b.y0 for b in g), max(b.y1 for b in g)) for g in groups]
    for i in range(len(extents)):
        for j in range(i + 1, len(extents)):
            (a0, a1), (b0, b1) = extents[i], extents[j]
            overlap = min(a1, b1) - max(a0, b0)
            smaller = min(a1 - a0, b1 - b0)
            if smaller > 0 and overlap >= smaller * 0.4:
                return True
    return False


def _detect_columns(
    flow: list[Block], content_l: float, content_r: float
) -> tuple[list[list[Block]], list[Block]]:
    """用页面内块分布估计列边界。

    成功时返回按 x 从左到右排列的列组（递归分侧，天然支持 2~4 栏），
    以及被提升为通栏的骑跨块（居中作者行、摘要行等）；
    找不到可靠空隙时返回单栏 ``[flow]``，调用方退化为 y 排序。
    """
    promoted: list[Block] = []
    columns = _split_columns_recursive(flow, content_l, content_r, promoted, depth=0)
    if not columns:
        # 分栏失败：已提升的骑跨块仍作为分隔带，但不能重复出现在列里
        promoted_ids = {id(b) for b in promoted}
        return [[b for b in flow if id(b) not in promoted_ids]], promoted
    return columns, promoted


def _split_columns_recursive(
    blocks: list[Block],
    content_l: float,
    content_r: float,
    promoted_out: list[Block],
    depth: int,
):
    if depth >= 2 or len(blocks) < 4:  # 少于 4 块不足以证明存在分栏
        return None
    gutter_c = gutter_hw = content_w = 0.0
    for _ in range(2):  # 提升骑跨块后重估一次空隙
        gap = _find_gutter(blocks, content_l, content_r)
        if gap is None:
            return None
        gutter_c, gutter_hw, content_w = gap
        riding = [
            b
            for b in blocks
            if b.x0 < gutter_c - gutter_hw
            and b.x1 > gutter_c + gutter_hw
            and (b.x1 - b.x0) >= content_w * _PROMOTE_RATIO
        ]
        if not riding:
            break
        if len(riding) > 3:  # 太多块骑跨说明这不是真正的栏间空隙
            return None
        promoted_out.extend(riding)
        riding_ids = {id(b) for b in riding}
        blocks = [b for b in blocks if id(b) not in riding_ids]

    left: list[Block] = []
    right: list[Block] = []
    for b in blocks:
        left_overlap = min(b.x1, gutter_c) - b.x0
        right_overlap = b.x1 - max(b.x0, gutter_c)
        if left_overlap <= 0:
            right.append(b)
        elif right_overlap <= 0:
            left.append(b)
        elif left_overlap >= right_overlap:
            left.append(b)  # 平局归左，保证确定性
        else:
            right.append(b)

    total_chars = sum(len(b.text) for b in blocks)

    def side_ok(side: list[Block]) -> bool:
        # 一侧只有一两个块时，要求它占有足量字符，避免把居中行误判成独立一栏
        return len(side) >= 2 or (
            total_chars > 0 and sum(len(b.text) for b in side) >= total_chars * _MIN_SIDE_CHARS
        )

    if not (side_ok(left) and side_ok(right)):
        return None
    if not _columns_run_in_parallel([left, right]):
        return None

    left_sub = _split_columns_recursive(left, content_l, gutter_c, promoted_out, depth + 1)
    right_sub = _split_columns_recursive(right, gutter_c, content_r, promoted_out, depth + 1)
    # 子层里可能又提升了骑跨块，兜底分组时必须剔除，防止同一块出现两遍
    promoted_ids = {id(b) for b in promoted_out}
    columns = left_sub if left_sub else [[b for b in left if id(b) not in promoted_ids]]
    columns.extend(right_sub if right_sub else [[b for b in right if id(b) not in promoted_ids]])
    return columns


def _find_gutter(blocks: list[Block], content_l: float, content_r: float):
    """在块的 x 覆盖率里找左右栏之间的空隙，返回 (中心, 半宽, 内容宽度)。

    逐级放宽：先找完全无覆盖的内部空隙；若空隙被少数骑跨块污染，
    再允许 1~4 个块的稀疏覆盖。候选空隙必须两侧都有文本（flanked），
    因此不会把页边留白或窄栏大片空白当成栏边界，对不对称双栏同样适用。
    """
    width = content_r - content_l
    if width <= 1.0 or len(blocks) < 4:
        return None
    bins = 120
    counts = [0] * bins
    for b in blocks:
        lo = max(0, min(bins - 1, int((b.x0 - content_l) / width * bins)))
        hi = max(lo + 1, min(bins, int((b.x1 - content_l) / width * bins) + 1))
        for i in range(lo, hi):
            counts[i] += 1

    def all_runs(covered, min_width: float):
        runs = []
        i = 0
        while i < bins:
            if covered(counts[i]):
                j = i
                while j + 1 < bins and covered(counts[j + 1]):
                    j += 1
                if i > 0 and j < bins - 1:  # 不认贴边的空隙（那是页边距）
                    gap_width = (j - i + 1) * width / bins
                    if gap_width >= min_width:
                        runs.append((gap_width, i, j))
                i = j + 1
            else:
                i += 1
        runs.sort(key=lambda r: -r[0])  # 宽的优先
        return runs

    def flanked(i: int, j: int) -> bool:
        # 空隙两侧紧邻区域必须有文本，证明这是"栏间"空隙，
        # 而不是版面大片留白（如窄栏整栏只有几个大块时形成的假空隙）
        window = 6
        left_window = counts[max(1, i - window):i]
        right_window = counts[j + 1:min(bins - 1, j + 1 + window)]
        if not left_window or not right_window:
            return False
        return min(left_window) >= 1 and min(right_window) >= 1

    def find_valid(limit: int, min_width: float):
        for _, i, j in all_runs(lambda c: c <= limit, min_width):
            if flanked(i, j):
                return i, j
        return None

    # 逐级放宽容忍度：优先找完全无覆盖的空隙，其次允许 1~4 个骑跨块
    # （居中标题/作者/摘要行、页码）。宽松级别会污染出更宽的假空隙，
    # 所以只要严格级别找到"两侧都有成规模文本"的空隙就立即采纳。
    run = find_valid(0, max(6.0, width * _MIN_GAP_RATIO))
    if run is None:
        for limit in (1, 2, 3, 4):
            run = find_valid(limit, max(10.0, width * 0.03))
            if run is not None:
                break
    if run is None:
        return None
    i, j = run
    center = content_l + (i + j + 1) * width / (2.0 * bins)
    half = max(1.0, (j - i + 1) * width / (2.0 * bins))
    return center, half, width


def _extract_column_footnotes(
    columns: list[list[Block]], page_height: float, body_size: float
) -> list[Block]:
    """识别栏底的小字号脚注块（双栏论文常贴在左/右栏最下方）。

    判据从严，宁漏勿误：
      * 页面成功分出 >= 2 栏（单栏论文维持原行为，零回归）；
      * 页面上存在"小字号 + 页面下部 + 以脚注标记开头"的块（1. / * / † …）；
      * 目标块是所在列**末尾**向上连续的小字号段（可容纳脚注的折行续块）；
      * 该段不超过 6 块、字符量不超过本栏的 35%。
    命中的块整体后置到页尾，避免"左栏正文 → 脚注 → 右栏正文"被打断；
    同时按"左栏 → 右栏、栏内自上而下"排列，跨页脚注续块也能顺读。
    """
    if len(columns) < 2 or page_height <= 0 or body_size <= 0:
        return []

    def is_footnote_like(b: Block) -> bool:
        return 0 < b.size <= body_size - _FOOTNOTE_SIZE_GAP and b.y0 >= page_height * _FOOTNOTE_TOP_RATIO

    small_bottom = [b for col in columns for b in col if is_footnote_like(b)]
    if not small_bottom or not any(_FOOTNOTE_MARKER_RE.match(b.text) for b in small_bottom):
        return []

    footnotes: list[Block] = []
    for col in columns:
        if len(col) < 2:
            continue
        # 页码块常贴在栏底、把脚注和正文隔开，统计时先排除
        ordered = [
            b for b in _sort_blocks_y(col) if not _PAGE_NUMBER_RE.fullmatch(normalize(b.text))
        ]
        run: list[Block] = []
        for b in reversed(ordered):
            if is_footnote_like(b):
                run.append(b)
            else:
                break
        if not run or len(run) > 6:
            continue
        run.reverse()
        run_chars = sum(len(b.text) for b in run)
        col_chars = sum(len(b.text) for b in ordered)
        if col_chars > 0 and run_chars <= col_chars * 0.35:
            footnotes.extend(run)
    return footnotes


def _order_blocks_legacy(blocks: list[Block]) -> list[Block]:
    """旧版贪心分列（v2 的兜底路径）：按 x 重叠度分列、列内按 y 排序。"""
    columns: list[list[Block]] = []
    for block in blocks:
        target = -1
        best_overlap = 0.0
        for index, column in enumerate(columns):
            reference = column[-1]
            overlap = min(reference.x1, block.x1) - max(reference.x0, block.x0)
            narrower = min(reference.x1 - reference.x0, block.x1 - block.x0)
            if narrower > 0 and overlap > max(6.0, narrower * 0.5) and overlap > best_overlap:
                target, best_overlap = index, overlap
        if target < 0:
            columns.append([block])
        else:
            columns[target].append(block)

    ordered: list[Block] = []
    for column in columns:
        if len(column) > 1:
            column.sort(key=lambda b: b.y0)
        ordered.extend(column)
    return ordered


# ---------------------------------------------------------------------------
# 字号 / 标题
# ---------------------------------------------------------------------------
def _estimate_body_size(pages: list[Page]) -> float:
    """用"字符数加权"的字号众数估计正文字号。"""
    weights: Counter[float] = Counter()
    for page in pages:
        for block in page.blocks:
            if block.size <= 0:
                continue
            key = round(block.size * 2) / 2.0  # 0.5pt 粒度
            weights[key] += len(block.text)
    if not weights:
        return 10.0
    return float(weights.most_common(1)[0][0])


def _mark_headings(pages: list[Page], body_size: float) -> None:
    max_size = max((b.size for p in pages for b in p.blocks), default=body_size)
    for page in pages:
        for block in page.blocks:
            if block.is_footnote:
                # 脚注块（如 "4We note that..."）不是章节标题，禁止进大纲
                block.is_heading, block.heading_level = False, 0
                continue
            block.is_heading, block.heading_level = _classify_heading(
                block, body_size, max_size, page.width
            )


_AFFILIATION_HINTS = (
    "university",
    "department",
    "institute",
    "college",
    "school of",
    "laboratory",
    "academy",
    "大学",
    "学院",
    "研究院",
    "研究所",
    "实验室",
    "@",
)


def _looks_like_author_or_affiliation(block: Block) -> bool:
    """识别作者行 / 单位行，避免它们被当成章节标题。

    作者行与单位行的共同特征是：字号偏大、单独成块、内容里没有章节编号，
    但出现多个逗号分隔的人名，或包含 university/@ 等单位特征词。
    """
    text = normalize(block.text)
    if not text or len(text) > 300:
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in _AFFILIATION_HINTS):
        return True
    # 多个短片段 + 逗号/顿号 → 人名列表
    parts = [p.strip() for p in re.split(r"[,，、;；]", text) if p.strip()]
    if len(parts) >= 2 and all(len(p) <= 24 for p in parts):
        name_like = sum(
            1
            for p in parts
            if re.fullmatch(r"(?:[A-Z][a-zA-Z.\-']+\s*){1,4}", p) or re.fullmatch(r"[\u4e00-\u9fff]{2,4}", p)
        )
        if name_like >= max(2, len(parts) - 1):
            return True
    return False


def _looks_like_numbered_heading(text: str) -> bool:
    """判断"以编号开头"的文本是否真的是标题，而不是恰好以数字开头的句子。

    反面例子（真实踩过的坑）：
        "2.6 个百分点的提升,说明提示设计对结构化解构任务具有实质影响。"
    它以 "2.6" 开头，但显然是一个正文句子。
    """
    if not text or len(text) > 90:
        return False
    # 编号必须紧跟空格或顿号/点号，再接上标题文字
    match = re.match(
        r"^(?:(?:\d{1,2}(?:\.\d{1,2}){0,2})|(?:[IVXLC]{1,5}\.)|(?:[一二三四五六七八九十]+\s*[、.．]))\s*[\)\.、．]?\s*(.+)$",
        text,
    )
    if not match:
        return False
    rest = match.group(1).strip()
    if not rest:
        return False
    # 版面碎片不是标题：页码/文章号（061102-3）、DOI（0031-9007=16=...）
    if re.search(r"\d{4,}", rest) or "=" in rest:
        return False
    # 标题不会以句末标点结尾
    if rest[-1] in "。；！？!?;":
        return False
    if has_cjk(rest):
        # 中文标题通常很短
        return len(rest) <= 30
    # 英文标题：控制词数，并排除像 "2.6 points of improvement" 这样的句子式片段
    words = rest.split()
    if len(words) > 12:
        return False
    if len(words) >= 4 and rest[-1] in ".,;:":
        return False
    return True


def _looks_like_title_text(text: str) -> bool:
    """标题的"语言学"守卫：排除公式碎片与图表坐标轴标注。

    真实踩到的两类误判（用论文实测发现）：
      * 数学论文里的行内公式被单独成块（``t)|0 ≤l ≤ai −1, 1 ≤t ≤m −n}.``），
        这类块字号常常大于正文，纯几何规则（字号/居中/加粗）判不出来；
      * 图表坐标轴刻度（``1.0`` / ``0.8`` / ``100 101``）同样"字号偏大、居中"，
        一篇 28 页的 arXiv 论文曾因此产出 167 个"标题"，其中 141 个是刻度。

    二者的共同点是**字母占比极低**或**括号不配平**，而真正的章节标题几乎不可能
    满足这两条（中英文标题的字母/汉字占比都很高）。
    """
    letters = sum(1 for ch in text if ch.isalpha())
    visible = sum(1 for ch in text if not ch.isspace())
    if visible < 2 or letters < 2:
        return False
    if letters / visible < 0.5:
        return False
    # 图表坐标轴刻度常写成"20 30 50 80 100 Number of nodes"：多个纯数字 token
    # 说明这是刻度而不是标题（"1. Introduction" 的 "1."、"GPT-4" 都不算纯数字）。
    if sum(1 for token in text.split() if re.fullmatch(r"\d+(?:\.\d+)?", token)) >= 2:
        return False
    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
        if text.count(opener) != text.count(closer):
            return False
    return True


def _classify_heading(block: Block, body_size: float, max_size: float, page_width: float) -> tuple[bool, int]:
    text = normalize(block.text)
    if not text or len(text) > 120:
        return False, 0
    if not _looks_like_title_text(text):
        return False, 0
    words = text.split()
    stripped = text.rstrip(" .:：")

    numbered = bool(_NUMBERED_HEADING_RE.match(stripped)) and _looks_like_numbered_heading(stripped)
    canonical = stripped.lower().strip("0123456789.、 ") in _CANONICAL_HEADINGS
    larger = block.size >= body_size + 0.6
    much_larger = block.size >= body_size + 2.0
    short_line = len(words) <= 16 if words else len(text) <= 40
    centered = page_width > 0 and abs(block.center_x - page_width / 2.0) < page_width * 0.12

    if numbered and short_line and len(text) <= 90:
        level = min(3, max(1, stripped.count(".") + (1 if re.match(r"^\d+\s", stripped) else 0) + 1))
        return True, level
    if canonical and short_line:
        return True, 1
    # 作者行 / 单位行即使字号很大也不是标题
    if _looks_like_author_or_affiliation(block):
        return False, 0
    if much_larger and short_line:
        level = 1 if block.size >= max_size - 2.0 else 2
        return True, level
    if larger and short_line and (block.bold or centered) and len(text) <= 60:
        return True, 2
    return False, 0


def _mark_boilerplate(pages: list[Page]) -> None:
    """跨页重复出现的短块 = 页眉/页脚。"""
    if len(pages) < 2:
        return
    counts: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for block in page.blocks:
            key = collapse_for_search(block.text)
            if not key or len(key) > 120 or key in seen:
                continue
            seen.add(key)
            counts[key] += 1
    # 页数很少时出现 2 次即可判定；长文档要求覆盖 40% 的页面，避免误杀正常重复句
    repeat_threshold = 2 if len(pages) <= 3 else max(3, int(len(pages) * 0.4))
    boilerplate = {key for key, n in counts.items() if n >= repeat_threshold}

    for page in pages:
        for block in page.blocks:
            key = collapse_for_search(block.text)
            if key and key in boilerplate:
                block.is_boilerplate = True
                continue
            # 纯页码行（第 3 页 / Page 3 / 3 / PRL 文章号 061102-3）
            if _PAGE_NUMBER_RE.fullmatch(normalize(block.text)):
                block.is_boilerplate = True


def _detect_two_column(pages: list[Page]) -> list[int]:
    """启发式分栏检测：左右两半都有成规模文本块且中间无明显跨越块。"""
    detected: list[int] = []
    for page in pages:
        blocks = [b for b in page.blocks if not b.is_boilerplate and len(normalize(b.text)) >= 40]
        if len(blocks) < 6:
            continue
        mid = page.width / 2.0
        left = [b for b in blocks if b.x1 <= mid + page.width * 0.03]
        right = [b for b in blocks if b.x0 >= mid - page.width * 0.03]
        spanning = [b for b in blocks if b.x0 < mid - page.width * 0.08 and b.x1 > mid + page.width * 0.08]
        if len(left) >= 3 and len(right) >= 3 and len(spanning) <= max(1, len(blocks) * 0.2):
            detected.append(page.number)
    return detected


def _suppress_title_headings(pages: list[Page], title: str) -> None:
    """把构成标题的那些块从大纲中剔除。

    ``_detect_title`` 会把 "…Evidence-Aware" + "Reranking" 合并成完整标题，
    但这两个块各自仍被标记为 heading；如果不处理，大纲里就会出现标题碎片，
    进而污染送给大模型的结构提示。
    """
    key = collapse_for_search(title)
    if not key:
        return
    for page in pages[:1]:
        for block in page.blocks:
            if not block.is_heading:
                continue
            block_key = collapse_for_search(block.text)
            if block_key and (block_key in key or key in block_key):
                block.is_heading = False
                block.heading_level = 0


def _detect_title(pages: list[Page]) -> str:
    """识别标题。

    标题常被生成器拆成多个文本块（例如 "…Evidence-Aware" + "Reranking"），
    因此这里先取出首屏顶部、字号接近最大的块，再把**字号相近**的相邻块合并，
    避免只截到标题的最后一个单词。
    """
    if not pages:
        return ""
    first = pages[0]
    body_size = _estimate_body_size(pages)

    candidates = [
        b
        for b in first.blocks
        if not b.is_boilerplate
        and b.top <= first.height * 0.45
        and 4 <= len(normalize(b.text)) <= 260
    ]
    if not candidates:
        return ""
    max_size = max(b.size for b in candidates)

    prominent = [b for b in candidates if b.size >= max_size - 0.5]
    # 字号明显大于正文时才认为它在构成标题，避免把正文首段当成标题
    if max_size < body_size + 1.0 and not any(_NUMBERED_HEADING_RE.match(normalize(b.text)) is None and b.bold for b in prominent):
        prominent = [b for b in candidates if b.bold] or [max(candidates, key=lambda b: b.size)]

    chain: list[Block] = []
    for block in prominent[:4]:
        if not chain:
            chain.append(block)
            continue
        previous = chain[-1]
        same_size = abs(block.size - previous.size) <= max(1.0, previous.size * 0.15)
        below = block.y0 >= previous.y0 - 4
        close = block.y0 - previous.y1 <= max(24.0, (previous.y1 - previous.y0) * 1.2)
        short = len(normalize(block.text)) <= 60
        if same_size and below and close and short:
            chain.append(block)
        else:
            break

    if not chain:
        return ""
    return normalize(" ".join(b.text for b in chain)).replace("\n", " ").strip()


def _detect_abstract(pages: list[Page]) -> str:
    for page in pages[:2]:
        for idx, block in enumerate(page.blocks):
            text = normalize(block.text)
            if not text:
                continue
            lowered = text.lower()
            if lowered.startswith("abstract") or lowered.startswith("摘要"):
                inline = re.sub(r"^(abstract|摘要)[:：\s]*", "", text, flags=re.IGNORECASE).strip()
                if len(inline) > 80:
                    return inline[:2000]
                collected: list[str] = []
                for nxt in page.blocks[idx + 1 : idx + 4]:
                    if nxt.is_heading or nxt.is_boilerplate or nxt.is_footnote:
                        break
                    collected.append(normalize(nxt.text))
                    if sum(len(c) for c in collected) > 600:
                        break
                joined = " ".join(c for c in collected if c)
                if joined:
                    return joined[:2000]
    return ""


def _collect_headings(pages: list[Page]) -> list[Heading]:
    headings: list[Heading] = []
    for page in pages:
        for block in page.blocks:
            if block.is_heading and not block.is_boilerplate:
                headings.append(Heading(text=normalize(block.text), page=page.number, level=block.heading_level))
    return headings


# ---------------------------------------------------------------------------
# 正文装配
# ---------------------------------------------------------------------------
def _collect_boilerplate_lines(pages: list[Page]) -> set[str]:
    """识别**行级**跨页重复内容（页眉/页脚常常和正文挤在同一个块里）。

    块级过滤漏掉的典型情况：末尾页的页脚与正文段落在同一 block 里，
    整块既不能删（丢正文）也不能留（混入"第 2 页 / 共 4 页"）。
    因此在行级别再做一次统计。
    """
    if len(pages) < 2:
        return set()
    pages_of_line: dict[str, set[int]] = {}
    for page in pages:
        seen: set[str] = set()
        for block in page.blocks:
            for raw_line in block.text.splitlines():
                line = normalize(raw_line)
                if not line or len(line) > 120:
                    continue
                key = collapse_for_search(line)
                if not key or key in seen:
                    continue
                seen.add(key)
                pages_of_line.setdefault(key, set()).add(page.number)

    threshold = 2 if len(pages) <= 4 else max(3, int(len(pages) * 0.4))
    return {key for key, seen_pages in pages_of_line.items() if len(seen_pages) >= threshold}


def _strip_boilerplate_lines(text: str, boilerplate_lines: set[str]) -> str:
    if not boilerplate_lines or not text:
        return text
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = normalize(raw_line)
        if line and collapse_for_search(line) in boilerplate_lines:
            continue
        if line:
            kept.append(line)
    return "\n".join(kept)


def _assemble(
    pages: list[Page],
) -> tuple[list[int], list[str], str, list[tuple[int, int]], dict[str, int | None]]:
    """拼接正文，返回 (正文页码, 每页正文, 全文, 每页字符区间, 统计)。

    ``统计`` 里带 ``truncated_from`` / ``truncated_pages``：正文在参考文献或
    附录处截断时，把"从第几页起没有被分析"如实暴露给上层。arXiv 论文常把
    附录放在参考文献之后，若不暴露这个数字，用户会以为 28 页都被读过了。
    """
    page_texts = ["" for _ in pages]
    body_pages: list[int] = []
    current_section = ""
    stopped = False
    stopped_at_page: int | None = None
    dropped_lines = 0
    boilerplate_lines = _collect_boilerplate_lines(pages)

    for page in pages:
        buffer: list[str] = []
        for block in page.blocks:
            if stopped:
                break
            if block.is_boilerplate:
                continue
            original = normalize(block.text)
            text = _strip_boilerplate_lines(original, boilerplate_lines)
            if text != original:
                dropped_lines += 1
            if not text:
                continue
            if block.is_heading:
                marker = text.lower().strip("0123456789.、 ")
                if marker in _STOP_SECTIONS or marker in _APPENDIX_SECTIONS:
                    # 参考文献/附录之后不再属于正文。注意只停止本页剩余块、
                    # 并让后续页面整体跳过，而**不是**跳出整个循环——
                    # 否则"参考文献紧跟在正文末尾页之前"会把后面所有正文都丢掉。
                    stopped = True
                    if stopped_at_page is None:
                        stopped_at_page = page.number
                    break
                current_section = text
                block.section = current_section
                buffer.append(f"\n## {text}")
                continue
            block.section = current_section
            buffer.append(text)

        joined = "\n".join(buffer).strip()
        page_texts[page.number - 1] = joined
        if joined:
            body_pages.append(page.number)

    # 统计"没被分析的页"：截断点之后正文为空的页。
    # 不用 ``总页数 - 截断页 + 1`` 估算：截断页本身可能已经贡献了内容
    # （例如参考文献排在正文最后一页的下半部分），那样会虚报漏读页数。
    unanalyzed_pages = [
        page.number
        for page in pages
        if stopped_at_page is not None
        and page.number >= stopped_at_page
        and not page_texts[page.number - 1]
    ]
    assemble_stats: dict[str, int | None] = {
        "dropped_lines": dropped_lines,
        "boilerplate_lines": len(boilerplate_lines),
        "truncated_from": unanalyzed_pages[0] if unanalyzed_pages else None,
        "truncated_pages": len(unanalyzed_pages),
    }

    # 只保留真正有内容的页，保证 body_pages 与 page_spans 一一对应
    kept_pages = [p for p in body_pages if page_texts[p - 1]]
    parts: list[str] = []
    page_spans: list[tuple[int, int]] = []
    cursor = 0
    for index, page_no in enumerate(kept_pages):
        text = page_texts[page_no - 1]
        if index > 0:
            cursor += 2  # "\n\n" 分隔符
        parts.append(text)
        page_spans.append((cursor, cursor + len(text)))
        cursor += len(text)
    full_text = "\n\n".join(parts).strip()
    return kept_pages, page_texts, full_text, page_spans, assemble_stats


def _check_quality(paper: PaperText, settings: Settings) -> None:
    """质量自检，把风险通过警告暴露出来，而不是静默产出坏结果。"""
    warn = paper.stats.setdefault("warnings", [])
    if paper.char_count < 1500:
        warn.append(f"正文仅提取到 {paper.char_count} 字符，可能是扫描件或解析失败。")
    if not paper.headings:
        warn.append("未识别到任何章节标题，导图将更依赖大模型的整体理解。")
    if paper.column_ratio > 0.5:
        warn.append(
            f"{len(paper.two_column_pages)}/{len(paper.body_pages)} 页疑似双栏排版，"
            "已按块排序重组，建议人工抽查阅读顺序。"
        )
    if paper.char_count > settings.max_input_chars and not settings.mock:
        warn.append(
            f"正文 {paper.char_count} 字符，超过模型输入上限 {settings.max_input_chars}，"
            "将按页分批完整阅读，再综合生成看板。"
        )
    raw_chars = paper.stats.get("raw_chars", 0)
    if raw_chars and paper.char_count > raw_chars * 1.05:
        warn.append("正文长度超过原始提取长度，可能存在重复内容，请检查。")
    truncated = int(paper.stats.get("truncated_pages") or 0)
    if truncated:
        warn.append(
            f"正文在参考文献/附录处截断：第 {int(paper.stats.get('truncated_from') or 0)} 页起共 "
            f"{truncated} 页未参与分析。这是为了避免参考文献污染分析；若论文的附录排在"
            "参考文献之后（arXiv 常见），附录内容不会进入看板。"
        )
