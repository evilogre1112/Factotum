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
    is_fallback: bool = False  # True: câu bị cụt lủn / không đoán được nên trả lại nguyên văn câu gốc


def evidence_tier(m: QueryMatch) -> int:
    """
    Phân hạng bằng chứng thống nhất toàn hệ thống (thấp hơn = ưu tiên cao hơn):
      0: Khớp KB chính xác (không thiếu từ) hoặc khớp ngữ cảnh 2 đầu
      1: Nằm trong cụm KB cha tần suất mạnh (freq >= 10)
      2: Chèn 1 từ thiếu tạo cụm KB chính xác
      3: Nằm trong cụm KB cha tần suất thấp (không thiếu từ)
      4: Chèn từ thiếu nằm trong cụm KB cha
      5: Suy luận Markov thuần (không có cụm KB nguyên vẹn)
      6: Fallback trả về nguyên văn câu gốc
    """
    if m.is_fallback:
        return 6
    if m.superstring_diff == -1:
        return 5
    if m.missing_count == 0:
        if m.superstring_diff == 0:
            return 0
        return 1 if m.freq >= 10 else 3
    return 2 if (m.missing_count == 1 and m.superstring_diff == 0) else 4


def compute_confidence(m: QueryMatch) -> float:
    """Tính độ tin cậy [0.0 .. 1.0] nhất quán theo hạng bằng chứng KB và xác suất Markov."""
    if m.is_fallback:
        return 0.0
    if m.context_tier == 1:
        return min(0.99, 0.90 + 0.09 * min(1.0, m.freq / 20.0))
    if m.context_tier in (0, 2) and m.superstring_diff == 0 and m.missing_count == 0:
        return min(0.99, 0.88 + 0.11 * min(1.0, m.freq / 20.0))
    if m.context_tier in (2, 3) or (m.context_tier == 0 and m.superstring_diff > 0 and m.missing_count == 0):
        if m.context_tier == 3 and m.freq >= 50:
            return min(0.94, 0.88 + 0.06 * min(1.0, m.freq / 200.0))
        return min(0.90, 0.74 + 0.16 * min(1.0, m.freq / 20.0))
    if m.superstring_diff >= 0:
        if m.missing_count == 0:
            base, span, scale, cap = (0.65, 0.17, 20.0, 0.82) if m.context_tier == 4 else (0.45, 0.20, 30.0, 0.65)
        else:
            base, span, scale, cap = (0.52, 0.24, 30.0, 0.76) if m.superstring_diff == 0 else (0.38, 0.20, 30.0, 0.58)
        return min(cap, base + span * min(1.0, m.freq / scale))

    # Nhánh Markov thuần (superstring_diff == -1)
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
    """Khóa xếp hạng thống nhất cho ứng viên trong pha tìm kiếm (Hạng 0 -> Hạng 6)."""
    tier = evidence_tier(m)
    diff_rank = m.superstring_diff if m.superstring_diff >= 0 else 999
    return (tier, m.missing_count, diff_rank, -m.freq, -m.markov_prob)


def finalize_matches(
    matches: list[QueryMatch],
    top_k: int | None = None,
    *,
    preserve_kb_order: bool = False,
    sort_markov_by_conf: bool = True,
) -> list[QueryMatch]:
    """
    Tính `confidence` và sắp xếp thống nhất trước khi đưa sang tầng hậu kiểm:
    - Mọi ứng viên có bằng chứng KB (`superstring_diff >= 0`) luôn đứng trên nhánh Markov (`-1`).
    - Trong nhánh Markov (`-1`), ưu tiên không thiếu từ trước rồi giảm dần theo `confidence` và `markov_prob`.
    """
    for m in matches:
        if m.confidence <= 0.0 and not m.is_fallback:
            m.confidence = compute_confidence(m)

    kb_part = [m for m in matches if m.superstring_diff >= 0 and not m.is_fallback]
    mk_part = [m for m in matches if m.superstring_diff < 0 and not m.is_fallback]
    fb_part = [m for m in matches if m.is_fallback]

    if not preserve_kb_order:
        kb_part.sort(key=rank_key)
    if sort_markov_by_conf:
        mk_part.sort(key=lambda m: (m.missing_count, -round(m.confidence, 4), -m.markov_prob, -m.freq))
    else:
        mk_part.sort(key=rank_key)

    ordered = kb_part + mk_part + fb_part
    return ordered[:top_k] if top_k is not None else ordered
