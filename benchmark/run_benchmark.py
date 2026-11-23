from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import sys
import tempfile
import time

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BENCH_DIR.parent
SRC_DIR = PROJECT_DIR / "src"
DATA_DIR = PROJECT_DIR / "data"
sys.path.insert(0, str(SRC_DIR))

from factotum.builder import make_tree  # noqa: E402
from factotum.builder.extractor import (  # noqa: E402
    extract_phrases_batch,
    write_pure_log,
)
from factotum.core.config import FactotumPaths  # noqa: E402
from factotum.core.doc_reader import read_all_files  # noqa: E402
from factotum.core.extension import (  # noqa: E402
    clean_word,
    get_current_timestamp,
    is_single_consonant_bullet,
    is_valid_vietnamese_syllable,
    print_progress,
    remove_accents,
    split_and_clean,
)
from factotum.engine import GraphQueryEngine, MAX_CANDIDATES_PER_KEY  # noqa: E402

TEST_CASES_PATH = BENCH_DIR / "test_cases.json"
FAILURES_CSV_PATH = BENCH_DIR / "failures.csv"

_LOG_EPS = math.log(1e-8)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[idx]


def is_meaningful_phrase(engine: GraphQueryEngine, pred: str) -> bool:
    """
    Thước đo tự động chung cho cả 3 mô hình (Base-1g, Base-2g, Factotum):
    Chuỗi dự đoán có ít nhất 2 từ và mọi cặp 2 từ liền kề đều là cặp có thật
    trong ngữ liệu đã học (fwd_prob).
    """
    words = pred.strip().lower().split()
    if len(words) < 2:
        return False
    return all(words[i + 1] in engine.fwd_prob.get(words[i], {}) for i in range(len(words) - 1))


def baseline_unigram_topk(engine: GraphQueryEngine, raw_query: str, top_k: int = 3) -> list[str]:
    """
    Baseline 1 (Unigram Frequency 3-best):
    Với mỗi token, nếu đã có dấu trong từ điển thì giữ nguyên, không thì lấy
    ứng viên theo tần suất đơn lẻ trong UnaccentedMap và tìm top_k tổ hợp tổng log-freq cao nhất.
    """
    tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
    if not tokens:
        return []
    cols: list[list[str]] = []
    for tok in tokens:
        no_acc = remove_accents(tok)
        if tok != no_acc and tok in engine.real_vocab:
            cols.append([tok])
        elif no_acc in engine.unaccented_map:
            cols.append(engine.unaccented_map[no_acc][: max(top_k, 5)])
        else:
            pieces = [
                engine.unaccented_map[ch][: max(top_k, 5)]
                for ch in no_acc
                if ch in engine.unaccented_map
            ]
            if pieces:
                cols.extend(pieces)
            else:
                cols.append([tok])

    beam: list[tuple[float, list[str]]] = [(0.0, [])]
    for col in cols:
        nxt: list[tuple[float, list[str]]] = []
        for sc, seq in beam:
            for w in col:
                w_sc = math.log(max(engine.dict_freq.get(w, 1), 1))
                nxt.append((sc + w_sc, seq + [w]))
        nxt.sort(key=lambda x: x[0], reverse=True)
        beam = nxt[:top_k]
    return [" ".join(seq) for _, seq in beam]


def baseline_bigram_viterbi_topk(
    engine: GraphQueryEngine, raw_query: str, top_k: int = 3
) -> list[str]:
    """
    Baseline 2 (Bigram + Viterbi 3-best):
    Dùng xác suất chuyển trạng thái Bigram `fwd_prob` để giải mã top_k chuỗi tốt nhất
    (không dùng Maximal Repeats KB, không dùng Inverted Index, không dùng A-F-B, không phát hiện thiếu từ).
    """
    tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
    if not tokens:
        return []

    cols: list[list[str]] = []
    for tok in tokens:
        no_acc = remove_accents(tok)
        if tok != no_acc and tok in engine.real_vocab:
            cols.append([tok])
        elif no_acc in engine.unaccented_map:
            cols.append(engine.unaccented_map[no_acc][:MAX_CANDIDATES_PER_KEY])
        else:
            splits = engine.split_sticky_token(no_acc)
            if splits:
                for piece in splits[0]:
                    cols.append(engine.unaccented_map[piece][:MAX_CANDIDATES_PER_KEY])
            else:
                cols.append([tok])

    if not cols:
        return []

    # Lưu top_k đường đi tốt nhất kết thúc tại mỗi trạng thái w
    dp: dict[str, list[tuple[float, list[str]]]] = {
        w: [(math.log(max(engine.dict_freq.get(w, 1), 1) / 100000.0), [w])] for w in cols[0]
    }

    for col_idx in range(1, len(cols)):
        cur_cands = cols[col_idx]
        new_dp: dict[str, list[tuple[float, list[str]]]] = {}
        prev_items = list(dp.items())

        for v in cur_cands:
            paths: list[tuple[float, list[str]]] = []
            for u, u_paths in prev_items:
                p_uv = engine.fwd_prob.get(u, {}).get(v)
                trans = math.log(p_uv) if p_uv and p_uv > 0 else _LOG_EPS
                for prev_score, prev_seq in u_paths:
                    paths.append((prev_score + trans, prev_seq + [v]))
            paths.sort(key=lambda x: x[0], reverse=True)
            new_dp[v] = paths[:top_k]

        dp = new_dp

    all_final: list[tuple[float, list[str]]] = []
    for paths in dp.values():
        all_final.extend(paths)
    all_final.sort(key=lambda x: x[0], reverse=True)
    return [" ".join(seq) for _, seq in all_final[:top_k]]


def run_suite(
    engine: GraphQueryEngine,
    cases: list[dict[str, str]],
    suite_name: str,
    show_progress: bool = False,
    collect_all_failures: list[dict] | None = None,
    corpus_ngrams: set[str] | None = None,
) -> tuple[str, dict]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    all_latencies: list[float] = []
    all_b1_ms: list[float] = []
    all_b2_ms: list[float] = []
    failures: list[dict] = []
    calib_rows: list[tuple[float, bool, bool, bool]] = []

    def _is_pv(pred: str) -> bool:
        if corpus_ngrams is None:
            return is_meaningful_phrase(engine, pred)
        s = pred.strip().lower()
        return bool(s) and (s in corpus_ngrams or s in engine.dict_freq)

    total = len(cases)
    t_suite = time.perf_counter()
    last_draw = 0.0

    for idx, case in enumerate(cases, start=1):
        cat = case["category"]
        q = case["query"]
        expected = case["expected"].strip().lower()

        t0 = time.perf_counter()
        matches = engine.query(q, top_k=3)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        all_latencies.append(dt_ms)

        preds = [m.phrase.strip().lower() for m in matches]
        f_conf = matches[0].confidence if matches else 0.0
        is_top1 = bool(preds) and preds[0] == expected
        is_top3 = expected in preds
        is_ok = bool(preds) and _is_pv(preds[0])
        calib_rows.append((f_conf, is_top1, is_top3, is_ok))

        t1 = time.perf_counter()
        b1_preds = [p.strip().lower() for p in baseline_unigram_topk(engine, q, top_k=3)]
        b1_ms = (time.perf_counter() - t1) * 1000.0
        all_b1_ms.append(b1_ms)
        is_b1_t1 = bool(b1_preds) and b1_preds[0] == expected
        is_b1_t3 = expected in b1_preds
        is_b1_ok = bool(b1_preds) and _is_pv(b1_preds[0])

        t2 = time.perf_counter()
        b2_preds = [p.strip().lower() for p in baseline_bigram_viterbi_topk(engine, q, top_k=3)]
        b2_ms = (time.perf_counter() - t2) * 1000.0
        all_b2_ms.append(b2_ms)
        is_b2_t1 = bool(b2_preds) and b2_preds[0] == expected
        is_b2_t3 = expected in b2_preds
        is_b2_ok = bool(b2_preds) and _is_pv(b2_preds[0])

        if not is_top1:
            fail_item = {
                "suite": suite_name.splitlines()[0],
                "category": cat,
                "query": q,
                "expected": expected,
                "got_top1": preds[0] if preds else "",
                "got_top3": " | ".join(preds),
                "in_top3": is_top3,
                "confidence": round(f_conf, 3),
                "ms": round(dt_ms, 3),
            }
            failures.append(fail_item)
            if collect_all_failures is not None:
                collect_all_failures.append(fail_item)

        by_cat[cat].append(
            {
                "f_t1": is_top1,
                "f_t3": is_top3,
                "f_ok": is_ok,
                "f_ms": dt_ms,
                "b1_t1": is_b1_t1,
                "b1_t3": is_b1_t3,
                "b1_ok": is_b1_ok,
                "b1_ms": b1_ms,
                "b2_t1": is_b2_t1,
                "b2_t3": is_b2_t3,
                "b2_ok": is_b2_ok,
                "b2_ms": b2_ms,
            }
        )

        if show_progress:
            now = time.perf_counter()
            if now - last_draw >= 0.1 or idx == total:
                print_progress(idx, total, t_suite, label="Benchmark", unit="câu")
                last_draw = now

    def _fmt(rows: list[dict], prefix: str) -> str:
        n = len(rows) or 1
        t1 = sum(1 for r in rows if r[f"{prefix}_t1"]) / n * 100
        t3 = sum(1 for r in rows if r[f"{prefix}_t3"]) / n * 100
        ok = sum(1 for r in rows if r[f"{prefix}_ok"]) / n * 100
        ms = statistics.mean(r[f"{prefix}_ms"] for r in rows) if rows else 0.0
        return f"{t1:5.1f}/{t3:5.1f}/{ok:5.1f}% ({ms:4.2f}ms)"

    all_rows = [r for rows in by_cat.values() for r in rows]
    total_wall_s = time.perf_counter() - t_suite
    engine_s = sum(all_latencies) / 1000.0
    avg_ms = statistics.mean(all_latencies) if all_latencies else 0.0
    p50_ms = percentile(all_latencies, 50)
    p95_ms = percentile(all_latencies, 95)
    p99_ms = percentile(all_latencies, 99)
    max_ms = max(all_latencies) if all_latencies else 0.0
    qps = (total / engine_s) if engine_s > 0 else 0.0

    ok_hdr = "PV" if corpus_ngrams is not None else "LV"
    lines: list[str] = []
    lines.append(f"\n==================== {suite_name} ====================")
    lines.append(
        f"{'Nhóm test':<22} | {'Số câu':>7} | {f'Base-1g (T1/T3/{ok_hdr}, ms)':^28} | "
        f"{f'Base-2g (T1/T3/{ok_hdr}, ms)':^28} | {f'Factotum (T1/T3/{ok_hdr}, ms)':^28}"
    )
    lines.append("-" * 124)

    for cat in sorted(by_cat.keys()):
        rows = by_cat[cat]
        lines.append(
            f"{cat:<22} | {len(rows):>7,} | {_fmt(rows, 'b1'):^28} | "
            f"{_fmt(rows, 'b2'):^28} | {_fmt(rows, 'f'):^28}"
        )

    lines.append("-" * 124)
    lines.append(
        f"{'TỔNG CỘNG':<22} | {total:>7,} | {_fmt(all_rows, 'b1'):^28} | "
        f"{_fmt(all_rows, 'b2'):^28} | {_fmt(all_rows, 'f'):^28}"
    )

    b1_all = (sum(1 for r in all_rows if r["b1_t1"]) / total * 100) if total else 0.0
    b2_all = (sum(1 for r in all_rows if r["b2_t1"]) / total * 100) if total else 0.0
    t1_all = (sum(1 for r in all_rows if r["f_t1"]) / total * 100) if total else 0.0
    t3_all = (sum(1 for r in all_rows if r["f_t3"]) / total * 100) if total else 0.0
    lines.append(
        f"Vượt Base-1g: +{t1_all - b1_all:.1f}% Top-1 | Vượt Base-2g: +{t1_all - b2_all:.1f}% Top-1 | "
        f"Engine: {engine_s:.2f}s (Wall: {total_wall_s:.2f}s) | P50={p50_ms:.2f}ms | P95={p95_ms:.2f}ms | P99={p99_ms:.2f}ms | Max={max_ms:.2f}ms | {qps:,.0f} câu/s"
    )

    if corpus_ngrams is not None and calib_rows:
        lines.append("\nConfidence Calibration:")
        bins = [
            (0.90, 1.01, "90-100%"),
            (0.70, 0.90, "70-90%"),
            (0.50, 0.70, "50-70%"),
            (0.30, 0.50, "30-50%"),
            (0.00, 0.30, "<30%"),
        ]
        ece_t1 = 0.0
        ece_pv = 0.0
        for lo, hi, lbl in bins:
            sub = [x for x in calib_rows if lo <= x[0] < hi]
            if not sub:
                continue
            avg_c = statistics.mean(x[0] for x in sub) * 100
            bt1 = sum(x[1] for x in sub) / len(sub) * 100
            bt3 = sum(x[2] for x in sub) / len(sub) * 100
            bpv = sum(x[3] for x in sub) / len(sub) * 100
            ece_t1 += (len(sub) / len(calib_rows)) * abs(avg_c - bt1)
            ece_pv += (len(sub) / len(calib_rows)) * abs(avg_c - bpv)
            lines.append(
                f"  {lbl:<8} | n={len(sub):>5,} | avg={avg_c:5.1f}% | Top-1={bt1:5.1f}% | Top-3={bt3:5.1f}% | PhraseValid={bpv:5.1f}%"
            )
        lines.append(f"  ECE (Top-1) = {ece_t1:.1f}% | ECE (PhraseValid) = {ece_pv:.1f}%")

    if failures:
        lines.append(
            f"\nMẫu các câu lệch Top-1 (hiển thị {min(12, len(failures))}/{len(failures):,}, toàn bộ lưu tại failures.csv):"
        )
        for f in failures[:12]:
            lines.append(
                f"  - [{f['category']}] '{f['query']}' -> Kỳ vọng: '{f['expected']}' | Top-1: '{f['got_top1']}' (conf={f['confidence']:.2f}, {f['ms']:.2f}ms)"
            )

    summary = {
        "total": total,
        "base1_acc": b1_all,
        "base2_acc": b2_all,
        "top1_acc": t1_all,
        "top3_acc": t3_all,
        "avg_ms": avg_ms,
        "p95_ms": p95_ms,
        "qps": qps,
        "wall_s": total_wall_s,
    }
    return "\n".join(lines), summary


def build_auto_stress_cases(
    engine: GraphQueryEngine, total_target: int = 10000, seed: int = 42
) -> list[dict[str, str]]:
    rng = random.Random(seed)
    pool_2_6 = [
        (p_str, words, freq)
        for p_str, words, freq in engine.phrases
        if 2 <= len(words) <= 6
        and freq >= 2
        and all(
            w.isalpha()
            and len(w) >= 2
            and not is_single_consonant_bullet(w)
            and is_valid_vietnamese_syllable(w)
            for w in words
        )
    ]
    pool_3_5 = [
        (p_str, words, freq)
        for p_str, words, freq in pool_2_6
        if 3 <= len(words) <= 5 and freq >= 3
    ]
    if not pool_2_6:
        return []

    weights_2_6 = [freq for _, _, freq in pool_2_6]
    pool_3_5_use = pool_3_5 or pool_2_6
    weights_3_5 = [freq for _, _, freq in pool_3_5_use]

    n_unacc = int(total_target * 0.50)
    n_mixed = int(total_target * 0.20)
    n_acro = int(total_target * 0.15)
    n_miss = total_target - n_unacc - n_mixed - n_acro

    cases: list[dict[str, str]] = []

    for p_str, _words, _freq in rng.choices(pool_2_6, weights=weights_2_6, k=n_unacc):
        cases.append(
            {"category": "auto_unaccented", "query": remove_accents(p_str), "expected": p_str}
        )

    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_mixed):
        q_tokens = []
        for idx, w in enumerate(words):
            if idx == len(words) - 1:
                q_tokens.append(remove_accents(w)[0])
            elif idx == 0:
                q_tokens.append(w)
            else:
                q_tokens.append(remove_accents(w))
        cases.append(
            {"category": "auto_mixed_abbr", "query": " ".join(q_tokens), "expected": p_str}
        )

    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_acro):
        acro = "".join(remove_accents(w)[0] for w in words)
        cases.append({"category": "auto_acronym", "query": acro, "expected": p_str})

    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_miss):
        if len(words) >= 3:
            drop_idx = rng.randint(1, len(words) - 2)
            dropped = [remove_accents(w) for i, w in enumerate(words) if i != drop_idx]
            cases.append(
                {"category": "auto_missing", "query": " ".join(dropped), "expected": p_str}
            )

    rng.shuffle(cases)
    return cases


def run_held_out_benchmark(
    all_failures: list[dict],
    n_unacc: int = 4000,
    n_mixed: int = 2000,
    n_acro: int = 2000,
    n_miss: int = 2000,
    seed: int = 42,
) -> str:
    """
    Đánh giá tổng quát hóa độc lập cấp tài liệu (Document-level 80% Train / 20% Unseen Docs)
    trên cả 4 dạng bài toán (mặc định 10.000 câu):
    - heldout_unacc_in_kb / heldout_unacc_novel (không dấu)
    - heldout_mixed_abbr (trộn dấu + viết tắt)
    - heldout_acronym    (viết tắt dính liền)
    - heldout_missing    (nhập thiếu 1 từ)
    """
    if not DATA_DIR.exists():
        return ""
    docs = read_all_files(DATA_DIR)
    if not docs:
        return ""
    docs.sort(key=lambda x: x[0])

    t_build_start = time.perf_counter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="nlp_heldout_"))
    try:
        pure_dir = tmp_dir / "Pure_Log"
        pure_dir.mkdir(parents=True, exist_ok=True)

        # 1. Tách hoàn toàn theo đơn vị văn bản: 80% văn bản Train / 20% văn bản Test chưa học
        all_corpus_ngrams: set[str] = set()
        doc_tokens_list: list[tuple[str, list[str]]] = []
        for doc_name, raw_str in docs:
            toks = split_and_clean(raw_str)
            doc_tokens_list.append((doc_name, toks))
            for k in (2, 3, 4, 5, 6):
                for i in range(len(toks) - k + 1):
                    all_corpus_ngrams.add(" ".join(toks[i : i + k]))

        rng_split = random.Random(seed)
        indices = list(range(len(docs)))
        rng_split.shuffle(indices)
        n_train = int(len(docs) * 0.80)
        train_idx = set(indices[:n_train])
        test_idx = set(indices[n_train:])

        train_docs = [docs[i] for i in sorted(train_idx)]
        held_out_tokens_all: list[str] = []
        for i in sorted(test_idx):
            held_out_tokens_all.extend(doc_tokens_list[i][1])

        batch_res = extract_phrases_batch([raw for _, raw in train_docs])
        for (doc_name, _), res in zip(train_docs, batch_res):
            write_pure_log(pure_dir / f"train_{doc_name}.txt", res)

        t_sa_done = time.perf_counter()

        # 2. Chạy MakeTree trên tập Train 80% văn bản
        make_tree.build_graph(
            paths=FactotumPaths.from_output_dir(tmp_dir), show_progress=True
        )

        t_build_total = time.perf_counter() - t_build_start

        # 3. Nạp Engine từ tập Train
        train_engine = GraphQueryEngine()
        train_engine.load(
            kb_path=tmp_dir / "KnowledgeBase.txt",
            data_path=tmp_dir / "Data.txt",
            unaccented_path=tmp_dir / "UnaccentedMap.txt",
            show_progress=False,
        )

        # 4. Trích cụm 3..5 từ từ các văn bản Held-out chưa từng thấy
        ngram_counts: Counter[str] = Counter()
        for k in (3, 4, 5):
            for i in range(len(held_out_tokens_all) - k + 1):
                window = held_out_tokens_all[i : i + k]
                if all(
                    w.isalpha()
                    and len(w) >= 2
                    and not is_single_consonant_bullet(w)
                    and is_valid_vietnamese_syllable(w)
                    and w in train_engine.real_vocab
                    for w in window
                ):
                    ngram_counts[" ".join(window)] += 1

        test_pool = [(p, c) for p, c in ngram_counts.items() if c >= 2]
        if not test_pool:
            return ""

        rng = random.Random(seed)
        weights = [c for _, c in test_pool]
        heldout_cases: list[dict[str, str]] = []

        # 4a. Không dấu (chia cụm đã gặp trong KB vs cụm mới hoàn toàn)
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_unacc):
            in_train_kb = p_str in train_engine.dict_freq
            cat = "heldout_unacc_in_kb" if in_train_kb else "heldout_unacc_novel"
            heldout_cases.append(
                {"category": cat, "query": remove_accents(p_str), "expected": p_str}
            )

        # 4b. Trộn có dấu / không dấu + viết tắt từ cuối
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_mixed):
            words = p_str.split()
            q_tokens = []
            for idx, w in enumerate(words):
                if idx == len(words) - 1:
                    q_tokens.append(remove_accents(w)[0])
                elif idx == 0:
                    q_tokens.append(w)
                else:
                    q_tokens.append(remove_accents(w))
            heldout_cases.append(
                {"category": "heldout_mixed_abbr", "query": " ".join(q_tokens), "expected": p_str}
            )

        # 4c. Viết tắt chữ cái đầu dính liền
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_acro):
            words = p_str.split()
            acro = "".join(remove_accents(w)[0] for w in words)
            heldout_cases.append(
                {"category": "heldout_acronym", "query": acro, "expected": p_str}
            )

        # 4d. Nhập thiếu 1 từ ở giữa
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_miss):
            words = p_str.split()
            drop_idx = rng.randint(1, len(words) - 2)
            dropped = [remove_accents(w) for i, w in enumerate(words) if i != drop_idx]
            heldout_cases.append(
                {"category": "heldout_missing", "query": " ".join(dropped), "expected": p_str}
            )

        rng.shuffle(heldout_cases)
        suite_title = (
            f"1. DOCUMENT-LEVEL HELD-OUT ({len(train_docs)} TRAIN DOCS / {len(test_idx)} UNSEEN DOCS - {len(heldout_cases):,} CÂU)\n"
            f"[Build Index Train: SA/LCP={t_sa_done - t_build_start:.2f}s | "
            f"Tổng Build={t_build_total:.2f}s | KB Train={len(train_engine.phrases):,} cụm]"
        )
        text_heldout, _ = run_suite(
            train_engine,
            heldout_cases,
            suite_title,
            show_progress=True,
            collect_all_failures=all_failures,
            corpus_ngrams=all_corpus_ngrams,
        )
        return text_heldout
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    target_n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000

    print("============================================================")
    print(f"    BENCHMARK ĐỘ CHÍNH XÁC & TỐC ĐỘ ({target_n:,} CÂU)    ")
    print("============================================================")

    t0 = time.perf_counter()
    engine = GraphQueryEngine()
    engine.load()
    load_s = time.perf_counter() - t0
    print(
        f">> Thời gian nạp đồ thị vào RAM: {load_s:.3f} s "
        f"({len(engine.phrases):,} cụm | {len(engine.real_vocab):,} từ đơn | {len(engine.unaccented_map):,} khóa)"
    )

    report_sections: list[str] = [
        f"Thời gian nạp RAM: {load_s:.3f} s | Cụm: {len(engine.phrases):,} | Từ đơn: {len(engine.real_vocab):,} | Khóa: {len(engine.unaccented_map):,}"
    ]
    all_failures: list[dict] = []

    # 1. Held-out Generalization Benchmark ĐẶT LÊN ĐẦU (80% Train / 20% Test độc lập - 1.000 câu)
    print("\n>> [1/3] Đang chạy Document-level Held-out (124 Train Docs / 32 Unseen Docs, 10.000 câu)...")
    text_heldout = run_held_out_benchmark(all_failures)
    if text_heldout:
        print(text_heldout)
        report_sections.append(text_heldout)

    # 2. Bộ test chuẩn thủ công (test_cases.json - 44 câu)
    if TEST_CASES_PATH.exists():
        manual_cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
        text_manual, _ = run_suite(
            engine,
            manual_cases,
            "2. BỘ TEST CHUẨN (test_cases.json)",
            collect_all_failures=all_failures,
        )
        print(text_manual)
        report_sections.append(text_manual)

    # 3. Bộ stress test 10.000 câu (Frequency-weighted)
    auto_cases = build_auto_stress_cases(engine, total_target=target_n)
    if auto_cases:
        print(f"\n>> [3/3] Đang chạy Stress Test {len(auto_cases):,} câu (Frequency-weighted)...")
        text_auto, _ = run_suite(
            engine,
            auto_cases,
            f"3. STRESS TEST {len(auto_cases):,} CÂU (FREQUENCY-WEIGHTED)",
            show_progress=True,
            collect_all_failures=all_failures,
        )
        print(text_auto)
        report_sections.append(text_auto)

    # 4. Xuất toàn bộ các câu sai ra failures.csv
    all_failures.sort(key=lambda x: (x["suite"], x["category"], -x["ms"]))
    with open(FAILURES_CSV_PATH, "w", encoding="utf-8-sig", newline="") as csv_f:
        writer = csv.DictWriter(
            csv_f,
            fieldnames=[
                "suite",
                "category",
                "query",
                "expected",
                "got_top1",
                "got_top3",
                "in_top3",
                "confidence",
                "ms",
            ],
        )
        writer.writeheader()
        writer.writerows(all_failures)

    # Xóa các file report cũ để tránh trùng lặp, chỉ lưu bản mới nhất
    for old_rep in BENCH_DIR.glob("report_*.txt"):
        old_rep.unlink(missing_ok=True)

    report_path = BENCH_DIR / f"report_{target_n}_{get_current_timestamp()}.txt"
    report_path.write_text("\n\n".join(report_sections) + "\n", encoding="utf-8")
    print(f"\n>> Đã lưu báo cáo benchmark tại: {report_path}")
    print(f">> Đã xuất toàn bộ {len(all_failures):,} câu lệch Top-1 ra: {FAILURES_CSV_PATH}")


if __name__ == "__main__":
    main()
