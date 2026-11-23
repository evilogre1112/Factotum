from factotum.core.extension import (
    get_token_class,
    is_class_token,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import (
    MAX_CONTEXT_WINDOW,
    MAX_CTX_CANDIDATES,
    MAX_SUPERSTRING_DIFF_K,
    QueryMatch,
    rank_key,
)
from factotum.engine.search import direct_kb_search, expand_query_variants, search_variant


def _has_contiguous_edges(fwd: dict[str, dict[str, int]], words: list[str]) -> bool:
    return all(words[i + 1] in fwd.get(words[i], ()) for i in range(len(words) - 1))


def _common_phrase_ids(index: GraphIndex, words: list[str]) -> set[int]:
    w2p = index.word_to_phrases
    sets: list[set[int]] = []
    for w in words:
        ps = w2p.get(w)
        if not ps:
            return set()
        sets.append(ps)
    if len(sets) == 1:
        return sets[0]
    sets.sort(key=len)
    return set.intersection(*sets)


def _extend_contiguous_states(
    index: GraphIndex,
    start_pids: set[int],
    anchor_words: list[str],
    extra_words: list[str],
    *,
    extend_right: bool,
    max_diff_k: int,
) -> list[tuple[int, set[int], int, int, str]]:
    """Mở rộng cửa sổ liên tiếp sang trái hoặc phải trên tập posting PID, trả về danh sách (step_len, pids, freq, diff, src)."""
    fwd = index.fwd_graph
    w2p = index.word_to_phrases
    states: list[tuple[int, set[int], int, int, str]] = []
    cur_pids = start_pids
    prev_w = anchor_words[-1] if extend_right else anchor_words[0]

    for step in range(1, len(extra_words) + 1):
        new_w = extra_words[step - 1] if extend_right else extra_words[-step]
        if step > 1:
            u, v = (prev_w, new_w) if extend_right else (new_w, prev_w)
            if v not in fwd.get(u, ()):
                break
        ps_new = w2p.get(new_w)
        if not ps_new:
            break
        cur_pids = cur_pids & ps_new
        if not cur_pids:
            break
        window = (anchor_words + extra_words[:step]) if extend_right else (extra_words[-step:] + anchor_words)
        cur_pids, freq, diff, src = index.filter_contiguous_pids(cur_pids, window, max_diff_k)
        if not cur_pids:
            break
        states.append((step, cur_pids, freq, diff, src))
        prev_w = new_w
    return states


def _lookup_bridged_score(
    index: GraphIndex,
    part_a: list[str],
    part_b: list[str],
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> tuple[int, int, str]:
    """Kiểm tra part_a + [1 từ nối] + part_b có cùng nằm trong 1 cụm KB thực tế hay không."""
    fwd = index.fwd_graph
    if not (_has_contiguous_edges(fwd, part_a) and _has_contiguous_edges(fwd, part_b)):
        return 0, -1, ""
    common_ids = _common_phrase_ids(index, part_a + part_b)
    if not common_ids:
        return 0, -1, ""

    a_len, b_len = len(part_a), len(part_b)
    span_total = a_len + 1 + b_len
    best_freq, best_diff, best_src = 0, max_diff_k + 1, ""
    phrases = index.phrases
    for pid in common_ids:
        p_str, p_words, p_freq = phrases[pid]
        diff = len(p_words) - span_total
        if not (0 <= diff <= max_diff_k):
            continue
        for idx in range(len(p_words) - span_total + 1):
            if p_words[idx : idx + a_len] == part_a and p_words[idx + a_len + 1 : idx + span_total] == part_b:
                if diff < best_diff or (diff == best_diff and p_freq > best_freq):
                    best_diff, best_freq, best_src = diff, p_freq, p_str
                break
    return (best_freq, best_diff + 1, best_src) if best_freq > 0 else (0, -1, "")


def _edge_weight_with_class(fwd: dict[str, dict[str, int]], u: str, v: str) -> int:
    """Lấy trọng số cạnh u -> v; nếu u hoặc v là mã/số OOV thì rơi về lớp hình thái (<date>, <code>, <num>, <roman>)."""
    if not u or not v:
        return 0
    w = fwd.get(u, {}).get(v, 0)
    if w > 0:
        return w
    for src, dst, is_right_cls in ((u, v, True), (u, v, False)):
        cls = get_token_class(dst if is_right_cls else src)
        if not cls:
            continue
        cls_keys = (cls, "<num>") if cls in ("<code>", "<date>") else (cls,)
        for ck in cls_keys:
            val = fwd.get(src, {}).get(ck, 0) if is_right_cls else fwd.get(ck, {}).get(dst, 0)
            if val > 0:
                return val
    return 0


def _graph_bridge_to_class_score(index: GraphIndex, f_words: list[str], right_tok: str) -> int:
    """Tính điểm nối cầu 2 bước F -> [C] -> <CLASS(right_tok)> (vd: 'nghị định' -> [số] -> <NUM>)."""
    cls_r = get_token_class(right_tok)
    if not cls_r or not f_words:
        return 0
    fwd, dict_freq = index.fwd_graph, index.dict_freq
    f_prefix = " ".join(f_words)
    best = 0
    for mid, _ in index.fwd_prob_top.get(f_words[-1], ())[:12]:
        kb_fc = dict_freq.get(f"{f_prefix} {mid}", 0)
        if kb_fc <= 0:
            continue
        mid_to_cls = fwd.get(mid, {}).get(cls_r, 0) or (
            fwd.get(mid, {}).get("<num>", 0) if cls_r in ("<code>", "<date>") else 0
        )
        if mid_to_cls > 0:
            best = max(best, min(kb_fc, mid_to_cls))
    return best


def is_normalized_anchor(
    index: GraphIndex,
    tok: str,
    sentence_has_accent: bool = False,
    prev_tok: str = "",
    next_tok: str = "",
) -> bool:
    """Xác định token có phải từ neo đã chuẩn hóa (số, từ có dấu trong KB, hoặc từ thanh ngang chuẩn)."""
    if is_class_token(tok) or tok.isdigit() or any(ch.isdigit() for ch in tok):
        return True
    has_accent = tok != remove_accents(tok)
    is_vn = is_valid_vietnamese_syllable(tok)
    if has_accent and tok in index.real_vocab and is_vn:
        return True
    if not (sentence_has_accent and len(tok) >= 2 and tok in index.real_vocab and is_vn):
        return False

    cands = index.unaccented_map.get(tok, [tok])
    fwd = index.fwd_graph
    prev_has_acc = bool(prev_tok and prev_tok != remove_accents(prev_tok))
    next_has_acc = bool(next_tok and next_tok != remove_accents(next_tok))
    prev_acc = prev_has_acc and prev_tok in index.real_vocab
    next_acc = next_has_acc and next_tok in index.real_vocab

    if prev_has_acc and next_has_acc:
        # Từ thanh ngang đứng giữa 2 từ đã có dấu: chỉ đổi sang biến thể có dấu c nếu [prev, c, next] có thật trong KB.
        return not any(
            fwd.get(prev_tok, {}).get(c, 0) > 0
            and fwd.get(c, {}).get(next_tok, 0) > 0
            and index.lookup_real_phrase_score([prev_tok, c, next_tok])[0] > 0
            for c in cands
            if c != tok
        )

    for is_prev, nb_acc, nb in ((True, prev_acc, prev_tok), (False, next_acc, next_tok)):
        if not nb_acc:
            continue
        w_self = fwd.get(nb, {}).get(tok, 0) if is_prev else fwd.get(tok, {}).get(nb, 0)
        w_alt = max(
            ((fwd.get(nb, {}).get(c, 0) if is_prev else fwd.get(c, {}).get(nb, 0)) for c in cands if c != tok),
            default=0,
        )
        if w_alt > w_self:
            return False
        if w_self > w_alt:
            return True

    if cands and cands[0] == tok:
        if prev_acc or next_acc:
            return True
        if prev_tok and index.unaccented_map.get(prev_tok, [""])[0] == prev_tok and fwd.get(prev_tok, {}).get(tok, 0) > 0:
            return True
        if next_tok and index.unaccented_map.get(next_tok, [""])[0] == next_tok and fwd.get(tok, {}).get(next_tok, 0) > 0:
            return True
    return False


def score_candidate_in_context(
    index: GraphIndex,
    f_match: QueryMatch,
    left_ctx: list[str],
    right_ctx: list[str],
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> tuple[int, int, int, int, str]:
    """Chấm điểm ứng viên F theo ngữ cảnh trái/phải (Tier 1: 2 đầu, Tier 2: 1 đầu, Tier 3: nối 1 từ, Tier 4: cạnh 1-gram, Tier 5: đơn lẻ)."""
    f_words = f_match.phrase.split()
    left_win = left_ctx[-MAX_CONTEXT_WINDOW:] if left_ctx else []
    right_win = right_ctx[:MAX_CONTEXT_WINDOW] if right_ctx else []
    fwd = index.fwd_graph

    e_left = fwd.get(left_win[-1], {}).get(f_words[0], 0) if left_win else 0
    e_right = fwd.get(f_words[-1], {}).get(right_win[0], 0) if right_win else 0

    base_pids = (
        _common_phrase_ids(index, f_words)
        if (e_left > 0 or e_right > 0) and f_match.superstring_diff != -1 and _has_contiguous_edges(fwd, f_words)
        else set()
    )

    left_states = (
        _extend_contiguous_states(index, base_pids, f_words, left_win, extend_right=False, max_diff_k=max_diff_k)
        if base_pids and e_left > 0
        else []
    )

    # Tier 1: Khớp ngữ cảnh cả 2 đầu (trái + F + phải trong cùng 1 cụm KB)
    if left_states and e_right > 0:
        best_t1: tuple[int, int, int, str] | None = None
        for l_len, l_pids, _, _, _ in reversed(left_states):
            anchor = left_win[-l_len:] + f_words
            for r_len, _, freq, diff, src in _extend_contiguous_states(
                index, l_pids, anchor, right_win, extend_right=True, max_diff_k=max_diff_k
            ):
                if freq > 0:
                    cand = (l_len + r_len, -diff, freq, src)
                    if best_t1 is None or cand > best_t1:
                        best_t1 = cand
        if best_t1 is not None:
            return (1, best_t1[0], best_t1[2], -best_t1[1], best_t1[3])

    # Tier 2: Khớp ngữ cảnh 1 đầu (trái + F hoặc F + phải trong 1 cụm KB)
    best_t2: tuple[int, int, int, str] | None = None
    right_states = (
        _extend_contiguous_states(index, base_pids, f_words, right_win, extend_right=True, max_diff_k=max_diff_k)
        if base_pids and e_right > 0
        else []
    )
    for states, win_len in ((left_states, len(left_win)), (right_states, len(right_win))):
        for s_len, _, freq, diff, src in reversed(states):
            if freq <= 0 or (win_len >= 2 and s_len == 1 and diff > 0):
                continue
            cand = (s_len, -diff, freq, src)
            if best_t2 is None or cand > best_t2:
                best_t2 = cand
    if best_t2 is not None:
        return (2, best_t2[0], best_t2[2], -best_t2[1], best_t2[3])

    if f_match.superstring_diff == -1:
        return (5, 0, 0, -1, "")

    # Tier 3: Nối cầu qua 1 từ đệm hoặc qua lớp hình thái (<NUM>, <CODE>, <DATE>)
    best_t3: tuple[int, int, int, str] | None = None
    for is_left, win in ((True, left_win), (False, right_win)):
        for span in range(len(win), 0, -1):
            part = win[-span:] if is_left else win[:span]
            freq, diff, src = (
                _lookup_bridged_score(index, part, f_words, max_diff_k)
                if is_left
                else _lookup_bridged_score(index, f_words, part, max_diff_k)
            )
            if freq > 0:
                cand = (span, -diff, freq, src)
                if best_t3 is None or cand > best_t3:
                    best_t3 = cand
                break
    if best_t3 is not None:
        return (3, best_t3[0], best_t3[2], -best_t3[1], best_t3[3])

    if right_win:
        bridge_cls = _graph_bridge_to_class_score(index, f_words, right_win[0])
        if bridge_cls > 0:
            return (3, 1, bridge_cls, 1, "")

    # Tier 4: Nối cạnh 1-gram trực tiếp hoặc qua lớp hình thái
    e_left_eff = e_left or (_edge_weight_with_class(fwd, left_win[-1], f_words[0]) if left_win else 0)
    e_right_eff = e_right or (_edge_weight_with_class(fwd, f_words[-1], right_win[0]) if right_win else 0)
    if left_win and right_win and e_left_eff > 0 and e_right_eff > 0:
        return (4, 2, min(e_left_eff, e_right_eff), -1, "")
    if e_left_eff > 0 or e_right_eff > 0:
        return (4, 1, max(e_left_eff, e_right_eff), -1, "")
    return (5, 0, 0, -1, "")


def _put_best_candidate(pool: dict[str, QueryMatch], cand: QueryMatch) -> None:
    prev = pool.get(cand.phrase)
    if prev is None or rank_key(cand) < rank_key(prev):
        pool[cand.phrase] = cand


def _filter_connected_oov_candidates(index: GraphIndex, candidates: list[QueryMatch]) -> list[QueryMatch]:
    """Luật đồ thị/KB thuần túy: giữ phương án tách OOV nếu khớp KB, hoặc nối thông liên tục kèm >= 1 cụm 3 từ KB."""
    fwd = index.fwd_graph
    return [
        m
        for m in candidates
        if m.superstring_diff >= 0
        or (
            len(ws := m.phrase.split()) >= 3
            and _has_contiguous_edges(fwd, ws)
            and any(index.lookup_real_phrase_score(ws[j : j + 3])[0] > 0 for j in range(len(ws) - 2))
        )
    ]


def query_with_context(
    index: GraphIndex,
    raw_tokens: list[str],
    top_k: int = 5,
    allow_missing: bool = True,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    sentence_has_accent: bool = False,
) -> list[QueryMatch]:
    """Khôi phục các đoạn chưa chuẩn (không dấu/viết tắt) dựa trên các đoạn neo chuẩn bao quanh."""
    segments: list[tuple[bool, list[str]]] = []
    n_raw = len(raw_tokens)
    for idx, tok in enumerate(raw_tokens):
        is_anc = is_normalized_anchor(
            index,
            tok,
            sentence_has_accent=sentence_has_accent,
            prev_tok=raw_tokens[idx - 1] if idx > 0 else "",
            next_tok=raw_tokens[idx + 1] if idx + 1 < n_raw else "",
        )
        if segments and segments[-1][0] == is_anc:
            segments[-1][1].append(tok)
        else:
            segments.append((is_anc, [tok]))

    sentence_beam: list[tuple[list[str], int, int, int, int, list[str], int, str, float]] = [
        ([], 0, 0, 0, 0, [], 0, "", 1.0)
    ]

    for seg_idx, (is_anc, seg_tokens) in enumerate(segments):
        if is_anc:
            sentence_beam = [
                (bw + seg_tokens, wt, tcs, tcf, tm, mw, ld, ls, mp)
                for bw, wt, tcs, tcf, tm, mw, ld, ls, mp in sentence_beam
            ]
            continue

        right_ctx = segments[seg_idx + 1][1] if (seg_idx + 1 < len(segments) and segments[seg_idx + 1][0]) else []
        seg_allow_missing = allow_missing and len(seg_tokens) <= 3
        f_variants = expand_query_variants(index, " ".join(seg_tokens))
        f_candidates_map: dict[str, QueryMatch] = {}
        left_anchors = {bw[-1] for bw, *_ in sentence_beam if bw}
        right_anchor = right_ctx[0] if right_ctx else ""

        for cols in f_variants:
            for l_anc in left_anchors:
                for m in direct_kb_search(index, [[l_anc]] + cols, allow_missing=False, max_diff_k=max_diff_k):
                    f_sub = " ".join(m.phrase.split()[1:])
                    if f_sub:
                        _put_best_candidate(
                            f_candidates_map,
                            QueryMatch(f_sub, m.freq, 0, m.superstring_diff, m.source_phrase, [], markov_prob=m.markov_prob),
                        )
            if right_anchor:
                for m in direct_kb_search(index, cols + [[right_anchor]], allow_missing=False, max_diff_k=max_diff_k):
                    f_sub = " ".join(m.phrase.split()[:-1])
                    if f_sub:
                        _put_best_candidate(
                            f_candidates_map,
                            QueryMatch(f_sub, m.freq, 0, m.superstring_diff, m.source_phrase, [], markov_prob=m.markov_prob),
                        )
            for m in sorted(search_variant(index, cols, allow_missing=seg_allow_missing, max_diff_k=max_diff_k), key=rank_key)[:MAX_CTX_CANDIDATES]:
                _put_best_candidate(f_candidates_map, m)

        f_candidates = sorted(f_candidates_map.values(), key=rank_key)[: MAX_CTX_CANDIDATES * 3]
        is_unmapped_oov_in_accented = (
            sentence_has_accent
            and len(seg_tokens) == 1
            and seg_tokens[0] not in index.unaccented_map
            and not index.get_acronym_pids(seg_tokens[0])
        )
        if is_unmapped_oov_in_accented:
            f_candidates = _filter_connected_oov_candidates(index, f_candidates)
        if not f_candidates:
            sentence_beam = [
                (bw + seg_tokens, max(wt, 5), tcs, tcf, tm, mw, -1, "", mp * 1e-6)
                for bw, wt, tcs, tcf, tm, mw, _ld, _ls, mp in sentence_beam
            ]
            continue

        left_seg_toks = segments[seg_idx - 1][1] if (seg_idx > 0 and segments[seg_idx - 1][0]) else []
        left_is_code = bool(left_seg_toks and (is_class_token(left_seg_toks[-1]) or any(ch.isdigit() for ch in left_seg_toks[-1])))
        right_is_code = bool(right_ctx and (is_class_token(right_ctx[0]) or any(ch.isdigit() for ch in right_ctx[0])))
        ctx_can_stand_alone = (
            (not left_seg_toks or left_is_code or (len(left_seg_toks) >= 2 and index.dict_freq.get(" ".join(left_seg_toks), 0) > 0))
            and (not right_ctx or right_is_code or (len(right_ctx) >= 2 and index.dict_freq.get(" ".join(right_ctx), 0) > 0))
        )

        next_beam: list[tuple[list[str], int, int, int, int, list[str], int, str, float]] = []
        for bw, wt, tcs, tcf, tm, mw, _ld, _ls, mp in sentence_beam:
            left_ctx = bw
            scored: list[tuple[tuple, QueryMatch, tuple[int, int, int, int, str]]] = []
            for f_m in f_candidates:
                ctx_info = score_candidate_in_context(index, f_m, left_ctx, right_ctx, max_diff_k=max_diff_k)
                tier, span_len, ctx_freq, ctx_diff, _src = ctx_info
                diff_rank = ctx_diff if ctx_diff >= 0 else (f_m.superstring_diff if f_m.superstring_diff >= 0 else 999)
                f_stands = ctx_can_stand_alone and tier >= 4 and f_m.superstring_diff == 0 and len(f_m.phrase.split()) >= 2
                sort_k = (
                    1 if f_m.superstring_diff == -1 else 0,
                    f_m.missing_count,
                    4 if f_stands else tier,
                    -span_len if tier <= 3 else 0,
                    diff_rank,
                    -(ctx_freq if tier <= 3 else f_m.freq),
                    -(f_m.freq if tier <= 3 else ctx_freq),
                    tier,
                    -f_m.markov_prob,
                )
                scored.append((sort_k, f_m, ctx_info))

            scored.sort(key=lambda x: x[0])
            keep_oov = (
                is_unmapped_oov_in_accented
                and (left_ctx or right_ctx)
                and scored
                and scored[0][2][0] >= 4
                and len(scored[0][1].phrase.split()) <= 2
            ) or (
                left_is_code
                and scored
                and scored[0][2][0] >= 5
                and len(seg_tokens) == 1
                and not is_valid_vietnamese_syllable(seg_tokens[0])
            )
            if keep_oov:
                next_beam.append((bw + seg_tokens, max(wt, 5), tcs, tcf, tm, mw, -1, "", mp * 1e-6))
                continue

            for _, f_m, (tier, span_len, ctx_freq, ctx_diff, ctx_src) in scored[: max(top_k, 5)]:
                f_words = f_m.phrase.split()
                f_stands = ctx_can_stand_alone and tier >= 4 and f_m.superstring_diff == 0 and len(f_words) >= 2
                b_prob = f_m.markov_prob if f_m.markov_prob > 0 else 1e-6
                if tier >= 4 and not f_stands:
                    long_f = len(f_words) >= 4 and len(seg_tokens) == 1
                    if left_ctx:
                        p_l = index.fwd_prob.get(left_ctx[-1], {}).get(f_words[0], 0.0)
                        b_prob *= (1.0 if p_l > 0.0 else 1e-8) if long_f else (p_l or 1e-8)
                    if right_ctx:
                        p_r = index.fwd_prob.get(f_words[-1], {}).get(right_ctx[0], 0.0)
                        b_prob *= (1.0 if p_r > 0.0 else 1e-8) if long_f else (p_r or 1e-8)
                next_beam.append(
                    (
                        bw + f_words,
                        wt + (4 if f_stands else tier),
                        tcs + (span_len if tier <= 3 else 0),
                        tcf + (ctx_freq if tier <= 3 else f_m.freq),
                        tm + f_m.missing_count,
                        mw + f_m.missing_words,
                        ctx_diff if tier <= 3 else (0 if f_stands else -1),
                        ctx_src if tier <= 3 else (f_m.source_phrase if f_stands else ""),
                        mp * b_prob,
                    )
                )

        next_beam.sort(
            key=lambda x: (
                0 if x[6] >= 0 else 1,
                x[1],
                -x[2],
                x[4],
                -x[3] if x[6] >= 0 else 0,
                -x[8],
            )
        )
        sentence_beam = next_beam[: max(top_k, 6)]

    results: list[QueryMatch] = []
    seen: set[str] = set()
    for bw, wt, tcs, tcf, tm, mw, ld, ls, mp in sentence_beam:
        full_phrase = " ".join(bw)
        if full_phrase in seen:
            continue
        seen.add(full_phrase)
        results.append(
            QueryMatch(
                phrase=full_phrase,
                freq=tcf,
                missing_count=tm,
                superstring_diff=ld,
                source_phrase=ls,
                missing_words=mw,
                context_tier=wt,
                context_span_len=tcs,
                markov_prob=mp,
            )
        )

    return results[:top_k]
