#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 PDF 解析流水线用的 4 个样例测试文件（fixtures）。

用法::

    python examples/make_samples.py

依赖：仅标准库 + PyMuPDF（不联网、不使用 reportlab）。

生成的 4 个 fixture 及其用途
---------------------------
1. ``sample_en_two_column.pdf``
   英文、6 页、双栏（two-column）学术论文，主题是检索增强的开放域问答方法。
   含标题 / 作者 / 单位 / Abstract / 1 Introduction / 2 Related Work /
   3 Method (3.1, 3.2) / 4 Experiments（含以纯文本绘制的数字表格）/
   5 Conclusion / 6 Limitations / References。
   每页页眉与页脚在字节层面完全一致（正文内容不同），用于验证流水线的
   "页眉页脚/boilerplate 过滤" 能否识别并剔除重复出现的样板文本。

2. ``sample_zh_single_column.pdf``
   中文、4 页、单栏论文，主题为"基于大模型的学术论文结构自动解析"。
   含 摘要 / 关键词 / 1 引言 / 2 相关工作 / 3 方法 / 4 实验结果与分析 /
   5 结论与展望 / 参考文献。使用 PyMuPDF 内置 CJK 字体（china-s），
   用于验证中文文本抽取与中文标题识别；页眉页脚同样逐页一致。

3. ``sample_long_outline.pdf``
   英文、24 页、单栏长文档，带 1 / 1.1 / 1.1.1 三级深层编号大纲，
   用于测试长文档处理（分块、内存占用）与标题层级检测。
   每个小节正文都是由确定性的领域词表拼装出来的、互不相同的段落
   （不是同一句话复制），最后 2 页为 References。

4. ``sample_scan_like.pdf``
   退化/对抗样例，3 页：
     - 第 1 页：只有矢量矩形（模拟扫描件），几乎没有可抽取文本；
     - 第 2 页：仅一行稀疏文本；
     - 第 3 页：文本存在但内容流顺序与阅读顺序不一致（含逆序插入与旋转文本）。
   用于验证流水线在异常输入下降级而不崩溃。

设计约束
-------
* 完全确定性：不使用 random，所有"变化"都由 crc32(编号) 派生，重复运行结果等价。
* 页眉页脚：把固定样板文字与页码/页数分开写入，因此同一字符串在每页的
  内容流中逐字节相同，只有数字部分不同。
* 所有 PDF 合计 < 2 MB。
"""

from __future__ import annotations

import os
import zlib

import pymupdf

# --------------------------------------------------------------------------
# 全局常量
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))

PAGE_W, PAGE_H = 595.0, 842.0          # A4
MARGIN = 54.0
HEADER_Y = 34.0                        # 页眉基线
HEADER_RULE_Y = 39.0                   # 页眉分隔线
BODY_TOP = 58.0                        # 正文起始 y（必须低于页眉分隔线 19pt 以上）
FOOTER_Y = PAGE_H - 30.0               # 页脚基线

EN_HEADER = "Proceedings of the 2026 Conference on Applied AI"
ZH_HEADER = "2026 年应用人工智能国际会议论文集"
OUTLINE_HEADER = "Technical Report TR-2026-114: Structured Analysis of Long Documents"
SCAN_HEADER = "SCAN-FIXTURE"           # 对抗样例：故意保留极简样板

BODY_FONTS = ("Times-Roman", "Times-Bold", "Times-Italic", "Courier")
CJK_FONTS = ("china-s", "china-ss")

EN_FOOTER_TMPL = "Page {cur} of {total}"
ZH_FOOTER_TMPL = "第 {cur} 页 / 共 {total} 页"
OUTLINE_FOOTER_TMPL = "TR-2026-114  page {cur} / {total}"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def fit_size(text, pairs, max_width, start):
    """逐步缩小字号，返回第一个能放进 max_width 的字号。"""
    size = start
    while size > 5.5:
        if all(pymupdf.get_text_length(text, fontname=fn, fontsize=size) <= max_width
               for fn, _ in pairs):
            return size
        size -= 0.2
    return max(size, 4.0)


def text_width(text, fontname, fontsize):
    """文本宽度（PyMuPDF 内置字体度量，无需页面对象）。"""
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=fontsize)


def wrap(text, fontname, fontsize, width):
    """贪心折行：支持 CJK（逐字断行）与西文（按词断行）。"""
    lines = []
    for raw in text.split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        cur = ""
        for token in _tokenize(raw):
            cand = cur + token
            if cur and text_width(cand.rstrip(), fontname,
                                  fontsize) > width:
                lines.append(cur.rstrip())
                cur = token if token != " " else ""
            else:
                cur = cand
        if cur.strip():
            lines.append(cur.rstrip())
    return lines


def _tokenize(line):
    """把一行切成"词 + 尾随空格"或"单个 CJK 字符"的 token 序列。"""
    out, buf = [], ""
    for ch in line:
        if _is_cjk(ch) or ch == " ":
            if buf:
                out.append(buf)
                buf = ""
            if ch == " ":
                if out:
                    out[-1] = out[-1] + " "
                else:
                    out.append(" ")
            else:
                out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _is_cjk(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF)


def put_lines(page, lines, x, y, fontname, fontsize, leading,
              rect_width, align=0):
    """逐行按绝对基线写入文本，返回下一行基线 y。

    使用 insert_text（而非 insert_textbox）以保证输出逐字节可控。
    align: 0 左对齐 / 1 居中 / 2 右对齐。
    """
    for ln in lines:
        if ln:
            w = text_width(ln, fontname, fontsize)
            if align == 1:
                lx = x + (rect_width - w) / 2.0
            elif align == 2:
                lx = x + rect_width - w
            else:
                lx = x
            page.insert_text((lx, y), ln, fontname=fontname,
                             fontsize=fontsize, color=(0, 0, 0))
        y += leading
    return y


def reopen_text(path):
    """重新打开 PDF 并返回全部文本，用于自检断言。"""
    d = pymupdf.open(path)
    try:
        return d.page_count, [p.get_text() for p in d]
    finally:
        d.close()


# 固定文档 ID / 时间戳，保证重复运行产出逐字节相同的文件
FIXED_ID = "[<0102030405060708090A0B0C0D0E0F10><0102030405060708090A0B0C0D0E0F10>]"
FIXED_DATE = "D:20260101000000Z"


def save_deterministic(doc, path, title):
    """确定性保存：固定元数据 + 固定文件 ID + no_new_id。"""
    doc.set_metadata({
        "title": title,
        "author": "PDF parsing pipeline test fixtures",
        "subject": "sample fixture for PDF structure parsing",
        "keywords": "fixture; pdf; parsing; benchmark",
        "creator": "examples/make_samples.py",
        "producer": "PyMuPDF",
        "creationDate": FIXED_DATE,
        "modDate": FIXED_DATE,
    })
    doc.xref_set_key(-1, "ID", FIXED_ID)
    doc.save(path, deflate=True, garbage=3, clean=True, no_new_id=True)
    doc.close()


def write_boilerplate(page, header, footer_left, footer_right, fontname,
                      fontsize=7.6, header_fontsize=8.2):
    # 说明见下方 docstring（保持函数签名稳定，注释与实现一致）。
    """写入页眉 + 页脚。

    样板字符串（header / footer_left）在每一页使用完全相同的参数与坐标写入，
    因此内容流逐字节一致；只有页码数字不同，方便 boilerplate 过滤器识别。
    """
    page.insert_text((MARGIN, HEADER_Y), header,
                     fontname=fontname, fontsize=header_fontsize,
                     color=(0.25, 0.25, 0.25))
    page.draw_line(pymupdf.Point(MARGIN, HEADER_RULE_Y),
                   pymupdf.Point(PAGE_W - MARGIN, HEADER_RULE_Y),
                   color=(0.6, 0.6, 0.6), width=0.4)
    # insert_text 没有 align 参数，右对齐需自行按文本宽度换算 x 坐标
    fr_w = text_width(footer_right, fontname, fontsize)
    page.insert_text((PAGE_W - MARGIN - fr_w, FOOTER_Y), footer_right,
                     fontname=fontname, fontsize=fontsize,
                     color=(0.25, 0.25, 0.25))
    if footer_left:
        page.insert_text((MARGIN, FOOTER_Y), footer_left,
                         fontname=fontname, fontsize=fontsize,
                         color=(0.25, 0.25, 0.25))


def draw_table(page, x, y, rows, width, fontsize=8.0,
               fontname="Courier", fontname_bold="Courier-Bold"):
    """把表格以纯文本绘制。

    每一行用一次 insert_text 写入，列间用空格按列宽对齐：这样 get_text()
    抽出来就是"一行一行、列对齐"的表格文本，便于流水线做表格识别。
    表头下方与表尾各画一条横线（矢量），模拟真实的 booktabs 三线表。
    """
    ncol = len(rows[0])
    # 每列取"最大内容宽度 + 2 个空格"作为该列的打印宽度
    colw = []
    for c in range(ncol):
        w = max(text_width(str(r[c]), fontname_bold if i == 0 else fontname,
                           fontsize)
                for i, r in enumerate(rows))
        colw.append(w + 2.4 * fontsize * 0.5)
    total = sum(colw)
    if total > width:                      # 超宽则整体等比缩小
        k = width / total
        colw = [w * k for w in colw]
        fontsize = max(fontsize * k, 5.5)
    leading = fontsize + 4.2
    cy = y
    for i, row in enumerate(rows):
        fn = fontname_bold if i == 0 else fontname
        parts = []
        for c, cell in enumerate(row):
            text = str(cell)
            # 末列不补空格；数字列右对齐，其余左对齐
            if c == ncol - 1:
                parts.append(text)
            else:
                avail = colw[c] - text_width(text, fn, fontsize)
                pad = max(int(avail / max(text_width(" ", fn, fontsize), 0.01)), 1)
                parts.append(text + " " * pad)
        page.insert_text((x, cy), "".join(parts), fontname=fn,
                         fontsize=fontsize, color=(0, 0, 0))
        if i == 0:
            page.draw_line(pymupdf.Point(x, cy + 3.6),
                           pymupdf.Point(x + min(width, sum(colw)), cy + 3.6),
                           color=(0, 0, 0), width=0.7)
        cy += leading
    page.draw_line(pymupdf.Point(x, cy - leading + 3.6),
                   pymupdf.Point(x + min(width, sum(colw)), cy - leading + 3.6),
                   color=(0, 0, 0), width=0.7)
    return cy - leading + 12.0


# --------------------------------------------------------------------------
# 双栏 / 单栏版面引擎
# --------------------------------------------------------------------------


class Block:
    """一段可排版的文本块。"""

    __slots__ = ("text", "font", "bold", "size", "leading", "align",
                 "space_before", "space_after", "hang")

    def __init__(self, text, font="Times-Roman", bold=False, size=9.2,
                 leading=None, align=3, space_before=0.0, space_after=3.0,
                 hang=0.0):
        self.text = text
        self.font = font
        self.bold = bold
        self.size = size
        self.leading = leading if leading else round(size + 1.9, 2)
        self.align = align
        self.space_before = space_before
        self.space_after = space_after
        self.hang = hang


class Flow:
    """单栏或双栏的流式排版器。

    页眉页脚不在建页时写入，而是在所有页面生成完毕后由 finish_footers()
    统一写入：这样"共 N 页"里的 N 才是真实总页数，而且每页使用的编码参数
    完全相同，样板字符串在内容流中逐字节一致（便于 boilerplate 过滤）。
    """

    def __init__(self, doc, header, footer_left, footer_tmpl, fontname="Times-Roman",
                 fontsize=7.6, top=BODY_TOP, bottom=FOOTER_Y - 16.0,
                 two_column=False, gutter=24.0, header_fontsize=8.2):
        self.doc = doc
        self.header = header
        self.footer_left = footer_left
        self.footer_tmpl = footer_tmpl
        self.fontname = fontname
        self.fontsize = fontsize
        self.top = top
        self.bottom = bottom
        self.two_column = two_column
        self.gutter = gutter
        self.header_fontsize = header_fontsize
        self.page = None
        self.col = 0
        self.y = top
        self.page_no = 0

    # ---- 版面几何 -------------------------------------------------------
    def col_x(self, c):
        if not self.two_column:
            return MARGIN, PAGE_W - 2 * MARGIN
        total = PAGE_W - 2 * MARGIN
        w = (total - self.gutter) / 2.0
        return MARGIN + c * (w + self.gutter), w

    def n_cols(self):
        return 2 if self.two_column else 1

    # ---- 开新页 / 换栏 --------------------------------------------------
    def new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.page_no += 1
        self.col = 0
        self.y = self.top
        return self.page

    def ensure_page(self):
        if self.page is None:
            self.new_page()

    def next_column(self):
        self.col += 1
        if self.col >= self.n_cols():
            self.new_page()
        else:
            self.y = self.top

    def space(self, need):
        """确保当前栏剩余高度 >= need。"""
        self.ensure_page()
        if self.y + need > self.bottom:
            if self.col + 1 < self.n_cols():
                self.col += 1
                self.y = self.top
            else:
                self.new_page()

    def finish_footers(self):
        """全部页面生成后统一写入页眉页脚（含真实总页数）。"""
        total = str(self.page_no)
        for i in range(self.page_no):
            write_boilerplate(self.doc[i], self.header, self.footer_left,
                              self.footer_tmpl.format(cur=i + 1, total=total),
                              self.fontname, self.fontsize, self.header_fontsize)
        return self.page_no

    # ---- 排版 -----------------------------------------------------------
    def place(self, blocks):
        for b in blocks:
            self._place_one(b)

    def _place_one(self, b):
        self.ensure_page()
        x, w = self.col_x(self.col)
        lines = wrap(b.text, b.font, b.size, w - b.hang)
        if not lines:
            return
        self.y += b.space_before
        i = 0
        while i < len(lines):
            if self.y + b.leading > self.bottom:
                if self.col + 1 < self.n_cols():
                    self.col += 1
                    self.y = self.top
                else:
                    self.new_page()
                x, w = self.col_x(self.col)
            room = int((self.bottom - self.y) // b.leading)
            chunk = lines[i:i + max(room, 1)]
            if chunk:
                put_lines(self.page, chunk, x + b.hang, self.y, b.font,
                          b.size, b.leading, w - b.hang, align=b.align)
                self.y += b.leading * len(chunk)
                i += len(chunk)
            if i < len(lines):
                self.next_column()
                x, w = self.col_x(self.col)
        self.y += b.space_after
        return self.y

    def place_full_width(self, blocks):
        """在页面顶部放整页宽内容（标题、摘要、宽表格），自己开新页。"""
        self.new_page()
        x, w = MARGIN, PAGE_W - 2 * MARGIN
        for b in blocks:
            lines = wrap(b.text, b.font, b.size, w)
            self.y += b.space_before
            put_lines(self.page, lines, x, self.y, b.font, b.size,
                      b.leading, w, align=b.align)
            self.y += b.leading * len(lines) + b.space_after
        return self.y

    def place_full_width_table(self, rows, caption, width_frac=1.0,
                               fontname="Courier", fontname_bold="Courier-Bold",
                               cap_font="Times-Bold", fontsize=8.0):
        """在新页顶部绘制整页宽表格，返回表格底部 y。

        中文表格必须显式传入 CJK 字体（fontname="china-s"），否则 Courier 无法
        编码汉字，抽取出来会是一串 · 占位符。
        """
        self.new_page()
        x = MARGIN
        w = (PAGE_W - 2 * MARGIN) * width_frac
        self.y += 16.0
        self.y = put_lines(self.page, wrap(caption, cap_font, 9.2, w),
                           x, self.y, cap_font, 9.2, 11.1, w, align=1) + 6.0
        bottom = draw_table(self.page, x, self.y + 6.0, rows, w,
                            fontsize=fontsize, fontname=fontname,
                            fontname_bold=fontname_bold)
        self.y = bottom + 8.0
        return bottom


# --------------------------------------------------------------------------
# Fixture 1: 英文双栏 6 页论文
# --------------------------------------------------------------------------


def build_en_two_column(path):
    doc = pymupdf.open()
    header = EN_HEADER
    footer_left = "ACAI 2026, Singapore. Accepted for oral presentation."

    BODY = 9.2
    flow = Flow(doc, header, footer_left, EN_FOOTER_TMPL, two_column=True,
                top=BODY_TOP - 4.0)

    title = "Retrieval-Augmented Question Answering with Evidence-Aware Reranking"
    authors = ("Wei Zhang, Priya Raghavan, Daniel Okafor, and Marta Kowalski")
    affil = ("Department of Computer Science, Northgate University, Boston, MA, USA\n"
             "{wei.zhang, priya.raghavan, daniel.okafor, marta.kowalski}@northgate.edu")

    abstract = (
        "Open-domain question answering (QA) systems increasingly rely on a retrieval "
        "component that supplies evidence to a large language model (LLM). While "
        "retrieval quality has improved substantially, the downstream reader remains "
        "sensitive to the ordering and the redundancy of the passages it receives. We "
        "present EARR (Evidence-Aware Reranking for Retrieval), a lightweight pipeline "
        "that couples a sparse-dense hybrid retriever with a cross-encoder reranker and "
        "an evidence-coverage controller. The controller estimates, for each candidate "
        "answer, how much of the required supporting evidence is already covered by the "
        "selected passages, and continues retrieving only when the estimated coverage "
        "falls below a calibrated threshold. On NaturalQuestions and HotpotQA, EARR "
        "improves exact match by 3.4 and 4.1 points respectively over a strong "
        "hybrid-retrieval baseline, while reducing the average number of passages passed "
        "to the reader by 38 percent. We further show that coverage-based early stopping "
        "is robust to retrieval noise and yields the largest gains on multi-hop questions, "
        "where a single passage is rarely sufficient. Our analysis indicates that most of "
        "the improvement comes from removing near-duplicate evidence rather than from "
        "adding new documents, a finding that has direct implications for the cost of "
        "deployed QA systems.")

    kw = ("Keywords: retrieval-augmented generation; open-domain question answering; "
          "cross-encoder reranking; evidence coverage; efficiency")

    flow.place_full_width([
        Block(title, font="Times-Bold", size=17.0, leading=20.0, align=1,
              space_after=10.0),
        Block(authors, font="Times-Roman", size=10.6, leading=13.0, align=1,
              space_after=4.0),
        Block(affil, font="Times-Italic", size=8.6, leading=11.0, align=1,
              space_after=14.0),
        Block("Abstract", font="Times-Bold", size=11.0, leading=13.0, align=1,
              space_after=6.0),
        Block(abstract, font="Times-Roman", size=9.2, leading=11.2, align=3,
              space_after=10.0),
        Block(kw, font="Times-Italic", size=9.0, leading=11.0, align=3,
              space_after=8.0),
    ])

    # 摘要下方的两栏正文（第 1 页下半部分开始）
    flow.place([
        Block("1  Introduction", font="Times-Bold", size=11.0, leading=13.4,
              space_before=0.0, space_after=5.0),
        Block("Open-domain question answering has moved from extractive span prediction "
              "over a single retrieved paragraph to multi-passage, generation-based "
              "answering in which a reader model conditions on a set of evidence "
              "documents. In this setting the retriever is no longer a mere filter; it "
              "defines the hypothesis space that the reader can possibly explore. A "
              "retriever that returns twenty passages of which fifteen are paraphrases of "
              "the same sentence wastes reader capacity and, worse, encourages the reader "
              "to over-commit to a single piece of evidence.", size=BODY),
        Block("Prior work has pursued two largely independent directions. The first "
              "improves the retriever itself, through better dense encoders, hybrid "
              "sparse-dense fusion, or query decomposition. The second improves the "
              "reader, through instruction tuning, chain-of-thought prompting, or "
              "self-consistency decoding. Comparatively little attention has been paid to "
              "the interface between the two: the selection, ordering, and stopping "
              "policy that decides which passages the reader actually sees.", size=BODY),
        Block("We argue that this interface deserves first-class treatment, because it is "
              "the only place where the system can reason about evidence sufficiency "
              "before paying the cost of generation. Our contributions are as follows.", size=BODY),
        Block("(1) We introduce an evidence-coverage controller that estimates whether the "
              "currently selected passages jointly support an answer, and uses that "
              "estimate to decide whether to retrieve more documents.", size=BODY),
        Block("(2) We show that a cross-encoder reranker trained with a redundancy penalty "
              "removes near-duplicate evidence and accounts for most of the end-to-end "
              "improvement we observe.", size=BODY),
        Block("(3) We provide a detailed ablation over retrieval depth, reranking depth, "
              "and stopping thresholds on two multi-hop and two single-hop benchmarks, "
              "together with a cost analysis in reader tokens.", size=BODY),
        Block(en_filler("intro-a", 6), size=BODY),
        Block(en_filler("intro-b", 6), size=BODY),
        Block(en_filler("intro-c", 5), size=BODY),

        Block("2  Related Work", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
        Block("Retrieval-augmented generation. Retrieval-augmented models augment a "
              "parametric reader with a non-parametric memory. Early systems combined a "
              "differentiable retriever with an extractive reader and trained both "
              "jointly. Later work scaled the memory to billions of passages and used "
              "generative readers, showing that retrieval quality dominates downstream "
              "accuracy once the reader is sufficiently large.", size=BODY),
        Block("Reranking. Cross-encoder rerankers score query-passage pairs jointly and "
              "consistently outperform bi-encoders at equal recall, at the cost of "
              "quadratic scoring time. Distillation and late-interaction models reduce "
              "that cost, but the ordering they produce is still optimized for relevance "
              "rather than for the complementarity of the selected set.", size=BODY),
        Block("Adaptive retrieval. Several systems decide when to retrieve or when to stop "
              "retrieving using a confidence estimate from the reader. Our controller "
              "differs in that it acts before generation and operates on an explicit "
              "coverage statistic rather than on the reader's token-level uncertainty.", size=BODY),
        Block(en_filler("related-a", 6), size=BODY),
        Block(en_filler("related-b", 6), size=BODY),
        Block(en_filler("related-c", 5), size=BODY),
        Block(en_filler("related-d", 5), size=BODY),

        Block("3  Method", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
        Block("EARR consists of three stages: hybrid retrieval, evidence-aware reranking, "
              "and coverage-controlled selection. Given a question q, the system produces "
              "an ordered evidence set S and a final answer a generated by a frozen "
              "reader model.", size=BODY),
        Block("3.1  Hybrid Retrieval", font="Times-Bold", size=10.0, leading=12.6,
              space_before=6.0, space_after=4.0),
        Block("We index a corpus of 21 million passages with both a BM25 inverted index "
              "and a dense bi-encoder. For a query we retrieve the top k_s = 100 sparse "
              "and k_d = 100 dense candidates and fuse their scores with reciprocal rank "
              "fusion, which requires no score calibration between the two retrievers. "
              "Fusion produces a candidate pool of at most 200 passages, deduplicated by "
              "a normalized hash of the first 200 characters.", size=BODY),
        Block("To keep latency predictable we cap the pool at 120 passages after fusion, "
              "preferring passages that share at least one named entity with the "
              "question. On the development set this cap changes final exact match by "
              "less than 0.2 points while cutting reranking cost by roughly one third.", size=BODY),
        Block(en_filler("method31-a", 6), size=BODY),
        Block(en_filler("method31-b", 5), size=BODY),
        Block("3.2  Evidence-Aware Reranking and Coverage Control", font="Times-Bold",
              size=10.0, leading=12.6, space_before=6.0, space_after=4.0),
        Block("The reranker is a cross-encoder that scores each candidate independently "
              "and is trained with a pairwise ranking objective plus an explicit "
              "redundancy penalty: two passages whose token-level similarity exceeds a "
              "threshold tau are pushed apart in the ranking, so that the top of the list "
              "is diverse as well as relevant.", size=BODY),
        Block("Coverage is estimated by decomposing the question into atomic information "
              "needs with a small sequence-to-sequence model, then measuring the fraction "
              "of needs that are entailed by the union of the selected passages "
              "according to a natural language inference model. Let c(S) denote this "
              "fraction. Selection proceeds greedily: add the highest-scoring remaining "
              "passage, recompute c(S), and stop when c(S) exceeds a threshold theta or "
              "when the budget of n passages is exhausted.", size=BODY),
        Block("The threshold theta is calibrated on a held-out split to maximize the "
              "expected F1 of the reader minus a per-passage token cost, which makes the "
              "stopping rule sensitive to the price of generation rather than to accuracy "
              "alone. In practice theta = 0.72 for single-hop and theta = 0.85 for "
              "multi-hop questions, reflecting the higher evidence requirements of the "
              "latter.", size=BODY),

        Block("4  Experiments", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
        Block("Datasets and metrics. We evaluate on NaturalQuestions and TriviaQA "
              "(single-hop) and on HotpotQA and MuSiQue (multi-hop). We report exact "
              "match (EM) and token-level F1, and we count reader input tokens as a proxy "
              "for cost. All systems use the same frozen reader with greedy decoding.", size=BODY),
        Block("Baselines. We compare against BM25-only, dense-only, hybrid retrieval "
              "without reranking, and hybrid retrieval with a standard cross-encoder "
              "reranker that has no redundancy penalty and no coverage controller.", size=BODY),
        Block(en_filler("exp-setup-a", 6), size=BODY),
        Block(en_filler("exp-setup-b", 5), size=BODY),
    ])

    # 第 4 页顶部：整页宽数字表格
    table_rows = [
        ["System", "NQ EM", "NQ F1", "HotpotQA EM", "HotpotQA F1", "Musique EM", "Passages", "Tokens"],
        ["BM25 only", "38.2", "46.9", "29.4", "41.0", "14.8", "20", "4180"],
        ["Dense only", "41.7", "50.3", "31.8", "43.6", "16.2", "20", "4180"],
        ["Hybrid (no rerank)", "44.9", "53.8", "34.1", "46.2", "18.0", "20", "4180"],
        ["Hybrid + cross-encoder", "47.6", "56.4", "36.9", "49.1", "19.7", "20", "4180"],
        ["EARR (ours, theta=0.72)", "51.0", "59.2", "40.4", "52.8", "22.6", "12.4", "2590"],
        ["EARR (ours, no coverage)", "48.3", "57.0", "37.4", "49.6", "20.1", "20", "4180"],
        ["EARR (ours, no redundancy)", "49.1", "57.5", "38.2", "50.3", "20.8", "17.6", "3680"],
    ]
    flow.place_full_width_table(
        table_rows,
        "Table 1: End-to-end results. Passages is the average number of passages sent to "
        "the reader; Tokens is the average reader input length. EARR matches or exceeds "
        "the strongest baseline while using substantially fewer passages.")

    flow.place([
        Block("Main results. Table 1 summarizes the comparison. EARR improves exact match "
              "by 3.4 points on NaturalQuestions and by 4.1 points on HotpotQA over the "
              "strongest hybrid baseline, while reducing the average number of passages "
              "sent to the reader from 20 to 12.4 and reader input tokens by about 38 "
              "percent. The gains are largest on MuSiQue, the benchmark with the deepest "
              "reasoning chains, which is consistent with the hypothesis that coverage "
              "control matters most when no single passage suffices.", size=BODY),
        Block("Ablations. Removing the redundancy penalty costs 1.9 EM points on HotpotQA "
              "and 2.5 points on MuSiQue, but only 0.7 points on NaturalQuestions. "
              "Removing the coverage controller while keeping the reranker costs more on "
              "single-hop data than the redundancy penalty does, because the controller "
              "also acts as a precision filter: it stops early when one passage already "
              "satisfies all information needs.", size=BODY),
        Block("Sensitivity to retrieval depth. Increasing the fused candidate pool from 60 "
              "to 200 passages yields diminishing returns; beyond 120 passages the "
              "reranker's recall plateaus while its latency grows linearly. We therefore "
              "fix the pool at 120 for all reported numbers.", size=BODY),
        Block("Error analysis. We manually inspected 100 failures of EARR on HotpotQA. "
              "Forty-one involve a coverage estimate that exceeded the threshold while a "
              "comparison question remained unanswered; twenty-six involve entity "
              "ambiguity in the question; the remainder are reader errors that persist "
              "even when all gold passages are provided.", size=BODY),
        Block(en_filler("exp-results-a", 6), size=BODY),
        Block(en_filler("exp-results-b", 6), size=BODY),
        Block(en_filler("exp-results-c", 5), size=BODY),
        Block(en_filler("exp-results-d", 5), size=BODY),
        Block(en_filler("exp-results-e", 6), size=BODY),
        Block(en_filler("exp-results-f", 6), size=BODY),
        Block(en_filler("exp-results-g", 5), size=BODY),
        Block(en_filler("exp-results-h", 5), size=BODY),
        Block(en_filler("conclusion-a", 6), size=BODY),
        Block(en_filler("conclusion-b", 5), size=BODY),
        Block(en_filler("limitations-a", 6), size=BODY),
        Block(en_filler("limitations-b", 5), size=BODY),
        Block(en_filler("limitations-c", 5), size=BODY),

        Block("5  Conclusion", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
        Block("We presented EARR, a retrieval-augmented QA pipeline that treats evidence "
              "selection as an explicit decision problem. By estimating the coverage of "
              "atomic information needs before generation, EARR stops retrieving as soon "
              "as the selected passages jointly support an answer. The result is a system "
              "that is both more accurate and substantially cheaper than a strong hybrid "
              "baseline, with the largest gains on multi-hop questions. We hope this "
              "encourages future work to report evidence selection statistics alongside "
              "accuracy, since the two are tightly coupled in deployed systems.", size=BODY),

        Block("6  Limitations", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
        Block("Our coverage estimator inherits the errors of the underlying entailment "
              "model, which was trained on English natural language inference data and "
              "transfers imperfectly to technical domains. The atomic-need decomposition "
              "assumes that questions can be split into independent sub-questions, which "
              "fails for questions requiring arithmetic or temporal ordering. Finally, our "
              "cost analysis counts reader tokens but ignores retrieval infrastructure "
              "cost, which dominates in very large corpora; a full deployment study "
              "remains future work. We also did not evaluate on languages other than "
              "English, and the threshold calibration procedure assumes the availability "
              "of a labelled development set of at least two thousand questions.", size=BODY),

        Block("References", font="Times-Bold", size=11.0, leading=13.4,
              space_before=8.0, space_after=5.0),
    ])

    refs = [
        "A. Vaswani, N. Shazeer, N. Parmar, et al. Attention is all you need. In Advances "
        "in Neural Information Processing Systems, 2017.",
        "V. Karpukhin, B. Oguz, S. Min, et al. Dense passage retrieval for open-domain "
        "question answering. In Proceedings of EMNLP, pages 6769-6781, 2020.",
        "S. Robertson and H. Zaragoza. The probabilistic relevance framework: BM25 and "
        "beyond. Foundations and Trends in Information Retrieval, 3(4):333-389, 2009.",
        "G. Izacard and E. Grave. Leveraging passage retrieval with generative models for "
        "open domain question answering. In Proceedings of EACL, pages 874-880, 2021.",
        "R. Nogueira and K. Cho. Passage re-ranking with BERT. arXiv:1901.04085, 2019.",
        "O. Khattab and M. Zaharia. ColBERT: Efficient and effective passage search via "
        "contextualized late interaction over BERT. In Proceedings of SIGIR, 2020.",
        "P. Lewis, E. Perez, A. Piktus, et al. Retrieval-augmented generation for "
        "knowledge-intensive NLP tasks. In Advances in Neural Information Processing "
        "Systems, 2020.",
        "Z. Yang, P. Qi, S. Zhang, et al. HotpotQA: A dataset for diverse, explainable "
        "multi-hop question answering. In Proceedings of EMNLP, pages 2369-2380, 2018.",
        "T. Kwiatkowski, J. Palomaki, O. Redfield, et al. Natural questions: A benchmark "
        "for question answering research. Transactions of the ACL, 7:453-466, 2019.",
        "H. Trivedi, N. Balasubramanian, T. Khot, and A. Sabharwal. MuSiQue: Multihop "
        "questions through single-hop decomposition. Transactions of the ACL, 2022.",
        "K. Lee, M.-W. Chang, and K. Toutanova. Latent retrieval for weakly supervised "
        "open domain question answering. In Proceedings of ACL, pages 6085-6095, 2019.",
        "S. Min, K. Lee, M.-W. Chang, et al. Meta-learning to adaptively retrieve. In "
        "Findings of EMNLP, 2023.",
        "S. Y. Feng, V. Balachandran, Y. Bai, and J. Glass. Fact-aware retrieval for "
        "knowledge-grounded dialogue. In Proceedings of NAACL, 2023.",
        "N. Thakur, N. Reimers, A. Ruckle, A. Srivastava, and I. Gurevych. BEIR: A "
        "heterogeneous benchmark for zero-shot evaluation of information retrieval "
        "models. In NeurIPS Datasets and Benchmarks, 2021.",
        "J. Liu, J. Lin, and J. Callan. Reducing the cost of cross-encoder reranking with "
        "adaptive cascade ranking. In Proceedings of SIGIR, 2024.",
    ]
    flow.place([Block(r, size=8.4, leading=10.2, space_after=3.4, hang=10.0)
                for r in refs])

    total_pages = flow.finish_footers()
    save_deterministic(doc, path, "Retrieval-Augmented Question Answering with "
                                  "Evidence-Aware Reranking (EN 2-column fixture)")
    return total_pages


# --------------------------------------------------------------------------
# Fixture 2: 中文单栏 4 页论文
# --------------------------------------------------------------------------


def build_zh_single_column(path):
    doc = pymupdf.open()
    flow = Flow(doc, ZH_HEADER, "计算机科学与技术学院 - 自然语言处理实验室",
                ZH_FOOTER_TMPL, fontname="china-s", fontsize=8.0,
                top=BODY_TOP + 8.0, two_column=False, header_fontsize=8.4)

    title = "基于大模型的学术论文结构自动解析方法研究"
    authors = "张伟，李静，王浩然，陈思远"
    affil = "北方理工大学 计算机科学与技术学院，北京 100084"

    abstract = (
        "摘要：学术论文的自动化解析是构建科技文献知识库的关键环节。针对现有方法在长文档、"
        "多栏排版与复杂标题层级上准确率不足的问题，本文提出一种基于大模型的学术论文结构自动"
        "解析方法。该方法首先利用版面分析模型定位标题、正文、图表与参考文献等区域，然后将"
        "区域序列输入大语言模型，通过层次化提示学习完成章节标题识别与层级归并，最后结合规则"
        "后处理修正编号不一致与跨栏断行带来的错误。在自建的 1200 篇中英文论文数据集上，本文"
        "方法的章节边界识别 F1 值达到 94.7%，标题层级判定准确率达到 91.2%，分别比基线方法"
        "提升 5.3 与 6.8 个百分点。实验表明，引入大模型的语义先验能够显著缓解纯规则方法在"
        "标题样式多变时的脆弱性，同时保持可接受的计算开销。")
    keywords = "关键词：大语言模型；学术论文解析；标题层级识别；版面分析；文档结构抽取"

    flow.place_full_width([
        Block(title, font="china-ss", size=18.0, leading=24.0, align=1,
              space_after=10.0),
        Block(authors, font="china-s", size=11.5, leading=15.0, align=1,
              space_after=4.0),
        Block(affil, font="china-s", size=10.0, leading=13.0, align=1,
              space_after=12.0),
        Block(abstract, font="china-s", size=10.5, leading=16.8, align=0,
              space_after=8.0),
        Block(keywords, font="china-s", size=10.5, leading=16.8, align=0,
              space_after=10.0),
    ])

    flow.place([
        Block("1 引言", font="china-ss", size=13.5, leading=18.0,
              space_before=6.0, space_after=6.0),
        Block("随着科技文献数量的快速增长，科研人员难以及时获取论文内部的完整结构信息。"
              "通用搜索引擎与文献数据库通常只索引标题、作者与摘要，而论文的章节组织、"
              "方法描述与实验结果等结构化信息往往以 PDF 版面形式存在，难以被机器直接利用。"
              "因此，如何从 PDF 文档中自动恢复出符合语义的章节树，成为构建科技知识图谱与"
              "智能问答系统的前提条件。", font="china-s", size=10.5, leading=16.8),
        Block("早期研究主要依赖规则与样式特征，例如通过字号、字体、加粗与缩进来判断标题"
              "层级。这类方法在排版规范的论文上表现良好，但面对跨出版社模板、双栏混排、"
              "扫描件以及图表标题干扰时鲁棒性明显不足。近年来，基于深度学习的版面分析模型"
              "能够较准确地检测文本区域及其类别，然而区域类别并不等于章节层级，如何把区域"
              "序列映射为带编号的层级结构仍是一个开放问题。", font="china-s", size=10.5,
              leading=16.8),
        Block("本文的主要贡献包括三点：其一，提出一种区域序列到章节树的端到端解析框架，"
              "把标题识别与层级归并统一在一个生成式模型中；其二，设计层次化提示模板，"
              "显式引入编号模式与上下文窗口，缓解长文档中上下文缺失的问题；其三，构建了"
              "包含 1200 篇中英文论文的评测数据集，并给出细粒度的评测指标与错误分析。",
              font="china-s", size=10.5, leading=16.8),

        Block("2 相关工作", font="china-ss", size=13.5, leading=18.0,
              space_before=10.0, space_after=6.0),
        Block("文档版面分析。基于目标检测的版面分析模型把页面视为图像，输出文本块、表格、"
              "图片与公式等区域及其坐标。此类方法对扫描件效果良好，但通常不区分一级标题与"
              "二级标题，需要额外的层级判定模块。", font="china-s", size=10.5, leading=16.8),
        Block("标题层级识别。已有工作利用编号正则表达式与字体统计特征进行层级推断，"
              "并借助条件随机场或图神经网络建模相邻标题之间的关系。这类方法依赖于编号"
              "书写规范，当作者使用无编号标题或编号跳号时错误率显著上升。", font="china-s",
              size=10.5, leading=16.8),
        Block("大模型在文档理解中的应用。大语言模型在长文本摘要、信息抽取与结构化生成任务"
              "上展现出较强的语义理解能力。部分研究将其用于参考文献解析与表格抽取，但在"
              "章节树恢复任务上仍缺乏系统性的评估。", font="china-s", size=10.5, leading=16.8),

        Block("3 方法", font="china-ss", size=13.5, leading=18.0,
              space_before=10.0, space_after=6.0),
        Block("本文方法由版面分析、区域序列编码与层次化生成三个模块组成。给定一篇 PDF "
              "文档，系统首先逐页解析出文本区域及其版面属性，随后按阅读顺序拼接为区域序列，"
              "最后通过大模型生成章节标题及其层级编号。", font="china-s", size=10.5,
              leading=16.8),
        Block("3.1 版面分析", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("我们采用轻量检测网络对页面进行区域划分，输出正文、标题、图表标题、页眉页脚"
              "与参考文献五类区域。为处理双栏排版，算法首先依据文本块的水平投影确定栏边界，"
              "再在每一栏内部按纵坐标排序，从而得到符合人类阅读习惯的区域顺序。页眉页脚"
              "区域通过跨页重复检测剔除，即统计同一文本在文档中的出现频次与出现位置，"
              "若某文本在超过 80% 的页面中出现在页面上下边缘 8% 的区域内，则判定为样板文本。",
              font="china-s", size=10.5, leading=16.8),
        Block("3.2 层次化提示学习", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("给定区域序列，我们构造包含三个部分的提示：任务说明、编号模式示例与当前"
              "文档的候选标题列表。模型需要为每个候选标题输出层级编号与置信度。为了处理"
              "超出上下文窗口的长文档，我们采用滑窗切分并在窗口之间传递已确定的层级状态，"
              "保证跨窗口的编号连续性。", font="china-s", size=10.5, leading=16.8),
        Block("3.3 规则后处理", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("模型输出可能存在编号跳号、层级倒挂与重复标题等问题。我们设计了三类后处理"
              "规则：对编号跳号采用最近邻插值补齐；对层级倒挂依照上下文最大层级进行裁剪；"
              "对重复标题依据文本相似度与位置距离进行合并。后处理不改变模型的主要判断，"
              "但可以消除约 30% 的格式类错误。", font="china-s", size=10.5, leading=16.8),

        Block("4 实验结果与分析", font="china-ss", size=13.5, leading=18.0,
              space_before=10.0, space_after=6.0),
        Block("4.1 实验设置", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("数据集包含 800 篇中文论文与 400 篇英文论文，覆盖计算机、物理与医学三个学科，"
              "每篇论文的章节树均由两名标注人员独立标注并交叉校验。评价指标为章节边界"
              "识别的精确率、召回率与 F1 值，以及标题层级判定的准确率。基线方法包括纯规则"
              "方法、版面分析加启发式层级判定，以及未使用层次化提示的通用大模型方法。",
              font="china-s", size=10.5, leading=16.8),
        Block("4.2 主要结果", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("如表 1 所示，本文方法在全部指标上均优于基线。相比纯规则方法，F1 值提升"
              "5.3 个百分点，层级判定准确率提升 6.8 个百分点；相比未使用层次化提示的"
              "大模型方法，F1 值仍有 2.6 个百分点的提升，说明提示设计对结构化解构任务"
              "具有实质影响。", font="china-s", size=10.5, leading=16.8),
    ])

    zh_table = [
        ["方法", "精确率/%", "召回率/%", "F1/%", "层级准确率/%", "耗时/s"],
        ["纯规则方法", "86.4", "84.1", "85.2", "79.5", "3.2"],
        ["版面分析+启发式", "89.7", "87.9", "88.8", "84.4", "12.6"],
        ["通用大模型", "91.2", "90.6", "90.9", "88.3", "48.7"],
        ["本文方法", "95.1", "94.3", "94.7", "91.2", "36.4"],
        ["本文方法（无后处理）", "93.8", "92.5", "93.1", "89.0", "35.1"],
    ]
    flow.place_full_width_table(zh_table, "表 1  不同方法在自建数据集上的性能对比",
                                width_frac=1.0, fontname="china-s",
                                fontname_bold="china-ss", cap_font="china-ss",
                                fontsize=8.6)

    flow.place([
        Block("4.3 消融实验", font="china-ss", size=11.5, leading=16.0,
              space_before=4.0, space_after=5.0),
        Block("为验证各模块的作用，我们分别移除跨页样板检测、层次化提示与规则后处理。"
              "移除跨页样板检测后，页眉页脚被误判为标题，导致精确率下降 3.1 个百分点；"
              "移除层次化提示后，层级判定准确率下降 4.7 个百分点；移除规则后处理主要"
              "影响编号跳号样本，召回率下降 1.8 个百分点。", font="china-s", size=10.5,
              leading=16.8),
        Block("4.4 错误分析", font="china-ss", size=11.5, leading=16.0,
              space_before=6.0, space_after=5.0),
        Block("我们对 120 个失败样本进行了人工分析。其中 42% 的错误来自图表标题被误判为"
              "章节标题，主要集中在图表标题采用与正文相同字号的模板；27% 来自扫描件中"
              "文本区域检测不完整；19% 来自作者使用了非标准的自定义编号体系；其余错误"
              "与多语言混排有关。这些观察为后续改进指明了方向。", font="china-s",
              size=10.5, leading=16.8),

        Block("5 结论与展望", font="china-ss", size=13.5, leading=18.0,
              space_before=10.0, space_after=6.0),
        Block("本文提出了一种基于大模型的学术论文结构自动解析方法，通过区域序列建模、"
              "层次化提示学习与规则后处理，在自建数据集上取得了 94.7% 的 F1 值与 91.2% 的"
              "层级判定准确率。实验表明，语义先验与结构约束的结合能够有效提升解析鲁棒性。"
              "未来工作将从三个方向展开：一是引入视觉信息以处理扫描件与复杂公式；二是"
              "扩展到跨语言与跨学科场景，构建统一的结构解析模型；三是研究在线增量解析，"
              "支持论文预印本的实时结构化入库。", font="china-s", size=10.5, leading=16.8),

        Block("参考文献", font="china-ss", size=13.5, leading=18.0,
              space_before=10.0, space_after=6.0),
    ])

    zh_refs = [
        "[1] 王浩然, 张伟. 面向科技文献的版面分析方法综述[J]. 计算机学报, 2024, 47(3): 512-530.",
        "[2] 李静, 陈思远. 基于预训练语言模型的文档结构抽取[J]. 软件学报, 2023, 34(11): 4901-4918.",
        "[3] 赵敏, 刘洋. 长文档层次化建模研究进展[J]. 自动化学报, 2025, 51(2): 233-249.",
        "[4] 孙鹏, 周颖. 中文 PDF 文档阅读顺序恢复方法[J]. 中文信息学报, 2024, 38(6): 78-89.",
        "[5] Xu Y, Li Z, Zhang H, et al. A benchmark for document layout analysis in "
        "scientific papers[C]. Proceedings of ICDAR, 2023: 220-235.",
        "[6] Chen L, Wang R, Sun Q. Hierarchical structure recovery from scholarly PDFs "
        "with large language models[C]. Proceedings of ACL, 2024: 1188-1201.",
        "[7] Brown T, Mann B, Ryder N, et al. Language models are few-shot learners[C]. "
        "Advances in Neural Information Processing Systems, 2020: 1877-1901.",
        "[8] Zhang Y, Liu M, Chen X. Cross-page boilerplate detection for web-scale "
        "document parsing[J]. Information Processing and Management, 2024, 61(4): 103-119.",
    ]
    flow.place([Block(r, font="china-s", size=9.5, leading=14.5, space_after=3.0,
                      hang=14.0) for r in zh_refs])

    total_pages = flow.finish_footers()
    save_deterministic(doc, path,
                       "基于大模型的学术论文结构自动解析方法研究 (ZH fixture)")
    return total_pages


# --------------------------------------------------------------------------
# Fixture 3: 24 页长文档 + 深层编号大纲
# --------------------------------------------------------------------------

TOPICS = [
    "document ingestion", "layout segmentation", "reading order recovery",
    "heading detection", "hierarchy induction", "table extraction",
    "formula recognition", "reference parsing", "metadata reconciliation",
    "cross-page boilerplate filtering", "multi-column flow analysis",
    "section boundary scoring", "outline normalization", "long-context chunking",
    "semantic section typing", "figure caption association", "footnote separation",
    "citation linking", "language identification", "OCR fallback routing",
]

SUBTOPICS = {
    "document ingestion": ["file format normalization", "page rasterization", "text layer validation", "encoding repair"],
    "layout segmentation": ["region proposal generation", "column boundary estimation", "block clustering", "noise suppression"],
    "reading order recovery": ["intra-column ordering", "cross-column stitching", "footnote routing", "caption anchoring"],
    "heading detection": ["typographic feature design", "numbered heading matching", "unnumbered heading inference", "negative example mining"],
    "hierarchy induction": ["depth estimation", "parent child assignment", "level smoothing", "outline validation"],
    "table extraction": ["cell grid reconstruction", "spanning cell handling", "numeric column typing", "table caption linking"],
    "formula recognition": ["inline formula isolation", "display formula grouping", "symbol dictionary construction", "latex normalization"],
    "reference parsing": ["reference block segmentation", "field level tagging", "author name disambiguation", "venue normalization"],
    "metadata reconciliation": ["title matching", "author affiliation binding", "venue string canonicalization", "duplicate merging"],
    "cross-page boilerplate filtering": ["repetition statistics", "margin band detection", "page number templating", "running head removal"],
    "multi-column flow analysis": ["gutter detection", "column continuation tracking", "float detection", "widow paragraph handling"],
    "section boundary scoring": ["boundary classifier training", "confidence calibration", "sliding window decoding", "boundary refinement"],
    "outline normalization": ["numbering scheme detection", "skipped level repair", "duplicate heading merging", "appendix handling"],
    "long-context chunking": ["chunk size selection", "overlap strategy", "state carry over", "memory budget planning"],
    "semantic section typing": ["section label taxonomy", "weak supervision design", "label imbalance handling", "taxonomy extension"],
    "figure caption association": ["caption pattern matching", "spatial proximity scoring", "multi-panel figure grouping", "credit line stripping"],
    "footnote separation": ["marker detection", "column bottom routing", "endnote distinction", "note reattachment"],
    "citation linking": ["citation span detection", "bibliography alignment", "ambiguous key resolution", "cross-reference graph construction"],
    "language identification": ["script level detection", "mixed language segments", "transliteration handling", "font encoding hints"],
    "OCR fallback routing": ["text layer quality scoring", "engine selection policy", "confidence threshold tuning", "post OCR repair"],
}

FRAMES = [
    "In this section we describe {T}. The design goal is to make {T} reliable on documents that were never seen during development, and to keep its failure modes predictable for downstream consumers.",
    "We treat {T} as a bounded subproblem of the overall parsing pipeline. {T} receives a normalized page representation and emits a typed structure together with a confidence value that later stages may abstain on.",
    "The implementation of {T} follows three principles: determinism, observability, and graceful degradation. None of the three is optional once the pipeline is deployed over heterogeneous corpora.",
    "Empirically, {T} accounts for a measurable share of end-to-end errors. We therefore instrument it with counters for rejected inputs, ambiguous outputs, and fallback invocations.",
    "An important design decision concerns the granularity at which {T} operates. Too coarse a granularity hides local evidence, while too fine a granularity makes the aggregation step unstable.",
    "We evaluated {T} against two reference implementations. The comparison is reported in the accompanying result tables and discussed in the analysis subsection below.",
    "The cost of {T} is dominated by the number of candidate regions rather than by the number of pages, which makes the per-document cost essentially linear in practice.",
    "Because {T} interacts with neighbouring stages, we specified an explicit interface contract: every field carries a provenance tag so that errors can be traced back to the stage that produced them.",
    "Our first prototype of {T} relied on hand written heuristics. Replacing them with a calibrated scoring function reduced variance across publishers without hurting the average case.",
    "A recurring difficulty for {T} is the tension between recall and precision on atypical templates. We resolved it by deferring borderline decisions to the verification stage.",
    "We report the behaviour of {T} on adversarial inputs separately, since averages over the full corpus hide the tail that matters most for robustness.",
    "The current version of {T} is deliberately conservative: when the evidence is weak it emits a low confidence record instead of guessing, and the pipeline treats such records as soft failures.",
]

DETAILS = [
    "the tuned configuration uses a window of {a} units, a stride of {b} units and a confidence floor of 0.{c}",
    "we fixed the learning rate schedule to cosine decay with a warmup of {a} steps and early stopping patience of {b} epochs",
    "the default threshold is set to 0.{c}, chosen on the development split to maximize the harmonic mean of precision and recall",
    "a single pass over the corpus of {a} documents completes in {b} minutes on eight worker processes without GPU acceleration",
    "we cap the candidate list at {a} entries and truncate each entry to {b} characters before scoring",
    "annotation guidelines were revised {a} times, and inter annotator agreement settled at kappa equal to 0.{c}",
    "the ablation removes one component at a time while holding all random seeds fixed, so differences above 0.{c} are treated as meaningful",
    "memory usage stays below {a} megabytes per worker because intermediate representations are streamed rather than materialized",
    "error bars are obtained by bootstrapping {a} resamples of the evaluation set, reported as a {b} percent confidence interval",
    "the fallback path is triggered for roughly {a} percent of pages, mostly scanned documents with a damaged text layer",
]

RESULT_POOL = [
    ("section boundary F1", "94.7", "92.1", "2.6"),
    ("heading level accuracy", "91.2", "86.5", "4.7"),
    ("reading order accuracy", "96.3", "94.8", "1.5"),
    ("table cell F1", "88.4", "84.0", "4.4"),
    ("reference field F1", "93.1", "89.6", "3.5"),
    ("boilerplate removal precision", "99.2", "97.4", "1.8"),
    ("outline depth exact match", "87.6", "81.2", "6.4"),
    ("cross-page continuation F1", "90.8", "85.3", "5.5"),
]


def _seed(*parts):
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))


def _pick(pool, *parts, offset=0):
    """按确定性序号从词表取值（不使用 random）。"""
    return pool[(_seed(*parts) + offset) % len(pool)]


EN_FILLER = [
    "This effect is stable across retrieval depths, which suggests that the controller "
    "is reacting to evidence structure rather than to the size of the candidate pool.",
    "We additionally verified that the ordering produced by the reranker is monotone "
    "with respect to the coverage estimate on 92 percent of the development questions.",
    "A practical consequence is that the same pipeline can be operated under a strict "
    "latency budget by lowering the passage budget without retraining any component.",
    "We report the median over five runs together with the interquartile range, since "
    "the distribution of reader input lengths is visibly skewed.",
    "The same interface also lets a downstream verifier request additional evidence "
    "when it disagrees with the generated answer, which we leave to future work.",
    "Careful prompt construction matters here: small changes in the instruction wording "
    "shift the measured coverage by up to four points on the multi-hop split.",
    "We therefore treat the coverage estimate as a ranking signal rather than as a "
    "calibrated probability, and we never threshold it for a hard decision.",
    "Finally, all reported numbers use the same frozen reader checkpoint, so the "
    "comparison isolates the contribution of evidence selection alone.",
]


def en_filler(seed, n=5):
    """确定性生成的补充段落（英文论文用，保证内容真实可读且可复现）。"""
    parts = []
    for i in range(n):
        parts.append(EN_FILLER[(_seed("enfill", seed) + i * 3) % len(EN_FILLER)])
    return " ".join(parts)


def make_paragraph(section_no, topic, para_idx, sentences=4):
    """由确定性词表拼装一段"该小节独有"的正文。"""
    out = []
    for i in range(sentences):
        base = _pick(FRAMES, section_no, topic, para_idx, i)
        detail = _pick(DETAILS, section_no, i, para_idx, offset=7)
        detail = (detail.replace("{a}", str(3 + (_seed(section_no, i) % 40)))
                        .replace("{b}", str(2 + (_seed(section_no, i, "b") % 27)))
                        .replace("{c}", str(1 + (_seed(section_no, i, "c") % 8))))
        out.append(base.format(T=topic).rstrip() + " Specifically, " + detail + ".")
    return " ".join(out)


def build_long_outline(path):
    doc = pymupdf.open()
    flow = Flow(doc, OUTLINE_HEADER, "", OUTLINE_FOOTER_TMPL,
                fontname="Times-Roman", fontsize=8.0, top=MARGIN + 4.0,
                header_fontsize=8.2)

    flow.place_full_width([
        Block("Structured Analysis of Long Technical Documents", font="Times-Bold",
              size=20.0, leading=24.0, align=1, space_after=12.0),
        Block("Technical Report TR-2026-114", font="Times-Roman", size=11.5,
              leading=14.0, align=1, space_after=4.0),
        Block("Document Intelligence Group, Northgate University", font="Times-Italic",
              size=10.5, leading=13.0, align=1, space_after=18.0),
        Block("Abstract", font="Times-Bold", size=12.5, leading=15.0, align=1,
              space_after=8.0),
        Block(
            "This report documents the design, implementation and evaluation of a "
            "pipeline that recovers the structured outline of long technical documents. "
            "The report is organized as a deeply nested document: chapters are numbered "
            "in the 1 style, sections in the 1.1 style, and subsections in the 1.1.1 "
            "style, so that a parser must reconstruct three levels of hierarchy from "
            "typography alone. Each subsection contains genuinely distinct prose drawn "
            "from the vocabulary of its own subproblem, which makes the fixture useful "
            "for testing heading detection, hierarchy induction and long-document "
            "chunking under realistic conditions. The evaluation in Chapter 4 reports "
            "section boundary F1, heading level accuracy and outline depth exact match "
            "for four reference systems, and the final two pages contain the "
            "bibliography that a reference parser is expected to segment.",
            font="Times-Roman", size=10.8, leading=13.4, align=3, space_after=10.0),
        Block("Keywords: long document parsing; outline recovery; heading detection; "
              "hierarchy induction; technical reports", font="Times-Italic",
              size=10.0, leading=12.4, align=3, space_after=8.0),
    ])

    chapters = [
        ("1", "Introduction", ["Motivation and Scope", "Problem Statement",
                               "Contributions", "Report Organization"]),
        ("2", "System Architecture", ["Pipeline Overview", "Interface Contracts",
                                      "Data Representations", "Failure Handling"]),
        ("3", "Core Components", ["Layout Processing", "Structure Recovery",
                                  "Semantic Annotation", "Quality Control"]),
        ("4", "Evaluation", ["Benchmarks and Metrics", "Main Results",
                             "Ablation Studies", "Error Analysis"]),
        ("5", "Deployment and Conclusion", ["Throughput and Latency",
                                            "Resource Budgets", "Monitoring",
                                            "Summary and Future Work"]),
    ]

    for ch_no, ch_title, secs in chapters:
        ci = int(ch_no)
        flow.place([
            Block(f"{ch_no}  {ch_title}", font="Times-Bold", size=14.0,
                  leading=17.5, space_before=10.0, space_after=6.0),
            Block(make_paragraph(f"{ch_no}", TOPICS[ci % len(TOPICS)], 0,
                                 sentences=4), size=10.6, leading=13.2),
        ])
        for si, sec_title in enumerate(secs, start=1):
            sec_no = f"{ch_no}.{si}"
            topic = TOPICS[(ci * 4 + si) % len(TOPICS)]
            flow.place([
                Block(f"{sec_no}  {sec_title}", font="Times-Bold", size=12.0,
                      leading=15.0, space_before=7.0, space_after=4.0),
                Block(make_paragraph(sec_no, topic, 0, sentences=4),
                      size=10.6, leading=13.2),
            ])
            # 每个 1.1 级小节下挂两个 1.1.1 级子小节（严格三级编号，不再嵌套更深）
            subs = SUBTOPICS[topic]
            for sssi, sub_title in enumerate(subs[:2], start=1):
                leaf_no = f"{sec_no}.{sssi}"
                leaf_topic = subs[(sssi + _seed(sec_no, sssi)) % len(subs)]
                blocks = [
                    Block(f"{leaf_no}  {sub_title.title()}",
                          font="Times-Bold", size=10.4, leading=13.0,
                          space_before=5.0, space_after=3.0),
                    Block(make_paragraph(leaf_no, leaf_topic, 0, sentences=5),
                          size=10.5, leading=13.0),
                ]
                # 约 1/4 的小节再加一段，用于把长文档填充到 24 页
                if _seed("extra", leaf_no) % 4 == 0:
                    blocks.append(Block(
                        make_paragraph(leaf_no, leaf_topic, 1, sentences=3),
                        size=10.5, leading=13.0))
                # 部分小节插入纯文本结果表，模拟报告中的数字表格。
                # 表格绘制在当前栏内，所以先排标题块，再按当前栏 x / 剩余高度画表。
                if _seed(leaf_no) % 11 == 0:
                    label, ours, base, delta = _pick(RESULT_POOL, leaf_no)
                    rows = [["System", label + " (%)", "delta"],
                            ["Rule based pipeline", base, "-"],
                            ["Layout model + heuristics",
                             str(round(float(base) + 1.8, 1)), "+1.8"],
                            ["Proposed pipeline", ours, "+" + delta]]
                    blocks.append(Block(f"Table {leaf_no}: {leaf_topic}.",
                                        font="Times-Italic", size=9.8,
                                        leading=12.0, align=1,
                                        space_before=3.0, space_after=2.0))
                    flow.place(blocks)
                    flow.space(len(rows) * 12.4 + 14.0)
                    tx, tw = flow.col_x(flow.col)
                    flow.y = draw_table(flow.page, tx + 4.0, flow.y + 8.0,
                                        rows, tw - 8.0, fontsize=8.4) + 6.0
                else:
                    flow.place(blocks)

    # 最后 2 页：References（强制换页）
    flow.new_page()
    flow.place([Block("References", font="Times-Bold", size=14.0, leading=17.5,
                      space_before=0.0, space_after=8.0)])
    refs = []
    for i in range(1, 49):
        topic = TOPICS[i % len(TOPICS)]
        refs.append(
            f"[{i}] {_pick(['Author A', 'Author B', 'Author C', 'Author D'], i)} "
            f"{_pick(['Smith', 'Kumar', 'Nowak', 'Tanaka', 'Ferreira', 'Okafor'], i, 's')}, "
            f"{_pick(['J.', 'M.', 'R.', 'L.', 'K.'], i, 'i')} "
            f"{_pick(['Anderson', 'Bergman', 'Costa', 'Dubois', 'Eriksen'], i, 'f')}. "
            f"{_pick(['Robust', 'Scalable', 'Efficient', 'Calibrated', 'Hierarchical'], i, 't')} "
            f"{topic} for heterogeneous scholarly corpora. "
            f"In Proceedings of the {2015 + (i % 11)} {_pick(['International Conference on Document Analysis', 'Annual Meeting of the Association for Computational Linguistics', 'ACM Symposium on Document Engineering', 'Joint Conference on Digital Libraries'], i, 'v')}, "
            f"pages {100 + i * 7}-{100 + i * 7 + 11}, {2015 + (i % 11)}.")
    flow.place([Block(r, size=9.4, leading=11.8, space_after=3.2, hang=16.0,
                      font="Times-Roman") for r in refs])

    total_pages = flow.finish_footers()
    save_deterministic(doc, path,
                       "Structured Analysis of Long Technical Documents (24-page outline fixture)")
    return total_pages


# --------------------------------------------------------------------------
# Fixture 4: 退化 / 对抗样例
# --------------------------------------------------------------------------


def build_scan_like(path):
    doc = pymupdf.open()

    # ---- 第 1 页：纯矢量矩形，模拟扫描页（几乎没有可抽取文本）----------
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p1.insert_text((MARGIN, HEADER_Y), SCAN_HEADER, fontname="Courier",
                   fontsize=8.0, color=(0.3, 0.3, 0.3))
    # 纸面底色
    p1.draw_rect(pymupdf.Rect(40, 52, PAGE_W - 40, PAGE_H - 52),
                 color=(0.75, 0.75, 0.75), fill=(0.94, 0.94, 0.92), width=1.2)
    # 模拟文字行的灰色条带（矢量形状，不含任何文本）
    y = 84.0
    i = 0
    while y < PAGE_H - 120:
        w = 150.0 + (_seed("scan", i) % 300)
        p1.draw_rect(pymupdf.Rect(62, y, 62 + w, y + 7.5),
                     color=None, fill=(0.28, 0.28, 0.30))
        y += 15.0
        i += 1
    # 歪斜的"页眉线"与噪点块，模拟扫描伪影
    for k in range(6):
        x0 = 70 + (_seed("noise", k) % 380)
        y0 = 120 + (_seed("noiseY", k) % 560)
        p1.draw_rect(pymupdf.Rect(x0, y0, x0 + 6, y0 + 6), color=None,
                     fill=(0.55, 0.55, 0.58))
    # 模拟一块被"贴上去"的插图
    p1.draw_rect(pymupdf.Rect(96, 420, 300, 560), color=(0.2, 0.2, 0.2),
                 fill=(0.86, 0.88, 0.90), width=1.0)
    for k in range(5):
        p1.draw_line(pymupdf.Point(110, 440 + k * 26),
                     pymupdf.Point(286, 440 + k * 26 + 8),
                     color=(0.35, 0.35, 0.45), width=1.6)
    p1.insert_text((PAGE_W - MARGIN - text_width("1", "Courier", 8.0), FOOTER_Y),
                   "1", fontname="Courier", fontsize=8.0,
                   color=(0.3, 0.3, 0.3))

    # ---- 第 2 页：仅一行稀疏文本 ---------------------------------------
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p2.insert_text((MARGIN, HEADER_Y), SCAN_HEADER, fontname="Courier",
                   fontsize=8.0, color=(0.3, 0.3, 0.3))
    p2.draw_rect(pymupdf.Rect(40, 52, PAGE_W - 40, PAGE_H - 52),
                 color=(0.75, 0.75, 0.75), fill=(0.97, 0.97, 0.96), width=1.2)
    p2.insert_text((MARGIN + 40, 300), "Figure 2: page 2 of the scan fixture.",
                   fontname="Times-Roman", fontsize=11.0)
    p2.insert_text((PAGE_W - MARGIN - text_width("2", "Courier", 8.0), FOOTER_Y),
                   "2", fontname="Courier", fontsize=8.0,
                   color=(0.3, 0.3, 0.3))

    # ---- 第 3 页：文本存在但内容流顺序 ≠ 阅读顺序 ----------------------
    p3 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p3.insert_text((MARGIN, HEADER_Y), SCAN_HEADER, fontname="Courier",
                   fontsize=8.0, color=(0.3, 0.3, 0.3))
    lines = [
        "The quick brown fox jumps over the lazy dog.",
        "This sentence was written first but appears second.",
        "Reading order in this page is deliberately scrambled.",
        "A parser must not assume content stream order is logical order.",
        "Numbers: 3.14159 2.71828 1.41421 0.57721",
        "Mixed language: 中文与 English 混排测试。",
        "Trailing fragment without terminal punctuation",
    ]
    # 先写最后一行 -> 内容流顺序与视觉顺序相反
    ys = [300 - 26 * k for k in range(len(lines))]
    for idx in range(len(lines) - 1, -1, -1):
        p3.insert_text((MARGIN + 30, ys[idx]), lines[idx],
                       fontname="Times-Roman", fontsize=10.5)
    # 旋转 90 度的侧边标注：只有几何信息才能把它放回正确位置
    p3.insert_textbox(pymupdf.Rect(PAGE_W - 60, 120, PAGE_W - 20, 420),
                      "SIDEBAR: rotated annotation block, only geometry "
                      "reveals its true reading position.",
                      fontname="Times-Italic", fontsize=9.0, rotate=90)
    # 一个没有文本层的"表格"区域：纯线条
    for k in range(4):
        p3.draw_line(pymupdf.Point(MARGIN + 30, 360 + k * 18),
                     pymupdf.Point(MARGIN + 330, 360 + k * 18),
                     color=(0.4, 0.4, 0.4), width=0.7)
    p3.insert_text((MARGIN + 30, 430),
                   "Above: an empty grid with no text layer at all.",
                   fontname="Times-Roman", fontsize=9.5)
    p3.insert_text((PAGE_W - MARGIN - text_width("3", "Courier", 8.0), FOOTER_Y),
                   "3", fontname="Courier", fontsize=8.0,
                   color=(0.3, 0.3, 0.3))

    total_pages = doc.page_count
    save_deterministic(doc, path,
                       "Scanned-like degenerate fixture (adversarial sample)")
    return total_pages


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

BUILDERS = [
    ("sample_en_two_column.pdf", build_en_two_column, True),
    ("sample_zh_single_column.pdf", build_zh_single_column, True),
    ("sample_long_outline.pdf", build_long_outline, True),
    ("sample_scan_like.pdf", build_scan_like, False),
]


def main():
    os.makedirs(HERE, exist_ok=True)
    rows = []
    print("生成样例 PDF 到:", HERE)
    print()
    for name, builder, need_text in BUILDERS:
        path = os.path.join(HERE, name)
        expected_pages = builder(path)
        pages, texts = reopen_text(path)

        # 自检：重新打开并断言应有文本的页面确实能抽出非空文本
        if need_text:
            empty = [i + 1 for i, t in enumerate(texts) if not t.strip()]
            assert not empty, f"{name}: 第 {empty} 页抽取不到文本"
            assert pages == expected_pages, (
                f"{name}: 页数不一致 {pages} != {expected_pages}")
        chars = sum(len(t) for t in texts)
        size_kb = os.path.getsize(path) / 1024.0
        rows.append((name, pages, chars, size_kb))
        if need_text:
            print(f"  [自检通过] {name}: {pages} 页, 抽取 {chars} 字符, "
                  f"{size_kb:.1f} KB")
        else:
            print(f"  [对抗样例，跳过非空断言] {name}: {pages} 页, "
                  f"抽取 {chars} 字符, {size_kb:.1f} KB")

    total_kb = sum(r[3] for r in rows)
    print()
    print(f"{'filename':<32}{'pages':>7}{'extracted_chars':>18}{'size_KB':>12}")
    print("-" * 69)
    for name, pages, chars, size_kb in rows:
        print(f"{name:<32}{pages:>7}{chars:>18}{size_kb:>12.1f}")
    print("-" * 69)
    print(f"{'TOTAL':<32}{sum(r[1] for r in rows):>7}"
          f"{sum(r[2] for r in rows):>18}{total_kb:>12.1f}")
    assert total_kb < 2048.0, "全部 PDF 总大小超过 2 MB"
    print()
    print("完成：4 个样例 PDF 已生成并通过自检。")


if __name__ == "__main__":
    main()
