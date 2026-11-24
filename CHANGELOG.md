# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.1.0] - 2026-11-24

Nâng cấp lõi tìm đường, tầng hậu kiểm, hỗ trợ chạy thử trực tiếp trên trình duyệt và bổ sung công cụ xây lại đồ thị.

### Tính năng mới

- **Demo web chạy trực tiếp trên trình duyệt (Pyodide WebAssembly):** xuất bản bản dùng thử tĩnh trên Hugging Face Space (`demo/build_space.py`, `demo/static/index.html`) không cần server backend.
- **Xây lại đồ thị từ KnowledgeBase trong CLI:** thêm hàm `make_tree.rebuild_from_kb` và tùy chọn `[4] Cập nhật lại đồ thị & .ftm từ KnowledgeBase.txt` trong `src/app.py`.

### Cải tiến

- **Tìm đường Viterbi + Markov hai chiều (`search.py`, `graph_index.py`):** giữ trạng thái theo từng từ, bổ sung backoff `best_prev_zero` và kết hợp xác suất thuận + ngược (`p_fwd^0.85 * p_rev^0.15`), đẩy Held-out 10.000 câu lên `62,3%` Top-1 / `71,8%` Top-3 / `84,1%` Phrase validity (viết tắt hỗn hợp đạt `70,5%` Top-1).
- **Nâng cấp tầng hậu kiểm (`reviewer.py`):** duyệt 1 lượt Top-5, sửa từ cầu nối `A - F' - B` bằng ngữ cảnh 2 đầu, mở nâng cấp 3-gram từ KB (`allow_kb_upgrade`) và xếp hạng lại Top-5 bằng mạch Markov.
- **Chuẩn hóa xếp hạng 7 tầng (`models.py`):** gộp toàn bộ tiêu chí xếp hạng về `evidence_tier`, `rank_key` và `finalize_matches`; bổ sung fast-path O(1) cho câu khớp trực tiếp KB và giảm độ trễ câu dài nhiễu từ `172 ms` xuống `6,4 ms`.

### Sửa lỗi

- Đồng bộ bộ lọc dấu `_filter_unaccented_candidates` khi nạp từ file `.ftm` (`_load_from_json_data`), sửa lỗi gán sai dấu khi nạp mô hình đóng gói.
- Sửa nhận diện số La Mã và từ viết tắt chương (`ch XXI`, `ch I`, `cg i`), giữ nguyên số thập phân (`0.4`) và đơn vị sau số (`0.4 mg`).
- Chặn token lớp (`<num>`, `<code>`, `<date>`, `<roman>`) lọt vào bước chèn từ thiếu và chặn gộp nhầm chuỗi phụ âm dài (`dbbbb`, `ttchh`).
- Giữ ổn định cách xử lý từ viết tắt OOV dài trong câu (`xkcbs`), sửa `NameError` ở `src/app.py`, escape ký tự `&` trong `run.bat` và `benchmark/run_benchmark.bat`, và bảo vệ `ProcessPoolExecutor` cho môi trường Pyodide.

## [1.0.0] - 2026-11-23

Bản phát hành ổn định đầu tiên.

### Tính năng

- Khôi phục dấu tiếng Việt, viết tắt chữ cái đầu (`xpvphc atgt`), viết tắt theo ngữ cảnh (`nd` → `nghị định` / `nhân dân`), chèn từ bị thiếu, sửa lỗi chính tả trên câu có dấu, giải mã Telex và ký tự lặp phím.
- Tự học từ tài liệu riêng (`.txt`, `.doc`, `.docx`) bằng Suffix Array + Maximal Repeats + đồ thị Markov 2 chiều; lõi C++ tùy chọn cho bước trích xuất.
- File mô hình `.ftm` an toàn (JSON nén `zlib`, không `pickle`); mô hình pháp luật mẫu `1,29 MB` học từ 156 văn bản công khai.
- Điểm tin cậy `confidence` đã hiệu chuẩn; tầng hậu kiểm giữ nguyên câu gốc (`is_fallback=True`) khi không có gợi ý trọn nghĩa.
- Danh sách từ không được đứng cuối câu do người dùng tự chỉnh (`output/dangling_end_words.txt`).
- Giao diện dòng lệnh (`python src/app.py`) và demo web Gradio (`python demo/app.py`).
