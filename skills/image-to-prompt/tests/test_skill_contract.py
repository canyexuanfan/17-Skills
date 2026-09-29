"""Static distribution safeguards only; keyword checks do not measure fidelity."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text_files() -> list[Path]:
    return [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in {".md", ".py", ".txt"}]


class SkillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_frontmatter_name_and_version(self) -> None:
        self.assertTrue(self.skill.startswith("---\n"))
        header = self.skill.split("---", 2)[1]
        name = re.search(r"^name:\s*(.+)$", header, re.M)
        self.assertIsNotNone(name)
        assert name is not None
        self.assertEqual(name.group(1), ROOT.name)
        self.assertRegex(name.group(1), r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertLessEqual(len(name.group(1)), 64)
        self.assertIn('version: "1.2.0"', header)

    def test_metadata_lengths(self) -> None:
        header = self.skill.split("---", 2)[1]
        for field, maximum in [("description", 1024), ("compatibility", 500)]:
            match = re.search(rf"^{field}:\s*(.+)$", header, re.M)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertGreater(len(match.group(1)), 0)
            self.assertLessEqual(len(match.group(1)), maximum)
        self.assertLess(len(self.skill.splitlines()), 500)

    def test_referenced_files_exist(self) -> None:
        references = re.findall(r"\[[^\]]+\]\((references/[^)]+)\)", self.skill)
        self.assertGreaterEqual(len(references), 8)
        for relative in references:
            path = (ROOT / relative).resolve()
            self.assertTrue(path.is_relative_to(ROOT.resolve()))
            self.assertTrue(path.is_file(), relative)
        for filename in ["image_probe.py", "layout_probe.py", "typography_probe.py"]:
            self.assertTrue((ROOT / "scripts" / filename).is_file())

    def test_utf8_and_ascii_paths(self) -> None:
        for path in text_files():
            with self.subTest(path=path.name):
                data = path.read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertNotIn("\ufffd", data.decode("utf-8"))
                self.assertTrue(str(path.relative_to(ROOT)).isascii())

    def test_python_syntax(self) -> None:
        for path in ROOT.rglob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_distribution_has_no_media_or_archived_answers(self) -> None:
        for path in ROOT.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                self.assertIn(path.suffix, {".md", ".py", ".txt"}, str(path))
        self.assertFalse((ROOT / "assets").exists())

    def test_core_structural_guardrails_present(self) -> None:
        # Guard against accidentally removing named requirements, not their effectiveness.
        for phrase in ["语义场景、版面区域、合成图层必须分别识别", "局部遮挡", "文字必须绑定", "反事实消歧", "逐句证据", "保留测试样本"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_core_independence_and_no_auto_generation(self) -> None:
        for phrase in ["一个 `text` 代码块", "不自动生成图片", "无需参考图或历史上下文", "附属资源不可用时", "没有 Python/Pillow"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)


    def test_typography_branch_guardrails_present(self) -> None:
        for phrase in ["字形骨架", "实际可见字面框", "行间字面空隙", "共轴、左右对齐和等宽", "排版闭合检查"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_continuous_background_guardrails_present(self) -> None:
        for phrase in ["观察分区不等于画面分屏", "软边不等于没有形状", "颜色交会不等于点光源", "宽端", "进出画布", "软边形状检查"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_consistency_and_analogy_guardrails_present(self) -> None:
        for phrase in ["同一属性只有一个已核对的描述基准", "类比不能代替拓扑", "原图证据—提示词规定—结果表现—原因置信度", "平台后叠加"]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_all_markdown_relative_links_resolve(self) -> None:
        for doc in ROOT.rglob("*.md"):
            content = doc.read_text(encoding="utf-8")
            for relative in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if "://" in relative or relative.startswith("#"):
                    continue
                path = (doc.parent / relative.split("#")[0]).resolve()
                with self.subTest(doc=doc.name, relative=relative):
                    self.assertTrue(path.is_relative_to(ROOT.resolve()))
                    self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
