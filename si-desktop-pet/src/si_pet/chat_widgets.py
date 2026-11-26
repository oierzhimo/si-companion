"""Shared styled UI helpers for chat dialogs (HTML transcript bubbles & collapsible config drawer)."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .storage import resource_root
from .theme import theme_colors


def portrait_file_uri(mood: str = 'neutral') -> str:
    """Return a local file:// URI to a rendered bust portrait if present."""
    pdir = resource_root() / 'assets' / 'portraits'
    for name in (f'{mood}.png', 'neutral.png'):
        cand = pdir / name
        if cand.is_file():
            return QUrl.fromLocalFile(str(cand)).toString()
    return ''


def render_transcript_html(turns: list[dict], portrait_mood: str = 'neutral') -> str:
    """Render plain-text-safe HTML chat bubbles with 祀's bust portrait.

    All user and assistant text is escaped via ``html.escape`` and keeps literal
    ``管理员：`` and ``祀：`` prefixes so ``QTextBrowser.toPlainText()`` matches
    plain-text expectations while displaying clean bubbles in the UI.
    """
    if not turns:
        return ''
    c = theme_colors()
    avatar_uri = portrait_file_uri(portrait_mood)
    parts: list[str] = [
        f'<div style="font-family:\'Microsoft YaHei UI\',\'Segoe UI\';font-size:13px;line-height:1.5;">'
    ]
    for turn in turns:
        user_raw = str(turn.get('user', ''))
        atts = turn.get('attachments')
        badges = ''
        if isinstance(atts, list) and atts:
            badge_str = ' '.join(
                f"[{a.get('kind', 'file')}:{a.get('name', '附件')}]"
                for a in atts
                if isinstance(a, dict)
            )
            if badge_str:
                badges = ' ' + badge_str
        user_html = html.escape(user_raw + badges).replace('\n', '<br>')
        asst_html = html.escape(str(turn.get('assistant', ''))).replace('\n', '<br>')

        parts.append(
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0 4px 0;">'
            f'<tr><td width="16%"></td>'
            f'<td width="84%" align="right">'
            f'<table cellspacing="0" cellpadding="9" style="background:{c["bubble_user_bg"]};'
            f'border:1px solid {c["border_soft"]};border-radius:10px;">'
            f'<tr><td style="color:{c["bubble_user_text"]};text-align:left;">'
            f'<span style="color:{c["muted_text"]};font-weight:600;">管理员：</span>{user_html}'
            f'</td></tr></table></td></tr></table>'
        )

        avatar_cell = (
            f'<td width="40" valign="top" style="padding:2px 6px 0 0;">'
            f'<img src="{avatar_uri}" width="34" height="34"></td>'
            if avatar_uri
            else '<td width="4"></td>'
        )
        parts.append(
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:4px 0 10px 0;">'
            f'<tr>{avatar_cell}'
            f'<td valign="top">'
            f'<table cellspacing="0" cellpadding="9" style="background:{c["bubble_pet_bg"]};'
            f'border:1px solid {c["bubble_pet_border"]};border-radius:10px;">'
            f'<tr><td style="color:{c["bubble_pet_text"]};">'
            f'<span style="color:{c["gold_accent"]};font-weight:600;">祀：</span>{asst_html}'
            f'</td></tr></table></td><td width="12%"></td></tr></table>'
        )
    parts.append('</div>')
    return ''.join(parts)


def build_config_drawer(form_layout, expanded: bool = False) -> tuple[QWidget, QLabel, QPushButton, QFrame]:
    """Wrap a connection/profile QFormLayout inside a collapsible top drawer."""
    wrapper = QWidget()
    wl = QVBoxLayout(wrapper)
    wl.setContentsMargins(0, 0, 0, 0)
    wl.setSpacing(6)

    bar = QHBoxLayout()
    bar.setContentsMargins(0, 0, 0, 0)
    summary = QLabel('')
    summary.setObjectName('muted')
    summary.setWordWrap(True)
    toggle = QPushButton('收起设置 ▾' if expanded else '连接与角色设置 ▸')
    toggle.setObjectName('chip')
    toggle.setAutoDefault(False)
    toggle.setDefault(False)
    bar.addWidget(summary, 1)
    bar.addWidget(toggle)
    wl.addLayout(bar)

    drawer = QFrame()
    drawer.setObjectName('drawer')
    dl = QVBoxLayout(drawer)
    dl.setContentsMargins(12, 10, 12, 10)
    dl.addLayout(form_layout)
    drawer.setVisible(expanded)
    wl.addWidget(drawer)

    def _toggle():
        vis = not drawer.isVisible()
        drawer.setVisible(vis)
        toggle.setText('收起设置 ▾' if vis else '连接与角色设置 ▸')

    toggle.clicked.connect(_toggle)
    return wrapper, summary, toggle, drawer
