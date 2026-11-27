from factotum.core.extension import is_dangling_phrase, remove_accents
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import QueryMatch, evidence_tier
from factotum.engine.reviewer_repairs import (
    _repair_broken_markov_bridge,
    _resolve_edits,
    _sentence_global_metrics,
    _try_bridge_broken_edge_by_overlapping_kb,
)


def _count_strong_acronym_kb_hits(
    index: GraphIndex,
    match: QueryMatch,
    orig_tokens: list[str],
    words: list[str],
) -> int:
    """Đếm số khối viết tắt Worker đã bung (`kind == 'acronym'`) khớp đúng cụm KB mạnh (freq >= 5)."""
    edits = _resolve_edits(match, orig_tokens, words)
    if not edits:
        return 0
    n = len(words)
    hits = 0
    for e in edits:
        if e.kind == "acronym" and 0 <= e.start < e.end <= n:
            if index.dict_freq.get(" ".join(words[e.start : e.end]), 0) >= 5:
                hits += 1
    return hits


def _broken_edge_nfd_distortion(
    index: GraphIndex,
    orig_tokens: list[str],
    words: list[str],
) -> tuple[float, int]:
    """Tính tổng độ biến dạng NFD + dấu thanh tại các token bị đứt toàn bộ cạnh nối (P_markov = 0)."""
    n = len(words)
    if n == 0 or len(orig_tokens) != n:
        return (0.0, 0)
    fwd = index.fwd_graph
    real_vocab = index.real_vocab
    dict_freq = index.dict_freq
    strong_f = index.strong_kb_freq
    total_c = 0.0
    total_tones = 0
    for i in range(n):
        if remove_accents(orig_tokens[i]) in real_vocab:
            continue
        if i > 0 and words[i - 1] not in real_vocab:
            continue
        if i + 1 < n and words[i + 1] not in real_vocab:
            continue
        left_ok = i > 0 and fwd.get(words[i - 1], {}).get(words[i], 0) > 0
        right_ok = i + 1 < n and fwd.get(words[i], {}).get(words[i + 1], 0) > 0
        if not left_ok and not right_ok:
            prev_connected = i <= 1 or fwd.get(words[i - 2], {}).get(words[i - 1], 0) > 0
            next_connected = i + 2 >= n or fwd.get(words[i + 1], {}).get(words[i + 2], 0) > 0
            if not (prev_connected and next_connected):
                continue
            if dict_freq.get(words[i], 0) < strong_f:
                total_c += 1.0
                total_tones += 2
            else:
                c_nfd, d_tone = index.nfd_token_distortion(orig_tokens[i], words[i])
                total_c += c_nfd
                total_tones += d_tone
    return (round(total_c, 4), total_tones)


def _deep_rerank_score(
    index: GraphIndex,
    m: QueryMatch,
    orig_tokens: list[str],
) -> tuple[int, int, int, int, int, int, float, tuple[float, int], float, int]:
    """Khóa xếp hạng tổng thể toàn câu theo hạng bằng chứng, cụm viết tắt KB, cạnh đứt, độ phủ KB, biến dạng NFD khi đứt cạnh và Markov."""
    words = m.phrase.split()
    is_acr_exp = m.missing_count == 0 and len(words) > len(orig_tokens)
    broken_edges, _kb_covered, weighted_kb, kb4_plus_hits, kb3_hits, _ = _sentence_global_metrics(index, words)
    acr_kb_hits = _count_strong_acronym_kb_hits(index, m, orig_tokens, words) if is_acr_exp else 0
    broken_dist = (
        _broken_edge_nfd_distortion(index, orig_tokens, words)
        if (broken_edges > 0 and round(m.confidence, 4) <= 0.12)
        else (0.0, 0)
    )
    return (
        evidence_tier(m),
        m.missing_count,
        -acr_kb_hits,
        broken_edges,
        0 if (is_acr_exp and m.context_tier > 0) else -weighted_kb,
        0 if is_acr_exp else -(kb4_plus_hits + kb3_hits),
        -round(m.confidence, 4),
        broken_dist,
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
    Tầng hậu kiểm 2 chặng trên danh sách đoạn chỉnh sửa `match.edits`:
    - Chặng 1: Hiệu chỉnh từng đoạn trong `match.edits` bằng cửa sổ ngữ cảnh thích ứng theo đồ thị và KB.
    - Chặng 2: Xếp hạng tổng thể toàn câu và lọc câu kết thúc cụt lủn.
    """
    orig_tokens = fallback_phrase.split() if fallback_phrase else []
    orig_by_phrase: dict[str, QueryMatch] = {}
    for m in matches:
        if m.phrase not in orig_by_phrase:
            orig_by_phrase[m.phrase] = m
    repaired_matches: list[QueryMatch] = []
    seen_phrases: set[str] = set()
    deep_limit = max(5, top_k)
    for idx_m, m in enumerate(matches):
        if orig_tokens or m.edits:
            rm = _repair_broken_markov_bridge(
                index,
                m,
                orig_tokens,
                allow_kb_upgrade=(idx_m < deep_limit),
                orig_by_phrase=orig_by_phrase,
            )
        else:
            rm = m
        if rm.phrase not in seen_phrases:
            seen_phrases.add(rm.phrase)
            repaired_matches.append(rm)
        if idx_m < deep_limit and rm.superstring_diff < 0 and rm.missing_count == 0:
            rm_words = rm.phrase.split()
            rm_edits = list(_resolve_edits(rm, orig_tokens, rm_words))
            bridged = _try_bridge_broken_edge_by_overlapping_kb(index, rm, rm_words, rm_edits)
            if bridged is not None and bridged.phrase not in seen_phrases:
                seen_phrases.add(bridged.phrase)
                repaired_matches.append(bridged)

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
            valid = head_keep + head_mk + valid[deep_limit:]
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
