"""全局配置：从环境变量 / .env 读取，任何密钥都不写进源码。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
VENDOR_DIR = ASSETS_DIR / "vendor"
TEMPLATE_DIR = ASSETS_DIR / "templates"
MERMAID_JS = VENDOR_DIR / "mermaid.min.js"
DASHBOARD_TEMPLATE = TEMPLATE_DIR / "dashboard.html"


def _load_dotenv() -> None:
    """加载 .env；优先用 python-dotenv，缺失时退化为极简解析器。"""
    env_file = PROJECT_ROOT / ".env"
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_file, override=False)
        return
    except Exception:  # pragma: no cover - 退化路径
        pass
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env_str(name, "").lower()
    if value == "":
        return default
    return value in {"1", "true", "yes", "on", "y"}


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
@dataclass
class Settings:
    """运行期设置。所有字段都可被环境变量覆盖。"""

    # --- 大模型 ---
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-pro"
    thinking: str = "disabled"
    temperature: float = 0.2
    max_tokens: int = 8192
    timeout: int = 180
    max_retries: int = 2
    retry_base_delay: float = 2.0  # 指数退避基数：2s → 4s → 8s（仅 429/5xx/超时等瞬时错误）

    # --- 解析 ---
    max_input_chars: int = 60000
    strict_citation: bool = False

    # --- 输出 ---
    output_html: str = ""
    keep_debug: bool = False

    # --- 运行模式 ---
    mock: bool = False
    mock_file: str = ""
    mock_pdf: str = ""  # 当前处理的 PDF 路径，供离线模式查找同名 .analysis.json
    verbose: bool = False

    # --- 元信息 ---
    warnings: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_env(cls) -> "Settings":
        warnings: list[str] = []
        if hasattr(os, "environ") and os.environ.get("OPENAI_API_KEY") and not os.environ.get("LLM_API_KEY"):
            warnings.append("检测到 OPENAI_API_KEY，已作为 LLM_API_KEY 使用。")
        api_key = _env_str("LLM_API_KEY") or _env_str("OPENAI_API_KEY")
        return cls(
            api_key=api_key,
            base_url=_env_str("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=_env_str("LLM_MODEL", "deepseek-v4-pro"),
            thinking=_env_str("LLM_THINKING", "disabled").lower(),
            temperature=_env_float("LLM_TEMPERATURE", 0.2),
            max_tokens=_env_int("LLM_MAX_TOKENS", 8192),
            timeout=_env_int("LLM_TIMEOUT", 180),
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
            retry_base_delay=_env_float("LLM_RETRY_BASE_DELAY", 2.0),
            max_input_chars=_env_int("MAX_INPUT_CHARS", 60000),
            strict_citation=_env_bool("STRICT_CITATION", False),
            output_html=_env_str("OUTPUT_HTML", ""),
            keep_debug=_env_bool("KEEP_DEBUG", False),
            mock=_env_bool("MOCK", False),
            mock_file=_env_str("MOCK_FILE", ""),
            verbose=_env_bool("VERBOSE", False),
            warnings=warnings,
        )

    # ---------------------------------------------------------------- 校验
    def validate(self) -> list[str]:
        """返回阻塞性问题列表；为空表示可以真实调用大模型。"""
        problems: list[str] = []
        if not self.api_key:
            problems.append(
                "未配置 API Key。请复制 .env.example 为 .env 并填写 LLM_API_KEY，"
                "或使用 `--mock` 离线演示模式。"
            )
        if not self.base_url.startswith(("http://", "https://")):
            problems.append(f"LLM_BASE_URL 不是合法 URL：{self.base_url!r}")
        if not self.model:
            problems.append("LLM_MODEL 为空。")
        if self.thinking not in {"enabled", "disabled"}:
            problems.append("LLM_THINKING 只能是 enabled 或 disabled。")
        return problems

    def masked_key(self) -> str:
        if not self.api_key:
            return "(未配置)"
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"
