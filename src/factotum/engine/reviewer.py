from factotum.core.extension import is_dangling_phrase
from factotum.engine.graph_index import GraphIndex
from factotum.engine.models import QueryMatch


def review_matches(
    index: GraphIndex,
    matches: list[QueryMatch],
    fallback_phrase: str,
    top_k: int,
) -> list[QueryMatch]:
    """
    Tầng hậu kiểm độc lập (Post-Review): nhìn lại toàn bộ câu đáp án đã tạo ở pha sinh.
    - Loại bỏ các câu kết thúc cụt lủn theo file tùy chỉnh `output/dangling_end_words.txt`.
    - Nếu không còn phương án hợp lệ nào, trả về nguyên văn câu gốc kèm `is_fallback=True`.
    """
    had_dangling = any(is_dangling_phrase(m.phrase) for m in matches)
    valid: list[QueryMatch] = []
    for m in matches:
        if is_dangling_phrase(m.phrase):
            continue
        if had_dangling and m.superstring_diff < 0:
            words = m.phrase.split()
            if len(words) >= 2 and index.fwd_graph.get(words[-2], {}).get(words[-1], 0) == 0:
                continue
        valid.append(m)

    if valid:
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
