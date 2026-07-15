# Kindle Screenshot Tool

<p align="center">
  <a href="README_JA.md"><kbd>日本語</kbd></a>
  &nbsp;
  <a href="README_EN.md"><kbd>English</kbd></a>
</p>

Windows版Kindleアプリに表示された本をページ送りしながら撮影し、OCRで検索可能なPDFとMarkdownへ変換するツールです。Kindleの本データへはアクセスせず、表示中のウィンドウ画像だけを扱います。

## 主な機能

- 起動中のWindows版Kindleを自動検出して直接キャプチャ
- Kindleを背面に置いたまま撮影可能
- 右／左のページ送りを選択可能
- 互換モードでは、ページ送り時だけKindleを一時的に前面化
- ページ番号のOCR検出と現在ページ表示
- 撮影画像から、1冊分の結合PDFと結合Markdownを作成

## 必要環境

- Windows 10 / 11
- Python 3.10以降
- Windows版Kindleアプリ
- Tesseract OCR（日本語データ `jpn` を含む。ページ番号検出用）
- NVIDIA GPUは任意。YomiToku OCRはGPUがあると高速です。

## インストール

```powershell
git clone https://github.com/ganpare/kindle2ss.git
cd kindle2ss
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe torch torchvision --index-url https://download.pytorch.org/whl/cu130
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

CUDAを使わない場合は、PyTorchの導入を次に置き換えてください。

```powershell
uv pip install --python .venv\Scripts\python.exe torch torchvision
```

## 起動

```powershell
.\.venv\Scripts\Activate.ps1
python kindle2ss_qt.py
```

## 使い方

1. Kindleで本を開き、最小化せずに表示したままにします。
2. ツールで「Kindleを再検出」を押します。
3. 「本文領域をKindle全体に設定」を押します。
4. ページ番号を使う場合は、Kindle下部に `ページ 8/62` のような表示が見える状態で「ページ番号領域を下部に設定」→「ページ番号を確認」を押します。
5. 「開始」を押します。通常は互換モードをオンのままにしてください。

## OCRの出力

OCRを有効にすると、撮影フォルダごとに次の構成で出力されます。

```text
output_YYYYMMDDHHMMSS/
output_YYYYMMDDHHMMSS_ocr/
  book.pdf          # 全ページを結合したPDF
  book.md           # 全ページを結合したMarkdown
  pdf_pages/        # ページ別のOCR結果
  markdown_pages/   # ページ別のOCR結果
```

## 注意

- Kindleウィンドウを最小化すると直接キャプチャできません。
- ページ番号が画面に表示されていない本・表示モードでは、ページ番号OCRは利用できません。その場合でも、同じ画面が3回続いた時点で撮影を停止します。
- 本ツールの利用は、購入コンテンツの利用規約および著作権法を守る範囲で行ってください。

## ライセンス

[MIT License](LICENSE)
