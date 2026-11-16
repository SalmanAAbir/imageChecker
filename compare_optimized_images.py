#!/usr/bin/env python3
"""
Compare original images against optimized JPG and WebP versions.

Expected layout:
    original/
        nested/path/photo.png
    optimized/
        nested/path/photo.jpg
        nested/path/photo.webp

Matching is based on the relative path + filename stem, ignoring extension.
The script never modifies source images. It writes a CSV report only.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
    import imagehash
    import numpy as np
except ImportError as exc:
    print(
        "Missing dependency. Install them with:\n"
        "  python -m pip install Pillow ImageHash numpy\n"
        f"\nDetails: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(2)


def structural_similarity(image1, image2, *, channel_axis, data_range, win_size):
    """Mean SSIM. Same check the script used to borrow from scikit-image."""
    if channel_axis != 2:
        raise ValueError("channel_axis must be 2")
    sigma = 1.5
    coords = np.arange(win_size) - (win_size // 2)
    kernel = np.exp(-(coords ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    pad = win_size // 2
    c1 = (0.01 * data_range) ** 2
    c2 = (0.03 * data_range) ** 2

    def blur(channel):
        padded = np.pad(channel, pad, mode="reflect")
        horizontal = sum(padded[:, i:i + channel.shape[1]] * kernel[i] for i in range(win_size))
        return sum(horizontal[i:i + channel.shape[0]] * kernel[i] for i in range(win_size))

    scores = []
    first = image1.astype(np.float64)
    second = image2.astype(np.float64)
    for channel in range(first.shape[2]):
        x = first[:, :, channel]
        y = second[:, :, channel]
        mu_x = blur(x)
        mu_y = blur(y)
        sigma_x = blur(x * x) - mu_x * mu_x
        sigma_y = blur(y * y) - mu_y * mu_y
        sigma_xy = blur(x * y) - mu_x * mu_y
        numerator = (2 * mu_x * mu_y + c1) * (2 * sigma_xy + c2)
        denominator = (mu_x * mu_x + mu_y * mu_y + c1) * (sigma_x + sigma_y + c2)
        scores.append(float(np.mean(numerator / denominator)))
    return float(np.mean(scores))


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff",
    ".bmp", ".gif", ".avif", ".heic", ".heif"
}
OPTIMIZED_EXTENSIONS = {
    "jpg": {".jpg", ".jpeg"},
    "webp": {".webp"},
}


def image_key(path: Path, root: Path) -> str:
    """Relative path without extension, normalized for case-insensitive matching."""
    relative = path.relative_to(root).with_suffix("")
    return relative.as_posix().casefold()


def collect_images(root: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS:
            found[image_key(path, root)].append(path)
    return found


def load_for_comparison(path: Path) -> Image.Image:
    """
    Apply EXIF orientation and composite transparency on white so RGB/JPG and
    transparent PNG/WebP can be compared on a consistent visible background.
    """
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")


def resize_for_ssim(image: Image.Image, max_side: int = 1600) -> Image.Image:
    """Bound memory and runtime while retaining enough detail for visual checks."""
    width, height = image.size
    scale = min(1.0, max_side / max(width, height))
    if scale < 1.0:
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        return image.resize(new_size, Image.Resampling.LANCZOS)
    return image.copy()


def classify_match(ssim_score: float, phash_distance: int, dimensions_match: bool) -> str:
    """Map similarity scores to PASS, REVIEW, or CHECK."""
    if (ssim_score >= 0.90 and phash_distance <= 12) or ssim_score >= 0.97:
        status = "PASS"
    elif ssim_score >= 0.80 and phash_distance <= 18:
        status = "REVIEW"
    else:
        status = "CHECK"
    if not dimensions_match and status == "PASS":
        status = "REVIEW"
    return status


# A close fingerprint is not enough on its own. A recolor or a small logo can
# keep the same light-and-dark pattern. Real JPG/WebP compression of these
# photos stays under a color gap of about 0.8 on a 64-pixel preview.
FAST_MATCH_DISTANCE = 10
COLOR_MATCH_MAX = 1.0


def fingerprint(image: Image.Image):
    return imagehash.dhash(image)


def color_difference(original: Image.Image, optimized: Image.Image, side: int = 64) -> float:
    """Mean color gap on a small preview. Compression stays well under 1.0."""
    original_small = np.asarray(original.resize((side, side), Image.Resampling.BILINEAR), dtype=np.float32)
    optimized_small = np.asarray(optimized.resize((side, side), Image.Resampling.BILINEAR), dtype=np.float32)
    return float(np.mean(np.abs(original_small - optimized_small)))


def pixel_similarity(original: Image.Image, optimized: Image.Image, max_side: int) -> float:
    optimized_aligned = optimized.resize(original.size, Image.Resampling.LANCZOS)
    original_small = resize_for_ssim(original, max_side)
    optimized_small = resize_for_ssim(optimized_aligned, max_side)
    original_array = np.asarray(original_small, dtype=np.uint8)
    optimized_array = np.asarray(optimized_small, dtype=np.uint8)
    smallest_side = min(original_array.shape[0], original_array.shape[1])
    if smallest_side < 3:
        tiny_size = (max(3, original_small.width), max(3, original_small.height))
        original_array = np.asarray(original_small.resize(tiny_size), dtype=np.uint8)
        optimized_array = np.asarray(optimized_small.resize(tiny_size), dtype=np.uint8)
        smallest_side = min(original_array.shape[0], original_array.shape[1])
    win_size = min(7, smallest_side if smallest_side % 2 == 1 else smallest_side - 1)
    return float(
        structural_similarity(
            original_array,
            optimized_array,
            channel_axis=2,
            data_range=255,
            win_size=win_size,
        )
    )


def compare_loaded(original: Image.Image, original_hash, optimized_path: Path, max_side: int) -> dict:
    optimized = load_for_comparison(optimized_path)
    distance = int(original_hash - fingerprint(optimized))
    dimensions_match = original.size == optimized.size
    color_gap = color_difference(original, optimized)
    ssim_score = ""
    notes = ""
    if distance <= FAST_MATCH_DISTANCE and color_gap <= COLOR_MATCH_MAX and dimensions_match:
        status = "PASS"
    elif distance <= FAST_MATCH_DISTANCE:
        status = "REVIEW"
        if not dimensions_match:
            notes = "Width or height changed."
        else:
            notes = f"Color or a small detail differs from the original (color gap {color_gap:.1f})."
    else:
        ssim_score = pixel_similarity(original, optimized, max_side)
        status = classify_match(ssim_score, distance, dimensions_match)
        ssim_score = round(ssim_score, 6)
        if status == "PASS" and color_gap > COLOR_MATCH_MAX:
            status = "REVIEW"
            notes = f"Color or a small detail differs from the original (color gap {color_gap:.1f})."
    return {
        "status": status,
        "ssim": ssim_score,
        "phash_distance": distance,
        "original_width": original.size[0],
        "original_height": original.size[1],
        "optimized_width": optimized.size[0],
        "optimized_height": optimized.size[1],
        "dimensions_match": dimensions_match,
        "notes": notes,
    }


def _compare_original(task):
    original_path, pairs, max_side = task
    try:
        original = load_for_comparison(Path(original_path))
        original_hash = fingerprint(original)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        message = f"Original could not be opened: {exc}"
        return original_path, [(fmt, optimized_path, None, message) for fmt, optimized_path in pairs]
    results = []
    for fmt, optimized_path in pairs:
        try:
            results.append((fmt, optimized_path, compare_loaded(original, original_hash, Path(optimized_path), max_side), ""))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            results.append((fmt, optimized_path, None, str(exc)))
    return original_path, results


def compare_folders(original_root: Path, optimized_root: Path, max_side: int = 1600, progress=None):
    """Compare every original with its JPG and WebP. Does not write a report."""
    originals = collect_images(original_root)
    optimized = collect_images(optimized_root)

    rows = []
    counts = defaultdict(int)
    total_originals = sum(len(paths) for paths in originals.values())
    comparable = sum(1 for paths in originals.values() if len(paths) == 1)
    total_pairs = comparable * len(OPTIMIZED_EXTENSIONS)
    processed = 0
    pending = []

    for key in sorted(originals):
        original_paths = originals[key]

        if len(original_paths) != 1:
            for original_path in original_paths:
                for fmt in ("jpg", "webp"):
                    rows.append({
                        "format": fmt.upper(),
                        "status": "AMBIGUOUS_ORIGINAL",
                        "original_file": str(original_path),
                        "optimized_file": "",
                        "ssim": "",
                        "phash_distance": "",
                        "original_width": "",
                        "original_height": "",
                        "optimized_width": "",
                        "optimized_height": "",
                        "dimensions_match": "",
                        "notes": "More than one original has this relative filename stem.",
                    })
                    counts["AMBIGUOUS_ORIGINAL"] += 1
            continue

        original_path = original_paths[0]
        candidates = optimized.get(key, [])

        for fmt, allowed_extensions in OPTIMIZED_EXTENSIONS.items():
            matches = [p for p in candidates if p.suffix.casefold() in allowed_extensions]
            base_row = {
                "format": fmt.upper(),
                "original_file": str(original_path),
                "optimized_file": "",
                "ssim": "",
                "phash_distance": "",
                "original_width": "",
                "original_height": "",
                "optimized_width": "",
                "optimized_height": "",
                "dimensions_match": "",
                "notes": "",
            }

            if not matches:
                row = {**base_row, "status": "MISSING",
                       "notes": f"No optimized {fmt.upper()} file with matching name."}
                rows.append(row)
                counts["MISSING"] += 1
                continue

            if len(matches) > 1:
                row = {**base_row, "status": "AMBIGUOUS_OPTIMIZED",
                       "optimized_file": " | ".join(str(p) for p in matches),
                       "notes": f"Found multiple optimized {fmt.upper()} matches."}
                rows.append(row)
                counts["AMBIGUOUS_OPTIMIZED"] += 1
                continue

            optimized_path = matches[0]
            pending.append((str(original_path), fmt, str(optimized_path)))

    grouped = defaultdict(list)
    for original_path, fmt, optimized_path in pending:
        grouped[original_path].append((fmt, optimized_path))
    tasks = [(path, pairs, max_side) for path, pairs in grouped.items()]
    total_pairs = len(pending)
    workers = min(8, os.cpu_count() or 4)
    if progress:
        progress(processed, total_pairs, total_originals)

    def record(fmt, optimized_path, result, error, original_path):
        nonlocal processed
        base_row = {
            "format": fmt.upper(),
            "original_file": original_path,
            "optimized_file": optimized_path,
            "ssim": "",
            "phash_distance": "",
            "original_width": "",
            "original_height": "",
            "optimized_width": "",
            "optimized_height": "",
            "dimensions_match": "",
            "notes": "",
        }
        if error:
            rows.append({**base_row, "status": "ERROR", "notes": error})
            counts["ERROR"] += 1
        else:
            counts[result["status"]] += 1
            if result["status"] != "PASS":
                rows.append({**base_row, **result})
        processed += 1
        if progress and (processed % 200 == 0 or processed == total_pairs):
            progress(processed, total_pairs, total_originals)

    if tasks:
        with Pool(workers) as pool:
            for original_path, results in pool.imap_unordered(_compare_original, tasks, chunksize=16):
                for fmt, optimized_path, result, error in results:
                    record(fmt, optimized_path, result, error, original_path)

    return {
        "rows": rows,
        "counts": dict(counts),
        "total_originals": total_originals,
        "processed": processed,
    }


def write_report(report_path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "format", "status", "original_file", "optimized_file", "ssim",
        "phash_distance", "original_width", "original_height",
        "optimized_width", "optimized_height", "dimensions_match", "notes",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    issues = [row for row in rows if row.get("status") != "PASS"]
    with report_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(issues)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare originals with optimized JPG and WebP images."
    )
    parser.add_argument("--original", required=True, type=Path,
                        help="Root folder containing original images.")
    parser.add_argument("--optimized", required=True, type=Path,
                        help="Root folder containing optimized JPG and WebP images.")
    parser.add_argument("--report", type=Path, default=Path("image_comparison_report.csv"),
                        help="CSV output path (default: image_comparison_report.csv).")
    parser.add_argument("--max-side", type=int, default=1600,
                        help="Maximum image side used for SSIM (default: 1600).")
    args = parser.parse_args()

    original_root = args.original.expanduser().resolve()
    optimized_root = args.optimized.expanduser().resolve()
    report_path = args.report.expanduser().resolve()

    if not original_root.is_dir():
        parser.error(f"Original folder does not exist: {original_root}")
    if not optimized_root.is_dir():
        parser.error(f"Optimized folder does not exist: {optimized_root}")
    if args.max_side < 64:
        parser.error("--max-side must be at least 64")

    print("Matching by relative folder path + filename (extension ignored).")

    def show_progress(processed, total_pairs, total_originals):
        if processed == 0:
            print(f"Found {total_originals:,} original image file(s).")
        elif processed % 250 == 0 or processed == total_pairs:
            print(f"Compared {processed:,} of {total_pairs:,} optimized image(s)...")

    result = compare_folders(original_root, optimized_root, args.max_side, show_progress)
    write_report(report_path, result["rows"])

    print("\nComparison complete.")
    print(f"CSV report: {report_path}")
    print(f"Original files: {result['total_originals']:,}")
    print(f"Optimized comparisons: {result['processed']:,}")
    for status in ("PASS", "REVIEW", "CHECK", "MISSING", "ERROR",
                   "AMBIGUOUS_ORIGINAL", "AMBIGUOUS_OPTIMIZED"):
        count = result["counts"].get(status, 0)
        if count:
            print(f"{status}: {count:,}")

    print(
        "\nThe spreadsheet lists only pictures that need attention. "
        "Same-picture files are counted on screen and left out of the file."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
