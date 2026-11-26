import math

from factotum.core.extension import (
    collapse_repeated_chars,
    decode_telex_word,
    get_compatible_token_classes,
    is_valid_vietnamese_syllable,
    is_vn_orthographic_confusion,
    remove_accents,
    token_similarity,
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
        if (
            tok in real_vocab
            or (is_valid_vietnamese_syllable(tok) and tok in ua_map)
            or get_compatible_token_classes(tok)
        ):
            out.append(tok)
            continue
        collapsed = collapse_repeated_chars(tok)
        if (
            collapsed != tok
            and len(collapsed) >= 2
            and any(ch in "aeiouy" for ch in remove_accents(tok))
            and is_valid_vietnamese_syllable(collapsed)
        ):
            if collapsed in real_vocab or (len(tok) >= 4 and collapsed in ua_map):
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


def _lookup_spell_kb_window(
    index: GraphIndex,
    fixed_words: list[str],
    pos: int,
    *,
    is_ortho_pair: bool,
    both_connected: bool,
    max_diff_k: int,
) -> tuple[int, int, str]:
    """Tra cụm KB thực tế trên toàn câu (n <= 6) hoặc cửa sổ 3 từ quanh vị trí sửa."""
    n = len(fixed_words)
    w = fixed_words[pos]
    if n == 1:
        return index.dict_freq.get(w, 0) or index.word_freq.get(w, 0), 0, w
    if n <= 6:
        freq, diff, src = index.lookup_real_phrase_score(fixed_words, max_diff_k=max_diff_k)
        if freq > 0 or n == 2:
            return freq, diff, src

    # Với câu >= 3 từ: bắt buộc tồn tại cửa sổ 3 từ thực tế trong KB (không hạ xuống 2-gram 1 phía).
    windows: list[list[str]] = []
    if 0 < pos < n - 1:
        windows.append(fixed_words[pos - 1 : pos + 2])
        if both_connected and is_ortho_pair:
            if pos + 2 < n:
                windows.append(fixed_words[pos : pos + 3])
            if pos >= 2:
                windows.append(fixed_words[pos - 2 : pos + 1])
    else:
        windows.append(fixed_words[0:3] if pos == 0 else fixed_words[n - 3 : n])

    for win3 in windows:
        freq, diff, src = index.lookup_real_phrase_score(win3, max_diff_k=max_diff_k)
        if freq > 0:
            return freq, diff, src
    return 0, -1, ""


def _local_markov_prob(index: GraphIndex, words: list[str], center: int) -> float:
    m_prob = 1.0
    start = max(0, center - 2)
    end = min(len(words), center + 3)
    for j in range(start, end - 1):
        m_prob *= index.fwd_prob.get(words[j], {}).get(words[j + 1], 1e-6)
    return m_prob


def try_graph_spell_correction(
    index: GraphIndex,
    raw_tokens: list[str],
    top_k: int = 5,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    min_sim: float = 0.58,
    allow_beam: bool = True,
) -> list[QueryMatch]:
    """Sửa lỗi chính tả bằng đồ thị cạnh xuôi/ngược kết hợp bằng chứng cụm 3 từ KB và cặp đồng âm tiếng Việt."""
    n = len(raw_tokens)
    if not (1 <= n <= 64):
        return []

    fwd, rev = index.fwd_graph, index.rev_graph
    found: dict[str, tuple[tuple[int, int, float, int], QueryMatch]] = {}
    best_per_pos: list[tuple[int, str, int, float]] = []
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

        one_side_ok = not is_nonword and (orig_l > 0 or orig_r > 0)
        at_long_edge = not is_nonword and tok != remove_accents(tok) and n >= 3 and (i == 0 or i == n - 1)
        has_l, has_r = bool(prev_w and prev_w in fwd), bool(next_w and next_w in rev)

        if has_l and has_r:
            cand_pool = set(fwd[prev_w]) | set(rev[next_w])
        elif has_r:
            cand_pool = rev[next_w].keys()
        elif has_l:
            cand_pool = fwd[prev_w].keys()
        else:
            cand_pool = index.unaccented_map.get(remove_accents(tok)[:1], ())

        eff_min_sim = min_sim if is_nonword else max(min_sim, 0.75)
        tok_has_tone = tok != remove_accents(tok)
        scored_cands: list[tuple[float, int, int, str, bool]] = []

        for w in cand_pool:
            if w == tok or (not is_nonword and tok_has_tone and w == remove_accents(w)):
                continue
            is_ortho = not is_nonword and is_vn_orthographic_confusion(tok, w)
            if (one_side_ok or at_long_edge) and not is_ortho:
                continue
            sim = vn_word_similarity(tok, w)
            if sim < eff_min_sim:
                continue
            e_l = fwd.get(prev_w, {}).get(w, 0) if prev_w else 0
            e_r = fwd.get(w, {}).get(next_w, 0) if next_w else 0
            both = 1 if (e_l > 0 and e_r > 0) else 0
            if one_side_ok and has_l and has_r and not both:
                continue
            edge_w = (e_l + e_r) if (has_l or has_r) else index.dict_freq.get(w, 0)
            scored_cands.append((sim, both, edge_w, w, is_ortho))

        scored_cands.sort(key=lambda x: (-round(x[0], 2), -x[1], -x[2]))
        pos_recorded = False
        for sim, both, edge_w, w, is_ortho in scored_cands[:6]:
            fixed_words = raw_tokens[:i] + [w] + raw_tokens[i + 1 :]
            kb_freq, kb_diff, kb_src = _lookup_spell_kb_window(
                index, fixed_words, i, is_ortho_pair=is_ortho, both_connected=bool(both), max_diff_k=max_diff_k
            )
            if kb_freq <= 0 and (not is_nonword or not both):
                continue

            phrase_str = " ".join(fixed_words)
            is_kb = 0 if kb_freq > 0 else 1
            m = QueryMatch(
                phrase=phrase_str,
                freq=kb_freq or edge_w,
                missing_count=0,
                superstring_diff=kb_diff if kb_freq > 0 else -1,
                source_phrase=kb_src if kb_freq > 0 else "",
                missing_words=[],
                context_tier=2 if kb_freq > 0 else 4,
                context_span_len=min(n - 1, 2) if kb_freq > 0 else 1,
                markov_prob=_local_markov_prob(index, fixed_words, i),
            )
            m.confidence = round(min(0.96, compute_confidence(m) * (0.50 + 0.50 * sim)), 4)
            rank_k = (is_kb, kb_diff if kb_freq > 0 else 99, -round(sim, 3), -(kb_freq or edge_w))
            if phrase_str not in found or rank_k < found[phrase_str][0]:
                found[phrase_str] = (rank_k, m)
            if not pos_recorded and is_nonword and (kb_freq > 0 or both):
                best_per_pos.append((i, w, kb_freq or edge_w, sim))
                pos_recorded = True

    if len(best_per_pos) >= 2:
        combined = list(raw_tokens)
        for pos_i, rep_w, _, _ in best_per_pos:
            combined[pos_i] = rep_w
        min_sim_all = min(x[3] for x in best_per_pos)
        total_freq = sum(x[2] for x in best_per_pos)
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

    if not found and allow_beam:
        return _beam_graph_spell_correction(
            index, raw_tokens, top_k=top_k, max_diff_k=max_diff_k
        )
    return [m for _, m in sorted(found.values(), key=lambda x: x[0])[:top_k]]


def _beam_graph_spell_correction(
    index: GraphIndex,
    raw_tokens: list[str],
    top_k: int = 5,
    max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    beam_width: int = 12,
) -> list[QueryMatch]:
    """Sửa lỗi chính tả đa từ bằng Beam Search hai chiều trên đồ thị."""
    n = len(raw_tokens)
    if n < 2:
        return []
    if not any(len(t) >= 2 and t.isalpha() for t in raw_tokens):
        return []

    fwd, rev = index.fwd_graph, index.rev_graph
    fwd_prob = index.fwd_prob
    real_vocab = index.real_vocab
    ua_map = index.unaccented_map

    has_accent = any(t != remove_accents(t) and t in real_vocab for t in raw_tokens)
    if has_accent:
        bad_indices = [
            i
            for i, tok in enumerate(raw_tokens)
            if (len(tok) >= 2 and tok.isalpha() and tok not in real_vocab and tok not in ua_map)
            or (i > 0 and raw_tokens[i - 1] in fwd and fwd.get(raw_tokens[i - 1], {}).get(tok, 0) == 0)
            or (i + 1 < n and raw_tokens[i + 1] in rev and fwd.get(tok, {}).get(raw_tokens[i + 1], 0) == 0)
        ]
        if bad_indices:
            spans: list[tuple[int, int]] = []
            for idx in bad_indices:
                st = max(0, idx - 1)
                ed = min(n, idx + 2)
                if spans and st <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(spans[-1][1], ed))
                else:
                    spans.append((st, ed))
            if len(spans) > 1 or (spans[0][1] - spans[0][0]) < n:
                repaired = list(raw_tokens)
                any_repaired = False
                min_sim_all = 1.0
                total_freq = 0
                span_count = 0
                for st, ed in spans:
                    sub_matches = _beam_graph_spell_correction(
                        index, repaired[st:ed], top_k=1, max_diff_k=max_diff_k, beam_width=beam_width
                    )
                    if sub_matches:
                        sub_words = sub_matches[0].phrase.split()
                        if len(sub_words) == ed - st and sub_words != repaired[st:ed]:
                            repaired[st:ed] = sub_words
                            any_repaired = True
                            total_freq += sub_matches[0].freq
                            span_count += 1
                            min_sim_all = min(min_sim_all, sub_matches[0].confidence)
                if any_repaired and not any(
                    w.isalpha() and len(w) >= 2 and w not in real_vocab for w in repaired
                ):
                    m_prob = max(index.dict_freq.get(repaired[0], 1), 1) / 100000.0
                    for k in range(n - 1):
                        m_prob *= fwd_prob.get(repaired[k], {}).get(repaired[k + 1], 1e-6)
                    m = QueryMatch(
                        phrase=" ".join(repaired),
                        freq=max(1, total_freq // max(span_count, 1)),
                        missing_count=0,
                        superstring_diff=0,
                        source_phrase="",
                        missing_words=[],
                        context_tier=2,
                        context_span_len=span_count,
                        markov_prob=m_prob,
                    )
                    m.confidence = round(min(0.95, max(0.5, min_sim_all)), 4)
                    return [m]
                return []
    elif len(index.build_kb_horizons(raw_tokens)) > 2:
        return []

    strong_anchors = [
        i
        for i, tok in enumerate(raw_tokens)
        if tok != remove_accents(tok) and tok in real_vocab
    ]

    def _expand_from_seed(seed_pos: int, seed_words: list[str]) -> list[tuple[list[str], float, float]]:
        beam: list[tuple[list[str], float, float]] = []
        for sw in seed_words:
            if sw not in real_vocab:
                continue
            buf = [""] * n
            buf[seed_pos] = sw
            sim0 = 1.0 if sw == raw_tokens[seed_pos] else token_similarity(raw_tokens[seed_pos], sw)
            beam.append((buf, 0.0, sim0))
        if not beam:
            return []

        def _step(j: int, prev_idx: int, go_right: bool) -> None:
            nonlocal beam
            tok = raw_tokens[j]
            ua_tok = remove_accents(tok)
            tok_accented = tok != ua_tok and tok in real_vocab
            tok_is_valid_ua = (not tok_accented) and (tok in ua_map)
            min_s = 0.72 if tok_is_valid_ua else 0.65
            next_beam: list[tuple[list[str], float, float]] = []
            seen_states: set[tuple[str, str]] = set()

            for buf, score, min_sim in beam:
                anchor_w = buf[prev_idx]
                nbrs = fwd.get(anchor_w, {}) if go_right else rev.get(anchor_w, {})
                if not nbrs:
                    continue
                for w in nbrs:
                    if not w or (w.startswith("<") and w != tok):
                        continue
                    edit_weight = 3.0
                    if tok_accented:
                        if w == tok:
                            sim = 1.0
                        elif is_vn_orthographic_confusion(tok, w):
                            sim = max(0.8, token_similarity(tok, w))
                        else:
                            continue
                    else:
                        if w == tok:
                            sim = 1.0
                        else:
                            ua_w = remove_accents(w)
                            if abs(len(ua_w) - len(ua_tok)) > max(2, len(ua_tok) // 3):
                                continue
                            if ua_w == ua_tok:
                                sim = 1.0
                            else:
                                sim = token_similarity(ua_tok, ua_w)
                                if sim < min_s:
                                    continue
                                if tok_is_valid_ua:
                                    edit_weight = 6.0
                    p = (
                        fwd_prob.get(anchor_w, {}).get(w, 0.0)
                        if go_right
                        else fwd_prob.get(w, {}).get(anchor_w, 0.0)
                    )
                    if p <= 0.0:
                        continue
                    new_score = score + math.log(p) + edit_weight * math.log(max(sim, 0.1))
                    new_buf = list(buf)
                    new_buf[j] = w
                    state_key = (anchor_w, w)
                    if state_key in seen_states and len(next_beam) >= beam_width * 2:
                        continue
                    seen_states.add(state_key)
                    next_beam.append((new_buf, new_score, min(min_sim, sim)))

            if not next_beam:
                beam = []
                return
            next_beam.sort(key=lambda x: -x[1])
            beam = next_beam[:beam_width]

        for j in range(seed_pos + 1, n):
            _step(j, j - 1, go_right=True)
            if not beam:
                return []
        for j in range(seed_pos - 1, -1, -1):
            _step(j, j + 1, go_right=False)
            if not beam:
                return []
        return beam

    raw_paths: list[tuple[list[str], float, float]] = []
    if strong_anchors:
        seed_positions = [strong_anchors[0]]
        if len(strong_anchors) >= 2 and strong_anchors[-1] != strong_anchors[0]:
            seed_positions.append(strong_anchors[-1])
        for pos in seed_positions:
            raw_paths.extend(_expand_from_seed(pos, [raw_tokens[pos]]))
    else:
        left_seeds = list(ua_map.get(raw_tokens[0], ()))[:6]
        if not left_seeds and raw_tokens[0] in real_vocab:
            left_seeds = [raw_tokens[0]]
        if left_seeds:
            raw_paths.extend(_expand_from_seed(0, left_seeds))

        right_seeds = list(ua_map.get(raw_tokens[-1], ()))[:6]
        if not right_seeds and raw_tokens[-1] in real_vocab:
            right_seeds = [raw_tokens[-1]]
        if right_seeds:
            raw_paths.extend(_expand_from_seed(n - 1, right_seeds))

    if not raw_paths:
        return []

    has_nonword = any(
        len(t) >= 2 and t.isalpha() and t not in real_vocab and t not in ua_map
        for t in raw_tokens
    )
    min_kb_freq = 1 if has_nonword else index.strong_kb_freq

    found: dict[str, tuple[tuple[int, int, float, int], QueryMatch]] = {}
    for fixed_words, _bscore, min_sim in raw_paths:
        if fixed_words == raw_tokens:
            continue
        if any(fwd.get(fixed_words[k], {}).get(fixed_words[k + 1], 0) == 0 for k in range(n - 1)):
            continue

        kb_freq, kb_diff, kb_src = index.lookup_real_phrase_score(
            fixed_words, max_diff_k=max_diff_k
        )
        if kb_freq <= 0 and n >= 4:
            win_freqs: list[int] = []
            for k in range(n - 2):
                wf, _, _ = index.lookup_real_phrase_score(
                    fixed_words[k : k + 3], max_diff_k=max_diff_k
                )
                win_freqs.append(wf)
            if win_freqs and (
                all(wf > 0 for wf in win_freqs)
                or (has_nonword and any(wf > 0 for wf in win_freqs))
            ):
                kb_freq = max(win_freqs) if has_nonword else min(win_freqs)
                kb_diff = 1
                kb_src = ""
        if kb_freq < min_kb_freq or (not has_nonword and kb_diff > 0):
            continue

        n_mutated_valid = sum(
            1
            for raw_t, fix_w in zip(raw_tokens, fixed_words)
            if raw_t in ua_map and remove_accents(fix_w) != remove_accents(raw_t)
        )
        m_prob = max(index.dict_freq.get(fixed_words[0], 1), 1) / 100000.0
        for k in range(n - 1):
            m_prob *= fwd_prob.get(fixed_words[k], {}).get(fixed_words[k + 1], 1e-6)

        phrase_str = " ".join(fixed_words)
        m = QueryMatch(
            phrase=phrase_str,
            freq=kb_freq,
            missing_count=0,
            superstring_diff=kb_diff,
            source_phrase=kb_src,
            missing_words=[],
            context_tier=2,
            context_span_len=min(n - 1, 2),
            markov_prob=m_prob,
        )
        m.confidence = round(min(0.96, compute_confidence(m) * (0.50 + 0.50 * min_sim)), 4)
        rank_k = (0, kb_diff, n_mutated_valid, -round(min_sim, 3), -kb_freq)
        if phrase_str not in found or rank_k < found[phrase_str][0]:
            found[phrase_str] = (rank_k, m)

    return [m for _, m in sorted(found.values(), key=lambda x: x[0])[:top_k]]
