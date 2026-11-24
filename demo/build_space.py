"""Đóng gói thư mục Static Space (Pyodide WebAssembly) sẵn sàng đẩy lên Hugging Face Spaces miễn phí.

    python demo/build_space.py            # tạo dist/hf_space/ (~1,35 MB)
"""

from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "hf_space"

SPACE_README = """---
title: Factotum Vietnamese Text Restoration
emoji: ⚡
colorFrom: blue
colorTo: green
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Restore Vietnamese diacritics, abbreviations & typos
---

# Factotum WebAssembly Demo

Khôi phục tiếng Việt gõ nhanh (không dấu, viết tắt, thiếu từ, sai chính tả, Telex) chạy 100% trên trình duyệt bằng Pyodide WebAssembly.
Mã nguồn, hướng dẫn tự huấn luyện và benchmark: https://github.com/evilogre1112/Factotum
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "output").mkdir(parents=True)

    # Nén gói src/factotum thành 1 file zip nhỏ (~35 KB) cho trình duyệt tải 1 lần
    pkg_zip = OUT / "factotum_pkg.zip"
    src_pkg = ROOT / "src" / "factotum"
    with zipfile.ZipFile(pkg_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(src_pkg.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            arcname = p.relative_to(ROOT / "src").as_posix()
            zf.write(p, arcname)

    for name in ("factotum_law.ftm", "dangling_end_words.txt"):
        shutil.copy2(ROOT / "output" / name, OUT / "output" / name)
    shutil.copy2(ROOT / "demo" / "static" / "index.html", OUT / "index.html")
    shutil.copy2(ROOT / "LICENSE", OUT / "LICENSE")
    (OUT / "README.md").write_text(SPACE_README, encoding="utf-8")

    size_mb = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) / 1e6
    print(f"Đã tạo {OUT} ({size_mb:.2f} MB | factotum_pkg.zip = {pkg_zip.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
