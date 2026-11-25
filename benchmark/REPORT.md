# Báo cáo Benchmark & Đánh giá Chi tiết (Factotum)

Tài liệu này tổng hợp toàn bộ kết quả đo đạc thực nghiệm, phân tích độ tin cậy (`confidence`), Ablation Study, đối đầu trực tiếp với LLM, đường cong tăng trưởng dữ liệu và bảng so sánh với các phương pháp khác của Factotum.

> Xem cách thiết lập 3 bộ test và lệnh tự chạy lại toàn bộ benchmark tại [benchmark/README.md](README.md).

---

## Giải thích nhanh các thuật ngữ đo đạc

| Thuật ngữ | Giải thích ngắn |
| :--- | :--- |
| **Maximal Repeats (Cụm lặp tối đại)** | Cụm từ xuất hiện lặp lại trọn vẹn trong tài liệu (ví dụ `xử phạt vi phạm hành chính`), được rút tự động bằng thuật toán `Suffix Array & LCP` mà không cần gán nhãn thủ công. |
| **Đồ thị Markov (2 chiều)** | Lưu xác suất hai từ đứng liền nhau (`A → B` và `B ← A`), giúp ghép câu mới chưa từng thấy nguyên vẹn và đoán từ bị thiếu ở giữa. |
| **OOV (Out-of-Vocabulary)** | Từ hoàn toàn mới chưa từng xuất hiện trong tập tài liệu đã học. |
| **ECE (Expected Calibration Error)** | Độ lệch trung bình (%) giữa điểm `confidence` thư viện báo ra và tỷ lệ đúng thực tế; ECE càng nhỏ thì điểm tin cậy càng sát thực tế. |

## Đọc các chỉ số Top-1, Top-3 và Phrase validity

Với câu không dấu đầy đủ (`xu phat vi pham hanh chinh`), đáp án gần như duy nhất nên chấm **Top-1** là đủ. Với viết tắt hoặc thiếu từ, một chuỗi ngắn có nhiều trường hợp hợp lệ khác nhau trong cùng lĩnh vực pháp luật; chấm cứng Top-1 sẽ tính các đáp án hợp lệ khác là sai:

| Câu nhập | Đáp án gốc trong test | Gợi ý Top-1 của Factotum | Chấm Top-1 | Nhận xét |
| :--- | :--- | :--- | :---: | :--- |
| `cnt` | `cho nhà thầu` | `của nhà thầu` | Sai | Cụm hợp lệ trong ngữ liệu |
| `tth` | `trừ trường hợp` | `trong trường hợp` | Sai | Cụm hợp lệ trong ngữ liệu |
| `cgt` | `cho gói thầu` | `của gói thầu` | Sai | Cụm hợp lệ trong ngữ liệu |
| `nvld` | `nghĩa vụ lao động` | `nghĩa vụ liên đới` | Sai | Cụm hợp lệ trong ngữ liệu |

Do đó các bảng bên dưới báo cáo song song **Top-1**, **Top-3** và **Phrase validity** (tỷ lệ toàn bộ cụm gợi ý số 1 xuất hiện nguyên vẹn trong ngữ liệu, chặt hơn kiểm tra từng cặp 2 từ riêng lẻ).

![Tổng quan Benchmark Factotum](../docs/images/benchmark_overview.png)

---

## 1. Document-level Held-out (124 văn bản học / 32 văn bản mới, 10.000 câu)

Tách hoàn toàn theo đơn vị file (`seed = 42`, tập kiểm tra rút các cụm trọn nghĩa không cụt lủn từ 32 văn bản chưa từng học: `4.000` câu không dấu, `2.000` viết tắt hỗn hợp, `2.000` thiếu từ, `2.000` viết tắt toàn cụm). Đối chiếu với `Base-1g` (Unigram 3-best) và `Base-2g` (Bigram + Viterbi 3-best) dùng chung từ điển và xác suất chuyển:

| Nhóm câu hỏi | Số câu | Base-1g (`T1 / T3 / Phrase`) | Base-2g (`T1 / T3 / Phrase`) | Factotum (`T1 / T3 / Phrase`) |
| :--- | :---: | :---: | :---: | :---: |
| Không dấu đã có trong tập học | 936 | 52,7 / 78,8 / 54,8% (`0,13 ms`) | 99,4 / 100,0 / 99,5% (`0,16 ms`) | **100,0 / 100,0 / 100,0%** (`2,03 ms`) |
| Không dấu mới hoàn toàn | 3.064 | 42,3 / 69,3 / 43,0% (`0,14 ms`) | **90,9 / 98,1** / 91,8% (`0,18 ms`) | 89,0 / 95,7 / **92,3%** (`4,30 ms`) |
| Viết tắt hỗn hợp | 2.000 | 11,2 / 23,2 / 16,2% (`0,14 ms`) | 59,6 / 75,7 / 69,0% (`0,58 ms`) | **70,8 / 82,6 / 82,0%** (`5,80 ms`) |
| Thiếu 1 từ ở giữa | 2.000 | 0,0 / 0,0 / 25,3% (`0,11 ms`) | 0,0 / 0,0 / 32,2% (`0,12 ms`) | **33,6 / 52,1 / 61,0%** (`3,11 ms`) |
| Viết tắt toàn cụm | 2.000 | 0,4 / 0,9 / 4,1% (`0,13 ms`) | 14,3 / 24,1 / 47,0% (`18,09 ms`) | **24,8 / 31,8 / 90,0%** (**`12,51 ms`**) |
| **Tổng (bỏ nhóm thiếu từ)** | **8.000** | **25,3 / 41,8 / 28,0%** | **64,9 / 74,2 / 75,8%** | **69,7 / 77,0 / 90,1%** |
| **Tổng cộng (tất cả)** | **10.000** | **20,2 / 33,4 / 27,4%** (`0,13 ms`) | **51,9 / 59,4 / 67,1%** (`3,83 ms`) | **62,4 / 72,0 / 84,2%** (**`5,79 ms`**, `P50 = 2,98 ms`) |

Hai baseline đạt `0%` Top-1/Top-3 ở nhóm thiếu từ vì không có cơ chế chèn từ. Khi bỏ nhóm này (`8.000` câu), Factotum hơn `Base-2g` `+4,8%` Top-1 (`69,7%` vs `64,9%`) và `+14,3%` Phrase validity (`90,1%` vs `75,8%`). Riêng ở nhóm không dấu mới hoàn toàn (`3.064` câu), `Base-2g` cao hơn Factotum `1,9%` Top-1 (`90,9%` vs `89,0%`) và nhanh hơn, trong khi Factotum vượt ở Phrase validity (`92,3%` vs `91,8%`).

Trên bộ **Stress Test 10.000 câu** (in-sample), Factotum đạt **`91,1%` Top-1 / `98,2%` Top-3 / `99,9%` LV** (`2,01 ms/câu`, `P50 = 1,34 ms`, `498 câu/s`, vượt `Base-2g` `+19,9%` Top-1). Bộ hồi quy thủ công đạt **`60/60` (`100%`)**, bộ bảo toàn câu hỏi có dấu chuẩn đạt **`23/23` (`100%`)**. Chi tiết phương pháp xem tại [README.md](README.md) và danh sách `4.644` câu lệch Top-1 tại [failures.csv](failures.csv).

---

## 2. Độ tin cậy (`confidence`) & Ablation Study (10.000 câu Document-level Held-out)

![Phân tích Confidence Calibration và Ablation Study](../docs/images/confidence_calibration.png)

| Mức `confidence` | Số câu | `confidence` TB | Đúng Top-1 | Đúng Top-3 | Phrase validity |
| :---: | :---: | :---: | :---: | :---: | :---: |
| `90 – 100%` | 3.887 | 95,3% | 67,4% | 75,5% | **99,7%** |
| `70 – 90%` | 2.104 | 80,7% | 70,9% | 79,5% | **93,6%** |
| `50 – 70%` | 1.999 | 57,7% | 64,8% | 74,4% | **77,2%** |
| `30 – 50%` | 1.309 | 44,4% | 52,0% | 64,4% | **64,5%** |
| `< 30%` | 701 | 9,6% | 22,7% | 36,7% | **27,4%** |

Với ngưỡng `confidence >= 70%` (`5.991/10.000` câu), Phrase validity đạt `93,6% – 99,7%` nên có thể tự áp dụng hoặc ưu tiên hiển thị; mức `50–70%` (`77,2%`) phù hợp hiển thị dạng gợi ý; mức `< 30%` (`27,4%`) hoặc `is_fallback = True` nên giữ nguyên câu gốc. Sai số hiệu chuẩn hiện tại: `ECE (Phrase validity) = 12,2%`, `ECE (Top-1) = 16,2%`.

Ablation Study trên cùng 10.000 câu:

| Cấu hình | Top-1 | Phrase validity | Ghi chú |
| :--- | :---: | :---: | :--- |
| Chỉ Maximal Repeats (bỏ đồ thị Markov) | `17,7%` | `30,6%` | Khớp cụm đã học (`100%`), `0%` cụm mới và thiếu từ. |
| Chỉ Đồ thị Bigram (`Base-2g`, bỏ Maximal Repeats) | `51,9%` | `67,1%` | Tốt ở không dấu mới (`90,9%`), yếu ở viết tắt (`47,0%` PV) và `0%` thiếu từ. |
| **Factotum đầy đủ (Maximal Repeats + Đồ thị + Chèn từ)** | **`62,4%`** | **`84,2%`** | Giữ cụm trọn nghĩa ở viết tắt và ghép cụm mới/chèn từ bằng đồ thị. |

---

## 3. Đối đầu trực tiếp với LLM (Held-out, zero-shot)

Rút `1.000` câu độc nhất từ 32 văn bản kiểm tra chưa từng học (`seed = 42`, chia đều 4 nhóm: không dấu mới, viết tắt hỗn hợp, thiếu 1 từ và viết tắt toàn cụm), lọc còn **`919` cụm trọn nghĩa** (bỏ `81` cửa sổ bị cắt cụt lủn theo `../output/dangling_end_words.txt`). Hai LLM lớn chạy ở chế độ zero-shot trên cùng một prompt có quy tắc giữ đúng độ dài cụm (cả hai trả lời đủ `1.000/1.000` câu và tuân thủ đúng quy tắc):

| Nhóm câu hỏi | Số câu | Factotum (`T1 / PV`) | Gemini Hydrogen, zero-shot (`T1 / PV`) | Claude Sonnet 5.5, zero-shot (`T1 / PV`) |
| :--- | :---: | :---: | :---: | :---: |
| Không dấu mới hoàn toàn | 231 | 84,4% / **90,0%** (`91,8%` T3) | **85,7%** / 87,9% | 79,7% / 81,8% |
| Viết tắt hỗn hợp | 228 | 63,6% / **78,9%** (`75,4%` T3) | 64,0% / 78,1% | **65,8%** / **78,9%** |
| Thiếu 1 từ ở giữa | 234 | 34,6% / **64,5%** (`51,7%` T3) | 33,3% / 59,0% | **36,8%** / 51,7% |
| Viết tắt toàn cụm | 226 | **22,6% / 92,0%** (`30,5%` T3) | 6,6% / 43,8% | 2,2% / 29,6% |
| **Tổng cộng (cụm trọn nghĩa)** | **919** | **51,4% / 81,3%** (`62,5%` T3) | **47,6% / 67,2%** | **46,2% / 60,6%** |
| **Thời gian chạy 1.000 câu** | | **`3,04 giây`** (1 CPU offline, `1,29 MB`) | `~15 giây` (batch cloud) | `~20 phút` (cloud) |

*Lưu ý công bằng:* trong tập `919` câu cố định giữ nguyên để so đối xứng với Gemini và Claude, có `26` câu kết thúc bằng từ `"của"` nay được tầng hậu kiểm của Factotum chủ động giữ nguyên câu gốc theo `../output/dangling_end_words.txt`; nếu loại hết `107` cụm cụt lủn (còn `893` cụm trọn nghĩa), Factotum đạt **`52,9%` Top-1 / `82,8%` PV** (`64,3%` T3; riêng viết tắt hỗn hợp `66,2%` Top-1 và viết tắt toàn cụm `93,2%` PV). Tính trên cả `1.000` câu thô, Factotum vẫn dẫn đầu với `47,2%` Top-1 / `77,2%` PV so với Gemini (`46,4% / 66,3%`) và Claude (`45,4% / 60,2%`). Hai LLM chạy zero-shot (không RAG, không fine-tune trên 124 văn bản luật), trong khi Factotum đã học trực tiếp phân phối cụm từ của lĩnh vực pháp luật. Ở nhóm viết tắt toàn cụm chuyên ngành, Factotum vượt trội về cả Top-1 (`22,6%`) lẫn Phrase validity (`92,0%`) với chi phí tính toán nhỏ hơn nhiều lần.

---

## 4. Giới hạn của bộ đo

- Các nghị định pháp luật thường trích dẫn và dùng lại nhiều mẫu câu hành chính giống nhau, nên tách theo file vẫn có thể còn trùng lặp nội dung một phần. Chưa có phép thử tách theo năm ban hành, theo loại văn bản hoặc cross-domain (học luật, kiểm tra kế toán/y tế).
- Toàn bộ câu kiểm tra được sinh tự động từ ngữ liệu và đã lọc OOV (chưa có tập log gõ thật của người dùng). `Phrase validity` chỉ kiểm tra nguyên cụm có tồn tại trong ngữ liệu, chưa thay thế đánh giá ngữ nghĩa thủ công.

---

## 5. Tỷ lệ tăng trưởng dữ liệu theo văn bản đầu vào

![Đường cong tăng trưởng dữ liệu Factotum](../docs/images/scaling_curve.png)

| Số văn bản | Số từ đầu vào (`N`) | Dung lượng văn bản thô | File kiến thức `.txt` (chưa nén) | Số từ lưu trong cụm kiến thức | File dữ liệu đóng gói `.ftm` |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 10 | 365.615 từ | 2,13 MB | 8,57 MB (**402%**) | 232.179 từ (**63,5%**) | 434,9 KB (**20,0%**) |
| 30 | 860.123 từ | 5,01 MB | 13,58 MB (**271%**) | 400.387 từ (**46,5%**) | 707,4 KB (**13,8%**) |
| 80 | 1.218.295 từ | 7,10 MB | 15,04 MB (**212%**) | 455.391 từ (**37,4%**) | 796,4 KB (**11,0%**) |
| 156 | 2.618.007 từ | 15,21 MB | 24,51 MB (**161%**) | 775.032 từ (**29,6%**) | 1,29 MB (**8,5%**, nén `11,8 : 1`) |

Hồi quy log-log ($\log S = a + \alpha \log N$) trên 4 mốc với $N$ là tổng số từ đầu vào:
- File `.txt` chưa nén: $S_{\text{txt}} \propto N^{0.53}$
- File đóng gói `.ftm`: $S_{\text{ftm}} \propto N^{0.56}$
- Số từ lưu trong cụm kiến thức: $S_{\text{words}} \propto N^{0.60}$

---

## 6. Quá trình cải tiến qua các phiên bản

![Biểu đồ tăng trưởng qua các phiên bản Factotum](../docs/images/version_progress.png)

---

## 7. So sánh với các hướng tiếp cận khác

Số liệu `Base-1g`, `Base-2g` và `Factotum` lấy từ Document-level Held-out 10.000 câu; cột `LLM (zero-shot)` tham chiếu kết quả đo trực tiếp trên `919` cụm trọn nghĩa ở mục 3; cột `SymSpell` là mức tham khảo định tính:

| Tiêu chí | `Base-1g` (Unigram) | `Base-2g` (Bigram + Viterbi) | Factotum | SymSpell / Edit Distance* | LLM (zero-shot) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Khôi phục dấu (cụm mới) | `42,3%` Top-1 | **`90,9%`** Top-1 (`98,1%` Top-3) | `89,0%` Top-1 (`92,3%` PV) | Không hỗ trợ | `79,7% – 85,7%` Top-1 (mục 3) |
| Khôi phục viết tắt toàn cụm | `0,4%` Top-1 (`4,1%` PV) | `14,3%` Top-1 (`47,0%` PV) | **`24,8%`** Top-1 (**`90,0%`** PV) | Không | `2,2% – 6,6%` Top-1 (`29,6% – 43,8%` PV) |
| Đoán từ bị thiếu ở giữa | `0%` | `0%` | `33,6%` Top-1 (`61,0%` PV) | Không | `33,3% – 36,8%` Top-1 (`51,7% – 59,0%` PV) |
| Sửa lỗi chính tả & gõ Telex | Không | Không | Sửa lỗi chính tả, giữ nguyên câu đúng | Tốt (từng từ đơn) | Tốt |
| Tự huấn luyện trên dữ liệu riêng | Nhanh | Nhanh | `~32 giây` (CPU + C++) / `~105 giây` (Python thuần) | Nhanh | Chậm, thường cần GPU |
| Dung lượng (Đĩa / RAM) | Vài MB | Vài MB | `1,29 MB` đĩa / `~147–197 MB` RAM | Rất nhỏ | `400 MB` – hàng chục GB* |
| Tốc độ suy luận | `0,13 ms/câu` (CPU) | `3,83 ms/câu` (CPU) | `2,01 – 5,79 ms/câu` (CPU offline) | `< 1 ms/câu`* | `~15 ms` (batch) – `1,2 s/câu` (cloud) |
| Hiểu ngữ nghĩa mở đa lĩnh vực | Không | Không | Không | Không | Cao |

*\*Khoảng tham khảo tùy kiến trúc mô hình, mức lượng tử hóa và phần cứng.*
