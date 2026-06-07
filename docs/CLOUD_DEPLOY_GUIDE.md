# 🌍 Хмарний деплой AGREGAT — Повний гайд для новачків

## 📖 Що ми робимо (простими словами)

У вас є **десктопний додаток** (AGREGAT — trading платформа). Він працює на комп'ютері користувача, але для **ліцензування** йому потрібен **сервер** в інтернеті, який перевіряє ключі.

### Як це працює:

```
Комп'ютер користувача                    Інтернет
┌─────────────────────┐
│  AGREGAT.exe        │
│  (десктоп додаток)  │───── HTTPS запит ─────▶  Сервер (Render.com)
│                     │                          │
│  Вводить ключ:      │◀─── "Ключ валідний" ────│  Перевіряє ключ в БД
│  "XXXX-XXXX-XXXX"   │                          │
└─────────────────────┘                          ▼
                                          База даних (Supabase)
                                          │
                                          Зберігає:
                                          - Ліцензії
                                          - Користувачів
                                          - Пристрої
```

### Що потрібно (все безкоштовно):

| Сервіс | Для чого | Ціна |
|--------|---------|------|
| **Supabase** | База даних (PostgreSQL) — зберігає ліцензії | $0 (до 500 MB) |
| **Render.com** | Хостинг сервера — запускає FastAPI | $0 (750 год/міс) |
| **GitHub** | Зберігання коду + авто-деплой | $0 |

---

## ✅ Крок 1: Supabase — ви вже створили проект!

Ваш проект: `https://ofdxnkaqanooqfqfhipy.supabase.co`

### Тепер потрібно отримати Connection String (рядок підключення до БД):

1. Відкрийте ваш проект в Supabase Dashboard
2. Зліва натисніть **⚙️ Project Settings** (іконка шестерні)
3. Натисніть **Database** в меню зліва
4. Прокрутіть до секції **Connection string**
5. Виберіть вкладку **URI** (не Connection Pooling)
6. Побачите щось таке:

```
postgresql://postgres:[YOUR-PASSWORD]@db.ofdxnkaqanooqfqfhipy.supabase.co:5432/postgres
```

7. **Скопіюйте цей рядок** — він потрібен для наступного кроку

> ⚠️ Якщо бачите `[YOUR-PASSWORD]` — замініть на реальний пароль, який ви вказали при створенні проекту.
> Якщо забули пароль: Settings → Database → Reset database password

---

## 🔧 Крок 2: Підготувати сервер для хмари

Сервер — це Python-додаток (FastAPI), який буде працювати на Render.com і перевіряти ліцензії.

### 2.1: Створити конфігураційний файл

Створіть файл `server/.env.production` (це шаблон для Render):

```env
# Режим production
DEBUG=false

# Рядок підключення до Supabase (вставте свій!)
DATABASE_URL=postgresql+asyncpg://postgres:ВАШ_ПАРОЛЬ@db.ofdxnkaqanooqfqfhipy.supabase.co:5432/postgres

# Секрети (згенеруйте нові для production)
JWT_SECRET=згенеруйте_новий_ключ
ENCRYPTION_KEY=згенеруйте_новий_ключ
OWNER_PASSWORD_SALT=згенеруйте_новий_сіль

# Admin
OWNER_EMAIL=ваш@email.com

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=10000

# SSL для Supabase
DB_SSL_MODE=verify-full

# Peppers (безпека)
ACTIVATION_KEY_PEPPER=згенеруйте_новий
FINGERPRINT_PEPPER=згенеруйте_новий

# CORS — дозволити всім (для бети)
ALLOWED_ORIGINS=*
ALLOWED_HOSTS=*
```

### 2.2: Згенерувати секрети

Відкрийте PowerShell і виконайте:

```powershell
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32)); print('ENCRYPTION_KEY=' + secrets.token_hex(32)); print('OWNER_PASSWORD_SALT=' + secrets.token_hex(32)); print('ACTIVATION_KEY_PEPPER=' + secrets.token_hex(32)); print('FINGERPRINT_PEPPER=' + secrets.token_hex(32))"
```

Скопіюйте вивід і вставте в `.env.production`.

---

## 🚀 Крок 3: Render.com — деплой сервера

### 3.1: Реєстрація

1. Відкрийте https://render.com
2. Натисніть **Get Started for Free**
3. Виберіть **Sign up with GitHub** (найпростіший спосіб)
4. Дозвольте доступ до вашого репозиторію AGREGAT

### 3.2: Створити Web Service

1. В Render Dashboard натисніть **New +** → **Web Service**
2. Виберіть ваш репозиторій `AGREGAT`
3. Налаштуйте:

| Поле | Значення |
|------|---------|
| **Name** | `agregat-server` |
| **Region** | `Frankfurt (EU)` — ближче до України |
| **Root Directory** | `server` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | `Free` |

### 3.3: Додати Environment Variables

Натисніть **Environment** → **Add Environment Variable** і додайте по одному:

```
DEBUG = false
DATABASE_URL = postgresql+asyncpg://postgres:ВАШ_ПАРОЛЬ@db.ofdxnkaqanooqfqfhipy.supabase.co:5432/postgres
JWT_SECRET = (ваш згенерований ключ)
ENCRYPTION_KEY = (ваш згенерований ключ)
OWNER_PASSWORD_SALT = (ваш згенерований сіль)
OWNER_EMAIL = ваш@email.com
SERVER_HOST = 0.0.0.0
SERVER_PORT = 10000
DB_SSL_MODE = verify-full
ACTIVATION_KEY_PEPPER = (ваш pepper)
FINGERPRINT_PEPPER = (ваш pepper)
ALLOWED_ORIGINS = *
ALLOWED_HOSTS = *
LOG_LEVEL = INFO
```

> 💡 **PORT** — Render автоматично встановить цю змінну. Не додавайте її вручну!

### 3.4: Deploy

1. Натисніть **Create Web Service**
2. Зачекайте 3-5 хвилин (перший деплой найдовший)
3. Коли побачите **Live** — сервер готовий!

### 3.5: Отримати URL сервера

Render дасть вам URL типу:
```
https://agregat-server-xxxx.onrender.com
```

Перевірте: відкрийте в браузері `https://agregat-server-xxxx.onrender.com/health`
Має бути: `{"status":"ok"}`

**Цей URL** — те, що потрібно вказати в десктопному додатку.

---

## 🔐 Крок 4: Створити адміністратора

Тепер потрібно створити owner-акаунт для управління ліцензіями.

### Варіант A: Через Render Shell (найпростіший)

1. В Render Dashboard → ваш сервіс → вкладка **Shell**
2. Зачекайте поки завантажиться термінал
3. Виконайте:

```bash
python scripts/create_owner.py
```

4. Введіть пароль (12+ символів)
5. Збережіть TOTP секрет і backup коди!

### Варіант B: Локально підключившись до Supabase

```powershell
cd server
.\venv\Scripts\Activate.ps1

# Встановіть DATABASE_URL на Supabase
$env:DATABASE_URL = "postgresql+asyncpg://postgres:ВАШ_ПАРОЛЬ@db.ofdxnkaqanooqfqfhipy.supabase.co:5432/postgres"

python scripts/create_owner.py
```

---

## 🎫 Крок 5: Створити першу ліцензію

1. Відкрийте `https://ВАШ_RENDER_URL/admin`
2. Login з email + пароль + TOTP код
3. Dashboard → **Create License**
4. Заповніть:
   - Email: ваш email
   - Hardware ID: `BETA-TESTER-001`
   - Plan: `pro`
   - Max devices: `3`
   - Expires: `2027-01-01`
5. Натисніть **Create**
6. Скопіюйте **Activation Key**

---

## 💻 Крок 6: Налаштувати десктоп додаток

### 6.1: Вказати URL сервера

Відкрийте `engine/security/license_client.py` і знайдіть рядок з URL сервера.
Замініть на ваш Render URL:

```python
# Було (localhost):
_LICENSE_SERVER_URL = "http://localhost:8000"

# Стало (ваш Render URL):
_LICENSE_SERVER_URL = "https://agregat-server-xxxx.onrender.com"
```

### 6.2: Зібрати .exe для бета-тестерів

```powershell
cd C:\Users\alephxau\Desktop\AGREGAT

# Встановити PyInstaller якщо немає
pip install pyinstaller

# Зібрати .exe
pyinstaller app.py --onefile --windowed --name AGREGAT --icon=assets/icon.ico

# Файл буде в dist/AGREGAT.exe
```

### 6.3: Роздати бета-тестерам

1. Завантажте `dist/AGREGAT.exe` на **GitHub Releases**:
   - GitHub → ваш репозиторій → Releases → Draft new release
   - Tag: `v0.1.0-beta`
   - Attach: `AGREGAT.exe`
   - Publish

2. Надішліть бета-тестерам:
   - Посилання на GitHub Release (для завантаження .exe)
   - Activation Key (через email або Telegram)

---

## 📊 Як це працює для бета-тестера

```
1. Бета-тестер завантажує AGREGAT.exe з GitHub
2. Запускає → бачить вікно "Enter License Key"
3. Вводить ключ, який ви йому дали
4. Додаток відправляє запит на Render сервер
5. Сервер перевіряє ключ в Supabase БД
6. Якщо валідний → додаток відкривається
7. Кожні 2 хвилини → heartbeat (перевірка що ключ ще активний)
```

---

## 🔄 Auto-Deploy (CI/CD)

Коли ви пушите код в GitHub, Render автоматично redeploy сервер:

```
git push → GitHub → Render бачить зміни → авто-deploy → сервер оновлено
```

Це працює автоматично після першого налаштування.

---

## ⚠️ Важливі моменти

### Render Free Plan обмеження:
- **750 годин/міс** — вистачає на 1 інстанс 24/7
- **Sleep після 15 хв неактивності** — перший запит після сну займе 30-60 секунд
- **512 MB RAM** — достатньо для FastAPI
- **Для бети це ОК** — для production потрібен платний план ($7/міс)

### Supabase Free Plan обмеження:
- **500 MB БД** — вистачить на тисячі ліцензій
- **2 проекти** — один для dev, один для production
- **Без ліміту запитів** — для ліцензування це більш ніж достатньо

### Безпека:
- Всі з'єднання через HTTPS (Render дає безкоштовний SSL)
- БД через SSL (Supabase дає безкоштовний SSL)
- Секрети в Environment Variables (ніколи в коді!)
- `.env` файл в `.gitignore` (ніколи не комітиться)

---

## 🆘 Troubleshooting

### "Server not responding" на Render
- Перевірте логи: Render Dashboard → Logs
- Перший запит після sleep може зайняти до 60 секунд
- Перевірте що DATABASE_URL правильний

### "Database connection failed"
- Перевірте що `DB_SSL_MODE=verify-full` встановлено
- Перевірте що пароль Supabase правильний
- Supabase → Settings → Database → Connection string → скопіюйте знову

### "ModuleNotFoundError" в Render
- Перевірте що `server/requirements.txt` містить всі залежності
- Build Command має бути: `pip install -r requirements.txt`

### Додаток не може підключитись до сервера
- Перевірте URL в `license_client.py`
- Переконайтесь що сервер Live в Render Dashboard
- Перевірте `https://` (не `http://`)

---

## 📋 Чекліст

- [ ] Supabase: скопіювати Connection String
- [ ] Створити `.env.production` з секретами
- [ ] Render.com: зареєструватись через GitHub
- [ ] Render: створити Web Service з `server/` як root
- [ ] Render: додати всі Environment Variables
- [ ] Render: дочекатись Deploy → Live
- [ ] Перевірити `/health` endpoint
- [ ] Створити owner через Render Shell
- [ ] Створити першу ліцензію через адмінку
- [ ] Оновити URL в `license_client.py`
- [ ] Зібрати `AGREGAT.exe` через PyInstaller
- [ ] Завантажити на GitHub Releases
- [ ] Роздати бета-тестерам

---

## 💰 Що буде коштувати грошей (колись)

| Коли | Що | Ціна |
|------|-----|------|
| Зараз (бета) | Все безкоштовно | $0 |
| 100+ користувачів | Render Starter (не засинає) | $7/міс |
| 1000+ користувачів | Supabase Pro | $25/міс |
| Production | Hetzner VPS (альтернатива Render) | €3.50/міс |

**Для бета-тестерів та інвесторів — $0/міс цілком достатньо!**