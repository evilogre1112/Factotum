from dataclasses import dataclass, field

from factotum.core.config import DEFAULT_PATHS
from factotum.core.extension import is_valid_vietnamese_syllable, remove_accents

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


@dataclass(frozen=True)
class EditSpan:
    """Mô tả một đoạn [start, end) trong câu đáp án mà Worker đã sửa, bung viết tắt hoặc chèn thêm."""

    orig: str   # Token gốc trước khi sửa ("" nếu là từ thiếu được chèn thêm)
    start: int  # Chỉ số từ bắt đầu (inclusive) trong `phrase.split()`
    end: int    # Chỉ số từ kết thúc (exclusive) trong `phrase.split()`
    kind: str   # "acronym" | "unaccented" | "typo" | "missing_insert" | "segment"


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
    edits: list[EditSpan] = field(default_factory=list)  # Danh sách các đoạn Worker đã sửa/bung gửi sang Reviewer


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


def build_edit_spans(
    orig_tokens: list[str],
    words: list[str],
    *,
    offset: int = 0,
    missing_words: list[str] | None = None,
    include_unaccented_unchanged: bool = False,
) -> list[EditSpan]:
    """
    Tính danh sách các đoạn `EditSpan` đã sửa/bung/chèn giữa `orig_tokens` và `words` (tại độ lệch `offset`).
    Các từ neo giữ nguyên không thay đổi sẽ không nằm trong danh sách này; nếu `include_unaccented_unchanged=True`,
    các từ không dấu nằm trong vùng chưa chuẩn hóa (dù Worker tạm giữ nguyên thanh ngang) vẫn được gửi sang Reviewer.
    """
    n_orig = len(orig_tokens)
    n_words = len(words)
    if n_words == 0:
        return []
    if n_orig == 0:
        return [EditSpan(orig="", start=offset, end=offset + n_words, kind="missing_insert")]

    if n_orig == n_words:
        edits: list[EditSpan] = []
        for i, (orig, w) in enumerate(zip(orig_tokens, words)):
            if orig == w:
                if include_unaccented_unchanged and orig == remove_accents(orig) and orig.isalpha():
                    edits.append(EditSpan(orig=orig, start=offset + i, end=offset + i + 1, kind="unaccented"))
                continue
            ua_orig, ua_w = remove_accents(orig), remove_accents(w)
            if ua_orig == ua_w:
                kind = "unaccented"
            elif len(ua_orig) == 1 and ua_orig.isalpha() and ua_w.startswith(ua_orig):
                kind = "abbrev"
            else:
                kind = "typo"
            edits.append(EditSpan(orig=orig, start=offset + i, end=offset + i + 1, kind=kind))
        return edits

    if n_words < n_orig:
        return [EditSpan(orig=" ".join(orig_tokens), start=offset, end=offset + n_words, kind="segment")]

    miss_counts: dict[str, int] = {}
    for mw in (missing_words or ()):
        miss_counts[mw] = miss_counts.get(mw, 0) + 1
    total_missing = sum(miss_counts.values())

    memo: dict[tuple[int, int, int], list[EditSpan] | None] = {}

    def _dp(ti: int, wi: int, rem_miss: int) -> list[EditSpan] | None:
        if ti == n_orig and wi == n_words:
            return []
        if wi == n_words:
            return None
        if ti == n_orig:
            if rem_miss >= (n_words - wi):
                return [
                    EditSpan(orig="", start=offset + k, end=offset + k + 1, kind="missing_insert")
                    for k in range(wi, n_words)
                ]
            return None
        if (n_words - wi) < (n_orig - ti):
            return None
        key = (ti, wi, rem_miss)
        if key in memo:
            return memo[key]

        tok = orig_tokens[ti]
        ua = remove_accents(tok)

        # 1. Bung viết tắt (len >= 2, không phải âm tiết tiếng Việt chuẩn) khớp chữ cái đầu
        if len(ua) >= 2 and ua.isalpha() and not is_valid_vietnamese_syllable(ua) and wi + len(ua) <= n_words:
            if all(
                (rw := remove_accents(words[wi + k])) and rw[0] == ua[k]
                for k in range(len(ua))
            ):
                rest = _dp(ti + 1, wi + len(ua), rem_miss)
                if rest is not None:
                    res = [EditSpan(orig=tok, start=offset + wi, end=offset + wi + len(ua), kind="acronym")] + rest
                    memo[key] = res
                    return res

        # 2. Khớp 1-1 chính xác hoặc cùng gốc không dấu
        if tok == words[wi] or ua == remove_accents(words[wi]):
            rest1 = _dp(ti + 1, wi + 1, rem_miss)
            if rest1 is not None:
                if tok == words[wi]:
                    if include_unaccented_unchanged and tok == ua and tok.isalpha():
                        res = [EditSpan(orig=tok, start=offset + wi, end=offset + wi + 1, kind="unaccented")] + rest1
                    else:
                        res = rest1
                else:
                    res = [EditSpan(orig=tok, start=offset + wi, end=offset + wi + 1, kind="unaccented")] + rest1
                memo[key] = res
                return res

        # 3. Chèn từ thiếu tại vị trí wi (ưu tiên trước khớp typo 1-1 để tránh lệch vị trí chèn)
        if rem_miss > 0 and words[wi] in miss_counts:
            rest_m = _dp(ti, wi + 1, rem_miss - 1)
            if rest_m is not None:
                res = [EditSpan(orig="", start=offset + wi, end=offset + wi + 1, kind="missing_insert")] + rest_m
                memo[key] = res
                return res

        # 4. Khớp 1-1 dạng viết tắt 1 chữ cái (vd 't' -> 'tiền') hoặc sửa lỗi chính tả (typo)
        rest_typo = _dp(ti + 1, wi + 1, rem_miss)
        if rest_typo is not None:
            ua_w = remove_accents(words[wi])
            kind = "abbrev" if (len(ua) == 1 and ua.isalpha() and ua_w.startswith(ua)) else "typo"
            res = [EditSpan(orig=tok, start=offset + wi, end=offset + wi + 1, kind=kind)] + rest_typo
            memo[key] = res
            return res

        memo[key] = None
        return None

    aligned = _dp(0, 0, total_missing)
    if aligned is not None:
        return aligned
    return [EditSpan(orig=" ".join(orig_tokens), start=offset, end=offset + n_words, kind="segment")]
