"""Binding layer між ключами налаштувань і QWidget.

Дозволяє автоматизувати capture / apply / reset / signal-wiring
для всіх зареєстрованих віджетів.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)


@dataclass
class SettingBinding:
    widget: QWidget
    apply: Callable[[Any], None] | None = None


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
        self._bindings: dict[str, SettingBinding] = {}

    # ------------------------------------------------------------------
    # Реєстрація
    # ------------------------------------------------------------------

    def bind(
        self,
        key: str,
        widget: QWidget,
        *,
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        """Зареєструвати віджет за ключем налаштування."""
        self._bindings[key] = SettingBinding(widget=widget, apply=apply)

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
            widget.setValue(int(value if value is not None else widget.minimum()))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value if value is not None else widget.minimum()))
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
        return {
            key: self.get_widget_value(binding.widget)
            for key, binding in self._bindings.items()
        }

    def apply_all(
        self,
        settings: dict,
        defaults: dict,
        *,
        apply_runtime: bool = False,
        exclude_runtime: set[str] | None = None,
    ) -> None:
        """Застосувати словник налаштувань до всіх віджетів.

        Пропускає ключі, яких немає в settings (залишаються поточними).
        """
        for key, binding in self._bindings.items():
            if key in settings:
                self.set_widget_value(binding.widget, settings[key])
            elif key in defaults:
                self.set_widget_value(binding.widget, defaults[key])
        if apply_runtime:
            self.apply_runtime_all(exclude=exclude_runtime)

    def reset_all(
        self,
        defaults: dict,
        *,
        apply_runtime: bool = False,
        exclude_runtime: set[str] | None = None,
    ) -> None:
        """Скинути всі зареєстровані віджети до дефолтних значень."""
        for key, binding in self._bindings.items():
            if key in defaults:
                self.set_widget_value(binding.widget, defaults[key])
        if apply_runtime:
            self.apply_runtime_all(exclude=exclude_runtime)

    def apply_runtime(self, key: str) -> None:
        """Apply one bound setting to runtime state, if it has an applier."""
        binding = self._bindings[key]
        if binding.apply is not None:
            binding.apply(self.get_widget_value(binding.widget))

    def apply_runtime_all(self, *, exclude: set[str] | None = None) -> None:
        """Apply every bound setting with a runtime applier."""
        exclude = exclude or set()
        for key in self._bindings:
            if key in exclude:
                continue
            self.apply_runtime(key)

    def wire_all(self, callback: Callable, *, apply_runtime: bool = True) -> None:
        """Підключити callback (зазвичай ``self._persist``) до всіх віджетів."""
        for key, binding in self._bindings.items():
            def on_change(*_args: Any, key: str = key) -> None:
                if apply_runtime:
                    self.apply_runtime(key)
                callback()

            self.connect_change_signal(binding.widget, on_change)

    def widgets_to_block(self) -> list[QWidget]:
        """Повернути список усіх зареєстрованих віджетів (для blockSignals)."""
        return [binding.widget for binding in self._bindings.values()]

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
