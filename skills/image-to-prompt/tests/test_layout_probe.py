"""Synthetic arithmetic/CLI tests; not visual recognition or generation tests."""
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
from layout_probe import MAX_ANNOTATIONS, normalize_layout  # noqa: E402

SCRIPT = ROOT / "scripts" / "layout_probe.py"


class LayoutArithmeticTests(unittest.TestCase):
    def test_box_and_point_percentages(self) -> None:
        result = normalize_layout(600, 300, [("region", (60, 30, 360, 180))], [("anchor", (450, 225))])
        box = result["boxes"][0]
        self.assertEqual(box["bbox_target_percent_ltrb"], [10, 10, 60, 60])
        self.assertEqual(box["center_target_percent_xy"], [35, 35])
        self.assertEqual(box["size_target_percent_wh"], [50, 50])
        self.assertEqual(box["area_percent_of_target"], 25)
        self.assertEqual(result["points"][0]["point_target_percent_xy"], [75, 75])
        self.assertFalse(result["semantic_recognition_performed"])

    def test_full_box_and_boundary_points(self) -> None:
        result = normalize_layout(80, 40, [("full", (0, 0, 80, 40))], [("edge", (80, 40))])
        self.assertEqual(result["boxes"][0]["area_percent_of_target"], 100)
        self.assertEqual(result["points"][0]["point_target_percent_xy"], [100, 100])

    def test_overlap_and_nesting_allowed(self) -> None:
        result = normalize_layout(100, 100, [("outer", (0, 0, 100, 100)), ("inner", (20, 20, 80, 80))])
        self.assertGreater(sum(box["area_percent_of_target"] for box in result["boxes"]), 100)

    def test_fractional_coordinates(self) -> None:
        result = normalize_layout(100, 100, points=[("p", (10.5, 20.25))])
        self.assertEqual(result["points"][0]["point_target_percent_xy"], [10.5, 20.25])

    def test_invalid_dimensions(self) -> None:
        for width, height in [(0, 50), (-1, 50), (10.5, 50), (True, 50)]:
            with self.subTest(width=width), self.assertRaises(ValueError):
                normalize_layout(width, height, points=[("p", (0, 0))])

    def test_invalid_boxes(self) -> None:
        for box in [(-1, 0, 5, 5), (5, 0, 1, 5), (0, 0, 0, 5), (0, 0, 101, 5), (0, 0, 5)]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                normalize_layout(100, 100, [("bad", box)])

    def test_invalid_points(self) -> None:
        for point in [(-1, 5), (101, 5), (5, 101), (5,), (float("nan"), 5), (float("inf"), 5), (True, 0), ("1", 0)]:
            with self.subTest(point=point), self.assertRaises(ValueError):
                normalize_layout(100, 100, points=[("bad", point)])

    def test_duplicate_and_invalid_labels(self) -> None:
        with self.assertRaises(ValueError):
            normalize_layout(100, 100, [("a", (0, 0, 10, 10))], [(" a ", (20, 20))])
        for label in ["", "   ", "a\nb", "x" * 81]:
            with self.subTest(label=label), self.assertRaises(ValueError):
                normalize_layout(100, 100, points=[(label, (10, 10))])

    def test_no_annotations(self) -> None:
        with self.assertRaises(ValueError):
            normalize_layout(100, 100)

    def test_annotation_limit(self) -> None:
        with self.assertRaises(ValueError):
            normalize_layout(100, 100, points=((str(i), (0, 0)) for i in range(MAX_ANNOTATIONS + 1)))


class LayoutCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "几何 样本.png"
        Image.new("RGB", (600, 300), "white").save(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, path: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), str(path or self.path), *args], capture_output=True, text=True)

    def test_cli_and_unicode(self) -> None:
        process = self.run_cli("--box", "区域", "60", "30", "360", "180", "--point", "锚点", "450", "225")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["boxes"][0]["label"], "区域")
        self.assertEqual(result["target_exact_aspect_ratio"], "2:1")

    def test_crop_uses_target_local_coordinates(self) -> None:
        process = self.run_cli("--crop", "100", "50", "500", "250", "--point", "mid", "200", "100")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["target_size_px_wh"], [400, 200])
        self.assertEqual(result["points"][0]["point_target_percent_xy"], [50, 50])

    def test_exif_orientation(self) -> None:
        path = self.root / "oriented.jpg"
        image = Image.new("RGB", (600, 300), "white")
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, exif=exif)
        process = self.run_cli("--point", "mid", "150", "300", path=path)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["target_size_px_wh"], [300, 600])
        self.assertEqual(result["points"][0]["point_target_percent_xy"], [50, 50])

    def test_input_is_unchanged(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        process = self.run_cli("--point", "p", "20", "30")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_invalid_cli_has_error_code(self) -> None:
        for args in [("--point", "p", "nan", "1"), ("--point", "p", "900", "1"), ()]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("layout_probe:", result.stderr)

    def test_missing_input_file(self) -> None:
        result = self.run_cli("--point", "p", "0", "0", path=self.root / "missing.png")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
