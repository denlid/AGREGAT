# 🚀 AGREGAT — Повний посібник з деплою та керування

## Зміст
1. [Огляд архітектури](#1-огляд-архітектури)
2. [Швидкий старт (локально)](#2-швидкий-старт-локально)
3. [Деплой сервера на Supabase](#3-деплой-сервера-на-supabase)
4. [Деплой сервера на VPS (альтернатива)](#4-деплой-сервера-на-vps-альтернатива)
5. [Збірка десктопного клієнта](#5-збірка-десктопного-клієнта)
6. [Керування ліцензіями](#6-керування-ліцензіями)
7. [Моніторинг та аудит](#7-моніторинг-та-аудит)
8. [Оновлення та підтримка](#8-оновлення-та-підтримка)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Огляд архітектури

```
┌─────────────────────────────────────────────────────────────┐
│                    AGREGAT Platform                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────┐         ┌─────────────────────────┐    │
│  │  Desktop Client  │ ◄─────► │   License Server        │    │
│  │  (PySide6 +      │  HTTPS  │   (FastAPI + PostgreSQL)│    │
│  │   Nuitka EXE)    │         │                         │    │
│  │                  │         │  ┌───────────────────┐  │    │
│  │  • License check │         │  │   Admin Panel     │  │    │
│  │  • Anti-debug    │         │  │   (Web UI + MFA)  │  │    │
│  │  • Heartbeat     │         │  └───────────────────┘  │    │
│  │  • Strategy IP   │         │                         │    │
│  └─────────────────┘         │  • License management   │    │
│                              │  • Device tracking      │    │
│                              │  • Session control      │    │
│                              │  • Audit log            │    │
│                              └─────────────────────────┘    │
│                                        ▲                     │
│                                        │                     │
│                              ┌─────────┴─────────┐          │
│                              │   Supabase / VPS   │          │
│                              │   PostgreSQL DB    │          │
│                              └────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### Компоненти:

| Компонент | Опис | Технологія |
|---|---|---|
| Desktop Client | Торговий термінал | Python + PySide6 → Nuitka EXE |
| License Server | Сервер ліцензій | FastAPI + PostgreSQL |
| Admin Panel | Веб-інтерфейс адміна | Jinja2 Templates + HTMX |
| Database | Зберігання даних | PostgreSQL (Supabase або self-hosted) |

---

## 2. Швидкий старт (локально)

### 2.1. Вимоги

- Python 3.10+
- Docker + Docker Compose
- Git

### 2.2. Клонувати репозиторій

```bash
git clone https://github.com/denlid/AGREGAT.git
cd AGREGAT
```

### 2.3. Запуск сервера (Docker)

```bash
cd server

# Скопіювати .env.example → .env
cp .env.example .env

# Згенерувати секрети
python -c "import secrets; print(f'JWT_SECRET={secrets.token_hex(32)}')" >> .env
python -c "from cryptography.fernet import Fernet; print(f'ENCRYPTION_KEY={Fernet.generate_key().decode()}')" >> .env

# Запуск
docker compose up -d

# Створити owner-акаунт
docker compose exec server python scripts/create_owner.py
```

### 2.4. Перевірка

```bash
# Health check
curl http://localhost:8000/health

# Admin panel
open http://localhost:8000/admin
```

### 2.5. Запуск клієнта (dev mode)

```bash
cd ..

# Створити .dev_mode для пропуску ліцензії
touch .dev_mode

# Встановити залежності
pip install -r requirements.txt

# Запуск
python app.py
```

---

## 3. Деплой сервера на Supabase

### 3.1. Створення проекту Supabase

1. Зареєструйтесь на [supabase.com](https://supabase.com)
2. Створіть новий проект (Free tier — 500MB DB, достатньо для старту)
3. Збережіть:
   - **Project URL**: `https://YOUR_PROJECT.supabase.co`
   - **Database Password**: з налаштувань проекту
   - **Service Role Key**: Settings → API → service_role key

### 3.2. Налаштування бази даних

Supabase вже має PostgreSQL. Потрібно лише створити таблиці:

#### Варіант A: Через SQL Editor (рекомендовано)

1. Відкрийте Supabase Dashboard → SQL Editor
2. Скопіюйте вміст `server/migrations/init.sql`
3. Виконайте SQL

#### Варіант B: Через командний рядок

```bash
# Підключення до Supabase PostgreSQL
psql "postgresql://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres"

# Виконати міграцію
\i server/migrations/init.sql
```

### 3.3. Деплой FastAPI сервера

Supabase **не** хостить Python-додатки. Потрібен окремий хостинг:

#### Безкоштовні варіанти:

| Сервіс | Ліміт | Посилання |
|---|---|---|
| **Railway** | $5/міс безкоштовно | railway.app |
| **Render** | 750 год/міс безкоштовно | render.com |
| **Fly.io** | 3 VM безкоштовно | fly.io |
| **Hugging Face Spaces** | CPU basic безкоштовно | huggingface.co/spaces |

#### Рекомендований: Railway

```bash
# 1. Встановити Railway CLI
npm install -g @railway/cli

# 2. Логін
railway login

# 3. Ініціалізація в папці server/
cd server
railway init

# 4. Додати змінні середовища
railway variables set DATABASE_URL="postgresql+asyncpg://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres"
railway variables set JWT_SECRET="YOUR_RANDOM_64_CHAR_HEX"
railway variables set ENCRYPTION_KEY="YOUR_FERNET_KEY"
railway variables set OWNER_EMAIL="your@email.com"
railway variables set DEBUG="false"
railway variables set ALLOWED_ORIGINS='["https://your-domain.com"]'

# 5. Деплой
railway up

# 6. Отримати URL
railway domain
```

#### Або через Docker (будь-який хостинг):

```bash
# Побудувати образ
cd server
docker build -t agregat-server .

# Запуск з підключенням до Supabase
docker run -d \
  --name agregat-server \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres" \
  -e JWT_SECRET="YOUR_RANDOM_64_CHAR_HEX" \
  -e ENCRYPTION_KEY="YOUR_FERNET_KEY" \
  -e OWNER_EMAIL="your@email.com" \
  -e DEBUG="false" \
  -e SUPABASE_URL="https://YOUR_PROJECT.supabase.co" \
  -e SUPABASE_SERVICE_KEY="YOUR_SERVICE_KEY" \
  agregat-server
```

### 3.4. Налаштування HTTPS

#### Через Cloudflare (безкоштовно, рекомендовано):

1. Зареєструйтесь на [cloudflare.com](https://cloudflare.com)
2. Додайте ваш домен
3. Змініть NS-записи на Cloudflare
4. Додайте DNS запис: `A record → your-server-ip`
5. Увімкніть SSL/TLS → Full (strict)
6. Увімкніть proxy (помаранчева хмара)

#### Через Railway/Render (автоматично):

Ці сервіси автоматично надають HTTPS з Let's Encrypt.

### 3.5. Створення owner-акаунта

```bash
# Якщо сервер на Railway/Render:
# Виконайте через їхній CLI або веб-консоль

# Локально через Docker:
docker compose exec server python scripts/create_owner.py

# Або через curl (якщо сервер вже працює):
# Це потрібно зробити ОДИН РАЗ, owner не можна створити через API
```

---

## 4. Деплой сервера на VPS (альтернатива)

### 4.1. Вибір VPS

| Провайдер | Ціна | RAM | SSD | Посилання |
|---|---|---|---|---|
| Hetzner | ~€4/міс | 2GB | 20GB | hetzner.com |
| DigitalOcean | ~$6/міс | 1GB | 25GB | digitalocean.com |
| Vultr | ~$2.50/міс | 512MB | 10GB | vultr.com |
| OVH | ~€3.50/міс | 2GB | 20GB | ovh.com |

### 4.2. Підготовка сервера (Ubuntu 22.04)

```bash
# SSH на сервер
ssh root@your-server-ip

# Оновлення
apt update && apt upgrade -y

# Встановити Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Встановити Docker Compose
apt install docker-compose-plugin -y

# Створити користувача (не працюйте як root!)
useradd -m -s /bin/bash agregat
usermod -aG docker agregat
su - agregat
```

### 4.3. Деплой

```bash
# Клонувати репозиторій
git clone https://github.com/denlid/AGREGAT.git
cd AGREGAT/server

# Створити .env
cp .env.example .env
nano .env  # Заповнити значення

# Генерація секретів
echo "JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> .env
echo "ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> .env

# Запуск
docker compose up -d

# Перевірка
docker compose ps
docker compose logs -f server

# Створити owner
docker compose exec server python scripts/create_owner.py
```

### 4.4. Nginx reverse proxy + HTTPS

```bash
# Встановити Nginx + Certbot
apt install nginx certbot python3-certbot-nginx -y

# Створити конфіг
cat > /etc/nginx/sites-available/agregat << 'EOF'
server {
    listen 80;
    server_name license.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -s /etc/nginx/sites-available/agregat /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Отримати SSL сертифікат
certbot --nginx -d license.yourdomain.com
```

### 4.5. Firewall

```bash
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable
```

---

## 5. Збірка десктопного клієнта

### 5.1. Windows

```bash
# Встановити залежності
pip install nuitka orderedset zstandard python-minifier

# Фінальна збірка
build_final_windows.cmd

# Результат: dist/AGREGAT.exe
```

#### Опціонально: UPX пакування

```bash
# Встановити UPX
# Завантажити з https://upx.github.io/ і додати в PATH

# Перекомпілювати (UPX включений автоматично в build_final_windows.cmd)
```

#### Опціонально: Code Signing

```bash
# Купити сертифікат (~$100/рік) на:
# - sectigo.com
# - digicert.com
# - globalsign.com

# Підписати:
signtool sign /f certificate.pfx /p PASSWORD /t http://timestamp.digicert.com dist\AGREGAT.exe
```

### 5.2. macOS

```bash
# Встановити залежності
pip3 install nuitka orderedset zstandard python-minifier

# Зробити скрипт виконуваним
chmod +x build_final_macos.sh

# Фінальна збірка
./build_final_macos.sh

# Результат: dist/AGREGAT.app (або dist/AGREGAT.dmg)
```

#### Опціонально: Code Signing + Notarization

```bash
# Потрібен Apple Developer account ($99/рік)

# Підписати:
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Name" \
  dist/AGREGAT.app

# Нотаризувати:
xcrun notarytool submit dist/AGREGAT.dmg \
  --apple-id YOUR_EMAIL \
  --password APP_PASSWORD \
  --team-id YOUR_TEAM_ID \
  --wait
```

### 5.3. Dev mode (для розробки)

```bash
# Створити файл .dev_mode в корені проекту
touch .dev_mode  # Linux/macOS
echo. > .dev_mode  # Windows

# Запуск без компіляції
python app.py
```

⚠️ **ВАЖЛИВО:** Видаліть `.dev_mode` перед релізом!

---

## 6. Керування ліцензіями

### 6.1. Доступ до Admin Panel

```
URL: https://your-server.com/admin
Логін: owner email (з .env)
Пароль: встановлений через create_owner.py
MFA: обов'язковий для всіх адмінів
```

### 6.2. Створення ліцензії

#### Через Admin Panel:
1. Логін → Dashboard → Licenses → Create
2. Заповнити:
   - Email користувача
   - Тип ліцензії (trial/standard/pro)
   - Термін дії
   - Макс. пристроїв
3. Натиснути "Generate Key"
4. Надіслати ключ користувачу

#### Через API (для автоматизації):

```bash
curl -X POST https://your-server.com/api/admin/licenses \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_email": "user@example.com",
    "license_type": "standard",
    "max_devices": 1,
    "valid_days": 365
  }'
```

### 6.3. Активація ліцензії (клієнт)

Користувач відкриває AGREGAT → Activation Dialog:

**Онлайн активація:**
1. Ввести email
2. Ввести activation key
3. Натиснути "Activate"
4. Сервер перевіряє ключ + створює сесію

**Офлайн активація:**
1. Ввести email
2. Ввести activation key
3. Скопіювати device fingerprint
4. Надіслати fingerprint адміну
5. Адмін генерує offline blob
6. Вставити blob в діалог

### 6.4. Відкликання ліцензії

#### Через Admin Panel:
1. Dashboard → Licenses → Знайти ліцензію
2. Натиснути "Revoke"
3. Обрати причину
4. Підтвердити

**Результат:**
- Сесія користувача анулюється
- Наступний heartbeat поверне "revoke"
- Клієнт зупиняється

#### Через API:

```bash
curl -X POST https://your-server.com/api/admin/licenses/revoke \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "license_id": "UUID",
    "reason": "payment_failed"
  }'
```

### 6.5. Моніторинг активних сесій

```
Admin Panel → Dashboard → Sessions
```

Показує:
- Хто підключений зараз
- Device fingerprint
- Останній heartbeat
- IP (хешований)

---

## 7. Моніторинг та аудит

### 7.1. Audit Log

Всі дії записуються в immutable audit log з hash chain:

```
Admin Panel → Dashboard → Audit Log
```

Події:
- `auth.login` — вхід адміна
- `auth.mfa_verify` — MFA верифікація
- `license.create` — створення ліцензії
- `license.revoke` — відкликання
- `license.activate` — активація клієнтом
- `session.heartbeat` — heartbeat
- `user.create` — створення користувача
- `server.error` — помилки сервера

### 7.2. Перевірка цілісності audit log

```bash
curl https://your-server.com/api/admin/audit/integrity \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

Повертає:
- `is_valid: true/false`
- `total_events`
- `broken_chain` (якщо хтось намагався змінити лог)

### 7.3. Логи сервера

```bash
# Docker
docker compose logs -f server

# VPS
journalctl -u agregat-server -f
```

### 7.4. Health Check

```bash
curl https://your-server.com/health
```

---

## 8. Оновлення та підтримка

### 8.1. Оновлення сервера

```bash
cd server

# Pull latest
git pull origin main

# Rebuild
docker compose down
docker compose build --no-cache
docker compose up -d

# Перевірка
docker compose logs -f server
```

### 8.2. Оновлення клієнта

1. Зібрати нову версію:
   ```bash
   build_final_windows.cmd  # або build_final_macos.sh
   ```

2. Збільшити версію в скрипті (`--product-version`)

3. Роздати користувачам новий EXE/DMG

### 8.3. Backup бази даних

#### Supabase (автоматично):
- Free tier: щоденні backup, 7 днів зберігання
- Pro tier: PITR (point-in-time recovery)

#### Self-hosted:

```bash
# Backup
docker compose exec postgres pg_dump -U agregat agregat > backup_$(date +%Y%m%d).sql

# Restore
cat backup_20260606.sql | docker compose exec -T postgres psql -U agregat agregat
```

#### Автоматичний backup (cron):

```bash
# Додати в crontab
0 3 * * * cd /home/agregat/AGREGAT/server && docker compose exec postgres pg_dump -U agregat agregat > /backups/agregat_$(date +\%Y\%m\%d).sql
```

### 8.4. Ротація секретів

```bash
# Згенерувати новий JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"

# Оновити .env
# Перезапустити сервер
docker compose restart server
```

⚠️ Після ротації JWT_SECRET всі активні сесії стануть недійсними!

---

## 9. Troubleshooting

### 9.1. Сервер не запускається

```bash
# Перевірити логи
docker compose logs server

# Поширені проблеми:
# 1. DATABASE_URL неправильний
# 2. JWT_SECRET не встановлений
# 3. Порт 8000 зайнятий
```

### 9.2. Клієнт не може активувати ліцензію

```
Проблема: "Connection refused"
Рішення: Перевірити що сервер працює і доступний

Проблема: "Invalid activation key"
Рішення: Перевірити що ключ правильний і не прострочений

Проблема: "Device fingerprint mismatch"
Рішення: Користувач змінив апаратне забезпечення. Потрібна нова активація.
```

### 9.3. Admin Panel не відкривається

```bash
# Перевірити що сервер працює
curl https://your-server.com/health

# Перевірити CORS
# ALLOWED_ORIGINS в .env має включати ваш домен
```

### 9.4. MFA не працює

```
Проблема: "Invalid TOTP code"
Рішення: 
1. Перевірити час на сервері (має бути синхронізований)
2. Використати backup codes
3. Скинути MFA через owner-акаунт
```

### 9.5. Nuitka compilation fails

```bash
# Поширені помилки:

# 1. "Module not found"
# Рішення: Додати --include-package або --include-module

# 2. "Missing data files"
# Рішення: Додати --include-data-dir

# 3. "Out of memory"
# Рішення: Збільшити swap або використати --jobs=1

# 4. PySide6 issues
# Рішення: pip install PySide6==6.6.0 (стабільна версія)
```

### 9.6. Windows Defender блокує EXE

```
Це нормальна поведінка для unsigned EXE.

Рішення:
1. Code signing certificate (найкраще)
2. Submit to Microsoft for analysis:
   https://www.microsoft.com/en-us/wdsi/filesubmission
3. Додати виключення в Windows Defender (для тестування)
```

---

## Додаток A: Повний .env приклад

```env
# Database (Supabase)
DATABASE_URL=postgresql+asyncpg://postgres.YOUR_PROJECT:YOUR_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres

# JWT
JWT_SECRET=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=5
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG=false
ALLOWED_ORIGINS=["https://your-domain.com", "app://agregat"]

# Supabase
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Encryption
ENCRYPTION_KEY=ABCDEF1234567890abcdef1234567890ABCDEF12345=

# Owner
OWNER_EMAIL=your@email.com
OWNER_PASSWORD_SALT=random_salt_here

# Rate Limits
RATE_LIMIT_REQUESTS_PER_MINUTE=60
RATE_LIMIT_AUTH_PER_MINUTE=5

# Heartbeat
HEARTBEAT_INTERVAL_SECONDS=120
HEARTBEAT_GRACE_PERIOD_SECONDS=420

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

## Додаток B: Структура проекту

```
AGREGAT/
├── app.py                      # Main entry point (desktop client)
├── build_final_windows.cmd     # Windows production build
├── build_final_macos.sh        # macOS production build
├── scripts/
│   └── minify_security.py      # Free code obfuscation
├── engine/
│   └── security/
│       ├── anti_debug.py       # Anti-debug + anti-tamper
│       ├── license_client.py   # Online license client
│       └── license_manager.py  # Offline license manager
├── gui/
│   └── dialogs/
│       └── activation_dialog.py # License activation UI
├── server/                     # License server (FastAPI)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── app/
│   │   ├── main.py             # FastAPI entry point
│   │   ├── config.py           # Settings
│   │   ├── database.py         # PostgreSQL connection
│   │   ├── models/             # SQLAlchemy models
│   │   ├── schemas/            # Pydantic schemas
│   │   ├── security/           # JWT, fingerprint, TOTP
│   │   ├── services/           # Business logic
│   │   ├── api/
│   │   │   ├── v1/             # Client API
│   │   │   └── admin/          # Admin API + panel
│   │   └── templates/          # Admin panel HTML
│   ├── migrations/
│   │   └── init.sql            # Database schema
│   └── scripts/
│       ├── create_owner.py     # Create owner account
│       └── generate_key.py     # Generate license key
├── DEPLOYMENT_GUIDE.md         # This file
└── PROTECTION_GUIDE.md         # Anti-reverse engineering guide
```

## Додаток C: Корисні команди

```bash
# === Сервер ===
docker compose up -d                    # Запуск
docker compose down                     # Зупинка
docker compose logs -f server           # Логи
docker compose exec server python ...   # Виконати в контейнері

# === Клієнт ===
python app.py                           # Dev mode
build_final_windows.cmd                 # Windows build
./build_final_macos.sh                  # macOS build

# === Безпека ===
python scripts/minify_security.py       # Обфускація
python server/scripts/create_owner.py   # Створити owner
python server/scripts/generate_key.py   # Генерувати ключ

# === Supabase ===
# SQL Editor → виконати init.sql
# Dashboard → Table Editor → перегляд даних
# Dashboard → API → тестування endpoint
```

---

*Останнє оновлення: 2026-06-06*
*AGREGAT Team*