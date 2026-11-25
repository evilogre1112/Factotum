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
    load_dangling_end_words,
    print_progress,
    remove_accents,
    split_and_clean,
)
from factotum.engine import GraphQueryEngine, MAX_CANDIDATES_PER_KEY  # noqa: E402

CONTEXT_CASES_PATH = BENCH_DIR / "context_cases.json"
FAILURES_CSV_PATH = BENCH_DIR / "failures.csv"

_LOG_EPS = math.log(1e-8)

# Hư từ / từ chức năng phổ biến được bỏ qua khi trích từ khóa nội dung tự động
_VN_STOPWORDS = frozenset(
    {
        "của", "cho", "về", "với", "bởi", "để", "nhằm", "tại", "do", "vì", "nhờ",
        "thì", "và", "nhưng", "hoặc", "hay", "nếu", "mà", "tuy", "dù", "hễ",
        "các", "những", "mỗi", "mọi", "là", "trong", "trên", "dưới", "theo",
        "từ", "đến", "khi", "được", "bị", "có", "không", "đã", "đang", "sẽ",
        "này", "đó", "nào", "như", "sau", "trước", "qua", "bằng", "vào", "ra",
    }
)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[idx]


def extract_content_tokens(text: str) -> list[str]:
    """Tách các từ nội dung (bỏ hư từ / từ chức năng) để đo độ sát ngữ cảnh & thực thể."""
    words = [w for w in text.strip().lower().split() if w]
    content = [w for w in words if w not in _VN_STOPWORDS]
    return content if content else words


def derive_required_keywords(case: dict) -> list[str]:
    """
    Lấy danh sách từ khóa/thực thể bắt buộc của câu test:
    - Nếu câu có khai báo tường minh `required_keywords` thì dùng trực tiếp.
    - Ngược lại, trích tự động các từ nội dung (không phải hư từ) từ `expected`.
    """
    explicit = case.get("required_keywords")
    if explicit:
        return [kw.strip().lower() for kw in explicit if kw and kw.strip()]
    return list(dict.fromkeys(extract_content_tokens(case["expected"])))


def _contains_keyword(pred_norm: str, pred_tokens: set[str], kw: str) -> bool:
    """Kiểm tra thực thể/từ khóa (1 từ hoặc cụm nhiều từ) có xuất hiện nguyên vẹn trong câu đầu ra."""
    if " " in kw:
        return f" {kw} " in f" {pred_norm} "
    return kw in pred_tokens


def score_context_clarification(
    pred: str, expected: str, required_keywords: list[str]
) -> tuple[float, bool, float, list[str]]:
    """
    Đánh giá chất lượng làm rõ ngữ cảnh (không chấm cứng Exact Top-1):
    - kw_recall: Tỷ lệ từ khóa/thực thể bắt buộc xuất hiện trong đầu ra [0..1]
    - all_kw_ok: Đạt đủ 100% từ khóa bắt buộc hay không
    - ctx_f1:    F1 trên tập từ nội dung (đo độ sát ngữ cảnh, phạt từ rác/lệch nghĩa)
    - missing:   Danh sách từ khóa bắt buộc bị thiếu
    """
    pred_norm = " ".join(pred.strip().lower().split())
    if not pred_norm:
        return 0.0, False, 0.0, list(required_keywords)

    pred_tokens = set(pred_norm.split())
    missing = [kw for kw in required_keywords if not _contains_keyword(pred_norm, pred_tokens, kw)]
    if required_keywords:
        kw_recall = (len(required_keywords) - len(missing)) / len(required_keywords)
        all_kw_ok = len(missing) == 0
    else:
        kw_recall = 1.0
        all_kw_ok = True

    ref_c = Counter(extract_content_tokens(expected))
    pred_c = Counter(extract_content_tokens(pred_norm))
    overlap = sum((ref_c & pred_c).values())
    n_ref = sum(ref_c.values())
    n_pred = sum(pred_c.values())
    if overlap == 0 or n_ref == 0 or n_pred == 0:
        ctx_f1 = 0.0
    else:
        prec = overlap / n_pred
        rec = overlap / n_ref
        ctx_f1 = 2.0 * prec * rec / (prec + rec)

    return kw_recall, all_kw_ok, ctx_f1, missing


def baseline_unigram_top1(engine: GraphQueryEngine, raw_query: str) -> str:
    tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
    if not tokens:
        return ""
    cols: list[list[str]] = []
    for tok in tokens:
        no_acc = remove_accents(tok)
        if tok != no_acc and tok in engine.real_vocab:
            cols.append([tok])
        elif no_acc in engine.unaccented_map:
            cols.append(engine.unaccented_map[no_acc][:5])
        else:
            pieces = [
                engine.unaccented_map[ch][:5]
                for ch in no_acc
                if ch in engine.unaccented_map
            ]
            if pieces:
                cols.extend(pieces)
            else:
                cols.append([tok])

    seq = [max(col, key=lambda w: engine.dict_freq.get(w, 1)) for col in cols]
    return " ".join(seq)


def baseline_bigram_viterbi_top1(engine: GraphQueryEngine, raw_query: str) -> str:
    tokens = [cleaned for t in raw_query.split() if (cleaned := clean_word(t))]
    if not tokens:
        return ""

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
        return ""

    dp: dict[str, tuple[float, list[str]]] = {
        w: (math.log(max(engine.dict_freq.get(w, 1), 1) / 100000.0), [w]) for w in cols[0]
    }
    for col_idx in range(1, len(cols)):
        cur_cands = cols[col_idx]
        new_dp: dict[str, tuple[float, list[str]]] = {}
        prev_items = list(dp.items())
        for v in cur_cands:
            best_sc = -1e18
            best_seq: list[str] = []
            for u, (prev_sc, prev_seq) in prev_items:
                p_uv = engine.fwd_prob.get(u, {}).get(v)
                sc = prev_sc + (math.log(p_uv) if p_uv and p_uv > 0 else _LOG_EPS)
                if sc > best_sc:
                    best_sc = sc
                    best_seq = prev_seq + [v]
            new_dp[v] = (best_sc, best_seq)
        dp = new_dp

    best_final = max(dp.values(), key=lambda x: x[0])
    return " ".join(best_final[1])


def run_suite(
    engine: GraphQueryEngine,
    cases: list[dict],
    suite_name: str,
    show_progress: bool = False,
    collect_all_failures: list[dict] | None = None,
    show_calibration: bool = False,
) -> tuple[str, dict]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    all_latencies: list[float] = []
    failures: list[dict] = []
    calib_rows: list[tuple[float, float, bool, float]] = []

    total = len(cases)
    t_suite = time.perf_counter()
    last_draw = 0.0

    for idx, case in enumerate(cases, start=1):
        cat = case["category"]
        q = case["query"]
        expected = case["expected"].strip().lower()
        req_kws = derive_required_keywords(case)

        # Raw query (trước khi làm rõ)
        raw_kw, raw_all, raw_f1, _ = score_context_clarification(q, expected, req_kws)

        # Factotum
        t0 = time.perf_counter()
        matches = engine.query(q, top_k=3)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        all_latencies.append(dt_ms)
        f_pred = matches[0].phrase.strip().lower() if matches else q.strip().lower()
        f_conf = matches[0].confidence if matches else 0.0
        f_kw, f_all, f_f1, f_miss = score_context_clarification(f_pred, expected, req_kws)
        calib_rows.append((f_conf, f_kw, f_all, f_f1))

        # Baseline 1 (Unigram)
        t1 = time.perf_counter()
        b1_pred = baseline_unigram_top1(engine, q).strip().lower()
        b1_ms = (time.perf_counter() - t1) * 1000.0
        b1_kw, b1_all, b1_f1, _ = score_context_clarification(b1_pred, expected, req_kws)

        # Baseline 2 (Bigram + Viterbi)
        t2 = time.perf_counter()
        b2_pred = baseline_bigram_viterbi_top1(engine, q).strip().lower()
        b2_ms = (time.perf_counter() - t2) * 1000.0
        b2_kw, b2_all, b2_f1, _ = score_context_clarification(b2_pred, expected, req_kws)

        if not f_all:
            fail_item = {
                "suite": suite_name.splitlines()[0],
                "category": cat,
                "query": q,
                "required_keywords": " | ".join(req_kws),
                "missing_keywords": " | ".join(f_miss),
                "got_output": f_pred,
                "kw_recall": round(f_kw, 3),
                "ctx_f1": round(f_f1, 3),
                "confidence": round(f_conf, 3),
                "ms": round(dt_ms, 3),
            }
            failures.append(fail_item)
            if collect_all_failures is not None:
                collect_all_failures.append(fail_item)

        by_cat[cat].append(
            {
                "raw_kw": raw_kw, "raw_all": raw_all, "raw_f1": raw_f1,
                "b1_kw": b1_kw, "b1_all": b1_all, "b1_f1": b1_f1, "b1_ms": b1_ms,
                "b2_kw": b2_kw, "b2_all": b2_all, "b2_f1": b2_f1, "b2_ms": b2_ms,
                "f_kw": f_kw, "f_all": f_all, "f_f1": f_f1, "f_ms": dt_ms,
            }
        )

        if show_progress:
            now = time.perf_counter()
            if now - last_draw >= 0.1 or idx == total:
                print_progress(idx, total, t_suite, label="Benchmark", unit="câu")
                last_draw = now

    def _fmt(rows: list[dict], prefix: str, show_ms: bool = True) -> str:
        n = len(rows) or 1
        kw = sum(r[f"{prefix}_kw"] for r in rows) / n * 100
        ak = sum(1 for r in rows if r[f"{prefix}_all"]) / n * 100
        f1 = sum(r[f"{prefix}_f1"] for r in rows) / n * 100
        if show_ms:
            ms = statistics.mean(r[f"{prefix}_ms"] for r in rows)
            return f"{kw:5.1f}/{ak:5.1f}/{f1:5.1f}% ({ms:4.2f}ms)"
        return f"{kw:5.1f}/{ak:5.1f}/{f1:5.1f}%"

    all_rows = [r for rows in by_cat.values() for r in rows]
    engine_s = sum(all_latencies) / 1000.0
    avg_ms = statistics.mean(all_latencies) if all_latencies else 0.0
    p50_ms = percentile(all_latencies, 50)
    p95_ms = percentile(all_latencies, 95)
    p99_ms = percentile(all_latencies, 99)
    qps = (total / engine_s) if engine_s > 0 else 0.0

    lines: list[str] = []
    lines.append(f"\n==================== {suite_name} ====================")
    lines.append("Thước đo: KW-Rec (phủ từ khóa bắt buộc) / All-KW (đủ 100% từ khóa) / Ctx-F1 (độ sát ngữ cảnh)")
    lines.append(
        f"{'Nhóm test':<24} | {'Số câu':>6} | {'Raw (KW/All/F1)':^19} | "
        f"{'Base-1g (KW/All/F1, ms)':^28} | {'Base-2g (KW/All/F1, ms)':^28} | {'Factotum (KW/All/F1, ms)':^28}"
    )
    lines.append("-" * 149)
    for cat in sorted(by_cat.keys()):
        rows = by_cat[cat]
        lines.append(
            f"{cat:<24} | {len(rows):>6,} | {_fmt(rows, 'raw', False):^19} | "
            f"{_fmt(rows, 'b1', True):^28} | {_fmt(rows, 'b2', True):^28} | {_fmt(rows, 'f', True):^28}"
        )
    lines.append("-" * 149)
    lines.append(
        f"{'TỔNG CỘNG':<24} | {total:>6,} | {_fmt(all_rows, 'raw', False):^19} | "
        f"{_fmt(all_rows, 'b1', True):^28} | {_fmt(all_rows, 'b2', True):^28} | {_fmt(all_rows, 'f', True):^28}"
    )

    f_kw_all = sum(r["f_kw"] for r in all_rows) / total * 100 if total else 0.0
    raw_kw_all = sum(r["raw_kw"] for r in all_rows) / total * 100 if total else 0.0
    b2_kw_all = sum(r["b2_kw"] for r in all_rows) / total * 100 if total else 0.0
    f_f1_all = sum(r["f_f1"] for r in all_rows) / total * 100 if total else 0.0
    lines.append(
        f"Tăng KW-Rec so với Raw: +{f_kw_all - raw_kw_all:.1f}% | Vượt Base-2g: +{f_kw_all - b2_kw_all:.1f}% KW-Rec | "
        f"Ctx-F1={f_f1_all:.1f}% | P50={p50_ms:.2f}ms | P95={p95_ms:.2f}ms | P99={p99_ms:.2f}ms | {qps:,.0f} câu/s"
    )

    if show_calibration and calib_rows:
        lines.append("\nConfidence Calibration (theo độ làm rõ ngữ cảnh):")
        bins = [
            (0.90, 1.01, "90-100%"),
            (0.70, 0.90, "70-90%"),
            (0.50, 0.70, "50-70%"),
            (0.30, 0.50, "30-50%"),
            (0.00, 0.30, "<30%"),
        ]
        for lo, hi, lbl in bins:
            sub = [x for x in calib_rows if lo <= x[0] < hi]
            if not sub:
                continue
            avg_c = statistics.mean(x[0] for x in sub) * 100
            b_kw = statistics.mean(x[1] for x in sub) * 100
            b_all = sum(1 for x in sub if x[2]) / len(sub) * 100
            b_f1 = statistics.mean(x[3] for x in sub) * 100
            lines.append(
                f"  {lbl:<8} | n={len(sub):>5,} | conf={avg_c:5.1f}% | KW-Rec={b_kw:5.1f}% | All-KW={b_all:5.1f}% | Ctx-F1={b_f1:5.1f}%"
            )

    if failures:
        lines.append(
            f"\nMẫu câu còn thiếu từ khóa bắt buộc (hiển thị {min(10, len(failures))}/{len(failures):,}, chi tiết tại failures.csv):"
        )
        for f in failures[:10]:
            lines.append(
                f"  - [{f['category']}] '{f['query']}' -> '{f['got_output']}' | Thiếu: [{f['missing_keywords']}] (KW={f['kw_recall']*100:.0f}%, F1={f['ctx_f1']*100:.0f}%)"
            )

    summary = {
        "total": total,
        "f_kw": f_kw_all,
        "f_f1": f_f1_all,
        "avg_ms": avg_ms,
        "qps": qps,
    }
    return "\n".join(lines), summary


def build_auto_stress_cases(
    engine: GraphQueryEngine, total_target: int = 10000, seed: int = 42
) -> list[dict]:
    rng = random.Random(seed)
    dangling_words = load_dangling_end_words()
    pool_2_6 = [
        (p_str, words, freq)
        for p_str, words, freq in engine.phrases
        if 2 <= len(words) <= 6
        and freq >= 2
        and words[-1] not in dangling_words
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

    cases: list[dict] = []
    for p_str, _words, _freq in rng.choices(pool_2_6, weights=weights_2_6, k=n_unacc):
        cases.append({"category": "auto_unaccented", "query": remove_accents(p_str), "expected": p_str})
    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_mixed):
        q_tokens = [
            w if idx == 0 else (remove_accents(w)[0] if idx == len(words) - 1 else remove_accents(w))
            for idx, w in enumerate(words)
        ]
        cases.append({"category": "auto_mixed_abbr", "query": " ".join(q_tokens), "expected": p_str})
    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_acro):
        acro = "".join(remove_accents(w)[0] for w in words)
        cases.append({"category": "auto_acronym", "query": acro, "expected": p_str})
    for p_str, words, _freq in rng.choices(pool_3_5_use, weights=weights_3_5, k=n_miss):
        if len(words) >= 3:
            drop_idx = rng.randint(1, len(words) - 2)
            dropped = [remove_accents(w) for i, w in enumerate(words) if i != drop_idx]
            cases.append({"category": "auto_missing", "query": " ".join(dropped), "expected": p_str})

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
    Document-level Held-out (80% văn bản học / 20% văn bản chưa từng học):
    Đo chất lượng làm rõ từ khóa thực thể & ngữ cảnh (KW-Rec, All-KW, Ctx-F1).
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

        doc_tokens_list: list[tuple[str, list[str]]] = [
            (doc_name, split_and_clean(raw_str)) for doc_name, raw_str in docs
        ]
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

        make_tree.build_graph(
            paths=FactotumPaths.from_output_dir(tmp_dir), show_progress=True
        )
        t_build_total = time.perf_counter() - t_build_start

        train_engine = GraphQueryEngine()
        train_engine.load(
            kb_path=tmp_dir / "KnowledgeBase.txt",
            data_path=tmp_dir / "Data.txt",
            unaccented_path=tmp_dir / "UnaccentedMap.txt",
            show_progress=False,
        )

        dangling_words = load_dangling_end_words()
        ngram_counts: Counter[str] = Counter()
        for k in (3, 4, 5):
            for i in range(len(held_out_tokens_all) - k + 1):
                window = held_out_tokens_all[i : i + k]
                if window[-1] not in dangling_words and all(
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
        heldout_cases: list[dict] = []

        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_unacc):
            cat = "heldout_unacc_in_kb" if p_str in train_engine.dict_freq else "heldout_unacc_novel"
            heldout_cases.append({"category": cat, "query": remove_accents(p_str), "expected": p_str})
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_mixed):
            words = p_str.split()
            q_tokens = [
                w if idx == 0 else (remove_accents(w)[0] if idx == len(words) - 1 else remove_accents(w))
                for idx, w in enumerate(words)
            ]
            heldout_cases.append({"category": "heldout_mixed_abbr", "query": " ".join(q_tokens), "expected": p_str})
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_acro):
            words = p_str.split()
            acro = "".join(remove_accents(w)[0] for w in words)
            heldout_cases.append({"category": "heldout_acronym", "query": acro, "expected": p_str})
        for p_str, _c in rng.choices(test_pool, weights=weights, k=n_miss):
            words = p_str.split()
            drop_idx = rng.randint(1, len(words) - 2)
            dropped = [remove_accents(w) for i, w in enumerate(words) if i != drop_idx]
            heldout_cases.append({"category": "heldout_missing", "query": " ".join(dropped), "expected": p_str})

        rng.shuffle(heldout_cases)
        suite_title = (
            f"1. DOCUMENT-LEVEL HELD-OUT ({len(train_docs)} TRAIN DOCS / {len(test_idx)} UNSEEN DOCS - {len(heldout_cases):,} CÂU)\n"
            f"[Build Train: SA/LCP={t_sa_done - t_build_start:.2f}s | "
            f"Tổng Build={t_build_total:.2f}s | KB Train={len(train_engine.phrases):,} cụm]"
        )
        text_heldout, _ = run_suite(
            train_engine,
            heldout_cases,
            suite_title,
            show_progress=True,
            collect_all_failures=all_failures,
            show_calibration=True,
        )
        return text_heldout
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    target_n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000

    print("=================================================================")
    print(f"   BENCHMARK LÀM RÕ NGỮ CẢNH & TỪ KHÓA THỰC THỂ ({target_n:,} CÂU)   ")
    print("=================================================================")

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

    print("\n>> [1/3] Đang chạy Document-level Held-out (10.000 câu)...")
    text_heldout = run_held_out_benchmark(all_failures)
    if text_heldout:
        print(text_heldout)
        report_sections.append(text_heldout)

    if CONTEXT_CASES_PATH.exists():
        ctx_cases = json.loads(CONTEXT_CASES_PATH.read_text(encoding="utf-8"))
        text_ctx, _ = run_suite(
            engine,
            ctx_cases,
            "2. BỘ TEST LÀM RÕ NGỮ CẢNH & TỪ KHÓA BẮT BUỘC (context_cases.json)",
            collect_all_failures=all_failures,
        )
        print(text_ctx)
        report_sections.append(text_ctx)

    auto_cases = build_auto_stress_cases(engine, total_target=target_n)
    if auto_cases:
        print(f"\n>> [3/3] Đang chạy Stress Test {len(auto_cases):,} câu (Context & Speed)...")
        text_auto, _ = run_suite(
            engine,
            auto_cases,
            f"3. STRESS TEST LÀM RÕ NGỮ CẢNH ({len(auto_cases):,} CÂU)",
            show_progress=True,
            collect_all_failures=all_failures,
        )
        print(text_auto)
        report_sections.append(text_auto)

    all_failures.sort(key=lambda x: (x["suite"], x["category"], x["kw_recall"]))
    with open(FAILURES_CSV_PATH, "w", encoding="utf-8-sig", newline="") as csv_f:
        writer = csv.DictWriter(
            csv_f,
            fieldnames=[
                "suite",
                "category",
                "query",
                "required_keywords",
                "missing_keywords",
                "got_output",
                "kw_recall",
                "ctx_f1",
                "confidence",
                "ms",
            ],
        )
        writer.writeheader()
        writer.writerows(all_failures)

    for old_rep in BENCH_DIR.glob("report_*.txt"):
        old_rep.unlink(missing_ok=True)

    report_path = BENCH_DIR / f"report_{target_n}_{get_current_timestamp()}.txt"
    report_path.write_text("\n\n".join(report_sections) + "\n", encoding="utf-8")
    print(f"\n>> Đã lưu báo cáo benchmark tại: {report_path}")
    print(f">> Đã xuất {len(all_failures):,} câu còn thiếu từ khóa bắt buộc ra: {FAILURES_CSV_PATH}")


if __name__ == "__main__":
    main()
