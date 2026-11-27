# Thư mục `output/` — Kho Tri Thức & Cấu Hình Factotum

Thư mục này lưu trữ toàn bộ **file tri thức thuần văn bản**, **file cấu hình tùy chỉnh hậu kiểm**, **file mô hình đóng gói `.ftm`** và **bộ nhớ đệm cục bộ** của Factotum.

---

## Phân loại nhanh mức độ can thiệp

| File | Mức độ can thiệp | Có đẩy lên GitHub? | Tóm tắt vai trò |
| :--- | :--- | :---: | :--- |
| `dangling_end_words.txt` | **Sinh ra để người dùng customize trực tiếp** | Có | Danh sách từ cấm đứng cuối câu (tự động đọc nóng, không cần rebuild) |
| `KnowledgeBase.txt` | **Có thể chỉnh sửa tri thức** *(chạy lại `[4]` sau khi sửa)* | Không *(sinh cục bộ)* | Kho cụm từ lặp tối đại gốc — nguồn để dựng lại toàn bộ đồ thị & chỉ mục |
| `Data.txt` | **Hạn chế sửa tay** *(file dẫn xuất tự động)* | Không *(sinh cục bộ)* | Bảng cạnh chuyển tiếp Markov $P(v \mid u)$ sinh tự động từ `KnowledgeBase.txt` |
| `UnaccentedMap.txt` | **Hạn chế sửa tay** *(file dẫn xuất tự động)* | Không *(sinh cục bộ)* | Bảng ánh xạ từ đơn không dấu / chữ cái đầu sang danh sách từ có dấu |
| `factotum_law.ftm` | **Không sửa tay** *(file đóng gói cho máy đọc)* | Có | Bản đóng gói gọn (`JSON + zlib`) an toàn để chia sẻ & triển khai |
| `factotum_law.mdis` | **Không sửa tay** *(xuất qua `save_mdis()`)* | Không *(sinh cục bộ)* | Kho tri thức vật lý MDIS nén (`JSON + zlib`): thực thể, chiều `+`/`-`, giao điểm 2 phía & `<AUX>` |
| `MDIS.txt` | **Chỉ đọc / kiểm tra** *(xuất qua `export_mdis_txt()`)* | Không *(sinh cục bộ)* | Bản văn bản thuần UTF-8 của cấu trúc MDIS (`[ENTITIES]`, `[DIMENSIONS_PLUS]`, `[DIMENSIONS_MINUS]`) |
| `model_cache.pkl` | **Không sửa tay** *(có thể xóa để tạo lại)* | Không *(cache cục bộ)* | Bộ nhớ đệm nhị phân (`pickle`) giúp khởi động nạp RAM nhanh hơn |

> [!TIP]
> Trên GitHub, `.gitignore` chỉ theo dõi `README.md`, `dangling_end_words.txt` và `factotum_law.ftm`. Các file văn bản lớn (`KnowledgeBase.txt`, `Data.txt`, `UnaccentedMap.txt`) và cache cục bộ (`model_cache.pkl`) được tạo tự động khi chạy bộ nạp dữ liệu (`src/app.py`).

---

## Chi tiết công dụng & hướng dẫn từng file

### 1. Nhóm sinh ra để người dùng customize theo ý mình

#### `dangling_end_words.txt`
- **Công dụng:** Chứa danh sách các hư từ, giới từ, liên từ (như `của`, `cho`, `về`, `với`, `thì`, `và`, `hoặc`, `nhưng`, `các`, `là`...) mà một câu tiếng Việt hoàn chỉnh không nên kết thúc cụt lủn bằng chúng. Tầng hậu kiểm (`is_dangling_phrase()` trong `reviewer.py`) dùng file này để loại bỏ các ứng viên kết thúc lơ lửng nghĩa.
- **Cách customize:**
  - Mở trực tiếp bằng bất kỳ trình soạn thảo văn bản nào (Notepad, VS Code...), mỗi dòng ghi **1 từ chữ thường** (UTF-8).
  - **Hiệu lực nóng (hot-reload):** Hệ thống tự kiểm tra thay đổi file định kỳ (~2 giây/lần), nên bạn chỉ cần lưu file là có tác dụng ngay mà **không cần rebuild kho tri thức hay khởi động lại chương trình**.

---

### 2. Nhóm người dùng có thể chỉnh sửa tri thức (cần đồng bộ lại)

#### `KnowledgeBase.txt`
- **Công dụng:** File nguồn tri thức trung tâm do bước trích xuất Suffix Array + LCP (`MakeTree`) sinh ra, gồm hai phần:
  - Các dòng `A,<cụm từ>,<tần suất>`: lưu các cụm từ lặp tối đại kèm tần suất xuất hiện (vd: `A,tai nạn giao thông,42`). Đây là dữ liệu nền để dựng 4 bảng chỉ mục ngược trên RAM (`word_to_phrases`, `acronym_to_pids`, `ua_phrase_to_pids`, `initial_pair_to_pids`).
  - Các dòng `AB,<từ/cụm A>,<từ/cụm B>,<tần suất>`: lưu các cạnh chuyển tiếp phân rã từ cụm.
- **Cách chỉnh sửa an toàn:**
  - Bạn có thể thoải mái **thêm cụm chuyên ngành mới, xóa cụm rác hoặc chỉnh tần suất** ở các dòng bắt đầu bằng `A,` (giữ đúng định dạng `A,<cụm chữ thường>,<số nguyên dương>`). Không cần sửa tay các dòng `AB,`.
  - Sau khi sửa xong, chạy `run.bat` (hoặc `python src/app.py`) và chọn **`[4] Cập nhật lại đồ thị & .ftm từ KnowledgeBase.txt`** (`make_tree.rebuild_from_kb()`). Lệnh này sẽ tự động:
    1. Dựng lại toàn bộ cạnh `AB,` trong `KnowledgeBase.txt`,
    2. Cập nhật lại `Data.txt` và `UnaccentedMap.txt`,
    3. Xóa `model_cache.pkl` cũ để tránh lệch dữ liệu,
    4. Đóng gói lại `factotum_law.ftm`.

---

### 3. Nhóm hạn chế sửa tay (file sinh tự động từ `KnowledgeBase.txt`)

#### `Data.txt`
- **Công dụng:** Bảng cạnh đồ thị Markov có hướng theo định dạng CSV `Word_A,Word_B,Freq,Probability` (vd: `<code>,ngày,804,0.10673039`). Cung cấp trọng số xác suất chuyển tiếp $P(v \mid u)$ cho quy hoạch động Top-K Viterbi, kiểm tra cạnh đứt/yếu khi chèn từ thiếu và chấm điểm Neo ngữ cảnh tầng 4.
- **Vì sao hạn chế sửa tay:**
  - Mọi thay đổi thủ công sẽ bị **ghi đè hoàn toàn** ở lần chạy `MakeTree` hoặc menu `[4]` kế tiếp.
  - Sửa rời rạc tần suất/xác suất dễ làm lệch chuẩn hóa $\sum_v P(v \mid u) = 1$ và mất đồng bộ với `KnowledgeBase.txt`.

#### `UnaccentedMap.txt`
- **Công dụng:** Bảng ánh xạ khóa của **từng từ đơn** (từ không dấu, chữ cái đầu, phụ âm ghép đầu như `ng`/`kh`/`nh`, cặp đầu+cuối) sang danh sách từ tiếng Việt có dấu hợp lệ theo định dạng `khóa: từ_1, từ_2, ...` (vd: `an: án, an, ăn, ấn, ẩn, ân`). Dùng trực tiếp khi tách chuỗi viết tắt dính liền (`split_sticky_token()`) và bung cột ứng viên.
- **Vì sao hạn chế sửa tay:**
  - File này được sinh tự động từ từ vựng của `KnowledgeBase.txt` qua hàm `generate_unaccented_keys()`.
  - Nếu bạn thêm từ mới bằng cách sửa tay ở đây mà từ đó chưa có trong `KnowledgeBase.txt`, lần rebuild kế tiếp thay đổi sẽ bị mất. Chỉ nên sửa tay tạm thời khi thử nghiệm ánh xạ viết tắt đặc thù.

---

### 4. Nhóm không chỉnh sửa tay (file nhị phân / đóng gói cho máy đọc)

#### `factotum_law.ftm`
- **Công dụng:** File mô hình đóng gói độc lập (`JSON` nén `zlib` với header `FTM1`) chứa dữ liệu lõi (`phrases`, `fwd_graph`, `unaccented_map`). An toàn khi chia sẻ công khai (không dùng `pickle`), được nạp bằng `GraphIndex.load_standalone()`.
- **Lưu ý:** Không mở sửa trực tiếp bằng trình soạn thảo văn bản. Muốn cập nhật nội dung, hãy chỉnh `KnowledgeBase.txt` rồi chạy menu `[4]`.

#### `model_cache.pkl`
- **Công dụng:** File cache nhị phân cục bộ (`pickle`) lưu sẵn toàn bộ chỉ mục ngược và đồ thị Markov đã dựng trên RAM để lần khởi chạy sau không phải đọc lại 3 file `.txt` lớn.
- **Lưu ý:** Không mở sửa tay. Bạn có thể **xóa thoải mái** file này bất cứ lúc nào nếu nghi ngờ cache cũ — Factotum sẽ tự động dựng lại ở lần khởi chạy tiếp theo.

---

### Thư mục trung gian khác (nếu có khi nạp tài liệu mới)
Khi chạy chức năng `[1] Thêm dữ liệu mới (.doc / .docx)`, pipeline sẽ tạo thêm các thư mục phụ trợ cục bộ như `Log/`, `Pure_Log/` và `Processed_Log/` để ghi nhận tiến trình trích xuất và chống trùng lặp tài liệu. Người dùng không cần chỉnh sửa các thư mục này.
