# Prompt: Додати нове налаштування

Скопіюй це повідомлення як інструкцію для AI (ChatGPT/Claude/GPT):

---

## Контекст

У нас є архітектура налаштувань, розділена на 3 файли:

| Файл | Призначення |
|------|-------------|
| `gui/controllers/settings_registry.py` | Єдиний реєстр: ключ → дефолт |
| `gui/controllers/settings_controller.py` | Контролер: save/load, restore |
| `gui/tabs/settings_tab.py` | UI: QWidgets + apply_persisted |
| `gui/persistence/settings_persist.py` | Читання/запис JSON (atomic) |

## Як це працює

`DEFAULTS` генерується з реєстру:
```python
DEFAULTS = {k: v["default"] for k, v in SETTINGS_REGISTRY.items()}
```

`SettingsController.save()` починає з `dict(DEFAULTS)`, мержить self.settings, потім актуальні значення з UI — **жоден ключ не губиться**.

## Завдання: додати налаштування "{назва}"

Потрібно зробити 6 змін:

### 1. `gui/controllers/settings_registry.py`
Додати в `SETTINGS_REGISTRY`:
```python
"{key}": {"default": {default_value}},
```

### 2. `gui/tabs/settings_tab.py` — створити QWidget
Створити віджет (QSpinBox / QDoubleSpinBox / QComboBox / QCheckBox / QLineEdit), використовуючи `DEFAULTS["{key}"]` для початкового значення.
Додати callback `self._on_{name}()` якщо потрібна логіка при зміні.

### 3. `gui/tabs/settings_tab.py` — `_wire_persistence_signals()`
Підключити сигнал зміни віджета до `self._persist()`.

### 4. `gui/tabs/settings_tab.py` — `_capture_settings()`
Додати в `snapshot.update({...})`:
```python
"{key}": self.{widget_name}.{value_getter}(),
```

### 5. `gui/tabs/settings_tab.py` — `apply_persisted()`
Додати:
```python
self.{widget_name}.{value_setter}({type_cast}(settings.get("{key}", DEFAULTS["{key}"])))
```

### 6. `gui/tabs/settings_tab.py` — `_reset_defaults()`
```python
self.{widget_name}.{value_setter}(DEFAULTS["{key}"])
```

## Приклад

Додати налаштування "Max Open Trades" (default=5, range 1-100):

```python
# settings_registry.py
"max_open_trades": {"default": 5},

# settings_tab.py — створити
self.max_open_trades_spin = QSpinBox()
self.max_open_trades_spin.setRange(1, 100)
self.max_open_trades_spin.setValue(DEFAULTS["max_open_trades"])
self.max_open_trades_spin.valueChanged.connect(self._persist)
some_layout.addRow("Max Open Trades:", self.max_open_trades_spin)

# settings_tab.py — _capture_settings()
"max_open_trades": self.max_open_trades_spin.value(),

# settings_tab.py — apply_persisted()
self.max_open_trades_spin.setValue(int(settings.get("max_open_trades", DEFAULTS["max_open_trades"])))

# settings_tab.py — _reset_defaults()
self.max_open_trades_spin.setValue(DEFAULTS["max_open_trades"])
```

## Перевірка

Після змін:
1. Запустити додаток, змінити налаштування → перевірити `saves/settings.json`
2. Закрити / відкрити додаток → налаштування має відновитися
3. Кнопка "Reset to Defaults" → має скинутися до дефолту