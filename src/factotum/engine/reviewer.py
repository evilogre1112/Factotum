from factotum.core.extension import is_dangling_phrase, is_valid_vietnamese_syllable, remove_accents
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import QueryMatch, compute_confidence, evidence_tier


def _repair_broken_markov_bridge(
    index: GraphIndex,
    match: QueryMatch,
    orig_tokens: list[str],
    *,
    allow_kb_upgrade: bool = False,
) -> QueryMatch:
    """
    Hậu kiểm cầu nối A - F - B trên nhánh Markov thuần (`superstring_diff < 0`):
    - Lượt thường: nếu `F` làm đứt cạnh (`A->F == 0` hoặc `F->B == 0`), tìm `F'` hợp lệ từ
      từ gốc ban đầu sao cho `A -> F' -> B` có xác suất thực tế trên đồ thị.
    - Lượt sâu (`allow_kb_upgrade=True`): kiểm tra thêm cửa sổ 3 từ KB `(A, F', B)` — nếu
      cả 3 từ không có trong KB nhưng tồn tại `F'` hợp lệ tạo thành cụm 3 từ có thật trong KB
      thì nâng cấp `F -> F'`.
    """
    if match.superstring_diff >= 0 or match.is_fallback:
        return match
    words = match.phrase.split()
    n = len(words)
    if n < 2 or n != len(orig_tokens):
        return match

    fwd_prob = index.fwd_prob
    ua_map = index.unaccented_map
    changed = False

    for i in range(n):
        orig = orig_tokens[i]
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


def _deep_rerank_score(index: GraphIndex, m: QueryMatch) -> tuple[int, int, int, int, float, float, int]:
    """Khóa xếp hạng lại nhánh Markov trong Top-5: ưu tiên hạng KB, ít cạnh đứt, nhiều cụm 3 từ KB và xác suất Markov."""
    words = m.phrase.split()
    fwd = index.fwd_graph
    broken_edges = (
        sum(1 for i in range(len(words) - 1) if fwd.get(words[i], {}).get(words[i + 1], 0) == 0)
        if len(words) >= 2
        else 0
    )
    kb3_hits = (
        sum(1 for i in range(len(words) - 2) if index.lookup_real_phrase_score(words[i : i + 3])[0] > 0)
        if len(words) >= 3
        else 0
    )
    return (
        evidence_tier(m),
        m.missing_count,
        broken_edges,
        -kb3_hits,
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
            n_orig = len(orig_tokens)
            head_keep = [
                item for item in head_slice
                if item.superstring_diff >= 0 or (item.missing_count == 0 and len(item.phrase.split()) != n_orig)
            ]
            head_mk = sorted(
                (
                    item for item in head_slice
                    if item.superstring_diff < 0 and not (item.missing_count == 0 and len(item.phrase.split()) != n_orig)
                ),
                key=lambda item: _deep_rerank_score(index, item),
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
