"""
AGREGAT Activation Dialog — Online-only server-authoritative activation.

C-05: Legacy offline (HWID/XOR/HMAC) activation has been REMOVED.
Only server-authoritative licensing with Ed25519 signed receipts is supported.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
)
from PySide6.QtGui import QFont as _QFont

from engine.security.license_client import (
    activate_license_online,
    generate_device_fingerprint,
    get_os_info,
)


def show_activation_dialog_online_only(hwid: str = "", app=None) -> bool:
    """
    Online-only activation dialog (production default).
    
    SECURITY (C-05): No offline tab. No legacy XOR/HMAC activation.
    Only server-authoritative licensing with Ed25519 signed receipts.
    
    Args:
        hwid: Device fingerprint prefix for display (informational only)
        app: QApplication instance for processEvents
    """
    dlg = QDialog()
    dlg.setWindowTitle("AGREGAT — Активація")
    dlg.setFixedSize(500, 380)
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
    """)

    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)

    title = QLabel("AGREGAT — License Activation")
    title.setFont(_QFont("Segoe UI", 18, _QFont.Weight.Bold))
    title.setStyleSheet("color: #58a6ff;")
    layout.addWidget(title)

    layout.addWidget(QLabel("Email:"))
    email_input = QLineEdit()
    email_input.setPlaceholderText("your@email.com")
    layout.addWidget(email_input)

    layout.addWidget(QLabel("Активаційний ключ:"))
    key_input = QTextEdit()
    key_input.setPlaceholderText("Вставте ключ активації...")
    key_input.setMaximumHeight(60)
    layout.addWidget(key_input)

    status_label = QLabel("")
    status_label.setStyleSheet("color: #f85149; font-weight: bold;")
    layout.addWidget(status_label)

    activate_btn = QPushButton("Активувати")

    def on_activate():
        email = email_input.text().strip()
        key = key_input.toPlainText().strip()
        if not email or not key:
            status_label.setText("Заповніть всі поля!")
            return

        status_label.setText("Активація...")
        status_label.setStyleSheet("color: #d29922;")
        activate_btn.setEnabled(False)
        if app:
            app.processEvents()

        success, msg = activate_license_online(email, key)

        if success:
            status_label.setText("✓ Активація успішна!")
            status_label.setStyleSheet("color: #3fb950;")
            dlg.accept()
        else:
            status_label.setText(f"✗ {msg}")
            status_label.setStyleSheet("color: #f85149;")
            activate_btn.setEnabled(True)

    activate_btn.clicked.connect(on_activate)
    layout.addWidget(activate_btn)

    # Device info footer (informational only — not used for offline activation)
    fp = generate_device_fingerprint()
    os_info = get_os_info()
    info_label = QLabel(f"Device: {fp[:16]}... | {os_info}")
    info_label.setStyleSheet("color: #484f58; font-size: 11px; font-family: 'Consolas';")
    layout.addWidget(info_label)

    return dlg.exec() == QDialog.DialogCode.Accepted


# C-05: Alias for backward compatibility — points to online-only version
# The old show_activation_dialog() with offline tab has been removed.
show_activation_dialog = show_activation_dialog_online_only