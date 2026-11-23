from array import array
from collections import defaultdict
import gc
import json
from pathlib import Path
import pickle
import sys
import time
import zlib

FTM_MAGIC = b"FTM1"
_MAX_COL_PID_CACHE = 2048

from factotum.builder.make_tree import generate_unaccented_keys
from factotum.core.extension import (
    get_token_class,
    is_valid_vietnamese_syllable,
    print_progress,
    remove_accents,
)
from factotum.engine.models import (
    CACHE_PATH,
    CACHE_VERSION,
    DATA_PATH,
    KB_PATH,
    MAX_SUPERSTRING_DIFF_K,
    UNACCENTED_MAP_PATH,
    QueryMatch,
    compute_confidence,
)


class GraphIndex:
    def __init__(self) -> None:
        self.dict_freq: dict[str, int] = {}                                # Cụm KB -> tần suất xuất hiện
        self.word_freq: dict[str, int] = defaultdict(int)                  # Từ đơn -> tần suất lớn nhất trong các cụm chứa nó
        self.real_vocab: set[str] = set()                                  # Tập từ đơn có thật trong KB
        self.phrases: list[tuple[str, tuple[str, ...], int]] = []          # (chuỗi cụm, tuple từ interned, tần suất)
        self.word_to_phrases: dict[str, set[int]] = defaultdict(set)       # Từ đơn -> tập ID cụm chứa từ đó
        self.col_id_to_pids: dict[int, set[int]] = {}                      # Cache lười (tối đa 256) id(list) -> tập ID cụm
        self.col_id_to_set: dict[int, set[str]] = {}                       # id(list ứng viên) -> set ứng viên
        self.col_id_to_initial: dict[int, str] = {}                        # id(list ứng viên) -> chữ cái đầu không dấu
        self.initial_pair_to_pids: dict[str, array] = {}                   # Cặp 2 chữ cái đầu liền kề -> array('I') nén 4B/id
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
    def _build_source_signature(
        kb_path: Path, data_path: Path, unaccented_path: Path
    ) -> tuple[int, tuple[tuple[bool, int, int], ...]]:
        file_sigs: list[tuple[bool, int, int]] = []
        for p in (kb_path, data_path, unaccented_path):
            if p.exists():
                st = p.stat()
                file_sigs.append((True, st.st_size, st.st_mtime_ns))
            else:
                file_sigs.append((False, 0, 0))
        return (CACHE_VERSION, tuple(file_sigs))

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

    def _export_payload(self, signature: object = None) -> dict:
        return {
            "version": CACHE_VERSION,
            "signature": signature,
            "dict_freq": self.dict_freq,
            "word_freq": self.word_freq,
            "real_vocab": self.real_vocab,
            "phrases": self.phrases,
            "word_to_phrases": self.word_to_phrases,
            "initial_pair_to_pids": self.initial_pair_to_pids,
            "len_to_phrases": self.len_to_phrases,
            "fwd_graph": self.fwd_graph,
            "rev_graph": self.rev_graph,
            "fwd_prob": self.fwd_prob,
            "fwd_prob_top": self.fwd_prob_top,
            "unaccented_map": self.unaccented_map,
            "ua_phrase_to_pids": self.ua_phrase_to_pids,
            "acronym_to_pids": self.acronym_to_pids,
        }

    def _import_payload(self, payload: dict) -> None:
        self.dict_freq = payload["dict_freq"]
        self.word_freq = payload["word_freq"]
        self.real_vocab = payload["real_vocab"]
        self.phrases = payload["phrases"]
        self.word_to_phrases = payload["word_to_phrases"]
        self.initial_pair_to_pids = payload["initial_pair_to_pids"]
        self.len_to_phrases = payload["len_to_phrases"]
        self.fwd_graph = payload["fwd_graph"]
        self.rev_graph = payload["rev_graph"]
        self.fwd_prob = payload["fwd_prob"]
        self.fwd_prob_top = payload["fwd_prob_top"]
        self.unaccented_map = payload["unaccented_map"]
        self.ua_phrase_to_pids = payload["ua_phrase_to_pids"]
        self.acronym_to_pids = payload["acronym_to_pids"]
        self.len_range_cache = {}
        self._rebuild_col_id_indexes()

    def _try_load_cache(
        self,
        cache_path: Path,
        expected_sig: tuple[int, tuple[tuple[bool, int, int], ...]],
    ) -> bool:
        if not cache_path.exists():
            return False
        gc_was_enabled = gc.isenabled()
        try:
            gc.disable()
            with open(cache_path, "rb") as f:
                payload = pickle.load(f)
        except Exception:
            return False
        finally:
            if gc_was_enabled:
                gc.enable()

        if not isinstance(payload, dict) or payload.get("signature") != expected_sig:
            return False

        try:
            self._import_payload(payload)
            return True
        except Exception:
            return False

    def _save_cache(
        self,
        cache_path: Path,
        sig: tuple[int, tuple[tuple[bool, int, int], ...]],
    ) -> None:
        payload = self._export_payload(signature=sig)
        tmp_path = cache_path.with_suffix(".pkl.tmp")
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_path.replace(cache_path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def save_standalone(self, model_path: str | Path) -> Path:
        """Lưu mô hình thành 1 file `.ftm` thuần dữ liệu (JSON + zlib, an toàn khi chia sẻ, không dùng pickle)."""
        dest = Path(model_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest.with_suffix(dest.suffix + ".tmp")
        data = {
            "version": CACHE_VERSION,
            "phrases": [[p_str, freq] for p_str, _, freq in self.phrases],
            "fwd_graph": self.fwd_graph,
            "unaccented_map": self.unaccented_map,
        }
        raw = FTM_MAGIC + zlib.compress(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            level=3,
        )
        try:
            with open(tmp_path, "wb") as f:
                f.write(raw)
            tmp_path.replace(dest)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        return dest

    def _load_from_json_data(self, data: dict) -> None:
        """Dựng toàn bộ chỉ mục RAM từ dữ liệu thuần JSON (.ftm), tối ưu bộ nhớ."""
        intern = sys.intern
        self.dict_freq = {}
        self.word_freq = defaultdict(int)
        self.real_vocab = set()
        self.phrases = []
        self.word_to_phrases = defaultdict(set)
        self.col_id_to_pids = {}
        self.col_id_to_set = {}
        self.col_id_to_initial = {}
        self.initial_pair_to_pids = {}
        self.len_to_phrases = defaultdict(set)
        self.len_range_cache = {}
        self.fwd_graph = {
            intern(u): {intern(v): w for v, w in nbrs.items()}
            for u, nbrs in data["fwd_graph"].items()
        }
        self.rev_graph = {}
        self.fwd_prob = {}
        self.fwd_prob_top = {}
        self.unaccented_map = {
            intern(k): [intern(w) for w in cands]
            for k, cands in data["unaccented_map"].items()
        }
        self.ua_phrase_to_pids = {}
        self.acronym_to_pids = {}

        word_ua_cache: dict[str, str] = {}
        ua_phrase_tmp: dict[str, list[int]] = defaultdict(list)
        acronym_tmp: dict[str, list[int]] = defaultdict(list)
        initial_pair_tmp: dict[str, set[int]] = defaultdict(set)

        for phrase, freq in data["phrases"]:
            self.dict_freq[phrase] = freq
            words = tuple(intern(w) for w in phrase.split())
            idx = len(self.phrases)
            self.phrases.append((phrase, words, freq))
            n_w = len(words)
            self.len_to_phrases[n_w].add(idx)
            initials: list[str] = []
            ua_words: list[str] = []
            for w in words:
                self.real_vocab.add(w)
                self.word_to_phrases[w].add(idx)
                if self.word_freq[w] < freq:
                    self.word_freq[w] = freq
                ua = word_ua_cache.get(w)
                if ua is None:
                    ua = remove_accents(w)
                    word_ua_cache[w] = ua
                ua_words.append(ua)
                initials.append(ua[0] if ua else "")
            if n_w >= 2:
                ua_phrase_tmp[" ".join(ua_words)].append(idx)
                acr = "".join(initials)
                if len(acr) == n_w:
                    acronym_tmp[acr].append(idx)
            for i in range(n_w - 1):
                if initials[i] and initials[i + 1]:
                    initial_pair_tmp[initials[i] + initials[i + 1]].add(idx)

        phrases_ref = self.phrases
        self.ua_phrase_to_pids = {
            k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
            for k, v in ua_phrase_tmp.items()
        }
        self.acronym_to_pids = {
            k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
            for k, v in acronym_tmp.items()
        }
        self.initial_pair_to_pids = {
            intern(k): array("I", v) for k, v in initial_pair_tmp.items()
        }
        del ua_phrase_tmp, acronym_tmp, initial_pair_tmp

        self._augment_all_class_edges()
        rev_tmp: dict[str, dict[str, int]] = defaultdict(dict)
        for u, nbrs in self.fwd_graph.items():
            total_out = sum(w for v, w in nbrs.items() if not v.startswith("<"))
            if total_out > 0:
                probs = {v: w / total_out for v, w in nbrs.items() if not v.startswith("<")}
                self.fwd_prob[u] = probs
                self.fwd_prob_top[u] = sorted(probs.items(), key=lambda x: -x[1])[:25]
            for v, w in nbrs.items():
                if not u.startswith("<") and not v.startswith("<"):
                    rev_tmp[v][u] = w
        self.rev_graph = dict(rev_tmp)

        self._rebuild_col_id_indexes()

    def load_standalone(self, model_path: str | Path, allow_pickle: bool = False) -> None:
        """
        Nạp mô hình từ file `.ftm` thuần dữ liệu (JSON + zlib, an toàn mặc định).
        Chỉ nạp file `.pkl` cục bộ khi người dùng chủ động bật `allow_pickle=True`.
        """
        src = Path(model_path)
        if not src.exists():
            raise FileNotFoundError(f"Không tìm thấy file mô hình: {src}")
        raw = src.read_bytes()
        if raw.startswith(FTM_MAGIC):
            gc_was_enabled = gc.isenabled()
            try:
                gc.disable()
                data = json.loads(zlib.decompress(raw[4:]).decode("utf-8"))
                self._load_from_json_data(data)
            finally:
                if gc_was_enabled:
                    gc.enable()
            self.loaded_from_cache = True
            return

        if not allow_pickle:
            raise ValueError(
                f"File '{src.name}' không ở định dạng .ftm an toàn (JSON+zlib). "
                "Nếu đây là file cache pickle cục bộ đáng tin cậy, hãy truyền allow_pickle=True."
            )

        gc_was_enabled = gc.isenabled()
        try:
            gc.disable()
            payload = pickle.loads(raw)
        finally:
            if gc_was_enabled:
                gc.enable()
        if not isinstance(payload, dict) or "phrases" not in payload:
            raise ValueError(f"File mô hình không hợp lệ: {src}")
        self._import_payload(payload)
        self.loaded_from_cache = True

    def load(
        self,
        kb_path: Path = KB_PATH,
        data_path: Path = DATA_PATH,
        unaccented_path: Path = UNACCENTED_MAP_PATH,
        show_progress: bool = True,
        use_cache: bool = True,
        cache_path: Path | None = None,
    ) -> None:
        intern = sys.intern
        self.dict_freq = {}
        self.word_freq = defaultdict(int)
        self.real_vocab = set()
        self.phrases = []
        self.word_to_phrases = defaultdict(set)
        self.col_id_to_pids = {}
        self.col_id_to_set = {}
        self.col_id_to_initial = {}
        self.initial_pair_to_pids = {}
        self.len_to_phrases = defaultdict(set)
        self.len_range_cache = {}
        self.fwd_graph = {}
        self.rev_graph = {}
        self.fwd_prob = {}
        self.fwd_prob_top = {}
        self.unaccented_map = {}
        self.ua_phrase_to_pids = {}
        self.acronym_to_pids = {}
        self.loaded_from_cache = False

        effective_cache: Path | None = None
        if use_cache:
            if cache_path is not None:
                effective_cache = cache_path
            elif (
                kb_path == KB_PATH
                and data_path == DATA_PATH
                and unaccented_path == UNACCENTED_MAP_PATH
            ):
                effective_cache = CACHE_PATH

        sig = (
            self._build_source_signature(kb_path, data_path, unaccented_path)
            if effective_cache is not None
            else None
        )
        if effective_cache is not None and sig is not None:
            if self._try_load_cache(effective_cache, sig):
                self.loaded_from_cache = True
                return

        fwd_graph_tmp: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        word_ua_cache: dict[str, str] = {}
        ua_phrase_tmp: dict[str, list[int]] = defaultdict(list)
        acronym_tmp: dict[str, list[int]] = defaultdict(list)
        initial_pair_tmp: dict[str, set[int]] = defaultdict(set)
        if kb_path.exists():
            total_kb = max(1, kb_path.stat().st_size // 1024)
            bytes_read = 0
            t_load = time.perf_counter()
            last_draw = 0.0
            with open(kb_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    if raw_line.startswith("AB,"):
                        break
                    bytes_read += len(raw_line)
                    if show_progress:
                        now = time.perf_counter()
                        if now - last_draw >= 0.1:
                            print_progress(
                                min(total_kb, bytes_read // 1024),
                                total_kb,
                                t_load,
                                label="Nạp RAM ",
                                unit="KB",
                            )
                            last_draw = now
                    line = raw_line.strip()
                    if not line or not line.startswith("A,"):
                        continue
                    c1 = line.find(",", 2)
                    if c1 == -1:
                        continue
                    phrase = line[2:c1]
                    if not phrase:
                        continue
                    freq = int(line[c1 + 1 :])
                    self.dict_freq[phrase] = freq
                    words = tuple(intern(w) for w in phrase.split())
                    idx = len(self.phrases)
                    self.phrases.append((phrase, words, freq))
                    self.len_to_phrases[len(words)].add(idx)
                    initials: list[str] = []
                    ua_words: list[str] = []
                    for w in words:
                        self.real_vocab.add(w)
                        self.word_to_phrases[w].add(idx)
                        if self.word_freq[w] < freq:
                            self.word_freq[w] = freq
                        ua = word_ua_cache.get(w)
                        if ua is None:
                            ua = remove_accents(w)
                            word_ua_cache[w] = ua
                        ua_words.append(ua)
                        initials.append(ua[0] if ua else "")
                    n_w = len(words)
                    if n_w >= 2:
                        ua_phrase_tmp[" ".join(ua_words)].append(idx)
                        acr = "".join(initials)
                        if len(acr) == n_w:
                            acronym_tmp[acr].append(idx)
                    for i in range(n_w - 1):
                        u_w, v_w = words[i], words[i + 1]
                        if fwd_graph_tmp[u_w][v_w] < freq:
                            fwd_graph_tmp[u_w][v_w] = freq
                        if initials[i] and initials[i + 1]:
                            initial_pair_tmp[initials[i] + initials[i + 1]].add(idx)
            if show_progress:
                print_progress(total_kb, total_kb, t_load, label="Nạp RAM ", unit="KB")

        phrases_ref = self.phrases
        self.ua_phrase_to_pids = {
            k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
            for k, v in ua_phrase_tmp.items()
        }
        self.acronym_to_pids = {
            k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
            for k, v in acronym_tmp.items()
        }
        self.initial_pair_to_pids = {
            intern(k): array("I", v) for k, v in initial_pair_tmp.items()
        }
        del ua_phrase_tmp, acronym_tmp, initial_pair_tmp

        if data_path.exists():
            with open(data_path, "r", encoding="utf-8") as f:
                next(f, None)
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    c1 = line.find(",")
                    if c1 == -1:
                        continue
                    c2 = line.find(",", c1 + 1)
                    if c2 == -1:
                        continue
                    c3 = line.find(",", c2 + 1)
                    w_a = line[:c1]
                    w_b = line[c1 + 1 : c2]
                    freq_str = line[c2 + 1 : c3] if c3 != -1 else line[c2 + 1 :]
                    freq = int(float(freq_str))
                    sp_a = w_a.rfind(" ")
                    u = w_a[sp_a + 1 :] if sp_a != -1 else w_a
                    sp_b = w_b.find(" ")
                    v = w_b[:sp_b] if sp_b != -1 else w_b
                    if u and v and fwd_graph_tmp[u][v] < freq:
                        fwd_graph_tmp[u][v] = freq

        self.fwd_graph = {u: dict(nbrs) for u, nbrs in fwd_graph_tmp.items()}
        self._augment_all_class_edges()
        rev_tmp: dict[str, dict[str, int]] = defaultdict(dict)
        for u, nbrs in self.fwd_graph.items():
            for v, w in nbrs.items():
                if not u.startswith("<") and not v.startswith("<"):
                    rev_tmp[v][u] = w
        self.rev_graph = dict(rev_tmp)

        for u, neighbors in self.fwd_graph.items():
            total_out = sum(w for v, w in neighbors.items() if not v.startswith("<"))
            if total_out > 0:
                probs = {v: w / total_out for v, w in neighbors.items() if not v.startswith("<")}
                self.fwd_prob[u] = probs
                self.fwd_prob_top[u] = sorted(probs.items(), key=lambda x: -x[1])[:25]

        if unaccented_path.exists():
            with open(unaccented_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or ":" not in line:
                        continue
                    key, vals = line.split(":", 1)
                    key = key.strip()
                    candidates = [v.strip() for v in vals.split(",") if v.strip()]
                    ua_key = remove_accents(key)
                    if len(key) >= 2 and key.isalpha():
                        if not is_valid_vietnamese_syllable(key):
                            candidates = [
                                v for v in candidates
                                if v != remove_accents(v) or is_valid_vietnamese_syllable(v)
                            ]
                        elif key == ua_key:
                            exact_syll = [v for v in candidates if remove_accents(v) == ua_key]
                            if exact_syll:
                                candidates = exact_syll
                    if key and candidates:
                        self.unaccented_map[intern(key)] = [intern(w) for w in candidates]

        self._rebuild_col_id_indexes()

        if effective_cache is not None and sig is not None and self.phrases:
            self._save_cache(effective_cache, sig)

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
