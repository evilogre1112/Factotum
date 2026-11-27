import hashlib
from pathlib import Path
import shutil
import time

from factotum.builder import make_tree
from factotum.builder.extractor import process_document
from factotum.core.doc_reader import read_single_file
from factotum.core.extension import format_duration, init_console, print_progress
from factotum.engine import GraphQueryEngine
from factotum.engine.models import OUTPUT_DIR

SRC_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SRC_DIR.parent
DEFAULT_DATA_DIR = PROJECT_DIR / "data"
UNPROCESSED_DATA_DIR = DEFAULT_DATA_DIR / "unprocessed"
PROCESSED_DATA_DIR = DEFAULT_DATA_DIR / "processed"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_processed_signatures(processed_dir: Path) -> tuple[set[str], set[str]]:
    """Thu thập tập tên file (chữ thường) và mã băm SHA-256 của toàn bộ file đã xử lý."""
    names: set[str] = set()
    hashes: set[str] = set()
    if not processed_dir.exists():
        return names, hashes
    for f in processed_dir.iterdir():
        if f.is_file() and f.suffix.lower() in (".doc", ".docx") and not f.name.startswith("~$"):
            names.add(f.name.lower())
            hashes.add(_file_sha256(f))
    return names, hashes


def add_data_flow(engine: GraphQueryEngine) -> None:
    UNPROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("\n--- [1] THÊM DỮ LIỆU MỚI (.doc / .docx) ---")
    print("Nhập đường dẫn file hoặc thư mục (có thể kéo-thả vào đây).")
    print(f"Bấm Enter để nạp tự động từ thư mục chưa xử lý: {UNPROCESSED_DATA_DIR}")
    raw_input = input("Đường dẫn: ").strip().strip('"').strip("'")

    target = Path(raw_input) if raw_input else UNPROCESSED_DATA_DIR
    if not target.exists():
        print(f"[Lỗi] Không tìm thấy đường dẫn: {target}\n")
        return

    t0 = time.perf_counter()
    candidate_files: list[Path] = []

    if target.is_file():
        if target.suffix.lower() not in (".doc", ".docx"):
            print(f"[Lỗi] Chỉ hỗ trợ file .doc hoặc .docx (nhận được: {target.suffix})\n")
            return
        candidate_files = [target]
    else:
        candidate_files = sorted(
            f
            for f in target.iterdir()
            if f.is_file()
            and f.suffix.lower() in (".doc", ".docx")
            and not f.name.startswith("~$")
        )

    if not candidate_files:
        print(f"[Thông báo] Không có file .doc / .docx nào chưa xử lý trong: {target}\n")
        return

    processed_names, processed_hashes = _collect_processed_signatures(PROCESSED_DATA_DIR)
    new_files: list[tuple[Path, str]] = []
    skipped_files: list[Path] = []
    for f in candidate_files:
        f_hash = _file_sha256(f)
        if f.name.lower() in processed_names or f_hash in processed_hashes:
            skipped_files.append(f)
        else:
            processed_names.add(f.name.lower())
            processed_hashes.add(f_hash)
            new_files.append((f, f_hash))

    if skipped_files:
        print(f"\n>> Bỏ qua {len(skipped_files)} file đã tồn tại trong '{PROCESSED_DATA_DIR.name}/' (trùng tên hoặc nội dung):")
        for sf in skipped_files[:10]:
            print(f"   - [Trùng] {sf.name}")
        if len(skipped_files) > 10:
            print(f"   ... và {len(skipped_files) - 10} file khác.")

    if not new_files:
        print(">> Không có file mới nào cần xử lý (tránh nạp trùng thành công).\n")
        return

    docs: list[tuple[Path, str]] = [(f, read_single_file(f)) for f, _ in new_files]
    total_words = sum(len(raw_str.split()) for _, raw_str in docs)
    print(
        f"\n>> Đã đọc {len(docs)} file mới ({total_words:,} từ) trong {time.perf_counter() - t0:.4f} s."
    )

    done_words = 0
    t_extract = time.perf_counter()
    for idx, (doc_path, raw_str) in enumerate(docs, start=1):
        print(f"\n[{idx}/{len(docs)}] Đang trích xuất cụm lõi: {doc_path.name}...")
        process_document(doc_path.name, raw_str)
        done_words += len(raw_str.split())
        print_progress(
            done_words, total_words, t_extract, label="Trích xuất", unit="từ"
        )

    print(
        f">> Xong bước trích xuất ({format_duration(time.perf_counter() - t_extract)}). "
        f"Đang dựng đồ thị tri thức (MakeTree)..."
    )
    make_tree.main()

    # Chuyển file từ unprocessed/ sang processed/ (hoặc sao lưu bản đối chiếu nếu nạp từ ngoài)
    for doc_path, _ in docs:
        dest = PROCESSED_DATA_DIR / doc_path.name
        if doc_path.parent.resolve() == UNPROCESSED_DATA_DIR.resolve():
            shutil.move(str(doc_path), str(dest))
        elif not dest.exists():
            shutil.copy2(str(doc_path), str(dest))

    # Tải lại đồ thị mới nhất vào RAM để sẵn sàng truy vấn ngay
    t_reload = time.perf_counter()
    engine.load()
    print(
        f">> Đã chuyển {len(docs)} file sang '{PROCESSED_DATA_DIR.name}/' và cập nhật vào RAM "
        f"({time.perf_counter() - t_reload:.3f} s): "
        f"{len(engine.phrases):,} cụm | {len(engine.real_vocab):,} từ đơn.\n"
    )


def query_test_flow(engine: GraphQueryEngine) -> None:
    if not engine.phrases:
        print("\n>> Đang nạp đồ thị vào RAM...")
        t0 = time.perf_counter()
        engine.load()
        if not engine.phrases:
            print("[Thông báo] Đồ thị đang trống! Hãy chọn [1] để nạp dữ liệu trước.\n")
            return
        src_tag = " [Binary Cache]" if engine.loaded_from_cache else " [Text -> đã lưu Cache]"
        print(
            f">> Đã nạp xong ({time.perf_counter() - t0:.3f} s{src_tag}): "
            f"{len(engine.phrases):,} cụm | {len(engine.real_vocab):,} từ đơn | "
            f"{len(engine.unaccented_map):,} khóa không dấu/viết tắt."
        )

    print("\n--- [2] TEST TRUY VẤN (gõ 'back' để quay lại menu) ---")
    while True:
        try:
            q = input("Truy vấn > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not q:
            continue
        if q.lower() in ("back", "exit", "0"):
            print()
            break

        t0 = time.perf_counter()
        matches = engine.query(q, top_k=5)
        dt_ms = (time.perf_counter() - t0) * 1000

        if not matches:
            print(f"  => Không tìm thấy đường đi phù hợp ({dt_ms:.2f} ms).\n")
            continue

        print(f"  Kết quả ({dt_ms:.2f} ms):")
        for i, m in enumerate(matches, start=1):
            display_phrase = m.phrase
            if (
                m.superstring_diff > 0
                and m.source_phrase
                and len(m.source_phrase.split()) > len(m.phrase.split())
                and all(w in m.source_phrase.split() for w in m.phrase.split())
            ):
                display_phrase = m.source_phrase
                tag = f"Hoàn thiện cụm tối đại (từ '{m.phrase}', +{m.superstring_diff} từ)"
            elif m.context_tier == 1:
                tag = (
                    "Khớp chính xác ngữ cảnh 2 đầu"
                    if m.superstring_diff == 0
                    else f"Ngữ cảnh 2 đầu A-F-B (+{m.context_span_len} từ neo)"
                )
            elif m.context_tier == 2:
                tag = (
                    "Khớp chính xác ngữ cảnh"
                    if m.superstring_diff == 0
                    else f"Ngữ cảnh 1 đầu (+{m.context_span_len} từ neo)"
                )
            elif m.context_tier == 3:
                tag = "Khớp chuỗi tối đại qua từ nối"
            elif m.context_tier == 4:
                tag = (
                    "Ghép cụm độc lập trong KB"
                    if m.superstring_diff == 0
                    else "Nối cạnh đồ thị với ngữ cảnh"
                )
            elif m.superstring_diff == 0:
                tag = "Khớp chính xác"
            elif m.superstring_diff > 0 and m.source_phrase:
                display_phrase = m.source_phrase
                tag = f"Hoàn thiện cụm tối đại (từ '{m.phrase}', +{m.superstring_diff} từ)"
            else:
                tag = "Lý thuyết Markov"

            score_str = (
                f"freq={m.freq}"
                if m.superstring_diff != -1
                else f"P_markov={m.markov_prob:.6f}"
            )
            conf_str = f"conf={m.confidence * 100:.0f}%"
            low_warn = " [Độ tin cậy thấp]" if m.confidence < 0.45 else ""
            miss_info = f" | Thiếu từ: {m.missing_words}" if m.missing_words else ""
            print(f"    [{i}] {display_phrase}  ({conf_str} | {score_str} | {tag}{miss_info}{low_warn})")
        print()


def rebuild_kb_flow(engine: GraphQueryEngine) -> None:
    print("\n--- [4] CẬP NHẬT LẠI ĐỒ THỊ & .FTM TỪ KNOWLEDGEBASE.TXT ---")
    kb_path = OUTPUT_DIR / "KnowledgeBase.txt"
    if not kb_path.exists():
        print(f"[Lỗi] Không tìm thấy {kb_path}. Hãy chọn [1] để nạp dữ liệu trước.\n")
        return

    t0 = time.perf_counter()
    total = make_tree.rebuild_from_kb(show_progress=True)
    engine.load(use_cache=True)
    ftm_path = OUTPUT_DIR / "factotum_law.ftm"
    engine.save_standalone(ftm_path)
    print(
        f">> Đã dựng lại xong ({time.perf_counter() - t0:.2f} s): "
        f"{total:,} cụm | {len(engine.real_vocab):,} từ đơn | "
        f"{len(engine.unaccented_map):,} khóa."
    )
    print(f">> Đã cập nhật Data.txt, UnaccentedMap.txt và xuất lại {ftm_path}\n")


def show_stats(engine: GraphQueryEngine) -> None:
    if not engine.phrases:
        engine.load()
    print("\n--- THỐNG KÊ ĐỒ THỊ HIỆN TẠI ---")
    print(f"- Số cụm tri thức (KnowledgeBase): {len(engine.phrases):,}")
    print(f"- Số từ đơn có dấu (Real Vocab):   {len(engine.real_vocab):,}")
    print(f"- Số khóa không dấu & viết tắt:    {len(engine.unaccented_map):,}")
    print(f"- Thư mục dữ liệu đồ thị:          {OUTPUT_DIR}\n")


def main() -> None:
    init_console()
    engine = GraphQueryEngine()
    print("==================================================")
    print("      FACTOTUM CLI - KHÔI PHỤC DẤU & VIẾT TẮT     ")
    print("==================================================")

    while True:
        print("Chọn chức năng:")
        print("  [1] Thêm dữ liệu mới (.doc / .docx) vào đồ thị")
        print("  [2] Test truy vấn (không dấu / viết tắt / thiếu từ)")
        print("  [3] Xem thống kê đồ thị hiện tại")
        print("  [4] Cập nhật lại đồ thị & .ftm từ KnowledgeBase.txt")
        print("  [0] Thoát")

        try:
            choice = input("Nhập lựa chọn (0-4): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTạm biệt!")
            break

        if choice == "1":
            add_data_flow(engine)
        elif choice == "2":
            query_test_flow(engine)
        elif choice == "3":
            show_stats(engine)
        elif choice == "4":
            rebuild_kb_flow(engine)
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("[!] Lựa chọn không hợp lệ, vui lòng nhập 0, 1, 2, 3 hoặc 4.\n")


if __name__ == "__main__":
    main()
