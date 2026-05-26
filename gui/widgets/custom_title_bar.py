from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class CustomTitleBar(QWidget):
    def __init__(self, parent=None, title="", menu_bar=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self._parent = parent
        self._start_pos = None

        # Floating, perfectly centered title
        self.title_label = QLabel(title, self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(12)

        self.logo = QLabel("🚀")
        layout.addWidget(self.logo)

        if menu_bar:
            self.menu_bar = menu_bar
            layout.addWidget(menu_bar)

        layout.addStretch()

        self.btn_min = QPushButton("—")
        self.btn_min.setFixedSize(46, 32)
        self.btn_min.clicked.connect(self._parent.showMinimized)

        self.btn_max = QPushButton("☐")
        self.btn_max.setFixedSize(46, 32)
        self.btn_max.clicked.connect(self._toggle_max)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(46, 32)
        self.btn_close.clicked.connect(self._parent.close)

        # Container for window controls to set 0 spacing
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)
        controls_layout.addWidget(self.btn_min)
        controls_layout.addWidget(self.btn_max)
        controls_layout.addWidget(self.btn_close)

        layout.addLayout(controls_layout)
        self._apply_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "title_label"):
            self.title_label.setGeometry(0, 0, self.width(), self.height())

    def _apply_style(self, theme=None):
        if not theme:
            from gui.styling.theme import get_theme

            theme = get_theme()

        bg = theme.get("surface", "#1A1A1A")
        text = theme.get("text", "#FFFFFF")
        text2 = theme.get("text2", "#AAAAAA")
        # `border` is read for forward-compatible theming hooks; ruff flags it
        # as unused because the current stylesheet does not reference it yet.
        border = theme.get("border", "#333333")  # noqa: F841
        hover = theme.get("hover", "#2A2A32")

<<<<<<< HEAD
=======
        r = "0px" if self._parent.isMaximized() else "8px"

>>>>>>> origin/main
        # Subtle gradient or flat bg
        self.setStyleSheet(f"""
            CustomTitleBar {{
                background: {bg};
<<<<<<< HEAD
=======
                border-top-left-radius: {r};
                border-top-right-radius: {r};
>>>>>>> origin/main
            }}
            QLabel {{
                color: {text};
            }}
        """)

        self.title_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {text2}; letter-spacing: 0.5px;"
        )
        self.logo.setStyleSheet("font-size: 15px;")

        if hasattr(self, "menu_bar"):
            self.menu_bar.setStyleSheet(f"""
                QMenuBar {{
                    background: transparent;
                    color: {text2};
                    font-size: 13px;
                }}
                QMenuBar::item {{
                    background: transparent;
                    padding: 6px 10px;
                    border-radius: 4px;
                }}
                QMenuBar::item:selected {{
                    background: {hover};
                    color: {text};
                }}
                QMenuBar::item:pressed {{
                    background: {theme.get("accent", "#555555")};
                    color: #FFFFFF;
                }}
            """)

        btn_style = f"""
            QPushButton {{ border: none; background: transparent; font-size: 14px; color: {text}; }}
            QPushButton:hover {{ background: {hover}; }}
        """
        close_style = f"""
            QPushButton {{ border: none; background: transparent; font-size: 14px; color: {text}; }}
            QPushButton:hover {{ background: #E81123; color: white; }}
        """

        self.btn_min.setStyleSheet(btn_style)
        self.btn_max.setStyleSheet(btn_style)
        self.btn_close.setStyleSheet(close_style)

    def _toggle_max(self):
        if self._parent.isMaximized():
            self._parent.showNormal()
            self.btn_max.setText("☐")
        else:
            self._parent.showMaximized()
            self.btn_max.setText("🗗")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            wh = self.window().windowHandle()
            if wh:
                wh.startSystemMove()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        self._toggle_max()
