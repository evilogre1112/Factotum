# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), đánh số phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [1.0.0] - 2026-11-23

Bản phát hành ổn định đầu tiên.

### Tính năng

- Khôi phục dấu tiếng Việt, viết tắt chữ cái đầu (`xpvphc atgt`), viết tắt theo ngữ cảnh (`nd` → `nghị định` / `nhân dân`), chèn từ bị thiếu, sửa lỗi chính tả trên câu có dấu, giải mã Telex và ký tự lặp phím.
- Tự học từ tài liệu riêng (`.txt`, `.doc`, `.docx`) bằng Suffix Array + Maximal Repeats + đồ thị Markov 2 chiều; lõi C++ tùy chọn cho bước trích xuất.
- File mô hình `.ftm` an toàn (JSON nén `zlib`, không `pickle`); mô hình pháp luật mẫu `1,29 MB` học từ 156 văn bản công khai.
- Điểm tin cậy `confidence` đã hiệu chuẩn; tầng hậu kiểm giữ nguyên câu gốc (`is_fallback=True`) khi không có gợi ý trọn nghĩa.
- Danh sách từ không được đứng cuối câu do người dùng tự chỉnh (`output/dangling_end_words.txt`).
- Giao diện dòng lệnh (`python src/app.py`) và demo web Gradio (`python demo/app.py`).
