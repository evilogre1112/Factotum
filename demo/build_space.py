"""Đóng gói thư mục sẵn sàng đẩy lên Hugging Face Spaces (Gradio SDK).

    python demo/build_space.py            # tạo dist/hf_space/
    cd dist/hf_space && git init && ...   # xem demo/README.md
"""

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "hf_space"

SPACE_README = """---
title: Factotum Vietnamese Text Restoration
emoji: 🇻🇳
colorFrom: blue
colorTo: green
sdk: gradio
app_file: app.py
pinned: false
license: mit
short_description: Restore Vietnamese diacritics, abbreviations and typos on CPU
---

# Factotum demo

Khôi phục tiếng Việt gõ nhanh (không dấu, viết tắt, thiếu từ, sai chính tả) thành câu chuẩn.
Mã nguồn, benchmark và giới hạn: https://github.com/evilogre1112/Factotum
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "output").mkdir(parents=True)
    shutil.copytree(
        ROOT / "src" / "factotum",
        OUT / "src" / "factotum",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.dll", "*.so", "*.dylib"),
    )
    for name in ("factotum_law.ftm", "dangling_end_words.txt"):
        shutil.copy2(ROOT / "output" / name, OUT / "output" / name)
    shutil.copy2(ROOT / "demo" / "app.py", OUT / "app.py")
    shutil.copy2(ROOT / "demo" / "requirements.txt", OUT / "requirements.txt")
    shutil.copy2(ROOT / "LICENSE", OUT / "LICENSE")
    (OUT / "README.md").write_text(SPACE_README, encoding="utf-8")
    size_mb = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1e6
    print(f"Đã tạo {OUT} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
