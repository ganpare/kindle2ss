import sys
import os
import time
import datetime
import subprocess
import re
import ctypes
import tempfile
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QLineEdit, QCheckBox, QComboBox,
    QGroupBox, QProgressBar, QMessageBox, QTextEdit, QSplitter, QDialog, QRubberBand
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QRect, QPoint
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QFont

import win32gui
import win32ui
import win32con
import win32api
import win32process
from PIL import Image
import pytesseract


PW_RENDERFULLCONTENT = 2
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSDATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")

if os.path.exists(TESSERACT_EXE):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
if os.path.isdir(TESSDATA_DIR):
    os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR


def get_window_size(handle: int) -> Tuple[int, int]:
    """ウィンドウ全体のサイズを返す。"""
    left, top, right, bottom = win32gui.GetWindowRect(handle)
    return right - left, bottom - top


def capture_window(handle: int) -> Image.Image:
    """背面のウィンドウも含め、PrintWindowで1枚の画像として取得する。"""
    if not win32gui.IsWindow(handle):
        raise RuntimeError("選択したKindleウィンドウが見つかりません。再検出してください。")
    if win32gui.IsIconic(handle):
        raise RuntimeError("Kindleが最小化されています。表示状態に戻してください。")

    width, height = get_window_size(handle)
    if width <= 0 or height <= 0:
        raise RuntimeError("Kindleウィンドウのサイズを取得できませんでした。")

    hwindc = win32gui.GetWindowDC(handle)
    srcdc = win32ui.CreateDCFromHandle(hwindc)
    memdc = srcdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    previous_bitmap = None

    try:
        bmp.CreateCompatibleBitmap(srcdc, width, height)
        previous_bitmap = memdc.SelectObject(bmp)
        result = ctypes.windll.user32.PrintWindow(handle, memdc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not result:
            raise RuntimeError("Kindle画面を取得できませんでした。Kindleを表示状態にしてください。")

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
    finally:
        # 選択中のBitmapをDCから外してから破棄する。選択したままDCを消すと
        # win32ui/MFC側で二重解放となり、Qt描画時にプロセスが落ちることがある。
        if previous_bitmap:
            memdc.SelectObject(previous_bitmap)
        win32gui.DeleteObject(bmp.GetHandle())
        memdc.DeleteDC()
        srcdc.DeleteDC()
        win32gui.ReleaseDC(handle, hwindc)


def crop_region(image: Image.Image, region: dict) -> Image.Image:
    """Kindleウィンドウ基準の領域を切り出す。"""
    left, top = region["left"], region["top"]
    right, bottom = left + region["width"], top + region["height"]
    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        raise RuntimeError("選択範囲がKindleウィンドウの外です。領域を選択し直してください。")
    return image.crop((left, top, right, bottom))


def read_page_number(handle: int, page_region: dict) -> Tuple[Optional[int], Optional[int]]:
    """Kindle下部のページ表示から現在ページと総ページ数を読み取る。"""
    image = crop_region(capture_window(handle), page_region)
    text = pytesseract.image_to_string(image, lang="jpn", config="--psm 6")
    for pattern in (r"(\d+)\s*/\s*(\d+)", r"(\d+)\s*ページ", r"位置No\.\s*(\d+)"):
        match = re.search(pattern, text)
        if match:
            if len(match.groups()) >= 2:
                return int(match.group(1)), int(match.group(2))
            return int(match.group(1)), None
    return None, None


def format_page_status(current_page: Optional[int], total_pages: Optional[int]) -> str:
    if current_page and total_pages:
        return f"現在のページ: {current_page}/{total_pages}"
    if current_page:
        return f"現在のページ: {current_page}"
    return "現在のページ: 検出できません"


def is_kindle_window(handle: int) -> bool:
    """Windows版Kindleのトップレベルウィンドウかを判定する。"""
    if not win32gui.IsWindowVisible(handle) or not win32gui.GetWindowText(handle):
        return False
    try:
        _, process_id = win32process.GetWindowThreadProcessId(handle)
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
        if not process:
            return False
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.c_uint32(len(buffer))
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length))
            return bool(ok and os.path.basename(buffer.value).lower() == "kindle.exe")
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    except Exception:
        return False


def find_kindle_windows() -> list[int]:
    """表示中のKindleウィンドウを前面順に返す。"""
    windows = []

    def collect(handle, _):
        if is_kindle_window(handle):
            windows.append(handle)
        return True

    win32gui.EnumWindows(collect, None)
    foreground = win32gui.GetForegroundWindow()
    windows.sort(key=lambda handle: handle != foreground)
    return windows


def activate_window(handle: int) -> bool:
    """Windowsの前面化制限を回避して、指定ウィンドウを一時的に操作可能にする。"""
    if win32gui.IsIconic(handle):
        win32gui.ShowWindow(handle, win32con.SW_RESTORE)

    current_handle = win32gui.GetForegroundWindow()
    current_thread, _ = win32process.GetWindowThreadProcessId(current_handle)
    target_thread, _ = win32process.GetWindowThreadProcessId(handle)
    own_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    attached_threads = []

    try:
        for thread_id in {current_thread, target_thread}:
            if thread_id and thread_id != own_thread:
                if ctypes.windll.user32.AttachThreadInput(own_thread, thread_id, True):
                    attached_threads.append(thread_id)
        for _ in range(3):
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                win32gui.BringWindowToTop(handle)
                win32gui.SetForegroundWindow(handle)
            except Exception:
                pass
            if win32gui.GetForegroundWindow() == handle:
                return True
            time.sleep(0.15)
        return False
    finally:
        for thread_id in attached_threads:
            ctypes.windll.user32.AttachThreadInput(own_thread, thread_id, False)


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    """Pillow画像をQtのPixmapへ変換する。"""
    image_rgb = image.convert("RGB")
    data = image_rgb.tobytes("raw", "RGB")
    qimage = QImage(data, image_rgb.width, image_rgb.height, QImage.Format_RGB888)
    # dataはこの関数を抜けると破棄されるため、Qt側に必ず所有コピーを作る。
    return QPixmap.fromImage(qimage.copy())


class ImageSelectionWidget(QLabel):
    """Kindleのキャプチャ画像上で領域を選択するウィジェット。"""
    region_selected = Signal(int, int, int, int)

    def __init__(self, image: Image.Image):
        super().__init__()
        original = pil_to_qpixmap(image)
        scale = min(950 / original.width(), 700 / original.height(), 1.0)
        self.pixmap = original.scaled(
            round(original.width() * scale), round(original.height() * scale),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.original_size = original.size()
        self.start_pos = None
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self.setPixmap(self.pixmap)
        self.setFixedSize(self.pixmap.size())
        self.setCursor(Qt.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.rubber_band.setGeometry(QRect(self.start_pos, self.start_pos))
            self.rubber_band.show()

    def mouseMoveEvent(self, event):
        if self.start_pos:
            self.rubber_band.setGeometry(QRect(self.start_pos, event.pos()).normalized())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.start_pos:
            return
        selected = QRect(self.start_pos, event.pos()).normalized().intersected(self.rect())
        self.start_pos = None
        self.rubber_band.hide()
        if selected.width() < 2 or selected.height() < 2:
            return
        scale_x = self.original_size.width() / self.pixmap.width()
        scale_y = self.original_size.height() / self.pixmap.height()
        self.region_selected.emit(
            round(selected.left() * scale_x),
            round(selected.top() * scale_y),
            round(selected.width() * scale_x),
            round(selected.height() * scale_y),
        )


class WindowRegionDialog(QDialog):
    """キャプチャ画像から本文またはページ番号の領域を選択するダイアログ。"""
    def __init__(self, image: Image.Image, title: str, parent=None):
        super().__init__(parent)
        self.selected_region = None
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(1000, 800)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Kindleの画像上で範囲をドラッグしてください。確定はマウスを離すだけです。"))
        self.selector = ImageSelectionWidget(image)
        self.selector.region_selected.connect(self.accept_region)
        layout.addWidget(self.selector, 1, Qt.AlignCenter)

        cancel_button = QPushButton("キャンセル")
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(cancel_button)

    def accept_region(self, left, top, width, height):
        self.selected_region = {"left": left, "top": top, "width": width, "height": height}
        self.accept()


class CaptureThread(QThread):
    """キャプチャ処理スレッド"""
    status_updated = Signal(str)
    progress_updated = Signal(str)
    page_number_updated = Signal(str)
    capture_completed = Signal(str, int)
    error_occurred = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.is_running = True

    def run(self):
        try:
            # 出力フォルダ作成
            folder_name = (self.settings['folder_prefix'] + "_" +
                          datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
            os.mkdir(folder_name)

            self.status_updated.emit(f"キャプチャ中... フォルダ: {folder_name}")

            prev_img = None
            same_cnt = 0
            page_count = 0
            total_pages = None

            while self.is_running:
                # スクリーンショット取得
                img = self.capture_screenshot()
                if img is None:
                    break

                page_count += 1

                # 画像が前回と異なる場合のみ保存
                if prev_img is None or not img.tobytes() == prev_img.tobytes():
                    filename = f"picture_{str(page_count).zfill(4)}.png"
                    img.save(os.path.join(folder_name, filename))
                    prev_img = img
                    same_cnt = 0

                    # ページ番号検出
                    if self.settings['auto_stop']:
                        current_page, detected_total = self.detect_page_number()
                        self.page_number_updated.emit(format_page_status(current_page, detected_total))
                        if detected_total:
                            total_pages = detected_total

                        if current_page and total_pages:
                            progress = f"ページ {current_page}/{total_pages} - {page_count} 枚キャプチャ済み"
                            self.progress_updated.emit(progress)

                            # 最終ページに到達
                            if current_page >= total_pages:
                                self.status_updated.emit("最終ページに到達しました")
                                break
                        else:
                            self.progress_updated.emit(f"{page_count} 枚キャプチャ済み")
                    else:
                        self.progress_updated.emit(f"{page_count} 枚キャプチャ済み")

                    # 次のページへ
                    self.send_page_turn()
                    time.sleep(self.settings['interval'])
                else:
                    same_cnt += 1

                # 3回同じ画像が出現したら終了
                if same_cnt >= 3:
                    self.status_updated.emit("同じ画面が3回連続で検出されました")
                    break

            # キャプチャ完了
            self.capture_completed.emit(folder_name, page_count)

        except Exception as e:
            self.error_occurred.emit(str(e))

    def capture_screenshot(self) -> Optional[Image.Image]:
        """固定したKindleウィンドウの選択範囲を取得する。"""
        return crop_region(capture_window(self.settings['target_handle']), self.settings['region'])

    def detect_page_number(self) -> Tuple[Optional[int], Optional[int]]:
        """ページ番号を検出"""
        try:
            return read_page_number(self.settings['target_handle'], self.settings['page_region'])

        except Exception as e:
            print(f"ページ番号検出エラー: {e}")
            return None, None

    def send_page_turn(self):
        """Kindleへ1回だけページ送りキーを送信する。"""
        handle = self.settings['target_handle']
        virtual_key = win32con.VK_RIGHT if self.settings['page_direction'] == "right" else win32con.VK_LEFT

        if self.settings['compatibility_mode']:
            previous_handle = win32gui.GetForegroundWindow()
            if activate_window(handle):
                time.sleep(0.1)
                win32api.keybd_event(virtual_key, 0, 0, 0)
                win32api.keybd_event(virtual_key, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.2)
                if (previous_handle != handle and win32gui.IsWindow(previous_handle)
                        and win32gui.IsWindowVisible(previous_handle) and not win32gui.IsIconic(previous_handle)):
                    try:
                        win32gui.SetForegroundWindow(previous_handle)
                    except Exception:
                        pass
                return

            self.status_updated.emit("Kindleを前面化できないため、直接キー送信に切り替えます")

        scan_code = win32api.MapVirtualKey(virtual_key, 0)
        key_down_lparam = 1 | (scan_code << 16)
        key_up_lparam = key_down_lparam | (1 << 30) | (1 << 31)
        win32gui.PostMessage(handle, win32con.WM_KEYDOWN, virtual_key, key_down_lparam)
        win32gui.PostMessage(handle, win32con.WM_KEYUP, virtual_key, key_up_lparam)

    def stop(self):
        """スレッド停止"""
        self.is_running = False


class OCRThread(QThread):
    """OCR処理スレッド"""
    status_updated = Signal(str)
    ocr_completed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, folder_name, settings):
        super().__init__()
        self.folder_name = folder_name
        self.settings = settings

    def run(self):
        try:
            self.status_updated.emit("Yomitoku: PDFを作成中...")
            ocr_output_dir = self.folder_name + "_ocr"
            pdf_page_dir = os.path.join(ocr_output_dir, "pdf_pages")
            markdown_page_dir = os.path.join(ocr_output_dir, "markdown_pages")
            os.makedirs(pdf_page_dir, exist_ok=True)
            os.makedirs(markdown_page_dir, exist_ok=True)

            self.run_yomitoku("pdf", pdf_page_dir)
            self.status_updated.emit("PDFを1冊に結合中...")
            self.merge_pdfs(pdf_page_dir, os.path.join(ocr_output_dir, "book.pdf"))

            self.status_updated.emit("Yomitoku: Markdownを作成中...")
            self.run_yomitoku("md", markdown_page_dir)
            self.status_updated.emit("Markdownを1冊に結合中...")
            self.merge_markdown(markdown_page_dir, os.path.join(ocr_output_dir, "book.md"))

            self.ocr_completed.emit(ocr_output_dir)

        except subprocess.CalledProcessError as e:
            self.error_occurred.emit(f"OCRエラー: {e.stderr}")
        except FileNotFoundError:
            self.error_occurred.emit("エラー: yomitokuがインストールされていません")
        except Exception as e:
            self.error_occurred.emit(str(e))

    def run_yomitoku(self, output_format: str, output_dir: str):
        yomitoku_exe = os.path.join(os.path.dirname(sys.executable), "yomitoku.exe")
        if not os.path.exists(yomitoku_exe):
            raise FileNotFoundError("Yomitokuの実行ファイルが見つかりません。")
        cmd = [yomitoku_exe, self.folder_name, "-f", output_format,
               "-o", output_dir, "-v", "--figure"]
        if self.settings['ocr_lite']:
            cmd.extend(["--lite", "-d", "cpu"])
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    @staticmethod
    def sort_key(path: str):
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path)]

    def merge_pdfs(self, source_dir: str, output_path: str):
        from pypdf import PdfReader, PdfWriter

        source_files = sorted(
            (os.path.join(source_dir, name) for name in os.listdir(source_dir) if name.lower().endswith(".pdf")),
            key=self.sort_key,
        )
        if not source_files:
            raise RuntimeError("YomitokuがPDFを出力しませんでした。")

        writer = PdfWriter()
        for source_file in source_files:
            for page in PdfReader(source_file).pages:
                writer.add_page(page)
        with open(output_path, "wb") as output_file:
            writer.write(output_file)

    def merge_markdown(self, source_dir: str, output_path: str):
        source_files = sorted(
            (os.path.join(source_dir, name) for name in os.listdir(source_dir) if name.lower().endswith(".md")),
            key=self.sort_key,
        )
        if not source_files:
            raise RuntimeError("YomitokuがMarkdownを出力しませんでした。")

        with open(output_path, "w", encoding="utf-8") as output_file:
            for index, source_file in enumerate(source_files, start=1):
                if index > 1:
                    output_file.write("\n\n---\n\n")
                output_file.write(f"<!-- Kindle capture {index} -->\n\n")
                with open(source_file, encoding="utf-8") as source:
                    output_file.write(source.read().strip())



class MainWindow(QMainWindow):
    """メインウィンドウ"""

    def __init__(self):
        super().__init__()
        self.region = None
        self.page_region = None
        self.region_window_size = None
        self.page_region_window_size = None
        self.target_handle = None
        self.capture_thread = None
        self.ocr_thread = None
        self.preview_pixmap = None

        self.setup_ui()
        self.apply_stylesheet()
        self.detect_kindle()

    def setup_ui(self):
        self.setWindowTitle("Kindle Screenshot Tool - Qt Edition")
        self.setMinimumSize(800, 900)

        # 中央ウィジェット
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # タイトル
        title = QLabel("Kindle Screenshot Tool")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50; padding: 10px;")
        main_layout.addWidget(title)

        # スプリッター（左：設定、右：プレビュー）
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, 1)

        # 左側：設定パネル
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(10)

        # 対象Kindle
        target_group = self.create_target_group()
        left_layout.addWidget(target_group)

        # キャプチャ領域設定
        region_group = self.create_region_group()
        left_layout.addWidget(region_group)

        # ページ番号検出設定
        page_group = self.create_page_detection_group()
        left_layout.addWidget(page_group)

        # キャプチャ設定
        capture_group = self.create_capture_settings_group()
        left_layout.addWidget(capture_group)

        # OCR設定
        ocr_group = self.create_ocr_settings_group()
        left_layout.addWidget(ocr_group)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # 右側：プレビューとログ
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(10)

        # プレビュー
        preview_group = self.create_preview_group()
        right_layout.addWidget(preview_group, 2)

        # ログ
        log_group = self.create_log_group()
        right_layout.addWidget(log_group, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([400, 400])

        # ステータスバー
        self.status_label = QLabel("待機中")
        self.status_label.setStyleSheet("font-size: 14px; padding: 5px;")
        main_layout.addWidget(self.status_label)

        # プログレスバー
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximum(0)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 制御ボタン
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.start_btn = QPushButton("開始")
        self.start_btn.setMinimumHeight(50)
        self.start_btn.clicked.connect(self.start_capture)
        button_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setMinimumHeight(50)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_capture)
        button_layout.addWidget(self.stop_btn)

        main_layout.addLayout(button_layout)

    def create_target_group(self) -> QGroupBox:
        """直接キャプチャするKindleウィンドウの設定。"""
        group = QGroupBox("対象のKindle")
        layout = QVBoxLayout()

        self.target_label = QLabel("Kindleを検出していません")
        self.target_label.setWordWrap(True)
        self.target_label.setStyleSheet("font-size: 11px; padding: 5px;")
        layout.addWidget(self.target_label)

        detect_btn = QPushButton("Kindleを再検出")
        detect_btn.clicked.connect(self.detect_kindle)
        layout.addWidget(detect_btn)

        group.setLayout(layout)
        return group

    def create_region_group(self) -> QGroupBox:
        """キャプチャ領域設定グループ"""
        group = QGroupBox("キャプチャ領域設定")
        layout = QVBoxLayout()

        self.region_label = QLabel(self.get_region_text())
        self.region_label.setStyleSheet("font-size: 11px; padding: 5px;")
        layout.addWidget(self.region_label)

        select_btn = QPushButton("本文領域をKindle全体に設定")
        select_btn.clicked.connect(self.select_region)
        layout.addWidget(select_btn)

        group.setLayout(layout)
        return group

    def create_page_detection_group(self) -> QGroupBox:
        """ページ番号検出設定グループ"""
        group = QGroupBox("ページ番号検出設定")
        layout = QVBoxLayout()

        self.auto_stop_check = QCheckBox("ページ番号を検出して自動停止")
        self.auto_stop_check.setChecked(True)
        layout.addWidget(self.auto_stop_check)

        self.page_region_label = QLabel(self.get_page_region_text())
        self.page_region_label.setStyleSheet("font-size: 10px; padding: 5px;")
        layout.addWidget(self.page_region_label)

        page_select_btn = QPushButton("ページ番号領域を下部に設定")
        page_select_btn.clicked.connect(self.select_page_region)
        layout.addWidget(page_select_btn)

        self.current_page_label = QLabel("現在のページ: 未確認")
        self.current_page_label.setStyleSheet("font-size: 12px; padding: 5px; font-weight: bold;")
        layout.addWidget(self.current_page_label)

        check_page_btn = QPushButton("ページ番号を確認")
        check_page_btn.clicked.connect(self.check_page_number)
        layout.addWidget(check_page_btn)

        group.setLayout(layout)
        return group

    def create_capture_settings_group(self) -> QGroupBox:
        """キャプチャ設定グループ"""
        group = QGroupBox("キャプチャ設定")
        layout = QVBoxLayout()

        # スクショ間隔
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("スクショ間隔 (秒):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 10)
        self.interval_spin.setValue(1)
        interval_layout.addWidget(self.interval_spin)
        interval_layout.addStretch()
        layout.addLayout(interval_layout)

        # ページ送り方向
        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("次ページのキー:"))
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("右矢印", "right")
        self.direction_combo.addItem("左矢印", "left")
        direction_layout.addWidget(self.direction_combo)
        direction_layout.addStretch()
        layout.addLayout(direction_layout)

        self.compatibility_mode_check = QCheckBox("互換モード（ページ送り時だけKindleを前面化）")
        self.compatibility_mode_check.setToolTip("直接キー送信が効かない場合だけ有効にしてください。")
        self.compatibility_mode_check.setChecked(True)
        layout.addWidget(self.compatibility_mode_check)

        # 出力フォルダ
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("出力フォルダ名:"))
        self.folder_edit = QLineEdit("output")
        folder_layout.addWidget(self.folder_edit)
        layout.addLayout(folder_layout)

        group.setLayout(layout)
        return group

    def create_ocr_settings_group(self) -> QGroupBox:
        """OCR設定グループ"""
        group = QGroupBox("Yomitoku OCR設定")
        layout = QVBoxLayout()

        self.enable_ocr_check = QCheckBox("OCR処理を有効化")
        self.enable_ocr_check.setChecked(True)
        layout.addWidget(self.enable_ocr_check)

        output_label = QLabel("出力: 結合PDF（book.pdf）+ 結合Markdown（book.md）")
        output_label.setWordWrap(True)
        layout.addWidget(output_label)

        # 軽量モード
        self.lite_mode_check = QCheckBox("軽量モード (CPU最適化)")
        layout.addWidget(self.lite_mode_check)

        group.setLayout(layout)
        return group

    def create_preview_group(self) -> QGroupBox:
        """プレビューグループ"""
        group = QGroupBox("プレビュー")
        layout = QVBoxLayout()

        self.preview_label = QLabel("プレビューはWindowsの画像ビューアで開きます")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(400, 300)
        self.preview_label.setStyleSheet("background-color: #ecf0f1; border: 1px solid #bdc3c7;")
        layout.addWidget(self.preview_label)

        preview_btn = QPushButton("プレビューを更新")
        preview_btn.clicked.connect(self.update_preview)
        layout.addWidget(preview_btn)

        group.setLayout(layout)
        return group

    def create_log_group(self) -> QGroupBox:
        """ロググループ"""
        group = QGroupBox("ログ")
        layout = QVBoxLayout()

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)

        group.setLayout(layout)
        return group

    def apply_stylesheet(self):
        """スタイルシート適用"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f6fa;
            }
            QGroupBox {
                font-weight: bold;
                border: 2px solid #3498db;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #2c3e50;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 8px 15px;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
            QLineEdit, QSpinBox, QComboBox {
                padding: 5px;
                border: 1px solid #bdc3c7;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox {
                spacing: 5px;
            }
            QProgressBar {
                border: 1px solid #bdc3c7;
                border-radius: 3px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #3498db;
            }
        """)

    def get_region_text(self) -> str:
        """領域テキスト取得"""
        if not self.region:
            return "本文領域: 未設定"
        return (f"左: {self.region['left']}, 上: {self.region['top']}, "
                f"幅: {self.region['width']}, 高さ: {self.region['height']}（Kindle基準）")

    def get_page_region_text(self) -> str:
        """ページ領域テキスト取得"""
        if not self.page_region:
            return "検出領域: 未設定"
        return (f"検出領域: 左: {self.page_region['left']}, 上: {self.page_region['top']}, "
                f"幅: {self.page_region['width']}, 高さ: {self.page_region['height']}（Kindle基準）")

    def get_target_error(self) -> Optional[str]:
        """選択済みKindleを安全に使えるか確認する。"""
        if not self.target_handle or not win32gui.IsWindow(self.target_handle):
            return "Kindleが見つかりません。「Kindleを再検出」を押してください。"
        if not win32gui.IsWindowVisible(self.target_handle):
            return "Kindleが非表示です。表示状態に戻してください。"
        if win32gui.IsIconic(self.target_handle):
            return "Kindleが最小化されています。最小化を解除してください。"
        if not is_kindle_window(self.target_handle):
            return "選択したKindleが終了したか、別のアプリに変わりました。再検出してください。"
        return None

    def validate_for_capture(self, include_page_region: bool = False) -> Optional[str]:
        error = self.get_target_error()
        if error:
            return error
        if not self.region:
            return "本文のキャプチャ領域を選択してください。"
        if include_page_region and self.auto_stop_check.isChecked() and not self.page_region:
            return "ページ番号による自動停止を使うには、ページ番号領域を選択してください。"

        current_size = get_window_size(self.target_handle)
        if self.region_window_size != current_size:
            return "Kindleのサイズが変わりました。本文領域を選択し直してください。"
        if (include_page_region and self.auto_stop_check.isChecked()
                and self.page_region_window_size != current_size):
            return "Kindleのサイズが変わりました。ページ番号領域を選択し直してください。"
        return None

    def detect_kindle(self):
        """起動中のKindleを検出し、最前面の候補を対象にする。"""
        candidates = find_kindle_windows()
        if not candidates:
            self.target_handle = None
            self.target_label.setText("Kindleが見つかりません。Windows版Kindleを起動してください。")
            if hasattr(self, "log_text"):
                self.log("Kindleを検出できませんでした")
            return

        self.target_handle = candidates[0]
        width, height = get_window_size(self.target_handle)
        title = win32gui.GetWindowText(self.target_handle) or "Kindle"
        extra = f"（{len(candidates)} 件検出。最前面の候補を選択）" if len(candidates) > 1 else ""
        self.target_label.setText(f"検出済み: {title} / {width}×{height}{extra}")
        if hasattr(self, "log_text"):
            self.log(f"Kindleを検出しました: {title} ({width}×{height})")

    def select_region(self):
        """本文領域をKindleウィンドウ全体へ安全に設定する。"""
        error = self.get_target_error()
        if error:
            QMessageBox.warning(self, "Kindleが必要です", error)
            return
        width, height = get_window_size(self.target_handle)
        self.region = {"left": 0, "top": 0, "width": width, "height": height}
        self.region_window_size = (width, height)
        self.region_label.setText(self.get_region_text())
        self.log("本文領域をKindle全体に設定しました")

    def select_page_region(self):
        """ページ番号表示がある下部帯を安全に設定する。"""
        error = self.get_target_error()
        if error:
            QMessageBox.warning(self, "Kindleが必要です", error)
            return
        width, height = get_window_size(self.target_handle)
        band_height = min(120, height)
        self.page_region = {"left": 0, "top": height - band_height, "width": width, "height": band_height}
        self.page_region_window_size = (width, height)
        self.page_region_label.setText(self.get_page_region_text())
        self.log("ページ番号領域をKindle下部に設定しました")

    def check_page_number(self):
        """今表示されているKindleのページ番号を画面上へ表示する。"""
        error = self.get_target_error()
        if error:
            QMessageBox.warning(self, "Kindleが必要です", error)
            return
        if not self.page_region:
            QMessageBox.warning(self, "ページ番号領域が必要です", "先に「ページ番号領域を下部に設定」を押してください。")
            return
        try:
            current_page, total_pages = read_page_number(self.target_handle, self.page_region)
            status = format_page_status(current_page, total_pages)
            self.current_page_label.setText(status)
            self.log(status)
        except Exception as e:
            QMessageBox.warning(self, "ページ番号の確認に失敗", str(e))

    def update_preview(self):
        """プレビュー更新"""
        try:
            error = self.validate_for_capture()
            if error:
                raise RuntimeError(error)
            img = crop_region(capture_window(self.target_handle), self.region)

            preview_path = os.path.join(tempfile.gettempdir(), "kindle2ss_preview.png")
            img.save(preview_path)
            os.startfile(preview_path)
            self.preview_label.setText("プレビューをWindowsの画像ビューアで開きました")
            self.log(f"プレビューを開きました: {preview_path}")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"プレビュー更新エラー: {e}")
            self.log(f"エラー: {e}")

    def start_capture(self):
        """キャプチャ開始"""
        if self.capture_thread and self.capture_thread.isRunning():
            return

        error = self.validate_for_capture(include_page_region=True)
        if error:
            QMessageBox.warning(self, "開始できません", error)
            self.log(f"開始を中止: {error}")
            return

        # 設定を収集
        settings = {
            'region': self.region,
            'page_region': self.page_region,
            'target_handle': self.target_handle,
            'auto_stop': self.auto_stop_check.isChecked(),
            'interval': self.interval_spin.value(),
            'page_direction': self.direction_combo.currentData(),
            'compatibility_mode': self.compatibility_mode_check.isChecked(),
            'folder_prefix': self.folder_edit.text(),
            'enable_ocr': self.enable_ocr_check.isChecked(),
            'ocr_lite': self.lite_mode_check.isChecked(),
        }

        # スレッド開始
        self.capture_thread = CaptureThread(settings)
        self.capture_thread.status_updated.connect(self.update_status)
        self.capture_thread.progress_updated.connect(self.update_progress)
        self.capture_thread.page_number_updated.connect(self.update_page_number)
        self.capture_thread.capture_completed.connect(self.on_capture_completed)
        self.capture_thread.error_occurred.connect(self.on_error)
        self.capture_thread.start()

        # UI更新
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.log("キャプチャを開始しました")

    def stop_capture(self):
        """キャプチャ停止"""
        if self.capture_thread:
            self.capture_thread.stop()
            self.capture_thread.wait()
            self.update_status("停止しました")
            self.log("キャプチャを停止しました")
            self.reset_ui()

    def on_capture_completed(self, folder_name, page_count):
        """キャプチャ完了"""
        self.update_status(f"キャプチャ完了: {page_count} 枚保存")
        self.log(f"キャプチャ完了: {folder_name} に {page_count} 枚保存")

        # OCR処理
        if self.enable_ocr_check.isChecked():
            settings = {
                'ocr_lite': self.lite_mode_check.isChecked(),
            }
            self.ocr_thread = OCRThread(folder_name, settings)
            self.ocr_thread.status_updated.connect(self.update_status)
            self.ocr_thread.ocr_completed.connect(self.on_ocr_completed)
            self.ocr_thread.error_occurred.connect(self.on_error)
            self.ocr_thread.start()
        else:
            self.reset_ui()

    def on_ocr_completed(self, output_dir):
        """OCR完了"""
        self.update_status(f"完了: book.pdf と book.md を保存しました")
        self.log(f"OCR処理完了: {output_dir}")
        QMessageBox.information(self, "完了", f"すべての処理が完了しました\nPDF: {output_dir}\\book.pdf\nMarkdown: {output_dir}\\book.md")
        self.reset_ui()

    def on_error(self, error_message):
        """エラー発生"""
        self.update_status(f"エラー: {error_message}")
        self.log(f"エラー: {error_message}")
        QMessageBox.critical(self, "エラー", error_message)
        self.reset_ui()

    def update_status(self, message):
        """ステータス更新"""
        self.status_label.setText(message)

    def update_progress(self, message):
        """進捗更新"""
        self.log(message)

    def update_page_number(self, message):
        """検出したページ番号を画面に表示する。"""
        self.current_page_label.setText(message)
        self.log(message)

    def log(self, message):
        """ログ追加"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def reset_ui(self):
        """UI リセット"""
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

    def closeEvent(self, event):
        """ウィンドウクローズイベント"""
        if self.capture_thread and self.capture_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "確認",
                "キャプチャ処理が実行中です。終了しますか？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.stop_capture()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Kindle Screenshot Tool")
    app.setOrganizationName("Kindle2SS")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
