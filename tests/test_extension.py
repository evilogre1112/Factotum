from pathlib import Path
import sys
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factotum.core.extension import (
    clean_word,
    collapse_repeated_chars,
    decode_telex_word,
    get_compatible_token_classes,
    is_valid_vietnamese_syllable,
    normalize_vietnamese_tone,
    remove_accents,
    vn_word_similarity,
)


class TestExtension(unittest.TestCase):
    def test_clean_and_normalize(self) -> None:
        self.assertEqual(clean_word("  Hoà, "), "hòa")
        self.assertEqual(clean_word("0.4"), "0.4")
        self.assertEqual(clean_word("0.4."), "0.4")
        self.assertEqual(get_compatible_token_classes("0.4"), ["<num>", "<code>"])
        self.assertEqual(normalize_vietnamese_tone("thuý"), "thúy")
        self.assertEqual(normalize_vietnamese_tone("quý"), "quý")
        self.assertEqual(remove_accents("đường bộ"), "duong bo")

    def test_vietnamese_syllable_validation(self) -> None:
        for valid in ("quyền", "huyện", "yết", "huýt", "nghiêng", "pháp", "luật"):
            self.assertTrue(is_valid_vietnamese_syllable(valid), valid)
        for invalid in ("ubnd", "gt", "hsmt", "yt", "xpvphc", "btp", "123"):
            self.assertFalse(is_valid_vietnamese_syllable(invalid), invalid)

    def test_telex_decoding(self) -> None:
        cases = {
            "buowcs": "bước",
            "dduowcj": "được",
            "phaps": "pháp",
            "luaatj": "luật",
            "chinhs": "chính",
            "quyeenf": "quyền",
            "nguowif": "người",
            "ddieefu": "điều",
            "xuwr": "xử",
        }
        for raw, expected in cases.items():
            self.assertEqual(decode_telex_word(raw), expected, raw)
        # Không giải mã nhầm từ viết tắt hoặc âm tiết không dấu hợp lệ
        for untouched in ("xpvphc", "atgt", "btp", "nha"):
            self.assertEqual(decode_telex_word(untouched), "", untouched)

    def test_collapse_and_similarity(self) -> None:
        self.assertEqual(collapse_repeated_chars("pháppp"), "pháp")
        self.assertEqual(collapse_repeated_chars("luậtt"), "luật")
        self.assertGreaterEqual(vn_word_similarity("pháppp", "pháp"), 0.95)
        self.assertGreaterEqual(vn_word_similarity("sử", "xử"), 0.75)
        self.assertGreaterEqual(vn_word_similarity("sữ", "xử"), 0.70)
        self.assertGreaterEqual(vn_word_similarity("luta", "luật"), 0.58)
        self.assertLess(vn_word_similarity("quản", "sử"), 0.20)


if __name__ == "__main__":
    unittest.main()
