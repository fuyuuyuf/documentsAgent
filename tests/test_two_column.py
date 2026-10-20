"""双栏解析专项测试：现场合成两栏 PDF，验证阅读顺序 / 通栏分隔带 / 栏底脚注 / 跨页衔接。

用法：
    python tests/test_two_column.py

不调用任何大模型 API（纯解析层，parse_pdf 不触网），可反复运行。
每个用例用 PyMuPDF 现场生成 PDF，并故意把内容流顺序打乱——
只有列感知重排工作正常时，断言才能通过。
"""

from __future__ import annotations

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
from src.parse import _order_blocks, _reflow_with_bands, parse_pdf  # noqa: E402

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
# 合成 PDF 的版面参数（与真实双栏论文一致的页心/栏宽/栏间距）
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = 595.0, 842.0
LEFT_COL = (72.0, 290.0)    # 左栏 x 范围（宽 218）
RIGHT_COL = (307.0, 525.0)  # 右栏 x 范围（宽 218），栏间距 17pt
SPAN_RECT = (72.0, 523.0)   # 通栏块 x 范围（宽 451）
BODY_SIZE = 10.5
BAND_SIZE = 11.5
FOOTNOTE_SIZE = 8.5

_COLUMN_GAP = (  # 左右栏之间的空白带，用于构造三栏等扩展场景
    "the gutter between two text columns stays empty so the parser can detect it "
)


def _body_text(tag: int) -> str:
    """正文块：以 'Block {tag}' 开头，长度足够铺满整栏宽度。"""
    return (
        f"Block {tag} checks coordinate aware reading order for two column papers. "
        "The parser must keep each column as a continuous stream instead of "
        "interleaving lines from the left and the right column on the same page."
    )


def _band_text(tag: str) -> str:
    """通栏块：跨满页心宽度，作为分隔带（标题 / 图表说明）。"""
    return (
        f"SPANBAND-{tag} spans the entire page body width and separates the upper "
        "band from the lower band, the way a section title or a figure caption "
        "would cross both columns of a real two column paper."
    )


def _footnote_text(marker: str, side: str) -> str:
    """栏底脚注：小字号 + 脚注标记开头，且刻意长得像章节标题（'1We ...'）。"""
    return (
        f"{marker}We thank the anonymous reviewers for comments on the {side} "
        "footnote fixture and for checking the parsing pipeline behaviour here."
    )


def _put(page, col: tuple[float, float], y: float, text: str, size: float = BODY_SIZE) -> None:
    """把一段文字放进指定 x 范围（栏/通栏）、顶端为 y 的文本框。"""
    page.insert_textbox(pymupdf.Rect(col[0], y, col[1], y + 160), text, fontsize=size, fontname="helv")


def _token_seq(text: str) -> list[str]:
    """按出现顺序抽取正文令牌：Block N / SPANBAND-x。"""
    seq: list[str] = []
    for m in re.finditer(r"Block (\d+)|SPANBAND-([A-Za-z0-9]+)", text):
        seq.append(m.group(1) or m.group(2))
    return seq


def _parse(path: Path):
    return parse_pdf(path, Settings())


# ---------------------------------------------------------------------------
# 场景一：左栏 1/2/3 + 通栏带 + 右栏 4/5/6（内容流完全乱序）
# ---------------------------------------------------------------------------
def build_separated_band_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # 内容流乱序插入：右栏在前、通栏夹中、左栏在后
    _put(page, RIGHT_COL, 400, _body_text(4))
    _put(page, SPAN_RECT, 340, _band_text("T1"), BAND_SIZE)
    _put(page, LEFT_COL, 100, _body_text(1))
    _put(page, RIGHT_COL, 550, _body_text(6))
    _put(page, LEFT_COL, 250, _body_text(3))
    _put(page, RIGHT_COL, 475, _body_text(5))
    _put(page, LEFT_COL, 175, _body_text(2))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# 场景二：跨页衔接（页1：通栏标题 + 左1-3/右4-6；页2：左7-9/右10-12）
# ---------------------------------------------------------------------------
def build_cross_page_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _put(page1, RIGHT_COL, 110, _body_text(4))
    _put(page1, LEFT_COL, 260, _body_text(3))
    _put(page1, SPAN_RECT, 70, _band_text("H"), BAND_SIZE)
    _put(page1, LEFT_COL, 110, _body_text(1))
    _put(page1, RIGHT_COL, 260, _body_text(6))
    _put(page1, LEFT_COL, 185, _body_text(2))
    _put(page1, RIGHT_COL, 185, _body_text(5))

    page2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _put(page2, RIGHT_COL, 110, _body_text(10))
    _put(page2, LEFT_COL, 260, _body_text(9))
    _put(page2, LEFT_COL, 110, _body_text(7))
    _put(page2, RIGHT_COL, 260, _body_text(12))
    _put(page2, LEFT_COL, 185, _body_text(8))
    _put(page2, RIGHT_COL, 185, _body_text(11))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# 场景三：通栏图表说明把页面分成上下两条带，每条带内左右两栏并行
# ---------------------------------------------------------------------------
def build_caption_bands_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _put(page, RIGHT_COL, 470, _body_text(6))
    _put(page, LEFT_COL, 100, _body_text(1))
    _put(page, SPAN_RECT, 300, _band_text("FIG"), BAND_SIZE)
    _put(page, RIGHT_COL, 100, _body_text(2))
    _put(page, LEFT_COL, 470, _body_text(5))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# 场景四：栏底脚注不混入正文流
# ---------------------------------------------------------------------------
def build_footnote_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # 脚注故意插在内容流中间，且左栏脚注以数字开头（容易被误判成标题）
    _put(page, RIGHT_COL, 100, _body_text(4))
    _put(page, LEFT_COL, 720, _footnote_text("1", "left column"), FOOTNOTE_SIZE)
    _put(page, LEFT_COL, 100, _body_text(1))
    _put(page, RIGHT_COL, 175, _body_text(5))
    _put(page, LEFT_COL, 175, _body_text(2))
    _put(page, RIGHT_COL, 720, _footnote_text("*", "right column"), FOOTNOTE_SIZE)
    _put(page, RIGHT_COL, 250, _body_text(6))
    _put(page, LEFT_COL, 250, _body_text(3))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# 场景五：三栏论文（降级鲁棒性）
# ---------------------------------------------------------------------------
def build_three_column_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    cols = ((52.0, 218.0), (240.0, 406.0), (428.0, 594.0))
    short = "Block {tag} checks coordinate aware reading order for multi column papers."
    # 内容流乱序：右栏 → 中栏 → 左栏（短块 + 大间距，避免块重叠被合并）
    for tag, y in ((7, 100), (8, 200), (9, 300)):
        _put(page, cols[2], y, short.format(tag=tag))
    for tag, y in ((4, 100), (5, 200), (6, 300)):
        _put(page, cols[1], y, short.format(tag=tag))
    for tag, y in ((1, 100), (2, 200), (3, 300)):
        _put(page, cols[0], y, short.format(tag=tag))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# 场景六：单栏论文（零回归）
# ---------------------------------------------------------------------------
def build_single_column_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # 窄块（居中公式风格的短行）也要保持原位
    _put(page, (200.0, 420.0), 330, "Block 21 sits between two full width paragraphs.")
    _put(page, SPAN_RECT, 250, _body_text(22))
    _put(page, SPAN_RECT, 70, _band_text("S"), BAND_SIZE)
    _put(page, SPAN_RECT, 120, _body_text(20))
    _put(page, SPAN_RECT, 400, _body_text(23))
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
def test_separated_band_order(tmp: Path) -> None:
    print("\n[场景一] 左栏 1/2/3 → 通栏带 → 右栏 4/5/6")
    path = tmp / "band.pdf"
    build_separated_band_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    check("正文顺序为 1→2→3→T1→4→5→6", seq == ["1", "2", "3", "T1", "4", "5", "6"], str(seq))


def test_cross_page_order(tmp: Path) -> None:
    print("\n[场景二] 跨页衔接：页1 左→右，页2 接左→右")
    path = tmp / "cross.pdf"
    build_cross_page_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    expected = ["H", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
    check("全局顺序为 H→1..6→7..12", seq == expected, str(seq))
    check(
        "页1 右栏末尾(6)之后接页2 左栏开头(7)",
        seq.index("6") < seq.index("7") and "7" in paper.page_texts[1],
        str(seq),
    )
    check(
        "页2 内部仍先左栏(7,8,9)后右栏(10,11,12)",
        seq[-6:] == ["7", "8", "9", "10", "11", "12"],
        str(seq[-6:]),
    )


def test_caption_band_between_columns(tmp: Path) -> None:
    print("\n[场景三] 通栏图表说明作为分隔带：上带左右两栏 → 通栏 → 下带左右两栏")
    path = tmp / "caption.pdf"
    build_caption_bands_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    check("正文顺序为 1→2→FIG→5→6", seq == ["1", "2", "FIG", "5", "6"], str(seq))


def test_footnotes_keep_out_of_flow(tmp: Path) -> None:
    print("\n[场景四] 栏底脚注后置，不截断 左栏→右栏 正文流")
    path = tmp / "footnote.pdf"
    build_footnote_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    check("正文顺序为 1→2→3→4→5→6", seq == ["1", "2", "3", "4", "5", "6"], str(seq))
    flat = re.sub(r"\s+", " ", paper.full_text)
    left_fn = flat.find("left column footnote fixture")
    right_fn = flat.find("right column footnote fixture")
    check(
        "脚注出现在全部正文之后（页尾）",
        min(left_fn, right_fn) > flat.find("Block 6") > -1,
        str(_token_seq(paper.full_text)),
    )
    check("左栏脚注排在右栏脚注之前", 0 <= left_fn < right_fn, f"left={left_fn} right={right_fn}")
    check("脚注内容未丢失", "footnote fixture" in paper.full_text)
    check("脚注未被误判成章节标题", "## 1We" not in paper.full_text, paper.full_text[:0])
    check("大纲中不含脚注碎片", all("footnote fixture" not in h.text for h in paper.headings))


def test_three_column_robust(tmp: Path) -> None:
    print("\n[场景五] 三栏论文：按列分派且顺序为 1..9，不崩溃")
    path = tmp / "three.pdf"
    build_three_column_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    check("正文顺序为 1..9（先左栏再中栏再右栏）", seq == [str(i) for i in range(1, 10)], str(seq))


def test_single_column_no_regression(tmp: Path) -> None:
    print("\n[场景六] 单栏论文零回归：y 序即阅读序，窄块原位保留")
    path = tmp / "single.pdf"
    build_single_column_pdf(path)
    paper = _parse(path)
    seq = _token_seq(paper.full_text)
    check("正文顺序为 S→20→22→21→23（窄块 21 保持原位）", seq == ["S", "20", "22", "21", "23"], str(seq))


def test_order_blocks_defensive(tmp: Path) -> None:
    print("\n[防御性] 退化输入与异常兜底")
    check("空块列表不崩溃", _order_blocks([], 595, 842, 10.5) == [])

    import pymupdf as pm

    lone = pm.open()
    page = lone.new_page(width=595, height=842)
    page.insert_textbox(pm.Rect(72, 100, 500, 200), "Block 1 alone on the page.", fontsize=10.5)
    data = page.get_text("dict")
    from src.parse import Block

    blocks = [
        Block(
            page=1,
            text="\n".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]),
            bbox=tuple(b["bbox"]),
            size=10.5,
            bold=False,
        )
        for b in data["blocks"]
        if b.get("type") == 0
    ]
    lone.close()
    check("单块列表原样返回", len(_order_blocks(blocks, 595, 842, 10.5)) == 1)

    original = _reflow_with_bands
    try:
        import src.parse as parse_mod

        parse_mod._reflow_with_bands = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = _order_blocks(blocks * 3, 595, 842, 10.5)
            check("v2 抛异常时退回旧实现且不崩", len(result) == len(blocks) * 3)
        except Exception as exc:  # pragma: no cover
            check("v2 抛异常时退回旧实现且不崩", False, repr(exc))
    finally:
        import src.parse as parse_mod

        parse_mod._reflow_with_bands = original


def test_real_two_column_fixture(tmp: Path) -> None:
    print("\n[附加] 真实双栏样例（examples/sample_en_two_column.pdf）顺序抽查")
    paper = _parse(PROJECT_ROOT / "examples" / "sample_en_two_column.pdf")
    flat = re.sub(r"\s+", " ", paper.full_text)
    checks = [
        ("标题被还原成完整一句",
         paper.title == "Retrieval-Augmented Question Answering with Evidence-Aware Reranking"),
        ("标题两段在正文中相邻", "Evidence-Aware Reranking" in flat),
        ("摘要在引言之前", flat.find("Open-domain question answering (QA) systems increasingly rely")
        < flat.find("1 Introduction") if "1 Introduction" in flat else False),
    ]
    for name, ok in checks:
        check(name, bool(ok))


def main() -> int:
    print("=" * 68)
    print("  双栏解析专项测试（现场合成 PDF，全程离线）")
    print("=" * 68)
    with tempfile.TemporaryDirectory(prefix="twocol_") as tmp_name:
        tmp = Path(tmp_name)
        for test in (
            test_separated_band_order,
            test_cross_page_order,
            test_caption_band_between_columns,
            test_footnotes_keep_out_of_flow,
            test_three_column_robust,
            test_single_column_no_regression,
            test_order_blocks_defensive,
            test_real_two_column_fixture,
        ):
            try:
                test(tmp)
            except Exception:
                FAILED.append(f"{test.__name__} 异常 {traceback.format_exc(limit=1)}")
                print(f"  ✗ {test.__name__} 执行异常：{traceback.format_exc(limit=1)}")

    print("=" * 68)
    print(f"  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print(f"  ✗ {item}")
        print("=" * 68)
        return 1
    print("  ✓ 全部通过")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
