from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
import time

from factotum.builder.extractor import extract_phrases_batch
from factotum.core.config import DEFAULT_PATHS, FactotumPaths, get_logger
from factotum.core.doc_reader import read_single_file
from factotum.engine.models import MAX_SUPERSTRING_DIFF_K, QueryMatch
from factotum.engine.query_engine import GraphQueryEngine

logger = get_logger("factotum.sdk")


def _read_folder_texts(folder: str | Path) -> list[str]:
    folder_path = Path(folder)
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục: {folder_path}")

    texts: list[str] = []
    for p in sorted(folder_path.iterdir()):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf == ".txt":
            content = p.read_text(encoding="utf-8", errors="ignore").strip()
        elif suf in (".doc", ".docx"):
            content = read_single_file(p).strip()
        else:
            continue
        if content:
            texts.append(content)
    return texts


def _extract_aggregated_phrases(texts: Iterable[str]) -> dict[str, int]:
    """Trích xuất song song đa nhân và gom tần suất cụm lõi (bỏ qua bản trùng >= 99% Jaccard)."""
    valid_texts = [t for t in texts if isinstance(t, str) and t.strip()]
    if not valid_texts:
        return {}

    aggregated: dict[str, int] = defaultdict(int)
    seen_doc_sets: list[set[str]] = []

    for results in extract_phrases_batch(valid_texts):
        if not results:
            continue
        pset = {r.text for r in results}
        is_dup = False
        for prev_set in seen_doc_sets:
            if abs(len(pset) - len(prev_set)) / max(len(pset), len(prev_set)) < 0.02:
                if len(pset & prev_set) / len(pset | prev_set) >= 0.99:
                    is_dup = True
                    break
        if is_dup:
            continue
        seen_doc_sets.append(pset)
        for r in results:
            aggregated[r.text] += r.freq

    return aggregated


class Factotum:
    """Mô hình Factotum: khôi phục dấu, viết tắt, thiếu từ, giải mã Telex và sửa chính tả tiếng Việt."""

    def __init__(self, engine: GraphQueryEngine | None = None) -> None:
        self.engine: GraphQueryEngine = engine if engine is not None else GraphQueryEngine()

    def fit(self, texts: Iterable[str]) -> "Factotum":
        """Huấn luyện mới hoàn toàn trong RAM từ danh sách văn bản (không ghi file tạm)."""
        self.engine = GraphQueryEngine()
        info = self.update(texts)
        if info["added"] == 0:
            raise ValueError("Không trích xuất được cụm lặp nào từ dữ liệu đầu vào.")
        return self

    def fit_folder(self, folder: str | Path) -> "Factotum":
        """Huấn luyện mới từ toàn bộ file .txt, .doc, .docx trong thư mục."""
        return self.fit(_read_folder_texts(folder))

    def update(self, texts: Iterable[str]) -> dict[str, int]:
        """Cập nhật tăng dần (incremental update) văn bản mới vào mô hình hiện có mà không build lại từ đầu."""
        t0 = time.perf_counter()
        phrase_counts = _extract_aggregated_phrases(texts)
        if not phrase_counts:
            return {"added": 0, "updated": 0}
        info = self.engine.add_phrases(phrase_counts)
        logger.info(
            "Incremental update: +%d cụm mới, cập nhật %d cụm (%.3fs)",
            info["added"],
            info["updated"],
            time.perf_counter() - t0,
        )
        return info

    def update_folder(self, folder: str | Path) -> dict[str, int]:
        """Cập nhật tăng dần toàn bộ file .txt, .doc, .docx trong thư mục vào mô hình hiện có."""
        return self.update(_read_folder_texts(folder))

    def save(self, model_path: str | Path) -> Path:
        """Xuất mô hình thành 1 file `.ftm` thuần dữ liệu (JSON + zlib, an toàn khi chia sẻ)."""
        return self.engine.save_standalone(model_path)

    @classmethod
    def load(cls, model_path: str | Path, allow_pickle: bool = False) -> "Factotum":
        """Nạp mô hình từ file `.ftm` an toàn (chỉ cho phép `.pkl` cục bộ khi `allow_pickle=True`)."""
        engine = GraphQueryEngine()
        engine.load_standalone(model_path, allow_pickle=allow_pickle)
        return cls(engine=engine)

    @classmethod
    def from_output_dir(
        cls,
        output_dir: str | Path | None = None,
        show_progress: bool = False,
    ) -> "Factotum":
        """Nạp mô hình từ thư mục chứa KnowledgeBase.txt, Data.txt, UnaccentedMap.txt."""
        paths = (
            FactotumPaths.from_output_dir(output_dir)
            if output_dir is not None
            else DEFAULT_PATHS
        )
        engine = GraphQueryEngine()
        engine.load(
            kb_path=paths.kb_path,
            data_path=paths.data_path,
            unaccented_path=paths.unaccented_map_path,
            show_progress=show_progress,
            use_cache=True,
            cache_path=paths.cache_path,
        )
        return cls(engine=engine)

    def predict(
        self,
        query: str,
        top_k: int = 5,
        allow_missing: bool = True,
        max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    ) -> list[QueryMatch]:
        """Trả về danh sách top-K kết quả khôi phục kèm điểm tin cậy."""
        return self.engine.query(
            query, top_k=top_k, allow_missing=allow_missing, max_diff_k=max_diff_k
        )

    def restore(self, query: str, allow_missing: bool = True) -> str:
        """Trả về trực tiếp chuỗi Top-1 đã khôi phục (hoặc giữ nguyên `query` nếu không khớp)."""
        matches = self.engine.query(query, top_k=1, allow_missing=allow_missing)
        return matches[0].phrase if matches else query

    def stats(self) -> dict[str, int]:
        """Thống kê quy mô đồ thị tri thức đang nạp trong RAM."""
        return {
            "phrases": len(self.engine.phrases),
            "vocab": len(self.engine.real_vocab),
            "unaccented_keys": len(self.engine.unaccented_map),
            "edges": sum(len(nbrs) for nbrs in self.engine.fwd_graph.values()),
        }
