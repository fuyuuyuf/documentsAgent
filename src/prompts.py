"""Prompt 模板：让模型只做"读论文 → 出结构化 JSON"，不产出任何代码。"""

from __future__ import annotations

import json

from .parse import PaperText

# ---------------------------------------------------------------------------
# 输出结构说明（同时充当"契约文档"和校验依据）
# ---------------------------------------------------------------------------
ANALYSIS_SCHEMA = """{
  "title": "论文标题（若原文有副标题可保留）",
  "one_sentence_summary": "一句话概括这篇论文做了什么（不超过 60 字）",
  "research_problem": {
    "statement": "论文要解决的核心问题（转述，不要照抄小标题）",
    "why_it_matters": "为什么这个问题重要 / 现有方法的不足",
    "evidence": "支撑上述判断的原文短摘录（10-40 字，必须逐字来自论文原文）",
    "page": 1
  },
  "contributions": [
    {"label": "贡献的简短标签（≤14 字）", "detail": "贡献具体是什么（≤80 字）",
     "novelty": "相比已有工作的新意在哪", "evidence": "原文短摘录", "page": 1}
  ],
  "method": [
    {"label": "方法步骤标签（≤14 字）", "detail": "这一步做了什么（≤80 字）",
     "evidence": "原文短摘录", "page": 1}
  ],
  "argument_flow": [
    {"seq": 1, "label": "论证环节标签（≤14 字）", "detail": "该环节在论证中起什么作用（≤80 字）",
     "evidence": "原文短摘录", "page": 1}
  ],
  "evidence_and_results": [
    {"label": "结果标签（≤14 字）", "detail": "具体的实验设置 / 数据 / 指标与结论（≤80 字）",
     "evidence": "原文短摘录，尽量包含具体数值", "page": 1}
  ],
  "conclusions": [
    {"label": "结论标签（≤14 字）", "detail": "作者基于哪些结果得出什么结论，以及适用范围（≤80 字）",
     "supports": [1], "evidence": "原文短摘录", "page": 1}
  ],
  "limitations": [
    {"label": "局限标签（≤14 字）", "detail": "哪项数据、实验、方法、假设或结论的适用范围受限，如何受限（≤80 字）",
     "kind": "数据 | 实验 | 方法 | 假设 | 应用边界 | 其他", "severity": "高 | 中 | 低",
     "evidence": "原文短摘录", "page": 1}
  ],
  "critique": {
    "data": "数据层面的问题（规模、来源、偏置、标注质量）",
    "experiment": "实验层面的问题（基线选择、指标、消融、可复现性）",
    "method": "方法层面的问题（假设是否过强、复杂度、泛化性）",
    "assumption": "所依赖的关键假设及其风险",
    "application_boundary": "能落地到什么场景、不能落地到什么场景",
    "overall": "整体批判性评价（2-3 句，指出最值得警惕的一点）"
  },
  "glossary": [
    {"term": "专业术语或概念", "plain": "用大白话解释（≤60 字），假设读者是大一新生",
     "evidence": "原文短摘录", "page": 1}
  ],
  "open_questions": ["论文没有回答、但值得继续追问的问题"],
  "reading_path": ["建议读者按什么顺序读这篇论文，3-5 条"],
  "semantic_links": [
    {"from": "method:1", "to": "evidence:1", "relation": "验证"}
  ]
}"""

SYSTEM_PROMPT = """你是一位资深的学术论文分析专家，擅长把结构复杂、术语密集的论文拆解成清晰的逻辑结构，并做批判性评估。

你的输出会被程序直接解析成 JSON，并渲染成交互式思维导图，因此必须严格遵守：

1. 只输出一个 JSON 对象，不要输出任何解释文字、不要使用 ``` 代码围栏。
2. 严格遵循给定的字段结构，不要增删顶层字段，不要改名。
3. `evidence` 字段必须是**逐字摘自论文原文**的短句（10-40 字），可以跨句拼接但不可改写、不可翻译、不可编造。
   摘录要尽量包含具体信息（方法名、数据集名、数值、结论句）。
4. `page` 字段填该摘录所在的页码，必须与正文中 `<<<PAGE n>>>` 锚点一致；无法确定时填 null，不要猜。
5. 不存在的分区用空数组表示，不要造"论文未明确说明"占位节点；`open_questions`、`reading_path` 与 `semantic_links` 也允许为空。
6. 标签（label）要**自己提炼**，不要照搬论文的章节小标题；同一个标签不要重复。
7. 所有文本使用与论文正文一致的语言（中文论文用中文；英文论文的说明用中文，但 evidence 保留英文原文）。
8. `critique` 各字段必须指出被评估的具体证据、方法或推断，以及这些材料为何不足；禁止写"需要更多实验验证"这类空话。
9. `glossary` 面向大一新生，用比喻或生活化例子解释，禁止用术语解释术语。
10. 论文的参考文献（References / 参考文献）部分不属于正文，不要为其生成节点。
11. 按"研究问题→方法/论证→可观察结果→有证据支持的结论"梳理因果与支持关系；贡献是作者提出的做法或发现，不要把贡献直接当成已经验证的结论。`conclusions[].supports` 填支撑该结论的 `evidence_and_results` 条目序号（从 1 开始，可多条）；没有可见证据就填 []，并在 detail 说明证据不足。若结果只能支持较窄的说法，结论必须保留该边界。
12. `limitations` 按受限制的对象填写 `kind`，数据或实验问题归到对应证据，方法或假设问题归到论证前提，应用边界归到结论。区分作者明说的局限与自己推断的批评：前者放 limitations 并提供原文摘录，后者放 critique；原文不足以判断时直说"现有摘录无法判断"，不要编造缺失的实验或缺陷。
13. `semantic_links` 按论文实际逻辑连接条目，格式为 `分区:从1开始的序号`；分区可用 problem、arguments、method、contributions、evidence、conclusions、limitations、critique。relation 写"提出"、"检验"、"支持"、"限制"等具体关系，禁止用重复的"下一步"。只连接真实有关系的节点，允许空数组。"""

CHUNK_SYSTEM_PROMPT = """你在逐批阅读一篇长论文。只输出 JSON 对象：{"findings":[{"type":"problem|argument|method|contribution|result|conclusion|limitation|term","statement":"本批可证实的具体发现","evidence":"本批原文逐字短摘录","page":1}]}。
每批保留关键发现 8-18 条，覆盖该批出现的论证、方法、结果与限制；没有出现的类型不要凑数。evidence 必须从本批原文逐字复制，page 对应 <<<PAGE n>>>。不得依据其他批次或常识补写。"""


def build_chunk_prompt(paper: PaperText, body: str, index: int, total: int) -> str:
    """把连续正文交给单批提取，保留原始页码。"""
    return (f"论文：{paper.title or '(未识别)'}。这是第 {index}/{total} 批连续正文。"
            "请提取本批证据与论证要点，严格按系统要求输出 JSON。\n"
            f"{body}")


def validate_chunk_findings(data: object) -> None:
    """避免空批次悄然进入综合阶段。"""
    from .llm import LLMError

    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise LLMError("分批分析必须返回 findings 数组。")
    if not data["findings"]:
        raise LLMError("分批分析未提取到任何要点。")
    for item in data["findings"]:
        if not isinstance(item, dict) or not str(item.get("statement") or "").strip():
            raise LLMError("每条 findings 必须有具体 statement。")


def build_synthesis_prompt(paper: PaperText, findings: list[dict]) -> str:
    """综合多批证据；引用只能来自批次保留的原文摘录。"""
    return f"""以下是同一篇论文逐批阅读后的证据笔记。所有正文均已阅读；笔记按原文顺序排列。
请按给定结构综合全文，去重、保留关键分歧，依据论文的实际论证安排导图关系。
`evidence` 只能逐字复用笔记中已有摘录；没有依据的字段写明无法判断。
`semantic_links` 用 from/to 引用分区和条目序号，分区取 problem、arguments、method、contributions、evidence、conclusions、limitations、critique；
例如 method:1 → evidence:2。只连真实存在且有直接支持/约束/检验关系的节点，relation 用两到六字描述实际关系；无需为每个节点凑连线。

标题：{paper.title or '(未识别)'}
页数：{paper.page_count}
章节大纲：
{paper.outline_text(limit=40) or '(未识别)'}

输出结构：
{ANALYSIS_SCHEMA}

逐批证据笔记：
{json.dumps(findings, ensure_ascii=False, separators=(',', ':'))}
"""


def _truncation_note(paper: PaperText, index_text: str) -> str:
    if len(index_text) < paper.char_count:
        return (
            "\n注意：由于长度限制，正文经过节选（摘要、结论相关章节优先完整保留，其余页均衡节选），"
            f"未提供完整全文。你看到约 {len(index_text)} 字符（全文 {paper.char_count} 字符）。"
            "请只基于可见内容分析，不要凭空推断省略部分的具体细节。\n"
        )
    return ""


def build_analysis_prompt(paper: PaperText, index_text: str) -> str:
    """构造第一阶段分析 Prompt：结构、贡献、论证、结论、局限、批判、术语。"""
    outline = paper.outline_text(limit=40) or "(未识别到明确章节标题)"
    column_note = (
        f"该论文有 {len(paper.two_column_pages)} 页为多栏排版，提取时已按块重排，阅读顺序如有异常请忽略。\n"
        if paper.two_column_pages
        else ""
    )
    return f"""请分析下面这篇论文，并按要求输出 JSON。

===== 论文元信息 =====
标题（自动识别）：{paper.title or "(未识别)"}
总页数：{paper.page_count}
正文字符数：{paper.char_count}
{column_note}
===== 自动识别的章节大纲（仅供参考，你可以重组逻辑）=====
{outline}

===== 结构化输出要求 =====
请输出恰好符合以下结构的 JSON：
{ANALYSIS_SCHEMA}

===== 论文正文（`<<<PAGE n>>>` 表示第 n 页开始）=====
{index_text}
{_truncation_note(paper, index_text)}
===== 开始输出 JSON ======"""


# ---------------------------------------------------------------------------
# 校验 / 修复
# ---------------------------------------------------------------------------
def validate_analysis(data: object) -> None:
    """结构校验；不通过抛出 ValueError，由 LLMClient 触发重问。"""
    from .llm import LLMError

    if not isinstance(data, dict):
        raise LLMError("顶层必须是 JSON 对象。")

    required_list = [
        "contributions",
        "method",
        "argument_flow",
        "evidence_and_results",
        "conclusions",
        "limitations",
        "glossary",
        "open_questions",
        "reading_path",
    ]
    missing = [key for key in required_list if key not in data]
    if missing:
        raise LLMError(f"缺少必需字段：{', '.join(missing)}")
    if not isinstance(data.get("research_problem"), dict):
        raise LLMError("research_problem 必须是对象。")
    if not isinstance(data.get("critique"), dict):
        raise LLMError("critique 必须是对象。")

    for key in required_list:
        value = data.get(key)
        if not isinstance(value, list):
            raise LLMError(f"{key} 必须是数组。")
        # 缺失的论文环节保持为空，导图按内容动态选择分区。

    if not data.get("title"):
        raise LLMError("title 不能为空。")
    if not data.get("one_sentence_summary"):
        raise LLMError("one_sentence_summary 不能为空。")
    if not (data["research_problem"].get("statement") or "").strip():
        raise LLMError("research_problem.statement 不能为空。")

    for key in ("contributions", "method", "argument_flow", "conclusions", "limitations"):
        for item in data[key]:
            if not isinstance(item, dict):
                raise LLMError(f"{key} 的元素必须是对象。")
            if not (item.get("label") or "").strip():
                raise LLMError(f"{key} 的元素缺少 label。")

    links = data.get("semantic_links", [])
    if not isinstance(links, list):
        raise LLMError("semantic_links 必须是数组。")
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("from"), str) or not isinstance(link.get("to"), str):
            raise LLMError("semantic_links 元素须包含 from 和 to。")


REPAIR_HINT = (
    "请检查：必需顶层字段齐全且数组类型正确；已有条目都有非空的 label；"
    "无相关内容时用空数组。只输出 JSON 对象本身。"
)


# ---------------------------------------------------------------------------
# 受众导读（提高部分：读者友好解读，第二轮调用）
# ---------------------------------------------------------------------------
# 第二轮只发"第一轮分析 JSON + 少量摘录"，不再重发正文全文；
# story / glossary 的字段名是下游 _merge_audience 的输入约定，不得改名。
AUDIENCE_SYSTEM_PROMPT = """你是一位擅长给低年级本科生做论文导读的助教。

你的任务：基于一篇论文的结构化分析 JSON（研究问题、方法、贡献、证据、结论、局限都在里面）
和少量原文摘录，产出一版"充实、不敷衍"的通俗解读，让没有任何相关背景的大一新生
既能看懂每个概念，也能明白这篇论文整体在做什么。

你的输出会被程序直接解析成 JSON，因此必须严格遵守：

1. 只输出一个 JSON 对象，不要输出任何解释文字，不要使用 ``` 代码围栏。
2. "需要解释的概念"清单里的每一个概念都必须逐个覆盖、一条不落，
   禁止只挑一两个概念敷衍；术语名必须照抄清单原文，不要改写或翻译。
3. 每个概念的解释必须同时包含三层，缺一不可：
   - plain（是什么）：大白话定义，60-120 字。先说清楚它是什么、解决什么麻烦，
     再自然带出它为什么存在；禁止用另一个术语解释术语，禁止只给一句话定义。
   - analogy（类比）：一个贴近生活的类比或例子，30-60 字，要具体到场景。
   - why_it_matters（作用）：它在这篇论文里起什么作用、为什么重要，30-60 字，
     必须结合本文的具体内容，不要写"很重要"这类空话。
4. story 字段：给全文一段"这篇论文在做一件什么事"的整体叙述，150-250 字，
   用讲故事的白话把"要解决什么问题 → 用了什么核心方法 → 得到什么关键结论 →
   有什么主要局限"串起来；核心方法、关键结论、主要局限三者缺一不可，
   必须取材于分析 JSON 里的 method / evidence_and_results / conclusions / limitations。
5. 不要编造论文与分析 JSON 之外的内容；所有说法都要能在分析 JSON 或摘录里找到出处。
6. 所有解释用中文（专有名词可保留原文）；每条解释控制在对应字数范围内。"""


def _audience_excerpt(paper: PaperText) -> str:
    """第二轮的少量必要摘录：摘要 + 章节大纲（不够时退化为正文开头）。

    刻意不放正文全文：概念解释与整体叙述所需的事实已经在第一轮分析结果里，
    重发正文只会成倍增加 token 消耗。
    """
    abstract = (paper.abstract or "").strip()
    if not abstract:
        abstract = paper.full_text[:800].strip()
    abstract = abstract[:1500]
    outline = (paper.outline_text(limit=30) or "(未识别到明确章节标题)")[:800]
    return f"[摘要/正文开头]\n{abstract}\n\n[章节大纲]\n{outline}"


def _audience_brief(analysis: dict) -> str:
    """把第一轮分析 JSON 压成第二轮真正需要的"内容简报"。

    写通俗解读需要的是语义骨架（问题 / 方法 / 结果 / 结论 / 局限）；
    evidence 原文摘录、页码、溯源标记、阅读路径、已有解释等字段对解读帮助有限，
    全文直发可能比正文节选还大，这里只保留必要字段以降低 token 消耗。
    """
    def items(key: str, fields: tuple[str, ...]) -> list[dict]:
        out: list[dict] = []
        for item in analysis.get(key) or []:
            if isinstance(item, dict):
                entry = {f: item[f] for f in fields if str(item.get(f) or "").strip()}
                if entry:
                    out.append(entry)
        return out

    critique = analysis.get("critique") if isinstance(analysis.get("critique"), dict) else {}
    brief = {
        "title": str(analysis.get("title") or "").strip(),
        "one_sentence_summary": str(analysis.get("one_sentence_summary") or "").strip(),
        "research_problem": {
            k: str(v).strip()
            for k, v in (analysis.get("research_problem") or {}).items()
            if k in ("statement", "why_it_matters") and str(v or "").strip()
        },
        "contributions": items("contributions", ("label", "detail")),
        "method": items("method", ("label", "detail")),
        "evidence_and_results": items("evidence_and_results", ("label", "detail")),
        "conclusions": items("conclusions", ("label", "detail")),
        "limitations": items("limitations", ("label", "detail")),
        "critique_overall": str(critique.get("overall") or "").strip(),
    }
    brief = {k: v for k, v in brief.items() if v}
    return json.dumps(brief, ensure_ascii=False, indent=1)


def build_audience_prompt(terms: list[str], analysis: dict, paper: PaperText) -> str:
    """构造第二轮通俗解读 Prompt：分析简报 + 少量摘录，不再重发正文全文。"""
    term_lines = "\n".join(f"- {t}" for t in terms) or "- (无)"
    brief_text = _audience_brief(analysis)
    return f"""下面是一篇论文的分析简报（由第一轮结构化分析整理，研究问题、方法、结果、结论、局限都在里面）与少量原文摘录。

论文标题：{paper.title or "(未识别)"}

需要解释的概念（共 {len(terms)} 个，必须逐个覆盖、一条不落）：
{term_lines}

===== 第一轮分析简报（写 story 与"作用"时的素材来源） =====
{brief_text}

===== 原文摘录（摘要与大纲，可供引用，无需重新分析全文） =====
{_audience_excerpt(paper)}

请输出恰好符合以下结构的 JSON（字段名不得改动）：
{{
  "story": "这篇论文在做一件什么事：150-250 字的整体叙述，必须覆盖核心方法、关键结论、主要局限",
  "glossary": [
    {{"term": "概念名（照抄上面清单）", "plain": "是什么：大白话定义（60-120 字）",
      "analogy": "贴近生活的类比（30-60 字）",
      "why_it_matters": "它在本文中的作用/为什么重要（30-60 字）", "page": null}}
  ]
}}

===== 开始输出 JSON ====="""


def validate_audience(data: object, *, required_terms: list[str] | None = None) -> None:
    """第二轮输出校验。

    ``required_terms`` 是分析 glossary 中出现的全部概念（由 pipeline 传入）：
    数量下限的硬约束——少覆盖任何一个都判不合格，触发一次带提示的重问，
    防止模型偷懒。直接调用（不传该参数）时保持原有的宽松校验。
    """
    from .llm import LLMError

    if not isinstance(data, dict):
        raise LLMError("顶层必须是 JSON 对象。")
    glossary = data.get("glossary")
    if not isinstance(glossary, list) or not glossary:
        raise LLMError("glossary 必须是非空数组。")

    seen: set[str] = set()
    for item in glossary:
        if not isinstance(item, dict) or not (item.get("term") or "").strip():
            raise LLMError("glossary 元素必须含非空 term。")
        term = str(item["term"]).strip()
        if term in seen:
            raise LLMError(f"glossary 中出现重复术语：{term}")
        seen.add(term)
        plain = str(item.get("plain") or "").strip()
        if not plain:
            raise LLMError(f"glossary 元素必须含非空 plain：{term}")
        if len(plain) < 20:
            raise LLMError(
                f"概念「{term}」的通俗解释只有 {len(plain)} 字（不足 20 字），"
                "太敷衍了。请给出 60-120 字的大白话定义。"
            )

    if required_terms:
        missing = [term for term in required_terms if term not in seen]
        if missing:
            raise LLMError(
                f"还有 {len(missing)} 个必需概念未被覆盖：{'、'.join(missing[:6])}"
                + ("等" if len(missing) > 6 else "")
                + "。必须逐个解释清单里的全部概念，一条不落。"
            )
