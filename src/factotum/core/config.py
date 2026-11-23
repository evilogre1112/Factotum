from dataclasses import dataclass
import logging
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PROJECT_DIR = SRC_DIR.parent


@dataclass(frozen=True)
class FactotumPaths:
    """Quản lý tập trung toàn bộ đường dẫn dữ liệu/đầu ra, tránh hardcode rải rác."""

    output_dir: Path
    data_dir: Path | None = None

    @property
    def log_dir(self) -> Path:
        return self.output_dir / "Log"

    @property
    def pure_log_dir(self) -> Path:
        return self.output_dir / "Pure_Log"

    @property
    def processed_log_dir(self) -> Path:
        return self.output_dir / "Processed_Log"

    @property
    def kb_path(self) -> Path:
        return self.output_dir / "KnowledgeBase.txt"

    @property
    def data_path(self) -> Path:
        return self.output_dir / "Data.txt"

    @property
    def unaccented_map_path(self) -> Path:
        return self.output_dir / "UnaccentedMap.txt"

    @property
    def cache_path(self) -> Path:
        return self.output_dir / "model_cache.pkl"

    @classmethod
    def default(cls) -> "FactotumPaths":
        return cls(
            output_dir=DEFAULT_PROJECT_DIR / "output",
            data_dir=DEFAULT_PROJECT_DIR / "data",
        )

    @classmethod
    def from_output_dir(cls, output_dir: str | Path) -> "FactotumPaths":
        return cls(output_dir=Path(output_dir))


DEFAULT_PATHS = FactotumPaths.default()


def get_logger(name: str = "factotum") -> logging.Logger:
    """Trả về logger chuẩn theo phân cấp `factotum.*` (dùng NullHandler mặc định cho thư viện)."""
    root_logger = logging.getLogger("factotum")
    if not root_logger.handlers:
        root_logger.addHandler(logging.NullHandler())
    return logging.getLogger(name)
