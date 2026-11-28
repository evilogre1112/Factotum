from array import array
from collections import defaultdict
import math
from pathlib import Path
import sys
import unicodedata

_MAX_COL_PID_CACHE = 2048
_NFD_TONE_MARKS = frozenset(("\u0300", "\u0301", "\u0309", "\u0303", "\u0323"))

from factotum.builder.make_tree import generate_unaccented_keys
from factotum.core.extension import (
    get_token_class,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine import graph_io
from factotum.engine.graph_io import FTM_MAGIC
from factotum.engine.mdis import MDISStore
from factotum.engine.models import (
    DATA_PATH,
    KB_PATH,
    MAX_SUPERSTRING_DIFF_K,
    UNACCENTED_MAP_PATH,
    QueryMatch,
    compute_confidence,
)


class GraphIndex:
    def __init__(self) -> None:
        self._reset_state()

    def _reset_state(self) -> None:
        self.dict_freq: dict[str, int] = {}                                # Cụm KB -> tần suất xuất hiện
        self.word_freq: dict[str, int] = defaultdict(int)                  # Từ đơn -> tần suất lớn nhất trong các cụm chứa nó
        self.real_vocab: set[str] = set()                                  # Tập từ đơn có thật trong KB
        self.phrases: list[tuple[str, tuple[str, ...], int]] = []          # (chuỗi cụm, tuple từ interned, tần suất)
        self.word_to_phrases: dict[str, set[int]] = defaultdict(set)       # Từ đơn -> tập ID cụm chứa từ đó
        self.col_id_to_pids: dict[int, set[int]] = {}                      # Cache lười id(list) -> tập ID cụm
        self.col_id_to_set: dict[int, set[str]] = {}                       # id(list ứng viên) -> set ứng viên
        self.col_id_to_initial: dict[int, str] = {}                        # id(list ứng viên) -> chữ cái đầu không dấu
        self.initial_pair_to_pids: dict[str, array] = {}                   # Cặp 2 chữ cái đầu liền kề -> array('I')
        self.len_to_phrases: dict[int, set[int]] = defaultdict(set)        # Độ dài cụm (số từ) -> tập ID cụm
        self.len_range_cache: dict[tuple[int, int], set[int]] = {}
        self.fwd_graph: dict[str, dict[str, int]] = {}                     # Đồ thị cạnh xuôi u -> {v: freq}
        self.rev_graph: dict[str, dict[str, int]] = {}                     # Đồ thị cạnh ngược v -> {u: freq}
        self.fwd_prob: dict[str, dict[str, float]] = {}                    # Xác suất Markov P(v | u)
        self.fwd_prob_top: dict[str, list[tuple[str, float]]] = {}         # Top 25 từ kế tiếp có xác suất cao nhất
        self.unaccented_map: dict[str, list[str]] = {}                     # Khóa không dấu/viết tắt -> danh sách từ có dấu
        self.ua_phrase_to_pids: dict[str, int | tuple[int, ...]] = {}      # Cụm không dấu -> pid (int) hoặc tuple[pid]
        self.acronym_to_pids: dict[str, int | tuple[int, ...]] = {}        # Mã viết tắt chữ cái đầu -> pid (int) hoặc tuple[pid]
        self.loaded_from_cache: bool = False
        self._max_key_len_cache: tuple[int, int] = (0, 7)
        self._mdis_phrase_score_cache: dict[tuple[str, int], tuple[int, int, str]] = {}
        self._kb_3gram_cache: dict[tuple[str, str, str], bool] = {}
        self.mdis: MDISStore = MDISStore()
        self._mdis_ready: bool = False

    @property
    def max_vocab_word_len(self) -> int:
        """Độ dài khóa không dấu lớn nhất trong từ điển."""
        n_keys = len(self.unaccented_map)
        if n_keys == 0:
            return 7
        if self._max_key_len_cache[0] == n_keys:
            return self._max_key_len_cache[1]
        val = max((len(k) for k in self.unaccented_map), default=7)
        self._max_key_len_cache = (n_keys, max(4, val))
        return self._max_key_len_cache[1]

    @property
    def strong_kb_freq(self) -> int:
        """Ngưỡng tần suất cụm KB mạnh theo quy mô kho."""
        return max(1, round(2.0 * math.log10(max(len(self.phrases), 1))))

    @property
    def medium_kb_freq(self) -> int:
        """Ngưỡng tần suất cụm KB trung bình theo quy mô kho."""
        return max(1, self.strong_kb_freq // 2)

    def markov_log_prob(self, words: list[str]) -> float:
        """Tính tổng log-xác suất tự nhiên chuỗi Markov bậc 1 trên danh sách từ `words` (không bị tràn về 0)."""
        if not words:
            return float("-inf")
        log_p = math.log(max(self.dict_freq.get(words[0], 1), 1) / 100000.0)
        fwd_prob = self.fwd_prob
        log_backoff = math.log(1e-6)
        for i in range(len(words) - 1):
            nbrs = fwd_prob.get(words[i])
            p = nbrs.get(words[i + 1]) if nbrs else None
            log_p += math.log(p) if (p is not None and p > 0.0) else log_backoff
        return log_p

    def markov_prob(self, words: list[str]) -> float:
        """Tính xác suất chuỗi Markov bậc 1 trên danh sách từ `words`."""
        log_p = self.markov_log_prob(words)
        return math.exp(log_p) if log_p > -700.0 else 0.0

    def step_prob(self, words: list[str]) -> float:
        """Tính xác suất chuẩn hóa theo độ dài G(W) = exp(ln P(W) / |W|) (chống tràn về 0 trên câu dài)."""
        if not words:
            return 0.0
        return math.exp(self.markov_log_prob(words) / len(words))

    def count_broken_edges(self, words: list[str]) -> int:
        """Đếm số cạnh đứt (tần suất chuyển tiếp = 0) trên chuỗi `words`."""
        fwd = self.fwd_graph
        return sum(1 for i in range(len(words) - 1) if fwd.get(words[i], {}).get(words[i + 1], 0) == 0)

    @staticmethod
    def within_distortion_bound(ua_a: str, ua_b: str, sim: float, *, is_known_anchor: bool) -> bool:
        """Kiểm tra độ biến dạng c(a|b) = 1 - sim <= 1/max(|a|,|b|) và < 1/2 (neo nghi vấn) hoặc <= 1/2 (từ lỗi)."""
        dist = 1.0 - sim
        if is_known_anchor:
            max_l = max(len(ua_a), len(ua_b), 1)
            return dist < 0.5 - 1e-9 and dist <= (1.0 / max_l) + 1e-9
        return dist <= 0.5 + 1e-9

    @staticmethod
    def is_subsequence_expansion(ua_short: str, ua_long: str) -> bool:
        """Kiểm tra `ua_short` có bảo toàn 100% thứ tự ký tự đã gõ bên trong `ua_long` (phép bung ký tự rớt)."""
        if len(ua_short) >= len(ua_long):
            return False
        it = iter(ua_long)
        return all(ch in it for ch in ua_short)

    @staticmethod
    def nfd_token_distortion(raw_tok: str, cand_w: str) -> tuple[float, int]:
        """Đo độ biến dạng ký tự/dấu trên không gian phân rã Unicode NFD: (c_NFD, số dấu thanh chênh lệch)."""
        if raw_tok == cand_w:
            return (0.0, 0)
        nfd_a = unicodedata.normalize("NFD", raw_tok.lower()).replace("đ", "d\u0335")
        nfd_b = unicodedata.normalize("NFD", cand_w.lower()).replace("đ", "d\u0335")
        max_l = max(len(nfd_a), len(nfd_b), 1)
        if remove_accents(raw_tok) == remove_accents(cand_w):
            c_nfd = abs(len(nfd_b) - len(nfd_a)) / max_l
        else:
            it = iter(nfd_b)
            sub_hits = sum(1 for ch in nfd_a if ch in it)
            c_nfd = (max_l - sub_hits) / max_l
        tones_a = sum(1 for ch in nfd_a if ch in _NFD_TONE_MARKS)
        tones_b = sum(1 for ch in nfd_b if ch in _NFD_TONE_MARKS)
        return (round(c_nfd, 4), abs(tones_b - tones_a))

    def score_kb_bridge(
        self,
        words: list[str],
        gap_i: int,
        *,
        max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
        require_tail_on_subphrase: bool = False,
    ) -> tuple[int, tuple[int, int] | None]:
        """Đánh giá cầu nối từ thiếu tại `gap_i` bằng phép giao bung u_R(X) ∩ u_L(Y) trên KB."""
        n_w = len(words)
        if gap_i <= 0 or gap_i >= n_w - 1:
            return 0, None
        dict_freq = self.dict_freq
        strong_f = self.strong_kb_freq
        max_kb_l = max(self.len_to_phrases, default=6)

        for wlen in range(min(n_w, max_kb_l), 3, -1):
            s_lo = max(0, gap_i + 2 - wlen)
            s_hi = min(gap_i, n_w - wlen + 1)
            for s_i in range(s_lo, s_hi):
                f_w = dict_freq.get(" ".join(words[s_i : s_i + wlen]), 0)
                if f_w > 0:
                    return f_w, (s_i, s_i + wlen)

        f_c, d_c, _ = self.lookup_real_phrase_score(
            words[gap_i - 1 : gap_i + 2], max_diff_k=max_diff_k
        )
        if f_c > 0 and d_c >= 0:
            if gap_i >= 2:
                f_left = dict_freq.get(" ".join(words[gap_i - 2 : gap_i + 1]), 0)
                if f_left >= strong_f and (not require_tail_on_subphrase or d_c == 0 or gap_i + 2 == n_w):
                    return max(f_left, f_c), (gap_i - 2, gap_i + 2)
            if gap_i + 2 < n_w:
                f_right = dict_freq.get(" ".join(words[gap_i : gap_i + 3]), 0)
                f_pair_l = dict_freq.get(f"{words[gap_i - 1]} {words[gap_i]}", 0)
                if (
                    f_right >= strong_f
                    and f_pair_l >= strong_f
                    and (not require_tail_on_subphrase or d_c == 0 or gap_i == 1)
                ):
                    return max(f_right, f_c), (gap_i - 1, gap_i + 3)
        return 0, None

    def build_kb_horizons(self, tokens: list[str]) -> list[tuple[int, int]]:
        """Phân đoạn chuỗi token thành các khoảng giao cụm KB liên tiếp [st, ed)."""
        n = len(tokens)
        if n == 0:
            return []

        ua_map = self.unaccented_map
        real_vocab = self.real_vocab

        def _tok_pids(tok: str) -> set[int] | tuple[int, ...]:
            ua = remove_accents(tok)
            cands = ua_map.get(tok) or ua_map.get(ua) or ([tok] if tok in real_vocab else [])
            if cands:
                return self.get_col_pids(cands)
            return self.get_acronym_pids(ua)

        raw_segs: list[list[int]] = []
        pos = 0
        while pos < n:
            first_pids = _tok_pids(tokens[pos])
            k = pos + 1
            if first_pids:
                cur: set[int] | None = None
                while k < n:
                    nxt = _tok_pids(tokens[k])
                    if not nxt:
                        break
                    base = first_pids if cur is None else cur
                    inter = set(base).intersection(nxt) if not isinstance(base, set) else base.intersection(nxt)
                    if inter:
                        cur = inter
                        k += 1
                    else:
                        break
            raw_segs.append([pos, k])
            pos = k

        merged: list[list[int]] = []
        for st, ed in raw_segs:
            if merged and ((ed - st) == 1 or (merged[-1][1] - merged[-1][0]) == 1):
                merged[-1][1] = ed
            else:
                merged.append([st, ed])
        return [(st, ed) for st, ed in merged]

    @staticmethod
    def _record_class_edge(fwd_graph: dict[str, dict[str, int]], u: str, v: str, freq: int) -> None:
        """Ghi nhận cạnh trừu tượng (<num>, <code>, <date>, <roman>) song song với cạnh thật để làm fallback OOV."""
        cls_v = get_token_class(v)
        if cls_v and cls_v != v:
            u_nbrs = fwd_graph.setdefault(u, {})
            u_nbrs[cls_v] = u_nbrs.get(cls_v, 0) + freq
            if cls_v in ("<code>", "<date>"):
                u_nbrs["<num>"] = u_nbrs.get("<num>", 0) + freq
        cls_u = get_token_class(u)
        if cls_u and cls_u != u:
            c_nbrs = fwd_graph.setdefault(cls_u, {})
            c_nbrs[v] = c_nbrs.get(v, 0) + freq
            if cls_u in ("<code>", "<date>"):
                n_nbrs = fwd_graph.setdefault("<num>", {})
                n_nbrs[v] = n_nbrs.get(v, 0) + freq

    def _augment_all_class_edges(self) -> None:
        """Bổ sung toàn bộ cạnh lớp hình thái (<NUM>, <CODE>, <DATE>) từ các cạnh thật trong `fwd_graph`."""
        real_edges = [
            (u, v, w)
            for u, nbrs in self.fwd_graph.items()
            if not u.startswith("<")
            for v, w in nbrs.items()
            if not v.startswith("<")
        ]
        for u, v, w in real_edges:
            self._record_class_edge(self.fwd_graph, u, v, w)

    @staticmethod
    def _filter_unaccented_candidates(key: str, candidates: list[str]) -> list[str]:
        ua_key = remove_accents(key)
        if len(key) >= 2 and key.isalpha():
            if not is_valid_vietnamese_syllable(key):
                return [v for v in candidates if v != remove_accents(v) or is_valid_vietnamese_syllable(v)]
            if key == ua_key:
                exact_syll = [v for v in candidates if remove_accents(v) == ua_key]
                if exact_syll:
                    return exact_syll
        return candidates

    def _rebuild_col_id_indexes(self) -> None:
        self.col_id_to_pids.clear()
        self.col_id_to_set.clear()
        self.col_id_to_initial.clear()
        self._mdis_phrase_score_cache.clear()
        self._kb_3gram_cache.clear()
        self._mdis_ready = False
        for key, candidates in self.unaccented_map.items():
            cid = id(candidates)
            self.col_id_to_set[cid] = set(candidates)
            ua_k = remove_accents(key)
            self.col_id_to_initial[cid] = ua_k[0] if ua_k else ""

    def get_col_pids(self, col: list[str]) -> set[int]:
        """Tra cứu lười tập PID cho một cột ứng viên, có cache giới hạn dung lượng."""
        cid = id(col)
        pids = self.col_id_to_pids.get(cid)
        if pids is not None:
            return pids
        if len(col) == 1:
            return self.word_to_phrases.get(col[0], set())
        pids = set()
        w2p = self.word_to_phrases
        for w in col:
            pids.update(w2p.get(w, ()))
        if cid in self.col_id_to_set:
            if len(self.col_id_to_pids) >= _MAX_COL_PID_CACHE:
                self.col_id_to_pids.pop(next(iter(self.col_id_to_pids)))
            self.col_id_to_pids[cid] = pids
        return pids

    def get_ua_pids(self, key: str) -> tuple[int, ...] | None:
        val = self.ua_phrase_to_pids.get(key)
        if val is None:
            return None
        return (val,) if isinstance(val, int) else val

    def get_acronym_pids(self, key: str) -> tuple[int, ...] | None:
        val = self.acronym_to_pids.get(key)
        if val is None:
            return None
        return (val,) if isinstance(val, int) else val

    def save_standalone(self, model_path: str | Path) -> Path:
        """Lưu mô hình thành 1 file `.ftm` thuần dữ liệu (JSON + zlib)."""
        return graph_io.save_standalone(self, model_path)

    def load_standalone(self, model_path: str | Path, allow_pickle: bool = False) -> None:
        """Nạp mô hình từ file `.ftm` (JSON + zlib), hoặc `.pkl` khi bật `allow_pickle=True`."""
        graph_io.load_standalone(self, model_path, allow_pickle=allow_pickle)
        self.ensure_mdis()

    def load(
        self,
        kb_path: Path = KB_PATH,
        data_path: Path = DATA_PATH,
        unaccented_path: Path = UNACCENTED_MAP_PATH,
        show_progress: bool = True,
        use_cache: bool = True,
        cache_path: Path | None = None,
    ) -> None:
        """Nạp đồ thị từ bộ file văn bản KB / data.txt / UM (có hỗ trợ cache cục bộ)."""
        graph_io.load_from_files(
            self,
            kb_path=kb_path,
            data_path=data_path,
            unaccented_path=unaccented_path,
            show_progress=show_progress,
            use_cache=use_cache,
            cache_path=cache_path,
        )
        self.ensure_mdis()

    def get_len_range_pids(self, min_len: int, max_len: int) -> set[int]:
        key = (min_len, max_len)
        cached = self.len_range_cache.get(key)
        if cached is not None:
            return cached
        res: set[int] = set()
        for length in range(min_len, max_len + 1):
            res.update(self.len_to_phrases.get(length, ()))
        self.len_range_cache[key] = res
        return res

    def has_kb_3gram(self, a: str, b: str, c: str) -> bool:
        """Kiểm tra nhanh 3 từ (a, b, c) có cùng xuất hiện trong ít nhất 1 cụm KB hay không (có cache, không tạo set tạm)."""
        key = (a, b, c)
        cached = self._kb_3gram_cache.get(key)
        if cached is not None:
            return cached
        if self.dict_freq.get(f"{a} {b} {c}", 0) > 0:
            res = True
        else:
            w2p = self.word_to_phrases
            p0 = w2p.get(a)
            if not p0:
                res = False
            else:
                p1 = w2p.get(b)
                p2 = w2p.get(c)
                if not p1 or not p2:
                    res = False
                else:
                    # Duyệt từ tập nhỏ nhất và thoát sớm ngay khi gặp PID chung đầu tiên (không cấp phát set trung gian)
                    l0, l1, l2 = len(p0), len(p1), len(p2)
                    if l0 <= l1 and l0 <= l2:
                        s_min, s_a, s_b = p0, p1, p2
                    elif l1 <= l0 and l1 <= l2:
                        s_min, s_a, s_b = p1, p0, p2
                    else:
                        s_min, s_a, s_b = p2, p0, p1
                    res = any((pid in s_a and pid in s_b) for pid in s_min)
        if len(self._kb_3gram_cache) >= 65536:
            self._kb_3gram_cache.clear()
        self._kb_3gram_cache[key] = res
        return res

    def has_mdis_3gram(self, a: str, b: str, c: str) -> bool:
        """Kiểm tra [b, c] có là tiền tố có thứ tự liên tiếp trên một chiều ngữ cảnh + của thực thể gốc `a` (theo bất biến MDIS)."""
        if self.dict_freq.get(f"{a} {b} {c}", 0) > 0:
            return True
        return self.lookup_real_phrase_score([a, b, c])[0] > 0

    def mdis_acronym_extensions(
        self, orig_acr: str, left_anchor: str = "", right_anchor: str = ""
    ) -> list[list[str]]:
        """
        Truy vấn tập chiều mở rộng viết tắt `orig_acr` theo cấu trúc MDIS:
        - Chiều + từ thực thể gốc trái `left_anchor`: `left_anchor -> [X_1, ..., X_k]`.
        - Chiều - vào thực thể gốc phải `right_anchor`: `[X_1, ..., X_k] <- right_anchor`.
        - Chiều độc lập không neo: `[X_1, ..., X_k]`.
        """
        ua_orig = remove_accents(orig_acr)
        span_len = len(ua_orig)
        if span_len < 2:
            return []
        phrases = self.phrases
        dict_freq = self.dict_freq
        seen: set[tuple[str, ...]] = set()
        anchored_cands: list[tuple[int, list[str]]] = []
        standalone_cands: list[tuple[int, list[str]]] = []

        if span_len >= 3 and left_anchor and not left_anchor.startswith("<"):
            ua_l = remove_accents(left_anchor)
            if ua_l:
                for pid in self.get_acronym_pids(ua_l[0] + ua_orig) or ():
                    _, p_words, p_freq = phrases[pid]
                    if len(p_words) == span_len + 1 and p_words[0] == left_anchor:
                        tail = tuple(p_words[1:])
                        if tail not in seen:
                            seen.add(tail)
                            anchored_cands.append((p_freq, list(tail)))

        if span_len >= 3 and right_anchor and not right_anchor.startswith("<"):
            ua_r = remove_accents(right_anchor)
            if ua_r:
                for pid in self.get_acronym_pids(ua_orig + ua_r[0]) or ():
                    _, p_words, p_freq = phrases[pid]
                    if len(p_words) == span_len + 1 and p_words[-1] == right_anchor:
                        head = tuple(p_words[:-1])
                        if head not in seen:
                            seen.add(head)
                            anchored_cands.append((p_freq, list(head)))

        for pid in self.get_acronym_pids(ua_orig) or ():
            p_str, p_words, p_freq = phrases[pid]
            pw_tup = tuple(p_words)
            if len(pw_tup) == span_len and pw_tup not in seen:
                seen.add(pw_tup)
                standalone_cands.append((dict_freq.get(p_str, p_freq), list(pw_tup)))

        anchored_cands.sort(key=lambda x: -x[0])
        standalone_cands.sort(key=lambda x: -x[0])
        return [w for _, w in anchored_cands] + [w for _, w in standalone_cands]

    def ensure_mdis(self) -> MDISStore:
        """Dựng lười cấu trúc vật lý MDIS (`entities`, `dims +/-`, `events` đối ứng, tọa độ 64-bit) từ `self.phrases`."""
        if not self._mdis_ready:
            self.mdis.build_from_phrases(self.phrases)
            self._mdis_ready = True
        return self.mdis

    def save_mdis(self, path: str | Path) -> Path:
        """Xuất cấu trúc vật lý MDIS ra file tri thức `.mdis`."""
        return self.ensure_mdis().save_file(path)

    def load_mdis(self, path: str | Path) -> None:
        """Nạp cấu trúc vật lý MDIS từ file tri thức `.mdis`."""
        self.mdis.load_file(path)
        self._mdis_ready = True

    def export_mdis_txt(self, path: str | Path) -> Path:
        """Xuất cấu trúc vật lý MDIS ra file văn bản `.txt` dễ đọc."""
        return self.ensure_mdis().export_text(path)

    def lookup_real_phrase_score(
        self, words: list[str], max_diff_k: int = MAX_SUPERSTRING_DIFF_K
    ) -> tuple[int, int, str]:
        """Kiểm tra chuỗi `words` có khớp chính xác hoặc nằm trong chiều ngữ cảnh MDIS của KB không."""
        phrase = " ".join(words)
        exact_freq = self.dict_freq.get(phrase)
        if exact_freq is not None:
            return exact_freq, 0, phrase

        cache_key = (phrase, max_diff_k)
        cached = self._mdis_phrase_score_cache.get(cache_key)
        if cached is not None:
            return cached

        fwd = self.fwd_graph
        for i in range(len(words) - 1):
            nbrs = fwd.get(words[i])
            if not nbrs or words[i + 1] not in nbrs:
                res = (0, -1, "")
                if len(self._mdis_phrase_score_cache) < 16384:
                    self._mdis_phrase_score_cache[cache_key] = res
                return res

        res = self.ensure_mdis().lookup_phrase_score(words, self.phrases, max_diff_k)
        if len(self._mdis_phrase_score_cache) < 16384:
            self._mdis_phrase_score_cache[cache_key] = res
        return res

    def filter_contiguous_pids(
        self, cand_pids: set[int], window: list[str], max_diff_k: int
    ) -> tuple[set[int], int, int, str]:
        """Lọc tập `cand_pids` giữ lại các cụm chứa `window` liên tiếp và trả về ứng viên tốt nhất."""
        target_len = len(window)
        padded_target = f" {' '.join(window)} "
        matched_pids: set[int] = set()
        best_freq = 0
        best_diff = max_diff_k + 1
        best_src = ""
        phrases = self.phrases

        for pid in cand_pids:
            p_str, p_words, p_freq = phrases[pid]
            diff = len(p_words) - target_len
            if diff < 0:
                continue
            if padded_target in f" {p_str} ":
                matched_pids.add(pid)
                if diff <= max_diff_k and (
                    diff < best_diff or (diff == best_diff and p_freq > best_freq)
                ):
                    best_diff = diff
                    best_freq = p_freq
                    best_src = p_str

        if best_freq > 0:
            return matched_pids, best_freq, best_diff, best_src
        return matched_pids, 0, -1, ""

    def add_phrases(self, phrase_counts: dict[str, int]) -> dict[str, int]:
        """
        Cập nhật tăng dần (incremental update) các cụm mới vào chỉ mục RAM hiện có
        mà không cần xây dựng lại toàn bộ đồ thị.
        """
        if not phrase_counts:
            return {"added": 0, "updated": 0}

        phrase_to_pid = {p_str: idx for idx, (p_str, _, _) in enumerate(self.phrases)}
        touched_fwd: set[str] = set()
        touched_ua_phrases: set[str] = set()
        touched_acronyms: set[str] = set()
        touched_um_keys: set[str] = set()
        added = 0
        updated = 0

        for raw_phrase, delta in phrase_counts.items():
            phrase = raw_phrase.strip().lower()
            if not phrase or delta <= 0:
                continue
            words = phrase.split()
            n_w = len(words)
            existing_pid = phrase_to_pid.get(phrase)

            if existing_pid is not None:
                old_str, old_words, old_freq = self.phrases[existing_pid]
                new_freq = old_freq + delta
                self.phrases[existing_pid] = (old_str, old_words, new_freq)
                self.dict_freq[phrase] = new_freq
                pid = existing_pid
                updated += 1
            else:
                new_freq = delta
                pid = len(self.phrases)
                self.phrases.append((phrase, words, new_freq))
                phrase_to_pid[phrase] = pid
                self.dict_freq[phrase] = new_freq
                self.len_to_phrases[n_w].add(pid)
                if self._mdis_ready:
                    self.mdis.add_phrase(pid, words, new_freq)
                added += 1

            ua_words: list[str] = []
            initials: list[str] = []
            for w in words:
                is_new_word = w not in self.real_vocab
                self.real_vocab.add(w)
                self.word_to_phrases[w].add(pid)
                if self.word_freq[w] < new_freq:
                    self.word_freq[w] = new_freq
                ua = remove_accents(w)
                ua_words.append(ua)
                initials.append(ua[0] if ua else "")

                # Cập nhật UnaccentedMap cho từ mới hoặc từ tăng tần suất
                for key in generate_unaccented_keys(w):
                    if len(key) >= 2 and key.isalpha():
                        if not is_valid_vietnamese_syllable(key):
                            if not (w != ua or is_valid_vietnamese_syllable(w)):
                                continue
                        elif key == remove_accents(key) and remove_accents(w) != key:
                            continue
                    cands = self.unaccented_map.get(key)
                    if cands is None:
                        self.unaccented_map[key] = [w]
                        touched_um_keys.add(key)
                    elif w not in cands:
                        cands.append(w)
                        touched_um_keys.add(key)
                    elif is_new_word or existing_pid is None:
                        touched_um_keys.add(key)

            if n_w >= 2:
                ua_p = " ".join(ua_words)
                cur_ua = self.ua_phrase_to_pids.get(ua_p)
                if cur_ua is None:
                    self.ua_phrase_to_pids[ua_p] = pid
                elif isinstance(cur_ua, int):
                    if cur_ua != pid:
                        self.ua_phrase_to_pids[ua_p] = (cur_ua, pid)
                        touched_ua_phrases.add(ua_p)
                elif pid not in cur_ua:
                    self.ua_phrase_to_pids[ua_p] = (*cur_ua, pid)
                    touched_ua_phrases.add(ua_p)
                else:
                    touched_ua_phrases.add(ua_p)

                acr = "".join(initials)
                if len(acr) == n_w:
                    cur_acr = self.acronym_to_pids.get(acr)
                    if cur_acr is None:
                        self.acronym_to_pids[acr] = pid
                    elif isinstance(cur_acr, int):
                        if cur_acr != pid:
                            self.acronym_to_pids[acr] = (cur_acr, pid)
                            touched_acronyms.add(acr)
                    elif pid not in cur_acr:
                        self.acronym_to_pids[acr] = (*cur_acr, pid)
                        touched_acronyms.add(acr)
                    else:
                        touched_acronyms.add(acr)

            for i in range(n_w - 1):
                u_w, v_w = words[i], words[i + 1]
                u_nbrs = self.fwd_graph.setdefault(u_w, {})
                old_w = u_nbrs.get(v_w, 0)
                if old_w < new_freq:
                    u_nbrs[v_w] = new_freq
                    self.rev_graph.setdefault(v_w, {})[u_w] = new_freq
                    self._record_class_edge(self.fwd_graph, u_w, v_w, new_freq - old_w)
                    touched_fwd.add(u_w)
                if initials[i] and initials[i + 1]:
                    pk = initials[i] + initials[i + 1]
                    arr = self.initial_pair_to_pids.get(pk)
                    if arr is None:
                        self.initial_pair_to_pids[pk] = array("I", (pid,))
                    elif pid not in arr:
                        arr.append(pid)

        # Chỉ tính lại xác suất Markov cho các đỉnh có cạnh thay đổi
        for u in touched_fwd:
            neighbors = self.fwd_graph[u]
            total_out = sum(w for v, w in neighbors.items() if not v.startswith("<"))
            if total_out > 0:
                probs = {v: w / total_out for v, w in neighbors.items() if not v.startswith("<")}
                self.fwd_prob[u] = probs
                self.fwd_prob_top[u] = sorted(probs.items(), key=lambda x: -x[1])[:25]

        # Chỉ sắp xếp lại các khóa bị ảnh hưởng
        phrases_ref = self.phrases
        for k in touched_ua_phrases:
            v = self.ua_phrase_to_pids[k]
            if isinstance(v, tuple) and len(v) > 1:
                self.ua_phrase_to_pids[k] = tuple(sorted(v, key=lambda p: -phrases_ref[p][2]))
        for k in touched_acronyms:
            v = self.acronym_to_pids[k]
            if isinstance(v, tuple) and len(v) > 1:
                self.acronym_to_pids[k] = tuple(sorted(v, key=lambda p: -phrases_ref[p][2]))

        for key in touched_um_keys:
            ua_key = remove_accents(key)
            cands = self.unaccented_map[key]
            cands.sort(
                key=lambda w: (
                    0 if remove_accents(w) == ua_key else 1,
                    -self.dict_freq.get(w, 0),
                    w,
                )
            )
            cid = id(cands)
            self.col_id_to_pids.pop(cid, None)
            self.col_id_to_set[cid] = set(cands)
            self.col_id_to_initial[cid] = ua_key[0] if ua_key else ""

        self.len_range_cache.clear()
        self._mdis_phrase_score_cache.clear()
        self._kb_3gram_cache.clear()
        return {"added": added, "updated": updated}

    def pids_to_exact_matches(
        self, pids: list[int], top_k: int, compute_conf: bool = True
    ) -> list[QueryMatch]:
        out: list[QueryMatch] = []
        phrases = self.phrases
        fwd_prob = self.fwd_prob
        for pid in pids[:top_k]:
            p_str, p_words, p_freq = phrases[pid]
            m_prob = 1.0
            for i in range(len(p_words) - 1):
                m_prob *= fwd_prob.get(p_words[i], {}).get(p_words[i + 1], 1e-6)
            m = QueryMatch(
                phrase=p_str,
                freq=p_freq,
                missing_count=0,
                superstring_diff=0,
                source_phrase=p_str,
                missing_words=[],
                markov_prob=m_prob,
            )
            if compute_conf:
                m.confidence = compute_confidence(m)
            out.append(m)
        return out
