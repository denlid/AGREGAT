# Prompt: Наша дилема з налаштуваннями

## Проблема

У нас є архітектура налаштувань в PySide6 додатку, розділена на 3 файли:

1. **`gui/controllers/settings_registry.py`** — єдиний реєстр ключів з дефолтами
2. **`gui/controllers/settings_controller.py`** — контролер (save/load, restore step position)
3. **`gui/tabs/settings_tab.py`** — UI (QWidgets), ~1200 рядків

Ми зробили рефакторинг: створили реєстр, винесли `DEFAULTS` в одне місце, виправили `SettingsController.save()` щоб він збирав всі ключі, а не підмножину.

## Дилема

Щоб додати **одне нове налаштування**, треба зробити 6 змін:

1. `settings_registry.py` — додати ключ + дефолт
2. `settings_tab.py` — створити QWidget
3. `settings_tab.py` `_wire_persistence_signals()` — підключити `self._persist()`
4. `settings_tab.py` `_capture_settings()` — додати в snapshot
5. `settings_tab.py` `apply_persisted()` — додати застосування
6. `settings_tab.py` `_reset_defaults()` — додати скидання

Кроки 2-6 це ручна робота з UI. Ми не хочемо автоматично генерувати UI з реєстру, бо:
- Різні типи віджетів (QSpinBox, QComboBox, QCheckBox)
- Різні callback-функції (одні міняють engine, інші ні)
- QFormLayout будується вручну з групуванням по підтабах

## Запитання

1. Чи нормально що 5 з 6 кроків — ручні, або варто щось автоматизувати?
2. Як мінімізувати ризик що розробник забуде один з кроків?
3. Чи є сенс зробити базовий клас для налаштувань, який би вимагав реалізувати всі методи?
4. Або може варто зробити декоратор/реєстрацію віджета, яка б автоматично підключала persist, capture, apply, reset?

## Поточний код для контексту

```python
# settings_registry.py
SETTINGS_REGISTRY = {
    "theme": {"default": "dark"},
    "font_size": {"default": 11},
    # ... 37 ключів
}
DEFAULTS = {k: v["default"] for k, v in SETTINGS_REGISTRY.items()}
ALL_KEYS = set(SETTINGS_REGISTRY.keys())

# settings_controller.py
def save(self, key=None, value=None):
    snapshot = dict(DEFAULTS)
    snapshot.update(self.settings)
    snapshot.update({
        "asset": h.asset_cb.currentText(),
        "initial_equity": h.settings_tab.equity_spin.value(),
        # ... актуальні значення з UI
    })
    settings_persist.save(snapshot)

# settings_tab.py
def _capture_settings(self):
    snapshot = dict(DEFAULTS)
    snapshot.update({
        "theme": self.theme_cb.currentText(),
        "font_size": self.font_size_spin.value(),
        # ... ручний список
    })
    return snapshot

def _persist(self):
    if self.pw and hasattr(self.pw, "settings_controller"):
        self.pw.settings_controller.save()
    else:
        settings_persist.save(self._capture_settings())

def apply_persisted(self, settings):
    # ручне застосування кожного ключа до віджета
    self.theme_cb.setCurrentText(settings.get("theme", "dark"))
    self.font_size_spin.setValue(int(settings.get("font_size", 11)))
    # ...

def _reset_defaults(self):
    # ручне скидання кожного віджета
    self.theme_cb.setCurrentText("dark")
    self.font_size_spin.setValue(11)
    # ...
```

---

Питання: **що порадите?** 6 кроків це ок, чи варто автоматизувати? Як саме?