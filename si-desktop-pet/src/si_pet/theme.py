"""Adaptive Windows Light / Dark theme palettes and stylesheets for SiDesktopPet."""
from __future__ import annotations

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
    'window_bg': '#eef3ee',
    'dialog_bg': '#f4f7f4',
    'sidebar_bg': '#fafcf8',
    'card_bg': '#fafcf9',
    'input_bg': '#ffffff',
    'text': '#1c362f',
    'brand_text': '#214536',
    'headline_text': '#204234',
    'muted_text': '#586c60',
    'nav_text': '#566b5e',
    'nav_checked_bg': '#e3ede0',
    'nav_checked_text': '#1d3b2e',
    'border': '#c8d7c9',
    'border_soft': '#dbe5db',
    'btn_bg': '#fafcf8',
    'btn_hover': '#e2eee2',
    'btn_hover_border': '#98b29d',
    'btn_pressed': '#ccdcca',
    'btn_disabled_text': '#8fa196',
    'primary_bg': '#2f5946',
    'primary_hover': '#406f58',
    'primary_text': '#ffffff',
    'slider_groove': '#dbe5d9',
    'slider_fill': '#6a9575',
    'slider_handle': '#2e5945',
    'check_bg': '#ffffff',
    'check_border': '#9bb59e',
    'check_checked': '#547d5d',
    'progress_bg': '#e1eade',
    'progress_chunk': '#789b75',
    'selection_bg': '#cfe2d2',
    'selection_text': '#142a24',
    'menu_bg': '#f7faf7',
    'menu_text': '#183b35',
    'menu_border': '#bdd3c5',
    'menu_selected': '#dcece2',
    'preview_grad0': '#e1ead8',
    'preview_grad1': '#c7d9c8',
    'preview_glow': (250, 253, 242, 65),
    'preview_text': '#44634d',
    'journal_meta': '#6f8274',
}

DARK_COLORS = {
    'window_bg': '#151b18',
    'dialog_bg': '#19201c',
    'sidebar_bg': '#1c241f',
    'card_bg': '#212b26',
    'input_bg': '#141a17',
    'text': '#eaf3ed',
    'brand_text': '#cbe8d7',
    'headline_text': '#d7efe1',
    'muted_text': '#a7bab0',
    'nav_text': '#b7c9bf',
    'nav_checked_bg': '#2c3d34',
    'nav_checked_text': '#eef8f2',
    'border': '#394a41',
    'border_soft': '#2c3932',
    'btn_bg': '#26322c',
    'btn_hover': '#32423a',
    'btn_hover_border': '#5f826d',
    'btn_pressed': '#3d5147',
    'btn_disabled_text': '#6b7e73',
    'primary_bg': '#366c53',
    'primary_hover': '#458366',
    'primary_text': '#ffffff',
    'slider_groove': '#2f3e36',
    'slider_fill': '#5f9674',
    'slider_handle': '#8bc4a6',
    'check_bg': '#141a17',
    'check_border': '#556e60',
    'check_checked': '#4e8668',
    'progress_bg': '#28342e',
    'progress_chunk': '#679f7b',
    'selection_bg': '#325242',
    'selection_text': '#f4faf6',
    'menu_bg': '#1f2823',
    'menu_text': '#eaf3ed',
    'menu_border': '#3b4e43',
    'menu_selected': '#304338',
    'preview_grad0': '#1f2923',
    'preview_grad1': '#293931',
    'preview_glow': (145, 195, 165, 28),
    'preview_text': '#bdd7c7',
    'journal_meta': '#a2bcae',
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
        f'QWidget{{font-family:"Microsoft YaHei UI","Segoe UI";font-size:13px;color:{c["text"]};}}'
        f'QWidget#panel{{background:{c["window_bg"]};}}'
        f'QFrame#sidebar{{background:{c["sidebar_bg"]};border-right:1px solid {c["border_soft"]};}}'
        f'QLabel{{background:transparent;color:{c["text"]};}}'
        f'QLabel#brand{{font-size:33px;font-weight:650;color:{c["brand_text"]};}}'
        f'QLabel#muted{{color:{c["muted_text"]};font-size:12px;}}'
        f'QLabel#headline{{font-size:30px;font-weight:650;color:{c["headline_text"]};}}'
        f'QLabel#eyebrow{{font-size:11px;color:{c["muted_text"]};letter-spacing:2px;}}'
        f'QLabel#subhead{{font-size:18px;font-weight:600;color:{c["text"]};}}'
        f'QFrame#card{{background:{c["card_bg"]};border:1px solid {c["border_soft"]};border-radius:19px;}}'
        f'QPushButton{{background:{c["btn_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:12px;padding:11px 17px;text-align:left;}}'
        f'QPushButton:hover{{background:{c["btn_hover"]};border-color:{c["btn_hover_border"]};}}'
        f'QPushButton:pressed{{background:{c["btn_pressed"]};}}'
        f'QPushButton:disabled{{color:{c["btn_disabled_text"]};}}'
        f'QPushButton#primary{{background:{c["primary_bg"]};color:{c["primary_text"]};border:0;font-weight:600;}}'
        f'QPushButton#primary:hover{{background:{c["primary_hover"]};}}'
        f'QPushButton#nav{{border:0;background:transparent;color:{c["nav_text"]};padding:13px 16px;border-radius:11px;}}'
        f'QPushButton#nav:checked{{background:{c["nav_checked_bg"]};color:{c["nav_checked_text"]};font-weight:600;}}'
        f'QSlider::groove:horizontal{{height:5px;background:{c["slider_groove"]};border-radius:2px;}}'
        f'QSlider::sub-page:horizontal{{background:{c["slider_fill"]};border-radius:2px;}}'
        f'QSlider::handle:horizontal{{background:{c["slider_handle"]};width:16px;margin:-6px 0;border-radius:8px;}}'
        f'QCheckBox{{spacing:10px;padding:8px 0;background:transparent;color:{c["text"]};}}'
        f'QCheckBox::indicator{{width:17px;height:17px;border:1px solid {c["check_border"]};border-radius:5px;background:{c["check_bg"]};}}'
        f'QCheckBox::indicator:checked{{background:{c["check_checked"]};border-color:{c["check_checked"]};}}'
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
        f'QDialog{{background:{c["dialog_bg"]};color:{c["text"]};font-family:"Microsoft YaHei UI","Segoe UI";font-size:13px;}}'
        f'QDialog QLabel{{background:transparent;color:{c["text"]};}}'
        f'QDialog QLineEdit,QDialog QTextEdit,QDialog QPlainTextEdit,QDialog QSpinBox,QDialog QListWidget{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:6px 8px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QTextBrowser{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:10px;padding:10px;selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QComboBox{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:6px 10px;}}'
        f'QDialog QComboBox QAbstractItemView,QListView{{background:{c["input_bg"]};color:{c["text"]};border:1px solid {c["border"]};selection-background-color:{c["selection_bg"]};selection-color:{c["selection_text"]};}}'
        f'QDialog QPushButton{{background:{c["btn_bg"]};color:{c["text"]};border:1px solid {c["border"]};border-radius:8px;padding:7px 14px;text-align:center;}}'
        f'QDialog QPushButton:hover{{background:{c["btn_hover"]};border-color:{c["btn_hover_border"]};}}'
        f'QDialog QPushButton:pressed{{background:{c["btn_pressed"]};}}'
        f'QDialog QPushButton:disabled{{color:{c["btn_disabled_text"]};}}'
        f'QDialog QCheckBox{{background:transparent;color:{c["text"]};spacing:8px;}}'
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
