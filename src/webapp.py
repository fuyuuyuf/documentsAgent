"""可选的网页上传界面：浏览器里拖入 PDF，一键生成看板并直接跳转查看。

只用标准库 ``http.server`` 实现，不引入 Flask/FastAPI 等额外依赖，
保证"装完 requirements.txt 就能跑"。

用法：
    python src/main.py --web               # 默认 http://127.0.0.1:8000
    python src/main.py --web --port 8899

安全边界：
  * 默认只监听 127.0.0.1，不对外网暴露；
  * 上传文件大小上限 64 MB，且强制校验 PDF 魔数；
  * 生成结果写入 outputs/ 目录，文件名做白名单清洗，避免路径穿越。
"""

from __future__ import annotations

import email
import email.policy
import json
import re
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from .config import PROJECT_ROOT, Settings
from .llm import LLMError
from .pipeline import Logger, run_pipeline
from .render import RenderError
from .textutils import truncate

UPLOAD_DIR = PROJECT_ROOT / "outputs" / "_uploads"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_SESSION_STATE: dict[str, dict] = {}
_STATE_LOCK = threading.Lock()
# 限制同时占用模型与渲染资源的任务数，也限制等待队列占用的内存。
MAX_PARALLEL_JOBS = 3
MAX_PENDING_JOBS = 12
_WORK_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL_JOBS)
_PENDING_SLOTS = threading.BoundedSemaphore(MAX_PENDING_JOBS)
UPLOAD_PAGE = PROJECT_ROOT / "assets" / "templates" / "upload.html"

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _safe_filename(name: str, fallback: str = "paper.pdf") -> str:
    """把上传的文件名清洗成安全的 basename，防路径穿越。"""
    base = Path(unquote(name or "")).name
    base = _SAFE_NAME_RE.sub("_", base).strip("._-")
    if not base:
        return fallback
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:120]


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>文档解析智能体 · 上传论文</title>
<style>
  :root {{ --bg:#f5f6fa; --panel:#fff; --border:#e2e5ee; --text:#1f2430; --dim:#6b7280; --accent:#4f46e5;
           --sans:"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
          color:var(--text); font-family:var(--sans); padding:24px; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:32px 36px;
           width:min(680px,100%); box-shadow:0 8px 30px rgba(16,24,40,.08); }}
  h1 {{ margin:0 0 6px; font-size:21px; }}
  p.sub {{ margin:0 0 22px; color:var(--dim); font-size:14px; }}
  .drop {{ border:2px dashed var(--border); border-radius:12px; padding:38px 20px; text-align:center;
           cursor:pointer; transition:.18s; background:#fafbfd; }}
  .drop:hover, .drop.over {{ border-color:var(--accent); background:#eef2ff; }}
  .drop strong {{ display:block; font-size:16px; margin-bottom:6px; }}
  .drop span {{ color:var(--dim); font-size:13px; }}
  input[type=file] {{ display:none; }}
  .row {{ margin-top:18px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  button {{ font-family:inherit; font-size:14px; padding:9px 18px; border-radius:9px; cursor:pointer;
            border:1px solid var(--border); background:#fafbfd; }}
  button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }}
  button:disabled {{ opacity:.5; cursor:not-allowed; }}
  .fname {{ color:var(--dim); font-size:13px; }}
  pre.log {{ margin-top:18px; background:#1c1f27; color:#d8dee9; padding:14px; border-radius:10px;
             font-size:12px; max-height:260px; overflow:auto; white-space:pre-wrap; display:none; }}
  .msg {{ margin-top:16px; padding:12px 14px; border-radius:10px; font-size:14px; display:none; }}
  .msg.ok {{ background:#ecfdf5; border:1px solid #6ee7b7; }}
  .msg.err {{ background:#fef2f2; border:1px solid #fca5a5; }}
  label.chk {{ font-size:13px; color:var(--dim); display:flex; align-items:center; gap:6px; }}
</style></head>
<body>
<div class="card">
  <h1>论文 → 交互式思维导图看板</h1>
  <p class="sub">拖入一篇 PDF 论文，自动完成文本提取、结构化分析、导图生成与看板渲染。</p>
  <form id="form" method="post" action="/generate" enctype="multipart/form-data">
    <label class="drop" id="drop">
      <strong>把 PDF 拖到这里，或点击选择文件</strong>
      <span>支持文字版 PDF；扫描件需要先做 OCR　·　上限 64 MB</span>
      <input type="file" id="file" name="pdf" accept="application/pdf,.pdf"/>
    </label>
    <div class="row">
      <button type="submit" class="primary" id="submit">开始生成</button>
      <span class="fname" id="fname">未选择文件</span>
    </div>
    <div class="row">
      <label class="chk"><input type="checkbox" name="mock" value="1"/> 离线演示模式（不调用大模型，不产生费用）</label>
      <label class="chk"><input type="checkbox" name="keep_debug" value="1"/> 导出中间产物</label>
    </div>
    <pre class="log" id="log"></pre>
  </form>
  <div class="msg" id="msg"></div>
</div>
<script>
(function () {{
  'use strict';
  var drop = document.getElementById('drop');
  var input = document.getElementById('file');
  var fname = document.getElementById('fname');
  var form = document.getElementById('form');
  var submit = document.getElementById('submit');
  var msg = document.getElementById('msg');
  var log = document.getElementById('log');

  function show(el) {{ el.style.display = 'block'; }}
  function setName() {{
    fname.textContent = input.files && input.files.length ? input.files[0].name : '未选择文件';
  }}
  input.addEventListener('change', setName);
  ['dragenter', 'dragover'].forEach(function (evt) {{
    drop.addEventListener(evt, function (e) {{ e.preventDefault(); drop.classList.add('over'); }});
  }});
  ['dragleave', 'drop'].forEach(function (evt) {{
    drop.addEventListener(evt, function (e) {{ e.preventDefault(); drop.classList.remove('over'); }});
  }});
  drop.addEventListener('drop', function (e) {{
    if (e.dataTransfer && e.dataTransfer.files.length) {{
      input.files = e.dataTransfer.files;
      setName();
    }}
  }});

  form.addEventListener('submit', function (e) {{
    e.preventDefault();
    if (!input.files || !input.files.length) {{
      msg.className = 'msg err'; msg.textContent = '请先选择一篇 PDF 论文。'; show(msg);
      return;
    }}
    submit.disabled = true;
    submit.textContent = '生成中…（长论文可能需要 1-3 分钟）';
    msg.style.display = 'none';
    log.textContent = '正在上传并解析，请稍候…\\n';
    show(log);

    var data = new FormData(form);
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/generate', true);
    xhr.onload = function () {{
      submit.disabled = false;
      submit.textContent = '开始生成';
      var res = null;
      try {{ res = JSON.parse(xhr.responseText); }} catch (err) {{ res = null; }}
      if (xhr.status === 200 && res && res.ok) {{
        log.textContent += res.log || '';
        msg.className = 'msg ok';
        msg.innerHTML = '生成成功，正在打开看板：<a href="' + res.url + '" target="_blank">' + res.html + '</a>';
        show(msg);
        window.open(res.url, '_blank');
      }} else {{
        log.textContent += (res && res.log) || xhr.responseText || '';
        msg.className = 'msg err';
        msg.textContent = (res && res.error) ? res.error : ('生成失败（HTTP ' + xhr.status + '）');
        show(msg);
      }}
    }};
    xhr.onerror = function () {{
      submit.disabled = false;
      submit.textContent = '开始生成';
      msg.className = 'msg err';
      msg.textContent = '网络错误：无法连接到本地服务，请确认终端里的进程还在运行。';
      show(msg);
    }};
    xhr.send(data);
  }});
}})();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "DocParserAgent/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 工具
    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_html(self, status: int, markup: str) -> None:
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:  # 收敛默认的逐请求日志
        if self.server.verbose:  # type: ignore[attr-defined]
            sys.stderr.write("  [web] %s\n" % (fmt % args))

    # ------------------------------------------------------------ 路由
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_html(200, UPLOAD_PAGE.read_text(encoding="utf-8"))
            return
        if path == "/health":
            self._send_json(200, {"ok": True, "version": "1.0.0"})
            return
        if path == "/api/config":
            settings = Settings.from_env()
            self._send_json(200, {"model": settings.model, "configured": not settings.validate()})
            return
        if path == "/api/history":
            output_dir = PROJECT_ROOT / "outputs"
            items = sorted(output_dir.glob("*.dashboard.html"), key=lambda item: item.stat().st_mtime, reverse=True)
            self._send_json(200, {"items": [
                {"name": item.name, "url": "/view/" + quote(item.name), "size": item.stat().st_size,
                 "modified": int(item.stat().st_mtime)} for item in items[:12]
            ]})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/")
            with _STATE_LOCK:
                state = _SESSION_STATE.get(job_id)
                snapshot = dict(state) if state else None
            self._send_json(200 if snapshot else 404, snapshot or {"error": "任务不存在或已过期"})
            return
        if path.startswith("/view/"):
            self._serve_output(unquote(path[len("/view/") :]))
            return
        self._send_html(404, "<h1>404</h1><p>页面不存在。<a href='/'>返回首页</a></p>")

    def _serve_output(self, name: str) -> None:
        target = (PROJECT_ROOT / "outputs" / Path(name).name).resolve()
        outputs_root = (PROJECT_ROOT / "outputs").resolve()
        if outputs_root not in target.parents and target.parent != outputs_root:
            self._send_html(403, "<h1>403</h1><p>非法路径。</p>")
            return
        if not target.exists() or target.suffix.lower() != ".html":
            self._send_html(404, "<h1>404</h1><p>看板文件不存在，可能已被移动。</p>")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/generate":
            self._send_json(404, {"ok": False, "error": "未知接口"})
            return
        try:
            self._handle_generate()
        except Exception as exc:  # noqa: BLE001 - 任何异常都要回给浏览器而不是断连
            if self.server.verbose:  # type: ignore[attr-defined]
                traceback.print_exc()
            self._send_json(500, {"ok": False, "error": f"服务端异常：{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------ 生成
    def _handle_generate(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(400, {"ok": False, "error": "请求格式不正确，需要 multipart/form-data 上传。"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "请求体为空。"})
            return
        if length > MAX_UPLOAD_BYTES + 4096:
            self._send_json(
                413,
                {"ok": False, "error": f"文件过大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）。"},
            )
            return

        # Python 3.13 起标准库移除了 cgi 模块，因此用 email 解析 multipart，
        # 好处是零额外依赖（不引入 multipart / starlette 等）。
        raw_body = self.rfile.read(length)
        try:
            fields = _parse_multipart(content_type, raw_body)
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": f"解析上传内容失败：{exc}"})
            return

        upload = fields.get("pdf")
        if not upload or not upload.get("filename"):
            self._send_json(400, {"ok": False, "error": "没有收到 PDF 文件，请重新选择。"})
            return

        data = upload.get("data") or b""
        if not data:
            self._send_json(400, {"ok": False, "error": "上传的文件是空的。"})
            return
        if not data.startswith(b"%PDF"):
            self._send_json(400, {"ok": False, "error": "这不是一个有效的 PDF 文件（缺少 %PDF 文件头）。"})
            return

        use_mock = _truthy(fields, "mock")
        keep_debug = _truthy(fields, "keep_debug")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(upload["filename"])
        if not _PENDING_SLOTS.acquire(blocking=False):
            self._send_json(429, {"ok": False, "error": "待处理任务已满，请稍后再试。"})
            return

        job_id = uuid.uuid4().hex
        upload_path = UPLOAD_DIR / job_id / safe_name
        try:
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            upload_path.write_bytes(data)
        except Exception:
            _PENDING_SLOTS.release()
            raise

        settings = Settings.from_env()
        settings.mock = use_mock or settings.mock
        settings.keep_debug = keep_debug
        if settings.mock:
            sample = PROJECT_ROOT / "examples" / (Path(safe_name).stem + ".analysis.json")
            if sample.exists():
                settings.mock_file = str(sample)
        with _STATE_LOCK:
            _SESSION_STATE[job_id] = {
                "id": job_id, "status": "running", "step": "文件已接收，等待处理",
                "log": "", "name": safe_name, "mode": "mock" if settings.mock else "api",
                "started": int(time.time()),
            }
            # 仅保留最近的任务状态，避免长时间运行后内存持续增长。
            for old_id in list(_SESSION_STATE)[:-20]:
                if _SESSION_STATE[old_id]["status"] != "running":
                    del _SESSION_STATE[old_id]
        try:
            threading.Thread(target=_run_job, args=(job_id, upload_path, settings), daemon=True).start()
        except Exception:
            with _STATE_LOCK:
                _SESSION_STATE.pop(job_id, None)
            _PENDING_SLOTS.release()
            raise
        self._send_json(202, {"ok": True, "id": job_id})


class _JobLog:
    """把流水线日志写入任务状态，供本地页面轮询。"""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def write(self, chunk: str) -> None:
        with _STATE_LOCK:
            state = _SESSION_STATE.get(self.job_id)
            if state is None:
                return
            state["log"] = (state["log"] + chunk)[-12000:]
            if chunk.startswith("[step ]"):
                state["step"] = chunk.removeprefix("[step ]").strip()

    def flush(self) -> None:
        pass


def _run_job(job_id: str, upload_path: Path, settings: Settings) -> None:
    """后台处理上传任务；失败信息只返回可操作摘要。"""
    logger = Logger("info", stream=_JobLog(job_id))
    try:
        with _WORK_SLOTS:
            with _STATE_LOCK:
                source_name = _SESSION_STATE[job_id]["name"]
                _SESSION_STATE[job_id]["step"] = "开始解析论文"
            output_name = f"{Path(source_name).stem}_{time.strftime('%Y%m%d-%H%M%S')}_{job_id[:4]}.dashboard.html"
            result = run_pipeline(
                upload_path, PROJECT_ROOT / "outputs" / output_name,
                settings=settings, logger=logger,
            )
        stats = result.payload.get("stats", {})
        with _STATE_LOCK:
            _SESSION_STATE[job_id].update({
                "status": "done", "step": "看板已生成", "url": "/view/" + quote(result.html_path.name),
                "output": result.html_path.name, "stats": stats,
                "page_count": result.paper.page_count,
                "elapsed": round(result.seconds, 1),
            })
    except (LLMError, RenderError, FileNotFoundError, ValueError) as exc:
        with _STATE_LOCK:
            _SESSION_STATE[job_id].update({"status": "error", "step": "处理失败", "error": truncate(str(exc), 240)})
    except Exception as exc:  # noqa: BLE001 - 后台线程必须把失败状态交还页面
        with _STATE_LOCK:
            _SESSION_STATE[job_id].update({
                "status": "error", "step": "处理失败",
                "error": f"程序发生异常：{type(exc).__name__}: {truncate(str(exc), 180)}",
            })
    finally:
        logger.close_log("任务结束")     # 运行日志收尾（成功/失败均已写入文件）
        _PENDING_SLOTS.release()


def _parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """用 email 模块解析 multipart/form-data。

    返回 ``{字段名: {"filename": str|None, "data": bytes, "value": str}}``。
    只处理普通表单字段与文件字段，够这个上传页使用。
    """
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = email.message_from_bytes(header + body, policy=email.policy.default)
    if not message.is_multipart():
        raise ValueError("请求不是 multipart 内容")

    fields: dict[str, dict] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            fields[name] = {"filename": filename, "data": payload, "value": ""}
        else:
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, "replace")
            except LookupError:
                text = payload.decode("utf-8", "replace")
            fields[name] = {"filename": None, "data": payload, "value": text.strip()}
    return fields


def _truthy(fields: dict, key: str) -> bool:
    entry = fields.get(key)
    if not entry:
        return False
    return str(entry.get("value", "")).lower() in {"1", "true", "yes", "on"}


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    settings = Settings.from_env()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.verbose = settings.verbose  # type: ignore[attr-defined]

    url = f"http://{host}:{port}/"
    print()
    print("─" * 68)
    print("  网页上传界面已启动")
    print(f"  地址：{url}")
    print(f"  输出目录：{PROJECT_ROOT / 'outputs'}")
    if not settings.mock and settings.validate():
        print("  提示：尚未配置 API Key，网页上请勾选「离线演示模式」，或先在 .env 中配置。")
    print("  按 Ctrl+C 停止服务")
    print("─" * 68)

    if open_browser:
        threading.Timer(0.8, lambda: _safe_open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[停止] 已关闭网页服务。")
    finally:
        httpd.server_close()
    return 0


def _safe_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 无图形环境时静默跳过
        pass
