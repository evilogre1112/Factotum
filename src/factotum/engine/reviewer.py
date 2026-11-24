from factotum.core.extension import is_dangling_phrase, is_valid_vietnamese_syllable, remove_accents
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import QueryMatch, compute_confidence, evidence_tier


def _align_tokens_to_words(
    orig_tokens: list[str],
    words: list[str],
) -> list[tuple[str, int, int]] | None:
    """Căn chỉnh mỗi token gốc (kể cả viết tắt bung nhiều từ như 'tngt') với đoạn [start, end) trong câu đáp án."""
    n_orig = len(orig_tokens)
    n_words = len(words)
    if n_orig == 0 or n_words == 0 or n_words < n_orig:
        return None
    if n_orig == n_words:
        return [(tok, i, i + 1) for i, tok in enumerate(orig_tokens)]

    memo: dict[tuple[int, int], list[tuple[str, int, int]] | None] = {}

    def _dp(ti: int, wi: int) -> list[tuple[str, int, int]] | None:
        if ti == n_orig and wi == n_words:
            return []
        if ti == n_orig or wi == n_words or (n_words - wi) < (n_orig - ti):
            return None
        key = (ti, wi)
        if key in memo:
            return memo[key]

        tok = orig_tokens[ti]
        ua = remove_accents(tok)
        # Thử khớp khối viết tắt (len >= 2, không phải âm tiết chuẩn) với đúng len(ua) từ có chữ cái đầu khớp
        if len(ua) >= 2 and ua.isalpha() and not is_valid_vietnamese_syllable(ua) and wi + len(ua) <= n_words:
            if all(
                (rw := remove_accents(words[wi + k])) and rw[0] == ua[k]
                for k in range(len(ua))
            ):
                rest = _dp(ti + 1, wi + len(ua))
                if rest is not None:
                    res = [(tok, wi, wi + len(ua))] + rest
                    memo[key] = res
                    return res

        # Thử khớp 1-1
        rest1 = _dp(ti + 1, wi + 1)
        if rest1 is not None:
            res = [(tok, wi, wi + 1)] + rest1
            memo[key] = res
            return res

        memo[key] = None
        return None

    return _dp(0, 0)


def _sentence_global_metrics(
    index: GraphIndex,
    words: list[str],
) -> tuple[int, int, int, int, float]:
    """Đánh giá tổng thể toàn câu: (số cạnh đứt, số từ được phủ bởi cụm KB >=3 từ, số cụm 4 từ KB, số cụm 3 từ KB, xác suất Markov)."""
    n = len(words)
    fwd = index.fwd_graph
    fwd_prob = index.fwd_prob
    broken_edges = (
        sum(1 for i in range(n - 1) if fwd.get(words[i], {}).get(words[i + 1], 0) == 0)
        if n >= 2
        else 0
    )
    covered = [False] * n
    kb3_hits = 0
    kb4_hits = 0
    if n >= 3:
        for i in range(n - 2):
            if index.lookup_real_phrase_score(words[i : i + 3])[0] > 0:
                kb3_hits += 1
                covered[i] = covered[i + 1] = covered[i + 2] = True
    if n >= 4:
        for i in range(n - 3):
            if index.lookup_real_phrase_score(words[i : i + 4])[0] > 0:
                kb4_hits += 1
                covered[i] = covered[i + 1] = covered[i + 2] = covered[i + 3] = True
    kb_covered = sum(covered)

    m_prob = max(index.dict_freq.get(words[0], 1), 1) / 100000.0 if n >= 1 else 0.0
    for i in range(n - 1):
        m_prob *= fwd_prob.get(words[i], {}).get(words[i + 1], 1e-6)
    return broken_edges, kb_covered, kb4_hits, kb3_hits, m_prob


def _repair_broken_markov_bridge(
    index: GraphIndex,
    match: QueryMatch,
    orig_tokens: list[str],
    *,
    allow_kb_upgrade: bool = False,
) -> QueryMatch:
    """
    Hậu kiểm tổng thể toàn câu trên nhánh Markov thuần (`superstring_diff < 0`):
    - Căn chỉnh token gốc với câu đáp án (hỗ trợ cả viết tắt bung nhiều từ như 'tngt').
    - Với khối viết tắt (>=2 từ): thử thay cả khối bằng cụm viết tắt KB nếu làm điểm tổng thể toàn câu tốt hơn.
    - Với từ đơn không dấu: sửa cầu nối `A - F' - B` và nâng cấp cụm 3 từ KB (`allow_kb_upgrade=True`).
    """
    if match.superstring_diff >= 0 or match.is_fallback or match.missing_count > 0:
        return match
    words = match.phrase.split()
    n = len(words)
    if n < 2:
        return match
    spans = _align_tokens_to_words(orig_tokens, words)
    if spans is None:
        return match

    fwd_prob = index.fwd_prob
    ua_map = index.unaccented_map
    changed = False

    # 1. Hậu kiểm theo khối cho token viết tắt bung nhiều từ (vd: 'tngt' -> 'tai nạn giao thông', 'atgt' -> 'an toàn giao thông')
    if allow_kb_upgrade and n > len(orig_tokens):
        cur_broken, cur_cov, cur_kb4, cur_kb3, cur_prob = _sentence_global_metrics(index, words)
        fwd_g = index.fwd_graph
        for orig, st, ed in spans:
            span_len = ed - st
            if span_len < 2:
                continue
            ua_orig = remove_accents(orig)
            acr_pids = index.get_acronym_pids(ua_orig)
            if not acr_pids:
                continue
            cand_phrases = [
                list(index.phrases[pid][1])
                for pid in acr_pids
                if len(index.phrases[pid][1]) == span_len
            ]
            cand_phrases.sort(key=lambda pw: -index.dict_freq.get(" ".join(pw), 0))
            cur_block_freq = index.dict_freq.get(" ".join(words[st:ed]), 0)
            left_b = words[st - 1] if st > 0 else ""
            right_b = words[ed] if ed < n else ""

            def _block_key(
                trial_words: list[str],
                block_freq: int,
                tb: int,
                tc: int,
                t4: int,
                t3: int,
                tp: float,
            ) -> tuple[int, int, int, int, int, int, float]:
                l_ok = (not left_b) or (fwd_g.get(left_b, {}).get(trial_words[st], 0) > 0)
                r_ok = (not right_b) or (fwd_g.get(trial_words[ed - 1], {}).get(right_b, 0) > 0)
                both_broken = 1 if (left_b and right_b and not l_ok and not r_ok) else 0
                strong_kb = 1 if (span_len >= 3 and block_freq >= 5) else 0
                return (strong_kb, -both_broken, -tb, tc, t4, t3, tp)

            best_block: list[str] | None = None
            best_key = _block_key(words, cur_block_freq, cur_broken, cur_cov, cur_kb4, cur_kb3, cur_prob)
            for c_words in cand_phrases[:8]:
                if c_words == words[st:ed]:
                    continue
                c_freq = index.dict_freq.get(" ".join(c_words), 0)
                trial = words[:st] + c_words + words[ed:]
                tb, tc, t4, t3, tp = _sentence_global_metrics(index, trial)
                t_key = _block_key(trial, c_freq, tb, tc, t4, t3, tp)
                if t_key > best_key:
                    best_key = t_key
                    best_block = c_words
                    cur_broken, cur_cov, cur_kb4, cur_kb3, cur_prob = tb, tc, t4, t3, tp
            if best_block is not None:
                words[st:ed] = best_block
                changed = True

    # 2. Hậu kiểm cầu nối từng từ đơn không dấu A - F' - B và cụm 3 từ KB
    for orig, st, ed in spans:
        if ed - st != 1:
            continue
        i = st
        ua_orig = remove_accents(orig)
        if orig != ua_orig:
            continue
        cur = words[i]
        left_w = words[i - 1] if i > 0 else ""
        right_w = words[i + 1] if i + 1 < n else ""

        p_l = fwd_prob.get(left_w, {}).get(cur, 0.0) if left_w else 1.0
        p_r = fwd_prob.get(cur, {}).get(right_w, 0.0) if right_w else 1.0
        edge_broken = (p_l == 0.0) or (p_r == 0.0)

        kb_cur = 0
        if allow_kb_upgrade and left_w and right_w:
            kb_cur = index.lookup_real_phrase_score([left_w, cur, right_w])[0]
        if not edge_broken and not (allow_kb_upgrade and left_w and right_w and kb_cur == 0):
            continue

        cands = ua_map.get(orig) or ua_map.get(ua_orig)
        if not cands:
            continue

        orig_is_syll = len(ua_orig) >= 2 and is_valid_vietnamese_syllable(ua_orig)
        both_broken = (left_w and p_l == 0.0) and (right_w and p_r == 0.0)
        best_f = ""
        best_score = (0, 0, 0.0)

        for cand in cands:
            if cand == cur:
                continue
            if orig_is_syll and remove_accents(cand) != ua_orig:
                continue
            cl = fwd_prob.get(left_w, {}).get(cand, 0.0) if left_w else 1.0
            cr = fwd_prob.get(cand, {}).get(right_w, 0.0) if right_w else 1.0

            kb_c = index.lookup_real_phrase_score([left_w, cand, right_w])[0] if (allow_kb_upgrade and left_w and right_w) else 0
            if allow_kb_upgrade and kb_c > 0 and kb_cur == 0 and cl > 0.0 and cr > 0.0:
                score = (3, kb_c, cl * cr)
            elif edge_broken and cl > 0.0 and cr > 0.0:
                score = (2, kb_c, cl * cr)
            elif edge_broken and both_broken and (cl > 0.0 or cr > 0.0):
                score = (1, kb_c, max(cl, cr))
            else:
                continue

            if score > best_score:
                best_score = score
                best_f = cand

        if best_f:
            words[i] = best_f
            changed = True

    if not changed:
        return match

    new_phrase = " ".join(words)
    new_prob = max(index.dict_freq.get(words[0], 1), 1) / 100000.0
    for i in range(n - 1):
        new_prob *= fwd_prob.get(words[i], {}).get(words[i + 1], 1e-6)
    repaired = QueryMatch(
        phrase=new_phrase,
        freq=match.freq,
        missing_count=match.missing_count,
        superstring_diff=match.superstring_diff,
        source_phrase=match.source_phrase,
        missing_words=list(match.missing_words),
        context_tier=match.context_tier,
        context_span_len=match.context_span_len,
        markov_prob=new_prob,
        is_fallback=match.is_fallback,
    )
    repaired.confidence = compute_confidence(repaired)
    return repaired


def _count_strong_acronym_kb_hits(
    index: GraphIndex,
    orig_tokens: list[str],
    words: list[str],
) -> int:
    """Đếm số khối viết tắt (>=3 ký tự, vd: 'atgt', 'tngt', 'xpvphc') khớp đúng cụm KB mạnh (freq >= 5)."""
    if len(words) <= len(orig_tokens):
        return 0
    spans = _align_tokens_to_words(orig_tokens, words)
    if not spans:
        return 0
    hits = 0
    for _orig, st, ed in spans:
        if ed - st >= 3 and index.dict_freq.get(" ".join(words[st:ed]), 0) >= 5:
            hits += 1
    return hits


def _deep_rerank_score(
    index: GraphIndex,
    m: QueryMatch,
    orig_tokens: list[str],
) -> tuple[int, int, int, int, int, int, float, float, int]:
    """Khóa xếp hạng tổng thể toàn câu cho nhánh Markov: ưu tiên hạng KB, khối viết tắt KB chuẩn, ít cạnh đứt, cụm KB và xác suất Markov."""
    words = m.phrase.split()
    is_acr_exp = m.missing_count == 0 and len(words) > len(orig_tokens)
    broken_edges, _kb_covered, kb4_hits, kb3_hits, _ = _sentence_global_metrics(index, words)
    acr_kb_hits = _count_strong_acronym_kb_hits(index, orig_tokens, words) if is_acr_exp else 0
    return (
        evidence_tier(m),
        m.missing_count,
        -acr_kb_hits,
        broken_edges,
        0 if (is_acr_exp and m.context_tier > 0) else -kb4_hits,
        0 if is_acr_exp else -kb3_hits,
        -round(m.confidence, 4),
        -m.markov_prob,
        -m.freq,
    )


def review_matches(
    index: GraphIndex,
    matches: list[QueryMatch],
    fallback_phrase: str,
    top_k: int = 5,
) -> list[QueryMatch]:
    """
    Tầng hậu kiểm độc lập (Post-Review): nhìn lại toàn bộ câu đáp án đã tạo ở pha sinh.
    - Review Top-5 ứng viên đầu (sửa cầu nối `A - F' - B` kèm nâng cấp cụm 3 từ KB)
      và xếp hạng lại nhánh Markov theo số cạnh đứt + cụm 3 từ KB.
    - Loại bỏ các câu kết thúc cụt lủn theo file tùy chỉnh `output/dangling_end_words.txt`.
    - Nếu không còn phương án hợp lệ nào, trả về nguyên văn câu gốc kèm `is_fallback=True`.
    """
    orig_tokens = fallback_phrase.split() if fallback_phrase else []
    repaired_matches: list[QueryMatch] = []
    seen_phrases: set[str] = set()
    deep_limit = max(5, top_k)
    for idx_m, m in enumerate(matches):
        if orig_tokens:
            rm = _repair_broken_markov_bridge(
                index, m, orig_tokens, allow_kb_upgrade=(idx_m < deep_limit)
            )
        else:
            rm = m
        if rm.phrase not in seen_phrases:
            seen_phrases.add(rm.phrase)
            repaired_matches.append(rm)

    had_dangling = any(is_dangling_phrase(m.phrase) for m in repaired_matches)
    valid: list[QueryMatch] = []
    for m in repaired_matches:
        if is_dangling_phrase(m.phrase):
            continue
        if had_dangling and m.superstring_diff < 0:
            words = m.phrase.split()
            if len(words) >= 2 and index.fwd_graph.get(words[-2], {}).get(words[-1], 0) == 0:
                continue
        valid.append(m)

    if valid:
        if orig_tokens:
            head_slice = valid[:deep_limit]
            head_keep = [item for item in head_slice if item.superstring_diff >= 0]
            head_mk = sorted(
                (item for item in head_slice if item.superstring_diff < 0),
                key=lambda item: _deep_rerank_score(index, item, orig_tokens),
            )
            head = head_keep + head_mk
            valid = head + valid[deep_limit:]
        return valid[:top_k]

    if not fallback_phrase:
        return []
    return [
        QueryMatch(
            phrase=fallback_phrase,
            freq=0,
            missing_count=0,
            superstring_diff=-1,
            source_phrase="",
            missing_words=[],
            context_tier=5,
            confidence=0.0,
            is_fallback=True,
        )
    ]
