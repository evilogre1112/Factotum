from functools import lru_cache
from pathlib import Path
import re
import time
import unicodedata

from factotum.core.vietnamese import is_valid_vietnamese_syllable, normalize_vietnamese_tone

_DANGLING_WORDS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "output" / "dangling_end_words.txt"
_DANGLING_CACHE: tuple[Path, float, float, frozenset[str]] | None = None
_DANGLING_RECHECK_INTERVAL_S = 2.0

SINGLE_CONSONANT_BULLETS = frozenset("bcdfghjklmnpqrstvwxzđ")
CLASS_TOKENS = frozenset({"<num>", "<code>", "<date>", "<roman>"})
_AMBIGUOUS_ROMAN = frozenset({"i", "v", "vi", "x", "xi"})
_ROMAN_PREFIX_WORDS = frozenset({"chương", "mục", "phần", "lục", "bảng", "phụ"})
_UNAMBIGUOUS_ROMAN_RE = re.compile(r"^(?:x{1,3}(?:ix|iv|v?i{0,3})|ix|iv|vi{2,3}|i{2,3})$")
_DECIMAL_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)+$")
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_SHORT_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})$")


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


@lru_cache(maxsize=32768)
def clean_word(w: str) -> str:
    if w in CLASS_TOKENS:
        return w
    w = unicodedata.normalize("NFC", w).lower()
    dec_cand = w.strip(".,;:!?()[]{}\"'/-_")
    if _DECIMAL_NUM_RE.match(dec_cand):
        return dec_cand
    cleaned = re.sub(r"[^\w/-]", "", w).strip("/-_")
    return normalize_vietnamese_tone(cleaned) if cleaned else ""


def is_single_consonant_bullet(w: str) -> bool:
    return len(w) == 1 and w in SINGLE_CONSONANT_BULLETS


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
        if tok.isdigit() or _DECIMAL_NUM_RE.match(tok):
            return ["<num>", "<code>"]
        return ["<code>"]
    if len(tok) >= 2 and _UNAMBIGUOUS_ROMAN_RE.match(tok) and not is_valid_vietnamese_syllable(tok):
        return ["<roman>"]
    if tok in _AMBIGUOUS_ROMAN and prev_tok in _ROMAN_PREFIX_WORDS:
        return ["<roman>"]
    return []


@lru_cache(maxsize=32768)
def get_token_class(tok: str, prev_tok: str = "") -> str:
    """Phân loại hình thái cho token biến thiên (<date>, <num>, <code>, <roman>)."""
    if not tok:
        return ""
    if tok in CLASS_TOKENS:
        return tok
    if any(ch.isdigit() for ch in tok):
        if _DATE_TOKEN_RE.match(tok):
            return "<date>"
        if tok.isdigit() or _DECIMAL_NUM_RE.match(tok):
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


def split_and_clean(text: str) -> list[str]:
    cleaned = [c for temp in text.split() if (c := clean_word(temp))]
    norm, _ = normalize_tokens_with_classes(cleaned)
    return norm
