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


if __name__ == "__main__":
    unittest.main()
