from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from factotum.builder.extractor import extract_phrases, write_pure_log
from factotum.builder.make_tree import build_graph, generate_unaccented_keys
from factotum.builder.suffix_array import build_sa_lcp, convert_text_to_int, extract_candidates
from factotum.core.config import FactotumPaths


class TestBuilder(unittest.TestCase):
    def test_suffix_array_and_lcp(self) -> None:
        tokens = ["a", "b", "a", "b", "c"]
        s = convert_text_to_int(tokens) + [0]
        sa, lcp = build_sa_lcp(s)
        # Mảng SA phải sắp xếp mọi hậu tố tăng dần theo thứ tự từ điển
        suffixes = [s[i:] for i in sa]
        self.assertEqual(suffixes, sorted(suffixes))
        cands = extract_candidates(lcp, len(s))
        self.assertTrue(any(length == 2 and freq == 2 for length, freq, _ in cands))

    def test_extract_phrases_in_memory(self) -> None:
        text = (
            "xử phạt vi phạm hành chính về an toàn giao thông. "
            "xử phạt vi phạm hành chính và an toàn giao thông đường bộ."
        )
        results, n, total_unique = extract_phrases(text)
        phrases = {r.text for r in results}
        self.assertGreater(n, 10)
        self.assertGreater(total_unique, 10)
        self.assertIn("xử phạt vi phạm hành chính", phrases)
        self.assertIn("an toàn giao thông", phrases)

    def test_no_cross_sentence_phrases(self) -> None:
        text = (
            "theo quy định của luật này.\nĐiều 23. Quy định về xử phạt.\n"
            "theo quy định của luật này.\nĐiều 24. Quy định về xử lý.\n"
        )
        results, _, _ = extract_phrases(text)
        phrases = {r.text for r in results}
        self.assertNotIn("này điều", phrases)
        self.assertNotIn("này điều <num>", phrases)
        self.assertIn("theo quy định của luật này", phrases)
        self.assertIn("điều <num>", phrases)

    def test_generate_unaccented_keys(self) -> None:
        keys = set(generate_unaccented_keys("được"))
        self.assertIn("duoc", keys)
        self.assertIn("d", keys)
        self.assertIn("đ", keys)
        self.assertIn("dc", keys)
        self.assertIn("đc", keys)
        self.assertIn("ng", set(generate_unaccented_keys("người")))
        self.assertIn("kh", set(generate_unaccented_keys("khác")))
        self.assertIn("nh", set(generate_unaccented_keys("nhà")))
        self.assertIn("gh", set(generate_unaccented_keys("ghi")))

    def test_build_graph_isolated_paths(self) -> None:
        text = (
            "hợp đồng mua bán hàng hóa quốc tế. "
            "hợp đồng mua bán hàng hóa và dịch vụ."
        )
        results, _, _ = extract_phrases(text)
        with tempfile.TemporaryDirectory() as tmp:
            paths = FactotumPaths.from_output_dir(tmp)
            write_pure_log(paths.pure_log_dir / "sample.txt", results)
            build_graph(paths=paths, show_progress=False)
            self.assertTrue(paths.kb_path.exists())
            self.assertTrue(paths.data_path.exists())
            self.assertTrue(paths.unaccented_map_path.exists())
            self.assertIn("hợp đồng mua bán hàng hóa", paths.kb_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
