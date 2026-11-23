from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import os
from pathlib import Path
import re
import sys
import time

_SENTENCE_SPLIT_RE = re.compile(r"(?:\.(?!\d)|[!?;\n\r]+)")

SRC_DIR = Path(__file__).resolve().parent.parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factotum.builder.suffix_array import (
    NgramResult,
    build_sa_lcp,
    extract_candidates,
)
from factotum.core.config import DEFAULT_PATHS, FactotumPaths, get_logger
from factotum.core.doc_reader import read_all_files
from factotum.core.extension import get_current_timestamp, is_single_consonant_bullet, split_and_clean

logger = get_logger("factotum.builder.extractor")


def filter_and_process_ngrams(
    candidates: list[tuple[int, int, int]],
    arr: list[str],
    sa: list[int],
    n: int,
) -> list[NgramResult]:
    """Lọc nhiễu ký hiệu mục đơn lẻ và loại chuỗi con trùng tần suất với chuỗi tối đại cha."""
    result: list[NgramResult] = []
    word_to_printed: dict[str, list[str]] = defaultdict(list)
    seen_phrases: set[str] = set()
    last_freq: int = -1

    for length, freq, sa_idx in candidates:
        if freq <= 1:
            continue
        if freq != last_freq:
            word_to_printed.clear()
            last_freq = freq

        start_pos = sa[sa_idx]
        if start_pos + length > n:
            length = n - start_pos

        tokens = arr[start_pos : start_pos + length]
        while tokens and is_single_consonant_bullet(tokens[0]):
            tokens = tokens[1:]
        while tokens and is_single_consonant_bullet(tokens[-1]):
            tokens = tokens[:-1]

        if not tokens:
            continue
        if any(
            is_single_consonant_bullet(tokens[idx])
            and (idx == 0 or tokens[idx - 1] != "điểm")
            for idx in range(len(tokens))
        ):
            continue

        shortest_list: list[str] | None = None
        for w in tokens:
            lst = word_to_printed.get(w)
            if not lst:
                shortest_list = None
                break
            if shortest_list is None or len(lst) < len(shortest_list):
                shortest_list = lst

        reconstructed = " ".join(tokens)
        if reconstructed in seen_phrases:
            continue
        padded_current = f" {reconstructed} "

        if shortest_list is not None:
            curr_len = len(padded_current)
            if any(curr_len <= len(p) and padded_current in p for p in shortest_list):
                continue

        seen_phrases.add(reconstructed)
        for w in set(tokens):
            word_to_printed[w].append(padded_current)
        result.append(NgramResult(freq, reconstructed))

    return result


def extract_phrases(raw_str: str) -> tuple[list[NgramResult], int, int]:
    """
    Trích xuất cụm lõi (Maximal Repeats) hoàn toàn trong RAM, không ghi file ra đĩa.
    Chèn sentinel duy nhất tại ranh giới câu/dòng (., !, ?, ;, \\n) để không dính cụm xuyên câu.
    Trả về: (danh sách NgramResult đã sắp xếp, tổng số từ N, tổng chuỗi con phân biệt).
    """
    segments = [seg for seg in _SENTENCE_SPLIT_RE.split(raw_str) if seg.strip()]
    arr: list[str] = []
    s: list[int] = []
    lookup: dict[str, int] = {}
    next_word_id = 1
    next_sep_id = -1
    word_count = 0

    for seg in segments:
        tokens = split_and_clean(seg)
        if not tokens:
            continue
        for tok in tokens:
            wid = lookup.get(tok)
            if wid is None:
                wid = next_word_id
                lookup[tok] = wid
                next_word_id += 1
            arr.append(tok)
            s.append(wid)
            word_count += 1
        # Sentinel âm duy nhất ngăn LCP vắt ngang sang câu tiếp theo
        arr.append("")
        s.append(next_sep_id)
        next_sep_id -= 1

    if word_count < 2:
        return [], word_count, 0

    n_total = len(s)
    sa, lcp = build_sa_lcp(s)
    total_unique = sum((n_total - sa[i]) - lcp[i] for i in range(1, n_total))

    candidates = extract_candidates(lcp, n_total)
    results = filter_and_process_ngrams(candidates, arr, sa, n_total)
    results.sort(key=lambda r: (r.text.count(" ") + 1, -r.freq))
    return results, word_count, total_unique


def _extract_worker(raw_str: str) -> list[NgramResult]:
    results, _, _ = extract_phrases(raw_str)
    return results


def extract_phrases_batch(
    texts: list[str],
    max_workers: int | None = None,
) -> list[list[NgramResult]]:
    """Trích xuất cụm lõi song song trên nhiều nhân CPU (dùng ProcessPoolExecutor khi >= 4 văn bản)."""
    if not texts:
        return []
    workers = max_workers or max(1, min(os.cpu_count() or 1, 8))
    if workers <= 1 or len(texts) < 4:
        return [_extract_worker(t) for t in texts]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_extract_worker, texts, chunksize=max(1, len(texts) // (workers * 4))))


def write_pure_log(filename: str | Path, results: list[NgramResult]) -> None:
    """Ghi danh sách cụm cốt lõi dạng CSV sạch vào thư mục Pure_Log."""
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("N_Gram,Count,Phrase\n")
        for res in results:
            n_gram = res.text.count(" ") + 1
            f.write(f'{n_gram},{res.freq},"{res.text}"\n')


def write_detailed_log(
    filename: str | Path,
    results: list[NgramResult],
    n: int,
    total_unique_substrings: int,
) -> None:
    """Ghi báo cáo chi tiết các cụm cốt lõi kèm thống kê N-gram."""
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("--- DANH SÁCH CÁC CỤM TỪ CỐT LÕI (NLP MAXIMAL REPEATS) ---\n\n")
        f.write("N_Gram,Count,Phrase\n")
        for res in results:
            n_gram = res.text.count(" ") + 1
            f.write(f'{n_gram},{res.freq},"{res.text}"\n')
        f.write("\n------------------------------------------------\n")
        f.write("THỐNG KÊ KHOA HỌC DỮ LIỆU:\n")
        f.write(f"Tổng số từ (N):,{n}\n")
        f.write(f"Số lượng chuỗi con thực tế PHÂN BIỆT:,{total_unique_substrings}\n")
        f.write(f"Số cụm từ cốt lõi sau lọc NLP:,{len(results)}\n")


def process_document(
    doc_name: str,
    raw_str: str,
    paths: FactotumPaths = DEFAULT_PATHS,
) -> list[NgramResult]:
    """Trích xuất cụm lõi từ 1 văn bản và xuất Log/Pure_Log theo `paths`."""
    t0 = time.perf_counter()
    final_results, n, total_unique = extract_phrases(raw_str)
    if n == 0:
        logger.warning("[%s] Văn bản không có từ hợp lệ sau khi làm sạch.", doc_name)
        return []

    timestamp = get_current_timestamp()
    safe_stem = Path(doc_name).stem.replace(" ", "_")
    detailed_filename = paths.log_dir / f"KetQua_Ngram_{safe_stem}_{timestamp}.txt"
    pure_filename = paths.pure_log_dir / f"Pure_Log_Ngram_{safe_stem}_{timestamp}.txt"

    write_detailed_log(detailed_filename, final_results, n, total_unique)
    write_pure_log(pure_filename, final_results)

    logger.info(
        "[%s] N=%d từ | %d cụm lõi (%.3fs)",
        doc_name,
        n,
        len(final_results),
        time.perf_counter() - t0,
    )
    return final_results


def main() -> None:
    input_folder = Path(sys.argv[1]) if len(sys.argv) > 1 else (DEFAULT_PATHS.data_dir or Path("."))
    t0 = time.perf_counter()
    docs = read_all_files(input_folder)
    if not docs:
        logger.warning("Không tìm thấy file .doc / .docx nào trong: %s", input_folder)
        return

    logger.info("Đã đọc %d file từ %s trong %.3fs.", len(docs), input_folder, time.perf_counter() - t0)
    for doc_name, raw_str in docs:
        process_document(doc_name, raw_str)


if __name__ == "__main__":
    main()
