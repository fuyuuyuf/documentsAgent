"""文档解析智能体：PDF → 结构化分析 → Mermaid 导图 → 离线交互式 HTML 看板。

对外主要接口::

    from src import run_pipeline, Settings
    result = run_pipeline("paper.pdf")
    print(result.html_path)

命令行入口见 ``src/main.py``。
"""

from __future__ import annotations

__version__ = "1.0.0"

from .bootstrap import check_environment, ensure_ready  # 纯标准库，缺依赖时也可用
from .config import Settings  # noqa: E402 纯标准库，缺依赖时也可用

try:  # 以下模块依赖第三方库（pymupdf 等）；缺失时跳过，由 bootstrap.py 引导安装
    from .diagram import Diagram, build_diagram, repair_mermaid, validate_mermaid  # noqa: E402
    from .llm import LLMClient, LLMError  # noqa: E402
    from .parse import PaperText, parse_pdf  # noqa: E402
    from .pipeline import Logger, PipelineResult, run_pipeline  # noqa: E402
    from .render import build_payload, render_html, write_dashboard  # noqa: E402
except ImportError:  # pragma: no cover - 环境缺依赖时保证 `import src` 不失败
    pass

__all__ = [
    "__version__",
    "Settings",
    "check_environment",
    "ensure_ready",
    "PaperText",
    "parse_pdf",
    "LLMClient",
    "LLMError",
    "Diagram",
    "build_diagram",
    "validate_mermaid",
    "repair_mermaid",
    "build_payload",
    "render_html",
    "write_dashboard",
    "run_pipeline",
    "Logger",
    "PipelineResult",
]
