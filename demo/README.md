# Demo trực tuyến (Gradio / Hugging Face Spaces)

## Chạy cục bộ

```bash
pip install gradio
python demo/app.py        # mở http://127.0.0.1:7860
```

## Đưa lên Hugging Face Spaces (miễn phí, CPU basic)

1. Tạo Space mới tại https://huggingface.co/new-space (SDK: **Gradio**, phần cứng: **CPU basic**).
2. Đóng gói thư mục Space:

   ```bash
   python demo/build_space.py      # tạo dist/hf_space/ (~1,5 MB)
   ```

3. Đẩy lên Space (thay `<user>`):

   ```bash
   cd dist/hf_space
   git init -b main
   git add .
   git commit -m "Factotum demo"
   git remote add origin https://huggingface.co/spaces/<user>/factotum
   git push -u origin main
   ```

4. Sau khi Space chạy, dán link vào phần **Thử ngay** trong README chính và vào mục *Website* của repo GitHub.

Bản Space không kèm thư viện C++ (`sa_lcp`) vì demo chỉ nạp mô hình `.ftm` có sẵn, không cần huấn luyện.
