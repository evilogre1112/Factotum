from factotum.core.extension import (
    collapse_repeated_chars,
    decode_telex_word,
    is_valid_vietnamese_syllable,
    remove_accents,
    vn_word_similarity,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import MAX_SUPERSTRING_DIFF_K, QueryMatch, compute_confidence


def preprocess_tokens(index: GraphIndex, raw_tokens: list[str]) -> tuple[list[str], bool]:
    """Tiền xử lý token: gộp ký tự lặp (pháppp -> pháp) và giải mã Telex thô (buowcs -> bước)."""
    out: list[str] = []
    changed = False
    real_vocab = index.real_vocab
    ua_map = index.unaccented_map

    for tok in raw_tokens:
        if tok in real_vocab:
            out.append(tok)
            continue
        if not is_valid_vietnamese_syllable(tok):
            collapsed = collapse_repeated_chars(tok)
            # Chỉ gộp ký tự lặp khi kết quả là âm tiết tiếng Việt >= 2 ký tự (tránh gộp nhầm viết tắt 'dd', 'tt', 'nn' -> 'd', 't', 'n')
            if collapsed != tok and len(collapsed) >= 2 and is_valid_vietnamese_syllable(collapsed):
                if collapsed in real_vocab:
                    out.append(collapsed)
                    changed = True
                    continue
                if len(tok) >= 4 and collapsed in ua_map:
                    out.append(collapsed)
                    changed = True
                    continue
            telex = decode_telex_word(tok)
            if telex and telex in real_vocab:
                out.append(telex)
                changed = True
                continue
        out.append(tok)
    return out, changed


def try_graph_spell_correction(
    index: GraphIndex,
    raw_tokens: list[str],
    top_k: int = 5,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    min_sim: float = 0.58,
) -> list[QueryMatch]:
    """Sửa lỗi chính tả bằng đồ thị cạnh xuôi/ngược kết hợp độ tương đồng âm tiết tiếng Việt."""
    n = len(raw_tokens)
    if n == 0 or n > 12:
        return []

    fwd = index.fwd_graph
    rev = index.rev_graph
    found: dict[str, tuple[tuple[int, int, float, int], QueryMatch]] = {}

    unacc_positions = {
        idx
        for idx, t in enumerate(raw_tokens)
        if t == remove_accents(t) and (len(t) == 1 or t in index.unaccented_map)
    }

    for i, tok in enumerate(raw_tokens):
        if len(tok) < 2 or not tok.isalpha():
            continue
        if i in unacc_positions and len(unacc_positions) >= 2:
            continue
        prev_w = raw_tokens[i - 1] if i > 0 else ""
        next_w = raw_tokens[i + 1] if i + 1 < n else ""

        is_nonword = tok not in index.real_vocab and tok not in index.unaccented_map
        broken_left = bool(prev_w and prev_w in fwd and tok not in fwd[prev_w])
        broken_right = bool(next_w and next_w in rev and tok not in rev[next_w])

        if not (is_nonword or broken_left or broken_right):
            continue
        if not is_nonword and tok == remove_accents(tok) and tok in index.unaccented_map:
            continue

        has_l = bool(prev_w and prev_w in fwd)
        has_r = bool(next_w and next_w in rev)
        if has_l and has_r:
            cand_pool = set(fwd[prev_w]) | set(rev[next_w])
        elif has_r:
            cand_pool = rev[next_w].keys()
        elif has_l:
            cand_pool = fwd[prev_w].keys()
        else:
            ua0 = remove_accents(tok)[:1]
            cand_pool = index.unaccented_map.get(ua0, ())

        scored_cands: list[tuple[float, int, int, str]] = []
        for w in cand_pool:
            if w == tok:
                continue
            sim = vn_word_similarity(tok, w)
            if sim < min_sim:
                continue
            e_l = fwd.get(prev_w, {}).get(w, 0) if prev_w else 0
            e_r = fwd.get(w, {}).get(next_w, 0) if next_w else 0
            both_bonus = 1 if (e_l > 0 and e_r > 0) else 0
            edge_w = (e_l + e_r) if (has_l or has_r) else index.dict_freq.get(w, 0)
            scored_cands.append((sim, both_bonus, edge_w, w))

        if not scored_cands:
            continue
        scored_cands.sort(key=lambda x: (-round(x[0], 2), -x[1], -x[2]))

        for sim, _, edge_w, w in scored_cands[:6]:
            fixed_words = raw_tokens[:i] + [w] + raw_tokens[i + 1 :]
            kb_freq, kb_diff, kb_src = (
                index.lookup_real_phrase_score(fixed_words, max_diff_k=max_diff_k)
                if n >= 2
                else (index.dict_freq.get(w, 0) or index.word_freq.get(w, 0), 0, w)
            )
            if kb_freq <= 0 and edge_w <= 0:
                continue

            m_prob = 1.0
            for j in range(n - 1):
                m_prob *= index.fwd_prob.get(fixed_words[j], {}).get(fixed_words[j + 1], 1e-6)

            phrase_str = " ".join(fixed_words)
            is_kb = 0 if kb_freq > 0 else 1
            m = QueryMatch(
                phrase=phrase_str,
                freq=kb_freq if kb_freq > 0 else edge_w,
                missing_count=0,
                superstring_diff=kb_diff if kb_freq > 0 else -1,
                source_phrase=kb_src if kb_freq > 0 else "",
                missing_words=[],
                context_tier=2 if kb_freq > 0 else 4,
                context_span_len=n - 1 if kb_freq > 0 else 1,
                markov_prob=m_prob,
            )
            base_conf = compute_confidence(m)
            m.confidence = round(min(0.96, base_conf * (0.50 + 0.50 * sim)), 4)

            rank_k = (is_kb, kb_diff if kb_freq > 0 else 99, -round(sim, 3), -(kb_freq or edge_w))
            prev = found.get(phrase_str)
            if prev is None or rank_k < prev[0]:
                found[phrase_str] = (rank_k, m)

    if not found:
        return []
    return [m for _, m in sorted(found.values(), key=lambda x: x[0])[:top_k]]
