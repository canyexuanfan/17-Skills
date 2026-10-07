#!/usr/bin/env python3
"""Run a small, offline acceptance suite against the public CLI and text detector.

The fixtures are generated locally. No user images, network requests, model
weights, pytest, or image fonts are required. Template tests are deliberately
self-consistent regression cases, not evidence of general detection accuracy.

    python scripts/selftest.py
    python scripts/selftest.py --output-dir /path/to/test-results

By default, a temporary directory is removed when the suite finishes. The
optional output directory retains test_results.json and the generated fixtures.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


SKILL_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = SKILL_ROOT / "scripts" / "run.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar(output: Path, suffix: str) -> Path:
    return output.with_name(output.stem + suffix)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class Suite:
    def __init__(self, root: Path, timeout: float):
        self.root = root
        self.timeout = timeout
        self.results: list[dict] = []
        self.last_call: dict | None = None
        self.fixtures = root / "fixtures"
        self.outputs = root / "outputs"
        self.fixtures.mkdir(parents=True)
        self.outputs.mkdir(parents=True)
        self.width, self.height = 240, 144
        yy, xx = np.mgrid[:self.height, :self.width]
        self.clean = np.stack([
            35 + xx * 0.32 + 4 * np.sin(yy / 9.0),
            55 + yy * 0.42 + 3 * np.cos(xx / 13.0),
            95 + 14 * np.sin((xx + yy) / 19.0),
        ], axis=2).round().clip(0, 255).astype(np.uint8)
        mask_image = Image.new("L", (self.width, self.height), 0)
        draw = ImageDraw.Draw(mask_image)
        # Two vector glyphs avoid font availability or text-renderer variation.
        draw.line([(59, 47), (70, 89), (83, 63), (96, 89), (107, 47)],
                  fill=255, width=5)
        draw.line([(128, 89), (128, 47), (150, 72), (172, 47), (172, 89)],
                  fill=255, width=5)
        self.mask = np.asarray(mask_image, dtype=np.uint8) > 0
        self.mask_path = self.fixtures / "glyph-mask.png"
        mask_image.save(self.mask_path)
        self.marked = self.clean.copy()
        self.marked[self.mask] = (234, 240, 246)
        self.rgb_path = self.fixtures / "marked-rgb.png"
        Image.fromarray(self.marked).save(self.rgb_path)
        self.alpha = ((xx * 3 + yy * 5) % 256).astype(np.uint8)
        self.rgba_path = self.fixtures / "marked-rgba.png"
        Image.fromarray(np.dstack([self.marked, self.alpha])).save(self.rgba_path)
        self.protect = np.zeros_like(self.mask)
        self.protect[:, self.width // 2:] = True
        self.protect_path = self.fixtures / "protect-right-half.png"
        Image.fromarray(self.protect.astype(np.uint8) * 255).save(self.protect_path)

    def execute(self, arguments: list, expected: int = 0) -> subprocess.CompletedProcess:
        command = [sys.executable, str(ENTRYPOINT)] + [str(a) for a in arguments]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUTF8"] = "1"
        self.last_call = {"arguments": [str(a) for a in arguments]}
        completed = subprocess.run(
            command, cwd=str(SKILL_ROOT), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=self.timeout,
            check=False,
        )
        self.last_call.update({
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-3000:],
            "stderr": completed.stderr[-3000:],
        })
        require(completed.returncode == expected,
                f"CLI exit code {completed.returncode}; expected {expected}.")
        return completed

    def read_report(self, path: Path) -> dict:
        require(path.is_file(), f"Expected report is missing: {path.name}")
        report = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(report, dict), "Report must be a JSON object.")
        return report

    def assert_pixels(self, source: Path, output: Path, allowed: np.ndarray,
                      source_hash: str, protected: np.ndarray | None = None) -> dict:
        require(output.is_file(), f"Output is missing: {output.name}")
        with Image.open(source) as image:
            source_mode = image.mode
            before = np.asarray(image).copy()
            size = image.size
        with Image.open(output) as image:
            output_mode = image.mode
            after = np.asarray(image).copy()
            require(image.format == "PNG", "Result must be lossless PNG.")
        require(after.shape == before.shape, "Image dimensions or channel count changed.")
        require(output_mode == source_mode, "RGB/RGBA mode changed.")
        changed = np.any(after != before, axis=2)
        outside = int(np.count_nonzero(changed & ~allowed))
        require(outside == 0, f"{outside} pixels changed outside the allowed mask.")
        require(np.any(changed & allowed), "No permitted pixels were repaired.")
        require(digest(source) == source_hash, "The input file was modified.")
        alpha_changed = 0
        if source_mode == "RGBA":
            alpha_changed = int(np.count_nonzero(after[:, :, 3] != before[:, :, 3]))
            require(alpha_changed == 0, "The alpha channel changed.")
        if protected is not None:
            require(np.array_equal(before[protected], after[protected]),
                    "Protected pixels changed.")
        report = self.read_report(sidecar(output, ".report.json"))
        require({"status", "method", "validation"}.issubset(report),
                "Report is missing required top-level fields.")
        require(report["status"] != "error" and
                not str(report["status"]).startswith("skipped"),
                "The repair was unexpectedly skipped or failed.")
        validation = report["validation"]
        require(validation.get("outside_mask_changed_pixels") == outside,
                "Reported outside-mask changes disagree with decoded pixels.")
        require(validation.get("alpha_changed_pixels") == alpha_changed,
                "Reported alpha changes disagree with decoded pixels.")
        require(validation.get("input_unchanged") is True,
                "Report did not verify an unchanged input.")
        require(list(validation.get("output_size", [])) == list(size),
                "Reported output size is incorrect.")
        mask_path = sidecar(output, ".mask.png")
        require(mask_path.is_file(), "The allowed-mask sidecar is missing.")
        with Image.open(mask_path) as image:
            reported_mask = np.asarray(image.convert("L")) > 0
        require(reported_mask.shape == allowed.shape, "Saved mask has the wrong dimensions.")
        require(not np.any(reported_mask & ~allowed),
                "Saved mask expands beyond the explicitly permitted pixels.")
        require(not np.any(changed & ~reported_mask),
                "Saved mask omits pixels that were modified.")
        return {"outside_mask_changed_pixels": outside,
                "alpha_changed_pixels": alpha_changed,
                "changed_pixels": int(np.count_nonzero(changed)),
                "input_unchanged": True, "mode": output_mode, "size": list(size)}

    def error_report(self, path: Path, tokens: tuple[str, ...] = ()) -> dict:
        report = self.read_report(path)
        require(report.get("status") == "error", "Expected an explicit error status.")
        detail = report.get("error") or report.get("message") or report.get("errors")
        require(bool(detail), "Error report contains no explanation.")
        detail_text = json.dumps(detail, ensure_ascii=False).lower()
        if tokens:
            require(any(token.lower() in detail_text for token in tokens),
                    "Error explanation does not identify the invalid input.")
        return {"status": "error", "error": detail}

    def classical_rgb(self, method: str) -> dict:
        output = self.outputs / f"rgb-{method}.png"
        before_hash = digest(self.rgb_path)
        arguments = ["--input", self.rgb_path, "--output", output,
                     "--profile", "none", "--mask", self.mask_path,
                     "--method", method, "--radius", "3", "--dilate", "0",
                     "--strict-classical"]
        if method == "ns":
            arguments.append("--no-preview")
        self.execute(arguments)
        metrics = self.assert_pixels(self.rgb_path, output, self.mask, before_hash)
        comparison = sidecar(output, ".comparison.png")
        require(comparison.exists() == (method == "telea"),
                "Default preview / --no-preview behavior is incorrect.")
        if comparison.exists():
            with Image.open(comparison) as image:
                image.verify()
        return metrics

    def protected_rgba(self) -> dict:
        output = self.outputs / "rgba-protected.png"
        before_hash = digest(self.rgba_path)
        self.execute(["--input", self.rgba_path, "--output", output,
                      "--profile", "none", "--mask", self.mask_path,
                      "--protect-mask", self.protect_path, "--method", "telea",
                      "--no-preview", "--strict-classical"])
        return self.assert_pixels(self.rgba_path, output, self.mask & ~self.protect,
                                  before_hash, protected=self.protect)

    def flat_fill(self) -> dict:
        clean = np.full_like(self.clean, (20, 31, 43))
        marked = clean.copy()
        marked[self.mask] = (240, 246, 252)
        source = self.fixtures / "flat-marked.png"
        output = self.outputs / "flat-restored.png"
        Image.fromarray(marked).save(source)
        before_hash = digest(source)
        self.execute(["--input", source, "--output", output,
                      "--profile", "none", "--mask", self.mask_path,
                      "--method", "fill", "--no-preview"])
        metrics = self.assert_pixels(source, output, self.mask, before_hash)
        with Image.open(output) as image:
            restored = np.asarray(image).astype(np.float32)
        error = np.abs(restored[self.mask] - clean[self.mask].astype(np.float32))
        require(float(error.max()) <= 2.0, "Flat-background recovery exceeds 2/255 error.")
        metrics["synthetic_masked_mae_255"] = float(error.mean())
        return metrics

    def no_match(self) -> dict:
        source = self.fixtures / "no-watermark.png"
        output = self.outputs / "no-match.png"
        Image.new("RGB", (1920, 1072), (64, 85, 100)).save(source)
        before_hash = digest(source)
        self.execute(["--input", source, "--output", output,
                      "--profile", "auto", "--method", "auto", "--no-preview"])
        report = self.read_report(sidecar(output, ".report.json"))
        require(report.get("status") == "skipped_no_match",
                "A watermark-free image was not skipped.")
        require(digest(source) == before_hash, "The negative input changed.")
        if output.exists():
            with Image.open(source) as original, Image.open(output) as result:
                require(np.array_equal(np.asarray(original), np.asarray(result)),
                        "A skipped image was modified.")
        return {"status": "skipped_no_match", "input_unchanged": True,
                "scope": "One uniform negative fixture; not a false-positive rate estimate."}

    def input_overwrite_rejected(self) -> dict:
        source = self.fixtures / "overwrite-guard.png"
        Image.fromarray(self.marked).save(source)
        before_hash = digest(source)
        report = self.outputs / "overwrite-error.json"
        self.execute(["--input", source, "--output", source, "--overwrite",
                      "--profile", "none", "--mask", self.mask_path,
                      "--method", "telea", "--report", report, "--no-preview"],
                     expected=2)
        require(digest(source) == before_hash, "Overwrite guard failed to preserve the input.")
        return self.error_report(report, ("input", "source", "same", "原图", "输入", "覆盖"))

    def invalid_mask(self) -> dict:
        mask = self.fixtures / "wrong-size-mask.png"
        Image.new("L", (self.width // 2, self.height), 255).save(mask)
        output = self.outputs / "invalid-mask.png"
        report = self.outputs / "invalid-mask-error.json"
        before_hash = digest(self.rgb_path)
        self.execute(["--input", self.rgb_path, "--output", output,
                      "--profile", "none", "--mask", mask, "--method", "telea",
                      "--report", report, "--no-preview"], expected=2)
        require(not output.exists(), "An invalid mask produced a result image.")
        require(digest(self.rgb_path) == before_hash, "Input changed after an invalid mask.")
        return self.error_report(report, ("mask", "掩膜", "掩码"))

    def invalid_radius(self) -> dict:
        output = self.outputs / "invalid-radius.png"
        report = self.outputs / "invalid-radius-error.json"
        self.execute(["--input", self.rgb_path, "--output", output,
                      "--profile", "none", "--mask", self.mask_path,
                      "--method", "telea", "--radius", "nan", "--report", report,
                      "--no-preview"], expected=2)
        require(not output.exists(), "A non-finite radius produced a result image.")
        return self.error_report(report, ("radius", "finite", "半径", "有限"))

    def builtin_profile(self) -> tuple[dict, np.ndarray]:
        profile_path = SKILL_ROOT / "assets" / "workbuddy-corner-v1.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        with Image.open(profile_path.parent / profile["template"]) as image:
            alpha_u8 = np.asarray(image.convert("L")).copy()
        return profile, alpha_u8

    def template_fixture(self) -> dict:
        if hasattr(self, "_template_fixture"):
            return self._template_fixture
        profile, alpha_u8 = self.builtin_profile()
        width, height = profile["reference_canvas"]
        yy, xx = np.indices((height, width), dtype=np.float32)
        clean = np.stack([
            80 + 28 * np.sin(xx / 13) + 10 * np.cos(yy / 11),
            95 + 25 * np.sin((xx + yy) / 29),
            115 + 20 * np.cos(xx / 17) + 8 * np.sin(yy / 23),
        ], axis=2).round().clip(0, 255).astype(np.uint8)
        x0, y0, x1, y1 = profile["reference_bbox"]
        x0, x1, y0, y1 = x0 + 2, x1 + 2, y0 - 3, y1 - 3
        alpha = alpha_u8.astype(np.float32) / 255.0
        alpha[alpha <= profile["alpha_support_threshold"]] = 0
        allowed = np.zeros((height, width), dtype=bool)
        allowed[y0:y1, x0:x1] = alpha > 0
        marked = clean.copy()
        foreground = np.asarray(profile["foreground_rgb"], dtype=np.float32)
        marked[y0:y1, x0:x1] = np.rint(
            (1 - alpha[:, :, None]) * clean[y0:y1, x0:x1] +
            alpha[:, :, None] * foreground).clip(0, 255).astype(np.uint8)
        source = self.fixtures / "shifted-template-marked.png"
        Image.fromarray(marked).save(source)
        Image.fromarray(clean).save(self.fixtures / "shifted-template-clean-truth.png")
        self._template_fixture = {"source": source, "clean": clean,
                                  "marked": marked, "allowed": allowed}
        return self._template_fixture

    def shifted_template_inverse(self) -> dict:
        fixture = self.template_fixture()
        source, clean, allowed = fixture["source"], fixture["clean"], fixture["allowed"]
        output = self.outputs / "shifted-template-inverse.png"
        source_hash = digest(source)
        self.execute(["--input", source, "--output", output,
                      "--profile", "workbuddy", "--method", "inverse",
                      "--strict-classical", "--no-preview"])
        metrics = self.assert_pixels(source, output, allowed, source_hash)
        with Image.open(output) as image:
            restored = np.asarray(image).astype(np.float32)
        error = np.abs(restored[allowed] - clean[allowed].astype(np.float32))
        require(float(error.mean()) <= 1.5,
                "The matched synthetic inverse has excessive reconstruction error.")
        metrics.update({"synthetic_masked_mae_255": float(error.mean()),
                        "integer_shift_xy": [2, -3],
                        "scope": "Same-template synthetic regression; not general detection or real-image quality evidence."})
        return metrics

    def invalid_profile_number(self) -> dict:
        profile, alpha = self.builtin_profile()
        fixture_dir = self.fixtures / "nonfinite-profile"
        fixture_dir.mkdir()
        profile["foreground_rgb"][0] = float("nan")
        Image.fromarray(alpha).save(fixture_dir / profile["template"])
        profile_path = fixture_dir / "nonfinite.json"
        # Deliberately write NaN to verify that non-standard/non-finite JSON is rejected.
        profile_path.write_text(json.dumps(profile, allow_nan=True), encoding="utf-8")
        output = self.outputs / "invalid-profile-number.png"
        report = self.outputs / "invalid-profile-number-error.json"
        self.execute(["--input", self.rgb_path, "--output", output,
                      "--profile", profile_path, "--method", "inverse",
                      "--report", report, "--no-preview"], expected=2)
        require(not output.exists(), "A non-finite profile produced a result image.")
        return self.error_report(report, ("nan", "finite", "json", "有限"))

    def invalid_alpha_template(self) -> dict:
        profile, alpha = self.builtin_profile()
        fixture_dir = self.fixtures / "opaque-alpha-profile"
        fixture_dir.mkdir()
        # A=1 makes inverse compositing undefined. Keep the rest of the profile valid.
        invalid_alpha = alpha.copy()
        invalid_alpha[invalid_alpha > 0] = 255
        Image.fromarray(invalid_alpha).save(fixture_dir / profile["template"])
        profile_path = fixture_dir / "opaque-alpha.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        output = self.outputs / "invalid-alpha.png"
        report = self.outputs / "invalid-alpha-error.json"
        self.execute(["--input", self.rgb_path, "--output", output,
                      "--profile", profile_path, "--method", "inverse",
                      "--report", report, "--no-preview"], expected=2)
        require(not output.exists(), "An opaque alpha template produced a result image.")
        return self.error_report(report, ("alpha", "opaque", "透明", "模板"))

    def result_path(self, raw: str, output_dir: Path) -> Path:
        path = Path(raw)
        choices = [path] if path.is_absolute() else [
            output_dir / path, self.root / path, output_dir / path.name]
        for candidate in choices:
            if candidate.is_file():
                return candidate.resolve()
        raise AssertionError(f"Batch result does not resolve to a file: {raw}")

    def batch_collisions(self) -> dict:
        fixture = self.template_fixture()
        inputs = self.fixtures / "same-stem-batch"
        outputs = self.outputs / "same-stem-batch"
        inputs.mkdir()
        first, second = inputs / "same.png", inputs / "same.bmp"
        Image.fromarray(fixture["marked"]).save(first)
        Image.fromarray(fixture["marked"]).save(second)
        hashes = {str(path.resolve()): digest(path) for path in (first, second)}
        self.execute(["--input-dir", inputs, "--output-dir", outputs,
                      "--profile", "workbuddy", "--method", "inverse",
                      "--max-workers", "2", "--no-preview"])
        summary = self.read_report(outputs / "summary.json")
        results = summary.get("results", [])
        require(len(results) == 2, "Batch summary does not contain both same-stem inputs.")
        paths = []
        for result in results:
            require({"status", "input", "output", "report"}.issubset(result),
                    "Batch result is missing required fields.")
            output = self.result_path(result["output"], outputs)
            paths.append(output)
            source = self.result_path(result["input"], inputs)
            require(str(source) in hashes, "Batch output is mapped to an unexpected source.")
            self.assert_pixels(source, output, fixture["allowed"], hashes[str(source)])
            self.result_path(result["report"], outputs)
        require(len(set(paths)) == 2, "Same-stem inputs overwrote one another.")
        return {"input_count": 2, "distinct_output_count": 2,
                "outside_mask_changed_pixels": 0, "input_unchanged": True}

    def batch_mixed_errors(self) -> dict:
        fixture = self.template_fixture()
        inputs = self.fixtures / "mixed-batch"
        outputs = self.outputs / "mixed-batch"
        inputs.mkdir()
        source = inputs / "valid.png"
        invalid = inputs / "invalid.png"
        Image.fromarray(fixture["marked"]).save(source)
        invalid.write_bytes(b"This is deliberately not a PNG image.\n")
        hashes = {path.name: digest(path) for path in (source, invalid)}
        self.execute(["--input-dir", inputs, "--output-dir", outputs,
                      "--profile", "workbuddy", "--method", "inverse",
                      "--max-workers", "2", "--no-preview"],
                     expected=1)
        summary = self.read_report(outputs / "summary.json")
        results = summary.get("results", [])
        require(len(results) == 2, "Mixed batch lost an input from the summary.")
        errors = [result for result in results if result.get("status") == "error"]
        successes = [result for result in results if result.get("status") != "error"
                     and not str(result.get("status", "")).startswith("skipped")]
        require(len(errors) == 1 and len(successes) == 1,
                "Mixed batch did not retain both a success and an error.")
        for result in results:
            require({"status", "input", "output", "report"}.issubset(result),
                    "A batch error omitted required result fields.")
        error_path = self.result_path(errors[0]["report"], outputs)
        self.error_report(error_path)
        output = self.result_path(successes[0]["output"], outputs)
        self.assert_pixels(source, output, fixture["allowed"], hashes[source.name])
        require(all(digest(path) == hashes[path.name] for path in (source, invalid)),
                "A mixed-batch input changed.")
        return {"success_count": 1, "error_count": 1, "batch_exit_code": 1}

    def text_fixture(self) -> dict:
        if hasattr(self, "_text_fixture"):
            return self._text_fixture
        width, height = 420, 300
        yy, xx = np.indices((height, width), dtype=np.float32)
        grain = 4 * np.sin(yy / 2.9 + np.sin(xx / 13.0))
        clean = np.stack([
            112 + .15 * xx + grain + 2 * np.cos(xx / 6.0),
            77 + .11 * xx + grain,
            47 + .07 * xx + grain,
        ], axis=2).round().clip(0, 255).astype(np.uint8)
        # Plain vector "TEST" lettering has no font, brand, or template dependency.
        paths = [
            [(248, 238), (272, 238)], [(260, 238), (260, 270)],
            [(307, 238), (283, 238), (283, 270), (307, 270)],
            [(283, 254), (302, 254)],
            [(342, 238), (318, 238), (318, 254), (342, 254),
             (342, 270), (318, 270)],
            [(353, 238), (377, 238)], [(365, 238), (365, 270)],
        ]
        truth_image = Image.new("L", (width, height), 0)
        marked_image = Image.fromarray(clean)
        mask_draw, image_draw = ImageDraw.Draw(truth_image), ImageDraw.Draw(marked_image)
        for points in paths:
            mask_draw.line(points, fill=255, width=9)
            image_draw.line(points, fill=(75, 75, 75), width=9)
        for points in paths:
            image_draw.line(points, fill=(235, 235, 235), width=5)
        source = self.fixtures / "unbranded-outlined-text.png"
        truth_path = self.fixtures / "unbranded-text-truth-mask.png"
        marked_image.save(source)
        truth_image.save(truth_path)
        truth = np.asarray(truth_image) > 0
        roi = (240, 230, 386, 279)
        self._text_fixture = {
            "source": source, "clean": clean, "marked": np.asarray(marked_image).copy(),
            "truth": truth, "truth_path": truth_path, "roi": roi,
            "roi_argument": ",".join(str(v) for v in roi),
        }
        return self._text_fixture

    def text_roi_auto_exemplar(self) -> dict:
        fixture = self.text_fixture()
        source, truth = fixture["source"], fixture["truth"]
        output = self.outputs / "text-roi-auto.png"
        source_hash = digest(source)
        self.execute(["--input", source, "--output", output,
                      "--profile", "none", "--text-roi", fixture["roi_argument"],
                      "--method", "auto", "--quality", "fast", "--strict-classical"])
        # Artifact validation remains independent when a later quality assertion fails.
        self._text_output = output
        selection_path = sidecar(output, ".selection.png")
        require(selection_path.is_file(), "Text selection was not saved.")
        with Image.open(selection_path) as image:
            selection = np.asarray(image.convert("L")) > 0
        roi_mask = np.zeros(truth.shape, dtype=bool)
        x0, y0, x1, y1 = fixture["roi"]
        roi_mask[y0:y1, x0:x1] = True
        require(not np.any(selection & ~roi_mask), "Text mask escaped the requested ROI.")
        recall = float(np.count_nonzero(selection & truth) / np.count_nonzero(truth))
        require(recall >= .95, "Text mask missed more than 5% of the known outlined glyphs.")
        require(float(selection.sum() / roi_mask.sum()) < .85,
                "Text selection degenerated into nearly complete rectangular erasure.")
        metrics = self.assert_pixels(source, output, selection, source_hash)
        report = self.read_report(sidecar(output, ".report.json"))
        require(report.get("method") == "exemplar", "Unknown textured text did not select exemplar.")
        require(report.get("status") == "needs_review", "Estimated text repair lacks review status.")
        require(report.get("detection", {}).get("kind") == "pale_text_region",
                "A generic text mask was confused with a calibrated template.")
        require(report.get("true_original_accuracy") is None,
                "Report invented original-image accuracy.")
        metrics.update({"method": "exemplar", "known_glyph_recall": recall,
                        "selection_pixels": int(selection.sum()),
                        "scope": "One vector-letter fixture; not universal text-detection evidence."})
        return metrics

    def outlined_geometry_fixture(self, dark_object: bool = False) -> dict:
        """Draw known glyph support, including a descender, without font assets."""
        width, height = 310, 190
        top, baseline = 64, 124
        paths = [
            [(50, top), (76, top), (50, baseline), (76, baseline)],
            [(108, baseline), (108, top), (138, baseline), (138, top)],
            [(172, top), (172, baseline - 4), (176, baseline),
             (196, baseline), (200, baseline - 4), (200, top)],
            [(228, top), (260, top)],
            [(252, top), (252, baseline - 4), (248, baseline + 4),
             (238, baseline + 4), (232, baseline - 1)],
        ]
        support_image = Image.new("L", (width, height), 0)
        core_image = Image.new("L", (width, height), 0)
        support_draw, core_draw = ImageDraw.Draw(support_image), ImageDraw.Draw(core_image)
        for points in paths:
            support_draw.line(points, fill=255, width=9)
            core_draw.line(points, fill=255, width=5)
        truth = np.asarray(support_image) > 0
        core = np.asarray(core_image) > 0
        clean = np.full((height, width, 3), (187, 147, 104), dtype=np.uint8)
        object_mask = np.zeros(truth.shape, dtype=bool)
        # A scene object starts immediately beside the final glyph, with no
        # watermark on it. Its support comes from drawing geometry, not detection.
        object_edge = int(np.nonzero(truth)[1].max()) + 1
        if dark_object:
            object_mask[:, object_edge:] = True
            clean[object_mask] = (40, 31, 25)
        marked = clean.copy()
        marked[truth] = (107, 101, 95)
        marked[core] = (238, 238, 238)
        name = "outlined-object-boundary" if dark_object else "outlined-descender"
        Image.fromarray(marked).save(self.fixtures / f"{name}.png")
        Image.fromarray(clean).save(self.fixtures / f"{name}-clean-truth.png")
        support_image.save(self.fixtures / f"{name}-glyph-truth.png")
        if dark_object:
            Image.fromarray(object_mask.astype(np.uint8) * 255).save(
                self.fixtures / f"{name}-object-truth.png")
        yy = np.indices(truth.shape)[0]
        return {"name": name, "marked": marked, "truth": truth, "core": core,
                "tail": truth & (yy >= baseline + 4), "object_mask": object_mask,
                "object_edge": object_edge, "roi": (38, 49, 284, 145)}

    def geometry_selection(self, fixture: dict) -> np.ndarray:
        # Exercise the public detector. Expected pixels are supplied only by the
        # independently drawn fixture; do not reconstruct internal thresholds.
        from text_mask import find_text_watermark

        found = find_text_watermark(fixture["marked"], roi=fixture["roi"])
        require(found.get("found") is True, "Known vector lettering was not detected.")
        selection = np.zeros(fixture["truth"].shape, dtype=bool)
        x0, y0, x1, y1 = found["bbox"]
        selection[y0:y1, x0:x1] = found["write_mask"]
        roi_mask = np.zeros_like(selection)
        x0, y0, x1, y1 = fixture["roi"]
        roi_mask[y0:y1, x0:x1] = True
        require(not np.any(selection & ~roi_mask), "Geometry selection escaped its ROI.")
        require(float(selection.sum() / roi_mask.sum()) < .85,
                "Geometry selection became nearly complete rectangular erasure.")
        Image.fromarray(selection.astype(np.uint8) * 255).save(
            self.outputs / f"{fixture['name']}.selection.png")
        return selection

    def text_descender_outline_coverage(self) -> dict:
        fixture = self.outlined_geometry_fixture()
        selection = self.geometry_selection(fixture)
        truth, tail = fixture["truth"], fixture["tail"]
        dark_tail = tail & ~fixture["core"]
        require(np.any(dark_tail), "The fixture lacks a known dark descender outline.")
        missed_tail = int(np.count_nonzero(tail & ~selection))
        require(missed_tail == 0,
                "Text selection clipped known descender or lower-outline pixels.")
        recall = float(np.count_nonzero(selection & truth) / np.count_nonzero(truth))
        require(recall >= .99, "Text selection missed known vector glyph support.")
        return {"known_glyph_recall": recall, "known_tail_pixels": int(tail.sum()),
                "known_dark_tail_pixels": int(dark_tail.sum()),
                "missed_tail_pixels": missed_tail,
                "scope": "Vector descender and outline regression; no hidden-background claim."}

    def text_morphology_respects_object_edge(self) -> dict:
        fixture = self.outlined_geometry_fixture(dark_object=True)
        selection = self.geometry_selection(fixture)
        truth, object_mask = fixture["truth"], fixture["object_mask"]
        require(not np.any(truth & object_mask), "Fixture lettering overlaps the scene object.")
        selected_object = int(np.count_nonzero(selection & object_mask))
        require(selected_object == 0,
                "Text selection crossed into the unmarked high-contrast scene object.")
        # Require the pale stroke immediately touching the edge to survive: a
        # detector that simply drops the adjacent glyph must not pass this test.
        edge_core = np.zeros_like(truth)
        contact_column = fixture["object_edge"] - 1
        edge_core[:, contact_column] = fixture["core"][:, contact_column]
        require(np.any(edge_core), "No pale glyph pixels touch the fixture object edge.")
        require(np.all(selection[edge_core]), "The edge-adjacent pale glyph was discarded.")
        recall = float(np.count_nonzero(selection & truth) / np.count_nonzero(truth))
        require(recall >= .99, "Object-edge protection discarded known glyph support.")
        return {"known_glyph_recall": recall, "selected_scene_object_pixels": selected_object,
                "edge_adjacent_pale_pixels_retained": int(edge_core.sum()),
                "scope": "Known vector lettering beside an unmarked scene edge; mask-only regression."}

    def exemplar_protected_donors(self) -> dict:
        fixture = self.text_fixture()
        truth = fixture["truth"]
        protect = np.zeros(truth.shape, dtype=bool)
        protect[230:279, 279:313] = True
        protect_path = self.fixtures / "exemplar-protected-letter.png"
        Image.fromarray(protect.astype(np.uint8) * 255).save(protect_path)
        allowed = truth & ~protect
        require(np.any(truth & protect), "Protection fixture missed all lettering.")
        results = []
        for index, color in enumerate(((235, 235, 235), (255, 0, 255))):
            marked = fixture["marked"].copy()
            # Only contaminated pixels inside protection differ between the inputs.
            marked[truth & protect] = color
            source = self.fixtures / f"protected-exclusion-{index}.png"
            output = self.outputs / f"protected-exclusion-{index}.png"
            Image.fromarray(marked).save(source)
            source_hash = digest(source)
            self.execute(["--input", source, "--output", output,
                          "--profile", "none", "--mask", fixture["truth_path"],
                          "--protect-mask", protect_path, "--method", "exemplar",
                          "--quality", "fast", "--no-preview"])
            self.assert_pixels(source, output, allowed, source_hash, protected=protect)
            report = self.read_report(sidecar(output, ".report.json"))
            selection_meta = report.get("selection_mask", {})
            require(selection_meta.get("source_excluded_pixels") == int(truth.sum()),
                    "Protection incorrectly removed contaminated pixels from donor exclusion.")
            require(selection_meta.get("pixels") == int(allowed.sum()),
                    "Selection does not respect protected pixels.")
            require(report.get("restoration", {}).get("exemplar", {}).get("source_policy")
                    == "intact_original_patches_only", "Exemplar donor policy is missing.")
            with Image.open(output) as image:
                results.append(np.asarray(image).copy())
        difference = np.abs(results[0][allowed].astype(np.int16) -
                            results[1][allowed].astype(np.int16))
        # OpenCV's float32 masked SSD may change a final rounding decision by one
        # code value even when contaminated samples have zero matching weight.
        # A copied protected mark would produce differences far beyond this limit.
        require(int(difference.max()) <= 1 and float(difference.mean()) <= .05,
                "Changing protected watermark colors materially changed repaired pixels; possible donor or matching leakage.")
        return {"protected_pixels_changed": 0, "outside_mask_changed_pixels": 0,
                "protected_input_variants": 2,
                "repaired_variant_max_channel_difference": int(difference.max()),
                "repaired_variant_mean_channel_difference": float(difference.mean()),
                "matching_quantization_tolerance_255": 1,
                "contaminated_source_excluded_pixels": int(truth.sum())}

    def consensus_cli_and_scope(self) -> dict:
        fixture = self.text_fixture()
        source = fixture["source"]
        output = self.outputs / "consensus-refined.png"
        source_hash = digest(source)
        self.execute(["--input", source, "--output", output, "--profile", "none",
                      "--mask", fixture["truth_path"], "--method", "exemplar",
                      "--quality", "fast", "--refine", "consensus", "--no-preview"])
        metrics = self.assert_pixels(source, output, fixture["truth"], source_hash)
        report = self.read_report(sidecar(output, ".report.json"))
        refinement = report.get("restoration", {}).get("refinement", {})
        require(refinement.get("method") == "structure_guided_original_donor_consensus",
                "Requested consensus refinement was silently skipped.")
        require(refinement.get("solver", {}).get("converged") is True,
                "Refinement did not report a converged gradient solve.")
        require(refinement.get("votes", 0) > 0,
                "Refinement completed without intact donor votes.")
        require(report.get("status") == "needs_review",
                "Approximate refinement incorrectly claims final accuracy.")
        invalid = self.outputs / "consensus-with-auto.png"
        error_path = self.outputs / "consensus-with-auto.json"
        self.execute(["--input", source, "--output", invalid, "--mask", fixture["truth_path"],
                      "--method", "auto", "--refine", "consensus", "--report", error_path],
                     expected=2)
        require(not invalid.exists(), "Incompatible refinement produced a result image.")
        self.error_report(error_path, ("refine", "exemplar"))
        metrics.update({"refinement": "consensus", "votes": refinement["votes"],
                        "solver_converged": True, "auto_refinement_rejected": True})
        return metrics

    def consensus_bounded_sources_and_sparse_support(self) -> dict:
        """A real distant donor strip must remain usable for thin local holes."""
        import consensus

        height, width = 108, 360
        yy, xx = np.indices((height, width), dtype=np.float32)
        grain = 1.2 * np.sin(.8 * yy + .05 * xx)
        clean = np.stack([155 + .08 * xx + grain, 121 + .06 * xx + grain,
                          87 + .04 * xx + grain], axis=-1).round().clip(0, 255).astype(np.uint8)
        mask = np.zeros((height, width), dtype=bool)
        mask[35:74, 276:280] = True
        mask[97, 328] = True  # A detached mark has no independently trusted patch context.
        marked = clean.copy()
        marked[mask] = (241, 241, 241)
        excluded = np.ones_like(mask)
        excluded[14:94, 138:168] = False  # A genuine, intact background strip.
        excluded[5:103, 271:287] = False  # Matching support, too narrow for a 19px donor.
        excluded |= mask
        initial = marked.copy()
        initial[mask] = (177, 138, 98)  # An approximation, never the clean truth.
        before = [a.copy() for a in (marked, mask, excluded, initial)]
        output, diagnostic = consensus.refine(
            marked, mask, initial=initial, source_exclusion=excluded)
        require(output.dtype == np.uint8 and output.shape == marked.shape,
                "The constrained donor result has wrong dimensions or dtype.")
        require(np.array_equal(output[~mask], marked[~mask]),
                "Constrained consensus modified pixels outside the exact mask.")
        require(all(np.array_equal(a, b) for a, b in zip(before, (marked, mask, excluded, initial))),
                "Constrained consensus mutated an input array.")
        require(diagnostic.get("covered_write_pixels") == int(mask.sum()),
                "Some explicitly selected pixels have no intact donor support.")
        require(diagnostic.get("bounded_context_search_centers", 0) > 0,
                "The distant valid donor strip was not reached.")
        require(diagnostic.get("low_support_propagated_centers", 0) > 0,
                "The detached mark was not covered by supported-field propagation.")
        require(diagnostic.get("solver", {}).get("converged") is True,
                "The constrained reconstruction did not converge.")
        Image.fromarray(marked).save(self.fixtures / "constrained-thin-marks.png")
        Image.fromarray(output).save(self.outputs / "constrained-thin-marks.png")
        return {"selection_pixels": int(mask.sum()), "outside_mask_changed_pixels": 0,
                "input_arrays_unchanged": True,
                "bounded_context_search_centers": diagnostic["bounded_context_search_centers"],
                "low_support_propagated_centers": diagnostic["low_support_propagated_centers"],
                "covered_write_pixels": diagnostic["covered_write_pixels"],
                "scope": "Distant intact sources and a detached low-evidence mark; not a visual-accuracy claim."}

    def consensus_rejects_padding_only_sources(self) -> dict:
        """Reflected pixels cannot invent a complete source patch in a tiny image."""
        import consensus

        rgb = np.full((17, 17, 3), (155, 119, 82), dtype=np.uint8)
        mask = np.zeros((17, 17), dtype=bool)
        mask[7:10, 7:10] = True
        marked = rgb.copy()
        marked[mask] = 240
        saved = marked.copy()
        try:
            consensus.refine(marked, mask, initial=rgb, source_exclusion=mask)
        except ValueError as error:
            require("no intact source patch" in str(error),
                    "Tiny-image source failure did not explain missing intact pixels.")
        else:
            require(False, "Reflected padding was accepted as a real intact source patch.")
        require(np.array_equal(marked, saved), "A failed source search changed its input.")
        return {"padding_only_sources_rejected": True, "input_unchanged": True}

    def selection_artifacts(self) -> dict:
        if not hasattr(self, "_text_output"):
            self.text_roi_auto_exemplar()
        output = self._text_output
        report = self.read_report(sidecar(output, ".report.json"))
        artifacts = report.get("artifacts", {})
        decoded = {}
        for key, suffix in (("selection", ".selection.png"),
                            ("selection_preview", ".selection-preview.png"),
                            ("mask", ".mask.png"), ("comparison", ".comparison.png")):
            path = sidecar(output, suffix)
            require(path.is_file(), f"Missing new artifact: {key}")
            require(Path(artifacts.get(key, "")).resolve() == path.resolve(),
                    f"Report points to the wrong artifact: {key}")
            with Image.open(path) as image:
                require(image.format == "PNG", f"Artifact is not a PNG: {key}")
                image.verify()
            with Image.open(path) as image:
                decoded[key] = np.asarray(image).copy()
        selection = decoded["selection"] > 0
        changed = decoded["mask"] > 0
        require(selection.shape == self.text_fixture()["truth"].shape,
                "Selection dimensions differ from the input.")
        require(not np.any(changed & ~selection), "Changed-pixel mask escapes selection.")
        require(report["selection_mask"]["pixels"] == int(selection.sum()),
                "Selection report count disagrees with the saved selection image.")
        require(min(decoded["selection_preview"].shape[:2]) > 0,
                "Selection preview has invalid dimensions.")
        return {"verified_png_artifacts": 4, "selection_pixels": int(selection.sum()),
                "changed_pixels": int(changed.sum()), "changed_subset_of_selection": True}

    def explicit_source_exclusion(self) -> dict:
        fixture = self.text_fixture()
        allowed = fixture["truth"]
        excluded = np.zeros(allowed.shape, dtype=bool)
        # An intact-looking patch immediately beside the first letter must be
        # excluded from matching and donors without joining the repair mask.
        excluded[228:276, 220:243] = True
        require(not np.any(excluded & allowed),
                "Source-exclusion fixture unexpectedly overlaps the repair mask.")
        exclusion_path = self.fixtures / "explicit-source-exclusion.png"
        Image.fromarray(excluded.astype(np.uint8) * 255).save(exclusion_path)
        exclusion_hash = digest(exclusion_path)
        mask_hash = digest(fixture["truth_path"])
        results = []
        sources = []
        source_hashes = []
        expected_excluded = int(np.count_nonzero(allowed | excluded))
        for index, color in enumerate(((0, 0, 255), (255, 255, 0))):
            marked = fixture["marked"].copy()
            marked[excluded] = color
            source = self.fixtures / f"explicit-source-exclusion-{index}.png"
            output = self.outputs / f"explicit-source-exclusion-{index}.png"
            Image.fromarray(marked).save(source)
            source_hash = digest(source)
            sources.append(source)
            source_hashes.append(source_hash)
            self.execute(["--input", source, "--output", output,
                          "--profile", "none", "--mask", fixture["truth_path"],
                          "--source-exclude-mask", exclusion_path,
                          "--method", "exemplar", "--quality", "fast", "--no-preview"])
            self.assert_pixels(source, output, allowed, source_hash, protected=excluded)
            with Image.open(sidecar(output, ".selection.png")) as image:
                selection = np.asarray(image.convert("L")) > 0
            require(np.array_equal(selection, allowed),
                    "Source exclusion expanded or reduced the authorized selection mask.")
            report = self.read_report(sidecar(output, ".report.json"))
            require(report.get("method") == "exemplar", "Explicit donor restriction changed the method.")
            require(report.get("selection_mask", {}).get("source_excluded_pixels") == expected_excluded,
                    "Explicit donor exclusion was not unioned with contaminated pixels.")
            with Image.open(output) as image:
                results.append(np.asarray(image).copy())
        difference = np.abs(results[0][allowed].astype(np.int16) -
                            results[1][allowed].astype(np.int16))
        require(int(difference.max()) <= 1 and float(difference.mean()) <= .05,
                "An explicitly excluded background patch materially influenced the restored pixels.")
        rejected_output = self.outputs / "source-exclusion-auto-rejected.png"
        rejected_report = self.outputs / "source-exclusion-auto-rejected.json"
        self.execute(["--input", sources[0], "--output", rejected_output,
                      "--profile", "none", "--mask", fixture["truth_path"],
                      "--source-exclude-mask", exclusion_path, "--method", "auto",
                      "--report", rejected_report, "--no-preview"], expected=2)
        self.error_report(rejected_report, ("source-exclude-mask", "requires", "exemplar"))
        require(not rejected_output.exists(), "Incompatible automatic method produced a result image.")
        require(all(digest(path) == before for path, before in zip(sources, source_hashes)),
                "A source image changed during explicit donor exclusion.")
        require(digest(exclusion_path) == exclusion_hash and digest(fixture["truth_path"]) == mask_hash,
                "An input selection or exclusion mask was modified.")
        return {"excluded_region_pixels_changed": 0, "outside_mask_changed_pixels": 0,
                "selection_identical_to_original_mask": True, "source_variants": 2,
                "repaired_variant_max_channel_difference": int(difference.max()),
                "repaired_variant_mean_channel_difference": float(difference.mean()),
                "matching_quantization_tolerance_255": 1,
                "explicit_source_excluded_pixels": int(excluded.sum()),
                "total_source_excluded_pixels": expected_excluded,
                "incompatible_auto_rejected": True, "all_input_files_unchanged": True}

    def invalid_text_parameters(self) -> dict:
        fixture = self.text_fixture()
        source_hash = digest(fixture["source"])
        checks = [
            ("bounds", ["--text-roi", "1,1,1000,1000"], ("roi", "bounds", "region")),
            ("selectors", ["--text-roi", fixture["roi_argument"], "--roi", "10,10,30,30"],
             ("only one", "text-roi", "roi")),
            ("dilation", ["--text-roi", fixture["roi_argument"], "--dilate", "2"],
             ("text-roi", "dilation", "dilate")),
        ]
        for name, extra, tokens in checks:
            output = self.outputs / f"invalid-text-{name}.png"
            report = self.outputs / f"invalid-text-{name}.json"
            self.execute(["--input", fixture["source"], "--output", output,
                          "--profile", "none", "--report", report, "--no-preview"] + extra,
                         expected=2)
            require(not output.exists(), f"Invalid text parameter produced an image: {name}")
            self.error_report(report, tokens)
        bad_quality_output = self.outputs / "invalid-text-quality.png"
        completed = self.execute(["--input", fixture["source"], "--output", bad_quality_output,
                                  "--text-roi", fixture["roi_argument"], "--quality", "unbounded"],
                                 expected=2)
        require("quality" in completed.stderr.lower() and "invalid choice" in completed.stderr.lower(),
                "Invalid quality has no clear command-line error.")
        require(not bad_quality_output.exists(), "Invalid quality produced a result image.")
        require(digest(fixture["source"]) == source_hash,
                "Invalid text parameters modified the input.")
        return {"invalid_cases_rejected": 4, "input_unchanged": True,
                "quality_error_is_argparse_stderr": True}

    def no_text_roi_is_not_erased(self) -> dict:
        fixture = self.text_fixture()
        source = self.fixtures / "no-text-in-requested-roi.png"
        output = self.outputs / "no-text-roi.png"
        report_path = self.outputs / "no-text-roi-error.json"
        Image.fromarray(fixture["clean"]).save(source)
        source_hash = digest(source)
        self.execute(["--input", source, "--output", output, "--profile", "none",
                      "--text-roi", fixture["roi_argument"], "--method", "auto",
                      "--report", report_path, "--no-preview"], expected=2)
        error = self.error_report(report_path, ("no reliable", "text-roi", "no rectangular"))
        require(not output.exists(), "A no-text ROI was silently erased as a rectangle.")
        require(not sidecar(output, ".selection.png").exists(),
                "A no-text ROI produced a misleading selection artifact.")
        require(digest(source) == source_hash, "The no-text source changed.")
        error.update({"input_unchanged": True, "result_image_created": False,
                      "scope": "One unlettered texture fixture; not a false-positive rate estimate."})
        return error

    def run_case(self, case_id: str, function) -> None:
        self.last_call = None
        started = time.monotonic()
        result = {"id": case_id}
        try:
            result["details"] = function() or {}
            result["status"] = "passed"
        except Exception as exc:
            result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            if self.last_call:
                result["last_cli_call"] = self.last_call
        result["duration_seconds"] = round(time.monotonic() - started, 4)
        self.results.append(result)
        print(f"[{result['status'].upper()}] {case_id}", file=sys.stderr, flush=True)

    def run(self) -> list[dict]:
        cases = [
            ("telea_rgb_mask_boundaries_and_preview", lambda: self.classical_rgb("telea")),
            ("ns_rgb_mask_boundaries_and_no_preview", lambda: self.classical_rgb("ns")),
            ("rgba_alpha_and_protect_mask", self.protected_rgba),
            ("flat_background_synthetic_truth", self.flat_fill),
            ("shifted_builtin_template_inverse_regression", self.shifted_template_inverse),
            ("no_match_is_skipped", self.no_match),
            ("input_overwrite_is_always_rejected", self.input_overwrite_rejected),
            ("wrong_size_mask_has_clear_error", self.invalid_mask),
            ("nonfinite_radius_has_clear_error", self.invalid_radius),
            ("nonfinite_profile_number_has_clear_error", self.invalid_profile_number),
            ("opaque_alpha_template_is_rejected", self.invalid_alpha_template),
            ("batch_same_stem_outputs_do_not_collide", self.batch_collisions),
            ("batch_mixed_error_is_reported", self.batch_mixed_errors),
            ("text_roi_builds_stroke_mask_and_auto_uses_exemplar", self.text_roi_auto_exemplar),
            ("text_descender_and_dark_outline_tails_are_covered", self.text_descender_outline_coverage),
            ("text_morphology_preserves_unmarked_high_contrast_object", self.text_morphology_respects_object_edge),
            ("exemplar_protected_watermark_is_excluded_from_donors", self.exemplar_protected_donors),
            ("selection_and_selection_preview_artifacts", self.selection_artifacts),
            ("consensus_refinement_cli_scope_and_explicit_method", self.consensus_cli_and_scope),
            ("consensus_reaches_bounded_sources_and_covers_sparse_support", self.consensus_bounded_sources_and_sparse_support),
            ("consensus_rejects_reflected_padding_as_complete_source", self.consensus_rejects_padding_only_sources),
            ("explicit_source_exclusion_preserves_selection_and_blocks_donor_influence", self.explicit_source_exclusion),
            ("invalid_text_roi_and_quality_have_clear_errors", self.invalid_text_parameters),
            ("unlettered_text_roi_is_not_rectangularly_erased", self.no_text_roi_is_not_erased),
        ]
        for case_id, function in cases:
            self.run_case(case_id, function)
        return self.results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path,
                        help="Retain JSON results and fixtures outside the skill directory.")
    parser.add_argument("--timeout", type=float, default=45.0,
                        help="Maximum seconds for each CLI call (default: 45).")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be finite and between 0 and 300 seconds.")
    if args.output_dir:
        target = args.output_dir.expanduser().resolve()
        if target == SKILL_ROOT or SKILL_ROOT in target.parents:
            parser.error("Test artifacts must be saved outside the installed skill directory.")
        target.mkdir(parents=True, exist_ok=True)
        work = target / ("run-" + uuid.uuid4().hex[:8])
        work.mkdir()
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="watermark-selftest-")
        work = Path(temporary.name)
        target = None
    started = time.monotonic()
    result = {
        "suite_version": "1.3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Offline synthetic regression and CLI boundary checks; not a general image-quality benchmark.",
        "artifacts_retained": bool(target),
        "environment": {"python": sys.version.split()[0], "platform": sys.platform},
    }
    exit_code = 1
    try:
        global np, Image, ImageDraw
        import numpy as np
        import PIL
        from PIL import Image, ImageDraw
        import cv2
        result["environment"].update({"numpy": np.__version__, "pillow": PIL.__version__,
                                      "opencv": cv2.__version__})
        require(ENTRYPOINT.is_file(), "scripts/run.py is missing.")
        suite = Suite(work, args.timeout)
        result["results"] = suite.run()
        result["passed"] = [r["id"] for r in suite.results if r["status"] == "passed"]
        result["failed"] = [r["id"] for r in suite.results if r["status"] == "failed"]
        result["counts"] = {"passed": len(result["passed"]), "failed": len(result["failed"]),
                            "total": len(suite.results)}
        result["status"] = "passed" if not result["failed"] else "failed"
        exit_code = 0 if not result["failed"] else 1
    except Exception as exc:
        result.update({"status": "error", "error": f"{type(exc).__name__}: {exc}",
                       "passed": [], "failed": ["suite_initialization"], "results": [],
                       "counts": {"passed": 0, "failed": 1, "total": 1}})
        exit_code = 2
    finally:
        result["duration_seconds"] = round(time.monotonic() - started, 4)
        if target:
            result["artifact_directory"] = str(work)
            (target / "test_results.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if temporary:
            temporary.cleanup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
