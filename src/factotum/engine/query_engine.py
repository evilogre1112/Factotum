from itertools import product

from factotum.engine.context import is_normalized_anchor, query_with_context
from factotum.core.extension import (
    clean_word,
    get_compatible_token_classes,
    is_class_token,
    is_valid_vietnamese_syllable,
    remove_accents,
)
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import (
    MAX_SUPERSTRING_DIFF_K,
    QueryMatch,
    compute_confidence,
    rank_key,
)
from factotum.engine.reviewer import review_matches
from factotum.engine.search import (
    expand_query_variants,
    query_by_kb_composition,
    search_variant,
    split_sticky_token,
)
from factotum.engine.spell import preprocess_tokens, try_graph_spell_correction


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
    tokens: list[str], max_variants: int = 4
) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Sinh tối đa `max_variants` biến thể nhãn lớp tương thích cho các token số/mã/ngày trong câu."""
    per_pos: list[list[tuple[str, tuple[str, str] | None]]] = []
    prev = ""
    for tok in tokens:
        compat = get_compatible_token_classes(tok, prev_tok=prev)
        if compat and not is_class_token(tok):
            per_pos.append([(cls, (cls, tok)) for cls in compat])
            prev = compat[0]
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
                for m in ctx_matches:
                    m.confidence = compute_confidence(m)
                return _restore_class_tokens(ctx_matches, class_slots)
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
                for m in ctx_matches:
                    m.confidence = compute_confidence(m)
                return _restore_class_tokens(ctx_matches, class_slots)

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

        ranked = sorted(all_matches.values(), key=rank_key)[: top_k + 4]
        for m in ranked:
            m.confidence = compute_confidence(m)
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

        raw_tokens, preprocessed = preprocess_tokens(self, orig_tokens)
        class_variants = _expand_class_variants(raw_tokens)

        if len(class_variants) == 1:
            norm_tokens, class_slots = class_variants[0]
            # Pha 1: Sinh ứng viên thuần đồ thị/KB
            raw_matches = self._query_single_variant(
                norm_tokens, class_slots, raw_query, preprocessed, top_k, allow_missing, max_diff_k
            )
            # Pha 2: Hậu kiểm độc lập trên toàn bộ câu đáp án vừa tạo
            return review_matches(self, raw_matches, fallback_phrase, top_k)

        merged: dict[str, QueryMatch] = {}
        for norm_tokens, class_slots in class_variants:
            for m in self._query_single_variant(
                norm_tokens, class_slots, raw_query, preprocessed, top_k, allow_missing, max_diff_k
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
