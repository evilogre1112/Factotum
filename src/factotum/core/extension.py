from datetime import datetime
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

_DANGLING_WORDS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "output" / "dangling_end_words.txt"
_DANGLING_CACHE: tuple[Path, float, float, frozenset[str]] | None = None
_DANGLING_RECHECK_INTERVAL_S = 2.0


def load_dangling_end_words(path: str | Path | None = None, force_reload: bool = False) -> frozenset[str]:
    """Đọc danh sách từ kết thúc cụt lủn từ file .txt thô (cache trong RAM, tự kiểm tra lại mỗi 2 giây)."""
    global _DANGLING_CACHE, DANGLING_END_WORDS
    target = Path(path) if path is not None else _DANGLING_WORDS_PATH
    now = time.monotonic()
    if (
        not force_reload
        and _DANGLING_CACHE is not None
        and _DANGLING_CACHE[0] == target
        and (now - _DANGLING_CACHE[2]) < _DANGLING_RECHECK_INTERVAL_S
    ):
        return _DANGLING_CACHE[3]
    if not target.is_file():
        return frozenset()
    mtime = target.stat().st_mtime
    if not force_reload and _DANGLING_CACHE is not None and _DANGLING_CACHE[0] == target and _DANGLING_CACHE[1] == mtime:
        _DANGLING_CACHE = (target, mtime, now, _DANGLING_CACHE[3])
        return _DANGLING_CACHE[3]
    words: set[str] = set()
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        clean_line = line.strip().lower()
        if not clean_line:
            continue
        for part in re.split(r"[,;\s]+", clean_line):
            if part:
                words.add(part)
    result = frozenset(words)
    _DANGLING_CACHE = (target, mtime, now, result)
    if path is None:
        DANGLING_END_WORDS = result
    return result


DANGLING_END_WORDS: frozenset[str] = load_dangling_end_words()


def is_dangling_phrase(phrase: str, dangling_words: frozenset[str] | None = None) -> bool:
    """Kiểm tra cụm từ dự đoán có bị kết thúc cụt lủn bởi từ trong file dangling_end_words.txt hay không."""
    tokens = phrase.strip().lower().split()
    if not tokens:
        return False
    banned = load_dangling_end_words() if dangling_words is None else dangling_words
    return tokens[-1] in banned

def init_console() -> None:
    """Bật màu ANSI & UTF-8 cho console Windows (chỉ gọi ở CLI, không chạy khi import thư viện)."""
    if os.name == "nt":
        os.system("")
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


SINGLE_CONSONANT_BULLETS = frozenset("bcdfghjklmnpqrstvwxzđ")  # Ký hiệu mục đơn lẻ trong văn bản luật (a, b, c, đ...)
CLASS_TOKENS = frozenset({"<num>", "<code>", "<date>", "<roman>"})  # Nhãn lớp hình thái chuẩn hóa trong KB & Đồ thị
_AMBIGUOUS_ROMAN = frozenset({"i", "v", "vi", "x", "xi"})
_ROMAN_PREFIX_WORDS = frozenset({"chương", "mục", "phần", "lục", "bảng", "phụ"})
_UNAMBIGUOUS_ROMAN_RE = re.compile(
    r"^(?:x{1,3}(?:ix|iv|v?i{0,3})|ix|iv|vi{2,3}|i{2,3})$"
)

_TONE_SUFFIX_OA_OE = (
    ("oà", "òa"), ("oá", "óa"), ("oả", "ỏa"), ("oã", "õa"), ("oạ", "ọa"),
    ("oè", "òe"), ("oé", "óe"), ("oẻ", "ỏe"), ("oẽ", "õe"), ("oẹ", "ọe"),
)
_TONE_SUFFIX_UY = (
    ("uỳ", "ùy"), ("uý", "úy"), ("uỷ", "ủy"), ("uỹ", "ũy"), ("uỵ", "ụy"),
)


def normalize_vietnamese_tone(w: str) -> str:
    """Chuẩn hóa vị trí dấu thanh kiểu mới ở cuối âm tiết oa/oe/uy (giữ nguyên qu-)."""
    for old_suf, new_suf in _TONE_SUFFIX_OA_OE:
        if w.endswith(old_suf):
            return w[:-2] + new_suf
    for old_suf, new_suf in _TONE_SUFFIX_UY:
        if w.endswith(old_suf) and not w[:-2].endswith("q"):
            return w[:-2] + new_suf
    return w


def clean_word(w: str) -> str:
    if w in CLASS_TOKENS:
        return w
    w = unicodedata.normalize("NFC", w).lower()
    cleaned = re.sub(r"[^\w/-]", "", w).strip("/-_")
    return normalize_vietnamese_tone(cleaned) if cleaned else ""


def is_single_consonant_bullet(w: str) -> bool:
    return len(w) == 1 and w in SINGLE_CONSONANT_BULLETS


_VN_SYLLABLE_RE = re.compile(
    r"^(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnpqrstvx])?(?:[aeiouy]{1,3}|(?=[aeiouy]*[aeiou])[aeiouy]{1,3}(?:ng|nh|ch|[cmnpt]))$"
)
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_SHORT_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})$")


def is_class_token(tok: str) -> bool:
    return tok in CLASS_TOKENS


def get_compatible_token_classes(tok: str, prev_tok: str = "") -> list[str]:
    """Trả về danh sách lớp hình thái tương thích cho 1 token lúc truy vấn (ưu tiên lớp chính trước)."""
    if not tok:
        return []
    if tok in CLASS_TOKENS:
        return [tok]
    if any(ch.isdigit() for ch in tok):
        if _DATE_TOKEN_RE.match(tok):
            return ["<date>", "<code>"]
        m_short = _SHORT_DATE_RE.match(tok)
        if m_short:
            d, m = int(m_short.group(1)), int(m_short.group(2))
            if 1 <= d <= 31 and 1 <= m <= 12:
                return ["<code>", "<date>"]
            return ["<code>"]
        if tok.isdigit():
            return ["<num>", "<code>"]
        return ["<code>"]
    if len(tok) >= 2 and _UNAMBIGUOUS_ROMAN_RE.match(tok) and not is_valid_vietnamese_syllable(tok):
        return ["<roman>"]
    if tok in _AMBIGUOUS_ROMAN and prev_tok in _ROMAN_PREFIX_WORDS:
        return ["<roman>"]
    return []


def get_token_class(tok: str, prev_tok: str = "") -> str:
    """Phân loại hình thái cho token biến thiên (<date>, <num>, <code>, <roman>)."""
    if not tok:
        return ""
    if tok in CLASS_TOKENS:
        return tok
    if any(ch.isdigit() for ch in tok):
        if _DATE_TOKEN_RE.match(tok):
            return "<date>"
        if tok.isdigit():
            return "<num>"
        return "<code>"
    if len(tok) >= 2 and _UNAMBIGUOUS_ROMAN_RE.match(tok) and not is_valid_vietnamese_syllable(tok):
        return "<roman>"
    if tok in _AMBIGUOUS_ROMAN and prev_tok in _ROMAN_PREFIX_WORDS:
        return "<roman>"
    return ""


def normalize_tokens_with_classes(
    tokens: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Chuẩn hóa token số/mã/ngày/La Mã sang nhãn lớp và lưu danh sách giá trị gốc để khôi phục."""
    norm: list[str] = []
    slots: list[tuple[str, str]] = []
    prev = ""
    for tok in tokens:
        cls = get_token_class(tok, prev_tok=prev)
        if cls and tok not in CLASS_TOKENS:
            norm.append(cls)
            slots.append((cls, tok))
            prev = cls
        else:
            norm.append(tok)
            prev = tok
    return norm, slots


def is_valid_vietnamese_syllable(w: str) -> bool:
    """Kiểm tra từ đơn có đúng cấu trúc âm tiết tiếng Việt chuẩn không (loại từ viết tắt thô như ubnd, gt)."""
    if not w or not w.isalpha():
        return False
    ua = remove_accents(w.lower())
    return bool(_VN_SYLLABLE_RE.match(ua))


def split_and_clean(text: str) -> list[str]:
    cleaned = [c for temp in text.split() if (c := clean_word(temp))]
    norm, _ = normalize_tokens_with_classes(cleaned)
    return norm

def remove_accents(text: str) -> str:
    """Bỏ toàn bộ dấu tiếng Việt, chuyển về ASCII không dấu."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


_TELEX_TONE_MAP = {"s": 1, "f": 2, "r": 3, "x": 4, "j": 5}
_VN_VOWEL_TONES: dict[str, tuple[str, ...]] = {
    "a": ("a", "á", "à", "ả", "ã", "ạ"),
    "ă": ("ă", "ắ", "ằ", "ẳ", "ẵ", "ặ"),
    "â": ("â", "ấ", "ầ", "ẩ", "ẫ", "ậ"),
    "e": ("e", "é", "è", "ẻ", "ẽ", "ẹ"),
    "ê": ("ê", "ế", "ề", "ể", "ễ", "ệ"),
    "i": ("i", "í", "ì", "ỉ", "ĩ", "ị"),
    "o": ("o", "ó", "ò", "ỏ", "õ", "ọ"),
    "ô": ("ô", "ố", "ồ", "ổ", "ỗ", "ộ"),
    "ơ": ("ơ", "ớ", "ờ", "ở", "ỡ", "ợ"),
    "u": ("u", "ú", "ù", "ủ", "ũ", "ụ"),
    "ư": ("ư", "ứ", "ừ", "ử", "ữ", "ự"),
    "y": ("y", "ý", "ỳ", "ỷ", "ỹ", "ỵ"),
}
_VN_CHAR_TO_MOD_AND_TONE: dict[str, tuple[str, int]] = {}
for _base_v, _forms in _VN_VOWEL_TONES.items():
    for _t_idx, _ch in enumerate(_forms):
        _VN_CHAR_TO_MOD_AND_TONE[_ch] = (_base_v, _t_idx)

_MODIFIED_VOWELS = frozenset("ăâêôơư")
_ALL_PLAIN_VOWELS = frozenset("aăâeêioôơuưy")


def _apply_vn_tone(base_word: str, tone: int) -> str:
    if tone <= 0 or not base_word:
        return base_word
    v_indices = [i for i, ch in enumerate(base_word) if ch in _ALL_PLAIN_VOWELS]
    if not v_indices:
        return base_word
    if len(v_indices) >= 2:
        if base_word.startswith("qu") and v_indices[0] == 1:
            v_indices = v_indices[1:]
        elif base_word.startswith("gi") and v_indices[0] == 1:
            v_indices = v_indices[1:]
    if not v_indices:
        return base_word

    target_idx = v_indices[0]
    if len(v_indices) == 1:
        target_idx = v_indices[0]
    else:
        mod_pos = [i for i in v_indices if base_word[i] in _MODIFIED_VOWELS]
        if mod_pos:
            target_idx = mod_pos[-1]
        else:
            has_coda = v_indices[-1] < len(base_word) - 1
            if len(v_indices) >= 3:
                target_idx = v_indices[1]
            elif has_coda:
                target_idx = v_indices[1]
            else:
                pair = base_word[v_indices[0]] + base_word[v_indices[1]]
                if pair in ("oa", "oe", "uy"):
                    target_idx = v_indices[1]
                else:
                    target_idx = v_indices[0]

    ch = base_word[target_idx]
    forms = _VN_VOWEL_TONES.get(ch)
    if not forms:
        return base_word
    res = base_word[:target_idx] + forms[tone] + base_word[target_idx + 1 :]
    return normalize_vietnamese_tone(res)


def decode_telex_word(tok: str) -> str:
    """Giải mã token gõ Telex thô chưa bật bộ gõ (vd: 'buowcs' -> 'bước', 'dduowcj' -> 'được')."""
    if not tok or len(tok) < 3 or not tok.isascii() or not tok.isalpha():
        return ""
    s = tok.lower()
    if not any(ch in "aeiouy" for ch in s):
        return ""

    first_v = next((i for i, ch in enumerate(s) if ch in "aeiouy"), -1)
    if first_v == -1:
        return ""

    tone = 0
    chars = list(s)
    for i in range(len(chars) - 1, first_v, -1):
        ch = chars[i]
        if ch in _TELEX_TONE_MAP:
            tone = _TELEX_TONE_MAP[ch]
            del chars[i]
            break

    w = "".join(chars)

    if w.startswith("dd"):
        w = "đ" + w[2:]
    elif w.startswith("d") and w.endswith("d") and len(w) >= 3:
        w = "đ" + w[1:-1]

    if "uow" in w:
        w = w.replace("uow", "ươ", 1)
    elif "uo" in w and "w" in w[w.index("uo") + 2 :]:
        w = w.replace("w", "", 1).replace("uo", "ươ", 1)
    else:
        for pat, rep in (("aw", "ă"), ("ow", "ơ"), ("uw", "ư")):
            if pat in w:
                w = w.replace(pat, rep, 1)
        if "w" in w:
            for base_v, rep in (("u", "ư"), ("o", "ơ"), ("a", "ă")):
                if base_v in w:
                    w = w.replace("w", "", 1).replace(base_v, rep, 1)
                    break

    for pat, base_v, rep in (("aa", "a", "â"), ("ee", "e", "ê"), ("oo", "o", "ô")):
        if pat in w:
            w = w.replace(pat, rep, 1)
        elif w.count(base_v) == 2 and w.endswith(base_v):
            idx = w.index(base_v)
            w = w[:idx] + rep + w[idx + 1 : -1]

    if w == s and tone == 0:
        return ""

    decoded = _apply_vn_tone(w, tone)
    if decoded == s or not is_valid_vietnamese_syllable(decoded):
        return ""
    return decoded


def collapse_repeated_chars(word: str) -> str:
    """Gộp các ký tự lặp liên tiếp bất thường (vd: 'pháppp' -> 'pháp')."""
    if len(word) <= 1:
        return word
    out = [word[0]]
    for ch in word[1:]:
        if ch != out[-1]:
            out.append(ch)
    return "".join(out)


def _damerau_levenshtein(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        dp[i][0] = i
    for j in range(lb + 1):
        dp[0][j] = j
    for i in range(1, la + 1):
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            val = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and ai == b[j - 2] and a[i - 2] == b[j - 1]:
                val = min(val, dp[i - 2][j - 2] + 1)
            dp[i][j] = val
    return dp[la][lb]


def _decompose_vn_features(word: str) -> tuple[str, str, int]:
    mod_chars: list[str] = []
    tone = 0
    for ch in word:
        info = _VN_CHAR_TO_MOD_AND_TONE.get(ch)
        if info is not None:
            mod_chars.append(info[0])
            if info[1] > 0:
                tone = info[1]
        else:
            mod_chars.append(ch)
    return remove_accents(word), "".join(mod_chars), tone


_VN_HOMOPHONE_ONSET_GROUPS = (
    frozenset({"s", "x"}),
    frozenset({"ch", "tr"}),
    frozenset({"d", "gi", "r"}),
    frozenset({"l", "n"}),
    frozenset({"c", "k", "q"}),
    frozenset({"g", "gh"}),
    frozenset({"ng", "ngh"}),
)


def is_vn_orthographic_confusion(word_a: str, word_b: str) -> bool:
    """Kiểm tra 2 từ có phải cặp nhầm chính tả đồng âm tiếng Việt (s/x, ch/tr, d/gi/r, l/n cùng vần+dấu, hoặc hỏi/ngã cùng âm)."""
    if not word_a or not word_b or word_a == word_b:
        return False
    _, mod_a, tone_a = _decompose_vn_features(word_a)
    _, mod_b, tone_b = _decompose_vn_features(word_b)
    if mod_a == mod_b:
        return (tone_a, tone_b) in ((3, 4), (4, 3))
    if tone_a != tone_b:
        return False
    va = next((i for i, c in enumerate(mod_a) if c in _ALL_PLAIN_VOWELS), -1)
    vb = next((i for i, c in enumerate(mod_b) if c in _ALL_PLAIN_VOWELS), -1)
    if va == -1 or vb == -1 or mod_a[va:] != mod_b[vb:]:
        return False
    onset_a, onset_b = mod_a[:va], mod_b[:vb]
    return any(onset_a in g and onset_b in g for g in _VN_HOMOPHONE_ONSET_GROUPS)


def vn_word_similarity(raw_word: str, candidate: str) -> float:
    """Đo độ tương đồng âm tiết tiếng Việt [0.0 .. 1.0] theo khung không dấu, dấu mũ/móc, thanh điệu và phần vần."""
    if not raw_word or not candidate:
        return 0.0
    if raw_word == candidate:
        return 1.0

    raw_c = collapse_repeated_chars(raw_word)
    cand_c = collapse_repeated_chars(candidate)
    if raw_c == cand_c:
        return 0.98

    ua_a, mod_a, tone_a = _decompose_vn_features(raw_c)
    ua_b, mod_b, tone_b = _decompose_vn_features(cand_c)

    max_len = max(len(ua_a), len(ua_b), 1)
    dl_ua = _damerau_levenshtein(ua_a, ua_b)
    if dl_ua > max(2, max_len // 2):
        return 0.0

    sim_ua = max(0.0, 1.0 - dl_ua / max_len)
    dl_mod = _damerau_levenshtein(mod_a, mod_b)
    sim_mod = max(0.0, 1.0 - dl_mod / max(len(mod_a), len(mod_b), 1))

    if tone_a == tone_b:
        sim_tone = 1.0
    elif (tone_a, tone_b) in ((3, 4), (4, 3)):
        sim_tone = 0.75
    elif tone_a == 0:
        sim_tone = 0.45
    else:
        sim_tone = 0.15

    va = next((i for i, c in enumerate(mod_a) if c in _ALL_PLAIN_VOWELS), -1)
    vb = next((i for i, c in enumerate(mod_b) if c in _ALL_PLAIN_VOWELS), -1)
    rime_bonus = 0.0
    if va != -1 and vb != -1 and mod_a[va:] == mod_b[vb:]:
        rime_bonus = 0.18

    score = 0.45 * sim_ua + 0.35 * sim_mod + 0.20 * sim_tone + rime_bonus
    return min(0.97, max(0.0, score))

def get_current_timestamp():
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