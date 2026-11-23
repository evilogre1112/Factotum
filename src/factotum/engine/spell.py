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
    if n == 0 or n > 64:
        return []

    fwd = index.fwd_graph
    rev = index.rev_graph
    found: dict[str, tuple[tuple[int, int, float, int], QueryMatch]] = {}
    best_per_pos: list[tuple[int, str, int, float, int]] = []

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
        orig_l = fwd.get(prev_w, {}).get(tok, 0) if prev_w else 0
        orig_r = fwd.get(tok, {}).get(next_w, 0) if next_w else 0
        broken_left = bool(prev_w and prev_w in fwd and orig_l == 0)
        broken_right = bool(next_w and next_w in rev and orig_r == 0)

        if not (is_nonword or broken_left or broken_right):
            continue
        if not is_nonword and tok == remove_accents(tok) and tok in index.unaccented_map:
            continue
        # Từ đã có trong từ vựng và nối được với ít nhất 1 lân cận (vd: "giao thông" trong "giao thông ở đâu")
        # thì không phải lỗi chính tả, tránh sửa bừa làm hỏng từ đúng.
        if not is_nonword and (orig_l > 0 or orig_r > 0):
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
        eff_min_sim = min_sim if is_nonword else max(min_sim, 0.72)
        for w in cand_pool:
            if w == tok:
                continue
            sim = vn_word_similarity(tok, w)
            if sim < eff_min_sim:
                continue
            e_l = fwd.get(prev_w, {}).get(w, 0) if prev_w else 0
            e_r = fwd.get(w, {}).get(next_w, 0) if next_w else 0
            both_bonus = 1 if (e_l > 0 and e_r > 0) else 0
            edge_w = (e_l + e_r) if (has_l or has_r) else index.dict_freq.get(w, 0)
            scored_cands.append((sim, both_bonus, edge_w, w))

        if not scored_cands:
            continue
        scored_cands.sort(key=lambda x: (-round(x[0], 2), -x[1], -x[2]))

        pos_best_recorded = False
        for sim, both_bonus, edge_w, w in scored_cands[:6]:
            fixed_words = raw_tokens[:i] + [w] + raw_tokens[i + 1 :]
            if n == 1:
                kb_freq, kb_diff, kb_src = (
                    index.dict_freq.get(w, 0) or index.word_freq.get(w, 0),
                    0,
                    w,
                )
            else:
                # Tra KB trên cả câu (nếu ngắn) hoặc cửa sổ cục bộ quanh vị trí sửa (cho câu dài > 5 từ)
                kb_freq, kb_diff, kb_src = (
                    index.lookup_real_phrase_score(fixed_words, max_diff_k=max_diff_k)
                    if n <= 6
                    else (0, -1, "")
                )
                if kb_freq <= 0:
                    w_start = max(0, i - 1)
                    w_end = min(n, i + 2)
                    kb_freq, kb_diff, kb_src = index.lookup_real_phrase_score(
                        fixed_words[w_start:w_end], max_diff_k=max_diff_k
                    )
                if kb_freq <= 0 and prev_w:
                    kb_freq, kb_diff, kb_src = index.lookup_real_phrase_score(
                        [prev_w, w], max_diff_k=max_diff_k
                    )
                if kb_freq <= 0 and next_w:
                    kb_freq, kb_diff, kb_src = index.lookup_real_phrase_score(
                        [w, next_w], max_diff_k=max_diff_k
                    )

            # Nếu từ gốc đã nằm trong từ vựng (real-word typo như "sử lý" -> "xử lý"),
            # bắt buộc phương án sửa phải tạo ra cụm có thật trong KB.
            if not is_nonword and kb_freq <= 0:
                continue
            if kb_freq <= 0 and edge_w <= 0:
                continue

            m_start = max(0, i - 2)
            m_end = min(n, i + 3)
            m_prob = 1.0
            for j in range(m_start, m_end - 1):
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
                context_span_len=min(n - 1, 2) if kb_freq > 0 else 1,
                markov_prob=m_prob,
            )
            base_conf = compute_confidence(m)
            m.confidence = round(min(0.96, base_conf * (0.50 + 0.50 * sim)), 4)

            rank_k = (is_kb, kb_diff if kb_freq > 0 else 99, -round(sim, 3), -(kb_freq or edge_w))
            prev = found.get(phrase_str)
            if prev is None or rank_k < prev[0]:
                found[phrase_str] = (rank_k, m)
            if not pos_best_recorded and is_nonword and (kb_freq > 0 or both_bonus > 0):
                best_per_pos.append((i, w, kb_freq or edge_w, sim, is_kb))
                pos_best_recorded = True

    if len(best_per_pos) >= 2:
        combined = list(raw_tokens)
        min_sim_all = 1.0
        total_freq = 0
        for pos_i, rep_w, freq_v, sim_v, _ in best_per_pos:
            combined[pos_i] = rep_w
            min_sim_all = min(min_sim_all, sim_v)
            total_freq += freq_v
        phrase_str = " ".join(combined)
        m = QueryMatch(
            phrase=phrase_str,
            freq=total_freq // len(best_per_pos),
            missing_count=0,
            superstring_diff=0,
            source_phrase="",
            missing_words=[],
            context_tier=2,
            context_span_len=len(best_per_pos),
            markov_prob=0.5,
        )
        m.confidence = round(min(0.96, compute_confidence(m) * (0.50 + 0.50 * min_sim_all)), 4)
        found[phrase_str] = ((0, 0, -round(min_sim_all, 3), -total_freq), m)

    if not found:
        return []
    return [m for _, m in sorted(found.values(), key=lambda x: x[0])[:top_k]]
