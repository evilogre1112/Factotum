from itertools import product

from factotum.core.extension import (
    clean_word,
    is_class_token,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import (
    MAX_CANDIDATES_PER_KEY,
    MAX_MISSING_GAP,
    MAX_QUERY_VARIANTS,
    MAX_SPLITS_PER_TOKEN,
    MAX_SUPERSTRING_DIFF_K,
    MAX_SYLLABLE_LEN,
    QueryMatch,
    rank_key,
)


def split_sticky_token(index: GraphIndex, token: str) -> list[list[str]]:
    """Tách token dính liền (vd: 'btup') thành các tổ hợp mảnh hợp lệ trong UnaccentedMap."""
    n = len(token)
    results: list[list[str]] = []
    path: list[str] = []
    ua_map = index.unaccented_map
    max_piece_len = index.max_vocab_word_len
    if n == 0 or n > max_piece_len * 3:
        return []

    def backtrack(start: int) -> None:
        if len(results) >= MAX_SPLITS_PER_TOKEN:
            return
        if start == n:
            results.append(path.copy())
            return

        max_end = min(n, start + max_piece_len)
        for end in range(max_end, start, -1):
            piece = token[start:end]
            if piece in ua_map and (len(piece) == 1 or is_valid_vietnamese_syllable(piece)):
                path.append(piece)
                backtrack(end)
                path.pop()
                if len(results) >= MAX_SPLITS_PER_TOKEN:
                    return

    backtrack(0)
    results.sort(key=len)
    return results


def expand_query_variants(index: GraphIndex, raw_query: str) -> list[list[list[str]]]:
    """Chuyển câu truy vấn thành tối đa MAX_QUERY_VARIANTS phương án chuỗi cột ứng viên."""
    raw_tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
    if not raw_tokens:
        return []

    ua_map = index.unaccented_map
    real_vocab = index.real_vocab
    per_token_options: list[list[list[list[str]]]] = []

    for tok in raw_tokens:
        if is_class_token(tok):
            per_token_options.append([[[tok]]])
            continue
        no_accent = remove_accents(tok)
        has_accent = tok != no_accent
        is_vn = is_valid_vietnamese_syllable(tok)

        if has_accent and is_vn:
            # Từ đã có dấu tiếng Việt hợp lệ (kể cả OOV như "nhiêu", "dượu"): giữ nguyên,
            # không xóa dấu để tra UnaccentedMap hay tách sticky token; lỗi chính tả do tầng spell xử lý.
            per_token_options.append([[[tok]]])
        elif (len(tok) == 1 or is_vn) and (tok in ua_map or no_accent in ua_map):
            cands = ua_map.get(tok) or ua_map[no_accent]
            per_token_options.append([[cands]])
        elif len(tok) == 2 and not is_vn:
            opts_2: list[list[list[str]]] = []
            cands_1w = ua_map.get(tok) or ua_map.get(no_accent)
            if cands_1w:
                opts_2.append([cands_1w])
            c0 = ua_map.get(tok[0]) or ua_map.get(no_accent[0])
            c1 = ua_map.get(tok[1]) or ua_map.get(no_accent[1])
            if c0 and c1:
                opts_2.append([c0, c1])
            per_token_options.append(opts_2 if opts_2 else [[[tok]]])
        else:
            # Trong câu dài (>= 4 từ), không băm từ OOV ngắn 3-4 ký tự không có trong chỉ mục viết tắt
            # (vd: "oto", "nhiu") thành 3-4 chữ cái đơn rời rạc; chỉ tách khi là viết tắt đã biết hoặc >= 5 ký tự.
            if (
                len(raw_tokens) >= 4
                and len(no_accent) <= 4
                and any(ch in "aeiou" for ch in no_accent)
                and not index.get_acronym_pids(no_accent)
            ):
                splits = [
                    sp for sp in split_sticky_token(index, no_accent)
                    if len(sp) <= 2 and all(len(p) >= 2 for p in sp)
                ]
            else:
                splits = split_sticky_token(index, no_accent)
            if splits:
                options = [[ua_map[piece] for piece in sp] for sp in splits]
                per_token_options.append(options)
            else:
                per_token_options.append([[[tok]]])

    multi_split_tokens = sum(1 for opts in per_token_options if len(opts) > 1)
    ambig_tokens = sum(1 for opts in per_token_options if len(opts) > 1 or len(opts[0][0]) > 1)
    max_vars = max(4, MAX_QUERY_VARIANTS // (1 + multi_split_tokens + max(0, ambig_tokens - 3) // 2))
    variants: list[list[list[str]]] = []
    for combo in product(*per_token_options):
        merged: list[list[str]] = []
        for part in combo:
            merged.extend(part)
        variants.append(merged)
        if len(variants) >= max_vars:
            break
    return variants


def direct_kb_search(
    index: GraphIndex,
    columns: list[list[str]],
    allow_missing: bool = True,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
) -> list[QueryMatch]:
    """Tìm trực tiếp các cụm có thật trong KB khớp `columns` (gồm khớp liên tiếp và thiếu 1 từ giữa)."""
    L = len(columns)
    if L < 2:
        return []

    col_pids: list[set[int]] = []
    col_sets: list[set[str]] = []
    col_initials: list[str] = []
    for col in columns:
        cid = id(col)
        pids = index.get_col_pids(col)
        cset = index.col_id_to_set.get(cid)
        init_ch = index.col_id_to_initial.get(cid)
        if cset is None:
            cset = set(col)
            init_ch = remove_accents(col[0])[0] if col and col[0] else ""
        if not pids:
            return []
        col_pids.append(pids)
        col_sets.append(cset)
        col_initials.append(init_ch or "")

    col_0 = col_sets[0]
    col_last = col_sets[L - 1]
    matches_by_phrase: dict[str, QueryMatch] = {}
    best_rk_by_phrase: dict[str, tuple[int, int, int, int, float]] = {}
    min_col_len = min(len(s) for s in col_pids)
    phrases = index.phrases
    fwd_prob = index.fwd_prob

    direct_sets = list(col_pids)
    pair_filters = []
    possible_direct = True
    if min_col_len > 300:
        direct_sets.append(index.get_len_range_pids(L, L + max_diff_k))
        for i in range(L - 1):
            if col_initials[i] and col_initials[i + 1]:
                pair_pids = index.initial_pair_to_pids.get(col_initials[i] + col_initials[i + 1])
                if not pair_pids:
                    possible_direct = False
                    break
                pair_filters.append(pair_pids)

    reusable_col_common: set[int] | None = None
    if possible_direct:
        if pair_filters:
            # Chuyển cặp nhỏ nhất sang set chỉ khi nhỏ hơn hoặc bằng tập nhỏ nhất hiện có;
            # khi tập giao đã thu gọn <= 32 phần tử thì dừng lọc pair_filters (lọc tiếp không làm giảm đáng kể).
            pair_filters.sort(key=len)
            direct_sets.sort(key=len)
            if len(pair_filters[0]) < len(direct_sets[0]):
                direct_common = set(pair_filters[0])
                for ds in direct_sets:
                    direct_common.intersection_update(ds)
                    if not direct_common:
                        break
                rem_filters = pair_filters[1:]
            else:
                direct_common = direct_sets[0].intersection(*direct_sets[1:])
                rem_filters = pair_filters
            for pf in rem_filters:
                if len(direct_common) <= 32:
                    break
                direct_common.intersection_update(pf)
        else:
            direct_sets.sort(key=len)
            direct_common = direct_sets[0].intersection(*direct_sets[1:])
            if min_col_len <= 300:
                reusable_col_common = direct_common
        max_len_direct = L + max_diff_k
        for pid in direct_common:
            p_str, p_words, p_freq = phrases[pid]
            M = len(p_words)
            if M < L or M > max_len_direct:
                continue
            diff_direct = M - L
            for start in range(diff_direct + 1):
                if p_words[start] not in col_0 or p_words[start + L - 1] not in col_last:
                    continue
                if L > 2:
                    ok = True
                    for i in range(1, L - 1):
                        if p_words[start + i] not in col_sets[i]:
                            ok = False
                            break
                    if not ok:
                        continue
                sub_words = p_words[start : start + L]
                sub_phrase = " ".join(sub_words)
                prev_rk = best_rk_by_phrase.get(sub_phrase)
                if prev_rk is not None:
                    # Nhanh: nếu diff lớn hơn hoặc bằng nhau nhưng freq nhỏ hơn thì bỏ qua không cần tính markov_prob
                    if diff_direct > prev_rk[2] or (diff_direct == prev_rk[2] and -p_freq > prev_rk[3]):
                        continue
                m_prob = 1.0
                for i in range(L - 1):
                    nbrs = fwd_prob.get(sub_words[i])
                    m_prob *= nbrs.get(sub_words[i + 1], 1e-6) if nbrs else 1e-6
                cand = QueryMatch(
                    phrase=sub_phrase,
                    freq=p_freq,
                    missing_count=0,
                    superstring_diff=diff_direct,
                    source_phrase=p_str,
                    missing_words=[],
                    markov_prob=m_prob,
                )
                rk = rank_key(cand)
                if prev_rk is None or rk < prev_rk:
                    matches_by_phrase[sub_phrase] = cand
                    best_rk_by_phrase[sub_phrase] = rk

    strong_f = index.strong_kb_freq
    strong_zero_miss = sum(
        1 for m in matches_by_phrase.values() if m.superstring_diff == 0 or m.freq >= strong_f
    )
    is_pure_acronym = all(len(col) > 15 for col in columns)
    if not allow_missing or L < 2 or strong_zero_miss >= 3 or (is_pure_acronym and strong_zero_miss >= 1):
        return list(matches_by_phrase.values())

    if reusable_col_common is not None:
        miss_common = reusable_col_common
    else:
        miss_sets = list(col_pids)
        if min_col_len > 300:
            miss_sets.append(index.get_len_range_pids(L + 1, L + 1 + max_diff_k))
        miss_sets.sort(key=len)
        miss_common = miss_sets[0].intersection(*miss_sets[1:])
    min_len_miss = L + 1
    max_len_miss = L + 1 + max_diff_k
    for pid in miss_common:
        p_str, p_words, p_freq = phrases[pid]
        M = len(p_words)
        if M < min_len_miss or M > max_len_miss:
            continue
        diff_miss = M - min_len_miss
        for start in range(diff_miss + 1):
            if p_words[start] not in col_0 or p_words[start + L] not in col_last:
                continue
            for gap_i in range(1, L):
                ok = True
                for i in range(1, gap_i):
                    if p_words[start + i] not in col_sets[i]:
                        ok = False
                        break
                if not ok:
                    continue
                for i in range(gap_i, L - 1):
                    if p_words[start + i + 1] not in col_sets[i]:
                        ok = False
                        break
                if not ok:
                    continue
                mid_word = p_words[start + gap_i]
                if is_class_token(mid_word):
                    continue
                sub_words = p_words[start : start + L + 1]
                sub_phrase = " ".join(sub_words)
                prev_rk = best_rk_by_phrase.get(sub_phrase)
                if prev_rk is not None and prev_rk[1] == 0:
                    continue
                m_prob = 1.0
                for i in range(L):
                    nbrs = fwd_prob.get(sub_words[i])
                    m_prob *= nbrs.get(sub_words[i + 1], 1e-6) if nbrs else 1e-6
                cand = QueryMatch(
                    phrase=sub_phrase,
                    freq=p_freq,
                    missing_count=1,
                    superstring_diff=diff_miss,
                    source_phrase=p_str,
                    missing_words=[mid_word],
                    markov_prob=m_prob,
                )
                rk = rank_key(cand)
                if prev_rk is None or rk < prev_rk:
                    matches_by_phrase[sub_phrase] = cand
                    best_rk_by_phrase[sub_phrase] = rk

    return list(matches_by_phrase.values())


def query_by_kb_composition(
    index: GraphIndex,
    raw_tokens: list[str],
    top_k: int = 5,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    min_bridge: int = 0,
) -> list[QueryMatch]:
    """Tách truy vấn thành 2 cụm KB độc lập nối qua cạnh đồ thị."""
    n_tok = len(raw_tokens)
    if n_tok < 2:
        return []

    def find_exact_kb(sub_tokens: list[str]) -> list[QueryMatch]:
        if len(sub_tokens) == 1:
            t0 = sub_tokens[0]
            if len(t0) >= 2 and not is_valid_vietnamese_syllable(t0):
                acr_pids = index.get_acronym_pids(remove_accents(t0))
                if acr_pids:
                    return index.pids_to_exact_matches(acr_pids, 3, compute_conf=False)
        else:
            sub_str = " ".join(sub_tokens)
            if sub_str in index.dict_freq:
                m_prob = 1.0
                for i in range(len(sub_tokens) - 1):
                    m_prob *= index.fwd_prob.get(sub_tokens[i], {}).get(sub_tokens[i + 1], 1e-6)
                return [
                    QueryMatch(
                        phrase=sub_str,
                        freq=index.dict_freq[sub_str],
                        missing_count=0,
                        superstring_diff=0,
                        source_phrase=sub_str,
                        missing_words=[],
                        markov_prob=m_prob,
                    )
                ]
            if all(len(t) >= 2 and is_valid_vietnamese_syllable(t) for t in sub_tokens):
                ua_pids = index.get_ua_pids(sub_str)
                if ua_pids:
                    return index.pids_to_exact_matches(ua_pids, 3, compute_conf=False)

        sub_q = " ".join(sub_tokens)
        found: dict[str, QueryMatch] = {}
        for cols in expand_query_variants(index, sub_q):
            for m in direct_kb_search(index, cols, allow_missing=False, max_diff_k=max_diff_k):
                if m.superstring_diff == 0 and m.missing_count == 0 and len(m.phrase.split()) >= 2:
                    prev = found.get(m.phrase)
                    if prev is None or m.freq > prev.freq:
                        found[m.phrase] = m
        return sorted(found.values(), key=lambda x: -x.freq)[:3]

    composed: dict[str, tuple[tuple[int, int, int], QueryMatch]] = {}
    horizons = index.build_kb_horizons(raw_tokens)
    if len(horizons) >= 2 and n_tok > 8:
        split_points = sorted(
            {
                pt
                for _, ed in horizons[:-1]
                for pt in (ed - 1, ed, ed + 1)
                if 1 <= pt < n_tok
            }
        )
    else:
        split_points = list(range(1, n_tok))
    for k in split_points:
        left_cands = find_exact_kb(raw_tokens[:k])
        if not left_cands:
            continue
        right_cands = find_exact_kb(raw_tokens[k:])
        if not right_cands:
            continue

        for lm in left_cands:
            l_last = lm.phrase.split()[-1]
            for rm in right_cands:
                r_first = rm.phrase.split()[0]
                bridge_e = index.fwd_graph.get(l_last, {}).get(r_first, 0)
                if bridge_e < min_bridge:
                    continue
                min_f = min(lm.freq, rm.freq)
                sum_f = lm.freq + rm.freq
                full_p = f"{lm.phrase} {rm.phrase}"
                score_k = (0 if bridge_e > 0 else 1, -min_f, -sum_f)
                cand = QueryMatch(
                    phrase=full_p,
                    freq=min_f,
                    missing_count=0,
                    superstring_diff=0,
                    source_phrase="",
                    missing_words=[],
                    context_tier=4,
                    context_span_len=0,
                    markov_prob=lm.markov_prob * rm.markov_prob,
                )
                prev = composed.get(full_p)
                if prev is None or score_k < prev[0]:
                    composed[full_p] = (score_k, cand)

    if not composed:
        return []
    return [m for _, m in sorted(composed.values(), key=lambda x: x[0])[:top_k]]
