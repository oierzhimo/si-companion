"""Adaptive Windows & macOS Light / Dark theme palettes and stylesheets for SiDesktopPet."""
from __future__ import annotations

import subprocess
import sys
import weakref

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

LIGHT_COLORS = {
    'window_bg': '#f3efe6',
    'dialog_bg': '#f7f4ec',
    'sidebar_bg': '#faf8f2',
    'card_bg': '#fcfaf5',
    'input_bg': '#ffffff',
    'text': '#1f2624',
    'brand_text': '#a93b32',
    'headline_text': '#222c29',
    'muted_text': '#626a65',
    'nav_text': '#555f59',
    'nav_checked_bg': '#e9e4d7',
    'nav_checked_text': '#1c2421',
    'border': '#cfc9ba',
    'border_soft': '#e4dfd3',
    'btn_bg': '#faf8f3',
    'btn_hover': '#eeeae0',
    'btn_hover_border': '#b59348',
    'btn_pressed': '#e2ddd0',
    'btn_disabled_text': '#969c95',
    'primary_bg': '#293430',
    'primary_hover': '#394842',
    'primary_text': '#fbf8f0',
    'gold_accent': '#b88c38',
    'seal_bg': '#b63d33',
    'seal_text': '#fcf7ef',
    'jade_accent': '#5b8772',
    'slider_groove': '#dfd9cb',
    'slider_fill': '#789b87',
    'slider_handle': '#b88c38',
    'check_bg': '#ffffff',
    'check_border': '#aba392',
    'check_checked': '#2e4d41',
    'progress_bg': '#e6e1d5',
    'progress_chunk': '#b88c38',
    'selection_bg': '#dce6de',
    'selection_text': '#17211e',
    'menu_bg': '#faf7f0',
    'menu_text': '#1f2624',
    'menu_border': '#c9c2b2',
    'menu_selected': '#eae5d7',
    'preview_grad0': '#eae5d8',
    'preview_grad1': '#d5ddd4',
    'preview_glow': (255, 250, 238, 72),
    'preview_text': '#48574f',
    'journal_meta': '#737a74',
    'bubble_user_bg': '#eae4d6',
    'bubble_user_text': '#1f2624',
    'bubble_pet_bg': '#f2f5f1',
    'bubble_pet_border': '#c8d3ca',
    'bubble_pet_text': '#1e2623',
}

DARK_COLORS = {
    'window_bg': '#141716',
    'dialog_bg': '#181c1a',
    'sidebar_bg': '#1a1f1d',
    'card_bg': '#202624',
    'input_bg': '#121614',
    'text': '#edeae1',
    'brand_text': '#df6c5c',
    'headline_text': '#e7e2d5',
    'muted_text': '#a6ada7',
    'nav_text': '#b4bbb5',
    'nav_checked_bg': '#2b3330',
    'nav_checked_text': '#f3efe5',
    'border': '#38413d',
    'border_soft': '#2a322f',
    'btn_bg': '#242c29',
    'btn_hover': '#2e3834',
    'btn_hover_border': '#c9a456',
    'btn_pressed': '#394540',
    'btn_disabled_text': '#6b726d',
    'primary_bg': '#365146',
    'primary_hover': '#436557',
    'primary_text': '#f7f3ea',
    'gold_accent': '#d4af5f',
    'seal_bg': '#b64035',
    'seal_text': '#fbf6ee',
    'jade_accent': '#79a38e',
    'slider_groove': '#2d3632',
    'slider_fill': '#6d9782',
    'slider_handle': '#d4af5f',
    'check_bg': '#141917',
    'check_border': '#58645d',
    'check_checked': '#6b9680',
    'progress_bg': '#272f2b',
    'progress_chunk': '#d4af5f',
    'selection_bg': '#32473e',
    'selection_text': '#f4f1e8',
    'menu_bg': '#1d2320',
    'menu_text': '#edeae1',
    'menu_border': '#3a443f',
    'menu_selected': '#2c3631',
    'preview_grad0': '#1d2421',
    'preview_grad1': '#27322e',
    'preview_glow': (212, 175, 95, 26),
    'preview_text': '#c4c8be',
    'journal_meta': '#9ea8a0',
    'bubble_user_bg': '#2a332f',
    'bubble_user_text': '#edeae1',
    'bubble_pet_bg': '#1f2824',
    'bubble_pet_border': '#36443e',
    'bubble_pet_text': '#e9e6dd',
}


def _windows_apps_dark_mode() -> bool | None:
    if sys.platform != 'win32' or winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
        ) as key:
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return int(value) == 0
    except OSError:
        return None


def _macos_dark_mode() -> bool | None:
    if sys.platform != 'darwin':
        return None
    try:
        res = subprocess.run(
            ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=1.0,
            check=False,
        )
        if res.returncode == 0 and 'dark' in (res.stdout or '').lower():
            return True
        return False
    except Exception:
        return None


def is_dark_theme(app=None) -> bool:
    """Return True when the active OS/Qt theme is dark mode."""
    app = app or QApplication.instance()
    if app is not None:
        try:
            hints = app.styleHints()
            if hasattr(hints, 'colorScheme'):
                scheme = hints.colorScheme()
                color_scheme_enum = getattr(Qt, 'ColorScheme', None)
                if color_scheme_enum is not None:
                    if scheme == color_scheme_enum.Dark:
                        return True
                    if scheme == color_scheme_enum.Light:
                        return False
        except (RuntimeError, AttributeError):
            pass

    win_dark = _windows_apps_dark_mode()
    if win_dark is not None:
        return win_dark

    mac_dark = _macos_dark_mode()
    if mac_dark is not None:
        return mac_dark

    if app is not None:
        try:
            return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
        except (RuntimeError, AttributeError):
            pass
    return False


def theme_colors(dark: bool | None = None) -> dict:
    if dark is None:
        dark = is_dark_theme()
    return dict(DARK_COLORS if dark else LIGHT_COLORS)


def panel_stylesheet(dark: bool | None = None) -> str:
    c = theme_colors(dark)
    return (
        f'QWidget{{font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px;color:{c["text"]};}}'
        f'QWidget#panel{{background:{c["window_bg"]};}}'
        f'QFrame#sidebar{{background:{c["sidebar_bg"]};border-right:1px solid {c["border_soft"]};}}'
        f'QLabel{{background:transparent;color:{c["text"]};}}'
        f'QLabel#brand{{font-size:33px;font-weight:650;color:{c["brand_text"]};}}'
        f'QLabel#seal{{background:{c["seal_bg"]};color:{c["seal_text"]};font-size:28px;font-weight:700;border-radius:10px;border:1px solid {c["gold_accent"]};padding:4px;}}'
        f'QLabel#muted{{color:{c["muted_text"]};font-size:12px;}}'
        f'QLabel#headline{{font-size:28px;font-weight:650;color:{c["headline_text"]};}}'
        f'QLabel#eyebrow{{font-size:11px;color:{c["muted_text"]};letter-spacing:2px;}}'
        f'QLabel#subhead{{font-size:16px;font-weight:600;color:{c["text"]};}}'
        f'QFrame#card{{background:{c["card_bg"]};border:1px solid {c["border_soft"]};border-radius:18px;}}'
        f'QPushButton{{background:{c["btn_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:11px;padding:10px 15px;text-align:left;}}'
        f'QPushButton:hover{{background:{c["btn_hover"]};border-color:{c["btn_hover_border"]};}}'
        f'QPushButton:pressed{{background:{c["btn_pressed"]};}}'
        f'QPushButton:disabled{{color:{c["btn_disabled_text"]};}}'
        f'QPushButton#actionTile{{text-align:center;padding:9px 10px;border-radius:10px;}}'
        f'QPushButton#chip{{text-align:center;padding:5px 11px;border-radius:8px;font-size:12px;}}'
        f'QPushButton#chip:checked{{background:{c["primary_bg"]};color:{c["primary_text"]};border-color:{c["gold_accent"]};font-weight:600;}}'
        f'QPushButton#primary{{background:{c["primary_bg"]};color:{c["primary_text"]};border:1px solid {c["gold_accent"]};font-weight:600;text-align:center;}}'
        f'QPushButton#primary:hover{{background:{c["primary_hover"]};}}'
        f'QPushButton#nav{{border:0;background:transparent;color:{c["nav_text"]};padding:12px 14px;border-radius:10px;}}'
        f'QPushButton#nav:checked{{background:{c["nav_checked_bg"]};color:{c["nav_checked_text"]};font-weight:600;border-left:3px solid {c["gold_accent"]};}}'
        f'QSlider::groove:horizontal{{height:5px;background:{c["slider_groove"]};border-radius:2px;}}'
        f'QSlider::sub-page:horizontal{{background:{c["slider_fill"]};border-radius:2px;}}'
        f'QSlider::handle:horizontal{{background:{c["slider_handle"]};width:16px;margin:-6px 0;border-radius:8px;}}'
        f'QCheckBox{{spacing:10px;padding:6px 0;background:transparent;color:{c["text"]};}}'
        f'QCheckBox::indicator{{width:18px;height:18px;border:2px solid {c["check_border"]};border-radius:6px;background:{c["check_bg"]};}}'
        f'QCheckBox::indicator:hover{{border-color:{c["gold_accent"]};}}'
        f'QCheckBox::indicator:checked{{background:{c["primary_text"]};border:5px solid {c["check_checked"]};}}'
        f'QLineEdit,QTextEdit,QPlainTextEdit,QSpinBox{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:6px 9px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QComboBox{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:7px;}}'
        f'QComboBox QAbstractItemView{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QScrollArea{{border:0;background:transparent;}}QScrollArea>QWidget>QWidget{{background:transparent;}}'
        f'QTextBrowser{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border_soft"]};border-radius:14px;padding:13px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QProgressBar{{background:{c["progress_bg"]};color:{c["text"]};border:0;border-radius:4px;height:8px;text-align:center;}}'
        f'QProgressBar::chunk{{background:{c["progress_chunk"]};border-radius:4px;}}'
    )


def dialog_stylesheet(dark: bool | None = None) -> str:
    c = theme_colors(dark)
    return (
        f'QDialog{{background:{c["dialog_bg"]};color:{c["text"]};font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px;}}'
        f'QDialog QLabel{{background:transparent;color:{c["text"]};}}'
        f'QDialog QLabel#muted{{color:{c["muted_text"]};font-size:12px;}}'
        f'QDialog QFrame#drawer{{background:{c["card_bg"]};border:1px solid {c["border_soft"]};border-radius:12px;}}'
        f'QDialog QLineEdit,QDialog QTextEdit,QDialog QPlainTextEdit,QDialog QSpinBox,QDialog QListWidget{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:6px 8px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QTextBrowser{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:12px;padding:12px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QComboBox{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:6px 10px;}}'
        f'QDialog QComboBox QAbstractItemView,QListView{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QPushButton{{background:{c["btn_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:7px 14px;text-align:center;}}'
        f'QDialog QPushButton:hover{{background:{c["btn_hover"]};border-color:{c["btn_hover_border"]};}}'
        f'QDialog QPushButton:pressed{{background:{c["btn_pressed"]};}}'
        f'QDialog QPushButton:disabled{{color:{c["btn_disabled_text"]};}}'
        f'QDialog QPushButton#primary{{background:{c["primary_bg"]};color:{c["primary_text"]};border:1px solid {c["gold_accent"]};font-weight:600;}}'
        f'QDialog QPushButton#primary:hover{{background:{c["primary_hover"]};}}'
        f'QDialog QPushButton#chip{{padding:5px 10px;font-size:12px;}}'
        f'QDialog QCheckBox{{background:transparent;color:{c["text"]};spacing:8px;}}'
        f'QDialog QCheckBox::indicator{{width:16px;height:16px;border:2px solid {c["check_border"]};border-radius:5px;background:{c["check_bg"]};}}'
        f'QDialog QCheckBox::indicator:checked{{background:{c["primary_text"]};border:4px solid {c["check_checked"]};}}'
    )


def menu_stylesheet(dark: bool | None = None) -> str:
    c = theme_colors(dark)
    return (
        f'QMenu{{background:{c["menu_bg"]};color:{c["menu_text"]};padding:8px;border:1px solid {c["menu_border"]};border-radius:8px;}}'
        f'QMenu::item{{padding:8px 24px;border-radius:6px;}}'
        f'QMenu::item:selected{{background:{c["menu_selected"]};color:{c["menu_text"]};}}'
    )


def bind_theme(widget, apply_fn) -> None:
    """Apply theme immediately and subscribe safely to OS color-scheme changes."""
    apply_fn()
    app = QApplication.instance()
    if app is None:
        return
    try:
        hints = app.styleHints()
        sig = getattr(hints, 'colorSchemeChanged', None)
        if sig is None:
            return
    except (RuntimeError, AttributeError):
        return

    ref = weakref.ref(widget)

    def _on_scheme_changed(*_args):
        target = ref()
        if target is None:
            try:
                sig.disconnect(_on_scheme_changed)
            except (RuntimeError, TypeError):
                pass
            return
        try:
            apply_fn()
        except RuntimeError:
            pass

    sig.connect(_on_scheme_changed)

    def _cleanup(*_args):
        try:
            sig.disconnect(_on_scheme_changed)
        except (RuntimeError, TypeError):
            pass

    try:
        widget.destroyed.connect(_cleanup)
    except (RuntimeError, AttributeError):
        pass
