# Báo cáo Đánh giá Thực nghiệm (Factotum v1.1.0 — MDIS)

## Tóm tắt kết quả chính

Báo cáo tổng hợp kết quả kiểm thử Factotum v1.1.0 trên **20.064 câu** thuộc 3 bộ dữ liệu độc lập (nguồn số liệu chạy thực tế: [report_10000_20261127_115720.txt](report_10000_20261127_115720.txt)):

| Bộ kiểm thử | Quy mô | Câu gốc chưa xử lý (`KW-Rec`) | Đối chiếu chính (`KW-Rec`) | **Factotum v1.1 (`KW-Rec` / `All-KW` / `Ctx-F1`)** | Tốc độ (Trung vị / TB) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Văn bản chưa từng học (Held-out)** | 10.000 câu | 20,0% | Base-2g: 86,8% | **95,1% / 89,4% / 95,5%** | `3,61 ms` / `6,96 ms` |
| **2. Bộ 64 ca thực tế có từ khóa bắt buộc** | 64 câu | 23,4% | Base-2g: 64,3% | **98,4% / 96,9% / 99,7%** | `16,25 ms` / `43,72 ms` |
| **3. Đối đầu trực tiếp với LLM (Held-out)** | 893 cụm trọn nghĩa | 11,3% | Gemini: 71,2% / Claude: 68,9% | **74,7% / 59,2% / 75,5%** | `9,39 giây / 1.000 câu` |
| **4. Kiểm thử tải trên thuật ngữ đã học** | 10.000 câu | 17,1% | Base-2g: 91,1% | **98,9% / 97,4% / 99,1%** | `3,05 ms` / `6,33 ms` |

> Xem quy trình tạo dữ liệu và câu lệnh tự chạy lại toàn bộ benchmark tại [benchmark/README.md](README.md).

---

## 1. Hệ thống chỉ số đánh giá

Đánh giá tập trung vào khả năng **làm rõ ngữ cảnh và khôi phục các từ khóa thực thể cốt lõi** của câu đầu ra thay vì ép so khớp từng chữ với một câu mẫu duy nhất. Cách đo này phản ánh trực tiếp mức độ hữu ích khi Factotum đứng trước công cụ tìm kiếm, trích xuất thực thể hoặc gợi ý:

| Chỉ số | Giải thích |
| :--- | :--- |
| **`KW-Rec`** (Tỷ lệ phủ từ khóa) | Phần trăm từ khóa / thực thể bắt buộc xuất hiện nguyên vẹn trong câu đầu ra. |
| **`All-KW`** (Đủ 100% từ khóa) | Phần trăm câu khôi phục đầy đủ toàn bộ từ khóa bắt buộc của câu đó. |
| **`Ctx-F1`** (Độ sát ngữ cảnh) | Mức độ trùng khớp ngữ cảnh (tính trên các từ mang nghĩa chính, đã loại bỏ hư từ như `của`, `về`, `các`, `và`...) so với câu chuẩn. |

---

## 2. Tổng quan kết quả trên 20.000 câu (Held-out & Kiểm thử tải)

![Tổng quan Benchmark Làm rõ Ngữ cảnh Factotum](../docs/images/benchmark_overview.png)

### 2.1. Kiểm thử trên văn bản chưa từng học (124 văn bản học / 32 văn bản mới — 10.000 câu)

Tập dữ liệu gồm 156 văn bản pháp luật được tách hoàn toàn theo từng file (`seed = 42`): 124 văn bản dùng để học (`28,69 giây` dựng mô hình, `100.246` cụm) và 32 văn bản còn lại dùng để trích xuất 10.000 câu kiểm thử trọn nghĩa.

| Nhóm câu hỏi | Số câu | Câu gốc (`KW / All / F1`) | Base-1g (`KW / All / F1`) | Base-2g (`KW / All / F1`) | Factotum v1.1 (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Không dấu đã có trong tập học | 3.585 | 9,2 / 0,5 / 9,1% | 78,3 / 55,5 / 79,6% (`0,04 ms`) | 99,5 / 99,2 / 99,5% (`0,05 ms`) | **99,8 / 99,7 / 99,8%** (`1,65 ms`) |
| Không dấu mới hoàn toàn | 415 | 11,1 / 0,0 / 10,6% | 71,6 / 33,0 / 73,4% (`0,05 ms`) | **95,7 / 91,1 / 95,9%** (`0,06 ms`) | 94,7 / 89,2 / 94,5% (`17,36 ms`) |
| Viết tắt hỗn hợp | 2.000 | 41,9 / 0,2 / 41,2% | 61,0 / 17,5 / 64,5% (`0,05 ms`) | 90,1 / 77,5 / 90,8% (`0,10 ms`) | **95,0 / 89,3 / 95,3%** (`8,09 ms`) |
| Thiếu 1 từ ở giữa | 2.000 | 7,3 / 0,0 / 7,9% | 62,8 / 8,8 / 70,6% (`0,05 ms`) | 72,8 / 14,0 / 80,8% (`0,06 ms`) | **93,0 / 80,5 / 94,5%** (`13,98 ms`) |
| Viết tắt toàn cụm (chuẩn & có từ neo) | 2.000 | 32,0 / 0,3 / 39,2% | 41,3 / 3,6 / 43,3% (`0,06 ms`) | 73,1 / 47,4 / 74,4% (`1,85 ms`) | **88,9 / 79,8 / 89,0%** (`6,17 ms`) |
| **Tổng cộng** | **10.000** | **20,0 / 0,3 / 21,4%** | **64,0 / 27,3 / 67,2%** (`0,05 ms`) | **86,8 / 67,1 / 88,9%** (`0,42 ms`) | **95,1 / 89,4 / 95,5%** (**`6,96 ms`**) |

**Nhận xét:**
- So với câu gốc chưa xử lý, Factotum v1.1 nâng tỷ lệ phủ từ khóa thêm **`+75,1%`** (`20,0% → 95,1%`) và độ sát ngữ cảnh thêm **`+74,1%`** (`21,4% → 95,5%`).
- So với `Base-2g` (Bigram + Viterbi), Factotum vượt **`+8,2%` KW-Rec** và **`+22,3%` All-KW**, cách biệt lớn nhất nằm ở các nhóm khó: **thiếu từ ở giữa** (`80,5%` vs `14,0%` All-KW), **viết tắt toàn cụm** (`79,8%` vs `47,4%` All-KW nhờ cấu trúc MDIS) và **viết tắt hỗn hợp** (`89,3%` vs `77,5%` All-KW).
- Tốc độ trung vị đạt **`3,61 ms/câu`** (trung bình `6,96 ms/câu`, tốc độ xử lý `144 câu/giây`).

### 2.2. Kiểm thử tải 10.000 câu trên thuật ngữ đã học

Đo tốc độ và độ ổn định trên kho tri thức đầy đủ (`113.107` cụm, `3.399` từ đơn, thời gian nạp vào RAM `0,391 giây`):

| Nhóm câu hỏi | Số câu | Câu gốc (`KW / All / F1`) | Base-1g (`KW / All / F1`) | Base-2g (`KW / All / F1`) | Factotum v1.1 (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Không dấu | 5.000 | 10,0 / 0,3 / 9,9% | 77,5 / 52,4 / 78,8% (`0,04 ms`) | 99,5 / 99,1 / 99,6% (`0,05 ms`) | **99,8 / 99,6 / 99,8%** (`1,85 ms`) |
| Viết tắt hỗn hợp | 2.000 | 31,2 / 0,2 / 29,9% | 62,7 / 11,1 / 65,3% (`0,06 ms`) | 93,1 / 79,1 / 93,8% (`0,14 ms`) | **98,6 / 96,2 / 98,7%** (`10,00 ms`) |
| Viết tắt toàn cụm | 1.500 | 31,3 / 0,2 / 38,6% | 39,2 / 2,9 / 41,7% (`0,06 ms`) | 78,2 / 56,1 / 79,3% (`1,92 ms`) | **97,8 / 95,5 / 97,9%** (`7,41 ms`) |
| Thiếu 1 từ ở giữa | 1.500 | 7,8 / 0,0 / 8,4% | 62,4 / 7,6 / 70,3% (`0,06 ms`) | 73,3 / 12,9 / 81,3% (`0,06 ms`) | **97,8 / 93,3 / 98,2%** (`15,25 ms`) |
| **Tổng cộng** | **10.000** | **17,1 / 0,2 / 18,0%** | **66,5 / 30,0 / 69,3%** (`0,05 ms`) | **91,1 / 75,7 / 92,6%** (`0,35 ms`) | **98,9 / 97,4 / 99,1%** (**`6,33 ms`**) |

- Trung vị `3,05 ms/câu`, tốc độ **`158 câu/giây`**, vượt `Base-2g` **`+7,8%` KW-Rec** và **`+21,7%` All-KW**.
- Tổng số câu còn thiếu từ khóa trên toàn bộ 20.064 câu chỉ còn **`1.326` câu** (giảm **43,4%** so với `2.343` câu ở v1.0.1).

---

## 3. Bộ test 64 ca thực tế có từ khóa bắt buộc ([context_cases.json](context_cases.json))

![Kết quả trên các ca thực tế có từ khóa bắt buộc](../docs/images/context_cases_chart.png)

Bộ dữ liệu gồm 64 câu gõ thực tế có gán danh sách thực từ bắt buộc (danh từ, động từ, thuật ngữ chuyên ngành):

| Nhóm câu hỏi | Số câu | Câu gốc (`KW / All / F1`) | Base-1g (`KW / All / F1`) | Base-2g (`KW / All / F1`) | Factotum v1.1 (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Viết tắt cơ quan/tổ chức chuẩn (`canonical_acronym`) | 11 | 0,0 / 0,0 / 0,0% | 0,0 / 0,0 / 9,1% (`0,03 ms`) | 36,4 / 36,4 / 51,8% (`1,99 ms`) | **100,0 / 100,0 / 100,0%** (`0,13 ms`) |
| Giải nhập nhằng ngữ cảnh (`context_disambiguation`) | 10 | 51,7 / 10,0 / 75,7% | 46,7 / 20,0 / 78,7% (`0,25 ms`) | 70,0 / 50,0 / 92,2% (`0,69 ms`) | **100,0 / 100,0 / 100,0%** (`20,37 ms`) |
| Viết tắt kèm từ neo (`contextual_acronym`) | 5 | 13,3 / 0,0 / 13,3% | 33,3 / 0,0 / 48,4% (`0,11 ms`) | 66,7 / 0,0 / 74,6% (`2,32 ms`) | **100,0 / 100,0 / 100,0%** (`75,60 ms`) |
| Bổ sung từ rơi ở giữa (`missing_word_enrichment`) | 5 | 10,0 / 0,0 / 22,3% | 20,0 / 0,0 / 67,7% (`0,04 ms`) | 20,0 / 0,0 / 76,5% (`0,04 ms`) | **100,0 / 100,0 / 100,0%** (`6,94 ms`) |
| Nhiều thực thể viết tắt (`multi_entity_context`) | 10 | 15,0 / 0,0 / 54,4% | 15,0 / 0,0 / 49,6% (`0,12 ms`) | 53,3 / 20,0 / 79,8% (`4,28 ms`) | **95,0 / 90,0 / 97,8%** (`153,69 ms`) |
| Cụm câu trọn vẹn không dấu (`repaired_clean_phrases`) | 10 | 0,0 / 0,0 / 9,5% | 50,0 / 20,0 / 70,3% (`0,11 ms`) | 100,0 / 100,0 / 100,0% (`0,13 ms`) | **100,0 / 100,0 / 100,0%** (`41,79 ms`) |
| Lỗi chính tả & câu hỏi thực tế (`typo_and_user_query`) | 13 | 55,1 / 38,5 / 72,0% | 66,7 / 38,5 / 81,0% (`0,09 ms`) | 80,8 / 61,5 / 86,6% (`0,39 ms`) | **96,2 / 92,3 / 100,0%** (`17,36 ms`) |
| **Tổng cộng** | **64** | **23,4 / 9,4 / 39,2%** | **35,2 / 14,1 / 58,1%** (`0,11 ms`) | **64,3 / 45,3 / 80,8%** (`1,40 ms`) | **98,4 / 96,9 / 99,7%** (**`43,72 ms`**) |

**Nhận xét:**
- Factotum đạt **62/64 câu đủ 100% từ khóa bắt buộc** (`98,4% KW-Rec`, `99,7% Ctx-F1`, vượt `Base-2g` **`+34,1%` KW-Rec** và **`+51,6%` All-KW**).
- 5/7 nhóm đạt **100%** tuyệt đối.

---

## 4. Lịch sử cải tiến qua các phiên bản (v1.0.0 → v1.1.0 MDIS)

![Lịch sử cải tiến Factotum qua các phiên bản](../docs/images/version_progress.png)

| Phiên bản | Điểm nhấn kiến trúc | Held-out 10k (`KW / All / F1`) | Viết tắt toàn cụm Held-out (`KW / All`) | 64 ca thực tế (`KW / All`) | Stress Test 10k (`KW / All`) | Tổng câu lỗi / 20.064 câu |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **v1.0.0** | Đồ thị Markov 2 chiều + Reviewer 2 chặng | `80,0 / 67,2 / 80,8%` | `39,0 / 28,0%` | `97,7 / 95,3%` | `95,1 / 92,5%` | `3.558` |
| **v1.0.1** | Ngưỡng thích ứng không gán cứng + Beam Spell 2 chiều | `91,9 / 84,0 / 92,5%` | `73,2 / 52,8%` | `97,7 / 95,3%` | `96,2 / 92,6%` | `2.343` (`-34,1%`) |
| **v1.1.0** | **Cấu trúc vật lý MDIS (`+/-`, `<AUX>`, tọa độ 64-bit)** | **`95,1 / 89,4 / 95,5%`** | **`88,9 / 79,8%`** | **`98,4 / 96,9%`** | **`98,9 / 97,4%`** | **`1.326`** (**`-62,7%`**) |

---

## 5. Đối đầu trực tiếp với LLM (Gemini Hydrogen & Claude Sonnet 5.5, zero-shot)

![Đối đầu trực tiếp với LLM theo thước đo Làm rõ Ngữ cảnh](../docs/images/llm_comparison.png)

Rút `1.000` câu độc nhất từ 32 văn bản chưa từng học (`seed = 42`, chia đều 4 nhóm), lọc bỏ `107` cụm kết thúc cụt lủn theo `../output/dangling_end_words.txt` còn **`893` cụm trọn nghĩa**. Hai mô hình ngôn ngữ lớn chạy ở chế độ zero-shot trên cùng một prompt (trả lời đủ `1.000/1.000` câu), sau đó toàn bộ kết quả được chấm đồng nhất theo cùng 3 chỉ số `KW-Rec / All-KW / Ctx-F1`:

| Nhóm câu hỏi | Số câu | Câu gốc (`KW / All / F1`) | **Factotum (`KW / All / F1`)** | Gemini Hydrogen, zero-shot (`KW / All / F1`) | Claude Sonnet 5.5, zero-shot (`KW / All / F1`) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Không dấu mới hoàn toàn | 227 | 9,7 / 0,4 / 9,0% | **96,9 / 91,2 / 96,6%** | 93,9 / 85,9 / 93,8% | 92,5 / 80,2 / 92,8% |
| Viết tắt hỗn hợp | 219 | 31,3 / 1,4 / 28,4% | 88,5 / 70,8 / 88,2% | 88,7 / 69,9 / 88,7% | **89,0 / 72,6 / 89,1%** |
| Thiếu 1 từ ở giữa | 227 | 4,4 / 0,4 / 4,6% | **74,4 / 47,1 / 78,4%** | 73,5 / 44,9 / 76,0% | 72,9 / 46,7 / 72,9% |
| Viết tắt toàn cụm | 220 | 0,0 / 0,0 / 0,0% | **38,6 / 27,3 / 38,2%** | 27,8 / 10,9 / 26,4% | 20,3 / 5,5 / 18,4% |
| **Tổng cộng (cụm trọn nghĩa)** | **893** | **11,3 / 0,6 / 10,4%** | **74,7 / 59,2 / 75,5%** | **71,2 / 53,1 / 71,4%** | **68,9 / 51,4 / 68,5%** |
| **Thời gian chạy 1.000 câu** | | | **`9,39 giây`** (cục bộ, `1,29 MB`) | `~15 giây` (batch cloud) | `~20 phút` (cloud) |

*Lưu ý đối chiếu công bằng:* Hai LLM chạy zero-shot (không RAG, không tinh chỉnh trên 124 văn bản luật), trong khi Factotum đã học trực tiếp từ 124 văn bản cùng lĩnh vực.

---

## 6. Phân tích Điểm tin cậy & So sánh tổng hợp

![Phân tích điểm tin cậy và tổng hợp 3 bộ kiểm thử](../docs/images/confidence_calibration.png)

| Mức điểm tin cậy (`confidence`) | Số câu (Held-out) | `confidence` TB | `KW-Rec` | `All-KW` | `Ctx-F1` | Khuyến nghị ứng dụng |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `90 – 100%` | 7.050 | 98,2% | **97,6%** | **95,0%** | **97,9%** | Tự động áp dụng |
| `70 – 90%` | 1.742 | 78,2% | 94,2% | 87,3% | 94,4% | Tự động áp dụng |
| `50 – 70%` | 665 | 59,1% | 90,6% | 78,9% | 90,4% | Hiển thị gợi ý |
| `30 – 50%` | 333 | 44,2% | 74,4% | 44,1% | 76,9% | Hiển thị gợi ý |
| `< 30%` | 210 | 9,4% | 63,2% | 23,3% | 68,2% | Giữ nguyên câu gốc |

Đường cong hiệu chuẩn đơn điệu hoàn hảo: ở nhóm `confidence ≥ 90%` (`7.050` câu, chiếm `70,5%` tập Held-out), tỷ lệ đủ 100% từ khóa đạt **`95,0%`** (`97,9%` Ctx-F1); ngược lại ở nhóm `< 30%` (`210` câu) giảm mạnh còn `23,3%`, giúp ứng dụng dễ dàng đặt ngưỡng an toàn.

---

## 7. Tỷ lệ tăng trưởng dữ liệu theo văn bản đầu vào

![Đường cong tăng trưởng dữ liệu Factotum](../docs/images/scaling_curve.png)

| Số văn bản | Số từ đầu vào (`N`) | Dung lượng văn bản thô | File tri thức `.txt` (chưa nén) | Số từ lưu trong kho cụm | File đóng gói `.ftm` |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 10 | 365.615 từ | 2,13 MB | 8,57 MB (**402%**) | 232.179 từ (**63,5%**) | 434,9 KB (**20,0%**) |
| 30 | 860.123 từ | 5,01 MB | 13,58 MB (**271%**) | 400.387 từ (**46,5%**) | 707,4 KB (**13,8%**) |
| 80 | 1.218.295 từ | 7,10 MB | 15,04 MB (**212%**) | 455.391 từ (**37,4%**) | 796,4 KB (**11,0%**) |
| 156 | 2.618.007 từ | 15,21 MB | 24,51 MB (**161%**) | 775.032 từ (**29,6%**) | 1,29 MB (**8,5%**, nén `11,8 : 1`) |

Khi số văn bản tăng gần 16 lần (`10 → 156` văn bản), kích thước file `.ftm` chỉ tăng khoảng 3 lần (`434,9 KB → 1,29 MB`, tỷ lệ tăng trưởng dưới tuyến tính $S_{\text{ftm}} \propto N^{0.56}$). Ngoài ra, cấu trúc vật lý MDIS nén (`.mdis`) cho toàn bộ 156 văn bản có dung lượng `4,87 MB` (`3.399` thực thể, `1` `<AUX>`, `110.804` chiều `+`, `2.959` chiều `-`, `664.884` giao điểm 2 phía) và tăng tốc tra cứu cụm con không khớp chính xác gấp **2,65×**.

---

## 8. So sánh tổng quan với các phương pháp khác

Số liệu `Base-1g`, `Base-2g` và `Factotum v1.1` lấy từ Held-out 10.000 câu và bộ `context_cases.json`; cột `LLM (zero-shot)` tham chiếu kết quả đo trực tiếp trên `893` cụm trọn nghĩa ở mục 5:

| Tiêu chí | `Base-1g` (Unigram) | `Base-2g` (Bigram + Viterbi) | **Factotum v1.1 (MDIS)** | SymSpell / Edit Distance* | LLM (zero-shot) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Làm rõ ngữ cảnh (Held-out 10.000 câu) | `64,0%` KW-Rec | `86,8%` KW-Rec (`67,1%` All-KW) | **`95,1%` KW-Rec (`89,4%` All-KW)** | Không hỗ trợ viết tắt/dấu | `68,9% – 71,2%` KW-Rec (mục 5) |
| Giải nhập nhằng & nhiều thực thể (64 ca) | `35,2%` KW-Rec | `64,3%` KW-Rec (`45,3%` All-KW) | **`98,4%` KW-Rec (`96,9%` All-KW)** | Không | Cao* |
| Viết tắt toàn cụm (Held-out 2.000 câu) | `41,3%` KW-Rec | `73,1%` KW-Rec (`47,4%` All-KW) | **`88,9%` KW-Rec (`79,8%` All-KW)** | Không | `20,3% – 27,8%` KW-Rec (mục 5) |
| Bổ sung từ rơi ở giữa (Held-out 2.000 câu) | `8,8%` All-KW | `14,0%` All-KW | **`80,5%` All-KW (`94,5%` Ctx-F1)** | Không | `44,9% – 46,7%` All-KW (mục 5) |
| Sửa lỗi chính tả & gõ Telex | Không | Không | Có, giữ nguyên từ đúng | Tốt (từng từ đơn) | Tốt |
| Tự học từ dữ liệu nội bộ | Nhanh | Nhanh | `~28 giây` (bản C++) | Nhanh | Chậm, thường cần GPU |
| Dung lượng lưu trữ / RAM | Vài MB | Vài MB | `1,29 MB` `.ftm` / `~180–220 MB` RAM | Rất nhỏ | `400 MB` – hàng chục GB* |
| Tốc độ xử lý (trung vị) | `0,05 ms/câu` | `0,35 ms/câu` | `3,05 – 3,61 ms/câu` | `< 1 ms/câu`* | `~15 ms` (batch) – `1,2 s/câu` |

*\*Khoảng tham khảo định tính tùy kiến trúc mô hình và phần cứng.*

---

## 9. Giới hạn của bộ kiểm thử

- Các văn bản quy phạm pháp luật có độ lặp mẫu câu hành chính cao, nên khi tách theo file vẫn có thể còn trùng lặp một phần cấu trúc câu; chưa đánh giá chéo sang lĩnh vực khác (như y tế, tài chính).
- Hai bộ 10.000 câu được trích xuất tự động từ các cụm trọn nghĩa trong văn bản thực tế; riêng bộ 64 câu [context_cases.json](context_cases.json) được thiết kế thủ công cho các tình huống nhập nhằng, viết tắt kèm từ neo và câu hỏi thực tế.
