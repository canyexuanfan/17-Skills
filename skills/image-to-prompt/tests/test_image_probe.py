"""Deterministic helper tests only; these do not test image-generation fidelity."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image_probe.py"
SPEC = importlib.util.spec_from_file_location("image_probe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ImageProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def save(self, name: str = "image.png", size: tuple[int, int] = (300, 400)) -> Path:
        path = self.root / name
        Image.new("RGB", size, "white").save(path)
        return path

    def test_portrait_exact_ratio(self) -> None:
        result = MODULE.probe_image(self.save())
        self.assertEqual(result["target"]["exact_aspect_ratio"], "3:4")
        self.assertEqual(result["target"]["orientation"], "portrait")

    def test_unusual_ratio_not_snapped_to_standard(self) -> None:
        result = MODULE.probe_image(self.save(size=(1000, 1330)))
        self.assertEqual(result["target"]["exact_aspect_ratio"], "100:133")

    def test_crop_changes_target_not_source(self) -> None:
        result = MODULE.probe_image(self.save(size=(500, 500)), crop=(50, 50, 350, 450))
        self.assertEqual(result["source"]["stored_size"]["exact_aspect_ratio"], "1:1")
        self.assertEqual(result["target"]["exact_aspect_ratio"], "3:4")
        self.assertTrue(result["target"]["crop_was_explicitly_supplied"])

    def test_exif_rotation(self) -> None:
        path = self.root / "rotated.jpg"
        image = Image.new("RGB", (400, 300), "white")
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, exif=exif)
        result = MODULE.probe_image(path)
        self.assertEqual(result["source"]["stored_size"]["exact_aspect_ratio"], "4:3")
        self.assertEqual(result["target"]["exact_aspect_ratio"], "3:4")
        self.assertTrue(result["source"]["exif_transform_applied"])

    def test_alpha_statistics_and_bbox(self) -> None:
        path = self.root / "alpha.png"
        image = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
        image.putpixel((4, 5), (0, 0, 255, 255))
        image.putpixel((5, 5), (0, 255, 0, 128))
        image.save(path)
        alpha = MODULE.probe_image(path)["target"]["alpha"]
        self.assertEqual(alpha["fully_transparent_pixels"], 98)
        self.assertEqual(alpha["partially_transparent_pixels"], 1)
        self.assertEqual(alpha["fully_opaque_pixels"], 1)
        self.assertEqual(alpha["visible_bbox_target_px_ltrb_exclusive"], (4, 5, 6, 6))

    def test_palette_excludes_hidden_rgb(self) -> None:
        path = self.root / "hidden.png"
        image = Image.new("RGBA", (20, 20), (255, 0, 0, 0))
        image.putpixel((5, 5), (0, 0, 255, 255))
        image.save(path)
        colors = MODULE.probe_image(path, palette_colors=3)["target"]["quantized_palette"]["colors"]
        self.assertEqual([color["hex"] for color in colors], ["#0000FF"])

    def test_all_transparent_palette_is_empty(self) -> None:
        path = self.root / "empty.png"
        Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(path)
        result = MODULE.probe_image(path, palette_colors=3)["target"]
        self.assertEqual(result["quantized_palette"]["colors"], [])
        self.assertIsNone(result["alpha"]["visible_bbox_target_px_ltrb_exclusive"])

    def test_checkerboard_pixels_remain_opaque(self) -> None:
        path = self.root / "checker.png"
        image = Image.new("RGB", (100, 100), "white")
        draw = ImageDraw.Draw(image)
        for x in range(0, 100, 10):
            for y in range(0, 100, 10):
                if (x + y) // 10 % 2:
                    draw.rectangle((x, y, x + 9, y + 9), fill=(180, 180, 180))
        image.save(path)
        self.assertFalse(MODULE.probe_image(path)["target"]["alpha"]["has_transparent_or_translucent_pixels"])

    def test_palette_transparency_mode(self) -> None:
        path = self.root / "indexed.png"
        image = Image.new("P", (10, 10), 0)
        image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        image.putpixel((4, 4), 1)
        image.save(path, transparency=0)
        alpha = MODULE.probe_image(path)["target"]["alpha"]
        self.assertEqual(alpha["fully_transparent_pixels"], 99)
        self.assertEqual(alpha["fully_opaque_pixels"], 1)

    def test_invalid_crops(self) -> None:
        path = self.save()
        for crop in [(-1, 0, 10, 10), (20, 0, 10, 10), (0, 0, 301, 400), (0, 0, 0, 0)]:
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                MODULE.probe_image(path, crop=crop)

    def test_resource_limit(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.probe_image(self.save(), max_pixels=100)

    def test_invalid_palette_size(self) -> None:
        for size in [-1, 17]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                MODULE.probe_image(self.save(), palette_colors=size)

    def test_nonexistent_file(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.probe_image(self.root / "not-here.png")

    def test_cli_json_and_unicode_path(self) -> None:
        path = self.save(name="中文 图片.png")
        process = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["source"]["filename"], "中文 图片.png")

    def test_input_not_modified(self) -> None:
        path = self.save()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        MODULE.probe_image(path, crop=(0, 0, 200, 200), palette_colors=4)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_animated_file_reports_frame_zero(self) -> None:
        path = self.root / "animated.gif"
        frames = [Image.new("RGB", (40, 40), "red"), Image.new("RGB", (40, 40), "blue")]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
        source = MODULE.probe_image(path)["source"]
        self.assertEqual(source["frame_count"], 2)
        self.assertEqual(source["frame_used_zero_based"], 0)

    def test_corrupt_file_cli_error(self) -> None:
        path = self.root / "bad.png"
        path.write_bytes(b"not an image")
        process = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn("image_probe:", process.stderr)
        self.assertEqual(process.stdout, "")

    def test_no_palette_by_default(self) -> None:
        self.assertNotIn("quantized_palette", MODULE.probe_image(self.save())["target"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
