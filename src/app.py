from pathlib import Path
import time

from factotum.builder import make_tree
from factotum.builder.extractor import process_document
from factotum.core.doc_reader import read_all_files, read_single_file
from factotum.core.extension import format_duration, init_console, print_progress
from factotum.engine import GraphQueryEngine

SRC_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SRC_DIR.parent
DEFAULT_DATA_DIR = PROJECT_DIR / "data"


def add_data_flow(engine: GraphQueryEngine) -> None:
    print("\n--- [1] THÊM DỮ LIỆU MỚI (.doc / .docx) ---")
    print(f"Nhập đường dẫn file hoặc thư mục (có thể kéo-thả vào đây).")
    print(f"Bấm Enter để dùng thư mục mặc định: {DEFAULT_DATA_DIR}")
    raw_input = input("Đường dẫn: ").strip().strip('"').strip("'")

    target = Path(raw_input) if raw_input else DEFAULT_DATA_DIR
    if not target.exists():
        print(f"[Lỗi] Không tìm thấy đường dẫn: {target}\n")
        return

    t0 = time.perf_counter()
    docs: list[tuple[str, str]] = []

    if target.is_file():
        if target.suffix.lower() not in (".doc", ".docx"):
            print(f"[Lỗi] Chỉ hỗ trợ file .doc hoặc .docx (nhận được: {target.suffix})\n")
            return
        docs = [(target.name, read_single_file(target))]
    else:
        docs = read_all_files(target)

    if not docs:
        print(f"[Thông báo] Không có file .doc / .docx nào trong: {target}\n")
        return

    total_words = sum(len(raw_str.split()) for _, raw_str in docs)
    print(
        f"\n>> Đã đọc {len(docs)} file ({total_words:,} từ) trong {time.perf_counter() - t0:.4f} s."
    )

    done_words = 0
    t_extract = time.perf_counter()
    for idx, (doc_name, raw_str) in enumerate(docs, start=1):
        print(f"\n[{idx}/{len(docs)}] Đang trích xuất cụm lõi: {doc_name}...")
        process_document(doc_name, raw_str)
        done_words += len(raw_str.split())
        print_progress(
            done_words, total_words, t_extract, label="Trích xuất", unit="từ"
        )

    print(
        f">> Xong bước trích xuất ({format_duration(time.perf_counter() - t_extract)}). "
        f"Đang dựng đồ thị tri thức (MakeTree)..."
    )
    make_tree.main()

    # Tải lại đồ thị mới nhất vào RAM để sẵn sàng truy vấn ngay
    t_reload = time.perf_counter()
    engine.load()
    print(
        f">> Đã cập nhật xong vào RAM ({time.perf_counter() - t_reload:.3f} s): "
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


def show_stats(engine: GraphQueryEngine) -> None:
    if not engine.phrases:
        engine.load()
    print("\n--- THỐNG KÊ ĐỒ THỊ HIỆN TẠI ---")
    print(f"- Số cụm tri thức (KnowledgeBase): {len(engine.phrases):,}")
    print(f"- Số từ đơn có dấu (Real Vocab):   {len(engine.real_vocab):,}")
    print(f"- Số khóa không dấu & viết tắt:    {len(engine.unaccented_map):,}")
    print(f"- Thư mục dữ liệu đồ thị:          {Maketree.OUTPUT_DIR}\n")


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
        print("  [0] Thoát")

        try:
            choice = input("Nhập lựa chọn (0-3): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTạm biệt!")
            break

        if choice == "1":
            add_data_flow(engine)
        elif choice == "2":
            query_test_flow(engine)
        elif choice == "3":
            show_stats(engine)
        elif choice == "0":
            print("Tạm biệt!")
            break
        else:
            print("[!] Lựa chọn không hợp lệ, vui lòng nhập 0, 1, 2 hoặc 3.\n")


if __name__ == "__main__":
    main()
