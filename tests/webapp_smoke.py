"""本地网页入口冒烟：上传 PDF、轮询进度并打开生成的看板。"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.webapp import Handler  # noqa: E402


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.verbose = False  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    started: dict | None = None
    state: dict | None = None
    parallel_ids: list[str] = []

    def read(path: str, *, body: bytes | None = None, content_type: str = "") -> tuple[int, bytes]:
        headers = {"Content-Type": content_type} if content_type else {}
        request = Request(origin + path, data=body, headers=headers)
        with urlopen(request, timeout=15) as response:
            return response.status, response.read()

    try:
        _, page = read("/")
        assert b'id="uploadForm"' in page and b'id="history"' in page
        _, config_raw = read("/api/config")
        config = json.loads(config_raw)
        assert "model" in config and "configured" in config
        assert "api_key" not in config and "key" not in config
        _, history_raw = read("/api/history")
        assert isinstance(json.loads(history_raw)["items"], list)

        pdf = (PROJECT_ROOT / "examples" / "sample_en_two_column.pdf").read_bytes()
        boundary = "----document-agent-smoke"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="mock"\r\n\r\n1\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="pdf"; filename="sample_en_two_column.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + pdf + f"\r\n--{boundary}--\r\n".encode()
        status, started_raw = read("/generate", body=body, content_type=f"multipart/form-data; boundary={boundary}")
        started = json.loads(started_raw)
        assert status == 202 and started["ok"] and started["id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, state_raw = read("/api/jobs/" + started["id"])
            state = json.loads(state_raw)
            if state["status"] != "running":
                break
            time.sleep(0.2)
        else:
            raise AssertionError("网页任务超时")

        assert state["status"] == "done", state.get("error") or state.get("log", "")[-400:]
        # 断言"全部证据都溯源命中"，但不写死数字：改样例 PDF / 分析 JSON 时不该碎
        assert state["stats"]["verifiedCount"] == state["stats"]["evidenceCount"] > 0, state["stats"]
        assert state["stats"]["unverifiedCount"] == 0, state["stats"]
        import pymupdf

        with pymupdf.open(PROJECT_ROOT / "examples" / "sample_en_two_column.pdf") as doc:
            assert state["page_count"] == doc.page_count
        assert state["url"].startswith("/view/")
        _, dashboard = read(state["url"])
        assert b'id="payload"' in dashboard and len(dashboard) > 2_000_000

        # 两份上传必须真正同时进入流水线，且各自取得独立任务状态。
        barrier = threading.Barrier(2, timeout=8)

        def fake_pipeline(upload_path, output_path, *, settings, logger):
            barrier.wait()
            return SimpleNamespace(
                payload={"stats": {"nodeCount": 1}},
                html_path=output_path,
                paper=SimpleNamespace(page_count=1),
                seconds=0.1,
            )

        with patch("src.webapp.run_pipeline", side_effect=fake_pipeline):
            for _ in range(2):
                status, raw = read("/generate", body=body, content_type=f"multipart/form-data; boundary={boundary}")
                submitted = json.loads(raw)
                assert status == 202 and submitted["ok"]
                parallel_ids.append(submitted["id"])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                states = [json.loads(read("/api/jobs/" + job_id)[1]) for job_id in parallel_ids]
                if all(item["status"] != "running" for item in states):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("并行任务超时")
            assert all(item["status"] == "done" for item in states), states
            assert len({item["output"] for item in states}) == 2
        print("网页上传、并行任务、任务进度、最近看板接口及离线产物验证通过。")
        return 0
    finally:
        server.shutdown()
        server.server_close()
        for job_id in ([started["id"]] if started and started.get("id") else []) + parallel_ids:
            uploads_root = (PROJECT_ROOT / "outputs" / "_uploads").resolve()
            upload_dir = (uploads_root / job_id).resolve()
            if upload_dir.parent == uploads_root and upload_dir.exists():
                shutil.rmtree(upload_dir)
        if state and state.get("status") == "done":
            outputs_root = (PROJECT_ROOT / "outputs").resolve()
            output = (outputs_root / state["output"]).resolve()
            if output.parent == outputs_root and output.name.startswith("sample_en_two_column_"):
                output.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
