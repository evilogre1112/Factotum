from array import array
from collections import defaultdict
import gc
import json
from pathlib import Path
import pickle
import sys
import time
from typing import TYPE_CHECKING
import zlib

from factotum.core.console import print_progress
from factotum.core.vietnamese import remove_accents
from factotum.engine.models import (
    CACHE_PATH,
    CACHE_VERSION,
    DATA_PATH,
    KB_PATH,
    UNACCENTED_MAP_PATH,
)

if TYPE_CHECKING:
    from factotum.engine.graph_index import GraphIndex

FTM_MAGIC = b"FTM1"


def build_source_signature(
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


def export_payload(index: "GraphIndex", signature: object = None) -> dict:
    return {
        "version": CACHE_VERSION,
        "signature": signature,
        "dict_freq": index.dict_freq,
        "word_freq": index.word_freq,
        "real_vocab": index.real_vocab,
        "phrases": index.phrases,
        "word_to_phrases": index.word_to_phrases,
        "initial_pair_to_pids": index.initial_pair_to_pids,
        "len_to_phrases": index.len_to_phrases,
        "fwd_graph": index.fwd_graph,
        "rev_graph": index.rev_graph,
        "fwd_prob": index.fwd_prob,
        "fwd_prob_top": index.fwd_prob_top,
        "unaccented_map": index.unaccented_map,
        "ua_phrase_to_pids": index.ua_phrase_to_pids,
        "acronym_to_pids": index.acronym_to_pids,
    }


def import_payload(index: "GraphIndex", payload: dict) -> None:
    index.dict_freq = payload["dict_freq"]
    index.word_freq = payload["word_freq"]
    index.real_vocab = payload["real_vocab"]
    index.phrases = payload["phrases"]
    index.word_to_phrases = payload["word_to_phrases"]
    index.initial_pair_to_pids = payload["initial_pair_to_pids"]
    index.len_to_phrases = payload["len_to_phrases"]
    index.fwd_graph = payload["fwd_graph"]
    index.rev_graph = payload["rev_graph"]
    index.fwd_prob = payload["fwd_prob"]
    index.fwd_prob_top = payload["fwd_prob_top"]
    index.unaccented_map = payload["unaccented_map"]
    index.ua_phrase_to_pids = payload["ua_phrase_to_pids"]
    index.acronym_to_pids = payload["acronym_to_pids"]
    index.len_range_cache = {}
    index._rebuild_col_id_indexes()


def try_load_cache(
    index: "GraphIndex",
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
        import_payload(index, payload)
        return True
    except Exception:
        return False


def save_cache(
    index: "GraphIndex",
    cache_path: Path,
    sig: tuple[int, tuple[tuple[bool, int, int], ...]],
) -> None:
    payload = export_payload(index, signature=sig)
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


def save_standalone(index: "GraphIndex", model_path: str | Path) -> Path:
    """Lưu mô hình thành 1 file `.ftm` thuần dữ liệu (JSON + zlib)."""
    dest = Path(model_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    data = {
        "version": CACHE_VERSION,
        "phrases": [[p_str, freq] for p_str, _, freq in index.phrases],
        "fwd_graph": index.fwd_graph,
        "unaccented_map": index.unaccented_map,
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


def _finalize_phrase_indexes(
    index: "GraphIndex",
    ua_phrase_tmp: dict[str, list[int]],
    acronym_tmp: dict[str, list[int]],
    initial_pair_tmp: dict[str, set[int]],
) -> None:
    intern = sys.intern
    phrases_ref = index.phrases
    index.ua_phrase_to_pids = {
        k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
        for k, v in ua_phrase_tmp.items()
    }
    index.acronym_to_pids = {
        k: (v[0] if len(v) == 1 else tuple(sorted(v, key=lambda pid: -phrases_ref[pid][2])))
        for k, v in acronym_tmp.items()
    }
    index.initial_pair_to_pids = {
        intern(k): array("I", v) for k, v in initial_pair_tmp.items()
    }


def _finalize_graph_probs(index: "GraphIndex") -> None:
    index._augment_all_class_edges()
    rev_tmp: dict[str, dict[str, int]] = defaultdict(dict)
    for u, nbrs in index.fwd_graph.items():
        total_out = sum(w for v, w in nbrs.items() if not v.startswith("<"))
        if total_out > 0:
            probs = {v: w / total_out for v, w in nbrs.items() if not v.startswith("<")}
            index.fwd_prob[u] = probs
            index.fwd_prob_top[u] = sorted(probs.items(), key=lambda x: -x[1])[:25]
        for v, w in nbrs.items():
            if not u.startswith("<") and not v.startswith("<"):
                rev_tmp[v][u] = w
    index.rev_graph = dict(rev_tmp)


def _index_single_phrase(
    index: "GraphIndex",
    phrase: str,
    freq: int,
    word_ua_cache: dict[str, str],
    ua_phrase_tmp: dict[str, list[int]],
    acronym_tmp: dict[str, list[int]],
    initial_pair_tmp: dict[str, set[int]],
    fwd_graph_tmp: dict[str, dict[str, int]] | None = None,
) -> None:
    intern = sys.intern
    index.dict_freq[phrase] = freq
    words = tuple(intern(w) for w in phrase.split())
    idx = len(index.phrases)
    index.phrases.append((phrase, words, freq))
    n_w = len(words)
    index.len_to_phrases[n_w].add(idx)

    initials: list[str] = []
    ua_words: list[str] = []
    for w in words:
        index.real_vocab.add(w)
        index.word_to_phrases[w].add(idx)
        if index.word_freq[w] < freq:
            index.word_freq[w] = freq
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
        if fwd_graph_tmp is not None:
            u_w, v_w = words[i], words[i + 1]
            if fwd_graph_tmp[u_w][v_w] < freq:
                fwd_graph_tmp[u_w][v_w] = freq
        if initials[i] and initials[i + 1]:
            initial_pair_tmp[initials[i] + initials[i + 1]].add(idx)


def load_from_json_data(index: "GraphIndex", data: dict) -> None:
    """Dựng toàn bộ chỉ mục RAM từ dữ liệu thuần JSON (.ftm)."""
    intern = sys.intern
    index._reset_state()
    index.fwd_graph = {
        intern(u): {intern(v): w for v, w in nbrs.items()}
        for u, nbrs in data["fwd_graph"].items()
    }
    for k, cands in data["unaccented_map"].items():
        filtered = index._filter_unaccented_candidates(k, cands)
        if k and filtered:
            index.unaccented_map[intern(k)] = [intern(w) for w in filtered]

    word_ua_cache: dict[str, str] = {}
    ua_phrase_tmp: dict[str, list[int]] = defaultdict(list)
    acronym_tmp: dict[str, list[int]] = defaultdict(list)
    initial_pair_tmp: dict[str, set[int]] = defaultdict(set)

    for phrase, freq in data["phrases"]:
        _index_single_phrase(
            index, phrase, freq, word_ua_cache, ua_phrase_tmp, acronym_tmp, initial_pair_tmp
        )

    _finalize_phrase_indexes(index, ua_phrase_tmp, acronym_tmp, initial_pair_tmp)
    _finalize_graph_probs(index)
    index._rebuild_col_id_indexes()


def load_standalone(index: "GraphIndex", model_path: str | Path, allow_pickle: bool = False) -> None:
    """Nạp mô hình từ file `.ftm` (JSON + zlib), hoặc `.pkl` khi bật `allow_pickle=True`."""
    src = Path(model_path)
    if not src.exists():
        raise FileNotFoundError(f"Không tìm thấy file mô hình: {src}")
    raw = src.read_bytes()
    if raw.startswith(FTM_MAGIC):
        gc_was_enabled = gc.isenabled()
        try:
            gc.disable()
            data = json.loads(zlib.decompress(raw[4:]).decode("utf-8"))
            load_from_json_data(index, data)
        finally:
            if gc_was_enabled:
                gc.enable()
        index.loaded_from_cache = True
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
    import_payload(index, payload)
    index.loaded_from_cache = True


def load_from_files(
    index: "GraphIndex",
    kb_path: Path = KB_PATH,
    data_path: Path = DATA_PATH,
    unaccented_path: Path = UNACCENTED_MAP_PATH,
    show_progress: bool = True,
    use_cache: bool = True,
    cache_path: Path | None = None,
) -> None:
    """Nạp đồ thị từ bộ file văn bản KB / data.txt / UM (có hỗ trợ cache nhị phân cục bộ)."""
    intern = sys.intern
    index._reset_state()

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
        build_source_signature(kb_path, data_path, unaccented_path)
        if effective_cache is not None
        else None
    )
    if effective_cache is not None and sig is not None:
        if try_load_cache(index, effective_cache, sig):
            index.loaded_from_cache = True
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
                _index_single_phrase(
                    index,
                    phrase,
                    freq,
                    word_ua_cache,
                    ua_phrase_tmp,
                    acronym_tmp,
                    initial_pair_tmp,
                    fwd_graph_tmp=fwd_graph_tmp,
                )
        if show_progress:
            print_progress(total_kb, total_kb, t_load, label="Nạp RAM ", unit="KB")

    _finalize_phrase_indexes(index, ua_phrase_tmp, acronym_tmp, initial_pair_tmp)

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

    index.fwd_graph = {u: dict(nbrs) for u, nbrs in fwd_graph_tmp.items()}
    _finalize_graph_probs(index)

    if unaccented_path.exists():
        with open(unaccented_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or ":" not in line:
                    continue
                key, vals = line.split(":", 1)
                key = key.strip()
                candidates = index._filter_unaccented_candidates(
                    key, [v.strip() for v in vals.split(",") if v.strip()]
                )
                if key and candidates:
                    index.unaccented_map[intern(key)] = [intern(w) for w in candidates]

    index._rebuild_col_id_indexes()

    if effective_cache is not None and sig is not None and index.phrases:
        save_cache(index, effective_cache, sig)
