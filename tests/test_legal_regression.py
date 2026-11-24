from collections import defaultdict
import csv
import json
import os
from pathlib import Path
import random
import sys
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factotum import Factotum
from factotum.core.doc_reader import read_single_file

MODEL_PATH = ROOT_DIR / "output" / "factotum_law.ftm"
MANUAL_CASES_PATH = ROOT_DIR / "benchmark" / "test_cases.json"
LEGAL_400_PATH = ROOT_DIR / "tests" / "fixtures" / "legal_eval_400.tsv"
DATA_DIR = ROOT_DIR / "data"


def _load_legal_400() -> list[tuple[str, str, str]]:
    with LEGAL_400_PATH.open(encoding="utf-8") as f:
        return [(r["category"], r["query"], r["expected"]) for r in csv.DictReader(f, delimiter="\t")]


@unittest.skipUnless(MODEL_PATH.is_file(), f"Thiếu mô hình {MODEL_PATH}")
class TestLegalModelRegression(unittest.TestCase):
    model: Factotum
    eval_rows: list[tuple[str, str, str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.model = Factotum.load(MODEL_PATH)
        cls.eval_rows = _load_legal_400()

    def test_manual_60_legal_cases(self) -> None:
        """Khóa toàn bộ 60 câu hồi quy thủ công miền pháp luật (phải đúng 60/60 Top-1)."""
        cases = json.loads(MANUAL_CASES_PATH.read_text(encoding="utf-8"))
        failures: list[str] = []
        for item in cases:
            got = self.model.restore(item["query"])
            if got != item["expected"]:
                failures.append(f"[{item['category']}] {item['query']!r}: got={got!r}, expected={item['expected']!r}")
        self.assertEqual(failures, [], f"Lệch {len(failures)}/{len(cases)} câu thủ công:\n" + "\n".join(failures))

    def test_canonical_legal_acronyms(self) -> None:
        """Khóa các viết tắt pháp luật chuẩn để sửa scoring không gây va chạm sang cụm khác."""
        expected_acronyms = {
            "btp": "bộ tư pháp",
            "ubnd": "ủy ban nhân dân",
            "csgt": "cảnh sát giao thông",
            "gplx": "giấy phép lái xe",
            "atgt": "an toàn giao thông",
            "xpvphc": "xử phạt vi phạm hành chính",
            "dkkh": "đăng ký kết hôn",
            "bhxh": "bảo hiểm xã hội",
            "bhyt": "bảo hiểm y tế",
            "hdnd": "hội đồng nhân dân",
            "bgtvt": "bộ giao thông vận tải",
            "btnmt": "bộ tài nguyên và môi trường",
            "btc": "bộ tài chính",
            "byt": "bộ y tế",
            "bgddt": "bộ giáo dục và đào tạo",
            "blds": "bộ luật dân sự",
            "blhs": "bộ luật hình sự",
            "bllđ": "bộ luật lao động",
        }
        failures: list[str] = []
        for abbr, expected in expected_acronyms.items():
            got = self.model.restore(abbr)
            if got != expected:
                failures.append(f"{abbr!r}: got={got!r}, expected={expected!r}")
        self.assertEqual(failures, [], "Va chạm viết tắt pháp luật:\n" + "\n".join(failures))

    def test_acronym_collision_and_valid_word_guard(self) -> None:
        """Từ tiếng Việt hợp lệ không được bung nhầm thành viết tắt; viết tắt ngắn phải giải đúng theo ngữ cảnh."""
        for single_word in ("ban", "can", "con", "cho", "nha"):
            got = self.model.restore(single_word)
            self.assertEqual(
                len(got.split()),
                1,
                f"Từ đơn {single_word!r} bị bung nhầm thành cụm viết tắt: {got!r}",
            )

        valid_phrases = {
            "ban hanh van ban": "ban hành văn bản",
            "can thiet phai co": "cần thiết phải có",
            "nha nuoc cong hoa": "nhà nước cộng hòa",
            "co tham quyen giai quyet": "có thẩm quyền giải quyết",
            "to chuc ca nhan": "tổ chức cá nhân",
            "chinh phu quy dinh": "chính phủ quy định",
            "phap luat": "pháp luật",
            "nghi dinh": "nghị định",
            "thong tu": "thông tư",
            "quyet dinh": "quyết định",
        }
        for query, expected in valid_phrases.items():
            self.assertEqual(self.model.restore(query), expected, query)

        contextual_acronyms = {
            "quan he hn va gia dinh": "quan hệ hôn nhân và gia đình",
            "nguoi dieu khien pt tham gia giao thong": "người điều khiển phương tiện tham gia giao thông",
            "xe o to kinh doanh vt hanh khach": "xe ô tô kinh doanh vận tải hành khách",
            "giay chung nhan kd an toan ky thuat": "giấy chứng nhận kiểm định an toàn kỹ thuật",
            "thanh lap hdnd cap tinh": "thành lập hội đồng nhân dân cấp tỉnh",
            "tham gia bhxh bat buoc": "tham gia bảo hiểm xã hội bắt buộc",
        }
        for query, expected in contextual_acronyms.items():
            self.assertEqual(self.model.restore(query), expected, query)

        # Khóa chống băm nhầm số thập phân, đơn vị đo sau số và từ khẩu ngữ/sai chính tả ngắn trong câu dài
        self.assertIn("0.4 mg", self.model.restore("thoi nong do con xe may kich khung 0.4 mg"))
        self.assertIn("bị phạt", self.model.restore("o to k that day an toan bi phat bn"))
        self.assertIn("nhiu tiền", self.model.restore("xe may di nham len cao toc phat nhiu tien"))
        self.assertIn("oto", self.model.restore("vua lai oto vua nhan tin dt phat may tr"))
        self.assertEqual(self.model.restore("ch XXI"), "chương xxi")
        self.assertEqual(self.model.restore("ch I"), "chương i")
        self.assertEqual(self.model.restore("cg i"), "chương i")

    def test_accented_sentence_typo_and_oov_preservation(self) -> None:
        """Câu đã có dấu: sửa đúng lỗi chính tả (kể cả câu >12 từ) và không làm hỏng từ đúng hay OOV hợp lệ."""
        cases = {
            "người điều khiển xe máy uống dượu bia tham gia giao thông bị phạt bao nhiêu": (
                "người điều khiển xe máy uống rượu bia tham gia giao thông bị phạt bao nhiêu"
            ),
            "uống dượu bia phạt bao nhiêu": "uống rượu bia phạt bao nhiêu",
            "bảo hiễm xã hội bắt buộc": "bảo hiểm xã hội bắt buộc",
            "bảo hiễm xã hội một lần tính như thế nào": "bảo hiểm xã hội một lần tính như thế nào",
            "xử phạt vi phạm hành chính bao nhiêu tiền": "xử phạt vi phạm hành chính bao nhiêu tiền",
            "nộp phạt vi phạm giao thông ở đâu": "nộp phạt vi phạm giao thông ở đâu",
            "sữ lý vi phạm hành chính": "xử lý vi phạm hành chính",
            "chạy xe vượt đèn đỏ bị phạt bao nhiêu tiền": "chạy xe vượt đèn đỏ bị phạt bao nhiêu tiền",
            "sang tên sổ đỏ mất những khoản phí nào": "sang tên sổ đỏ mất những khoản phí nào",
            "ly hôn đơn phương mất bao lâu thì xong": "ly hôn đơn phương mất bao lâu thì xong",
            "làm căn cước công dân gắn chip ở đâu": "làm căn cước công dân gắn chip ở đâu",
            "tạm trú tạm vắng đăng ký online được không": "tạm trú tạm vắng đăng ký online được không",
            "trốn đóng bảo hiểm xã hội bị xử lý thế nào": "trốn đóng bảo hiểm xã hội bị xử lý thế nào",
            "khởi kiện đòi nợ cần chuẩn bị hồ sơ gì": "khởi kiện đòi nợ cần chuẩn bị hồ sơ gì",
            "sử lý vi phạm hành chính": "xử lý vi phạm hành chính",
            "quy định sử lý vi phạm hành chính": "quy định xử lý vi phạm hành chính",
            "người có thẩm quyền sử phạt vi phạm hành chính": "người có thẩm quyền xử phạt vi phạm hành chính",
            "việc sử lý vi phạm hành chính": "việc xử lý vi phạm hành chính",
            "xử phạt vi phạm hành chính và sử lý vi phạm": "xử phạt vi phạm hành chính và xử lý vi phạm",
            "xkcbsx có bị xử phạt không": "xét kháng cáo bổ sung xuất có bị xử phạt không",
            "xkcbs có bị phạt không": "xe không có biển số có bị phạt không",
            "tôi di xe tren duong gay tngt": "tôi đi xe trên đường gây tai nạn giao thông",
            "nguoi lai xe gay tngt": "người lái xe gây tai nạn giao thông",
            "vi pham atgt bi xpvphc": "vi phạm an toàn giao thông bị xử phạt vi phạm hành chính",
        }
        for query, expected in cases.items():
            self.assertEqual(self.model.restore(query), expected, query)

    def test_legal_400_scoring_regression_gate(self) -> None:
        """Khóa sàn Top-1 / Top-3 theo từng nhóm trên 400 câu thực tế để phát hiện tụt điểm khi sửa scoring."""
        floors = {
            "khong_dau_moi": (0.83, 0.94),
            "viet_tat_hon_hop": (0.70, 0.80),
            "thieu_1_tu": (0.49, 0.62),
            "viet_tat_toan_cum": (0.26, 0.36),
        }
        stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # [n, t1, t3]
        for cat, query, expected in self.eval_rows:
            cands = self.model.predict(query, top_k=3)
            preds = [c.phrase for c in cands]
            stats[cat][0] += 1
            if preds and preds[0] == expected:
                stats[cat][1] += 1
            if expected in preds:
                stats[cat][2] += 1

        tot_n = tot_t1 = tot_t3 = 0
        for cat, (min_t1, min_t3) in floors.items():
            n, t1, t3 = stats[cat]
            self.assertGreater(n, 0, f"Thiếu nhóm {cat}")
            self.assertGreaterEqual(t1 / n, min_t1, f"{cat} Top-1 tụt xuống {t1/n:.3f} < {min_t1:.2f}")
            self.assertGreaterEqual(t3 / n, min_t3, f"{cat} Top-3 tụt xuống {t3/n:.3f} < {min_t3:.2f}")
            tot_n += n
            tot_t1 += t1
            tot_t3 += t3

        self.assertGreaterEqual(tot_t1 / tot_n, 0.58, f"Tổng Top-1 tụt xuống {tot_t1/tot_n:.3f} < 0.58")
        self.assertGreaterEqual(tot_t3 / tot_n, 0.69, f"Tổng Top-3 tụt xuống {tot_t3/tot_n:.3f} < 0.69")

    def test_confidence_bounds_and_calibration_order(self) -> None:
        """Khóa miền giá trị confidence [0, 1], không trùng ứng viên và tính đơn điệu nhóm cao > nhóm thấp."""
        hi_n = hi_ok = lo_n = lo_ok = 0
        for _, query, expected in self.eval_rows:
            cands = self.model.predict(query, top_k=3)
            self.assertTrue(cands, f"Không trả về ứng viên cho {query!r}")
            phrases = [c.phrase for c in cands]
            self.assertEqual(len(phrases), len(set(phrases)), f"Trùng ứng viên Top-3 cho {query!r}: {phrases}")
            for c in cands:
                self.assertGreaterEqual(c.confidence, 0.0)
                self.assertLessEqual(c.confidence, 1.0)
            ok1 = cands[0].phrase == expected
            if cands[0].confidence >= 0.70:
                hi_n += 1
                hi_ok += ok1
            elif cands[0].confidence < 0.50:
                lo_n += 1
                lo_ok += ok1

        self.assertGreater(hi_n, 50)
        self.assertGreater(lo_n, 20)
        self.assertGreater(
            hi_ok / hi_n,
            lo_ok / lo_n + 0.10,
            f"Hiệu chuẩn confidence lệch: high={hi_ok/hi_n:.3f}, low={lo_ok/lo_n:.3f}",
        )

    @unittest.skipUnless(
        os.environ.get("FACTOTUM_RUN_HELDOUT_GATE") == "1" and DATA_DIR.is_dir(),
        "Đặt FACTOTUM_RUN_HELDOUT_GATE=1 (khi có thư mục data/) để chạy cổng held-out tách văn bản.",
    )
    def test_document_level_heldout_gate(self) -> None:
        """Train lại từ 124 văn bản (seed=42) và replay 400 câu từ 32 văn bản chưa từng học."""
        doc_files = sorted(
            p for p in DATA_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in (".txt", ".doc", ".docx")
        )
        rng = random.Random(42)
        shuffled = list(doc_files)
        rng.shuffle(shuffled)
        split_idx = int(len(shuffled) * 0.8)
        train_texts = [read_single_file(p) for p in shuffled[:split_idx]]
        heldout_model = Factotum().fit([t for t in train_texts if t.strip()])

        t1 = t3 = 0
        for _, query, expected in self.eval_rows:
            preds = [c.phrase for c in heldout_model.predict(query, top_k=3)]
            if preds and preds[0] == expected:
                t1 += 1
            if expected in preds:
                t3 += 1

        n = len(self.eval_rows)
        self.assertGreaterEqual(t1 / n, 0.50, f"Held-out Top-1 tụt xuống {t1/n:.3f} < 0.50")
        self.assertGreaterEqual(t3 / n, 0.61, f"Held-out Top-3 tụt xuống {t3/n:.3f} < 0.61")


if __name__ == "__main__":
    unittest.main()
