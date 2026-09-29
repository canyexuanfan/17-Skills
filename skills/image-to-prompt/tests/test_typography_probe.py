"""Generic synthetic geometry tests, not visual recognition or image generation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from typography_probe import measure_typography  # noqa: E402

SCRIPT = ROOT / "scripts" / "typography_probe.py"


class TypographyArithmeticTests(unittest.TestCase):
    def test_single_line_and_canvas_margins(self) -> None:
        r = measure_typography(500, 250, [("row", (50, 25, 450, 75))])
        self.assertEqual(r["adjacent_ink_gaps"], [])
        self.assertEqual(r["visible_ink_block"]["size_target_percent_wh"], [80, 20])
        self.assertEqual(r["block_margins_percent_of_target_extent"], {"left": 10, "top": 10, "right": 10, "bottom": 70})
        self.assertEqual(r["height_closure"]["residual_px"], 0)
        self.assertFalse(r["font_or_ocr_recognition_performed"])

    def test_positive_gap_and_height_closure(self) -> None:
        r = measure_typography(500, 250, [("a", (10, 20, 250, 50)), ("b", (20, 80, 210, 130))])
        gap = r["adjacent_ink_gaps"][0]
        self.assertEqual(gap["signed_ink_gap_px"], 30)
        self.assertEqual(gap["signed_ink_gap_percent_of_target_height"], 12)
        self.assertEqual(gap["signed_gap_divided_by_mean_row_ink_height"], .75)
        self.assertEqual(r["height_closure"]["visible_ink_block_height_px"], 110)
        self.assertEqual(r["height_closure"]["residual_px"], 0)

    def test_touching_rows(self) -> None:
        r = measure_typography(90, 90, [("a", (0, 0, 60, 30)), ("b", (0, 30, 60, 60))])
        self.assertEqual(r["adjacent_ink_gaps"][0]["signed_ink_gap_px"], 0)
        self.assertFalse(r["adjacent_ink_gaps"][0]["ink_bounds_overlap_vertically"])

    def test_overlap_is_preserved(self) -> None:
        r = measure_typography(90, 90, [("a", (0, 0, 60, 40)), ("b", (0, 30, 60, 70))])
        self.assertEqual(r["adjacent_ink_gaps"][0]["signed_ink_gap_px"], -10)
        self.assertTrue(r["adjacent_ink_gaps"][0]["ink_bounds_overlap_vertically"])
        self.assertEqual(r["height_closure"]["residual_px"], 0)

    def test_different_widths_not_forced_equal(self) -> None:
        r = measure_typography(300, 200, [("a", (15, 20, 285, 50)), ("b", (80, 90, 210, 150))])
        self.assertEqual(r["rows"][0]["size_target_percent_wh"][0], 90)
        self.assertAlmostEqual(r["rows"][1]["size_target_percent_wh"][0], 43.333)
        self.assertEqual(r["visible_ink_block"]["supplied_bbox_target_px_ltrb"], [15, 20, 285, 150])

    def test_physical_aspect_ratio_not_normalized_percent_quotient(self) -> None:
        r = measure_typography(200, 600, [("a", (20, 60, 120, 160))])
        self.assertEqual(r["rows"][0]["visible_ink_width_divided_by_height"], 1)
        self.assertEqual(r["rows"][0]["size_target_percent_wh"], [50, 16.667])

    def test_explicit_container(self) -> None:
        r = measure_typography(500, 250, [("a", (50, 50, 450, 100))], container=(25, 25, 475, 225))
        self.assertEqual(r["block_margins_inside_container_px_ltrb_named"], {"left": 25, "top": 25, "right": 25, "bottom": 125})

    def test_fractional_coordinates(self) -> None:
        r = measure_typography(100, 100, [("a", (1.5, 2.5, 90.5, 22.5)), ("b", (1.5, 25.75, 80.5, 51.25))])
        self.assertEqual(r["adjacent_ink_gaps"][0]["signed_ink_gap_px"], 3.25)
        self.assertEqual(r["height_closure"]["residual_px"], 0)

    def test_empty_and_duplicate_rows_rejected(self) -> None:
        with self.assertRaises(ValueError):
            measure_typography(100, 100, [])
        with self.assertRaises(ValueError):
            measure_typography(100, 100, [("a", (0, 0, 50, 20)), ("a", (0, 30, 50, 60))])

    def test_invalid_rows_rejected(self) -> None:
        for row in [(0, 0, 101, 20), (0, 0, 0, 20), (0, 0, float("nan"), 20), (0, True, 50, 20)]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                measure_typography(100, 100, [("a", row)])

    def test_nonmonotone_rows_rejected_not_reordered(self) -> None:
        for lines in [[("a", (0, 50, 50, 70)), ("b", (0, 10, 50, 30))], [("a", (0, 0, 50, 90)), ("b", (0, 10, 50, 40))]]:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                measure_typography(100, 100, lines)

    def test_outside_or_invalid_container(self) -> None:
        for c in [(10, 10, 90, 90), (0, 0, 101, 100), (0, 0, 0, 100)]:
            with self.subTest(container=c), self.assertRaises(ValueError):
                measure_typography(100, 100, [("a", (0, 0, 100, 100))], container=c)


class TypographyCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "字面 算术.png"
        Image.new("RGB", (500, 250), "white").save(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), str(self.path), *args], capture_output=True, text=True)

    def test_unicode_and_no_source_mutation(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).digest()
        p = self.run_cli("--line", "文字行", "50", "25", "450", "75")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["rows"][0]["label"], "文字行")
        self.assertEqual(r["target_exact_aspect_ratio"], "2:1")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_crop_is_target_local(self) -> None:
        p = self.run_cli("--crop", "100", "50", "400", "200", "--line", "a", "30", "15", "270", "45")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["target_size_px_wh"], [300, 150])
        self.assertEqual(r["rows"][0]["bbox_target_percent_ltrb"], [10, 10, 90, 30])

    def test_exif_orientation(self) -> None:
        self.path = self.path.with_suffix(".jpg")
        im = Image.new("RGB", (300, 150), "white")
        exif = Image.Exif()
        exif[274] = 6
        im.save(self.path, exif=exif)
        p = self.run_cli("--line", "a", "15", "30", "135", "90")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["target_size_px_wh"], [150, 300])
        self.assertEqual(r["rows"][0]["bbox_target_percent_ltrb"], [10, 10, 90, 30])

    def test_invalid_cli_fails_cleanly(self) -> None:
        p = self.run_cli("--line", "a", "0", "0", "900", "10")
        self.assertEqual(p.returncode, 2)
        self.assertIn("typography_probe", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_no_lines_is_not_automatic_recognition(self) -> None:
        p = self.run_cli()
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
