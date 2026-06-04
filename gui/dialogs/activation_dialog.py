"""
Activation Dialog — красиве вікно активації AGREGAT з HWID та вводом ліцензійного ключа.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit
)
from PySide6.QtGui import QFont as _QFont

from engine.security.license_manager import save_license_key, verify_license


def show_activation_dialog(hwid: str, app=None) -> bool:
    """
    Показує красиве вікно активації з полем для вводу ключа.
    Повертає True, якщо діалог прийнято (ключ збережено й валідовано).
    """
    dlg = QDialog()
    dlg.setWindowTitle("AGREGAT — Активація")
    dlg.setFixedSize(520, 380)
    dlg.setStyleSheet("""
        QDialog { background: #0d1117; }
        QLabel { color: #c9d1d9; }
        QLineEdit, QTextEdit { 
            background: #161b22; color: #e6edf3; border: 1px solid #30363d;
            border-radius: 6px; padding: 8px; font-family: 'Consolas'; font-size: 13px;
        }
        QPushButton {
            background: #238636; color: white; border: none; border-radius: 6px;
            padding: 10px 24px; font-weight: bold; font-size: 14px;
        }
        QPushButton:hover { background: #2ea043; }
        QPushButton#copyBtn {
            background: #30363d; padding: 6px 16px; font-size: 12px;
        }
        QPushButton#copyBtn:hover { background: #484f58; }
    """)

    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)

    title = QLabel("AGREGAT — Private Beta")
    title.setFont(_QFont("Segoe UI", 18, _QFont.Weight.Bold))
    title.setStyleSheet("color: #58a6ff;")
    layout.addWidget(title)

    layout.addWidget(QLabel("Ваш Hardware ID (надішліть розробнику):"))

    hwid_row = QHBoxLayout()
    hwid_field = QLineEdit(hwid)
    hwid_field.setReadOnly(True)
    hwid_row.addWidget(hwid_field)

    copy_btn = QPushButton("Копіювати")
    copy_btn.setObjectName("copyBtn")
    copy_btn.clicked.connect(
        lambda: (app.clipboard().setText(hwid) if app else None,
                 copy_btn.setText("✓ Скопійовано!"))
    )
    hwid_row.addWidget(copy_btn)
    layout.addLayout(hwid_row)

    layout.addWidget(QLabel("Вставте ліцензійний ключ від розробника:"))

    key_input = QTextEdit()
    key_input.setPlaceholderText("Вставте ключ сюди...")
    key_input.setMaximumHeight(80)
    layout.addWidget(key_input)

    status_label = QLabel("")
    status_label.setStyleSheet("color: #f85149; font-weight: bold;")
    layout.addWidget(status_label)

    activate_btn = QPushButton("Активувати")

    def on_activate():
        raw_key = key_input.toPlainText().strip()
        if not raw_key:
            status_label.setText("Введіть ключ!")
            return
        if save_license_key(raw_key):
            ok, msg = verify_license()
            if ok:
                dlg.accept()
            else:
                if msg.startswith("NO_LICENSE"):
                    status_label.setText("Невірний ключ.")
                else:
                    status_label.setText(msg)
        else:
            status_label.setText("Помилка збереження ключа.")

    activate_btn.clicked.connect(on_activate)
    layout.addWidget(activate_btn)

    return dlg.exec() == QDialog.DialogCode.Accepted