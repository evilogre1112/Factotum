# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.1.2] - 2026-11-28

Chuyển đổi toàn bộ quá trình tính xác suất chuỗi Markov sang không gian log-probability ($\sum \ln P$) để triệt tiêu tràn số thực trên câu dài.

### Cải tiến chính

- Chuyển Beam Search, xếp hạng ứng viên và tính điểm tin cậy sang tổng log-probability (`markov_log_prob`, `effective_log_prob`) — loại bỏ hiện tượng tràn về `0.000000` trên văn bản dài hàng trăm từ mà vẫn giữ nguyên 100% độ chính xác trên 20.064 câu benchmark.
- Hiển thị `logP=...` thay cho `P_markov=0.000000` trong giao diện CLI (`src/app.py`).

## [1.1.1] - 2026-11-28

Bản tối ưu tốc độ thực thi (không suy giảm độ chính xác) và nâng cao độ ổn định khi xử lý câu dài phẳng không dấu câu.

### Cải tiến chính

- **Tăng tốc truy vấn & nạp mô hình (2–3×):** Tối ưu bỏ dấu tiếng Việt, phân loại hình thái token, tính khoảng cách chỉnh sửa và giao tập ứng viên cụm từ mà vẫn giữ nguyên 100% độ chính xác trên bộ 20.064 câu.
- **Khắc phục lỗi biên đoạn trên câu dài không dấu câu:** Sửa lỗi lặp từ neo, rơi đoạn khi lát trung gian kết thúc bằng hư từ (`với`, `và`, `có`, `của`...) và ngăn nhánh chèn từ thiếu phá vỡ cụm từ đã khớp sẵn.

### Kết quả Benchmark (20.064 câu — `report_10000_20261128_185548.txt`)

- **Nạp RAM:** `3,404 s → 1,482 s` (**2,30× nhanh hơn**).
- **Held-out 10.000 câu:** **`95,8%` KW-Rec / `91,0%` All-KW / `96,2%` Ctx-F1** · Mean `8,60 ms → 3,55 ms` (**2,42× nhanh hơn**), P99 `94,96 ms → 15,62 ms` (**6,08× nhanh hơn**), `282 câu/giây`.
- **64 ca thực tế (`context_cases.json`):** **`98,4%` KW-Rec / `96,9%` All-KW / `99,7%` Ctx-F1** · P50 `12,33 ms → 10,64 ms`, Mean `24,90 ms → 14,80 ms`.
- **Stress Test 10.000 câu:** **`99,1%` KW-Rec / `97,6%` All-KW / `99,2%` Ctx-F1** · P50 `2,05 ms`, Mean `3,24 ms`, **`308 câu/giây`**.

## [1.1.0] - 2026-11-27

Bản nâng cấp lớn về chất lượng mở rộng viết tắt theo ngữ cảnh, độ chính xác khôi phục dấu và tốc độ tra cứu cụm từ.

### Cải tiến chính

- Cải thiện mạnh khả năng mở rộng viết tắt toàn cụm và viết tắt dài nhiều từ dựa trên ngữ cảnh xung quanh.
- Giảm thiểu lỗi lệch dấu thanh điệu trên các cụm từ hiếm hoặc ngoài tập huấn luyện.
- Tối ưu tốc độ tra cứu cụm con trên kho tri thức lớn.
- Tự động tách các từ tiếng Việt bị dính liền do lỗi văn bản nguồn khi nạp dữ liệu và hỗ trợ chia thư mục `data/unprocessed/` – `data/processed/` kèm chống nạp trùng bằng mã băm nội dung.

### Kết quả Benchmark (20.064 câu — `report_10000_20261127_201343.txt`)

- **Held-out 10.000 câu:** **`95,8%` KW-Rec / `91,0%` All-KW / `96,2%` Ctx-F1** (viết tắt toàn cụm đạt `91,0%` KW-Rec và `83,7%` All-KW).
- **64 ca thực tế (`context_cases.json`):** **`98,4%` KW-Rec / `96,9%` All-KW / `99,7%` Ctx-F1** (`62/64` câu đạt 100%, trung vị `12,33 ms`).
- **Stress Test 10.000 câu:** **`99,1%` KW-Rec / `97,6%` All-KW / `99,2%` Ctx-F1** (`206 câu/giây`, trung vị `2,22 ms`).
- Tổng số câu còn thiếu từ khóa trên 20.064 câu giảm **51,3%** so với v1.0.1 (`2.343 → 1.141` câu) và **67,9%** so với v1.0.0 (`3.558 → 1.141` câu).

## [1.0.1] - 2026-11-26

Cải tiến khả năng sửa lỗi chính tả đa từ, khôi phục nhiều vị trí thiếu từ trên câu dài và mở rộng bộ kiểm thử thực tế.

### Cải tiến chính

- Nâng cao độ ổn định khi làm rõ các câu dài có nhiều từ bị thiếu hoặc nhiều từ sai chính tả đứng liền nhau.
- Mở rộng bộ kiểm thử thực tế từ 30 câu lên 64 câu có từ khóa bắt buộc (`97,7%` KW-Rec).
- Held-out 10.000 câu tăng từ `80,0% → 91,9%` KW-Rec (`67,2% → 84,0%` All-KW).

## [1.0.0] - 2026-11-25

Phiên bản ổn định đầu tiên của Factotum — công cụ làm rõ ngữ cảnh & thực thể cho tiếng Việt gõ tắt, bỏ dấu, thiếu từ và sai chính tả.

### Tính năng

- Làm rõ ngữ cảnh tiếng Việt: mở rộng viết tắt chữ cái đầu (`csgt xpvphc`, `xpvphc atgt`), giải nhập nhằng theo ngữ cảnh (`nd` → `nghị định` / `nhân dân`), khôi phục dấu, bổ sung từ bị rơi ở giữa, sửa lỗi chính tả trên câu có dấu, giải mã Telex và ký tự lặp phím.
- Tự học từ tài liệu riêng (`.txt`, `.doc`, `.docx`); hỗ trợ cập nhật tri thức và xây lại dữ liệu trực tiếp trong CLI (`src/app.py`).
- File mô hình `.ftm` thuần dữ liệu (JSON nén `zlib`, không `pickle`); mô hình pháp luật mẫu `1,29 MB` học từ 156 văn bản công khai.
- Lọc câu kết thúc cụt nghĩa (`output/dangling_end_words.txt`) và trả về điểm tin cậy (`confidence`).
- Giao diện dòng lệnh (`python src/app.py`), demo web Gradio (`python demo/app.py`) và bản dùng thử tĩnh trên Hugging Face Spaces (`demo/build_space.py`).
- Bộ benchmark làm rõ ngữ cảnh & từ khóa thực thể (`benchmark/run_benchmark.py`, `benchmark/context_cases.json`) đo `KW-Rec / All-KW / Ctx-F1` trên Held-out 10.000 câu (`80,0% KW-Rec / 80,8% Ctx-F1`), 30 ca thực tế (`98,3% KW-Rec / 96,7% All-KW`) và Stress Test 10.000 câu (`95,1% KW-Rec`).
