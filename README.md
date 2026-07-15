# Kindle Screenshot Tool

> [日本語版 README](README_JP.md)

A Windows tool that captures pages shown in the Kindle desktop app, then uses OCR to create a searchable PDF and a single Markdown book. It works only with the visible Kindle window; it does not access Kindle book data.

## Features

- Automatically detects a running Windows Kindle app and captures it directly
- Captures Kindle while it remains in the background
- Choose right- or left-arrow page turns
- Compatibility mode brings Kindle to the foreground only while turning a page
- OCR page-number detection with current-page status
- Optional book-title OCR with a manual correction field
- Exports one combined PDF and one combined Markdown file per capture

## Requirements

- Windows 10 or 11
- Python 3.10+
- Windows Kindle desktop app
- Tesseract OCR with Japanese language data (`jpn`) for page-number detection
- An NVIDIA GPU is optional, but speeds up YomiToku OCR considerably

## Installation

```powershell
git clone https://github.com/ganpare/kindle2ss.git
cd kindle2ss
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe torch torchvision --index-url https://download.pytorch.org/whl/cu130
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

For a CPU-only setup, replace the PyTorch installation command with:

```powershell
uv pip install --python .venv\Scripts\python.exe torch torchvision
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python kindle2ss_qt.py
```

## Usage

1. Open a book in Kindle and leave the window visible (not minimized).
2. Select **Kindleを再検出** to find the target window.
3. Select **本文領域をKindle全体に設定** to use the full Kindle window.
4. To use page-number detection, make sure the Kindle footer shows a value such as `Page 8/62`, then set and check the bottom page-number region.
5. Select **開始**. Leave compatibility mode enabled unless direct key delivery works in your environment.

## OCR output

Every OCR-enabled capture creates the following structure:

```text
output_<book-title>_YYYYMMDDHHMMSS/
output_<book-title>_YYYYMMDDHHMMSS_ocr/
  book.pdf          # all pages merged into one PDF
  book.md           # all pages merged into one Markdown file, headed by the book title
  pdf_pages/        # per-page OCR artifacts
  markdown_pages/   # per-page OCR artifacts
```

## Notes

- A minimized Kindle window cannot be captured directly.
- Page-number OCR is unavailable when the page indicator is not visible. Capture still stops after the same screen is detected three times.
- Use this tool only in accordance with the terms for your content and applicable copyright law.

## License

[MIT License](LICENSE)
