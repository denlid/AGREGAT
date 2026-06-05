"""Binding layer між ключами налаштувань і QWidget.

Дозволяє автоматизувати capture / apply / reset / signal-wiring
для всіх зареєстрованих віджетів.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)


class SettingsBinding:
    """Реєструє QWidget за ключем налаштування.

    Використання::

        self._bind = SettingsBinding()
        self._bind.bind("theme", self.theme_cb)
        self._bind.bind("font_size", self.font_size_spin)

        # автоматично:
        snapshot = self._bind.capture_all()
        self._bind.apply_all(settings, DEFAULTS)
        self._bind.reset_all(DEFAULTS)
        self._bind.wire_all(self._persist)
    """

    def __init__(self) -> None:
        self._bindings: dict[str, QWidget] = {}

    # ------------------------------------------------------------------
    # Реєстрація
    # ------------------------------------------------------------------

    def bind(self, key: str, widget: QWidget) -> None:
        """Зареєструвати віджет за ключем налаштування."""
        self._bindings[key] = widget

    # ------------------------------------------------------------------
    # Read / Write значень (адаптери)
    # ------------------------------------------------------------------

    @staticmethod
    def get_widget_value(widget: QWidget) -> Any:
        """Прочитати поточне значення з віджета."""
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        raise TypeError(f"Unsupported widget type: {type(widget).__name__}")

    @staticmethod
    def set_widget_value(widget: QWidget, value: Any) -> None:
        """Записати значення у віджет."""
        if isinstance(widget, QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(str(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value if value is not None else ""))
        else:
            raise TypeError(f"Unsupported widget type: {type(widget).__name__}")

    @staticmethod
    def connect_change_signal(widget: QWidget, callback: Callable) -> None:
        """Підключити callback до сигналу зміни віджета."""
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.valueChanged.connect(callback)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(callback)
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(callback)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(callback)

    # ------------------------------------------------------------------
    # Групові операції
    # ------------------------------------------------------------------

    def capture_all(self) -> dict[str, Any]:
        """Створити словник {ключ: значення} з усіх зареєстрованих віджетів."""
        return {key: self.get_widget_value(w) for key, w in self._bindings.items()}

    def apply_all(self, settings: dict, defaults: dict) -> None:
        """Застосувати словник налаштувань до всіх віджетів.

        Пропускає ключі, яких немає в settings (залишаються поточними).
        """
        for key, widget in self._bindings.items():
            if key in settings:
                self.set_widget_value(widget, settings[key])
            elif key in defaults:
                self.set_widget_value(widget, defaults[key])

    def reset_all(self, defaults: dict) -> None:
        """Скинути всі зареєстровані віджети до дефолтних значень."""
        for key, widget in self._bindings.items():
            if key in defaults:
                self.set_widget_value(widget, defaults[key])

    def wire_all(self, callback: Callable) -> None:
        """Підключити callback (зазвичай ``self._persist``) до всіх віджетів."""
        for widget in self._bindings.values():
            self.connect_change_signal(widget, callback)

    def widgets_to_block(self) -> list[QWidget]:
        """Повернути список усіх зареєстрованих віджетів (для blockSignals)."""
        return list(self._bindings.values())

    def validate(self, all_keys: set[str], /, *, exclude: set[str] | None = None) -> None:
        """Перевірити що всі ключі (крім виключених) зареєстровані.

        Кидає RuntimeError, якщо якісь ключі відсутні.
        """
        if exclude is None:
            exclude = set()
        expected = all_keys - exclude
        missing = expected - set(self._bindings.keys())
        if missing:
            raise RuntimeError(
                f"Settings not bound in SettingsTab: {missing}\n"
                f"Add self._bind.bind('{next(iter(missing))}', widget) "
                f"after creating the widget."
            )