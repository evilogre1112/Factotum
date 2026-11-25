from datetime import datetime
import os
import sys
import time


def init_console() -> None:
    """Bật màu ANSI & UTF-8 cho console Windows (chỉ gọi ở CLI, không chạy khi import thư viện)."""
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def get_current_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def print_progress(
    current: int,
    total: int,
    start_time: float,
    label: str = "Tiến độ",
    unit: str = "mục",
    bar_width: int = 24,
) -> None:
    if total <= 0:
        return
    ratio = min(1.0, current / total)
    filled = int(bar_width * ratio)
    done = current >= total

    bar_color = "\033[92m" if done else "\033[96m"
    dim_color = "\033[90m"
    yellow = "\033[93m"
    bold = "\033[1m"
    reset = "\033[0m"

    bar = f"{bar_color}{'█' * filled}{dim_color}{'░' * (bar_width - filled)}{reset}"
    elapsed = max(1e-6, time.perf_counter() - start_time)
    speed = current / elapsed
    eta = (total - current) / speed if speed > 0 else 0.0

    line = (
        f"\r{bold}{label}{reset} [{bar}] {bar_color}{ratio * 100:5.1f}%{reset} "
        f"| {current:,}/{total:,} {unit} "
        f"| {yellow}{speed:,.0f} {unit}/s{reset} "
        f"| ETA: {bold}{format_duration(eta)}{reset}   "
    )
    sys.stdout.write(line)
    if done:
        sys.stdout.write("\n")
    sys.stdout.flush()
