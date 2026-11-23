# Phương pháp Benchmark (Factotum)

Thư mục này chứa mã nguồn đánh giá độ chính xác và tốc độ của Factotum cùng 2 baseline (`Base-1g`, `Base-2g`).

## 1. Cách chạy

Từ thư mục gốc `Factotum`:

```bash
# Chạy đầy đủ: Document-level Held-out 10.000 câu + 60 câu thủ công + Stress Test 10.000 câu
python benchmark/run_benchmark.py 10000

# Hoặc chạy nhanh với số lượng Stress Test nhỏ hơn (ví dụ 1.000 câu)
python benchmark/run_benchmark.py 1000
```

Quá trình lấy mẫu cố định `seed = 42`. Chạy xong sẽ tạo 2 file trong `benchmark/`:
- `report_<N>_<timestamp>.txt`: bảng kết quả chi tiết của cả 3 bộ test và bảng Confidence Calibration.
- `failures.csv`: danh sách toàn bộ câu lệch Top-1 (`suite`, `category`, `query`, `expected`, `got_top1`, `got_top3`, `in_top3`, `confidence`, `ms`).

## 2. Cách tạo 3 bộ câu hỏi

### Bộ 1: Document-level Held-out 80/20 (10.000 câu)

1. Đọc toàn bộ 156 văn bản trong `data/`, sắp xếp theo tên file rồi chia ngẫu nhiên (`seed = 42`) theo đơn vị tài liệu: 124 văn bản (80%) để huấn luyện và 32 văn bản còn lại (20%, `468.614` từ) giữ riêng hoàn toàn để kiểm tra.
2. Huấn luyện lại một mô hình Factotum độc lập từ đầu chỉ trên 124 văn bản huấn luyện (lưu trong thư mục tạm và xóa sau khi đo xong).
3. Từ 32 văn bản kiểm tra, trích các cụm `3–5` từ trọn nghĩa (không kết thúc bằng từ cụt lủn trong `output/dangling_end_words.txt`) xuất hiện từ `2` lần trở lên, trong đó mọi từ đơn đều là âm tiết tiếng Việt hợp lệ và đã có trong từ điển tập học.
4. Lấy mẫu theo trọng số tần suất (`seed = 42`) để tạo 10.000 câu gồm 4 dạng:
   - Không dấu (`4.000` câu): bỏ toàn bộ dấu; tách thành cụm đã có trong tập học (`952` câu) và cụm mới hoàn toàn (`3.048` câu).
   - Viết tắt hỗn hợp (`2.000` câu): giữ dấu từ đầu, bỏ dấu các từ giữa, viết tắt chữ cái đầu từ cuối (ví dụ `pháp cua nha thau t`).
   - Viết tắt toàn cụm (`2.000` câu): ghép chữ cái đầu của tất cả các từ trong cụm (ví dụ `ctkt`, `dghc`, `tght`).
   - Thiếu 1 từ ở giữa (`2.000` câu): xóa ngẫu nhiên 1 từ giữa cụm và bỏ dấu các từ còn lại (ví dụ `nay gom cac` từ `này bao gồm các`).

### Bộ 2: Bộ test thủ công (`test_cases.json`, 60 câu)

60 câu hồi quy chia thành 6 nhóm (`acronym`, `contextual`, `first_last_abbr`, `missing_word`, `mixed_abbr`, `unaccented`), dùng để kiểm tra các trường hợp khó khi sửa thuật toán.

Kết quả hiện tại: Factotum đạt `60/60` Top-1 (`100%`) và `60/60` Top-3 (`100%`), so với `Base-1g` (`21,7% / 26,7%`) và `Base-2g` (`56,7% / 61,7%`).

### Bộ 3: Stress Test 10.000 câu (in-sample)

Lấy mẫu theo trọng số tần suất từ chính các cụm `2–6` từ trọn nghĩa đã học (`seed = 42`), dùng để đo tốc độ (`1,50 ms/câu`, `P50 = 1,02 ms`, `665 câu/s`) và khả năng giải mã trên thuật ngữ quen thuộc, không dùng thay cho Held-out.

| Nhóm câu hỏi | Số câu | Base-1g (`T1 / T3 / Local`) | Base-2g (`T1 / T3 / Local`) | Factotum (`T1 / T3 / Local`) |
| :--- | :---: | :---: | :---: | :---: |
| Không dấu (`auto_unaccented`) | 5.000 | 57,7 / 84,1 / 63,2% (`0,06 ms`) | 98,8 / 100,0 / 100,0% (`0,06 ms`) | **99,6 / 100,0 / 100,0%** (`1,32 ms`) |
| Viết tắt hỗn hợp (`auto_mixed_abbr`) | 2.000 | 15,6 / 31,9 / 37,9% (`0,07 ms`) | 77,5 / 88,8 / 98,7% (`0,30 ms`) | **94,0 / 98,9 / 99,9%** (`3,27 ms`) |
| Viết tắt toàn cụm (`auto_acronym`) | 1.500 | 1,6 / 2,5 / 23,5% (`0,06 ms`) | 34,9 / 52,8 / 98,5% (`8,61 ms`) | **72,7 / 89,5 / 99,5%** (**`0,10 ms`**) |
| Thiếu 1 từ ở giữa (`auto_missing`) | 1.500 | 0,0 / 0,0 / 32,5% (`0,05 ms`) | 0,0 / 0,0 / 46,7% (`0,05 ms`) | **75,1 / 98,8 / 99,9%** (`1,17 ms`) |
| **Tổng cộng** | **10.000** | **32,2 / 48,8 / 47,6%** (`0,06 ms`) | **70,2 / 75,7 / 91,5%** (`1,39 ms`) | **90,8 / 98,0 / 99,9%** (**`1,50 ms`**, `665 câu/s`) |

## 3. Tiêu chí chấm và 2 baseline

Cả 3 mô hình (`Base-1g`, `Base-2g`, `Factotum`) đo trên cùng tập câu hỏi với 4 tiêu chí:
- `Top-1`: gợi ý số 1 khớp nguyên văn đáp án gốc.
- `Top-3`: đáp án gốc nằm trong 3 gợi ý đầu.
- `Phrase validity` (trên Held-out): toàn bộ cụm gợi ý số 1 xuất hiện nguyên vẹn trong ngữ liệu; `Local validity` (trên Stress Test): mọi cặp 2 từ liền kề đều tồn tại trong ngữ liệu đã học.
- Tốc độ (`ms`, `câu/s`): đo bằng `time.perf_counter()` trên 1 nhân CPU.

Hai baseline dùng chung từ điển và bảng xác suất chuyển từ chính tập huấn luyện:
- `Base-1g` (Unigram 3-best): chọn theo tần suất từ đơn cao nhất, giữ 3 tổ hợp đầu.
- `Base-2g` (Bigram + Viterbi 3-best): giải mã Viterbi theo xác suất chuyển cặp từ liền kề, giữ 3 đường đi đầu.

## 4. Giới hạn của bộ đo

- Stress Test 10.000 câu là in-sample; để xem khả năng xử lý văn bản mới hãy xem bảng Document-level Held-out.
- Tách theo file luật vẫn có thể còn trùng lặp đoạn trích dẫn hoặc mẫu câu hành chính giữa các nghị định; toàn bộ câu test sinh tự động và đã lọc OOV.
- `Base-1g` và `Base-2g` không có cơ chế chèn từ thiếu nên đạt `0%` Top-1/Top-3 ở nhóm thiếu từ.
- `Phrase validity` chỉ kiểm tra tồn tại trong ngữ liệu, chưa thay thế chấm ngữ nghĩa thủ công.
