from __future__ import annotations

import unittest

from services.protocol.sandbox_files import (
    SANDBOX_NOTE,
    contains_sandbox_link,
    rewrite_sandbox_links,
    scrub_sandbox_stream,
)


class RewriteTests(unittest.TestCase):
    def test_markdown_link_keeps_label(self):
        text = "已整理好。\n\n[下载 TXT 文件](sandbox:/mnt/data/Am和kaka补单.txt)"
        result = rewrite_sandbox_links(text)
        self.assertNotIn("sandbox:", result)
        self.assertIn("下载 TXT 文件", result)
        self.assertIn(SANDBOX_NOTE, result)

    def test_markdown_link_without_label_uses_filename(self):
        result = rewrite_sandbox_links("[](sandbox:/mnt/data/report.csv)")
        self.assertTrue(result.startswith("report.csv"))
        self.assertIn(SANDBOX_NOTE, result)

    def test_bare_link(self):
        result = rewrite_sandbox_links("文件在这里 sandbox:/mnt/data/demo.txt 请下载")
        self.assertNotIn("sandbox:", result)
        self.assertIn("demo.txt", result)
        self.assertIn(SANDBOX_NOTE, result)

    def test_backtick_wrapped_bare_link(self):
        result = rewrite_sandbox_links("路径：`sandbox:/mnt/data/a.xlsx`")
        self.assertNotIn("sandbox:", result)
        self.assertIn("a.xlsx", result)

    def test_url_encoded_name(self):
        result = rewrite_sandbox_links("[x](sandbox:/mnt/data/%E6%8A%A5%E5%91%8A.txt)")
        # %E6%8A%A5%E5%91%8A == 报告
        self.assertIn("x", result)

    def test_plain_text_unchanged(self):
        text = "这是一段普通文本，包含数组 [1, 2, 3] 和函数 foo(bar)。"
        self.assertEqual(rewrite_sandbox_links(text), text)

    def test_normal_markdown_link_unchanged(self):
        text = "see [docs](https://example.com/path) for details"
        self.assertEqual(rewrite_sandbox_links(text), text)

    def test_contains(self):
        self.assertTrue(contains_sandbox_link("a sandbox:/x b"))
        self.assertFalse(contains_sandbox_link("no special link here"))
        self.assertFalse(contains_sandbox_link(""))


class StreamScrubTests(unittest.TestCase):
    def _run(self, text: str, chunk_size: int) -> str:
        deltas = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        return "".join(scrub_sandbox_stream(iter(deltas)))

    def test_stream_matches_full_rewrite_all_chunk_sizes(self):
        samples = [
            "已整理好。\n\n[下载 TXT 文件](sandbox:/mnt/data/补单记录.txt) 完成",
            "前缀文本 sandbox:/mnt/data/demo.txt 后缀文本",
            "普通回复，没有任何特殊链接，包含 [数组] 和 (括号)。",
            "see [docs](https://example.com) and [file](sandbox:/mnt/data/x.csv)",
            "结尾就是链接：[下载](sandbox:/mnt/data/y.pdf)",
            "多个：[a](sandbox:/mnt/data/a.txt) 和 [b](sandbox:/mnt/data/b.txt)",
        ]
        for text in samples:
            expected = rewrite_sandbox_links(text)
            for chunk_size in (1, 2, 3, 5, 7, 13, 50):
                with self.subTest(text=text[:20], chunk_size=chunk_size):
                    self.assertEqual(self._run(text, chunk_size), expected)

    def test_stream_split_exactly_between_bracket_and_paren(self):
        # ']' ends one delta, '(sandbox:' starts the next — the riskiest split.
        deltas = ["请看 [下载]", "(sandbox:/mnt/data/z.txt) 谢谢"]
        result = "".join(scrub_sandbox_stream(iter(deltas)))
        self.assertNotIn("sandbox:", result)
        self.assertIn("下载", result)
        self.assertIn(SANDBOX_NOTE, result)

    def test_plain_stream_has_no_extra_buffering_artifacts(self):
        text = "第一句。第二句。第三句。"
        self.assertEqual(self._run(text, 3), text)

    def test_empty_deltas_skipped(self):
        deltas = ["hello ", "", "world"]
        self.assertEqual("".join(scrub_sandbox_stream(iter(deltas))), "hello world")


if __name__ == "__main__":
    unittest.main()
