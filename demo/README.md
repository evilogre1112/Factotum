# Demo trực tuyến (WebAssembly / Gradio)

## 1. Dùng thử trực tuyến ngay

Bản demo chạy 100% trên trình duyệt qua **Pyodide (Python WebAssembly)**, không cần cài đặt hay server backend:

- **Live Demo:** https://huggingface.co/spaces/evilogre1112/factotum

## 2. Chạy cục bộ

- **Bản Gradio:**
  ```bash
  pip install gradio
  python demo/app.py        # mở http://127.0.0.1:7860
  ```
- **Bản Static WebAssembly:**
  ```bash
  python demo/build_space.py
  python -m http.server 8000 --directory dist/hf_space
  # mở http://127.0.0.1:8000
  ```

## 3. Tự triển khai lên Hugging Face Static Space (miễn phí)

1. Đóng gói thư mục tĩnh (`~1,42 MB`):
   ```bash
   python demo/build_space.py
   ```
2. Đăng nhập Hugging Face (`hf auth login`, dùng Write token) rồi tạo Static Space và đẩy thư mục `dist/hf_space/` lên bằng `huggingface_hub`:
   ```python
   from huggingface_hub import HfApi
   api = HfApi()
   api.create_repo("<user>/factotum", repo_type="space", space_sdk="static", exist_ok=True)
   api.upload_folder(folder_path="dist/hf_space", repo_id="<user>/factotum", repo_type="space")
   ```
