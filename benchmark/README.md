# Phương pháp Benchmark Làm rõ Ngữ cảnh (Factotum)

Thư mục này chứa mã nguồn đánh giá khả năng **làm rõ ngữ cảnh và khôi phục từ khóa thực thể bắt buộc** của Factotum so với câu gốc chưa xử lý (`Raw`) và 2 baseline (`Base-1g`, `Base-2g`).

> Để xem **báo cáo kết quả chi tiết 10.000 mẫu, phân tích Confidence Calibration và đường cong tăng trưởng dữ liệu**, mở [REPORT.md](REPORT.md).

## 1. Cách chạy

Từ thư mục gốc `Factotum`:

```bash
# Chạy đầy đủ: Held-out 10.000 câu + 30 câu từ khóa bắt buộc (context_cases.json) + Stress Test 10.000 câu
python benchmark/run_benchmark.py 10000

# Hoặc chạy nhanh với số lượng nhỏ hơn (ví dụ 1.000 câu)
python benchmark/run_benchmark.py 1000
```

Quá trình lấy mẫu cố định `seed = 42`. Chạy xong sẽ tạo 2 file trong `benchmark/`:
- `report_<N>_<timestamp>.txt`: bảng kết quả `KW-Rec / All-KW / Ctx-F1`, độ trễ (`ms`, `P50/P95/P99`, `câu/s`) và bảng Confidence Calibration của cả 3 bộ test.
- `failures.csv`: danh sách các câu còn thiếu ít nhất 1 từ khóa bắt buộc (`suite`, `category`, `query`, `required_keywords`, `got`, `missing_keywords`, `kw_recall`, `ctx_f1`, `confidence`, `ms`).

## 2. Thước đo đánh giá (Không chấm cứng Top-1)

Thay vì ép đầu ra phải khớp nguyên văn từng chữ với 1 câu mẫu duy nhất (Top-1 cứng), bộ benchmark đo xem đầu ra có **làm rõ được đúng ngữ cảnh và các thực thể/từ khóa cốt lõi** hay không:

- **`KW-Rec` (Keyword Recall):** Tỷ lệ từ khóa/thực thể bắt buộc xuất hiện nguyên vẹn trong câu đầu ra của mô hình.
- **`All-KW` (All Keywords Hit):** Tỷ lệ câu khôi phục đủ **100%** mọi từ khóa bắt buộc của câu đó.
- **`Ctx-F1` (Context Content-Word F1):** Độ sát ngữ cảnh tính theo F1 trên tập từ nội dung (đã loại hư từ/từ chức năng như `của`, `về`, `các`, `theo`, `và`...) giữa câu đầu ra và câu chuẩn.
- **Tốc độ (`ms`, `P50/P95/P99`, `câu/s`):** Đo bằng `time.perf_counter()` trên 1 nhân CPU.

Ví dụ: với câu nhập `"nhà nước ban hành nd về luật ld"`, nếu hệ thống làm rõ được các thực thể chính để các công cụ tìm kiếm / embedding / trích xuất thực thể phía sau nhận diện đúng chủ đề mà không cần ép từng hư từ khớp 100% câu mẫu, chỉ số `KW-Rec` và `Ctx-F1` sẽ phản ánh trung thực mức độ hữu ích thực tế.

## 3. Cấu trúc 3 bộ câu hỏi

### Bộ 1: Document-level Held-out 80/20 (10.000 câu)

1. Đọc toàn bộ 156 văn bản trong `data/`, sắp xếp theo tên file rồi chia ngẫu nhiên (`seed = 42`) theo đơn vị tài liệu: **124 văn bản (80%)** để huấn luyện và **32 văn bản còn lại (20%)** giữ riêng hoàn toàn để kiểm tra.
2. Huấn luyện lại một mô hình Factotum độc lập từ đầu chỉ trên 124 văn bản huấn luyện (lưu trong thư mục tạm và xóa sau khi đo xong).
3. Từ 32 văn bản chưa từng học, trích các cụm `3–5` từ trọn nghĩa (không kết thúc bằng từ cụt lủn trong `output/dangling_end_words.txt`), tự động trích tập từ khóa nội dung bắt buộc (bỏ hư từ) và lấy mẫu `10.000` câu theo 4 dạng:
   - Không dấu (`4.000` câu): tách thành cụm đã có trong tập học (`936` câu) và cụm mới hoàn toàn (`3.064` câu).
   - Viết tắt hỗn hợp (`2.000` câu): giữ dấu từ đầu, bỏ dấu các từ giữa, viết tắt chữ cái đầu từ cuối.
   - Viết tắt toàn cụm (`2.000` câu): ghép chữ cái đầu của tất cả các từ trong cụm.
   - Thiếu 1 từ ở giữa (`2.000` câu): xóa ngẫu nhiên 1 từ giữa cụm và bỏ dấu các từ còn lại.

### Bộ 2: Bộ test Làm rõ ngữ cảnh & Từ khóa bắt buộc ([context_cases.json](context_cases.json), 30 câu)

30 câu thực tế được gán tay danh sách `required_keywords` bắt buộc, chia thành 4 nhóm:
- `context_disambiguation` (10 câu): giải nhập nhằng cùng 1 từ viết tắt/không dấu theo ngữ cảnh khác nhau (ví dụ `tòa án nd tối cao` vs `nd 23/24 quy định về atgt đường bộ`).
- `multi_entity_context` (10 câu): nhiều thực thể viết tắt dài đứng cùng câu hoặc sát nhau (ví dụ `csgt xpvphc người không có gplx`).
- `missing_word_enrichment` (5 câu): rơi từ ở giữa làm mờ thực thể (ví dụ `an toan thong`, `cơ quan nước`).
- `typo_and_unaccented` (5 câu): gõ Telex, lặp phím, sai chính tả trên câu có dấu.

### Bộ 3: Stress Test 10.000 câu (in-sample)

Lấy mẫu theo trọng số tần suất từ các cụm `2–6` từ trọn nghĩa đã học (`seed = 42`) để đo tốc độ thực tế (`2,31 ms/câu`, `P50 = 1,43 ms`, `433 câu/s`) và độ phủ từ khóa trên thuật ngữ quen thuộc:

| Nhóm câu hỏi | Số câu | Raw (`KW / All / F1`) | Base-1g (`KW / All / F1`) | Base-2g (`KW / All / F1`) | Factotum (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Không dấu (`auto_unaccented`) | 5.000 | 9,4 / 1,2 / 8,7% | 78,9 / 57,3 / 79,9% (`0,04 ms`) | 99,3 / 99,0 / 99,3% (`0,05 ms`) | **99,8 / 99,7 / 99,8%** (`1,59 ms`) |
| Viết tắt hỗn hợp (`auto_mixed_abbr`) | 2.000 | 27,5 / 1,1 / 25,5% | 63,8 / 21,6 / 64,5% (`0,06 ms`) | 94,6 / 85,4 / 94,6% (`0,14 ms`) | **98,6 / 96,4 / 98,5%** (`5,90 ms`) |
| Viết tắt toàn cụm (`auto_acronym`) | 1.500 | 0,0 / 0,0 / 0,0% | 16,8 / 4,5 / 16,2% (`0,04 ms`) | 53,8 / 43,5 / 53,3% (`2,36 ms`) | **82,2 / 78,6 / 82,2%** (**`0,23 ms`**) |
| Thiếu 1 từ ở giữa (`auto_missing`) | 1.500 | 6,3 / 0,1 / 6,8% | 54,2 / 6,5 / 64,6% (`0,04 ms`) | 57,5 / 7,3 / 67,5% (`0,04 ms`) | **87,8 / 77,1 / 90,0%** (`1,99 ms`) |
| **Tổng cộng** | **10.000** | **11,1 / 0,8 / 10,5%** | **62,9 / 34,6 / 65,0%** (`0,05 ms`) | **85,3 / 74,2 / 86,7%** (`0,42 ms`) | **95,1 / 92,5 / 95,4%** (**`2,31 ms`**, `433 câu/s`) |

## 4. Hai baseline đối chứng

Hai baseline dùng chung từ điển và bảng xác suất chuyển từ chính tập huấn luyện:
- `Base-1g` (Unigram): chọn tổ hợp có tần suất từ đơn cao nhất.
- `Base-2g` (Bigram + Viterbi): giải mã Viterbi theo xác suất chuyển cặp từ liền kề.

## 5. Giới hạn của bộ đo

- Stress Test 10.000 câu là in-sample; để đánh giá khả năng tổng quát trên văn bản mới hãy xem bảng Document-level Held-out ở [REPORT.md](REPORT.md).
- Tách theo file văn bản luật vẫn có thể còn trùng lặp một phần mẫu câu hành chính giữa các nghị định; các câu Held-out/Stress Test được sinh tự động và đã lọc OOV.
- `Base-1g` và `Base-2g` không có cơ chế chèn từ thiếu nên chỉ đạt `All-KW` rất thấp ở nhóm thiếu từ (`6,5% – 15,2%`).
