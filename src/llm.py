"""大模型客户端：OpenAI 兼容 API、鲁棒 JSON 解析、失败自动重问、离线 mock 模式。

反幻觉的关键约定：模型只负责"读论文 → 输出结构化 JSON"，
Mermaid 语法与 HTML 由本地代码确定性生成，模型不直接产出代码。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .textutils import truncate


class LLMError(RuntimeError):
    """所有大模型相关失败的统一出口，便于上层给出友好提示。"""


@dataclass
class LLMResult:
    text: str
    model: str
    rounds: int = 1
    usage: dict = field(default_factory=dict)
    repaired: bool = False


def estimate_tokens(text: str) -> int:
    """离线估算一段文本的 token 数（无 API usage 时的兜底口径）。

    经验值：中文约 0.6 token/字（1.6-1.7 字/token），英文约 0.25 token/字符
    （4 字符/token）。只用于日志展示与费用预估，不参与任何业务判断。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, round(cjk * 0.6 + other / 4))


# ---------------------------------------------------------------------------
# JSON 抽取
# ---------------------------------------------------------------------------
_CODE_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """从模型输出里尽力抽取一个 JSON 对象。

    依次尝试：直接解析 → 去代码围栏 → 括号配对扫描。
    全部失败抛 LLMError。
    """
    if text is None:
        raise LLMError("模型返回为空。")
    raw = text.strip()
    if not raw:
        raise LLMError("模型返回为空。")

    for candidate in (raw, *_CODE_FENCE_RE.findall(raw)):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 括号配对扫描：处理"前后有解释文字"的情况
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(raw)):
            ch = raw[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    snippet = raw[start : idx + 1]
                    try:
                        return json.loads(snippet)
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"无法从模型输出中解析出 JSON。开头内容：{truncate(raw, 180)}")


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class LLMClient:
    """OpenAI 兼容接口封装（DeepSeek / Kimi / 通义 / Ollama / vLLM 均可）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = "mock" if settings.mock else "api"
        self.call_count = 0
        self.total_tokens = 0
        self._client = None
        self.mock_payload: dict | None = None
        # 重试进度回调：pipeline 会把它接到 Logger.warn 上，让退避重试可见。
        # 离线 mock 模式同样保留该属性（只是永远不会触发），保证两条路径代码一致。
        self.log: Callable[[str], None] | None = None

        if self.mode == "mock":
            self.mock_payload = self._load_mock_payload()
            return

        problems = settings.validate()
        if problems:
            raise LLMError("；".join(problems))
        self._client = self._build_client()

    # ------------------------------------------------------------ 构造
    def _build_client(self):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LLMError("缺少 openai 依赖，请执行：pip install -r requirements.txt") from exc
        try:
            return OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
                timeout=float(self.settings.timeout),
                max_retries=0,  # 重试逻辑自己控制，便于给出清晰日志
            )
        except Exception as exc:
            raise LLMError(f"初始化大模型客户端失败：{exc}") from exc

    # ------------------------------------------------------------ mock
    def _load_mock_payload(self) -> dict:
        """离线模式加载顺序：

        1. ``MOCK_FILE`` / ``--mock-file`` 显式指定的文件（最高优先级）
        2. 输入 PDF 同目录下的 ``<论文名>.analysis.json`` 或 ``<论文名>.json``
           （这样每篇示例论文都能配一份"真实感"的离线分析，证据引用可被溯源校验命中）
        3. 仓库自带的 ``examples/sample_analysis.json``
        4. 内置占位数据（保证完全没有素材时也能跑通流程）
        """
        candidates: list[Path] = []
        if self.settings.mock_file:
            candidates.append(Path(self.settings.mock_file).expanduser())

        pdf = Path(self.settings.mock_pdf).expanduser() if self.settings.mock_pdf else None
        if pdf is not None:
            stem = pdf.stem
            # sample_en_two_column.pdf → sample_en_two_column.analysis.json
            candidates.append(pdf.with_name(stem + ".analysis.json"))
            # 也接受 sample_en_two_column.analysis.pdf → sample_en_two_column.analysis.json
            candidates.append(pdf.with_suffix(".json"))
            candidates.append(pdf.parent / f"{stem}.json")

        candidates.append(Path(__file__).resolve().parent.parent / "examples" / "sample_analysis.json")

        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            if isinstance(data, dict):
                data["_mock_source"] = str(path)
                return data
        return {"_mock_source": "(builtin)", "_builtin": True}

    # ------------------------------------------------------------ 调用
    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        if self.mode == "mock":
            return self._mock_complete(system, user, json_mode=json_mode)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = max(1, self.settings.max_retries + 1)
        base_delay = max(0.0, float(getattr(self.settings, "retry_base_delay", 2.0)))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "messages": messages,
                "temperature": self.settings.temperature if temperature is None else temperature,
                "max_tokens": self.settings.max_tokens if max_tokens is None else max_tokens,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if self.settings.model.startswith("deepseek-v4"):
                # V4 默认开启思考模式；结构化 JSON 任务使用非思考模式可避免推理占满输出预算。
                kwargs["extra_body"] = {"thinking": {"type": self.settings.thinking}}
            try:
                response = self._client.chat.completions.create(**kwargs)  # type: ignore[union-attr]
            except Exception as exc:
                if json_mode and _looks_like_unsupported(exc):
                    # 部分服务不支持 response_format，降级为纯文本重试
                    json_mode = False
                    attempts = max(attempts, attempt + 1)
                    continue
                if _classify_error(exc) == "fatal":
                    # 401/403/404/参数错误/余额不足等确定性错误：重试没有意义，直接给中文报错
                    raise self._wrap_error(exc) from exc
                if attempt >= attempts:
                    raise self._wrap_error(exc) from exc
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                self._notify_retry(attempt, attempts - 1, exc, delay)
                time.sleep(delay)
                continue

            self.call_count += 1
            usage = getattr(response, "usage", None)
            usage_dict = {}
            if usage is not None:
                usage_dict = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
                self.total_tokens += usage_dict.get("total_tokens", 0)

            content = ""
            try:
                content = response.choices[0].message.content or ""
            except (AttributeError, IndexError, TypeError):
                content = ""
            if not content.strip():
                if attempt >= attempts:
                    raise LLMError("模型返回内容为空。")
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                self._notify_retry(attempt, attempts - 1, LLMError("模型返回内容为空"), delay)
                time.sleep(delay)
                continue
            return LLMResult(text=content, model=self.settings.model, rounds=attempt, usage=usage_dict)

        raise self._wrap_error(last_error or LLMError("未知错误"))

    def _notify_retry(self, attempt: int, max_retries: int, exc: Exception, delay: float) -> None:
        """把一次退避重试报告给上层（pipeline 会接到日志；默认静默）。"""
        if self.log is None:
            return
        try:
            self.log(
                f"大模型请求失败（{truncate(str(exc), 100)}），"
                f"{delay:.0f} 秒后进行第 {attempt}/{max_retries} 次重试"
            )
        except Exception:  # noqa: BLE001 - 日志回调绝不能影响主流程
            pass

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        validate: Callable[[Any], None] | None = None,
        repair_hint: str = "",
        max_tokens: int | None = None,
    ) -> tuple[dict, LLMResult]:
        """要求模型返回 JSON；校验失败时带着错误信息重问一次（自修复）。"""
        result = self.complete(system, user, json_mode=True, max_tokens=max_tokens)
        try:
            data = extract_json(result.text)
            if validate:
                validate(data)
            if not isinstance(data, dict):
                raise LLMError("顶层 JSON 必须是对象。")
            return data, result
        except LLMError as first_error:
            repair_user = (
                f"{user}\n\n"
                "----- 上一次的输出不合格，请重新输出 -----\n"
                f"错误信息：{first_error}\n"
                + (f"额外要求：{repair_hint}\n" if repair_hint else "")
                + "请只输出一个合法的 JSON 对象，不要任何解释文字或代码围栏。"
            )
            repaired = self.complete(system, repair_user, json_mode=True, max_tokens=max_tokens)
            try:
                data = extract_json(repaired.text)
                if validate:
                    validate(data)
                if not isinstance(data, dict):
                    raise LLMError("顶层 JSON 必须是对象。")
                repaired.repaired = True
                return data, repaired
            except LLMError as second_error:
                raise LLMError(
                    f"两次尝试均未能得到合法 JSON。第一次：{first_error}；第二次：{second_error}"
                ) from second_error

    # ------------------------------------------------------------ 工具
    def _mock_analysis(self) -> dict:
        """离线模式的第一轮分析数据（含来源标记），两次 mock 调用共用同一份。"""
        payload = dict(self.mock_payload or {})
        builtin = bool(payload.pop("_builtin", False))
        source = payload.pop("_mock_source", "(builtin)")
        if builtin:
            payload = _builtin_analysis()
        payload["_meta"] = {
            "mode": "mock",
            "source": source,
            "note": "离线演示模式：未调用任何大模型 API，导图内容来自本地样例 JSON。",
        }
        return payload

    def _mock_complete(self, system: str, user: str, *, json_mode: bool) -> LLMResult:
        """离线模式的"模型响应"。

        与真实模式走同一个 ``complete_json`` 入口：第一轮返回完整分析 JSON；
        第二轮（通俗解读）按 user prompt 中的 ``"story"`` 字段识别，返回由
        分析内容本地拼装的解读 JSON——mock 不产生新事实，只演示同样的数据流。
        """
        payload = self._mock_analysis()
        body: dict
        if json_mode and '"story"' in user:
            body = _mock_audience_response(payload)
        else:
            body = payload
        text = json.dumps(body, ensure_ascii=False, indent=2) if json_mode else json.dumps(body, ensure_ascii=False)
        return LLMResult(text=text, model="mock-offline", rounds=1, usage={"total_tokens": 0})

    @staticmethod
    def _wrap_error(exc: Exception) -> LLMError:
        name = type(exc).__name__
        message = str(exc)
        hint = ""
        lowered = message.lower()
        if "401" in message or "invalid api key" in lowered or "authentication" in lowered:
            hint = "（API Key 无效或已过期，请检查 .env 中的 LLM_API_KEY）"
        elif "404" in message:
            hint = "（Base URL 或模型名不正确，请检查 LLM_BASE_URL / LLM_MODEL）"
        elif "429" in message or "rate limit" in lowered:
            hint = "（触发限流，请稍后重试或降低请求频率）"
        elif "insufficient" in lowered or "balance" in lowered or "quota" in lowered:
            hint = "（账户余额或额度不足）"
        elif "timeout" in lowered or "timed out" in lowered:
            hint = "（请求超时，可调大 LLM_TIMEOUT 或换用更快的模型）"
        elif "connection" in lowered or "connect" in lowered:
            hint = "（网络不可达，请检查网络或 Base URL 是否可访问）"
        return LLMError(f"调用大模型失败：{name}: {truncate(message, 300)}{hint}")


def _looks_like_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "response_format",
            "json_object",
            "unsupported",
            "not supported",
            "invalid request",
            "unknown parameter",
        )
    )


# 确定性失败的 HTTP 状态码：重试只会重复失败、白烧时间，必须立刻给出中文报错。
_FATAL_STATUS = {400, 401, 403, 404, 405, 422}
# 无 status_code 时的字符串兜底判断（按出现顺序，先判确定性失败再判瞬时失败）。
_FATAL_MARKERS = (
    "401",
    "403",
    "invalid api key",
    "authentication",
    "unauthorized",
    "404",
    "model not found",
    "invalid request",
    "unknown parameter",
    "insufficient",
    "quota",
    "balance",
)
_TRANSIENT_MARKERS = (
    "429",
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "overloaded",
    "server error",
    "internal error",
    "bad gateway",
    "service unavailable",
    "529",
)


def _classify_error(exc: Exception) -> str:
    """把异常分为 ``fatal``（重试无意义）与 ``transient``（值得指数退避重试）。

    优先读 SDK 的 ``status_code``（openai 库的 APIStatusError 都带）；
    拿不到再用错误消息做保守判断，未知错误按瞬时处理（网络抖动占多数）。
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return "fatal" if status in _FATAL_STATUS else "transient"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "transient"
    message = str(exc).lower()
    if any(marker in message for marker in _FATAL_MARKERS):
        return "fatal"
    if any(marker in message for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "transient"


def _mock_audience_response(analysis: dict) -> dict:
    """离线模式的第二轮（通俗解读）响应。

    story 由分析 JSON 的研究问题 / 方法 / 结果 / 局限本地拼装而成；
    glossary 原样返回样例数据（管线里的合并逻辑会按 term 对齐覆盖）。
    字段结构与真实模型被要求输出的结构完全一致，便于离线验证下游代码路径。
    """
    glossary = [dict(item) for item in (analysis.get("glossary") or []) if isinstance(item, dict)]
    return {"story": _compose_story(analysis), "glossary": glossary}


def _compose_story(analysis: dict) -> str:
    """从第一轮分析 JSON 里拼一段"这篇论文在做一件什么事"的整体叙述。

    只做确定性拼装、不引入分析之外的新事实；供 --mock 演示第二轮的
    story 合并效果（真实运行时该段由大模型撰写）。
    """
    problem = ""
    raw_problem = analysis.get("research_problem")
    if isinstance(raw_problem, dict):
        problem = str(raw_problem.get("statement") or "").strip()

    method_labels = []
    for item in analysis.get("method") or []:
        if isinstance(item, dict) and str(item.get("label") or "").strip():
            method_labels.append(str(item["label"]).strip())
    method_text = "、".join(method_labels[:4]) if method_labels else "论文中的核心方法"

    result_text = ""
    for key in ("evidence_and_results", "conclusions"):
        items = [it for it in (analysis.get(key) or []) if isinstance(it, dict)]
        if items:
            result_text = str(items[0].get("detail") or "").strip()
            break

    limitation_text = ""
    for item in analysis.get("limitations") or []:
        if isinstance(item, dict) and str(item.get("detail") or "").strip():
            limitation_text = str(item["detail"]).strip()
            break

    def _sentence(text: str) -> str:
        return text.rstrip("。．.!！?？;； \t\r\n") + "。" if text else ""

    parts = [f"这篇论文要做的事：{problem}" if problem else "这篇论文提出并验证了一个新的处理方法。"]
    parts.append(_sentence(f"作者的做法是：{method_text}"))
    if result_text:
        parts.append(_sentence(f"关键结果是：{result_text}"))
    if limitation_text:
        parts.append(_sentence(f"需要注意的局限是：{limitation_text}"))
    return "".join(parts)


def _builtin_analysis() -> dict:
    """内置兜底样例，保证在完全没有配置的情况下也能演示完整流程。"""
    return {
        "title": "离线演示样例：论文解析结果占位",
        "one_sentence_summary": "这是内置的离线演示数据，用于在未配置 API Key 时验证 PDF→导图→看板的完整链路。",
        "research_problem": {
            "statement": "占位内容：请配置 API Key 后重新运行，以得到真实论文的分析结果。",
            "why_it_matters": "离线模式下不调用任何大模型，因此内容为固定样例。",
            "evidence": "占位",
            "page": None,
        },
        "contributions": [
            {
                "label": "占位贡献 1",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "novelty": "占位",
                "evidence": "占位",
                "page": None,
            }
        ],
        "method": [
            {
                "label": "占位方法步骤",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "evidence": "占位",
                "page": None,
            }
        ],
        "argument_flow": [
            {
                "label": "占位论证环节",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "evidence": "占位",
                "page": None,
            }
        ],
        "evidence_and_results": [
            {
                "label": "占位实验结果",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "evidence": "占位",
                "page": None,
            }
        ],
        "conclusions": [
            {
                "label": "占位结论",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "evidence": "占位",
                "page": None,
            }
        ],
        "limitations": [
            {
                "label": "占位局限",
                "detail": "配置 LLM_API_KEY 后由真实模型生成。",
                "kind": "内容",
                "severity": "低",
                "evidence": "占位",
                "page": None,
            }
        ],
        "critique": {
            "data": "占位",
            "experiment": "占位",
            "method": "占位",
            "assumption": "占位",
            "application_boundary": "占位",
            "overall": "这是离线演示模式，未调用大模型。",
        },
        "glossary": [
            {
                "term": "离线演示模式",
                "plain": "它是指程序完全不联网、不调用任何付费的大模型接口，改用提前准备好的本地样例数据把整条流水线（解析、分析、画图、生成网页）原样跑一遍。",
                "analogy": "像新司机先在驾校场地里空车绕圈，不上真路也能检查刹车和方向盘都好使。",
                "why_it_matters": "它让你在没配置 API Key 的情况下也能验证工具本身是否装好、链路是否通畅。",
                "evidence": "占位",
                "page": None,
            }
        ],
        "open_questions": ["配置 API Key 后重新运行以获取真实分析"],
        "reading_path": ["配置 LLM_API_KEY", "重新运行 main.py", "查看生成的 HTML 看板"],
    }
