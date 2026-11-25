# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.0.0] - 2026-11-25

Phiên bản ổn định đầu tiên của Factotum — công cụ làm rõ ngữ cảnh & thực thể cho tiếng Việt gõ tắt, bỏ dấu, thiếu từ và sai chính tả.

### Tính năng

- Làm rõ ngữ cảnh tiếng Việt: mở rộng viết tắt chữ cái đầu (`csgt xpvphc`, `xpvphc atgt`), giải nhập nhằng theo ngữ cảnh (`nd` → `nghị định` / `nhân dân`), khôi phục dấu, bổ sung từ bị rơi ở giữa, sửa lỗi chính tả trên câu có dấu, giải mã Telex và ký tự lặp phím.
- Tự học từ tài liệu riêng (`.txt`, `.doc`, `.docx`) bằng Suffix Array + Maximal Repeats + đồ thị Markov 2 chiều; có sẵn lõi C++ tùy chọn tăng tốc trích xuất và tùy chọn xây lại đồ thị từ `KnowledgeBase.txt` trong CLI (`src/app.py`).
- File mô hình `.ftm` thuần dữ liệu (JSON nén `zlib`, không `pickle`); mô hình pháp luật mẫu `1,29 MB` học từ 156 văn bản công khai.
- Tầng hậu kiểm 2 chặng (`reviewer.py`) nhận danh sách đoạn chỉnh sửa từ bước tìm kiếm (`edits`), mở rộng cửa sổ ngữ cảnh thích ứng không giới hạn cứng, hội tụ 2 lượt cho các cụm viết tắt đứng sát nhau, lọc câu kết thúc cụt nghĩa (`output/dangling_end_words.txt`) và trả về điểm tin cậy (`confidence`).
- Giao diện dòng lệnh (`python src/app.py`), demo web Gradio (`python demo/app.py`) và bản dùng thử tĩnh trên Hugging Face Spaces (`demo/build_space.py`).
- Bộ benchmark làm rõ ngữ cảnh & từ khóa thực thể (`benchmark/run_benchmark.py`, `benchmark/context_cases.json`) đo `KW-Rec / All-KW / Ctx-F1` trên Held-out 10.000 câu (`80,0% KW-Rec / 80,8% Ctx-F1`), 30 ca thực tế (`98,3% KW-Rec / 96,7% All-KW`) và Stress Test 10.000 câu (`95,1% KW-Rec`).
