from factotum.core.extension import (
    get_token_class,
    is_valid_vietnamese_syllable,
    remove_accents,
    token_similarity,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import (
    EditSpan,
    QueryMatch,
    build_edit_spans,
    compute_confidence,
)


def _has_graph_edge(fwd_g: dict[str, dict[str, int]], u: str, v: str) -> bool:
    """Kiểm tra cạnh u -> v trong đồ thị, có xét cả lớp hình thái (<date>, <code>, <num>, <roman>)."""
    if not u or not v:
        return True
    u_nbrs = fwd_g.get(u)
    if u_nbrs:
        if u_nbrs.get(v, 0) > 0:
            return True
        cls_v = get_token_class(v)
        if cls_v:
            if u_nbrs.get(cls_v, 0) > 0:
                return True
            if cls_v in ("<code>", "<date>") and u_nbrs.get("<num>", 0) > 0:
                return True
    cls_u = get_token_class(u)
    if cls_u:
        if fwd_g.get(cls_u, {}).get(v, 0) > 0:
            return True
        if cls_u in ("<code>", "<date>") and fwd_g.get("<num>", {}).get(v, 0) > 0:
            return True
    return False


def _max_kb_phrase_len(index: GraphIndex) -> int:
    """Độ dài cụm lớn nhất tồn tại trong KB."""
    if not index.len_to_phrases:
        return 0
    return max(index.len_to_phrases)


def _resolve_edits(
    match: QueryMatch,
    orig_tokens: list[str],
    words: list[str],
) -> list[EditSpan]:
    """Lấy danh sách `EditSpan` từ `match.edits`, hoặc suy diễn bằng `build_edit_spans` nếu chưa gắn."""
    if match.edits:
        return match.edits
    if not orig_tokens or words == orig_tokens:
        return []
    return build_edit_spans(orig_tokens, words, missing_words=match.missing_words)


def _sentence_global_metrics(
    index: GraphIndex,
    words: list[str],
    *,
    allow_superstring: bool = True,
) -> tuple[int, int, int, int, int, float]:
    """
    Đánh giá tổng thể toàn câu theo các đoạn liên thông trong đồ thị `fwd_graph` (độ dài 3..min(n, max_kb_len)).
    Trả về (broken_edges, kb_covered, weighted_kb, kb4_plus_hits, kb3_hits, m_prob).
    """
    n = len(words)
    fwd = index.fwd_graph
    fwd_prob = index.fwd_prob
    dict_freq = index.dict_freq
    broken_edges = (
        sum(1 for i in range(n - 1) if not _has_graph_edge(fwd, words[i], words[i + 1]))
        if n >= 2
        else 0
    )
    covered = [False] * n
    weighted_kb = 0
    kb4_plus_hits = 0
    kb3_hits = 0

    upper_wlen = min(n, _max_kb_phrase_len(index))
    if upper_wlen >= 3:
        for i in range(n - 2):
            # Mở rộng liên tiếp từ vị trí i chừng nào cạnh liền kề vẫn tồn tại trong đồ thị
            max_j = min(n, i + upper_wlen)
            phrase_acc = words[i]
            for j in range(i + 1, max_j):
                if not _has_graph_edge(fwd, words[j - 1], words[j]):
                    break
                phrase_acc = f"{phrase_acc} {words[j]}"
                wlen = j - i + 1
                if wlen < 3:
                    continue
                freq = dict_freq.get(phrase_acc, 0)
                if freq == 0 and allow_superstring and wlen <= 4:
                    freq = index.lookup_real_phrase_score(words[i : j + 1])[0]
                if freq > 0:
                    for p in range(i, j + 1):
                        covered[p] = True
                    if wlen == 3:
                        kb3_hits += 1
                        weighted_kb += 3
                    else:
                        kb4_plus_hits += 1
                        weighted_kb += wlen * wlen

    kb_covered = sum(covered)

    m_prob = max(dict_freq.get(words[0], 1), 1) / 100000.0 if n >= 1 else 0.0
    for i in range(n - 1):
        m_prob *= fwd_prob.get(words[i], {}).get(words[i + 1], 1e-6)
    return broken_edges, kb_covered, weighted_kb, kb4_plus_hits, kb3_hits, m_prob


def _span_kb_context(
    index: GraphIndex,
    words: list[str],
    st: int,
    ed: int,
    *,
    allow_superstring: bool = True,
) -> tuple[int, int, int, int]:
    """
    Quét ngữ cảnh KB quanh đoạn [st, ed) theo chuỗi cạnh liên thông của đồ thị tới min(n, max_kb_len).
    Trả về (max_kb_len, boundary_cross_hits, total_hits, max_freq).
    """
    n = len(words)
    if n < 3:
        return 0, 0, 0, 0
    upper_wlen = min(n, _max_kb_phrase_len(index))
    if upper_wlen < 3:
        return 0, 0, 0, 0
    fwd = index.fwd_graph
    dict_freq = index.dict_freq

    # Tìm biên liên thông tối đa quanh [st, ed) dựa trên thông tin cạnh đồ thị đã biết
    left_limit = st
    while left_limit > 0 and (st - left_limit) < upper_wlen and _has_graph_edge(fwd, words[left_limit - 1], words[left_limit]):
        left_limit -= 1
    right_limit = ed
    while right_limit < n and (right_limit - ed) < upper_wlen and _has_graph_edge(fwd, words[right_limit - 1], words[right_limit]):
        right_limit += 1

    max_len = 0
    cross_hits = 0
    hits = 0
    max_freq = 0

    for a in range(left_limit, ed):
        max_b = min(right_limit, a + upper_wlen)
        phrase_acc = words[a]
        for b_idx in range(a + 1, max_b):
            if not _has_graph_edge(fwd, words[b_idx - 1], words[b_idx]):
                break
            phrase_acc = f"{phrase_acc} {words[b_idx]}"
            b = b_idx + 1
            if b <= st:
                continue
            wlen = b - a
            if wlen < 3:
                continue
            freq = dict_freq.get(phrase_acc, 0)
            if freq == 0 and allow_superstring and wlen <= 4:
                freq = index.lookup_real_phrase_score(words[a:b])[0]
            if freq > 0:
                hits += 1
                if a < st or b > ed:
                    cross_hits += 1
                if wlen > max_len:
                    max_len = wlen
                if freq > max_freq:
                    max_freq = freq
    return max_len, cross_hits, hits, max_freq


def _review_acronym_edits(
    index: GraphIndex,
    words: list[str],
    edits: list[EditSpan],
) -> bool:
    """Hậu kiểm tối đa 2 lượt các khối viết tắt (`kind == 'acronym'`) dựa trên ngữ cảnh toàn câu và biên đồ thị."""
    n = len(words)
    acronym_edits = [e for e in edits if e.kind == "acronym" and 0 <= e.start < e.end <= n]
    if not acronym_edits:
        return False

    fwd_g = index.fwd_graph
    dict_freq = index.dict_freq
    changed = False

    max_passes = 2 if len(acronym_edits) > 1 else 1
    med_kb_freq = index.medium_kb_freq
    for _pass in range(max_passes):
        pass_changed = False
        cur_metrics: tuple[int, int, int, int, int, float] | None = None
        for ed_span in acronym_edits:
            orig, st, ed = ed_span.orig, ed_span.start, ed_span.end
            span_len = ed - st
            left_b = words[st - 1] if st > 0 else ""
            right_b = words[ed] if ed < n else ""
            cur_block_freq = dict_freq.get(" ".join(words[st:ed]), 0)
            cur_l_ok = _has_graph_edge(fwd_g, left_b, words[st])
            cur_r_ok = _has_graph_edge(fwd_g, words[ed - 1], right_b)
            if cur_block_freq >= med_kb_freq and (span_len >= 3 or (cur_l_ok and cur_r_ok)):
                continue
            if span_len >= 3:
                cur_l_dim_freq = dict_freq.get(" ".join(words[st - 1 : ed]), 0) if st > 0 else 0
                cur_r_dim_freq = dict_freq.get(" ".join(words[st : ed + 1]), 0) if ed < n else 0
                if max(cur_l_dim_freq, cur_r_dim_freq) >= med_kb_freq:
                    continue
            cand_phrases = index.mdis_acronym_extensions(orig, left_b, right_b)
            if not cand_phrases:
                continue
            cur_s_max_len, _, _, cur_s_freq = _span_kb_context(
                index, words, st, ed, allow_superstring=False
            )
            cur_anchored_dim = span_len >= 3 and cur_s_max_len > span_len
            cur_eff_freq = max(cur_block_freq, cur_s_freq if cur_anchored_dim else 0)
            if cur_eff_freq >= med_kb_freq and (cur_anchored_dim or span_len >= 3 or (cur_l_ok and cur_r_ok)):
                continue
            if cur_metrics is None:
                cur_metrics = _sentence_global_metrics(index, words, allow_superstring=False)

            def _block_key(
                trial_words: list[str],
                block_freq: int,
                metrics: tuple[int, int, int, int, int, float],
            ) -> tuple[int, int, int, int, int, int, int, int, int, float]:
                tb, tc, tw_kb, t4p, t3, tp = metrics
                l_ok = _has_graph_edge(fwd_g, left_b, trial_words[st])
                r_ok = _has_graph_edge(fwd_g, trial_words[ed - 1], right_b)
                both_broken = 1 if (left_b and right_b and not l_ok and not r_ok) else 0
                s_max_len, s_cross, _s_hits, s_freq = _span_kb_context(
                    index, trial_words, st, ed, allow_superstring=False
                )
                anchored_dim = (
                    1
                    if (
                        span_len >= 3
                        and s_max_len > span_len
                        and (not left_b or l_ok)
                        and (not right_b or r_ok)
                    )
                    else 0
                )
                eff_freq = max(block_freq, s_freq if anchored_dim else 0)
                strong_kb = 1 if eff_freq >= med_kb_freq else 0
                return (
                    strong_kb,
                    anchored_dim,
                    -both_broken,
                    s_cross,
                    -tb,
                    s_max_len,
                    tc,
                    tw_kb,
                    t4p + t3,
                    tp + min(eff_freq, 5000) * 1e-12 + min(block_freq, 5000) * 1e-13,
                )

            best_block: list[str] | None = None
            best_key = _block_key(words, cur_block_freq, cur_metrics)
            for c_words in cand_phrases[:12]:
                if c_words == words[st:ed]:
                    continue
                c_freq = dict_freq.get(" ".join(c_words), 0)
                trial = words[:st] + c_words + words[ed:]
                t_metrics = _sentence_global_metrics(index, trial, allow_superstring=False)
                t_key = _block_key(trial, c_freq, t_metrics)
                if t_key > best_key:
                    best_key = t_key
                    best_block = c_words
                    cur_metrics = t_metrics
            if best_block is not None:
                words[st:ed] = best_block
                changed = True
                pass_changed = True
        if not pass_changed:
            break
    return changed


def _review_single_edits(
    index: GraphIndex,
    words: list[str],
    edits: list[EditSpan],
    *,
    allow_kb_upgrade: bool,
) -> bool:
    """Hậu kiểm cầu nối ..A - F' - B.. tại các vị trí từ đơn không dấu hoặc viết tắt 1 chữ cái."""
    n = len(words)
    fwd_prob = index.fwd_prob
    ua_map = index.unaccented_map
    max_possible_len = min(n, _max_kb_phrase_len(index))
    changed = False

    single_targets = [
        (e.orig, e.start)
        for e in edits
        if e.kind in ("unaccented", "abbrev") and e.end - e.start == 1 and 0 <= e.start < n
    ]
    for orig, i in single_targets:
        ua_orig = remove_accents(orig)
        if orig != ua_orig:
            continue
        cur = words[i]
        left_w = words[i - 1] if i > 0 else ""
        right_w = words[i + 1] if i + 1 < n else ""

        p_l = fwd_prob.get(left_w, {}).get(cur, 0.0) if left_w else 1.0
        p_r = fwd_prob.get(cur, {}).get(right_w, 0.0) if right_w else 1.0
        cur_broken = (1 if (left_w and p_l == 0.0) else 0) + (1 if (right_w and p_r == 0.0) else 0)
        edge_broken = cur_broken > 0

        cur_kb_len, cur_kb_cross, cur_kb_hits, cur_kb_freq = (
            _span_kb_context(index, words, i, i + 1) if allow_kb_upgrade else (0, 0, 0, 0)
        )
        if not edge_broken and not (allow_kb_upgrade and n >= 3 and cur_kb_len < max_possible_len):
            continue

        cands = ua_map.get(orig) or ua_map.get(ua_orig) or ()

        cur_adj_kb_freq = max(
            index.dict_freq.get(f"{left_w} {cur}", 0) if left_w else 0,
            index.dict_freq.get(f"{cur} {right_w}", 0) if right_w else 0,
        )
        orig_is_syll = len(ua_orig) >= 2 and is_valid_vietnamese_syllable(ua_orig)
        best_f = ""
        best_score = (0, 0, 0, 0, 0.0)

        for cand in cands:
            if cand == cur:
                continue
            if orig_is_syll and remove_accents(cand) != ua_orig:
                continue
            cl = fwd_prob.get(left_w, {}).get(cand, 0.0) if left_w else 1.0
            cr = fwd_prob.get(cand, {}).get(right_w, 0.0) if right_w else 1.0
            cand_broken = (1 if (left_w and cl == 0.0) else 0) + (1 if (right_w and cr == 0.0) else 0)
            if cand_broken > cur_broken:
                continue

            c_kb_len, c_kb_cross, c_kb_hits, c_kb_freq = (0, 0, 0, 0)
            if allow_kb_upgrade and n >= 3 and cand_broken == 0:
                words[i] = cand
                c_kb_len, c_kb_cross, c_kb_hits, c_kb_freq = _span_kb_context(index, words, i, i + 1)
                words[i] = cur

            # Ưu tiên sửa cạnh đứt trước, sau đó nâng cấp ngữ cảnh KB quanh vị trí đang xét
            if edge_broken and cand_broken < cur_broken:
                cand_adj_kb_freq = max(
                    index.dict_freq.get(f"{left_w} {cand}", 0) if left_w else 0,
                    index.dict_freq.get(f"{cand} {right_w}", 0) if right_w else 0,
                )
                if cur_adj_kb_freq > 0 and c_kb_len <= cur_kb_len and cand_adj_kb_freq < cur_adj_kb_freq:
                    continue
                tier_gain = 3 if cand_broken == 0 else 2
                score = (tier_gain, c_kb_len, c_kb_hits, c_kb_freq, cl * cr if cand_broken == 0 else max(cl, cr))
            elif (
                allow_kb_upgrade
                and cand_broken == 0
                and cur_broken == 0
                and (c_kb_len, c_kb_cross, c_kb_hits) > (cur_kb_len, cur_kb_cross, cur_kb_hits)
                and (cur_kb_hits == 0 or c_kb_len > cur_kb_len)
            ):
                score = (1, c_kb_len, c_kb_hits, c_kb_freq, cl * cr)
            elif (
                allow_kb_upgrade
                and cand_broken == 1
                and cur_broken == 1
                and cur_adj_kb_freq == 0
                and cur_kb_len == 0
            ):
                cand_adj_kb_freq = max(
                    index.dict_freq.get(f"{left_w} {cand}", 0) if left_w else 0,
                    index.dict_freq.get(f"{cand} {right_w}", 0) if right_w else 0,
                )
                if cand_adj_kb_freq < index.strong_kb_freq:
                    continue
                score = (1, 2, 1, cand_adj_kb_freq, max(cl, cr))
            else:
                continue

            if score > best_score:
                best_score = score
                best_f = cand

        # Dò hàng xóm đồ thị để sửa lỗi chính tả khi đổi dấu trong ua_map không nối được cạnh.
        is_valid_ua = orig in ua_map
        if (
            not best_f
            and edge_broken
            and n >= 2
            and len(ua_orig) >= 2
            and ua_orig.isalpha()
        ):
            all_edges_broken = all(
                fwd_prob.get(words[j], {}).get(words[j + 1], 0.0) == 0.0 for j in range(n - 1)
            )
            if all_edges_broken or is_valid_ua:
                left_nbrs = index.fwd_graph.get(left_w, {}) if left_w else {}
                right_nbrs = index.rev_graph.get(right_w, {}) if right_w else {}
                if left_w and right_w:
                    nbr_pool = (
                        (set(left_nbrs) | set(right_nbrs))
                        if is_valid_ua
                        else ((set(left_nbrs) & set(right_nbrs)) or (set(left_nbrs) | set(right_nbrs)))
                    )
                elif left_w:
                    nbr_pool = left_nbrs
                else:
                    nbr_pool = right_nbrs

                min_valid_kb_freq = index.strong_kb_freq if not all_edges_broken else index.medium_kb_freq
                strong_f = index.strong_kb_freq
                right_starts_ind_kb = (
                    i + 3 < n
                    and index.dict_freq.get(f"{words[i + 1]} {words[i + 2]} {words[i + 3]}", 0) >= strong_f
                )
                max_len_diff = max(1, len(ua_orig) // 3)
                best_nbr = ""
                best_nbr_score = (0, 0.0, 0.0)
                for cand in nbr_pool:
                    if not cand or cand == cur or cand.startswith("<"):
                        continue
                    ua_cand = remove_accents(cand)
                    if abs(len(ua_cand) - len(ua_orig)) > max_len_diff:
                        continue
                    sim = token_similarity(ua_orig, ua_cand)
                    if not index.within_distortion_bound(ua_orig, ua_cand, sim, is_known_anchor=is_valid_ua):
                        continue
                    if is_valid_ua:
                        cl = fwd_prob.get(left_w, {}).get(cand, 0.0) if left_w else 1.0
                        cr = fwd_prob.get(cand, {}).get(right_w, 0.0) if right_w else 1.0
                        both_ok = (not left_w or cl > 0.0) and (not right_w or cr > 0.0)
                        ind_clause_ok = (
                            not both_ok
                            and cur_adj_kb_freq == 0
                            and left_w
                            and cl > 0.0
                            and i >= 2
                            and right_starts_ind_kb
                            and index.is_subsequence_expansion(ua_orig, ua_cand)
                            and index.dict_freq.get(f"{words[i - 2]} {left_w} {cand}", 0) >= strong_f
                        )
                        if not (both_ok or ind_clause_ok):
                            continue
                        trial = list(words)
                        trial[i] = cand
                        local_kb_freq = index.dict_freq.get(" ".join(trial), 0)
                        if both_ok and left_w and right_w and cur_broken == 2 and cur_adj_kb_freq == 0:
                            local_kb_freq = max(
                                local_kb_freq,
                                index.dict_freq.get(f"{left_w} {cand} {right_w}", 0),
                                index.dict_freq.get(f"{words[i - 2]} {left_w} {cand} {right_w}", 0) if i >= 2 else 0,
                                index.dict_freq.get(f"{left_w} {cand} {right_w} {words[i + 2]}", 0) if i + 2 < n else 0,
                            )
                        if both_ok and not right_w and i >= 2 and index.is_subsequence_expansion(ua_orig, ua_cand):
                            f3_l = index.dict_freq.get(f"{words[i - 2]} {left_w} {cand}", 0)
                            if f3_l > 0:
                                local_kb_freq = max(
                                    local_kb_freq,
                                    f3_l,
                                    index.dict_freq.get(f"{left_w} {cand}", 0),
                                )
                        if ind_clause_ok:
                            local_kb_freq = max(local_kb_freq, index.dict_freq.get(f"{words[i - 2]} {left_w} {cand}", 0))
                        if local_kb_freq < min_valid_kb_freq:
                            continue
                        cand_broken = 0 if both_ok else 1
                    else:
                        cl = fwd_prob.get(left_w, {}).get(cand, 0.0) if left_w else 1.0
                        cr = fwd_prob.get(cand, {}).get(right_w, 0.0) if right_w else 1.0
                        cand_broken = (1 if (left_w and cl == 0.0) else 0) + (1 if (right_w and cr == 0.0) else 0)
                        if cand_broken >= cur_broken:
                            continue
                    tier_gain = 3 if cand_broken == 0 else 2
                    nbr_score = (tier_gain, round(sim, 4), cl * cr if cand_broken == 0 else max(cl, cr))
                    if nbr_score > best_nbr_score:
                        best_nbr_score = nbr_score
                        best_nbr = cand
                if best_nbr:
                    best_f = best_nbr

            # Cấu hình hai từ lỗi liền kề (b/c1 - lỗi1 - lỗi2 hoặc lỗi2 - lỗi1 - b/c2):
            # Khi đổi dấu w1 thuộc ua_map[orig] nối được với b/c1 nhưng đứt với từ kề bên (do từ kề bên là neo giả lỗi2),
            # đồng thời dò hàng xóm đồ thị w2 cho từ kề bên trong cận biến dạng 1/max(|x|,|y|) tạo cụm 3 từ KB mạnh.
            if not best_f and is_valid_ua and cands and cur_adj_kb_freq == 0:
                strong_f = index.strong_kb_freq
                single_map = {pos_s: orig_s for orig_s, pos_s in single_targets}
                if left_w and i + 1 < n and (i + 1) in single_map:
                    cur_next = words[i + 1]
                    after_next = words[i + 2] if i + 2 < n else ""
                    next_adj_kb = max(
                        index.dict_freq.get(f"{cur} {cur_next}", 0),
                        index.dict_freq.get(f"{cur_next} {after_next}", 0) if after_next else 0,
                    )
                    if next_adj_kb == 0:
                        orig_next = remove_accents(single_map[i + 1])
                        best_pair_sc = (0, 0.0, 0.0)
                        best_pair: tuple[str, str] | None = None
                        for w1 in cands:
                            p_l1 = fwd_prob.get(left_w, {}).get(w1, 0.0)
                            if p_l1 == 0.0:
                                continue
                            for w2, p_12 in index.fwd_prob_top.get(w1, ()):
                                if w2.startswith("<"):
                                    continue
                                after_ind_kb = (
                                    i + 4 < n
                                    and index.dict_freq.get(f"{words[i + 2]} {words[i + 3]} {words[i + 4]}", 0) >= strong_f
                                )
                                if (not after_next and not all_edges_broken) or (
                                    after_next and fwd_prob.get(w2, {}).get(after_next, 0.0) == 0.0 and not after_ind_kb
                                ):
                                    continue
                                ua_w2 = remove_accents(w2)
                                sim2 = token_similarity(orig_next, ua_w2)
                                if not index.within_distortion_bound(orig_next, ua_w2, sim2, is_known_anchor=(orig_next in ua_map)):
                                    continue
                                if (orig_next in ua_map) and not index.is_subsequence_expansion(orig_next, ua_w2):
                                    continue
                                f3 = index.dict_freq.get(f"{left_w} {w1} {w2}", 0)
                                if f3 >= strong_f:
                                    sc = (f3, round(sim2, 4), p_l1 * p_12)
                                    if sc > best_pair_sc:
                                        best_pair_sc = sc
                                        best_pair = (w1, w2)
                        if best_pair is not None:
                            words[i], words[i + 1] = best_pair
                            changed = True
                            continue

        if best_f:
            words[i] = best_f
            changed = True
    return changed


def _try_bridge_broken_edge_by_overlapping_kb(
    index: GraphIndex,
    match: QueryMatch,
    words: list[str],
    edits: list[EditSpan],
) -> QueryMatch | None:
    """Chèn từ nối tại cạnh đứt khi tạo được hai cụm 3 từ KB gối đầu nhau phủ kín hai phía."""
    n = len(words)
    if n < 3 or match.missing_count > 0:
        return None
    fwd = index.fwd_graph
    broken_gaps = [i for i in range(1, n) if fwd.get(words[i - 1], {}).get(words[i], 0) == 0]
    if len(broken_gaps) != 1:
        return None
    gap_i = broken_gaps[0]
    if edits and not any(e.start <= gap_i <= e.end for e in edits):
        return None
    left_w, right_w = words[gap_i - 1], words[gap_i]
    strong_f = index.strong_kb_freq
    best_mid = ""
    best_score = (0, 0, 0)

    for mid_w, _p1 in index.fwd_prob_top.get(left_w, ()):
        if mid_w.startswith("<") or right_w not in fwd.get(mid_w, ()):
            continue
        trial = words[:gap_i] + [mid_w] + words[gap_i:]
        best_f, _span = index.score_kb_bridge(trial, gap_i, require_tail_on_subphrase=True)
        if best_f <= 0:
            continue
        e_min = min(fwd.get(left_w, {}).get(mid_w, 0), fwd.get(mid_w, {}).get(right_w, 0))
        sc = (best_f, e_min, 0)
        if sc > best_score:
            best_score = sc
            best_mid = mid_w

    if not best_mid:
        return None

    new_words = words[:gap_i] + [best_mid] + words[gap_i:]
    m_prob = index.markov_prob(new_words)
    shifted_edits: list[EditSpan] = []
    for e in edits:
        st = e.start + (1 if e.start >= gap_i else 0)
        ed = e.end + (1 if e.end > gap_i else 0)
        shifted_edits.append(EditSpan(orig=e.orig, start=st, end=ed, kind=e.kind))
    shifted_edits.append(EditSpan(orig="", start=gap_i, end=gap_i + 1, kind="missing_insert"))
    bridged = QueryMatch(
        phrase=" ".join(new_words),
        freq=best_score[0],
        missing_count=1,
        superstring_diff=0,
        source_phrase="",
        missing_words=[best_mid],
        context_tier=3,
        context_span_len=len(new_words) - 1,
        markov_prob=m_prob,
        edits=shifted_edits,
    )
    bridged.confidence = compute_confidence(bridged)
    return bridged


def _repair_broken_markov_bridge(
    index: GraphIndex,
    match: QueryMatch,
    orig_tokens: list[str],
    *,
    allow_kb_upgrade: bool = False,
    orig_by_phrase: dict[str, QueryMatch] | None = None,
) -> QueryMatch:
    """
    Hậu kiểm 2 chặng trên danh sách đoạn chỉnh sửa `match.edits`:
    - Chặng 1: Quét cửa sổ ngữ cảnh thích ứng tại từng đoạn `[e.start, e.end)` trong `match.edits`.
    - Chặng 2: Đánh giá toàn câu sau khi hiệu chỉnh cục bộ, giữ nguyên các từ neo.
    """
    if match.is_fallback or match.missing_count > 0:
        return match
    if (
        match.superstring_diff == 0
        and match.context_tier == 0
        and match.phrase in index.dict_freq
    ):
        return match

    words = match.phrase.split()
    n = len(words)
    if n < 2:
        return match

    edits = list(_resolve_edits(match, orig_tokens, words))
    if not edits and match.context_tier > 0:
        return match

    changed = False
    if allow_kb_upgrade:
        changed = _review_acronym_edits(index, words, edits) or changed
    if match.superstring_diff < 0:
        changed = _review_single_edits(index, words, edits, allow_kb_upgrade=allow_kb_upgrade) or changed

    if not changed:
        return match

    new_phrase = " ".join(words)
    if match.superstring_diff >= 0 and orig_by_phrase is not None:
        existing = orig_by_phrase.get(new_phrase)
        if existing is not None and existing.superstring_diff >= 0 and existing.context_tier == match.context_tier:
            return existing

    new_prob = index.markov_prob(words)
    new_freq = match.freq
    new_diff = match.superstring_diff
    new_src = match.source_phrase
    if match.superstring_diff >= 0:
        kb_f, kb_d, kb_src = index.lookup_real_phrase_score(words)
        if kb_f > 0:
            new_freq, new_diff, new_src = kb_f, kb_d, kb_src

    repaired = QueryMatch(
        phrase=new_phrase,
        freq=new_freq,
        missing_count=match.missing_count,
        superstring_diff=new_diff,
        source_phrase=new_src,
        missing_words=list(match.missing_words),
        context_tier=match.context_tier,
        context_span_len=match.context_span_len,
        markov_prob=new_prob,
        is_fallback=match.is_fallback,
        edits=edits,
    )
    repaired.confidence = compute_confidence(repaired)
    return repaired
