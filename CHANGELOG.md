# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.1.0] - 2026-11-27

Bản nâng cấp lớn về chất lượng mở rộng viết tắt theo ngữ cảnh, độ chính xác khôi phục dấu và tốc độ tra cứu cụm từ.

### Cải tiến chính

- Cải thiện mạnh khả năng mở rộng viết tắt toàn cụm và viết tắt dài nhiều từ dựa trên ngữ cảnh xung quanh.
- Giảm thiểu lỗi lệch dấu thanh điệu trên các cụm từ hiếm hoặc ngoài tập huấn luyện.
- Tối ưu tốc độ tra cứu cụm con trên kho tri thức lớn.

### Kết quả Benchmark (20.064 câu — `report_10000_20261127_115720.txt`)

- **Held-out 10.000 câu:** **`95,1%` KW-Rec / `89,4%` All-KW / `95,5%` Ctx-F1** (viết tắt toàn cụm tăng từ `73,2% → 88,9%` KW-Rec và `52,8% → 79,8%` All-KW).
- **64 ca thực tế (`context_cases.json`):** **`98,4%` KW-Rec / `96,9%` All-KW / `99,7%` Ctx-F1** (`62/64` câu đạt 100%).
- **Stress Test 10.000 câu:** **`98,9%` KW-Rec / `97,4%` All-KW / `99,1%` Ctx-F1** (viết tắt toàn cụm tăng từ `79,5% → 97,8%` KW-Rec).
- Tổng số câu còn thiếu từ khóa trên 20.064 câu giảm **43,4%** so với v1.0.1 (`2.343 → 1.326` câu) và **62,7%** so với v1.0.0 (`3.558 → 1.326` câu).

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
