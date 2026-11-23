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


def is_normalized_anchor(
    index: GraphIndex,
    tok: str,
    sentence_has_accent: bool = False,
    prev_tok: str = "",
    next_tok: str = "",
) -> bool:
    """Xác định token có phải từ neo đã chuẩn hóa (số, từ có dấu trong KB, hoặc từ không dấu chuẩn có cạnh mạnh)."""
    if is_class_token(tok) or tok.isdigit() or any(ch.isdigit() for ch in tok):
        return True
    if tok != remove_accents(tok) and tok in index.real_vocab and is_valid_vietnamese_syllable(tok):
        return True
    if not (
        sentence_has_accent
        and len(tok) >= 2
        and tok in index.real_vocab
        and is_valid_vietnamese_syllable(tok)
    ):
        return False

    cands = index.unaccented_map.get(tok, [tok])
    prev_acc = bool(
        prev_tok and prev_tok != remove_accents(prev_tok) and prev_tok in index.real_vocab
    )
    next_acc = bool(
        next_tok and next_tok != remove_accents(next_tok) and next_tok in index.real_vocab
    )
    fwd = index.fwd_graph

    next_has_accent = bool(next_tok and next_tok != remove_accents(next_tok))
    prev_has_accent = bool(prev_tok and prev_tok != remove_accents(prev_tok))
    if (prev_acc or prev_has_accent) and (next_acc or next_has_accent):
        # Khi từ thanh ngang đứng giữa 2 từ đã có dấu (vd: "mất bao lâu", "tối đa bao lâu"),
        # chỉ đổi sang biến thể có dấu c nếu cả cụm 3 từ [prev_tok, c, next_tok] thực sự tồn tại trong KB.
        real_3gram_alt = any(
            fwd.get(prev_tok, {}).get(c, 0) > 0
            and fwd.get(c, {}).get(next_tok, 0) > 0
            and index.lookup_real_phrase_score([prev_tok, c, next_tok])[0] > 0
            for c in cands
            if c != tok
        )
        return not real_3gram_alt

    if prev_acc:
        w_self = fwd.get(prev_tok, {}).get(tok, 0)
        w_alt = max((fwd.get(prev_tok, {}).get(c, 0) for c in cands if c != tok), default=0)
        if w_alt > w_self:
            return False
        if w_self > 0 and w_self > w_alt:
            return True

    if next_acc:
        w_self = fwd.get(tok, {}).get(next_tok, 0)
        w_alt = max((fwd.get(c, {}).get(next_tok, 0) for c in cands if c != tok), default=0)
        if w_alt > w_self:
            return False
        if w_self > 0 and w_self > w_alt:
            return True

    if cands and cands[0] == tok:
        if prev_acc or next_acc:
            return True
        if (
            prev_tok
            and index.unaccented_map.get(prev_tok, [""])[0] == prev_tok
            and fwd.get(prev_tok, {}).get(tok, 0) > 0
        ):
            return True
        if (
            next_tok
            and index.unaccented_map.get(next_tok, [""])[0] == next_tok
            and fwd.get(tok, {}).get(next_tok, 0) > 0
        ):
            return True
    return False


def lookup_bridged_left_score(
    index: GraphIndex,
    left_part: list[str],
    f_words: list[str],
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> tuple[int, int, str]:
    """Kiểm tra left_part + [1 từ nối, vd 'và'] + f_words có cùng nằm trong 1 cụm KB thực tế không."""
    fwd = index.fwd_graph
    for i in range(len(left_part) - 1):
        nbrs = fwd.get(left_part[i])
        if not nbrs or left_part[i + 1] not in nbrs:
            return 0, -1, ""
    for i in range(len(f_words) - 1):
        nbrs = fwd.get(f_words[i])
        if not nbrs or f_words[i + 1] not in nbrs:
            return 0, -1, ""

    all_w = left_part + f_words
    w2p = index.word_to_phrases
    posting_sets: list[set[int]] = []
    for w in all_w:
        ps = w2p.get(w)
        if not ps:
            return 0, -1, ""
        posting_sets.append(ps)
    posting_sets.sort(key=len)
    common_ids = set.intersection(*posting_sets)
    if not common_ids:
        return 0, -1, ""

    l_len = len(left_part)
    f_len = len(f_words)
    span_total = l_len + 1 + f_len
    best_freq = 0
    best_diff = max_diff_k + 1
    best_src = ""
    phrases = index.phrases
    for pid in common_ids:
        p_str, p_words, p_freq = phrases[pid]
        diff = len(p_words) - span_total
        if diff < 0 or diff > max_diff_k:
            continue
        for idx in range(len(p_words) - span_total + 1):
            if (
                p_words[idx : idx + l_len] == left_part
                and p_words[idx + l_len + 1 : idx + span_total] == f_words
            ):
                if diff < best_diff or (diff == best_diff and p_freq > best_freq):
                    best_diff = diff
                    best_freq = p_freq
                    best_src = p_str
                break
    if best_freq > 0:
        return best_freq, best_diff + 1, best_src
    return 0, -1, ""


def _edge_weight_with_class(fwd: dict[str, dict[str, int]], u: str, v: str) -> int:
    """Lấy trọng số cạnh u -> v; nếu u hoặc v là mã/số OOV thì rơi về lớp hình thái (<date>, <code>, <num>, <roman>)."""
    if not u or not v:
        return 0
    w = fwd.get(u, {}).get(v, 0)
    if w > 0:
        return w
    cls_v = get_token_class(v)
    if cls_v:
        u_nbrs = fwd.get(u, {})
        w = u_nbrs.get(cls_v, 0) or (u_nbrs.get("<num>", 0) if cls_v in ("<code>", "<date>") else 0)
        if w > 0:
            return w
    cls_u = get_token_class(u)
    if cls_u:
        w = fwd.get(cls_u, {}).get(v, 0) or (fwd.get("<num>", {}).get(v, 0) if cls_u in ("<code>", "<date>") else 0)
        if w > 0:
            return w
    return 0


def lookup_bridged_right_score(
    index: GraphIndex,
    f_words: list[str],
    right_part: list[str],
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> tuple[int, int, str]:
    """Kiểm tra f_words + [1 từ nối] + right_part có cùng nằm trong 1 cụm KB thực tế không."""
    fwd = index.fwd_graph
    for i in range(len(f_words) - 1):
        nbrs = fwd.get(f_words[i])
        if not nbrs or f_words[i + 1] not in nbrs:
            return 0, -1, ""
    for i in range(len(right_part) - 1):
        nbrs = fwd.get(right_part[i])
        if not nbrs or right_part[i + 1] not in nbrs:
            return 0, -1, ""

    all_w = f_words + right_part
    w2p = index.word_to_phrases
    posting_sets: list[set[int]] = []
    for w in all_w:
        ps = w2p.get(w)
        if not ps:
            return 0, -1, ""
        posting_sets.append(ps)
    posting_sets.sort(key=len)
    common_ids = set.intersection(*posting_sets)
    if not common_ids:
        return 0, -1, ""

    f_len = len(f_words)
    r_len = len(right_part)
    span_total = f_len + 1 + r_len
    best_freq = 0
    best_diff = max_diff_k + 1
    best_src = ""
    phrases = index.phrases
    for pid in common_ids:
        p_str, p_words, p_freq = phrases[pid]
        diff = len(p_words) - span_total
        if diff < 0 or diff > max_diff_k:
            continue
        for idx in range(len(p_words) - span_total + 1):
            if (
                p_words[idx : idx + f_len] == f_words
                and p_words[idx + f_len + 1 : idx + span_total] == right_part
            ):
                if diff < best_diff or (diff == best_diff and p_freq > best_freq):
                    best_diff = diff
                    best_freq = p_freq
                    best_src = p_str
                break
    if best_freq > 0:
        return best_freq, best_diff + 1, best_src
    return 0, -1, ""


def _graph_bridge_to_class_score(index: GraphIndex, f_words: list[str], right_tok: str) -> int:
    """
    Tính điểm nối cầu 2 bước F -> [C] -> <CLASS(right_tok)> (vd: 'nghị định' -> [số] -> <NUM>).
    Chỉ tính khi cụm (F + C) là cụm có thật trong KB và cạnh C -> <CLASS> mạnh.
    """
    cls_r = get_token_class(right_tok)
    if not cls_r or not f_words:
        return 0
    fwd = index.fwd_graph
    dict_freq = index.dict_freq
    last_w = f_words[-1]
    f_prefix = " ".join(f_words)
    best = 0
    for mid, _ in index.fwd_prob_top.get(last_w, ())[:12]:
        kb_fc = dict_freq.get(f"{f_prefix} {mid}", 0)
        if kb_fc <= 0:
            continue
        mid_to_cls = fwd.get(mid, {}).get(cls_r, 0) or (
            fwd.get(mid, {}).get("<num>", 0) if cls_r in ("<code>", "<date>") else 0
        )
        if mid_to_cls > 0:
            score = min(kb_fc, mid_to_cls)
            if score > best:
                best = score
    return best


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
    w2p = index.word_to_phrases

    e_left = fwd.get(left_win[-1], {}).get(f_words[0], 0) if left_win else 0
    e_right = fwd.get(f_words[-1], {}).get(right_win[0], 0) if right_win else 0

    base_pids: set[int] = set()
    if (e_left > 0 or e_right > 0) and f_match.superstring_diff != -1:
        f_feasible = True
        for i in range(len(f_words) - 1):
            nbrs = fwd.get(f_words[i])
            if not nbrs or f_words[i + 1] not in nbrs:
                f_feasible = False
                break
        if f_feasible:
            f_sets: list[set[int]] = []
            for w in f_words:
                ps = w2p.get(w)
                if not ps:
                    f_sets.clear()
                    break
                f_sets.append(ps)
            if f_sets:
                if len(f_sets) == 1:
                    base_pids = f_sets[0]
                else:
                    f_sets.sort(key=len)
                    base_pids = set.intersection(*f_sets)

    left_states: list[tuple[int, set[int], int, int, str]] = []
    if base_pids and e_left > 0:
        cur_pids = base_pids
        prev_w = f_words[0]
        for l_len in range(1, len(left_win) + 1):
            new_w = left_win[-l_len]
            if l_len > 1:
                nbrs = fwd.get(new_w)
                if not nbrs or prev_w not in nbrs:
                    break
            ps_new = w2p.get(new_w)
            if not ps_new:
                break
            cur_pids = cur_pids & ps_new
            if not cur_pids:
                break
            window = left_win[-l_len:] + f_words
            cur_pids, freq, diff, src = index.filter_contiguous_pids(cur_pids, window, max_diff_k)
            if not cur_pids:
                break
            left_states.append((l_len, cur_pids, freq, diff, src))
            prev_w = new_w

    if left_states and e_right > 0:
        best_t1: tuple[int, int, int, str] | None = None
        for l_len, l_pids, _, _, _ in reversed(left_states):
            cur_pids = l_pids
            prev_w = f_words[-1]
            for r_len in range(1, len(right_win) + 1):
                new_w = right_win[r_len - 1]
                if r_len > 1:
                    nbrs = fwd.get(prev_w)
                    if not nbrs or new_w not in nbrs:
                        break
                ps_new = w2p.get(new_w)
                if not ps_new:
                    break
                cur_pids = cur_pids & ps_new
                if not cur_pids:
                    break
                window = left_win[-l_len:] + f_words + right_win[:r_len]
                cur_pids, freq, diff, src = index.filter_contiguous_pids(cur_pids, window, max_diff_k)
                if not cur_pids:
                    break
                if freq > 0:
                    span_len = l_len + r_len
                    cand = (span_len, -diff, freq, src)
                    if best_t1 is None or cand > best_t1:
                        best_t1 = cand
                prev_w = new_w
        if best_t1 is not None:
            return (1, best_t1[0], best_t1[2], -best_t1[1], best_t1[3])

    best_t2: tuple[int, int, int, str] | None = None
    for l_len, _, freq, diff, src in reversed(left_states):
        if best_t2 is not None and l_len < best_t2[0]:
            break
        if freq <= 0:
            continue
        if len(left_win) >= 2 and l_len == 1 and diff > 0:
            continue
        cand = (l_len, -diff, freq, src)
        if best_t2 is None or cand > best_t2:
            best_t2 = cand

    if base_pids and e_right > 0:
        cur_pids = base_pids
        prev_w = f_words[-1]
        for r_len in range(1, len(right_win) + 1):
            new_w = right_win[r_len - 1]
            if r_len > 1:
                nbrs = fwd.get(prev_w)
                if not nbrs or new_w not in nbrs:
                    break
            ps_new = w2p.get(new_w)
            if not ps_new:
                break
            cur_pids = cur_pids & ps_new
            if not cur_pids:
                break
            window = f_words + right_win[:r_len]
            cur_pids, freq, diff, src = index.filter_contiguous_pids(cur_pids, window, max_diff_k)
            if not cur_pids:
                break
            if freq > 0 and not (len(right_win) >= 2 and r_len == 1 and diff > 0):
                cand = (r_len, -diff, freq, src)
                if best_t2 is None or cand > best_t2:
                    best_t2 = cand
            prev_w = new_w

    if best_t2 is not None:
        return (2, best_t2[0], best_t2[2], -best_t2[1], best_t2[3])

    if f_match.superstring_diff == -1:
        return (5, 0, 0, -1, "")

    best_t3: tuple[int, int, int, str] | None = None
    for l_len in range(len(left_win), 0, -1):
        left_part = left_win[-l_len:]
        freq, diff, src = lookup_bridged_left_score(index, left_part, f_words, max_diff_k=max_diff_k)
        if freq > 0:
            cand = (l_len, -diff, freq, src)
            if best_t3 is None or cand > best_t3:
                best_t3 = cand
            break

    for r_len in range(len(right_win), 0, -1):
        right_part = right_win[:r_len]
        freq, diff, src = lookup_bridged_right_score(index, f_words, right_part, max_diff_k=max_diff_k)
        if freq > 0:
            cand = (r_len, -diff, freq, src)
            if best_t3 is None or cand > best_t3:
                best_t3 = cand
            break

    if best_t3 is not None:
        return (3, best_t3[0], best_t3[2], -best_t3[1], best_t3[3])

    # Nối cầu 2 bước qua lớp hình thái (<NUM>, <CODE>, <DATE>): F -> [C] -> <CLASS(B)>
    if right_win:
        bridge_cls = _graph_bridge_to_class_score(index, f_words, right_win[0])
        if bridge_cls > 0:
            return (3, 1, bridge_cls, 1, "")

    e_left_eff = e_left or (_edge_weight_with_class(fwd, left_win[-1], f_words[0]) if left_win else 0)
    e_right_eff = e_right or (_edge_weight_with_class(fwd, f_words[-1], right_win[0]) if right_win else 0)
    if left_win and right_win and e_left_eff > 0 and e_right_eff > 0:
        return (4, 2, min(e_left_eff, e_right_eff), -1, "")
    if e_left_eff > 0 or e_right_eff > 0:
        return (4, 1, max(e_left_eff, e_right_eff), -1, "")

    return (5, 0, 0, -1, "")


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
        prev_t = raw_tokens[idx - 1] if idx > 0 else ""
        next_t = raw_tokens[idx + 1] if idx + 1 < n_raw else ""
        is_anc = is_normalized_anchor(
            index,
            tok,
            sentence_has_accent=sentence_has_accent,
            prev_tok=prev_t,
            next_tok=next_t,
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
            for i in range(len(sentence_beam)):
                bw, wt, tcs, tcf, tm, mw, ld, ls, mp = sentence_beam[i]
                sentence_beam[i] = (bw + seg_tokens, wt, tcs, tcf, tm, mw, ld, ls, mp)
            continue

        right_ctx: list[str] = []
        if seg_idx + 1 < len(segments) and segments[seg_idx + 1][0]:
            right_ctx = segments[seg_idx + 1][1]

        seg_allow_missing = allow_missing and len(seg_tokens) <= 3
        f_query = " ".join(seg_tokens)
        f_variants = expand_query_variants(index, f_query)
        f_candidates_map: dict[str, QueryMatch] = {}
        left_anchors = {bw[-1] for bw, *_ in sentence_beam if bw}
        right_anchor = right_ctx[0] if right_ctx else ""

        for cols in f_variants:
            for l_anc in left_anchors:
                for m in direct_kb_search(
                    index, [[l_anc]] + cols, allow_missing=False, max_diff_k=max_diff_k
                ):
                    f_sub = " ".join(m.phrase.split()[1:])
                    if f_sub:
                        cand_l = QueryMatch(
                            f_sub, m.freq, 0, m.superstring_diff, m.source_phrase, [], markov_prob=m.markov_prob
                        )
                        prev = f_candidates_map.get(f_sub)
                        if prev is None or rank_key(cand_l) < rank_key(prev):
                            f_candidates_map[f_sub] = cand_l

            if right_anchor:
                for m in direct_kb_search(
                    index, cols + [[right_anchor]], allow_missing=False, max_diff_k=max_diff_k
                ):
                    f_sub = " ".join(m.phrase.split()[:-1])
                    if f_sub:
                        cand_r = QueryMatch(
                            f_sub, m.freq, 0, m.superstring_diff, m.source_phrase, [], markov_prob=m.markov_prob
                        )
                        prev = f_candidates_map.get(f_sub)
                        if prev is None or rank_key(cand_r) < rank_key(prev):
                            f_candidates_map[f_sub] = cand_r

            var_matches = sorted(
                search_variant(index, cols, allow_missing=seg_allow_missing, max_diff_k=max_diff_k),
                key=rank_key,
            )[:MAX_CTX_CANDIDATES]
            for m in var_matches:
                prev = f_candidates_map.get(m.phrase)
                if prev is None or rank_key(m) < rank_key(prev):
                    f_candidates_map[m.phrase] = m

        f_candidates = sorted(f_candidates_map.values(), key=rank_key)[: MAX_CTX_CANDIDATES * 3]
        is_unmapped_oov_in_accented = (
            sentence_has_accent
            and len(seg_tokens) == 1
            and seg_tokens[0] not in index.unaccented_map
            and not index.get_acronym_pids(seg_tokens[0])
        )
        if is_unmapped_oov_in_accented and not any(m.superstring_diff >= 0 for m in f_candidates):
            f_candidates = []
        if not f_candidates:
            for i in range(len(sentence_beam)):
                bw, wt, tcs, tcf, tm, mw, ld, ls, mp = sentence_beam[i]
                sentence_beam[i] = (bw + seg_tokens, max(wt, 5), tcs, tcf, tm, mw, -1, "", mp * 1e-6)
            continue

        left_seg_toks = (
            segments[seg_idx - 1][1] if (seg_idx > 0 and segments[seg_idx - 1][0]) else []
        )
        left_seg_str = " ".join(left_seg_toks)
        right_seg_str = " ".join(right_ctx) if right_ctx else ""
        left_is_code = bool(left_seg_toks and (is_class_token(left_seg_toks[-1]) or any(ch.isdigit() for ch in left_seg_toks[-1])))
        right_is_code = bool(right_ctx and (is_class_token(right_ctx[0]) or any(ch.isdigit() for ch in right_ctx[0])))
        ctx_can_stand_alone = (
            (not left_seg_toks or left_is_code or (len(left_seg_toks) >= 2 and index.dict_freq.get(left_seg_str, 0) > 0))
            and (not right_ctx or right_is_code or (len(right_ctx) >= 2 and index.dict_freq.get(right_seg_str, 0) > 0))
        )

        next_beam: list[tuple[list[str], int, int, int, int, list[str], int, str, float]] = []
        for bw, wt, tcs, tcf, tm, mw, _ld, _ls, mp in sentence_beam:
            left_ctx = bw
            scored: list[tuple[tuple, QueryMatch, tuple[int, int, int, int, str]]] = []
            for f_m in f_candidates:
                ctx_info = score_candidate_in_context(
                    index, f_m, left_ctx, right_ctx, max_diff_k=max_diff_k
                )
                tier, span_len, ctx_freq, ctx_diff, _src = ctx_info
                diff_rank = (
                    ctx_diff
                    if ctx_diff >= 0
                    else (f_m.superstring_diff if f_m.superstring_diff >= 0 else 999)
                )
                f_is_fallback = 1 if f_m.superstring_diff == -1 else 0
                f_words_len = len(f_m.phrase.split())
                eff_tier = (
                    4
                    if (ctx_can_stand_alone and tier >= 4 and f_m.superstring_diff == 0 and f_words_len >= 2)
                    else tier
                )
                primary_freq = ctx_freq if tier <= 3 else f_m.freq
                secondary_freq = f_m.freq if tier <= 3 else ctx_freq
                sort_k = (
                    f_is_fallback,
                    f_m.missing_count,
                    eff_tier,
                    -span_len if tier <= 3 else 0,
                    diff_rank,
                    -primary_freq,
                    -secondary_freq,
                    tier,
                    -f_m.markov_prob,
                )
                scored.append((sort_k, f_m, ctx_info))

            scored.sort(key=lambda x: x[0])
            if is_unmapped_oov_in_accented and (left_ctx or right_ctx) and scored and scored[0][2][0] >= 4:
                # Nếu tách từ OOV (vd: "chip" -> "chi phí") không khớp ngữ cảnh xung quanh trong KB (tier >= 4), giữ nguyên từ OOV.
                next_beam.append((bw + seg_tokens, max(wt, 5), tcs, tcf, tm, mw, -1, "", mp * 1e-6))
                continue
            for _, f_m, (tier, span_len, ctx_freq, ctx_diff, ctx_src) in scored[: max(top_k, 5)]:
                f_words = f_m.phrase.split()
                f_stands_alone = (
                    ctx_can_stand_alone
                    and tier >= 4
                    and f_m.superstring_diff == 0
                    and len(f_words) >= 2
                )
                use_diff = ctx_diff if tier <= 3 else (0 if f_stands_alone else -1)
                use_src = ctx_src if tier <= 3 else (f_m.source_phrase if f_stands_alone else "")
                use_freq = ctx_freq if tier <= 3 else f_m.freq
                use_tier = 4 if f_stands_alone else tier
                b_prob = f_m.markov_prob if f_m.markov_prob > 0 else 1e-6
                if tier >= 4 and not f_stands_alone:
                    if left_ctx:
                        b_prob *= index.fwd_prob.get(left_ctx[-1], {}).get(f_words[0], 1e-8)
                    if right_ctx:
                        b_prob *= index.fwd_prob.get(f_words[-1], {}).get(right_ctx[0], 1e-8)
                next_beam.append(
                    (
                        bw + f_words,
                        wt + use_tier,
                        tcs + (span_len if tier <= 3 else 0),
                        tcf + use_freq,
                        tm + f_m.missing_count,
                        mw + f_m.missing_words,
                        use_diff,
                        use_src,
                        mp * b_prob,
                    )
                )

        next_beam.sort(key=lambda x: (x[1], -x[2], x[4], -x[3]))
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
