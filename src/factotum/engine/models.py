from dataclasses import dataclass

from factotum.core.config import DEFAULT_PATHS

OUTPUT_DIR = DEFAULT_PATHS.output_dir
KB_PATH = DEFAULT_PATHS.kb_path
DATA_PATH = DEFAULT_PATHS.data_path
UNACCENTED_MAP_PATH = DEFAULT_PATHS.unaccented_map_path
CACHE_PATH = DEFAULT_PATHS.cache_path
CACHE_VERSION = 6

MAX_SYLLABLE_LEN = 7          # Độ dài tối đa của 1 âm tiết tiếng Việt không dấu
MAX_SPLITS_PER_TOKEN = 8      # Số cách tách tối đa cho 1 token dính liền (vd: "btup")
MAX_QUERY_VARIANTS = 16       # Số tổ hợp biến thể tối đa cho cả câu truy vấn
MAX_CANDIDATES_PER_KEY = 60   # Số từ ứng viên có dấu tối đa cho mỗi khóa (vd: "b")
BEAM_WIDTH = 30               # Độ rộng Beam Search trên đồ thị Markov
MAX_SUPERSTRING_DIFF_K = 3    # Độ lệch tối đa khi tìm cụm con nằm trong chuỗi tối đại cha
MAX_MISSING_GAP = 1           # Cho phép phát hiện thiếu tối đa 1 từ đệm giữa 2 token
MAX_CONTEXT_WINDOW = 4        # Số từ ngữ cảnh chuẩn tối đa xét ở mỗi bên trái/phải
MAX_CTX_CANDIDATES = 12       # Số ứng viên F tối đa đưa vào chấm ngữ cảnh 2 đầu


@dataclass
class QueryMatch:
    phrase: str
    freq: int
    missing_count: int
    superstring_diff: int  # 0: khớp chính xác; >0: trong chuỗi tối đại cha; -1: ghép Markov
    source_phrase: str     # Chuỗi tối đại gốc chứa kết quả (nếu có)
    missing_words: list[str]
    context_tier: int = 0  # 1: 2 đầu A-F-B, 2: 1 đầu, 3: nối qua 1 từ đệm, 4: cạnh 1-gram, 5: đơn lẻ
    context_span_len: int = 0  # Số từ ngữ cảnh chuẩn khớp được quanh F
    markov_prob: float = 0.0   # Xác suất chuỗi Markov P(w1 -> ... -> wn)
    confidence: float = 0.0    # Độ tin cậy tổng hợp [0.0 .. 1.0]


def compute_confidence(m: QueryMatch) -> float:
    """Tính độ tin cậy [0.0 .. 1.0] dựa trên mức khớp KB và xác suất Markov."""
    if m.context_tier == 1:
        return min(0.99, 0.90 + 0.09 * min(1.0, m.freq / 20.0))
    if m.context_tier in (0, 2) and m.superstring_diff == 0 and m.missing_count == 0:
        return min(0.99, 0.88 + 0.11 * min(1.0, m.freq / 20.0))
    if m.context_tier in (2, 3) or (
        m.context_tier == 0 and m.superstring_diff > 0 and m.missing_count == 0
    ):
        if m.context_tier == 3 and m.freq >= 50:
            return min(0.94, 0.88 + 0.06 * min(1.0, m.freq / 200.0))
        return min(0.90, 0.74 + 0.16 * min(1.0, m.freq / 20.0))
    if m.context_tier == 4 and m.superstring_diff >= 0:
        return min(0.82, 0.65 + 0.17 * min(1.0, m.freq / 20.0))
    if m.context_tier >= 5 and m.superstring_diff >= 0:
        return min(0.65, 0.45 + 0.20 * min(1.0, m.freq / 30.0))
    if m.superstring_diff == 0 and m.missing_count >= 1:
        return min(0.76, 0.52 + 0.24 * min(1.0, m.freq / 30.0))
    if m.superstring_diff > 0 and m.missing_count >= 1:
        return min(0.58, 0.38 + 0.20 * min(1.0, m.freq / 30.0))
    if m.markov_prob <= 0.0:
        return 0.10
    n_steps = max(1, len(m.phrase.split()) - 1)
    step_prob = m.markov_prob ** (1.0 / n_steps)
    if step_prob <= 1e-4:
        return 0.10
    if m.missing_count >= 1:
        return min(0.35, 0.12 + 0.23 * (step_prob ** 0.35))
    return min(0.84, 0.35 + 0.55 * (step_prob ** 0.25))


def rank_key(m: QueryMatch) -> tuple[int, int, int, int, float]:
    """Khóa sắp xếp ưu tiên: khớp KB chính xác (Tier 0) -> trong cụm cha -> thiếu từ -> Markov (Tier 5)."""
    if m.superstring_diff == -1:
        match_tier = 5
    elif m.missing_count == 0 and m.superstring_diff == 0:
        match_tier = 0
    elif m.missing_count == 0 and m.superstring_diff > 0 and m.freq >= 10:
        match_tier = 1
    elif m.missing_count == 1 and m.superstring_diff == 0:
        match_tier = 2
    elif m.missing_count == 0 and m.superstring_diff > 0:
        match_tier = 3
    else:
        match_tier = 4

    diff_rank = m.superstring_diff if m.superstring_diff >= 0 else 999
    return (match_tier, m.missing_count, diff_rank, -m.freq, -m.markov_prob)
