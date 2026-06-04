"""
Splash Screen — кастомний сплеш-скрін AGREGAT з малюванням QPainter.
"""

from PySide6.QtWidgets import QSplashScreen
from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
from PySide6.QtCore import Qt


def create_agregat_splash(theme_choice: str = "dark") -> QSplashScreen:
    """
    Створює і повертає QSplashScreen з логотипом AGREGAT,
    підлаштований під тему (dark/light).
    """
    splash_pix = QPixmap(600, 350)
    splash_pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(splash_pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    bg_color = QColor("#0d1117") if theme_choice == "dark" else QColor("#ffffff")
    painter.setBrush(bg_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(splash_pix.rect(), 24, 24)

    # Draw elegant text
    font_title = QFont("Segoe UI", 52, QFont.Weight.Black)
    font_title.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    font_title.setHintingPreference(QFont.HintingPreference.PreferFullHinting)

    font_sub = QFont("Segoe UI", 12, QFont.Weight.Bold)
    font_sub.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
    font_sub.setHintingPreference(QFont.HintingPreference.PreferFullHinting)

    text_color = QColor("#c9d1d9") if theme_choice == "dark" else QColor("#24292f")
    accent_color = QColor("#e34c26")

    painter.setFont(font_title)
    painter.setPen(text_color)
    painter.drawText(splash_pix.rect(), Qt.AlignmentFlag.AlignCenter, "AGREGAT")

    painter.setFont(font_sub)
    painter.setPen(accent_color)
    rect_sub = splash_pix.rect()
    rect_sub.setTop(rect_sub.height() // 2 + 50)
    painter.drawText(
        rect_sub, Qt.AlignmentFlag.AlignCenter,
        "INSTITUTIONAL QUANT PLATFORM • PRIVATE BETA PROTOTYPE"
    )
    painter.end()

    splash = QSplashScreen(
        splash_pix,
        Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint,
    )
    splash.setMask(splash_pix.mask())
    return splash