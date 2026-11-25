<p align="center">
  <img src="docs/images/factotum_logo.png" alt="Factotum Logo" width="160" />
</p>

<h1 align="center">Factotum</h1>

<p align="center">
  <strong>Biến dòng tiếng Việt gõ nhanh, gõ tắt, bỏ dấu thành câu chuẩn xác — chỉ trong vài mili-giây, ngay trên máy tính thường.</strong>
</p>

<p align="center">
  <a href="https://huggingface.co/spaces/evilogre1112/factotum"><img src="https://img.shields.io/badge/Dùng%20thử%20ngay-Hugging%20Face%20Spaces-FFD21E?logo=huggingface&logoColor=black" alt="Live Demo on Hugging Face Spaces" /></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/thư%20viện%20ngoài-0-2EA043" alt="Zero Dependencies" />
  <img src="https://img.shields.io/badge/chạy%20offline-CPU%20thường-orange" alt="CPU only" />
  <img src="https://img.shields.io/badge/release-v1.1.0-blue" alt="Release v1.1.0" />
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT License" />
</p>

> **Trải nghiệm ngay không cần cài đặt:** mở **[Demo trực tuyến trên trình duyệt](https://huggingface.co/spaces/evilogre1112/factotum)** (chạy hoàn toàn trên trình duyệt của bạn, không gửi dữ liệu ra máy chủ).

---

## Bạn gõ vội — Factotum hiểu đúng ngữ cảnh

Hằng ngày chúng ta thường gõ tắt (`tngt`, `ubnd`, `xpvphc`), bỏ dấu (`xu phat vi pham`), rớt chữ (`an toan thong`) hoặc dính lỗi bộ gõ Telex (`buowcs`, `phappp`). **Factotum** tự động khôi phục câu chuẩn mà không cần mạng Internet, không cần GPU và không tốn phí gọi API:

| Tình huống thực tế | Bạn gõ | Factotum trả về |
| :--- | :--- | :--- |
| **Gõ không dấu** | `xu phat vi pham hanh chinh` | `xử phạt vi phạm hành chính` |
| **Viết tắt liên tiếp** | `xpvphc atgt` | `xử phạt vi phạm hành chính an toàn giao thông` |
| **Tự hiểu viết tắt theo ngữ cảnh** | `nd 23/24 quy định điều gì` | `nghị định 23/24 quy định điều gì` |
| **Cùng từ `nd`, khác ngữ cảnh** | `tòa án nd tối cao` | `tòa án nhân dân tối cao` |
| **Phân biệt nghĩa không dấu** | `co the bị xử phạt` / `xâm phạm co the người khác` | `có thể bị xử phạt` / `xâm phạm cơ thể người khác` |
| **Rơi mất từ ở giữa** | `an toan thong` | `an toàn giao thông` |
| **Lỗi gõ Telex & lặp phím** | `buowcs`, `vi pham phappp luat` | `bước`, `vi phạm pháp luật` |
| **Sai chính tả trên câu có dấu** | `uống dượu bia phạt bao nhiêu` | `uống rượu bia phạt bao nhiêu` |
| **Giữ nguyên câu đã gõ đúng** | `chạy xe vượt đèn đỏ bị phạt bao nhiêu tiền` | Giữ nguyên không sửa bừa |

---

## Tại sao nên chọn Factotum?

- **Siêu nhẹ & 100% Offline:** Chỉ cần Python 3.10+ trên CPU thông thường, không cần GPU, không cài thêm thư viện ngoài. Toàn bộ kho tri thức đóng gói gọn trong một file `.ftm` nhẹ (~1,3 MB).
- **Bảo mật tuyệt đối dữ liệu nội bộ:** Mọi quá trình học tài liệu và xử lý câu chữ đều diễn ra cục bộ trên máy hoặc máy chủ riêng của bạn — không gửi văn bản nhạy cảm ra cloud bên thứ ba.
- **Tốc độ gần như tức thì:** Phản hồi chỉ vài mili-giây mỗi câu, đủ nhanh để gắn thẳng vào thanh tìm kiếm hoặc gợi ý ngay trong lúc người dùng đang gõ.
- **Tự học từ chính tài liệu của bạn:** Đưa thẳng thư mục file Word (`.doc`, `.docx`) hoặc `.txt` của công ty/ngành nghề vào — Factotum tự rút ra bộ thuật ngữ chuyên ngành mà không cần gán nhãn thủ công.
- **Minh bạch, không phải "hộp đen":** Không "ảo giác" bịa câu như mô hình AI lớn. Nếu câu quá mơ hồ, Factotum chủ động giữ nguyên câu gốc thay vì đoán bừa; bạn cũng có thể mở các file tri thức thuần văn bản ra xem và tự chỉnh sửa theo ý muốn.

---

## Các trường hợp ứng dụng thực tế & Cách áp dụng

Factotum phát huy sức mạnh lớn nhất khi đứng làm **lớp làm sạch và chuẩn hóa tiếng Việt** cho các lĩnh vực có bộ thuật ngữ lặp lại ổn định:

```text
Người dùng gõ nhanh: "nd 23/24 quy định điều gì"
           ↓
     [ Factotum ]        Chuẩn hóa trong vài ms (100% offline)
           ↓
"nghị định 23/24 quy định điều gì"
           ↓
  [ Thanh tìm kiếm / Chatbot / Form nhập liệu ] → Kết quả chính xác hơn
```

| Lĩnh vực áp dụng | Bài toán thực tế | Cách áp dụng Factotum |
| :--- | :--- | :--- |
| **Pháp lý – Hành chính công** | Chuyên viên, người dân tra cứu luật hay gõ tắt `tngt`, `atgt`, `xpvphc`, `tòa án nd tối cao`. | Dùng ngay file `output/factotum_law.ftm` có sẵn đặt trước ô tìm kiếm văn bản luật hoặc công cụ tốc ký. |
| **Y tế – Bệnh án – Dược** | Bác sĩ, điều dưỡng ghi chú nhanh tên thuốc, chẩn đoán viết tắt; yêu cầu bảo mật hồ sơ bệnh nhân. | Chạy `fit_folder()` trên kho bệnh án mẫu / danh mục thuốc nội bộ (`.docx`/`.txt`) để tạo file `.ftm` chạy hoàn toàn offline trong mạng nội bộ bệnh viện. |
| **CSKH – Live Chat – Tìm kiếm sản phẩm** | Khách hàng nhắn không dấu, rơi chữ, sai chính tả khiến công cụ tìm kiếm (Elasticsearch/FAQ) không ra kết quả. | Gắn `model.restore(query)` trước bước tìm kiếm hoặc trước chatbot để tăng tỷ lệ bắt đúng ý định mà không tốn chi phí gọi LLM cho từng tin nhắn. |
| **Kế toán – Kho vận – Doanh nghiệp** | Nhân viên nhập liệu biên bản, mã vật tư, quy trình nội bộ với nhiều cụm từ lặp lại đặc thù của công ty. | Nạp tài liệu quy trình nội bộ để Factotum tự học thuật ngữ riêng; khi có tài liệu mới chỉ cần gọi `update_folder()` để học bổ sung. |

![Minh họa cách Factotum tổ chức tri thức](docs/images/knowledge_graph_flower.png)

---

## Bắt đầu nhanh trong 1 phút

### 1. Cài đặt

```bash
git clone https://github.com/evilogre1112/Factotum.git
cd Factotum
pip install -e .
```

### 2. Khôi phục ngay với dữ liệu pháp luật có sẵn

```python
from factotum import Factotum

model = Factotum.load("output/factotum_law.ftm")

print(model.restore("xpvphc atgt"))                # xử phạt vi phạm hành chính an toàn giao thông
print(model.restore("nd 23/24 quy định điều gì"))  # nghị định 23/24 quy định điều gì
print(model.restore("tòa án nd tối cao"))          # tòa án nhân dân tối cao
print(model.restore("an toan thong"))              # an toàn giao thông
```

### 3. Tự dạy Factotum học tài liệu của riêng bạn

Factotum đọc trực tiếp file `.txt`, `.docx` và `.doc` (không cần cài thêm thư viện ngoài):

```python
from factotum import Factotum

# Học từ cả thư mục tài liệu của bạn
model = Factotum().fit_folder("duong_dan/thu_muc_tai_lieu")

# Lưu thành 1 file .ftm nhỏ gọn để mang đi chạy bất cứ đâu
model.save("my_company.ftm")

# Sau này có tài liệu mới, chỉ cần học bổ sung:
# model.update_folder("duong_dan/tai_lieu_moi")
```

### 4. Mở giao diện dùng thử

```bash
python src/app.py                         # Giao diện dòng lệnh (nạp dữ liệu, test truy vấn, cập nhật KB)
pip install gradio && python demo/app.py  # Giao diện web trực quan tại http://127.0.0.1:7860
```

---

## Cấu hình & Tùy chỉnh theo nhu cầu

### 1. Dùng điểm tin cậy (`confidence`) để quyết định tự sửa hay gợi ý

Khi tích hợp Factotum vào ứng dụng thực tế, bạn có thể lấy Top-K gợi ý kèm điểm tin cậy (`0% – 100%`) và cờ `is_fallback`:

```python
results = model.predict("quyền sử dd", top_k=3)
best = results[0]

if not best.is_fallback and best.confidence >= 0.70:
    # Độ tin cậy cao → có thể tự động áp dụng
    text = best.phrase
elif not best.is_fallback and best.confidence >= 0.50:
    # Độ tin cậy trung bình → hiển thị dạng gợi ý cho người dùng chọn
    suggestions = [r.phrase for r in results]
else:
    # Chưa đủ chắc chắn → giữ nguyên câu người dùng nhập
    text = "quyền sử dd"
```

### 2. Tùy chỉnh danh sách từ không được đứng cuối câu

Mở trực tiếp file [output/dangling_end_words.txt](output/dangling_end_words.txt) để thêm hoặc bớt các hư từ, liên từ (`của`, `cho`, `về`, `thì`, `và`...). Nếu gợi ý kết thúc cụt nghĩa bởi các từ này, Factotum sẽ tự động loại bỏ. Thay đổi có hiệu lực ngay sau ~2 giây mà **không cần khởi động lại hay huấn luyện lại**.

### 3. Quản lý & Chỉnh sửa kho tri thức

Xem hướng dẫn chi tiết file nào được sửa tự do, file nào nên chạy đồng bộ lại và file nào hạn chế đụng vào tại **[output/README.md](output/README.md)**.

<details>
<summary><strong>Tùy chọn tăng tốc học dữ liệu trên Linux / macOS</strong> (Windows đã có sẵn)</summary>

Trên Windows, bản tăng tốc C++ đã được đóng gói sẵn. Trên Linux/macOS, Factotum vẫn chạy bình thường bằng Python thuần; nếu bạn muốn tăng tốc bước học tài liệu lên gấp ~3 lần, chỉ cần biên dịch thêm 1 lệnh:

```bash
# Linux
g++ -O3 -std=c++17 -shared -fPIC src/factotum/builder/native/sa_lcp.cpp -o src/factotum/builder/native/libsa_lcp.so

# macOS
g++ -O3 -std=c++17 -shared -fPIC src/factotum/builder/native/sa_lcp.cpp -o src/factotum/builder/native/libsa_lcp.dylib
```

</details>

---

## Điểm yếu & Giới hạn hiện tại

Để bạn cân nhắc đúng trước khi áp dụng vào dự án, dưới đây là các giới hạn trung thực của Factotum:

- **Phụ thuộc vào bộ tài liệu đã học:** Factotum chỉ thực sự mạnh trong đúng lĩnh vực có thuật ngữ lặp lại ổn định mà bạn đã nạp. Từ quá hiếm, từ hoàn toàn mới (chưa từng xuất hiện trong tài liệu) hoặc ngôn ngữ mạng xã hội / tiếng lóng đa chủ đề có thể bị đoán lệch sang cụm phổ biến hơn.
- **Không hiểu ngữ nghĩa sâu ở cấp độ cả đoạn văn dài:** Factotum không dùng mạng nơ-ron hay LLM, nên không thể suy luận ý định phức tạp, tóm tắt hay viết lại câu theo văn phong khác.
- **Hạn chế tự nhiên với viết tắt quá ngắn không có ngữ cảnh:** Một chuỗi 3–4 chữ cái đứng một mình có thể hợp lệ theo nhiều nghĩa khác nhau (ví dụ `cnt` có thể là `cho nhà thầu` hoặc `của nhà thầu`). Trong trường hợp này nên dùng Factotum kèm ngữ cảnh xung quanh hoặc hiển thị Top-3 gợi ý.
- **Hạn chế khi thiếu nhiều từ liên tiếp hoặc sai chính tả quá nặng:** Cơ chế chèn từ hiện tối ưu cho trường hợp rơi 1 từ ở giữa cụm; nếu câu đầu vào vừa thiếu nhiều từ vừa sai chính tả nặng cùng lúc, Factotum sẽ ưu tiên giữ nguyên câu gốc để tránh sửa sai.

---

## Báo cáo Benchmark & Tài liệu chuyên sâu

Toàn bộ số liệu đánh giá độc lập, biểu đồ và đối chiếu với các mô hình ngôn ngữ lớn (LLM) được tách riêng để giữ trang giới thiệu gọn gàng:

- **[Báo cáo Benchmark & So sánh LLM chi tiết](benchmark/REPORT.md)** — Kết quả Held-out 10.000 câu, đối đầu zero-shot với Gemini & Claude, phân tích Confidence Calibration, Ablation Study và đường cong tăng trưởng dữ liệu.
- **[Phương pháp & Hướng dẫn tự chạy Benchmark](benchmark/README.md)** — Cách tạo tập test và lệnh tái lập kết quả (`python benchmark/run_benchmark.py 10000`).
- **[Giải thích các file tri thức & Cách tùy chỉnh](output/README.md)** — Chi tiết từng file trong thư mục `output/`.
- **[Hướng dẫn đóng gói Demo Web](demo/README.md)** — Tự triển khai demo trình duyệt trên Hugging Face Spaces hoặc Gradio.
- **[Đóng góp](CONTRIBUTING.md)** • **[Lịch sử phiên bản](CHANGELOG.md)** • **[Trích dẫn (`CITATION.cff`)](CITATION.cff)**

## Giấy phép

MIT License.
