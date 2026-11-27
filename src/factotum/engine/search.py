from factotum.core.extension import (
    is_class_token,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.kb_search import (
    direct_kb_search,
    expand_query_variants,
    query_by_kb_composition,
    split_sticky_token,
)
from factotum.engine.models import (
    BEAM_WIDTH,
    MAX_CANDIDATES_PER_KEY,
    MAX_MISSING_GAP,
    MAX_SUPERSTRING_DIFF_K,
    QueryMatch,
    rank_key,
)


def search_variant(
    index: GraphIndex,
    columns: list[list[str]],
    allow_missing: bool = True,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> list[QueryMatch]:
    """Tìm trong KB trước; chỉ chạy Beam Search Markov khi chưa tìm đủ cụm KB thực tế."""
    if not columns:
        return []

    if len(columns) == 1:
        matches = []
        for w in columns[0][:MAX_CANDIDATES_PER_KEY]:
            freq = index.dict_freq.get(w, 0) or index.word_freq.get(w, 0)
            if freq > 0:
                matches.append(QueryMatch(w, freq, 0, 0, w, [], markov_prob=1.0))
        if not matches and columns[0]:
            w = columns[0][0]
            matches.append(QueryMatch(w, 0, 0, -1, "", [], markov_prob=1e-6))
        return matches

    kb_matches = direct_kb_search(
        index, columns, allow_missing=allow_missing, max_diff_k=max_diff_k
    )
    results_map: dict[str, QueryMatch] = {m.phrase: m for m in kb_matches}

    has_exact_zero_miss = any(
        m.superstring_diff == 0 and m.missing_count == 0 for m in kb_matches
    )
    if len(kb_matches) >= 3 or (has_exact_zero_miss and all(len(c) <= 15 for c in columns)):
        return list(results_map.values())

    trimmed_cols = [col[:MAX_CANDIDATES_PER_KEY] for col in columns]
    dict_freq = index.dict_freq
    fwd_prob = index.fwd_prob
    fwd_prob_top = index.fwd_prob_top
    max_per_state = 2

    beam: list[tuple[list[str], float, int, list[str]]] = [
        ([w], max(dict_freq.get(w, 1), 1) / 100000.0, 0, []) for w in trimmed_cols[0]
    ]

    ua_map = index.unaccented_map

    def _is_initial_col(col: list[str]) -> bool:
        if not col:
            return False
        ch = remove_accents(col[0])[:1]
        return bool(ch) and col is ua_map.get(ch)

    for col_idx in range(1, len(columns)):
        full_col = columns[col_idx]
        target_list = trimmed_cols[col_idx]
        target_set = set(full_col)
        is_init_c = _is_initial_col(full_col)
        prev_init_c = _is_initial_col(columns[col_idx - 1])
        prev2_init_c = _is_initial_col(columns[col_idx - 2]) if col_idx >= 2 else False
        in_acronym_span = (is_init_c and prev_init_c) or (prev_init_c and prev2_init_c)
        per_state_zero: dict[str, list[tuple[list[str], float, int, list[str]]]] = {}
        with_miss_next: list[tuple[list[str], float, int, list[str]]] = []

        def _push_zero(nxt_w: str, item: tuple[list[str], float, int, list[str]]) -> None:
            bucket = per_state_zero.get(nxt_w)
            if bucket is None:
                per_state_zero[nxt_w] = [item]
            elif len(bucket) < max_per_state:
                bucket.append(item)
                bucket.sort(key=lambda x: -x[1])
            elif item[1] > bucket[-1][1]:
                bucket[-1] = item
                bucket.sort(key=lambda x: -x[1])

        best_prev_zero: tuple[list[str], float, int, list[str]] | None = None

        for state in beam:
            path_words, curr_prob, miss_cnt, miss_words = state
            if miss_cnt == 0 and (best_prev_zero is None or curr_prob > best_prev_zero[1]):
                best_prev_zero = state
            last_w = path_words[-1]
            prob_neighbors = fwd_prob.get(last_w, {})

            found_direct = False
            if len(full_col) < len(prob_neighbors):
                for nxt in full_col:
                    p_edge = prob_neighbors.get(nxt)
                    if p_edge is not None:
                        found_direct = True
                        cand_item = (path_words + [nxt], curr_prob * p_edge, miss_cnt, miss_words)
                        if miss_cnt == 0:
                            _push_zero(nxt, cand_item)
                        else:
                            with_miss_next.append(cand_item)
            else:
                for nxt, p_edge in prob_neighbors.items():
                    if nxt in target_set:
                        found_direct = True
                        cand_item = (path_words + [nxt], curr_prob * p_edge, miss_cnt, miss_words)
                        if miss_cnt == 0:
                            _push_zero(nxt, cand_item)
                        else:
                            with_miss_next.append(cand_item)

            if not found_direct:
                for nxt in target_list[:4]:
                    backoff_p = 1e-5 * (max(dict_freq.get(nxt, 1), 1) / 100000.0)
                    cand_item = (path_words + [nxt], curr_prob * backoff_p, miss_cnt, miss_words)
                    if miss_cnt == 0:
                        _push_zero(nxt, cand_item)
                    else:
                        with_miss_next.append(cand_item)

                if (
                    allow_missing
                    and MAX_MISSING_GAP >= 1
                    and miss_cnt < 1
                    and not in_acronym_span
                ):
                    t_len = len(target_list)
                    for mid, p1 in fwd_prob_top.get(last_w, ()):
                        if is_class_token(mid):
                            continue
                        mid_neighbors = fwd_prob.get(mid, {})
                        if not mid_neighbors:
                            continue
                        if t_len < len(mid_neighbors):
                            for nxt in target_list:
                                p2 = mid_neighbors.get(nxt)
                                if p2 is not None:
                                    with_miss_next.append(
                                        (path_words + [mid, nxt], curr_prob * p1 * p2, miss_cnt + 1, miss_words + [mid])
                                    )
                        else:
                            for nxt, p2 in mid_neighbors.items():
                                if nxt in target_set:
                                    with_miss_next.append(
                                        (path_words + [mid, nxt], curr_prob * p1 * p2, miss_cnt + 1, miss_words + [mid])
                                    )

        # Luôn cho phép trạng thái dẫn đầu toàn cục chuyển tiếp backoff sang top-6 ứng viên đích
        # (tránh trường hợp từ đầu tần suất cao bị cắt mất đường tới ứng viên đích đúng chỉ vì vướng 1 cạnh nhiễu khác).
        if best_prev_zero is not None:
            bp_words, bp_prob, _, bp_miss = best_prev_zero
            for nxt in target_list[:6]:
                backoff_p = 1e-5 * (max(dict_freq.get(nxt, 1), 1) / 100000.0)
                _push_zero(nxt, (bp_words + [nxt], bp_prob * backoff_p, 0, bp_miss))

        zero_flat = [item for bucket in per_state_zero.values() for item in bucket]
        if not zero_flat and not with_miss_next and target_list:
            for path_words, curr_prob, miss_cnt, miss_words in beam[:5]:
                for nxt in target_list[:3]:
                    item = (path_words + [nxt], curr_prob * 1e-6, miss_cnt, miss_words)
                    if miss_cnt == 0:
                        zero_flat.append(item)
                    else:
                        with_miss_next.append(item)

        if not zero_flat and not with_miss_next:
            break

        zero_flat.sort(key=lambda x: -x[1])
        if with_miss_next:
            w2p = index.word_to_phrases

            def _has_kb_3gram(a: str, b: str, c: str) -> bool:
                if dict_freq.get(f"{a} {b} {c}", 0) > 0:
                    return True
                p0 = w2p.get(a)
                if p0:
                    p1 = w2p.get(b)
                    p2 = w2p.get(c)
                    if p1 and p2 and (p0 & p1 & p2):
                        return True
                return False

            def _has_kb_suffix(pw: list[str]) -> bool:
                if len(pw) >= 3 and _has_kb_3gram(pw[-3], pw[-2], pw[-1]):
                    return True
                if len(pw) >= 4 and _has_kb_3gram(pw[-4], pw[-3], pw[-2]):
                    return True
                return False

            with_miss_next.sort(
                key=lambda x: (
                    1 if _has_kb_suffix(x[0]) else 0,
                    x[1] ** (1.0 / max(len(x[0]), 1)),
                ),
                reverse=True,
            )
            seen_miss: set[tuple[str, ...]] = set()
            top_miss: list[tuple[list[str], float, int, list[str]]] = []
            for item in with_miss_next:
                k_pw = tuple(item[0])
                if k_pw not in seen_miss:
                    seen_miss.add(k_pw)
                    top_miss.append(item)
                    if len(top_miss) >= BEAM_WIDTH:
                        break
        else:
            top_miss = []
        if col_idx < len(columns) - 1 and is_init_c:
            best_per_word = [bucket[0] for bucket in per_state_zero.values()]
            best_per_word.sort(key=lambda x: -x[1])
            second_per_word = [bucket[1] for bucket in per_state_zero.values() if len(bucket) > 1]
            second_per_word.sort(key=lambda x: -x[1])
            beam = (
                best_per_word[: BEAM_WIDTH * max_per_state]
                + second_per_word[: BEAM_WIDTH // (max_per_state + 1)]
                + top_miss
            )
        else:
            beam = zero_flat[:BEAM_WIDTH] + top_miss

    has_direct_kb = bool(results_map)
    max_kb_len = max(index.len_to_phrases, default=6)

    def _kb_covered_count(ws: list[str], min_wlen: int = 2, extra_span: tuple[int, int] | None = None) -> int:
        n_w = len(ws)
        cov: set[int] = set(range(extra_span[0], extra_span[1])) if extra_span else set()
        for wlen in range(min(n_w, max_kb_len), min_wlen - 1, -1):
            for s_i in range(n_w - wlen + 1):
                if dict_freq.get(" ".join(ws[s_i : s_i + wlen]), 0) > 0:
                    cov.update(range(s_i, s_i + wlen))
                    if len(cov) == n_w:
                        return n_w
        return len(cov)

    strong_f = index.strong_kb_freq

    def _junction_kb_connected(ws: list[str], j_idx: int) -> bool:
        n_w = len(ws)
        if j_idx <= 0 or j_idx >= n_w:
            return False
        for wlen in range(min(n_w, max_kb_len), 2, -1):
            s_lo = max(0, j_idx + 1 - wlen)
            s_hi = min(j_idx, n_w - wlen + 1)
            for s_i in range(s_lo, s_hi):
                if dict_freq.get(" ".join(ws[s_i : s_i + wlen]), 0) > 0:
                    return True
        return False

    def _has_independent_boundary_phrases(ws: list[str], j_idx: int) -> tuple[bool, bool]:
        n_w = len(ws)
        left_ind = any(
            dict_freq.get(" ".join(ws[s_i:j_idx]), 0) >= strong_f
            for s_i in range(max(0, j_idx - max_kb_len), max(0, j_idx - 1))
        )
        right_ind = any(
            dict_freq.get(" ".join(ws[j_idx:e_i]), 0) >= strong_f
            for e_i in range(min(n_w + 1, j_idx + 2), min(n_w + 1, j_idx + max_kb_len + 1))
        )
        return left_ind, right_ind

    zero_beams = [pw for pw, _, mc, _ in beam if mc == 0]
    top_zero = zero_beams[0] if zero_beams else []
    min_zero_broken = min((index.count_broken_edges(pw) for pw in zero_beams), default=len(columns))
    best_zero_step_prob = max((index.step_prob(pw) for pw in zero_beams if pw), default=0.0)
    best_zero_cov2_ratio = max(
        (_kb_covered_count(pw, min_wlen=2) / len(pw) for pw in zero_beams if pw),
        default=0.0,
    )
    best_zero_cov3_ratio = max(
        (_kb_covered_count(pw, min_wlen=3) / len(pw) for pw in zero_beams if pw),
        default=0.0,
    )

    for path_words, m_prob, miss_cnt, miss_words in beam:
        phrase = " ".join(path_words)
        if phrase in results_map:
            continue
        promoted = False
        cand_broken = index.count_broken_edges(path_words) if (not has_direct_kb and miss_cnt == 1) else 0
        if (
            not has_direct_kb
            and miss_cnt == 1
            and len(miss_words) == 1
            and len(path_words) >= 3
            and cand_broken <= min_zero_broken
            and (cand_broken < min_zero_broken or index.step_prob(path_words) > best_zero_step_prob)
        ):
            mw = miss_words[0]
            n_pw = len(path_words)
            for gap_i in range(1, n_pw - 1):
                if path_words[gap_i] != mw:
                    continue
                if min_zero_broken == 0 and any(_junction_kb_connected(zw, gap_i) for zw in zero_beams):
                    continue
                # Khi cột gap_i-1 là âm tiết đầy đủ và đường 0-thiếu đầu bảng đã thông suốt tại biên đó,
                # không dùng nhánh đồng âm cụt đường để chèn từ thiếu
                if (
                    min_zero_broken == 0
                    and top_zero
                    and gap_i < len(top_zero)
                    and gap_i - 1 < len(columns)
                    and not _is_initial_col(columns[gap_i - 1])
                    and path_words[gap_i - 1] != top_zero[gap_i - 1]
                    and dict_freq.get(f"{top_zero[gap_i - 1]} {top_zero[gap_i]}", 0) >= strong_f
                ):
                    continue
                # Nếu X_0 và Y_0 đều là cụm KB độc lập quanh biên gap_i, phép bung không được phá hủy X_0 hay Y_0
                if top_zero and gap_i < len(top_zero):
                    l_ind, r_ind = _has_independent_boundary_phrases(top_zero, gap_i)
                    if l_ind and r_ind and (
                        path_words[:gap_i] != top_zero[:gap_i] or path_words[gap_i + 1 :] != top_zero[gap_i:]
                    ):
                        continue
                bridge_freq, bridge_span = index.score_kb_bridge(
                    path_words, gap_i, max_diff_k=max_diff_k
                )
                if bridge_freq > 0 and bridge_span is not None:
                    cov2_ratio = _kb_covered_count(path_words, min_wlen=2, extra_span=bridge_span) / n_pw
                    cov3_ratio = _kb_covered_count(path_words, min_wlen=3, extra_span=bridge_span) / n_pw
                    strictly_better = (cand_broken < min_zero_broken) or (cov3_ratio > best_zero_cov3_ratio)
                    if cov2_ratio >= best_zero_cov2_ratio and strictly_better:
                        results_map[phrase] = QueryMatch(
                            phrase,
                            bridge_freq,
                            miss_cnt,
                            0,
                            "",
                            miss_words,
                            markov_prob=m_prob,
                        )
                        promoted = True
                        break
        if not promoted:
            results_map[phrase] = QueryMatch(
                phrase, 0, miss_cnt, -1, "", miss_words, markov_prob=m_prob
            )

    return list(results_map.values())
