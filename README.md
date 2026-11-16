# Image checker

Check that compressed JPEG and WebP files are still the same pictures as the originals. The checker does not change or delete any image.

Each original is matched to a compressed file with the same name. The extension is ignored, so `ring.png` is compared with `ring.jpg` and `ring.webp`.

A file passes only when all three agree:

- **dHash** (the fingerprint) is within **10** on a scale of 64. This compares the light-and-dark layout.
- **Color gap** on a 64-pixel preview is **1.0 or less**. Normal JPEG and WebP compression stays under about 0.8.
- **Width and height** are the same.

If the fingerprint is close but the color or the size is not, the file is marked for a quick look. If the fingerprint is far apart, a slower pixel comparison decides whether it looks different.

The spreadsheet lists only pictures that need attention. Matching files are counted and left out of the file.

## Install

You need Python 3.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate the environment with `.venv\Scripts\activate`.

## Check pictures in the browser (Mac)

Double-click `Compare Pictures.command`. The first time, it installs the libraries. Then a browser page opens.

1. Choose the original folder.
2. Choose the compressed folder.
3. Click **Check pictures**.

Leave the Terminal window open while the check runs. The folder buttons use Finder, so this page is for Mac.

## Check pictures from the command line

```bash
python compare_optimized_images.py \
  --original /path/to/originals \
  --optimized /path/to/compressed \
  --report image_comparison_report.csv
```

Folders can contain subfolders. A file matches another file in the same relative folder with the same name.

## Report

`image_comparison_report.csv` contains only files that are not a clear match:

| Status | Meaning |
|---|---|
| REVIEW | Same layout, but the color or the size differs. Look at it. |
| CHECK | The pictures look different. |
| MISSING | No compressed JPEG or WebP with that name. |
| ERROR | A file could not be opened. |
| AMBIGUOUS_ORIGINAL | More than one original has that name. |
| AMBIGUOUS_OPTIMIZED | More than one compressed file has that name. |
