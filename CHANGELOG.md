# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.1.0] - 2026-11-27

Bản nâng cấp kiến trúc lõi với **Cấu trúc Giao cắt Đa chiều có Thứ tự (MDIS)**, giúp bứt phá mạnh độ chính xác mở rộng viết tắt theo neo ngữ cảnh và tăng tốc tra cứu cụm con.

### Nâng cấp kiến trúc & tính năng

- **Cấu trúc vật lý MDIS (`src/factotum/engine/mdis.py`):** Triển khai đầy đủ `MDISStore` gồm thực thể (`entities` + 1 thực thể giả `<AUX>`), chiều dương `+` (ngữ cảnh xuôi), chiều âm `-` (hứng giao điểm đối ứng chặn đầu bằng `<AUX>`), giao điểm 2 phía (`events`) truy xuất `other_entity` trong $O(1)$ và chỉ mục tọa độ vật lý nén 64-bit (`edge_postings`) nhanh hơn **2,65×** so với quét chuỗi con trên truy vấn cụm con.
- **Mở rộng viết tắt neo gốc theo bất biến MDIS:** Bổ sung `GraphIndex.mdis_acronym_extensions()` kết hợp hậu kiểm theo neo trái/phải (`+`/`-`) trong `reviewer.py`, bảo toàn trạng thái cụm KB cho viết tắt dài (4+ từ) trong `context.py` và chặn sửa sai nguyên âm đối với từ viết tắt thuần phụ âm trong `spell.py`.
- **Phá vỡ đồng hạng bằng biến dạng dấu NFD:** Khi xác suất chuyển Markov $P_{\text{markov}} = 0$, tự động ưu tiên ứng viên có số thao tác biến đổi dấu nhỏ nhất (tránh lệch thanh điệu).
- **Xuất/nạp kho tri thức MDIS:** Hỗ trợ lưu/nạp file nén `.mdis` (`save_mdis()`, `load_mdis()`) và xuất file văn bản thuần UTF-8 `MDIS.txt` (`export_mdis_txt()`) để kiểm tra trực tiếp.

### Kết quả Benchmark (20.064 câu — `report_10000_20261127_115720.txt`)

- **Held-out 10.000 câu:** **`95,1%` KW-Rec / `89,4%` All-KW / `95,5%` Ctx-F1** (viết tắt toàn cụm tăng vọt từ `73,2% → 88,9%` KW-Rec và `52,8% → 79,8%` All-KW).
- **64 ca thực tế (`context_cases.json`):** **`98,4%` KW-Rec / `96,9%` All-KW / `99,7%` Ctx-F1** (`62/64` câu đạt 100%).
- **Stress Test 10.000 câu:** **`98,9%` KW-Rec / `97,4%` All-KW / `99,1%` Ctx-F1** (viết tắt toàn cụm tăng từ `79,5% → 97,8%` KW-Rec).
- Tổng số câu còn thiếu từ khóa trên 20.064 câu giảm **43,4%** so với v1.0.1 (`2.343 → 1.326` câu) và **62,7%** so với v1.0.0 (`3.558 → 1.326` câu).

## [1.0.1] - 2026-11-26

Cải tiến ngưỡng thích ứng không gán cứng và sửa lỗi chính tả đa từ.

### Nâng cấp chính

- **Ngưỡng thích ứng không gán cứng (Zero-Hardcoding Adaptive Thresholds):** Tự suy diễn `strong_kb_freq`, `medium_kb_freq` và ngưỡng độ tin cậy trực tiếp từ phân phối tần suất của kho tri thức thay vì gán hằng số cố định.
- **Beam Search 2 chiều cho lỗi chính tả đa từ liền kề (`spell.py`):** Khôi phục được 2 từ sai chính tả liên tiếp dựa trên neo đầu/giữa/cuối và xử lý đa vị trí thiếu từ trên câu dài.
- **Kết quả Benchmark:** Held-out 10.000 câu tăng từ `80,0% → 91,9%` KW-Rec (`67,2% → 84,0%` All-KW); mở rộng bộ test thực tế từ 30 lên 64 câu đạt `97,7%` KW-Rec.

## [1.0.0] - 2026-11-25

Phiên bản ổn định đầu tiên của Factotum — công cụ làm rõ ngữ cảnh & thực thể cho tiếng Việt gõ tắt, bỏ dấu, thiếu từ và sai chính tả.

### Tính năng

- Làm rõ ngữ cảnh tiếng Việt: mở rộng viết tắt chữ cái đầu (`csgt xpvphc`, `xpvphc atgt`), giải nhập nhằng theo ngữ cảnh (`nd` → `nghị định` / `nhân dân`), khôi phục dấu, bổ sung từ bị rơi ở giữa, sửa lỗi chính tả trên câu có dấu, giải mã Telex và ký tự lặp phím.
- Tự học từ tài liệu riêng (`.txt`, `.doc`, `.docx`) bằng Suffix Array + Maximal Repeats + đồ thị Markov 2 chiều; có sẵn lõi C++ tùy chọn tăng tốc trích xuất và tùy chọn xây lại đồ thị từ `KnowledgeBase.txt` trong CLI (`src/app.py`).
- File mô hình `.ftm` thuần dữ liệu (JSON nén `zlib`, không `pickle`); mô hình pháp luật mẫu `1,29 MB` học từ 156 văn bản công khai.
- Tầng hậu kiểm 2 chặng (`reviewer.py`) nhận danh sách đoạn chỉnh sửa từ bước tìm kiếm (`edits`), mở rộng cửa sổ ngữ cảnh thích ứng không giới hạn cứng, hội tụ 2 lượt cho các cụm viết tắt đứng sát nhau, lọc câu kết thúc cụt nghĩa (`output/dangling_end_words.txt`) và trả về điểm tin cậy (`confidence`).
- Giao diện dòng lệnh (`python src/app.py`), demo web Gradio (`python demo/app.py`) và bản dùng thử tĩnh trên Hugging Face Spaces (`demo/build_space.py`).
- Bộ benchmark làm rõ ngữ cảnh & từ khóa thực thể (`benchmark/run_benchmark.py`, `benchmark/context_cases.json`) đo `KW-Rec / All-KW / Ctx-F1` trên Held-out 10.000 câu (`80,0% KW-Rec / 80,8% Ctx-F1`), 30 ca thực tế (`98,3% KW-Rec / 96,7% All-KW`) và Stress Test 10.000 câu (`95,1% KW-Rec`).
