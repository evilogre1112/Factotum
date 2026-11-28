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

    def test_physical_mdis_store_and_persistence(self) -> None:
        """Cấu trúc MDIS vật lý (Entity / +/- Dimension / Twin Event / AUX) và lưu-tải file .mdis."""
        import tempfile
        from pathlib import Path
        from factotum.engine.mdis import MDISStore

        model = Factotum().fit(
            [
                (
                    "quyền và nghĩa vụ dân sự. "
                    "thực hiện nghĩa vụ dân sự. "
                    "nghĩa vụ dân sự của công dân."
                )
                * 4
            ]
        )
        store = model.engine.ensure_mdis()
        st = store.stats()
        self.assertEqual(st["aux_entities"], 1)
        self.assertGreater(st["entities"], 5)
        self.assertGreater(st["dims_plus"], 0)
        self.assertGreater(st["dims_minus"], 0)
        self.assertGreater(st["events"], 0)

        # Kiểm tra định lý giao cắt hai chiều và phần tử ẩn <AUX>
        self.assertTrue(store.query_prefix("nghĩa", ["vụ", "dân", "sự"]))
        self.assertFalse(store.query_prefix("vụ", ["nghĩa"]))

        # Kiểm tra chiều - đọc ngược và chiều + đọc xuôi với other_entity O(1)
        plus_dims = [d for d in range(len(store.dim_mode)) if store.dim_mode[d] == 1 and store.dim_events[d]]
        minus_dims = [d for d in range(len(store.dim_mode)) if store.dim_mode[d] == 0 and store.dim_events[d]]
        self.assertTrue(plus_dims)
        self.assertTrue(minus_dims)
        d_plus = plus_dims[0]
        self.assertEqual(store.logical_events(d_plus), list(store.dim_events[d_plus]))
        d_minus = minus_dims[0]
        self.assertEqual(store.logical_events(d_minus), list(reversed(store.dim_events[d_minus])))
        # Phần tử đầu tiên logic của chiều '-' luôn là sự kiện <AUX> (ID 0, chặn đầu)
        first_minus_ev = store.logical_events(d_minus)[0]
        self.assertEqual(store.other_entity(first_minus_ev, d_minus), 0)

        # Kiểm tra tra cứu cụm con qua MDISStore và cập nhật động add_phrases
        freq_sub, diff_sub, src_sub = model.engine.lookup_real_phrase_score(["nghĩa", "vụ", "dân"])
        self.assertGreater(freq_sub, 0)
        self.assertGreaterEqual(diff_sub, 0)
        self.assertIn("nghĩa vụ dân", src_sub)

        model.engine.add_phrases({"bảo hộ quyền tác giả": 7})
        self.assertTrue(store.query_prefix("bảo", ["hộ", "quyền", "tác", "giả"]))
        freq_new, diff_new, src_new = model.engine.lookup_real_phrase_score(["hộ", "quyền", "tác"])
        self.assertEqual(freq_new, 7)
        self.assertEqual(diff_new, 2)
        self.assertEqual(src_new, "bảo hộ quyền tác giả")

        # Lưu và tải lại file .mdis
        with tempfile.TemporaryDirectory() as tmpdir:
            mdis_path = Path(tmpdir) / "test.mdis"
            model.engine.save_mdis(mdis_path)
            self.assertTrue(mdis_path.exists())
            reloaded = MDISStore()
            reloaded.load_file(mdis_path)
            self.assertEqual(reloaded.stats(), store.stats())
            self.assertTrue(reloaded.query_prefix("nghĩa", ["vụ", "dân", "sự"]))
            self.assertFalse(reloaded.query_prefix("vụ", ["nghĩa"]))
            self.assertEqual(
                reloaded.lookup_phrase_score(["hộ", "quyền", "tác"], model.engine.phrases, 4),
                store.lookup_phrase_score(["hộ", "quyền", "tác"], model.engine.phrases, 4),
            )

    def test_long_sentence_real_word_typo_local_kb_window(self) -> None:
        """Sửa lỗi chính tả từ hợp lệ (vd 'nang' -> 'nạn') trong câu dài nhờ cửa sổ KB cục bộ 3-4 từ."""
        model = Factotum().fit(
            [
                (
                    "lái xe ô tô gây tai nạn giao thông bị xử phạt nghiêm. "
                    "người lái xe gây tai nạn giao thông phải bồi thường. "
                    "phòng chống tai nạn giao thông đường bộ. "
                    "năng lực hành vi dân sự của công dân."
                )
                * 4
            ]
        )
        self.assertEqual(model.restore("gay tai nang giao thong"), "gây tai nạn giao thông")
        self.assertEqual(
            model.restore("lái xe oto gay tai nang giao thông"),
            "lái xe oto gây tai nạn giao thông",
        )

    def test_long_flat_unaccented_sentence_chunk_boundaries(self) -> None:
        """Câu dài không dấu câu không bị rơi đoạn, lặp từ neo hay chèn từ thiếu phá vỡ cụm KB."""
        model = Factotum().fit(
            [
                (
                    "trong quan hệ dân sự các bên bình đẳng không được phân biệt đối xử. "
                    "trong quản lý hành chính nhà nước phải tuân thủ pháp luật. "
                    "thỏa thuận không vi phạm điều cấm của luật không trái đạo đức xã hội. "
                    "có hiệu lực thực hiện đối với các bên và phải được chủ thể khác tôn trọng."
                )
                * 4
            ]
        )
        out = model.restore(
            "trong quan he dan su cac ben binh dang thoa thuan khong vi pham dieu cam cua luat "
            "khong trai dao duc xa hoi co hieu luc thuc hien doi voi cac ben va phai duoc chu the khac ton trong"
        )
        self.assertIn("trong quan hệ dân sự các bên bình đẳng", out)
        self.assertIn("có hiệu lực thực hiện đối với các bên và phải được chủ thể khác tôn trọng", out)


if __name__ == "__main__":
    unittest.main()
