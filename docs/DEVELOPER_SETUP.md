# 🚀 AGREGAT — Швидкий старт для розробників

## Що потрібно

1. **Python 3.10+** — вже є
2. **PostgreSQL** — оберіть один з варіантів нижче
3. **Git** — вже є

---

## Крок 1: PostgreSQL (оберіть ОДИН варіант)

### Варіант A: Локальний PostgreSQL (рекомендовано)

```powershell
# Завантажити PostgreSQL 16:
# https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
# Встановити, запам'ятати пароль для postgres

# Створити базу та користувача:
psql -U postgres
CREATE USER agregat WITH PASSWORD 'agregat_dev';
CREATE DATABASE agregat OWNER agregat;
\q
```

### Варіант B: Docker (якщо є Docker Desktop)

```powershell
cd server
docker compose up -d db
# PostgreSQL буде на localhost:5432
```

### Варіант C: Supabase (безкоштовний хмарний, нічого встановлювати)

1. Зареєструватись на https://supabase.com
2. Створити проект
3. Скопіювати **Connection String** (Pool mode, Transaction)
4. Вставити в `server/.env` як `DATABASE_URL`

---

## Крок 2: Налаштувати сервер

```powershell
cd server

# Створити віртуальне середовище
python -m venv venv
.\venv\Scripts\Activate.ps1

# Встановити залежності
pip install -r requirements.txt

# .env файл вже створений з dev-налаштуваннями
# Перевірте що DATABASE_URL правильний
```

---

## Крок 3: Запустити сервер (адмінку)

```powershell
cd server
.\venv\Scripts\Activate.ps1

# Запуск в dev-режимі (auto-reload)
python -m app.main

# Або через uvicorn:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Перевірка:**
- Адмінка: http://localhost:8000/admin
- API Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

---

## Крок 4: Створити адміністратора (owner)

```powershell
# Скрипт створює owner-акаунт з MFA
cd server
.\venv\Scripts\Activate.ps1
python scripts/create_owner.py

# Скрипт запитає пароль (мін 12 символів)
# і згенерує TOTP секрет + backup коди
# ЗБЕРІЖІТЬ backup коди — вони більше не покажуться!
```

---

## Крок 5: Створити ліцензію для розробників

Через адмінку:
1. Відкрити http://localhost:8000/admin/dashboard
2. Login → Dashboard → Create License
3. Вказати email, hardware ID, термін дії
4. Скопіювати activation key

Через API:
```powershell
curl -X POST http://localhost:8000/api/admin/licenses `
  -H "X-Admin-Token: YOUR_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{
    "email": "dev@localhost",
    "hardware_id": "DEV-TEST",
    "plan": "pro",
    "max_devices": 3,
    "expires_at": "2027-01-01T00:00:00Z"
  }'
```

---

## Крок 6: Деплой коду розробникам

### Що КОМІТИТИ в git:

```powershell
# Додати нові файли
git add server/.env.example
git add docs/DEVELOPER_SETUP.md
git add engine/security/
git add gui/security/
git add server/app/security/
git add server/app/models/encrypted_fields.py

# Коміт
git commit -m "security: complete security audit - 18 modules

- Engine: license_client, anti_debug, secure_memory, request_signing,
  encrypted_storage, anti_tamper, strategy_sandbox, secret_store,
  integrity, update_checker
- GUI: web_security, file_security, ai_privacy
- Server: replay_protection, middleware, honeypot, memory_monitor,
  encrypted_fields
- Dev setup: .env.example, DEVELOPER_SETUP.md"

# Пуш
git push origin main
```

### Що НЕ КОМІТИТИ (вже в .gitignore):

```
server/.env              ← секрети!
server/venv/             ← віртуальне середовище
*.pem                    ← приватні ключі
__pycache__/             ← кеш Python
```

---

## Крок 7: Розробник отримує код

```powershell
# Клонувати
git clone https://github.com/denlid/AGREGAT.git
cd AGREGAT

# Налаштувати сервер
cd server
copy .env.example .env
# Редагувати .env — вставити свій DATABASE_URL

# Встановити залежності
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Запустити
python -m app.main
```

---

## Крок 8: Налаштувати клієнт (десктоп додаток)

```powershell
# В корені проекту
cd AGREGAT

# Встановити залежності
pip install -r requirements.txt

# Для dev-режиму без ліцензії — встановити змінну:
$env:AGREGAT_DEV_MODE = "1"

# Запустити
python app.py
```

### Для розробників без ліцензії:

Додати в `engine/security/license_client.py` dev-bypass:

```python
# В _load_receipt() додати на початку:
if os.environ.get("AGREGAT_DEV_MODE") == "1":
    logger.info("DEV MODE: license check bypassed")
    return None
```

---

## Файли для розробників

| Файл | Опис |
|------|------|
| `server/.env.example` | Шаблон конфігурації сервера |
| `docs/DEVELOPER_SETUP.md` | Цей файл — швидкий старт |
| `docs/ADMIN_BUILD_GUIDE.md` | Детальний гайд для адмінів |
| `SECURITY_AUDIT_REPORT.md` | Звіт аудиту безпеки |

---

## Проблеми та рішення

### "ModuleNotFoundError: No module named 'asyncpg'"
```powershell
pip install asyncpg
```

### "Connection refused" на PostgreSQL
- Перевірте що PostgreSQL запущено: `Get-Service postgresql*`
- Перевірте порт: `netstat -an | findstr 5432`

### "password authentication failed"
- Перевірте `DATABASE_URL` в `.env`
- Переконайтесь що користувач `agregat` існує в PostgreSQL

### "DEBUG=false blocks startup"
- Для dev завжди `DEBUG=true` в `.env`
- Production вимагає повну конфігурацію

---

## Швидкі команди

```powershell
# Перевірити сервер
curl http://localhost:8000/health

# Перевірити чи PostgreSQL працює
psql -U agregat -d agregat -c "SELECT 1"

# Переглянути логи сервера
# Логи виводяться в консоль (LOG_LEVEL=INFO)

# Скинути базу (DEV тільки!)
psql -U postgres -c "DROP DATABASE agregat; CREATE DATABASE agregat OWNER agregat;"