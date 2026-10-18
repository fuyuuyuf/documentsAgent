"""文本工具：规范化、截断、页码归一、引用校验（用于反幻觉证据溯源）。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_WS_RE = re.compile(r"\s+")
#: 与 collapse_for_search 的保留字符集合一致，用于构建"压缩文本下标 → 原文下标"映射
_KEEP_RE = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")

# PDF 抽取常见噪声
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u00ad": "",  # soft hyphen
}


def normalize(text: str) -> str:
    """面向匹配的强规范化：去 HTML、统一引号连字符、折叠空白。"""
    if not text:
        return ""
    out = []
    for ch in text:
        out.append(_LIGATURES.get(ch, ch))
    text = "".join(out)
    text = re.sub(r"<[^>]{1,80}>", " ", text)  # 去掉模型可能塞进来的 <br/> 等标签
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^[#>\-\*\s]+", "", text)
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def collapse_for_search(text: str) -> str:
    """压缩成"只保留字母数字与 CJK"的形态，抵抗空白/连字符差异。"""
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", (text or "").lower())


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def as_page_number(value: Any) -> int | None:
    """把模型给出的页码归一成 int；布尔 / 非数字字符串 / None 一律返回 None。

    供 diagram / render 等消费分析 JSON 的模块共用：页码只接受
    ``int`` 或纯数字字符串，其余形态（含 ``bool``，它是 ``int`` 的子类）
    都视为"模型没有给出有效页码"。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def middle_ellipsis(text: str, limit: int) -> str:
    """把长句压到 limit 字符以内，中间用省略号，便于做导图节点标签。"""
    text = normalize(text)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    head = int((limit - 1) * 0.6)
    tail = limit - 1 - head
    return f"{text[:head]}…{text[len(text) - tail:]}"


# ---------------------------------------------------------------------------
# 引用校验
# ---------------------------------------------------------------------------
class CitationVerifier:
    """在论文原文里定位模型给出的引用，返回页码与置信度。

    策略（逐级放宽，避免把正常改写误判为幻觉）：
      1. 规范化后精确子串匹配
      2. 压缩空白/连字符后再匹配
      3. 取引文最长片段（>=24 字符）再匹配
    匹配得分 = 命中长度 / 引文长度。
    """

    def __init__(self, paper_text: str, page_spans: list[tuple[int, int]]):
        self.raw = paper_text or ""
        self.page_spans = list(page_spans or [])
        self._norm, self._norm_offsets = self._normalize_with_offsets(self.raw)
        self._squashed = collapse_for_search(self.raw)
        # squashed 形态无法回溯页码，因此记录每页 squashed 的起止
        self._squashed_page_spans = self._build_squashed_spans()
        # squashed 下标 → 原文下标（供 window() 截取上下文用，长度与 _squashed 严格一致）
        self._squashed_offsets = self._build_squashed_offsets(self.raw)

    # -------------------------------------------------------------- 内部
    @staticmethod
    def _normalize_with_offsets(raw: str) -> tuple[str, list[int]]:
        """与 normalize 同构的规范化，同时保留每个字符在原文中的下标。"""
        chars: list[str] = []
        offsets: list[int] = []
        prev_space = False
        for idx, ch in enumerate(raw):
            mapped = _LIGATURES.get(ch, ch)
            if mapped == "":
                continue
            if mapped.isspace():
                if prev_space or not chars:
                    continue
                chars.append(" ")
                offsets.append(idx)
                prev_space = True
                continue
            for sub in mapped:
                chars.append(sub)
                offsets.append(idx)
            prev_space = False
        return "".join(chars), offsets

    def _build_squashed_spans(self) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for start, end in self.page_spans:
            prefix = collapse_for_search(self.raw[:start])
            whole = collapse_for_search(self.raw[:end])
            spans.append((len(prefix), len(whole)))
        return spans

    def _page_at(self, char_pos: int) -> int | None:
        for page_index, (start, end) in enumerate(self.page_spans, start=1):
            if start <= char_pos < end:
                return page_index
        return None

    def _page_at_squashed(self, pos: int) -> int | None:
        for page_index, (start, end) in enumerate(self._squashed_page_spans, start=1):
            if start <= pos < end:
                return page_index
        return None

    # -------------------------------------------------------------- 下标映射（window 用）
    @staticmethod
    def _build_squashed_offsets(raw: str) -> list[int]:
        """为每个保留下来的压缩字符记录其在原文里的下标（与 collapse_for_search 同构）。"""
        offsets: list[int] = []
        for idx, ch in enumerate(raw):
            if _KEEP_RE.match(ch):
                offsets.append(idx)
        return offsets

    def _norm_span(self, pos: int, length: int) -> tuple[int, int] | None:
        """规范化文本下标 → 原文区间 [start, end)。越界返回 None。"""
        offsets = self._norm_offsets
        if pos < 0 or not offsets:
            return None
        end_index = min(pos + max(1, length) - 1, len(offsets) - 1)
        return offsets[pos], offsets[end_index] + 1

    def _squashed_span(self, pos: int, length: int) -> tuple[int, int] | None:
        """压缩文本下标 → 原文区间 [start, end)（区间内含被压缩掉的标点/空白）。越界返回 None。"""
        offsets = self._squashed_offsets
        if pos < 0 or not offsets:
            return None
        end_index = min(pos + max(1, length) - 1, len(offsets) - 1)
        return offsets[pos], offsets[end_index] + 1

    # -------------------------------------------------------------- 公共
    def verify(self, quote: str, claimed_page: int | None = None) -> dict:
        """返回 {found, page, score, method, mismatch}。"""
        result = {"found": False, "page": None, "score": 0.0, "method": "none"}
        cleaned = normalize(quote)
        if len(cleaned) < 6:
            result["method"] = "too_short"
            return result

        # 1) 规范化精确匹配
        pos = self._norm.find(cleaned)
        if pos >= 0:
            result.update(found=True, page=self._page_at(pos), score=1.0, method="exact")
        else:
            # 2) 压缩形态匹配
            squashed = collapse_for_search(cleaned)
            spos = self._squashed.find(squashed) if len(squashed) >= 8 else -1
            if spos >= 0:
                result.update(
                    found=True,
                    page=self._page_at_squashed(spos),
                    score=1.0,
                    method="squashed",
                )
            else:
                # 3) 最长片段匹配
                best_len = 0
                best_pos = -1
                for frag in self._fragments(cleaned):
                    fpos = self._norm.find(frag)
                    if fpos < 0:
                        fpos = self._squashed_find_as_norm(frag)
                    if fpos >= 0 and len(frag) > best_len:
                        best_len, best_pos = len(frag), fpos
                if best_pos >= 0:
                    ratio = best_len / max(1, len(cleaned))
                    result.update(
                        found=ratio >= 0.5,
                        page=self._page_at(best_pos),
                        score=round(ratio, 3),
                        method="fragment",
                    )
                else:
                    result["method"] = "not_found"

        if result["page"] is None and result["found"]:
            result["page"] = self._page_at_squashed(self._squashed.find(collapse_for_search(cleaned)))

        result["mismatch"] = bool(
            claimed_page and result["page"] and int(claimed_page) != int(result["page"])
        )
        return result

    def window(self, quote: str, before: int = 30, after: int = 60) -> str:
        """返回引文命中位置在原文里的简要上下文（含命中前后各一段原文）。

        定位策略与 verify() 完全一致（规范化精确匹配 → 压缩空白/连字符匹配 →
        最长片段匹配），命中后把下标映射回原文并截取窗口；找不到返回空串。
        纯只读操作，不改变任何内部状态，也不影响 verify() 的返回契约。
        返回值把换行折叠为空格、去掉 ## 标题标记，适合一行简要展示。
        """
        cleaned = normalize(quote)
        if len(cleaned) < 6 or not self.raw or not self._norm:
            return ""
        span = None
        pos = self._norm.find(cleaned)
        if pos >= 0:
            span = self._norm_span(pos, len(cleaned))
        else:
            squashed = collapse_for_search(cleaned)
            spos = self._squashed.find(squashed) if len(squashed) >= 8 else -1
            if spos >= 0:
                span = self._squashed_span(spos, len(squashed))
            else:
                best_len = 0
                best_span = None
                for frag in self._fragments(cleaned):
                    fpos = self._norm.find(frag)
                    if fpos >= 0:
                        candidate = self._norm_span(fpos, len(frag))
                    else:
                        needle = collapse_for_search(frag)
                        npos = self._squashed.find(needle) if len(needle) >= 8 else -1
                        candidate = self._squashed_span(npos, len(needle)) if npos >= 0 else None
                    if candidate and len(frag) > best_len:
                        best_len, best_span = len(frag), candidate
                span = best_span
        if not span:
            return ""
        start, end = span
        lo = max(0, start - before)
        hi = min(len(self.raw), end + after)
        head = self.raw[lo:start].strip()
        core = self.raw[start:end].strip()
        tail = self.raw[end:hi].strip()
        parts = []
        if head:
            parts.append(("…" if lo > 0 else "") + head)
        parts.append(core)
        if tail:
            parts.append(tail + ("…" if hi < len(self.raw) else ""))
        joined = " ".join(parts)
        joined = re.sub(r"\s*##\s+", " ", joined)   # 去掉解析阶段打的 ## 标题标记
        joined = re.sub(r"\s+", " ", joined).strip()
        return joined

    def _squashed_find_as_norm(self, frag: str) -> int:
        """把压缩匹配命中的位置映射回规范化文本下标；失败返回 -1。"""
        needle = collapse_for_search(frag)
        if len(needle) < 8:
            return -1
        idx = self._squashed.find(needle)
        if idx < 0:
            return -1
        page = self._page_at_squashed(idx)
        if page is None:
            return -1
        start, end = self.page_spans[page - 1]
        local = self._norm.find(normalize(self.raw[start:end])[: max(8, len(frag))])
        return -1 if local < 0 else local

    @staticmethod
    def _fragments(text: str, min_len: int = 24) -> list[str]:
        if len(text) < min_len:
            return [text] if len(text) >= 8 else []
        pieces = [p.strip() for p in re.split(r"[,;，；。.]\s*", text) if p.strip()]
        frags = [p for p in pieces if len(p) >= min_len]
        if not frags:
            frags = [text[i : i + min_len] for i in range(0, max(1, len(text) - min_len + 1), min_len)]
        return sorted(frags, key=len, reverse=True)[:6]
