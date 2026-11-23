from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factotum import Factotum


class TestFactotum(unittest.TestCase):
    def setUp(self) -> None:
        self.base_docs = [
            (
                "xử phạt vi phạm hành chính về an toàn giao thông đường bộ. "
                "quy định xử phạt vi phạm hành chính và xử lý vi phạm pháp luật. "
                "bộ tư pháp hướng dẫn thực hiện pháp luật và xử lý vi phạm hành chính. "
                "an toàn giao thông đường bộ là trách nhiệm của toàn xã hội."
            )
            * 3
        ]
        self.model = Factotum().fit(self.base_docs)

    def test_unaccented_and_acronym(self) -> None:
        self.assertEqual(self.model.restore("xu phat vi pham hanh chinh"), "xử phạt vi phạm hành chính")
        self.assertEqual(self.model.restore("xpvphc"), "xử phạt vi phạm hành chính")
        self.assertEqual(self.model.restore("atgt"), "an toàn giao thông")
        self.assertEqual(
            self.model.restore("xpvphc atgt", allow_missing=False),
            "xử phạt vi phạm hành chính an toàn giao thông",
        )

    def test_missing_word(self) -> None:
        self.assertEqual(self.model.restore("an toan thong"), "an toàn giao thông")

    def test_telex_and_spell_correction(self) -> None:
        self.assertEqual(self.model.restore("phaps luaatj"), "pháp luật")
        self.assertEqual(self.model.restore("pháppp luật"), "pháp luật")
        self.assertEqual(self.model.restore("sữ lý"), "xử lý")
        self.assertEqual(self.model.restore("sử lý"), "xử lý")

    def test_save_and_load_standalone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_file = Path(tmp) / "test.ftm"
            self.model.save(model_file)
            loaded = Factotum.load(model_file)
            self.assertEqual(loaded.stats(), self.model.stats())
            self.assertEqual(loaded.restore("xpvphc"), "xử phạt vi phạm hành chính")

    def test_incremental_update_without_rebuild(self) -> None:
        old_phrases = self.model.stats()["phrases"]
        new_medical_docs = [
            (
                "khám chữa bệnh bảo hiểm y tế tại bệnh viện đa khoa trung ương. "
                "hồ sơ bệnh án điện tử và khám chữa bệnh bảo hiểm y tế."
            )
            * 3
        ]
        info = self.model.update(new_medical_docs)
        self.assertGreater(info["added"], 0)
        self.assertGreater(self.model.stats()["phrases"], old_phrases)

        # Dữ liệu mới hoạt động ngay lập tức
        self.assertEqual(self.model.restore("kham chua benh bhyt"), "khám chữa bệnh bảo hiểm y tế")
        self.assertEqual(self.model.restore("hsba dien tu"), "hồ sơ bệnh án điện tử")
        # Dữ liệu cũ vẫn nguyên vẹn
        self.assertEqual(self.model.restore("xpvphc"), "xử phạt vi phạm hành chính")

    def test_domain_agnostic_adaptation_and_acronym_isolation(self) -> None:
        """Factotum không phụ thuộc miền luật: cùng một ký hiệu viết tắt tự đổi nghĩa theo ngữ liệu mới."""
        finance_model = Factotum().fit(
            [
                (
                    "báo cáo tài chính kiểm toán độc lập cuối năm. "
                    "hóa đơn giá trị gia tăng và báo cáo tài chính hợp nhất. "
                    "kiểm toán độc lập xác nhận báo cáo tài chính và hóa đơn giá trị gia tăng."
                )
                * 3
            ]
        )
        tech_model = Factotum().fit(
            [
                (
                    "hệ thống kỹ thuật phần mềm quản lý cơ sở dữ liệu. "
                    "bảo mật an toàn thông tin và kỹ thuật phần mềm hiện đại. "
                    "quản lý cơ sở dữ liệu đảm bảo an toàn thông tin trong hệ thống kỹ thuật phần mềm."
                )
                * 3
            ]
        )

        # Cùng mã viết tắt / cụm thuộc từng miền tự thích nghi mà không cần hardcode
        self.assertEqual(finance_model.restore("bctc"), "báo cáo tài chính")
        self.assertEqual(finance_model.restore("hdgtgt"), "hóa đơn giá trị gia tăng")
        self.assertEqual(finance_model.restore("bao cao chinh"), "báo cáo tài chính")
        self.assertEqual(finance_model.restore("kt doc lap"), "kiểm toán độc lập")

        self.assertEqual(tech_model.restore("csdl"), "cơ sở dữ liệu")
        self.assertEqual(tech_model.restore("attt"), "an toàn thông tin")
        self.assertEqual(tech_model.restore("co so lieu"), "cơ sở dữ liệu")
        self.assertEqual(tech_model.restore("kt phan mem"), "kỹ thuật phần mềm")

        # Chống va chạm: từ đơn hợp lệ không bị bung nhầm thành cụm viết tắt ở bất kỳ miền nào
        self.assertEqual(finance_model.restore("bao cao tai chinh"), "báo cáo tài chính")
        self.assertEqual(tech_model.restore("he thong ky thuat phan mem"), "hệ thống kỹ thuật phần mềm")

    def test_dangling_end_words_filter_and_fallback(self) -> None:
        """Loại bỏ dự đoán kết thúc bằng từ cụt lủn; nếu không có phương án hợp lệ thì trả về câu gốc kèm is_fallback=True."""
        # Khi người dùng nhập câu kết thúc bằng từ cụt lủn ("của", "cho", "thì"...), trả về câu gốc kèm is_fallback=True
        matches_cua = self.model.predict("bộ tư pháp của", top_k=3)
        self.assertEqual(len(matches_cua), 1)
        self.assertEqual(matches_cua[0].phrase, "bộ tư pháp của")
        self.assertTrue(matches_cua[0].is_fallback)
        self.assertEqual(matches_cua[0].confidence, 0.0)

        # Khi dự đoán hợp lệ, is_fallback phải là False
        matches_ok = self.model.predict("btp", top_k=3)
        self.assertFalse(matches_ok[0].is_fallback)
        self.assertEqual(matches_ok[0].phrase, "bộ tư pháp")


if __name__ == "__main__":
    unittest.main()
