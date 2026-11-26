import re
import unicodedata

_TONE_SUFFIX_OA_OE = (
    ("oà", "òa"), ("oá", "óa"), ("oả", "ỏa"), ("oã", "õa"), ("oạ", "ọa"),
    ("oè", "òe"), ("oé", "óe"), ("oẻ", "ỏe"), ("oẽ", "õe"), ("oẹ", "ọe"),
)
_TONE_SUFFIX_UY = (
    ("uỳ", "ùy"), ("uý", "úy"), ("uỷ", "ủy"), ("uỹ", "ũy"), ("uỵ", "ụy"),
)

_VN_SYLLABLE_RE = re.compile(
    r"^(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnpqrstvx])?(?:[aeiouy]{1,3}|(?=[aeiouy]*[aeiou])[aeiouy]{1,3}(?:ng|nh|ch|[cmnpt]))$"
)

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

_VN_HOMOPHONE_ONSET_GROUPS = (
    frozenset({"s", "x"}),
    frozenset({"ch", "tr"}),
    frozenset({"d", "gi", "r"}),
    frozenset({"l", "n"}),
    frozenset({"c", "k", "q"}),
    frozenset({"g", "gh"}),
    frozenset({"ng", "ngh"}),
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


def remove_accents(text: str) -> str:
    """Bỏ toàn bộ dấu tiếng Việt, chuyển về ASCII không dấu."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def is_valid_vietnamese_syllable(w: str) -> bool:
    """Kiểm tra từ đơn có đúng cấu trúc âm tiết tiếng Việt chuẩn không (loại từ viết tắt thô như ubnd, gt)."""
    if not w or not w.isalpha():
        return False
    ua = remove_accents(w.lower())
    return bool(_VN_SYLLABLE_RE.match(ua))


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
    if len(v_indices) > 1:
        mod_pos = [i for i in v_indices if base_word[i] in _MODIFIED_VOWELS]
        if mod_pos:
            target_idx = mod_pos[-1]
        else:
            has_coda = v_indices[-1] < len(base_word) - 1
            if len(v_indices) >= 3 or has_coda:
                target_idx = v_indices[1]
            else:
                pair = base_word[v_indices[0]] + base_word[v_indices[1]]
                target_idx = v_indices[1] if pair in ("oa", "oe", "uy") else v_indices[0]

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


def token_similarity(word_a: str, word_b: str) -> float:
    """Đo độ tương đồng chữ cái trung lập ngôn ngữ [0.0 .. 1.0] dựa trên Damerau-Levenshtein."""
    if not word_a or not word_b:
        return 0.0
    if word_a == word_b:
        return 1.0
    ua_a = remove_accents(collapse_repeated_chars(word_a))
    ua_b = remove_accents(collapse_repeated_chars(word_b))
    if ua_a == ua_b:
        return 0.98 if word_a != word_b else 1.0
    max_len = max(len(ua_a), len(ua_b), 1)
    dl = _damerau_levenshtein(ua_a, ua_b)
    if dl > max(2, max_len // 2):
        return 0.0
    return max(0.0, 1.0 - dl / max_len)


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
    rime_bonus = 0.18 if (va != -1 and vb != -1 and mod_a[va:] == mod_b[vb:]) else 0.0

    score = 0.45 * sim_ua + 0.35 * sim_mod + 0.20 * sim_tone + rime_bonus
    return min(0.97, max(0.0, score))
