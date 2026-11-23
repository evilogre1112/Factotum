from collections import defaultdict
from pathlib import Path
import shutil
import time

from factotum.core.config import DEFAULT_PATHS, FactotumPaths, get_logger
from factotum.core.extension import is_class_token, is_valid_vietnamese_syllable, print_progress, remove_accents

logger = get_logger("factotum.builder.make_tree")


def generate_unaccented_keys(word: str) -> list[str]:
    """Sinh các khóa không dấu, chữ cái đầu và viết tắt đầu+cuối cho 1 từ đơn tiếng Việt."""
    if is_class_token(word):
        return []
    keys: list[str] = []
    no_accent = remove_accents(word)
    is_vn = is_valid_vietnamese_syllable(word)

    if is_vn or word != no_accent:
        keys.append(no_accent)
    if len(no_accent) > 1 and no_accent[0].isalpha() and is_vn:
        keys.append(no_accent[0])
        if word[0] != no_accent[0]:
            keys.append(word[0])
    if len(word) >= 2 and word[0].isalpha() and word[-1].isalpha() and is_vn:
        first_variants = (word[0], no_accent[0]) if word[0] != no_accent[0] else (word[0],)
        last_variants = (word[-1], no_accent[-1]) if word[-1] != no_accent[-1] else (word[-1],)
        for f_ch in first_variants:
            for l_ch in last_variants:
                keys.append(f_ch + l_ch)
    return keys


def _read_and_dedup_pure_logs(log_dir: Path, processed_dir: Path) -> dict[str, int]:
    """Đọc toàn bộ file Pure_Log mới, bỏ qua văn bản trùng lặp >= 99% (Jaccard) và chuyển sang Processed_Log."""
    files = sorted(f for f in log_dir.glob("*.txt") if f.is_file()) if log_dir.exists() else []
    if not files:
        logger.info("Không có file .txt mới trong %s.", log_dir)
        return {}

    aggregated: dict[str, int] = defaultdict(int)
    seen_doc_sets: list[set[str]] = []

    for file_path in files:
        doc_phrases: dict[str, int] = {}
        with open(file_path, "r", encoding="utf-8") as in_file:
            next(in_file, None)
            for raw_line in in_file:
                line = raw_line.strip()
                if not line:
                    continue
                first_quote = line.find('"')
                last_quote = line.rfind('"')
                if first_quote == -1 or last_quote <= first_quote:
                    continue
                phrase = line[first_quote + 1 : last_quote].lower()
                nums = [p for p in line[:first_quote].split(",") if p.strip()]
                if nums:
                    doc_phrases[phrase] = int(nums[-1])

        pset = set(doc_phrases.keys())
        is_dup = False
        if pset:
            for prev_set in seen_doc_sets:
                if abs(len(pset) - len(prev_set)) / max(len(pset), len(prev_set)) < 0.02:
                    if len(pset & prev_set) / len(pset | prev_set) >= 0.99:
                        is_dup = True
                        break
        if not is_dup:
            seen_doc_sets.append(pset)
            for phrase, cnt in doc_phrases.items():
                aggregated[phrase] += cnt

        shutil.move(str(file_path), str(processed_dir / file_path.name))

    return aggregated


def _decompose_phrase_edges(
    words: list[str],
    word_trie: dict,
    get_or_add_id,
) -> set[tuple[int, int]]:
    """Phân rã 1 cụm thành các cạnh đồ thị hợp lệ bằng Word-Trie + DP (cụm > 12 từ nối 1-gram liền kề)."""
    k_words = len(words)
    if k_words <= 1:
        return set()

    if k_words > 12:
        return {(get_or_add_id(words[i]), get_or_add_id(words[i + 1])) for i in range(k_words - 1)}

    seg_id: dict[tuple[int, int], int] = {}
    starts_at: list[list[int]] = [[] for _ in range(k_words)]
    ends_at: list[list[int]] = [[] for _ in range(k_words)]
    for i in range(k_words):
        node = word_trie
        for j in range(i, k_words):
            node = node.get(words[j])
            if node is None:
                break
            pid_sub = node.get("")
            if pid_sub is not None:
                seg_id[(i, j)] = pid_sub
                starts_at[i].append(j)
                ends_at[j].append(i)

    can_start: set[tuple[int, int]] = {(0, e) for e in starts_at[0] if e < k_words - 1}
    for s2 in range(1, k_words):
        prev_list = [s1 for s1 in ends_at[s2 - 1] if (s1, s2 - 1) in can_start]
        if not prev_list:
            continue
        for e2 in starts_at[s2]:
            for s1 in prev_list:
                if (s1 == 0 and e2 == k_words - 1) or (s1, e2) not in seg_id:
                    can_start.add((s2, e2))
                    break

    can_end: set[tuple[int, int]] = {(s, k_words - 1) for s in ends_at[k_words - 1] if s > 0}
    for e1 in range(k_words - 2, -1, -1):
        next_list = [e2 for e2 in starts_at[e1 + 1] if (e1 + 1, e2) in can_end]
        if not next_list:
            continue
        for s1 in ends_at[e1]:
            for e2 in next_list:
                if (s1 == 0 and e2 == k_words - 1) or (s1, e2) not in seg_id:
                    can_end.add((s1, e1))
                    break

    unique_edges: set[tuple[int, int]] = set()
    for e1 in range(k_words - 1):
        s2 = e1 + 1
        valid_left = [s1 for s1 in ends_at[e1] if (s1, e1) in can_start]
        valid_right = [e2 for e2 in starts_at[s2] if (s2, e2) in can_end]
        if not valid_left or not valid_right:
            continue
        for s1 in valid_left:
            u = seg_id[(s1, e1)]
            for e2 in valid_right:
                if (s1 == 0 and e2 == k_words - 1) or (s1, e2) not in seg_id:
                    unique_edges.add((u, seg_id[(s2, e2)]))

    return unique_edges


def _write_graph_outputs(
    paths: FactotumPaths,
    id_to_phrase: list[str],
    dict_freq: dict[int, int],
    edge_freq: dict[int, dict[int, int]],
    unaccented_links: dict[str, set[int]],
) -> None:
    """Xuất KnowledgeBase.txt, Data.txt và UnaccentedMap.txt theo `paths`."""
    kb_tmp = paths.kb_path.with_suffix(".txt.tmp")
    with open(kb_tmp, "w", encoding="utf-8") as out_kb:
        sorted_nodes = sorted(
            (i for i in range(1, len(id_to_phrase)) if dict_freq[i] > 0),
            key=lambda i: id_to_phrase[i],
        )
        for node_id in sorted_nodes:
            out_kb.write(f"A,{id_to_phrase[node_id]},{dict_freq[node_id]}\n")

        kb_edges = [
            (id_to_phrase[id_a], id_to_phrase[id_b], total_w)
            for id_a, targets in edge_freq.items()
            for id_b, total_w in targets.items()
        ]
        kb_edges.sort(key=lambda e: (e[0], e[1]))
        for src, tgt, weight in kb_edges:
            out_kb.write(f"AB,{src},{tgt},{weight}\n")
    kb_tmp.replace(paths.kb_path)

    with open(paths.data_path, "w", encoding="utf-8") as out_data:
        out_data.write("Word_A,Word_B,Freq,Probability\n")
        for id_a in sorted(edge_freq.keys(), key=lambda i: id_to_phrase[i]):
            targets = edge_freq[id_a]
            total_out = sum(targets.values())
            if total_out <= 0:
                continue
            export_edges = sorted(
                ((id_b, w, w / total_out) for id_b, w in targets.items()),
                key=lambda x: (-x[2], -x[1]),
            )
            for id_b, w, prob in export_edges:
                out_data.write(f"{id_to_phrase[id_a]},{id_to_phrase[id_b]},{w},{prob:.8f}\n")

    with open(paths.unaccented_map_path, "w", encoding="utf-8") as out_ua:
        for key_str in sorted(unaccented_links.keys()):
            ua_key = remove_accents(key_str)
            targets = sorted(
                unaccented_links[key_str],
                key=lambda tid: (
                    0 if remove_accents(id_to_phrase[tid]) == ua_key else 1,
                    -dict_freq[tid],
                    id_to_phrase[tid],
                ),
            )
            out_ua.write(f"{key_str}: {', '.join(id_to_phrase[tid] for tid in targets)}\n")


def build_graph(paths: FactotumPaths = DEFAULT_PATHS, show_progress: bool = True) -> None:
    """Dựng KnowledgeBase.txt, Data.txt và UnaccentedMap.txt từ Pure_Log theo `paths`."""
    t_start = time.perf_counter()
    paths.processed_log_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    phrase_to_id: dict[str, int] = {}
    id_to_phrase: list[str] = [""]
    word_trie: dict = {}
    dict_freq: dict[int, int] = defaultdict(int)
    edge_freq: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    unaccented_links: dict[str, set[int]] = defaultdict(set)

    def get_or_add_id(p: str) -> int:
        pid = phrase_to_id.get(p)
        if pid is not None:
            return pid
        new_id = len(id_to_phrase)
        phrase_to_id[p] = new_id
        id_to_phrase.append(p)
        node = word_trie
        for w in p.split():
            node = node.setdefault(w, {})
        node[""] = new_id
        return new_id

    def register_unaccented_tokens(phrase: str) -> None:
        for word in phrase.split():
            w_id = get_or_add_id(word)
            for key in generate_unaccented_keys(word):
                unaccented_links[key].add(w_id)

    # 1. Nạp KnowledgeBase lịch sử nếu có
    if paths.kb_path.exists():
        with open(paths.kb_path, "r", encoding="utf-8") as kb_file:
            for line in kb_file:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if parts[0] == "A" and len(parts) >= 3:
                    p_id = get_or_add_id(parts[1])
                    dict_freq[p_id] = int(parts[2])
                    register_unaccented_tokens(parts[1])
                elif parts[0] == "AB" and len(parts) >= 4:
                    edge_freq[get_or_add_id(parts[1])][get_or_add_id(parts[2])] += int(float(parts[3]))
        logger.info("Đã nạp tri thức lịch sử từ %s.", paths.kb_path)

    # 2. Đọc & gom cụm mới từ Pure_Log
    aggregated_new = _read_and_dedup_pure_logs(paths.pure_log_dir, paths.processed_log_dir)
    sorted_unique_new = sorted(aggregated_new.items(), key=lambda kv: (kv[0].count(" ") + 1, kv[0]))

    total_phrases = len(sorted_unique_new)
    t_ingest = time.perf_counter()
    last_draw = 0.0
    for idx_p, (phrase, count) in enumerate(sorted_unique_new, start=1):
        if show_progress:
            now = time.perf_counter()
            if now - last_draw >= 0.1 or idx_p == total_phrases:
                print_progress(idx_p, total_phrases, t_ingest, label="MakeTree", unit="cụm")
                last_draw = now

        p_id = get_or_add_id(phrase)
        dict_freq[p_id] += count
        register_unaccented_tokens(phrase)

        for u, v in _decompose_phrase_edges(phrase.split(), word_trie, get_or_add_id):
            edge_freq[u][v] += count

    # 3. Xuất 3 file đầu ra
    _write_graph_outputs(paths, id_to_phrase, dict_freq, edge_freq, unaccented_links)
    logger.info("MakeTree hoàn tất trong %.3fs -> %s", time.perf_counter() - t_start, paths.output_dir)


def main() -> None:
    build_graph(paths=DEFAULT_PATHS, show_progress=True)


if __name__ == "__main__":
    main()
