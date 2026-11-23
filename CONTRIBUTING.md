# Đóng góp cho Factotum

Cảm ơn bạn đã quan tâm! Mọi đóng góp đều được hoan nghênh, đặc biệt là **báo lỗi kèm câu nhập thật**: đó là dữ liệu quý nhất để cải thiện mô hình.

## Báo lỗi dự đoán sai

Mở [Issue](https://github.com/evilogre1112/Factotum/issues/new) và ghi:

- Câu nhập (nguyên văn bạn gõ)
- Kết quả Factotum trả về (kèm `confidence` nếu có)
- Kết quả bạn mong muốn
- Mô hình đang dùng (`output/factotum_law.ftm` hay mô hình tự huấn luyện)

## Chạy dự án cục bộ

```bash
git clone https://github.com/evilogre1112/Factotum.git
cd Factotum
pip install -e .
python -m unittest discover -s tests -v   # 23 bài kiểm tra, ~6 giây
```

## Gửi Pull Request

1. Tạo nhánh mới từ `main`.
2. Nếu sửa logic xếp hạng/chấm điểm, thêm câu tái hiện lỗi vào `tests/test_legal_regression.py` (hoặc `tests/test_factotum.py` nếu không phụ thuộc miền luật).
3. Đảm bảo toàn bộ test vẫn đạt, sau đó chạy `python benchmark/run_benchmark.py 10000` và ghi chênh lệch Top-1 / Top-3 / Phrase validity vào mô tả PR.
4. Không thêm thư viện ngoài vào lõi `src/factotum/` (dự án cố ý chỉ dùng thư viện chuẩn Python).

## Quy tắc thiết kế

- Không hardcode từ vựng của một miền hay một ngôn ngữ cụ thể trong engine; mọi quyết định phải dựa trên đồ thị và KB học từ dữ liệu.
- Câu đã đúng chính tả và có thật trong KB thì không được sửa.
- Khi không đủ bằng chứng, giữ nguyên câu gốc thay vì đoán bừa.

## Ý tưởng cần người đóng góp

- Bộ câu hỏi gõ thật của người dùng (thay cho câu sinh tự động) để đo lại benchmark.
- Mô hình `.ftm` cho miền khác (y tế, kế toán, giáo dục) và phép thử cross-domain.
- Gói PyPI và bản build C++ cho Linux/macOS qua GitHub Actions.
