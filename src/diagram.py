"""Mermaid 导图生成、语法预校验与自动修复。

对应评分项："看板可用"（语法正确、层级清晰）+ "稳定鲁棒"（生成前校验、失败可修复）。

原则：模型绝不直接产出 Mermaid 代码。模型只给结构化 JSON，
由本模块确定性生成语法安全的 Mermaid，因此"语法错误"只可能来自标签内容，
而标签内容会被 sanitize_label 主动清洗，并有 validate() 兜底 + 自动降级。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from .textutils import as_page_number, has_cjk, normalize, truncate

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DIAGRAM_TYPE = "flowchart TD"

SECTION_TITLES: dict[str, str] = {
    "root": "论文概览",
    "problem": "研究问题",
    "arguments": "论证路径",
    "method": "方法路径",
    "contributions": "核心贡献",
    "evidence": "实验与证据",
    "conclusions": "结论",
    "limitations": "局限与风险",
    "critique": "批判性分析",
    "glossary": "通俗解读",
}

# 危险字符 → 安全替代（保留可读性，同时避免破坏 Mermaid 语法）
_CHAR_REPLACEMENTS = {
    '"': "'",
    "`": "'",
    "(": "（",
    ")": "）",
    "[": "【",
    "]": "】",
    "{": "｛",
    "}": "｝",
    "|": "/",
    ";": "；",
    "#": "＃",
    "&": "和",
    "<": "＜",
    ">": "＞",
    "\\": "/",
    "\n": " ",
    "\r": " ",
    "\t": " ",
}

_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_UNBALANCED_PAIRS = {"[": "]", "(": ")", "{": "}"}


# ---------------------------------------------------------------------------
# 标签清洗与截断
# ---------------------------------------------------------------------------
def sanitize_label(text: str, limit: int = 26) -> str:
    """把任意模型文本变成可安全放进 Mermaid 方括号标签的短字符串。"""
    cleaned = normalize(text)
    if not cleaned:
        return "（无内容）"
    for bad, good in _CHAR_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, good)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()

    cleaned = _smart_truncate(cleaned, limit)
    # 去掉首尾可能残留的标点，读起来更干净
    cleaned = cleaned.strip(" .，。、；;：:·-—_")
    return cleaned or "（无内容）"


def _smart_truncate(text: str, limit: int) -> str:
    """中文按字符数、英文按词数截断。"""
    if len(text) <= limit:
        return text
    if has_cjk(text):
        # 中文：优先在标点处断开
        head = text[:limit]
        for punct in ("，", "、", "。", "；", "：", " ", ","):
            idx = head.rfind(punct)
            if idx >= limit * 0.6:
                return head[:idx]
        return head + "…"
    words = text.split(" ")
    if len(words) <= 1:
        return text[:limit] + "…"
    out: list[str] = []
    total = 0
    for word in words:
        if total + len(word) + 1 > limit:
            break
        out.append(word)
        total += len(word) + 1
    return (" ".join(out) or words[0]) + "…"


def _quote(label: str) -> str:
    """已清洗的标签用双引号包裹，进一步消除语法歧义。"""
    return '"' + label.replace('"', "'") + '"'


# ---------------------------------------------------------------------------
# 标签确定性换行（紧凑布局）
# ---------------------------------------------------------------------------
# 节点标签单行目标宽度与最大行数：中文/全角按 1、英文/数字按 0.5 计。
# 同一分区的一排节点若被超长单行标签撑开，整图就会又宽又扁，
# 因此超过目标宽度的标签在自然断点折行（Mermaid 里用 <br/> 连接）。
LABEL_WRAP_WIDTH = 16
LABEL_MAX_LINES = 3

# 圆形（根节点）与菱形（研究问题）节点的折行参数要比普通矩形更窄：
# 这两类形状由 Mermaid 按文字外接矩形整体膨胀（圆直径 ≈ 最宽行 × √2 + 边距，
# 菱形外框 ≈ 文字框的外接正方形），文字越宽整体越大、四周空白越多，
# 因此单行目标宽度收窄到 10~11 全角（实测菱形 320px → 248px，圆 239px → 159px）；
# 行数上限保持 3 行——行数再放宽会把形状纵向拉高（"不过度分行"），
# 且既有测试约定节点标签不超过 3 行（<br/> ≤ 2）。
ROOT_WRAP_WIDTH = 11
ROOT_MAX_LINES = 3
ROOT_LABEL_MAX_WIDTH = 24   # 根标签总宽上限（全角），超出自动省略
DIAMOND_WRAP_WIDTH = 10
DIAMOND_MAX_LINES = 3

# 词块切分：英文/数字串（含内部 ' - . _，如 CIFAR-10、92.4、state-of-the-art）
# 是不可拆分的词块；其余字符逐字成块（中文等全角字符之间的边界即自然断点）。
_LABEL_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+(?:['\-.][0-9A-Za-z_]+)*|[\s\S]")


def _char_width(ch: str) -> float:
    """单个字符的显示宽度：中文/全角记 1，英文/数字等半角记 0.5。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("F", "W") else 0.5


def _display_width(text: str) -> float:
    """文本的显示宽度（全角 1 / 半角 0.5），用于决定折行位置。"""
    return sum(_char_width(ch) for ch in text)


def _wrap_tokens(text: str) -> list[tuple[int, int, float, bool]]:
    """把文本切成"不可再拆"的最小块：(起点, 终点, 显示宽度, 是否空格)。

    * 英文/数字词块保持原子性，绝不从单词中间断开；
    * 紧贴在非空格块之后的单个标点/符号并入前块——换行只发生在标点之后，
      避免标点（如 ，。％…）落到行首；
    * 空格单独成块，折行时随切片自然丢弃，不会出现在行首/行尾。
    """
    tokens: list[list] = []
    for match in _LABEL_TOKEN_RE.finditer(text):
        start, end = match.span()
        chunk = match.group()
        if chunk == " ":
            tokens.append([start, end, 0.5, True])
            continue
        if (
            tokens
            and end - start == 1
            and tokens[-1][1] == start
            and not tokens[-1][3]
            and unicodedata.category(chunk)[0] in ("P", "S")
        ):
            tokens[-1][1] = end
            tokens[-1][2] += _char_width(chunk)
            continue
        tokens.append([start, end, _display_width(chunk), False])
    return [tuple(item) for item in tokens]


def _wrap_lines(text: str, width: float, max_lines: int) -> list[str]:
    """贪心按显示宽度折行；超出 ``max_lines`` 的内容在末行截断并加省略号。"""
    lines: list[str] = []
    line_start = line_end = -1
    line_width = 0.0
    pending_space = False
    for start, end, token_width, is_space in _wrap_tokens(text):
        if is_space:
            if line_start >= 0:
                pending_space = True
            continue
        if line_start < 0:
            line_start, line_end, line_width = start, end, token_width
            continue
        extra = token_width + (0.5 if pending_space else 0.0)
        if line_width + extra <= width:
            line_width += extra
            line_end = end
            pending_space = False
            continue
        if len(lines) == max_lines - 1:
            # 已是允许的最后一行仍放不下：截断加省略号
            lines.append(text[line_start:line_end] + "…")
            return lines
        lines.append(text[line_start:line_end])
        # 触发折行的当前块成为新行的第一个块（断点处的空格随之丢弃）
        line_start, line_end, line_width = start, end, token_width
        pending_space = False
    if line_start >= 0:
        lines.append(text[line_start:line_end])
    return lines


def wrap_label(text: str, width: int = LABEL_WRAP_WIDTH, max_lines: int = LABEL_MAX_LINES) -> str:
    """把已清洗的标签按显示宽度确定性折行，用 ``<br/>`` 连接（不调用模型）。

    宽度规则：中文/全角字符按 1、英文/数字等半角字符按 0.5 计；
    在空格、标点之后与中英文字符边界处折行，单词（如 ImageNet、CIFAR-10）
    不从中间断开；总行数不超过 ``max_lines``，放不下的部分截断并加省略号。
    单行能放下的标签原样返回；已含 ``<br/>`` 的标签视为已折行，不再处理。
    """
    cleaned = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if not cleaned or max_lines < 1 or _display_width(cleaned) <= width:
        return cleaned
    if "<br" in cleaned.lower():
        return cleaned
    lines = [line.strip() for line in _wrap_lines(cleaned, float(width), max_lines)]
    lines = [line for line in lines if line]
    if len(lines) <= 1:
        return lines[0] if lines else cleaned
    return "<br/>".join(lines)


def _root_display_label(title: str) -> str:
    """根节点（圆形）的展示标签：论文标题可能很长，圆的直径随最宽行增长，
    先按显示宽度截到 ``ROOT_LABEL_MAX_WIDTH``（全角）以内并加省略号，
    再由 ``wrap_label`` 折行——省略与折行并用，圆才不会巨大且留大片空白。
    """
    text = sanitize_label(title, 120)  # 这里只负责清洗；截断按显示宽度在下方执行
    if _display_width(text) <= ROOT_LABEL_MAX_WIDTH:
        return text
    # 在词边界截断（_wrap_lines 的单词不拆断规则同样适用），留 1 全角给省略号
    line = (_wrap_lines(text, float(ROOT_LABEL_MAX_WIDTH - 1), 1) or [text])[0]
    if _display_width(line) > ROOT_LABEL_MAX_WIDTH - 1:
        # 单个超长词块（如超长英文单词）放不进预算：词内硬切兜底
        while line and _display_width(line) > ROOT_LABEL_MAX_WIDTH - 1:
            line = line[:-1]
    line = line.strip()
    return (line if line.endswith("…") else line + "…") or "（无内容）"


# ---------------------------------------------------------------------------
# 图模型
# ---------------------------------------------------------------------------
@dataclass
class DiagramNode:
    nid: str
    section: str
    label: str
    detail: str = ""
    evidence: str = ""
    page: int | None = None
    extra: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)


@dataclass
class DiagramEdge:
    src: str
    dst: str
    label: str = ""


@dataclass
class Diagram:
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    title: str = ""
    warnings: list[str] = field(default_factory=list)
    _by_id: dict[str, DiagramNode] = field(default_factory=dict, repr=False)

    def add_node(self, node: DiagramNode) -> str:
        self.nodes.append(node)
        self._by_id[node.nid] = node
        return node.nid

    def node(self, nid: str) -> DiagramNode | None:
        return self._by_id.get(nid)

    def edge(self, src: str, dst: str, label: str = "") -> None:
        if src in self._by_id and dst in self._by_id and src != dst:
            self.edges.append(DiagramEdge(src, dst, label))

    # ---------------------------------------------------------------- 渲染
    def to_mermaid(self, hidden: set[str] | None = None) -> str:
        hidden = hidden or set()
        lines: list[str] = [DIAGRAM_TYPE]
        if self.title:
            lines.insert(0, "---")
            lines.insert(1, f"title: {_quote(sanitize_label(self.title, 60))}")
            lines.insert(2, "---")

        grouped: dict[str, list[DiagramNode]] = {}
        for node in self.nodes:
            if node.nid in hidden:
                continue
            grouped.setdefault(node.section, []).append(node)

        for section in self.sections:
            members = grouped.get(section)
            if not members:
                continue
            lines.append(f"  subgraph {section}_grp[{_quote(SECTION_TITLES.get(section, section))}]")
            lines.append("    direction TB")
            for node in members:
                lines.append(f"    {node.nid}{_shape(node)}")
            lines.append("  end")

        visible = {n.nid for n in self.nodes if n.nid not in hidden}
        section_of = {n.nid: n.section for n in self.nodes}
        # 渲染层聚合（只改渲染输出，不改节点本身）：跨分区的边按
        # (源分区, 目标分区) 合并为一条"分区 → 分区"（subgraph id）的边。
        # dagre 会为每条跨分区边在两侧 cluster 内插入边界锚点（锚点成组占行、
        # 位置镜像对侧节点），扇形边越多，cluster 外框被撑得越大——实测
        # "结论"分区框 852×360、节点只占 323×32，空白 97.3%。
        # 合并为每分区一条进出边后，cluster 收紧到内容大小（"结论"分区框
        # 降到 376×82，空白 73.4%）。分区内链式边（论证/方法步骤）保持原样。
        cross_groups: dict[tuple[str, str], list[DiagramEdge]] = {}
        for edge in self.edges:
            if edge.src in visible and edge.dst in visible:
                src_sec, dst_sec = section_of[edge.src], section_of[edge.dst]
                if src_sec != dst_sec:
                    cross_groups.setdefault((src_sec, dst_sec), []).append(edge)

        # 每个分区只保留一条进入它的主关系，避免多条长跨区弧线互相穿插。
        # 节点级关系仍完整保留在 Diagram.edges 中，供后续分析使用。
        incoming: dict[str, tuple[str, str]] = {}
        for key in cross_groups:
            incoming.setdefault(key[1], key)

        emitted: set[tuple[str, str]] = set()
        for edge in self.edges:
            if edge.src not in visible or edge.dst not in visible:
                continue
            src_sec, dst_sec = section_of[edge.src], section_of[edge.dst]
            if src_sec != dst_sec:
                key = (src_sec, dst_sec)
                if incoming.get(dst_sec) != key:
                    continue
                if key in emitted:
                    continue
                emitted.add(key)
                # 标签取该组中出现次数最多者（同频取先出现者，保证确定性）：
                # 组内若干条节点级边各带关系词（结论/局限/支撑…），聚合后应代表
                # 多数语义，而不是碰巧排在前面的那条。
                counts: dict[str, int] = {}
                order: list[str] = []
                for e in cross_groups[key]:
                    if not e.label:
                        continue
                    if e.label not in counts:
                        counts[e.label] = 0
                        order.append(e.label)
                    counts[e.label] += 1
                label = max(order, key=lambda lb: counts[lb]) if counts else ""
                edge = DiagramEdge(f"{src_sec}_grp", f"{dst_sec}_grp", label)
            if edge.label:
                lines.append(f"  {edge.src} -->|{_quote(sanitize_label(edge.label, 12))}| {edge.dst}")
            else:
                lines.append(f"  {edge.src} --> {edge.dst}")

        # 分区配色不写入 Mermaid：历史版本曾输出 classDef *_cls，但它从未通过
        # class 语句挂载到任何节点，属于无效输出（节点颜色实际由前端 CSS 变量
        # 按 data-theme 统一驱动）。省掉这段可少 ~10 行死样式，导图源码更小。
        return "\n".join(lines)

    def node_index(self) -> list[dict]:
        """给前端面板使用的轻量索引。"""
        out: list[dict] = []
        for node in self.nodes:
            if node.section == "root":
                continue
            entry = {
                "id": node.nid,
                "section": node.section,
                "sectionTitle": SECTION_TITLES.get(node.section, node.section),
                "label": node.label,
                "detail": node.detail,
                "evidence": node.evidence,
                "page": node.page,
            }
            if node.extra:
                entry["extra"] = node.extra
            if node.verify:
                entry["verify"] = node.verify
            out.append(entry)
        return out


# ---------------------------------------------------------------------------
# 把溯源校验结果回填到图节点上
# ---------------------------------------------------------------------------
# 分区 key → analysis 中的数组字段名（research_problem 单独处理）
_SECTION_TO_ANALYSIS_KEY: dict[str, str] = {
    "arguments": "argument_flow",
    "method": "method",
    "contributions": "contributions",
    "evidence": "evidence_and_results",
    "conclusions": "conclusions",
    "limitations": "limitations",
    "glossary": "glossary",
}


def attach_verification(diagram: Diagram, analysis: dict) -> int:
    """把 analysis 里各条目的 verify/page/evidence 回填到对应图节点。

    为什么需要这一步：溯源校验发生在 ``build_diagram()`` **之后**，
    如果不回填，前端节点详情面板就拿不到页码与"是否已在原文定位到"，
    20 分的证据溯源在界面上就体现不出来（汇总统计对得上、点开却是空的）。

    匹配方式：同一分区的节点与同名字段数组按顺序一一对应——
    ``build_diagram`` 正是按数组顺序创建节点的，所以顺序可靠。
    返回成功回填的节点数。
    """
    attached = 0

    # 研究问题：一个分区只有一个节点
    problem = analysis.get("research_problem")
    if isinstance(problem, dict):
        for node in diagram.nodes:
            if node.section == "problem":
                _copy_fields(node, problem)
                attached += 1
                break

    for section, key in _SECTION_TO_ANALYSIS_KEY.items():
        items = analysis.get(key)
        if not isinstance(items, list):
            continue
        nodes = [n for n in diagram.nodes if n.section == section]
        for node, item in zip(nodes, items):
            if isinstance(item, dict):
                _copy_fields(node, item)
                attached += 1
    return attached


def _copy_fields(node: DiagramNode, item: dict) -> None:
    """把校验结果与被修正过的页码写回节点（节点自身内容保持不变）。"""
    verify = item.get("verify")
    if isinstance(verify, dict) and verify:
        node.verify = dict(verify)
    page = as_page_number(item.get("page"))
    if page is not None:
        node.page = page
    # 证据可能被 strict 模式清空，同步过去避免前端显示已失效的引用
    evidence = item.get("evidence")
    if isinstance(evidence, str) and evidence != node.evidence:
        node.evidence = evidence


def _shape(node: DiagramNode) -> str:
    # 换行在 sanitize 之后、拼进 Mermaid 之前应用：节点标签本身保持单行
    # （前端详情面板/检索用的就是 node.label），只有 Mermaid 源码带 <br/>。
    if node.section == "root":
        # 圆形按最宽行 × √2 膨胀，用更窄的折行宽度让圆明显缩小；
        # 根标签在 build_diagram 里已做过 ≤24 全角的省略策略。
        return f"(({_quote(wrap_label(node.label, ROOT_WRAP_WIDTH, ROOT_MAX_LINES))}))"
    if node.section == "problem":
        # 菱形外框 ≈ 文字框外接正方形，同样用窄行收紧整体尺寸。
        return f"{{{_quote(wrap_label(node.label, DIAMOND_WRAP_WIDTH, DIAMOND_MAX_LINES))}}}"
    label = _quote(wrap_label(node.label))
    if node.section in {"contributions", "conclusions"}:
        return f"({label})"
    if node.section == "evidence":
        return f"[/{label}/]"
    if node.section == "limitations":
        return f"[[{label}]]"
    return f"[{label}]"


# ---------------------------------------------------------------------------
# 从分析结果构建导图
# ---------------------------------------------------------------------------
def _as_list(data: dict, key: str) -> list[dict]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text_of(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_diagram(analysis: dict) -> Diagram:
    """把 LLM 的结构化分析转成确定性的 Mermaid 图模型。"""
    diagram = Diagram(warnings=[])
    title = _text_of(analysis, "title") or "论文结构导图"
    diagram.title = title

    counts: dict[str, int] = {}

    def new_id(section: str) -> str:
        counts[section] = counts.get(section, 0) + 1
        return f"{section}_{counts[section]}"

    def last_node(*sections: str) -> str | None:
        """按优先级选已有分区的末节点。"""
        for section in sections:
            for node in reversed(diagram.nodes):
                if node.section == section:
                    return node.nid
        return None

    # --- 根节点 + 一句话总结 ---
    summary = _text_of(analysis, "one_sentence_summary")
    root_id = diagram.add_node(
        DiagramNode(
            nid="root_1",
            section="root",
            # 论文标题可能很长：根节点展示标签先做 ≤24 全角的省略策略，
            # 再由 _shape 按 ROOT_WRAP_WIDTH 折行——省略+折行并用，
            # 圆直径才不会随标题长度无限膨胀（详情面板用 detail/summary）。
            label=_root_display_label(title),
            detail=summary,
        )
    )
    diagram.sections.append("root")
    if summary:
        root_id_2 = diagram.add_node(
            DiagramNode(nid="root_2", section="root", label="一句话总结", detail=summary)
        )
        diagram.edge(root_id, root_id_2)

    # --- 研究问题 ---
    problem = analysis.get("research_problem") if isinstance(analysis.get("research_problem"), dict) else {}
    statement = _text_of(problem, "statement")
    if statement:
        diagram.sections.append("problem")
        pid = diagram.add_node(
            DiagramNode(
                nid=new_id("problem"),
                section="problem",
                label=sanitize_label(statement, 30),
                detail=statement,
                evidence=_text_of(problem, "evidence"),
                page=as_page_number(problem),
                extra={"why": _text_of(problem, "why_it_matters")},
            )
        )
        diagram.edge(root_id_2 if summary else root_id, pid, "核心问题")

    # --- 论证路径（逻辑主干，串成链） ---
    arguments = _as_list(analysis, "argument_flow")
    if arguments:
        diagram.sections.append("arguments")
        previous = None
        for index, item in enumerate(arguments[:6], start=1):
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("arguments"),
                    section="arguments",
                    label=sanitize_label(_text_of(item, "label") or f"环节 {index}", 22),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                    extra={"seq": item.get("seq", index)},
                )
            )
            if index == 1:
                source = last_node("problem", "root") or root_id
                diagram.edge(source, nid, "展开论证")
            elif previous:
                diagram.edge(previous, nid)
            previous = nid

    # --- 方法路径 ---
    method = _as_list(analysis, "method")
    if method:
        diagram.sections.append("method")
        previous = None
        for index, item in enumerate(method[:6], start=1):
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("method"),
                    section="method",
                    label=sanitize_label(_text_of(item, "label") or f"步骤 {index}", 22),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                )
            )
            source = (last_node("problem", "root") if index == 1 else previous)
            if source:
                diagram.edge(source, nid, "采用方法" if index == 1 else "")
            previous = nid

    # --- 核心贡献 ---
    contributions = _as_list(analysis, "contributions")
    if contributions:
        diagram.sections.append("contributions")
        anchor = last_node("arguments", "problem", "root") or root_id
        for index, item in enumerate(contributions[:5], start=1):
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("contributions"),
                    section="contributions",
                    label=sanitize_label(_text_of(item, "label") or f"贡献 {index}", 20),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                    extra={"novelty": _text_of(item, "novelty")},
                )
            )
            diagram.edge(anchor, nid, "形成贡献" if index == 1 else "")

    # --- 实验与证据 ---
    evidence_items = _as_list(analysis, "evidence_and_results")
    evidence_node_ids: list[str] = []
    if evidence_items:
        diagram.sections.append("evidence")
        anchor = last_node("method", "arguments", "problem", "root") or root_id
        for index, item in enumerate(evidence_items[:5], start=1):
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("evidence"),
                    section="evidence",
                    label=sanitize_label(_text_of(item, "label") or f"结果 {index}", 20),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                )
            )
            evidence_node_ids.append(nid)
            diagram.edge(anchor, nid, "得到证据" if index == 1 else "")

    # --- 结论 ---
    conclusions = _as_list(analysis, "conclusions")
    if conclusions:
        diagram.sections.append("conclusions")
        for index, item in enumerate(conclusions[:4], start=1):
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("conclusions"),
                    section="conclusions",
                    label=sanitize_label(_text_of(item, "label") or f"结论 {index}", 20),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                )
            )
            supports = item.get("supports")
            if isinstance(supports, list):
                linked = set()
                for evidence_index in supports:
                    if (type(evidence_index) is int and 1 <= evidence_index <= len(evidence_node_ids)
                            and evidence_index not in linked):
                        diagram.edge(evidence_node_ids[evidence_index - 1], nid, "支持结论")
                        linked.add(evidence_index)
                if linked:
                    continue
                # 模型明确给空列表时，不假造证据到结论的支持关系。
                source = last_node("contributions", "method", "arguments", "problem", "root") or root_id
                diagram.edge(source, nid, "待核实结论")
            else:
                # 兼容旧分析 JSON：缺少 supports 时沿用原有的兜底连线。
                source = last_node("evidence", "contributions", "method", "arguments", "problem", "root") or root_id
                diagram.edge(source, nid, "归纳结论" if index == 1 else "")

    # --- 局限与风险 ---
    limitations = _as_list(analysis, "limitations")
    if limitations:
        diagram.sections.append("limitations")
        limitation_sources = {
            "数据": ("evidence", "method", "problem"),
            "实验": ("evidence", "method", "problem"),
            "方法": ("method", "arguments", "problem"),
            "假设": ("arguments", "method", "problem"),
            "应用边界": ("conclusions", "contributions", "problem"),
        }
        for index, item in enumerate(limitations[:5], start=1):
            kind = _text_of(item, "kind") or "其他"
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("limitations"),
                    section="limitations",
                    label=sanitize_label(_text_of(item, "label") or f"局限 {index}", 20),
                    detail=_text_of(item, "detail"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                    extra={
                        "kind": kind,
                        "severity": _text_of(item, "severity") or "中",
                    },
                )
            )
            # 局限约束的是数据、方法或结论的适用范围，不是结论的下一步。
            source = last_node(*limitation_sources.get(kind, ("problem", "root")), "root") or root_id
            diagram.edge(source, nid, "约束条件")

    # --- 批判性分析：各维度回连到受评估的论证环节 ---
    critique = analysis.get("critique") if isinstance(analysis.get("critique"), dict) else {}
    critique_parts = {
        "数据": _text_of(critique, "data"),
        "实验": _text_of(critique, "experiment"),
        "方法": _text_of(critique, "method"),
        "假设": _text_of(critique, "assumption"),
        "应用边界": _text_of(critique, "application_boundary"),
        "总体": _text_of(critique, "overall"),
    }
    critique_parts = {k: v for k, v in critique_parts.items() if v}
    if critique_parts:
        diagram.sections.append("critique")
        critique_sources = {
            "数据": ("evidence", "method", "problem"),
            "实验": ("evidence", "method", "problem"),
            "方法": ("method", "arguments", "problem"),
            "假设": ("arguments", "method", "problem"),
            "应用边界": ("conclusions", "contributions", "problem"),
            "总体": ("conclusions", "evidence", "problem"),
        }
        for key, value in critique_parts.items():
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("critique"),
                    section="critique",
                    label=sanitize_label(f"{key}：{value}", 24),
                    detail=value,
                    extra={"dimension": key},
                )
            )
            source = last_node(*critique_sources[key], "root") or root_id
            diagram.edge(source, nid, "评估依据")

    # --- 通俗解读（术语） ---
    glossary = _as_list(analysis, "glossary")
    if glossary:
        diagram.sections.append("glossary")
        anchor = last_node("problem", "root") or root_id
        for index, item in enumerate(glossary[:6], start=1):
            term = _text_of(item, "term") or f"术语 {index}"
            nid = diagram.add_node(
                DiagramNode(
                    nid=new_id("glossary"),
                    section="glossary",
                    label=sanitize_label(term, 16),
                    detail=_text_of(item, "plain"),
                    evidence=_text_of(item, "evidence"),
                    page=as_page_number(item),
                    extra={
                        "analogy": _text_of(item, "analogy"),
                        "why": _text_of(item, "why_it_matters"),
                    },
                )
            )
            diagram.edge(anchor, nid, "术语解读" if index == 1 else "")

    # 模型给出明确的论证关系时，用该关系替换目标节点的默认跨分区锚点。
    # 留在同一分区内的步骤线不受影响；无效编号和逆向关系直接丢弃。
    section_rank = {key: index for index, key in enumerate(
        ("root", "problem", "arguments", "method", "contributions",
         "evidence", "conclusions", "limitations", "critique", "glossary")
    )}
    explicit: dict[str, list[tuple[str, str]]] = {}
    for link in (analysis.get("semantic_links") or [])[:30]:
        if not isinstance(link, dict):
            continue
        endpoints = []
        for field_name in ("from", "to"):
            match = re.fullmatch(r"([a-z_]+):([1-9][0-9]*)", str(link.get(field_name) or ""))
            endpoints.append((match.group(1), int(match.group(2))) if match else None)
        if not all(endpoints):
            continue
        (source_section, source_index), (target_section, target_index) = endpoints
        source_id, target_id = f"{source_section}_{source_index}", f"{target_section}_{target_index}"
        if (source_id not in diagram._by_id or target_id not in diagram._by_id
                or section_rank.get(source_section, 99) >= section_rank.get(target_section, -1)):
            continue
        relation = sanitize_label(str(link.get("relation") or "关联"), 8)
        explicit.setdefault(target_id, []).append((source_id, relation))
    if explicit:
        diagram.edges = [edge for edge in diagram.edges
                         if edge.dst not in explicit
                         or diagram.node(edge.src).section == diagram.node(edge.dst).section]
        for target_id, sources in explicit.items():
            for source_id, relation in dict.fromkeys(sources):
                diagram.edge(source_id, target_id, relation)

    if not diagram.nodes:
        diagram.warnings.append("分析结果为空，未能生成任何节点。")
    return diagram


# ---------------------------------------------------------------------------
# 语法校验
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "Mermaid 语法预校验通过" + (f"（{len(self.warnings)} 条提示）" if self.warnings else "")
        return "Mermaid 语法预校验失败：" + "；".join(self.errors)


def validate_mermaid(code: str) -> ValidationResult:
    """本地静态校验，不依赖 mermaid-cli / Node。

    检查：首行类型合法、子图闭合、括号与引号配平、节点 id 合法、
    边引用的节点已定义、标签非空。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not code or not code.strip():
        return ValidationResult(False, ["Mermaid 代码为空。"])

    raw_lines = code.splitlines()
    # 跳过 front-matter
    start = 0
    if raw_lines and raw_lines[0].strip() == "---":
        for idx in range(1, len(raw_lines)):
            if raw_lines[idx].strip() == "---":
                start = idx + 1
                break
    body = [line for line in raw_lines[start:] if line.strip() and not line.strip().startswith("%%")]

    if not body:
        return ValidationResult(False, ["Mermaid 代码体为空。"])
    header = body[0].strip()
    if not re.match(r"^(flowchart|graph)\s+(TD|TB|BT|LR|RL)$", header):
        errors.append(f"首行必须是 flowchart/graph + 方向，当前为：{header!r}")

    defined: set[str] = set()
    edge_ids: Counter[str] = Counter()
    subgraph_open = False
    node_line_re = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*(\(\(|\[\[|\[/|\[|\(|\{)(.*)$")

    for lineno, line in enumerate(body[1:], start=start + 2):
        stripped = line.strip()
        if stripped.startswith("subgraph"):
            if subgraph_open:
                errors.append(f"第 {lineno} 行：subgraph 未闭合就嵌套了新的 subgraph。")
            subgraph_open = True
            sg_match = re.match(r"^subgraph\s+([A-Za-z][A-Za-z0-9_]*)\s*\[.*\]$", stripped)
            if not sg_match:
                warnings.append(f"第 {lineno} 行：subgraph 语法非常规，可能渲染异常：{truncate(stripped, 60)}")
            else:
                # 子图 id 也是合法的边端点（渲染层扇形边聚合会连到分区），
                # 记入已定义集合，避免被误报成"默认矩形渲染"的孤儿节点。
                defined.add(sg_match.group(1))
            continue
        if stripped == "end":
            if not subgraph_open:
                warnings.append(f"第 {lineno} 行：多余的 end。")
            subgraph_open = False
            continue
        if stripped.startswith(("direction ", "classDef ", "class ", "click ", "style ", "linkStyle ")):
            continue

        # 括号/引号配平（忽略引号内内容）
        unquoted = re.sub(r'"[^"]*"', '""', stripped)
        for opener, closer in _UNBALANCED_PAIRS.items():
            if unquoted.count(opener) != unquoted.count(closer):
                errors.append(
                    f"第 {lineno} 行：{opener} 与 {closer} 数量不匹配（{unquoted.count(opener)} vs {unquoted.count(closer)}）。"
                )
        if unquoted.count('"') % 2 != 0:
            errors.append(f"第 {lineno} 行：引号未闭合。")

        # 边：a --> b  或  a -->|label| b
        if "-->" in stripped or "---" in stripped:
            # 先摘掉边标签，避免标签里的字符干扰端点解析
            edge_text = re.sub(r"\|[^|]*\|", " ", stripped)
            parts = re.split(r"\s*-{2,3}>?\s*", edge_text)
            if len(parts) >= 2:
                for part in parts:
                    token = part.strip()
                    match = node_line_re.match(token)
                    if match:
                        # 行内定义，例如 `a_1["标签"] --> b_1`
                        defined.add(match.group(1))
                        edge_ids[match.group(1)] += 1
                        continue
                    name = token.split()[0] if token.split() else ""
                    if name and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
                        edge_ids[name] += 1
                    elif name:
                        errors.append(f"第 {lineno} 行：边端点 {name!r} 不是合法的节点 id。")
            continue

        # 节点定义
        match = node_line_re.match(stripped)
        if match:
            nid = match.group(1)
            defined.add(nid)
            label_part = match.group(3)
            if not re.search(r"[^\s\[\]\(\)\{\}/]", re.sub(r'"[^"]*"', "", label_part)) and '"' not in label_part:
                warnings.append(f"第 {lineno} 行：节点 {nid} 的标签可能为空。")
            continue

        # classDef 之类的赋值行已在上方跳过；其余视为可疑
        if ":" in stripped and stripped.split(":")[0].strip() in SECTION_TITLES:
            continue
        warnings.append(f"第 {lineno} 行：未识别的语句，已忽略：{truncate(stripped, 50)}")

    if subgraph_open:
        errors.append("存在未闭合的 subgraph。")

    # Mermaid 允许只写 `a --> b` 而让 b 隐式成节点（默认矩形），
    # 因此只把"仅出现一次、且从未带形状定义"的 id 视为可疑，降级为提示而非错误，
    # 避免把合法的简写误判成语法错误（误判会让上层白白触发一轮修复重试）。
    orphan = sorted(nid for nid, count in edge_ids.items() if nid not in defined and count < 2)
    if orphan:
        warnings.append(
            f"以下节点只在边中出现一次、没有显式标签，将按默认矩形渲染：{', '.join(orphan)}"
        )

    if len(defined) > 60:
        warnings.append(f"节点数量 {len(defined)} 较多，导图可能偏拥挤。")

    return ValidationResult(not errors, errors, warnings)


# ---------------------------------------------------------------------------
# 自动修复
# ---------------------------------------------------------------------------
def repair_mermaid(code: str) -> tuple[str, list[str]]:
    """规则化修复：不调用大模型也能救回大多数语法问题。

    返回 (修复后的代码, 修复动作说明)。
    """
    actions: list[str] = []
    lines = [line.rstrip() for line in (code or "").splitlines()]

    # 1) 去掉代码围栏与解释性前后缀
    cleaned: list[str] = []
    for line in lines:
        if line.strip().startswith("```"):
            actions.append("移除代码围栏")
            continue
        cleaned.append(line)
    lines = cleaned

    # 2) 定位首行类型
    header_index = next(
        (i for i, line in enumerate(lines) if re.match(r"^(flowchart|graph)\s+(TD|TB|BT|LR|RL)", line.strip())),
        -1,
    )
    if header_index < 0:
        actions.append("补写缺失的 `flowchart TD` 首行")
        lines.insert(0, DIAGRAM_TYPE)
    elif header_index > 0:
        actions.append("删除首行之前的解释文字")
        lines = lines[header_index:]

    # 3) 逐行规范化
    fixed: list[str] = [lines[0].strip() if lines else DIAGRAM_TYPE]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("```"):
            continue
        if "-->" in stripped:
            stripped = re.sub(r"-{2,}>", "-->", stripped)
            stripped = re.sub(r"\s*-->\s*", " --> ", stripped)
        fixed.append("  " + stripped if not stripped.startswith(("subgraph", "end")) else stripped)

    # 4) 补全未闭合的 subgraph
    opens = sum(1 for line in fixed if line.strip().startswith("subgraph"))
    closes = sum(1 for line in fixed if line.strip() == "end")
    while closes < opens:
        fixed.append("  end")
        closes += 1
        actions.append("补全未闭合的 subgraph")

    repaired = "\n".join(fixed)
    if repaired != (code or "").strip():
        actions.append("统一边语法与缩进")
    return repaired, sorted(set(actions))
