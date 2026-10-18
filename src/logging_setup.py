"""运行日志落盘：每次解析运行在 demo/log/ 下写一份日志，最多保留 10 份。

由 ``run_pipeline`` / ``render_from_analysis`` 在入口调用（CLI、网页工作台、
批量任务共用同一条路径）。日志只含运行信息（输入路径、阶段、耗时、错误），
不含任何密钥——模型密钥在所有日志输出中始终保持脱敏。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "log"
LOG_KEEP = 10  # 最多保留的日志份数（超出删最旧）
LOG_PREFIX = "run_"


def create_run_log(log_dir: Path | None = None, keep: int = LOG_KEEP) -> Path:
    """新建本次运行的日志文件并剪枝历史日志，返回文件路径。

    文件名带毫秒级时间戳，避免同一秒内多个任务（网页工作台最多 3 并发）
    互相覆盖。
    """
    directory = Path(log_dir) if log_dir else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = directory / f"{LOG_PREFIX}{stamp}.log"
    path.touch()
    _prune(directory, keep)
    return path


def _prune(directory: Path, keep: int) -> None:
    """只保留最新 keep 份日志，删除更旧的；删除失败不影响主流程。"""
    try:
        logs = sorted(directory.glob(f"{LOG_PREFIX}*.log"))
    except OSError:
        return
    for old in logs[:-keep] if len(logs) > keep else []:
        try:
            old.unlink()
        except OSError:
            pass
