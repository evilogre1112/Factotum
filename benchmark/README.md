# Phương pháp Benchmark Làm rõ Ngữ cảnh (Factotum)

Thư mục này chứa mã nguồn đánh giá khả năng **làm rõ ngữ cảnh và khôi phục từ khóa thực thể bắt buộc** của Factotum so với câu gốc chưa xử lý (`Raw`) và 2 baseline (`Base-1g`, `Base-2g`).

> Để xem **báo cáo kết quả chi tiết 10.000 mẫu, phân tích Confidence Calibration và đường cong tăng trưởng dữ liệu**, mở [REPORT.md](REPORT.md).

## 1. Cách chạy

Từ thư mục gốc `Factotum`:

```bash
# Chạy đầy đủ: Held-out 10.000 câu + 64 câu từ khóa bắt buộc (context_cases.json) + Stress Test 10.000 câu
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
3. Từ 32 văn bản chưa từng học, trích xuất các cụm `2–6` từ trọn nghĩa bằng Suffix Array / LCP (loại bỏ cụm cụt đầu/cuối và đảm bảo có ít nhất 2 thực từ danh/động từ), trích tập từ khóa nội dung bắt buộc (bỏ hư từ và từ nghi vấn phụ trợ) và lấy mẫu `10.000` câu theo 4 dạng:
   - Không dấu (`4.000` câu): tách thành cụm đã có trong tập học (`3.585` câu) và cụm mới hoàn toàn (`415` câu).
   - Viết tắt hỗn hợp (`2.000` câu): giữ dấu từ đầu, bỏ dấu các từ giữa, viết tắt chữ cái đầu từ cuối.
   - Viết tắt toàn cụm (`2.000` câu): các cơ quan/tổ chức chuẩn được viết tắt độc lập; các cụm viết tắt khác đi kèm từ neo ngữ cảnh.
   - Thiếu 1 từ ở giữa (`2.000` câu): xóa ngẫu nhiên 1 từ giữa cụm và bỏ dấu các từ còn lại.

### Bộ 2: Bộ test Làm rõ ngữ cảnh & Từ khóa bắt buộc ([context_cases.json](context_cases.json), 64 câu)

64 câu thực tế được gán tay danh sách `required_keywords` (chỉ giữ danh từ, động từ và thuật ngữ thực thể), chia thành 7 nhóm:
- `canonical_acronym` (11 câu): viết tắt độc lập tên cơ quan, tổ chức, văn bản chuẩn (`ubnd`, `csgt`, `bhxh`, `btp`, `tand`, `gplx`...).
- `context_disambiguation` (10 câu): giải nhập nhằng cùng 1 từ viết tắt/không dấu theo ngữ cảnh khác nhau (`tòa án nd tối cao` vs `nd 23/24 quy định về atgt đường bộ`).
- `contextual_acronym` (5 câu): viết tắt chuyên ngành đi kèm từ neo ngữ cảnh (`bien ban bqts ke bien`, `thu tuc ldc hop phap`...).
- `multi_entity_context` (10 câu): nhiều thực thể viết tắt dài đứng cùng câu (`csgt xpvphc người không có gplx`).
- `repaired_clean_phrases` (10 câu): các mệnh đề pháp lý trọn vẹn không dấu (`he thong trich xuat ma so thue`, `thi hanh quyet dinh cua trong tai`...).
- `missing_word_enrichment` (5 câu): rơi từ ở giữa làm mờ thực thể (`an toan thong đường bộ`, `giay lai xe`).
- `typo_and_user_query` (13 câu): lỗi chính tả và câu hỏi thực tế của người dùng (`uống dượu bia phạt bao nhiêu`, `sang tên sổ đỏ mất những khoản phí nào`...).

### Bộ 3: Stress Test 10.000 câu (in-sample)

Lấy mẫu theo trọng số tần suất từ các cụm `2–6` từ trọn nghĩa đã học (`seed = 42`) để đo tốc độ thực tế (`3,39 ms/câu`, `P50 = 2,20 ms`) và độ phủ từ khóa trên thuật ngữ quen thuộc:

| Nhóm câu hỏi | Số câu | Raw (`KW / All / F1`) | Base-1g (`KW / All / F1`) | Base-2g (`KW / All / F1`) | Factotum (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Không dấu (`auto_unaccented`) | 5.000 | 10,0 / 0,3 / 9,9% | 77,5 / 52,4 / 78,8% (`0,04 ms`) | 99,5 / 99,1 / 99,6% (`0,05 ms`) | **99,8 / 99,6 / 99,8%** (`1,57 ms`) |
| Viết tắt hỗn hợp (`auto_mixed_abbr`) | 2.000 | 31,2 / 0,2 / 29,9% | 62,7 / 11,1 / 65,3% (`0,05 ms`) | 93,1 / 79,1 / 93,8% (`0,12 ms`) | **98,6 / 96,2 / 98,7%** (`5,57 ms`) |
| Viết tắt toàn cụm (`auto_acronym`) | 1.500 | 31,3 / 0,2 / 38,6% | 39,2 / 2,9 / 41,7% (`0,05 ms`) | 78,2 / 56,1 / 79,3% (`1,62 ms`) | **79,4 / 63,9 / 80,3%** (`6,36 ms`) |
| Thiếu 1 từ ở giữa (`auto_missing`) | 1.500 | 7,8 / 0,0 / 8,4% | 62,4 / 7,6 / 70,3% (`0,04 ms`) | 73,3 / 12,9 / 81,3% (`0,05 ms`) | **97,8 / 93,3 / 98,2%** (`3,57 ms`) |
| **Tổng cộng** | **10.000** | **17,1 / 0,2 / 18,0%** | **66,5 / 30,0 / 69,3%** (`0,04 ms`) | **91,1 / 75,7 / 92,6%** (`0,30 ms`) | **96,2 / 92,6 / 96,4%** (**`3,39 ms`**, `295 câu/s`) |

## 4. Hai baseline đối chứng

Hai baseline dùng chung từ điển và bảng xác suất chuyển từ chính tập huấn luyện:
- `Base-1g` (Unigram): chọn tổ hợp có tần suất từ đơn cao nhất.
- `Base-2g` (Bigram + Viterbi): giải mã Viterbi theo xác suất chuyển cặp từ liền kề.

## 5. Giới hạn của bộ đo

- Stress Test 10.000 câu là in-sample; để đánh giá khả năng tổng quát trên văn bản mới hãy xem bảng Document-level Held-out ở [REPORT.md](REPORT.md).
- Tách theo file văn bản luật vẫn có thể còn trùng lặp một phần mẫu câu hành chính giữa các nghị định; các câu Held-out/Stress Test được sinh tự động và đã lọc OOV.
- `Base-1g` và `Base-2g` không có cơ chế chèn từ thiếu nên chỉ đạt `All-KW` rất thấp ở nhóm thiếu từ (`6,5% – 15,2%`).
