"""Factotum web demo (Gradio).

Chạy cục bộ:
    pip install gradio
    python demo/app.py

Triển khai Hugging Face Spaces: xem demo/README.md (dùng `python demo/build_space.py`).
"""

from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
# Hỗ trợ 2 bố cục: repo gốc (demo/app.py) và thư mục Space phẳng (app.py ở gốc)
ROOT = HERE.parent if (HERE.parent / "src" / "factotum").exists() else HERE
sys.path.insert(0, str(ROOT / "src"))

import gradio as gr  # noqa: E402

from factotum import Factotum  # noqa: E402

MODEL_PATH = ROOT / "output" / "factotum_law.ftm"
MODEL = Factotum.load(MODEL_PATH)

EXAMPLES = [
    ["xpvphc atgt"],
    ["nd 23/24 quy định điều gì"],
    ["tòa án nd tối cao"],
    ["xâm phạm co the người khác"],
    ["xkcbsx có bị xử phạt không"],
    ["tôi có thể bán bh cho n khác không"],
    ["an toan thong"],
    ["uống dượu bia phạt bao nhiêu"],
    ["vi pham phappp luat"],
    ["quyen su dung dat"],
]


def restore(query: str, top_k: int = 5):
    query = (query or "").strip()
    if not query:
        return "", [], ""
    t0 = time.perf_counter()
    matches = MODEL.predict(query, top_k=int(top_k))
    ms = (time.perf_counter() - t0) * 1000
    if not matches:
        return query, [], f"Không tìm thấy gợi ý phù hợp ({ms:.1f} ms)."
    rows = [
        [i, m.phrase, f"{m.confidence:.0%}", "giữ câu gốc" if m.is_fallback else ""]
        for i, m in enumerate(matches, 1)
    ]
    top = matches[0]
    note = f"{ms:.1f} ms trên CPU | độ tin cậy Top-1: {top.confidence:.0%}"
    if top.is_fallback:
        note += " | không có gợi ý trọn nghĩa nên giữ nguyên câu gốc"
    elif top.confidence < 0.5:
        note += " | độ tin cậy thấp, nên xem các gợi ý khác"
    return top.phrase, rows, note


DESCRIPTION = """
**Factotum** khôi phục câu tiếng Việt gõ nhanh (không dấu, viết tắt, thiếu từ, sai chính tả, Telex) thành câu chuẩn.
Mô hình demo (`1,29 MB`) học từ 156 văn bản pháp luật công khai, chạy hoàn toàn trên CPU, không dùng GPU hay LLM.

Restores fast-typed Vietnamese (no diacritics, abbreviations, missing words, typos) using a statistical
phrase graph learned from your own documents. CPU-only, ~2 ms per query.

[GitHub](https://github.com/evilogre1112/Factotum)
"""

with gr.Blocks(title="Factotum: Vietnamese text restoration") as demo:
    gr.Markdown("# Factotum (`factotum-vn-nlp`)")
    gr.Markdown(DESCRIPTION)
    with gr.Row():
        with gr.Column(scale=3):
            inp = gr.Textbox(label="Câu nhập / Input", placeholder="vd: xpvphc atgt", lines=2)
            k = gr.Slider(1, 10, value=5, step=1, label="Số gợi ý (Top-K)")
            btn = gr.Button("Khôi phục / Restore", variant="primary")
        with gr.Column(scale=4):
            out = gr.Textbox(label="Kết quả Top-1", lines=2)
            info = gr.Markdown()
    table = gr.Dataframe(
        headers=["#", "Gợi ý", "Độ tin cậy", "Ghi chú"],
        datatype=["number", "str", "str", "str"],
        label="Top-K",
        interactive=False,
    )
    gr.Examples(EXAMPLES, inputs=inp)
    gr.Markdown(
        "Lưu ý: độ chính xác phụ thuộc tài liệu đã học (miền pháp luật). "
        "Trên 10.000 câu từ 32 văn bản chưa từng học: Top-1 ~62%, Top-3 ~72% "
        "(chi tiết và giới hạn của phép đo xem README)."
    )
    btn.click(restore, [inp, k], [out, table, info])
    inp.submit(restore, [inp, k], [out, table, info])

if __name__ == "__main__":
    demo.launch()
