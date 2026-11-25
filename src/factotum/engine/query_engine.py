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
            if ctx_matches and any(m.superstring_diff >= 0 for m in ctx_matches):
                return _restore_class_tokens(finalize_matches(ctx_matches, preserve_kb_order=True), class_slots)
            spell_matches = try_graph_spell_correction(
                self, norm_tokens, top_k=top_k, max_diff_k=max_diff_k
            )
            if spell_matches and (
                not ctx_matches
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

        # Với câu đã có dấu chuẩn nhưng chưa khớp cụm KB mạnh (sd == 0 hoặc freq >= 10):
        # thử chèn tối đa 1 từ thiếu ở giữa bằng direct_kb_search (chỉ nhận cụm KB thật
        # khi cạnh gốc tại khe hở bị đứt hoặc yếu hơn rõ rệt so với cầu nối qua từ chèn).
        if (
            sentence_has_accent
            and not has_unknown
            and allow_missing
            and 2 <= n_raw <= 8
            and not any(m.superstring_diff == 0 or m.freq >= 10 for m in all_matches.values())
        ):
            miss_kb = direct_kb_search(
                self, [[t] for t in norm_tokens], allow_missing=True, max_diff_k=max_diff_k
            )
            added_miss = False
            for m in miss_kb:
                if m.missing_count != 1 or not (m.superstring_diff == 0 or m.freq >= 10):
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

            # Nếu cả câu dài chưa nằm gọn trong 1 cụm KB nhưng có đúng 1 cạnh đứt (direct_e == 0),
            # thử chèn 1 từ nối `mid` tại cạnh đứt sao cho nối thông `left -> mid -> right` và khớp cụm KB mạnh quanh khe hở.
            if not added_miss and n_raw >= 4:
                broken_positions = [
                    i for i in range(1, n_raw)
                    if self.fwd_graph.get(norm_tokens[i - 1], {}).get(norm_tokens[i], 0) == 0
                ]
                if len(broken_positions) == 1:
                    gap_i = broken_positions[0]
                    left_w, right_w = norm_tokens[gap_i - 1], norm_tokens[gap_i]
                    best_mid = ""
                    best_mid_score = (0, 0)
                    for mid_w, _p1 in self.fwd_prob_top.get(left_w, ()):
                        if is_class_token(mid_w) or right_w not in self.fwd_graph.get(mid_w, ()):
                            continue
                        trial = norm_tokens[:gap_i] + [mid_w] + norm_tokens[gap_i:]
                        # Kiểm tra cụm 3–4 từ chứa `mid_w` có thật và mạnh trong KB (freq >= 10)
                        kb_hit = 0
                        for wlen in (4, 3):
                            for s_idx in range(max(0, gap_i - wlen + 1), min(gap_i + 1, len(trial) - wlen + 1)):
                                f_w, d_w, _ = self.lookup_real_phrase_score(trial[s_idx : s_idx + wlen], max_diff_k=0)
                                if f_w >= 10 and d_w == 0:
                                    kb_hit = max(kb_hit, f_w)
                        if kb_hit >= 10:
                            e_min = min(
                                self.fwd_graph.get(left_w, {}).get(mid_w, 0),
                                self.fwd_graph.get(mid_w, {}).get(right_w, 0),
                            )
                            sc = (kb_hit, e_min)
                            if sc > best_mid_score:
                                best_mid_score = sc
                                best_mid = mid_w
                    if best_mid:
                        trial = norm_tokens[:gap_i] + [best_mid] + norm_tokens[gap_i:]
                        m_prob = max(self.dict_freq.get(trial[0], 1), 1) / 100000.0
                        for i in range(len(trial) - 1):
                            m_prob *= self.fwd_prob.get(trial[i], {}).get(trial[i + 1], 1e-6)
                        cand = QueryMatch(
                            phrase=" ".join(trial),
                            freq=best_mid_score[0],
                            missing_count=1,
                            superstring_diff=0,
                            source_phrase="",
                            missing_words=[best_mid],
                            markov_prob=m_prob,
                            edits=[EditSpan(orig="", start=gap_i, end=gap_i + 1, kind="missing_insert")],
                        )
                        all_matches[cand.phrase] = cand

        if not any(m.superstring_diff >= 0 for m in all_matches.values()):
            if 2 <= n_raw <= 8:
                for m in query_by_kb_composition(
                    self, norm_tokens, top_k=top_k, max_diff_k=max_diff_k
                ):
                    all_matches[m.phrase] = m

        if not any(m.superstring_diff >= 0 for m in all_matches.values()):
            spell_matches = try_graph_spell_correction(
                self, norm_tokens, top_k=top_k, max_diff_k=max_diff_k
            )
            if spell_matches:
                return _restore_class_tokens(spell_matches, class_slots)

        ranked = finalize_matches(list(all_matches.values()), top_k=top_k + 4, sort_markov_by_conf=False)
        return _restore_class_tokens(ranked, class_slots)

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
