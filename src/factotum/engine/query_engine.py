from itertools import product

from factotum.engine.context import is_normalized_anchor, query_with_context
from factotum.core.extension import (
    _AMBIGUOUS_ROMAN,
    _ROMAN_PREFIX_WORDS,
    clean_word,
    get_compatible_token_classes,
    is_class_token,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import (
    MAX_SUPERSTRING_DIFF_K,
    EditSpan,
    QueryMatch,
    build_edit_spans,
    compute_confidence,
    finalize_matches,
    rank_key,
)
from factotum.engine.reviewer import review_matches
from factotum.engine.search import (
    direct_kb_search,
    expand_query_variants,
    query_by_kb_composition,
    search_variant,
    split_sticky_token,
)
from factotum.engine.spell import preprocess_tokens, try_graph_spell_correction


def _attach_worker_edits(orig_tokens: list[str], matches: list[QueryMatch]) -> list[QueryMatch]:
    """Gắn danh sách `edits` cho các ứng viên chưa có thông tin chỉnh sửa."""
    for m in matches:
        if m.is_fallback or m.edits:
            continue
        words = m.phrase.split()
        if words != orig_tokens or m.superstring_diff < 0:
            m.edits = build_edit_spans(
                orig_tokens,
                words,
                missing_words=m.missing_words,
                include_unaccented_unchanged=(m.superstring_diff < 0),
            )
    return matches


def _restore_class_tokens(
    matches: list[QueryMatch], class_slots: list[tuple[str, str]]
) -> list[QueryMatch]:
    """Khôi phục các nhãn lớp (<num>, <code>, <date>, <roman>) về đúng giá trị gốc người dùng đã gõ."""
    if not class_slots or not matches:
        return matches
    for m in matches:
        words = m.phrase.split()
        slot_idx = 0
        for i, w in enumerate(words):
            if slot_idx < len(class_slots) and w == class_slots[slot_idx][0]:
                words[i] = class_slots[slot_idx][1]
                slot_idx += 1
        m.phrase = " ".join(words)
        if m.source_phrase:
            src_words = m.source_phrase.split()
            slot_idx = 0
            for i, w in enumerate(src_words):
                if slot_idx < len(class_slots) and w == class_slots[slot_idx][0]:
                    src_words[i] = class_slots[slot_idx][1]
                    slot_idx += 1
            m.source_phrase = " ".join(src_words)
    return matches


def _expand_class_variants(
    tokens: list[str],
    max_variants: int = 4,
    ua_map: dict[str, list[str]] | None = None,
) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Sinh tối đa `max_variants` biến thể nhãn lớp tương thích cho các token số/mã/ngày/La Mã trong câu."""
    per_pos: list[list[tuple[str, tuple[str, str] | None]]] = []
    prev = ""
    for tok in tokens:
        compat = get_compatible_token_classes(tok, prev_tok=prev)
        if compat and not is_class_token(tok):
            per_pos.append([(cls, (cls, tok)) for cls in compat])
            prev = compat[0]
        elif (
            ua_map
            and tok in _AMBIGUOUS_ROMAN
            and prev
            and any(w in _ROMAN_PREFIX_WORDS for w in ua_map.get(prev, ()))
        ):
            per_pos.append([("<roman>", ("<roman>", tok)), (tok, None)])
            prev = "<roman>"
        else:
            per_pos.append([(tok, None)])
            prev = tok

    variants: list[tuple[list[str], list[tuple[str, str]]]] = []
    for combo in product(*per_pos):
        norm_toks = [item[0] for item in combo]
        slots = [item[1] for item in combo if item[1] is not None]
        variants.append((norm_toks, slots))
        if len(variants) >= max_variants:
            break
    return variants


class GraphQueryEngine(GraphIndex):
    def split_sticky_token(self, token: str) -> list[list[str]]:
        return split_sticky_token(self, token)

    def _query_single_variant(
        self,
        norm_tokens: list[str],
        class_slots: list[tuple[str, str]],
        raw_query: str,
        preprocessed: bool,
        top_k: int,
        allow_missing: bool,
        max_diff_k: int,
    ) -> list[QueryMatch]:
        effective_query = " ".join(norm_tokens) if (preprocessed or class_slots) else raw_query

        sentence_has_accent = any(
            t != remove_accents(t) and t in self.real_vocab for t in norm_tokens
        )
        n_raw = len(norm_tokens)

        if sentence_has_accent and all(len(t) >= 2 or is_class_token(t) for t in norm_tokens):
            exact_phrase = " ".join(norm_tokens)
            exact_freq = self.dict_freq.get(exact_phrase, 0)
            if exact_freq > 0:
                m = QueryMatch(exact_phrase, exact_freq, 0, 0, exact_phrase, [], markov_prob=1.0)
                m.confidence = compute_confidence(m)
                return _restore_class_tokens([m], class_slots)

        if not sentence_has_accent and not class_slots:
            if n_raw >= 2 and all(len(t) >= 2 and is_valid_vietnamese_syllable(t) for t in norm_tokens):
                ua_pids = self.get_ua_pids(" ".join(norm_tokens))
                if ua_pids and (not allow_missing or len(ua_pids) >= top_k):
                    return self.pids_to_exact_matches(ua_pids, top_k + 4)
            elif n_raw == 1 and len(norm_tokens[0]) >= 3 and not is_valid_vietnamese_syllable(norm_tokens[0]):
                acr_pids = self.get_acronym_pids(norm_tokens[0])
                if acr_pids:
                    return self.pids_to_exact_matches(acr_pids, top_k + 4)

        anchor_flags = [
            is_normalized_anchor(
                self,
                norm_tokens[i],
                sentence_has_accent=sentence_has_accent,
                prev_tok=norm_tokens[i - 1] if i > 0 else "",
                next_tok=norm_tokens[i + 1] if i + 1 < n_raw else "",
            )
            for i in range(n_raw)
        ]
        has_anchor = any(anchor_flags)
        has_unknown = not all(anchor_flags)

        if has_anchor and has_unknown:
            ctx_matches = query_with_context(
                self,
                norm_tokens,
                top_k=top_k,
                allow_missing=allow_missing,
                max_diff_k=max_diff_k,
                sentence_has_accent=sentence_has_accent,
            )
            ctx_words = ctx_matches[0].phrase.split() if ctx_matches else []
            ctx_has_unresolved_oov = bool(
                ctx_words
                and any(
                    len(t) >= 2 and t.isalpha() and t not in self.real_vocab and t not in self.unaccented_map
                    for t in ctx_words
                )
            )
            raw_has_nonword = any(
                len(t) >= 2 and t.isalpha() and t not in self.real_vocab and t not in self.unaccented_map
                for t in norm_tokens
            )
            ctx_has_broken = bool(
                raw_has_nonword
                and len(ctx_words) >= 2
                and any(
                    self.fwd_graph.get(ctx_words[k], {}).get(ctx_words[k + 1], 0) == 0
                    for k in range(len(ctx_words) - 1)
                )
            )
            if (
                ctx_matches
                and not ctx_has_unresolved_oov
                and not ctx_has_broken
                and any(m.superstring_diff >= 0 for m in ctx_matches)
            ):
                return _restore_class_tokens(finalize_matches(ctx_matches, preserve_kb_order=True), class_slots)
            spell_matches = try_graph_spell_correction(
                self, norm_tokens, top_k=top_k, max_diff_k=max_diff_k
            )
            if spell_matches and (
                not ctx_matches
                or ctx_has_unresolved_oov
                or ctx_has_broken
                or any(m.superstring_diff >= 0 for m in spell_matches)
                or spell_matches[0].confidence > compute_confidence(ctx_matches[0])
            ):
                return _restore_class_tokens(spell_matches, class_slots)
            if ctx_matches:
                return _restore_class_tokens(finalize_matches(ctx_matches, preserve_kb_order=True), class_slots)

        if sentence_has_accent and not has_unknown:
            # Mọi từ trong câu đã có dấu đều đã được xác nhận là từ neo chuẩn (anchor):
            # khóa cứng các từ neo, không bung lại từ thanh ngang ("bao", "chi") qua UnaccentedMap.
            variants = [[[t] for t in norm_tokens]]
            eff_allow_missing = False
        else:
            variants = expand_query_variants(self, effective_query)
            eff_allow_missing = allow_missing
        all_matches: dict[str, QueryMatch] = {}

        for columns in variants:
            matches = search_variant(
                self, columns, allow_missing=eff_allow_missing, max_diff_k=max_diff_k
            )
            for m in matches:
                prev = all_matches.get(m.phrase)
                if prev is None or rank_key(m) < rank_key(prev):
                    all_matches[m.phrase] = m

        # Chèn từ thiếu cho câu đã có dấu tại các vị trí đứt cạnh.
        strong_f = self.strong_kb_freq
        if (
            sentence_has_accent
            and not has_unknown
            and allow_missing
            and n_raw >= 2
            and not any(m.superstring_diff == 0 or m.freq >= strong_f for m in all_matches.values())
        ):
            added_miss = False
            max_kb_l = max(self.len_to_phrases, default=8)
            if n_raw <= max_kb_l:
                miss_kb = direct_kb_search(
                    self, [[t] for t in norm_tokens], allow_missing=True, max_diff_k=max_diff_k
                )
                for m in miss_kb:
                    if m.missing_count != 1 or not (m.superstring_diff == 0 or m.freq >= strong_f):
                        continue
                    m_words = m.phrase.split()
                    if len(m_words) != n_raw + 1:
                        continue
                    gap_idx = next(
                        (i for i in range(1, n_raw) if m_words[:i] == norm_tokens[:i] and m_words[i + 1 :] == norm_tokens[i:]),
                        -1,
                    )
                    if gap_idx < 0:
                        continue
                    left_w, mid_w, right_w = norm_tokens[gap_idx - 1], m_words[gap_idx], norm_tokens[gap_idx]
                    direct_e = self.fwd_graph.get(left_w, {}).get(right_w, 0)
                    bridge_e = min(
                        self.fwd_graph.get(left_w, {}).get(mid_w, 0),
                        self.fwd_graph.get(mid_w, {}).get(right_w, 0),
                    )
                    if direct_e == 0 or (bridge_e > 0 and direct_e * 5 < bridge_e):
                        prev = all_matches.get(m.phrase)
                        if prev is None or rank_key(m) < rank_key(prev):
                            all_matches[m.phrase] = m
                            added_miss = True

            if not added_miss and n_raw >= 3:
                broken_positions = [
                    i for i in range(1, n_raw)
                    if self.fwd_graph.get(norm_tokens[i - 1], {}).get(norm_tokens[i], 0) == 0
                ]
                if broken_positions:
                    cur_tokens = list(norm_tokens)
                    inserted_words: list[str] = []
                    insert_edits: list[EditSpan] = []
                    min_kb_hit = 0
                    offset = 0
                    for orig_gap in broken_positions:
                        gap_i = orig_gap + offset
                        left_w, right_w = cur_tokens[gap_i - 1], cur_tokens[gap_i]
                        best_mid = ""
                        best_mid_score = (0, 0)
                        for mid_w, _p1 in self.fwd_prob_top.get(left_w, ()):
                            if is_class_token(mid_w) or right_w not in self.fwd_graph.get(mid_w, ()):
                                continue
                            trial = cur_tokens[:gap_i] + [mid_w] + cur_tokens[gap_i:]
                            kb_hit, _ = self.score_kb_bridge(
                                trial, gap_i, max_diff_k=max_diff_k, require_tail_on_subphrase=True
                            )
                            if kb_hit == 0:
                                eff_diff_k = 0 if strong_f > 1 else max_diff_k
                                f_w, d_w, _ = self.lookup_real_phrase_score(
                                    trial[gap_i - 1 : gap_i + 2], max_diff_k=eff_diff_k
                                )
                                if f_w >= strong_f and d_w >= 0:
                                    kb_hit = f_w
                            if kb_hit > 0:
                                e_min = min(
                                    self.fwd_graph.get(left_w, {}).get(mid_w, 0),
                                    self.fwd_graph.get(mid_w, {}).get(right_w, 0),
                                )
                                sc = (kb_hit, e_min)
                                if sc > best_mid_score:
                                    best_mid_score = sc
                                    best_mid = mid_w
                        if best_mid:
                            cur_tokens = cur_tokens[:gap_i] + [best_mid] + cur_tokens[gap_i:]
                            inserted_words.append(best_mid)
                            insert_edits.append(EditSpan(orig="", start=gap_i, end=gap_i + 1, kind="missing_insert"))
                            min_kb_hit = best_mid_score[0] if min_kb_hit == 0 else min(min_kb_hit, best_mid_score[0])
                            offset += 1
                    if inserted_words:
                        m_prob = self.markov_prob(cur_tokens)
                        cand = QueryMatch(
                            phrase=" ".join(cur_tokens),
                            freq=min_kb_hit,
                            missing_count=len(inserted_words),
                            superstring_diff=0,
                            source_phrase="",
                            missing_words=inserted_words,
                            markov_prob=m_prob,
                            edits=insert_edits,
                        )
                        all_matches[cand.phrase] = cand

        if not any(m.superstring_diff >= 0 for m in all_matches.values()):
            if n_raw >= 2 and len(self.build_kb_horizons(norm_tokens)) <= 3:
                for m in query_by_kb_composition(
                    self, norm_tokens, top_k=top_k, max_diff_k=max_diff_k
                ):
                    all_matches[m.phrase] = m

        if not any(m.superstring_diff >= 0 for m in all_matches.values()):
            has_nonword = any(
                len(t) >= 2 and t.isalpha() and t not in self.real_vocab and t not in self.unaccented_map
                for t in norm_tokens
            )
            has_missing_bridge = any(m.missing_count > 0 for m in all_matches.values())
            all_edges_broken = True
            if all_matches:
                top_cand = min(all_matches.values(), key=rank_key)
                tw = top_cand.phrase.split()
                all_edges_broken = len(tw) >= 2 and all(
                    self.fwd_graph.get(tw[k], {}).get(tw[k + 1], 0) == 0 for k in range(len(tw) - 1)
                )
            spell_matches = try_graph_spell_correction(
                self,
                norm_tokens,
                top_k=top_k,
                max_diff_k=max_diff_k,
                allow_beam=(has_nonword or (all_edges_broken and not has_missing_bridge)),
            )
            if spell_matches:
                for m in spell_matches:
                    prev = all_matches.get(m.phrase)
                    if prev is None or rank_key(m) < rank_key(prev):
                        all_matches[m.phrase] = m

        ranked = finalize_matches(list(all_matches.values()), top_k=top_k + 4, sort_markov_by_conf=False)
        return _restore_class_tokens(ranked, class_slots)

    def _query_long_linear_stability(
        self,
        orig_tokens: list[str],
        horizons: list[tuple[int, int]],
        top_k: int,
        allow_missing: bool,
        max_diff_k: int,
    ) -> list[QueryMatch]:
        """Giải mã cuốn chiếu câu dài theo các đoạn giao KB và độ ổn định tiền tố."""
        n = len(orig_tokens)
        horizon_ends = [ed for _, ed in horizons]
        committed: list[str] = []
        anchor_words: list[str] = []
        pos = 0
        chunk_confs: list[float] = []

        def _decode_chunk(tokens_chunk: list[str]) -> tuple[list[str], list[EditSpan], float]:
            sub_q = " ".join(tokens_chunk)
            ms = self._query_direct(tokens_chunk, sub_q, top_k=1, allow_missing=allow_missing, max_diff_k=max_diff_k)
            if not ms:
                return list(tokens_chunk), [], 0.0
            return ms[0].phrase.split(), ms[0].edits, ms[0].confidence

        while pos < n:
            anc_len = len(anchor_words)
            future_ends = [ed for ed in horizon_ends if ed > pos]
            if not future_ends:
                k = n
            else:
                k = future_ends[0]
                if (k - pos) <= 2 and len(future_ends) >= 2:
                    k = future_ends[1]

            if k >= n:
                words_out, _, conf = _decode_chunk(anchor_words + orig_tokens[pos:])
                body = words_out[anc_len:] if len(words_out) >= anc_len and words_out[:anc_len] == anchor_words else words_out
                committed.extend(body)
                chunk_confs.append(conf)
                break

            span_len = k - pos
            next_horizon_end = next((ed for ed in future_ends if ed > k), min(n, k + span_len))
            max_probe = min(n, max(k + 2, next_horizon_end))

            best_commit_toks = 0
            best_commit_words: list[str] = []
            best_conf = 0.0

            w_k, edits_k, conf_k = _decode_chunk(anchor_words + orig_tokens[pos:k])
            body_k = w_k[anc_len:] if len(w_k) >= anc_len and w_k[:anc_len] == anchor_words else w_k

            for k_next in range(k + 1, max_probe + 1):
                w_k1, edits_k1, conf_k1 = _decode_chunk(anchor_words + orig_tokens[pos:k_next])
                body_k1 = w_k1[anc_len:] if len(w_k1) >= anc_len and w_k1[:anc_len] == anchor_words else w_k1

                lcp_len = 0
                lim = min(len(body_k), len(body_k1))
                while lcp_len < lim and body_k[lcp_len] == body_k1[lcp_len]:
                    lcp_len += 1

                max_tok_step = max(1, (k - pos) - 1) if (k - pos) > 2 else (k - pos)
                if len(body_k) == (k - pos):
                    tok_consumed = min(lcp_len, max_tok_step)
                    word_cursor = tok_consumed
                else:
                    idx_t = 0
                    idx_w = 0
                    while idx_t < max_tok_step and idx_w < lcp_len:
                        miss_ed = next(
                            (e for e in edits_k if e.start - anc_len == idx_w and e.kind == "missing_insert"),
                            None,
                        )
                        if miss_ed is not None:
                            span_w = miss_ed.end - miss_ed.start
                            if idx_w + span_w <= lcp_len:
                                idx_w += span_w
                                continue
                            break
                        raw_t = orig_tokens[pos + idx_t]
                        matched_ed = next(
                            (e for e in edits_k if e.start - anc_len == idx_w and e.orig == raw_t),
                            None,
                        )
                        if matched_ed is not None:
                            span_w = matched_ed.end - matched_ed.start
                            if idx_w + span_w <= lcp_len:
                                idx_w += span_w
                                idx_t += 1
                            else:
                                break
                        else:
                            idx_w += 1
                            idx_t += 1
                    tok_consumed = idx_t
                    word_cursor = idx_w

                min_stable = max(1, (k - pos) // 2)
                if tok_consumed >= min_stable:
                    best_commit_toks = tok_consumed
                    best_commit_words = body_k[:word_cursor]
                    best_conf = max(conf_k, conf_k1)
                    break

                if tok_consumed > best_commit_toks:
                    best_commit_toks = tok_consumed
                    best_commit_words = body_k[:word_cursor]
                    best_conf = conf_k

                w_k, edits_k, conf_k = w_k1, edits_k1, conf_k1
                body_k = body_k1
                k = k_next

            if best_commit_toks <= 0:
                step = max(1, (k - pos) // 2)
                best_commit_toks = step
                best_commit_words = body_k[:step] if len(body_k) >= step else body_k
                best_conf = conf_k

            committed.extend(best_commit_words)
            chunk_confs.append(best_conf)
            anchor_words = committed[-2:] if len(committed) >= 2 else list(committed)
            pos += best_commit_toks

        if not committed:
            return []
        phrase_str = " ".join(committed)
        m_prob = self.markov_prob(committed)
        avg_conf = sum(chunk_confs) / len(chunk_confs) if chunk_confs else 0.5
        res = QueryMatch(
            phrase=phrase_str,
            freq=self.dict_freq.get(phrase_str, 0),
            missing_count=0,
            superstring_diff=-1,
            source_phrase="",
            missing_words=[],
            markov_prob=m_prob,
            confidence=round(min(0.95, max(0.1, avg_conf)), 4),
        )
        return [res]

    def _query_direct(
        self,
        orig_tokens: list[str],
        raw_query: str,
        top_k: int = 5,
        allow_missing: bool = True,
        max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    ) -> list[QueryMatch]:
        fallback_phrase = " ".join(orig_tokens)
        eff_top_k = max(top_k, 5)

        raw_tokens, preprocessed = preprocess_tokens(self, orig_tokens)
        class_variants = _expand_class_variants(raw_tokens, ua_map=self.unaccented_map)

        if len(class_variants) == 1:
            norm_tokens, class_slots = class_variants[0]
            # Pha 1 (Worker): Sinh ứng viên đồ thị/KB kèm `edits`
            raw_matches = _attach_worker_edits(
                orig_tokens,
                self._query_single_variant(
                    norm_tokens, class_slots, raw_query, preprocessed, eff_top_k, allow_missing, max_diff_k
                ),
            )
            # Pha 2 (Reviewer): Hậu kiểm cục bộ trên `edits` và xếp hạng toàn câu
            return review_matches(self, raw_matches, fallback_phrase, top_k)

        merged: dict[str, QueryMatch] = {}
        for norm_tokens, class_slots in class_variants:
            for m in _attach_worker_edits(
                orig_tokens,
                self._query_single_variant(
                    norm_tokens, class_slots, raw_query, preprocessed, eff_top_k, allow_missing, max_diff_k
                ),
            ):
                prev = merged.get(m.phrase)
                if prev is None or (m.confidence, m.freq) > (prev.confidence, prev.freq):
                    merged[m.phrase] = m

        ranked = sorted(
            merged.values(),
            key=lambda m: (-round(m.confidence, 4), m.missing_count, -m.freq, -m.markov_prob),
        )
        return review_matches(self, ranked, fallback_phrase, top_k)

    def query(
        self,
        raw_query: str,
        top_k: int = 5,
        allow_missing: bool = True,
        max_diff_k: int = MAX_SUPERSTRING_DIFF_K,
    ) -> list[QueryMatch]:
        """Truy vấn khôi phục dấu, viết tắt, thiếu từ và sửa lỗi chính tả cho 1 câu đầu vào."""
        orig_tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
        if not orig_tokens:
            return []
        accented_cnt = sum(1 for t in orig_tokens if t != remove_accents(t))
        if accented_cnt * 4 < len(orig_tokens):
            horizons = self.build_kb_horizons(orig_tokens)
            real_phrase_horizons = sum(1 for st, ed in horizons if (ed - st) >= 3)
            if len(horizons) > 3 and real_phrase_horizons >= 2:
                return self._query_long_linear_stability(
                    orig_tokens, horizons, top_k=top_k, allow_missing=allow_missing, max_diff_k=max_diff_k
                )
        return self._query_direct(
            orig_tokens, raw_query, top_k=top_k, allow_missing=allow_missing, max_diff_k=max_diff_k
        )


def main() -> None:
    engine = GraphQueryEngine()
    engine.load()
    print(
        f">> Đã nạp đồ thị: {len(engine.phrases)} cụm, "
        f"{len(engine.real_vocab)} từ đơn, {len(engine.unaccented_map)} khóa không dấu/viết tắt.\n"
    )

    while True:
        try:
            q = input("Nhập câu truy vấn (gõ 'exit' để thoát): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() == "exit":
            break

        matches = engine.query(q, top_k=5)
        if not matches:
            print("  => Không tìm thấy đường đi phù hợp trong đồ thị.\n")
            continue

        for i, m in enumerate(matches, start=1):
            tag = (
                "Trả về câu gốc (kết thúc cụt lủn / không hợp lệ)"
                if m.is_fallback
                else (
                    "Khớp chính xác"
                    if m.superstring_diff == 0
                    else (
                        f"Trong chuỗi tối đại '{m.source_phrase}' (lệch +{m.superstring_diff})"
                        if m.superstring_diff > 0
                        else "Lý thuyết Markov"
                    )
                )
            )
            miss_info = f" | Thiếu từ: {m.missing_words}" if m.missing_words else ""
            print(f"  [{i}] {m.phrase}  (freq={m.freq} | {tag}{miss_info})")
        print()


if __name__ == "__main__":
    main()
