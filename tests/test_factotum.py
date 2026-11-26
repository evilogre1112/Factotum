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
        """Loại bỏ dự đoán kết thúc bằng từ cụt lủn trong file tùy chỉnh; nếu không còn phương án hợp lệ thì trả về câu gốc kèm is_fallback=True."""
        from factotum.core.extension import load_dangling_end_words

        dangling = load_dangling_end_words(force_reload=True)
        if not dangling:
            self.skipTest("File dangling_end_words.txt đang trống.")
        sample_word = "cho" if "cho" in dangling else next(iter(dangling))
        q = f"bộ tư pháp {sample_word}"
        matches_fb = self.model.predict(q, top_k=3)
        self.assertEqual(len(matches_fb), 1)
        self.assertEqual(matches_fb[0].phrase, q)
        self.assertTrue(matches_fb[0].is_fallback)
        self.assertEqual(matches_fb[0].confidence, 0.0)

        # Khi dự đoán hợp lệ, is_fallback phải là False
        matches_ok = self.model.predict("btp", top_k=3)
        self.assertFalse(matches_ok[0].is_fallback)
        self.assertEqual(matches_ok[0].phrase, "bộ tư pháp")

    def test_post_review_deep_pass(self) -> None:
        """Tầng hậu kiểm review cả Top-5 và rà lại Top-1 mà không làm hỏng kết quả chuẩn."""
        deep_matches = self.model.predict("xu phat vi pham hanh chinh", top_k=5)
        self.assertTrue(deep_matches)
        self.assertEqual(deep_matches[0].phrase, "xử phạt vi phạm hành chính")
        self.assertEqual(
            self.model.restore("an toan giao thong"),
            "an toàn giao thông",
        )

    def test_reviewer_zero_markov_neighbor_spell_repair(self) -> None:
        """Khi Worker cho chuỗi 0% Markov (đứt cạnh), Reviewer dò hàng xóm đồ thị để sửa lỗi chính tả."""
        from factotum.engine.models import EditSpan, QueryMatch
        from factotum.engine.reviewer import review_matches

        model = Factotum().fit(
            [
                (
                    "hoạt động kinh doanh và đăng ký kinh doanh theo quy định. "
                    "công đoàn cơ sở và đoàn kiểm tra liên ngành."
                )
                * 4
            ]
        )
        dead_match = QueryMatch(
            phrase="kinh đoàn",
            freq=0,
            missing_count=0,
            superstring_diff=-1,
            source_phrase="",
            missing_words=[],
            markov_prob=1e-12,
            edits=[
                EditSpan(orig="kinh", start=0, end=1, kind="unaccented"),
                EditSpan(orig="doan", start=1, end=2, kind="unaccented"),
            ],
        )
        reviewed = review_matches(model.engine, [dead_match], "kinh doan", top_k=3)
        self.assertTrue(reviewed)
        self.assertEqual(reviewed[0].phrase, "kinh doanh")
        self.assertGreater(reviewed[0].markov_prob, dead_match.markov_prob)
        self.assertEqual(model.restore("kinh doan"), "kinh doanh")
        self.assertEqual(model.restore("kinh doang"), "kinh doanh")

    def test_bidirectional_beam_spell_multi_typos(self) -> None:
        """Beam Search 2 chiều từ neo sửa được 2 từ lỗi liền kề dù neo ở đầu, giữa, cuối hay không có neo."""
        model = Factotum().fit(
            [
                (
                    "đăng ký hộ kinh doanh và hoạt động hộ kinh doanh theo quy định pháp luật. "
                    "hộ kinh doanh chịu trách nhiệm về nghĩa vụ thuế."
                )
                * 4
            ]
        )
        for q in ("hộ kilh doan", "ho kilh doanh", "ho kinh doan", "ho kilh doan", "hộ kihw doan", "ho kihw doan"):
            self.assertEqual(model.restore(q), "hộ kinh doanh", f"Failed on {q!r}")
        self.assertEqual(model.restore("kihw doan"), "kinh doanh")

    def test_long_sentence_spell_and_multi_missing_gaps(self) -> None:
        """Sửa lỗi chính tả trên câu dài và chèn nhiều vị trí thiếu từ."""
        model = Factotum().fit(
            [
                (
                    "thủ tục đăng ký hộ kinh doanh tại ủy ban nhân dân cấp huyện. "
                    "hoạt động hộ kinh doanh theo quy định của pháp luật. "
                    "người sử dụng lao động có trách nhiệm đóng bảo hiểm xã hội. "
                    "người sử dụng lao động và giấy phép lái xe của công dân. "
                    "giấy phép lái xe theo quy định hiện hành."
                )
                * 4
            ]
        )
        self.assertEqual(
            model.restore("thủ tục đăng ký hộ kilh doan tại ủy ban nhân dân"),
            "thủ tục đăng ký hộ kinh doanh tại ủy ban nhân dân",
        )
        self.assertEqual(
            model.restore("người sử dụng động và giấy phép xe"),
            "người sử dụng lao động và giấy phép lái xe",
        )
        self.assertGreaterEqual(model.engine.strong_kb_freq, 1)
        self.assertLessEqual(model.engine.medium_kb_freq, model.engine.strong_kb_freq)


if __name__ == "__main__":
    unittest.main()
