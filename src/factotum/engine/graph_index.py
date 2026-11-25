from array import array
from collections import defaultdict
from pathlib import Path
import sys

_MAX_COL_PID_CACHE = 2048

from factotum.builder.make_tree import generate_unaccented_keys
from factotum.core.extension import (
    get_token_class,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine import graph_io
from factotum.engine.graph_io import FTM_MAGIC
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

    def lookup_real_phrase_score(
        self, words: list[str], max_diff_k: int = MAX_SUPERSTRING_DIFF_K
    ) -> tuple[int, int, str]:
        """Kiểm tra chuỗi `words` có khớp chính xác hoặc nằm trong chuỗi tối đại cha của KB không."""
        phrase = " ".join(words)
        exact_freq = self.dict_freq.get(phrase)
        if exact_freq is not None:
            return exact_freq, 0, phrase

        fwd = self.fwd_graph
        for i in range(len(words) - 1):
            nbrs = fwd.get(words[i])
            if not nbrs or words[i + 1] not in nbrs:
                return 0, -1, ""

        posting_sets: list[set[int]] = []
        w2p = self.word_to_phrases
        for w in words:
            ps = w2p.get(w)
            if not ps:
                return 0, -1, ""
            posting_sets.append(ps)

        if len(posting_sets) == 1:
            common_ids = posting_sets[0]
        else:
            posting_sets.sort(key=len)
            common_ids = set.intersection(*posting_sets)
            if not common_ids:
                return 0, -1, ""

        padded_target = f" {phrase} "
        best_freq = 0
        best_diff = max_diff_k + 1
        best_source = ""
        target_len = len(words)
        phrases = self.phrases

        for pid in common_ids:
            p_str, p_words, p_freq = phrases[pid]
            diff = len(p_words) - target_len
            if diff < 0 or diff > max_diff_k:
                continue
            if padded_target in f" {p_str} ":
                if diff < best_diff or (diff == best_diff and p_freq > best_freq):
                    best_diff = diff
                    best_freq = p_freq
                    best_source = p_str

        if best_freq > 0:
            return best_freq, best_diff, best_source
        return 0, -1, ""

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
