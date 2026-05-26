"""
Quantum Orbital Spinner — Premium indeterminate progress indicator.
Replaces standard spinners with an electron-orbital animation effect.
"""

import math
from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class QuantumSpinner(QWidget):
    """Orbital electron-style loading spinner with glowing trails."""

    def __init__(self, parent=None, color: str = "#5C8AFF", size: int = 40):
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0.0
        self._orbiting = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(30)  # ~33 fps
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

    def start(self):
        self._orbiting = True
        self._angle = 0.0
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._orbiting = False
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle += 0.15
        if self._angle > 2 * math.pi:
            self._angle -= 2 * math.pi
        self.update()

    def paintEvent(self, event):
        if not self._orbiting:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        radius = self.width() / 2 - 4
        base_alpha = 200

        # Draw 3 orbiting electrons with fading trails
        for i in range(3):
            offset = i * 2.094  # 120 degrees apart
            angle = self._angle + offset
            ex = cx + radius * 0.7 * math.cos(angle)
            ey = cy + radius * 0.7 * math.sin(angle)

            # Glow trail (larger, transparent)
            for t in range(3):
                t_angle = angle - t * 0.3
                tx = cx + radius * 0.7 * math.cos(t_angle)
                ty = cy + radius * 0.7 * math.sin(t_angle)
                trail_alpha = base_alpha // (t + 3)
                trail_size = 4 - t
                if trail_size > 0:
                    trail_color = QColor(self._color)
                    trail_color.setAlpha(trail_alpha)
                    painter.setBrush(QBrush(trail_color))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(QPointF(tx, ty), trail_size, trail_size)

            # Electron core
            core_color = QColor(self._color)
            core_color.setAlpha(base_alpha)
            painter.setBrush(QBrush(core_color))
            painter.setPen(QPen(QColor(self._color), 1))
            painter.drawEllipse(QPointF(ex, ey), 3.5, 3.5)

            # Orbit ring segment
            painter.setPen(QPen(QColor(self._color).lighter(120), 0.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), radius * 0.7, radius * 0.7)

        painter.end()


class PulsingIndicator:
    """Mixin for pulsing glow effect on metric values."""

    @staticmethod
    def create_pulse_animation(widget, color_key: str = "accent_light", interval_ms: int = 1500):
        """Create a pulsing timer that oscillates opacity."""
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtGui import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(interval_ms)
        anim.setStartValue(0.85)
        anim.setKeyValueAt(0.5, 1.0)
        anim.setEndValue(0.85)
        anim.setLoopCount(-1)  # infinite
        return anim