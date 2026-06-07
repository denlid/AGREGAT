# AGREGAT Server — Backend API & Admin Panel

**⚠️ SECURITY NOTICE:** This directory contains server-side code for AGREGAT's licensing and authentication system. Do NOT modify security-critical files without owner approval.

---

## Quick Start (Local Development)

### Prerequisites
- Docker & Docker Compose
- Python 3.12+ (for running outside Docker)

### 1. Set up environment
```bash
cd server
cp .env.example .env
# Edit .env and fill in the values
```

### 2. Start services
```bash
docker-compose up -d
```

This starts:
- **API Server** on `http://localhost:8000`
- **PostgreSQL** on `localhost:5432`
- **Redis** on `localhost:6379`

### 3. Create owner account
```bash
docker-compose exec api python scripts/create_owner.py
```

### 4. Access
- **API Docs:** http://localhost:8000/docs (Swagger UI)
- **Admin Panel:** http://localhost:8000/admin

---

## Supabase Setup (Production)

### 1. Create Supabase Project
1. Go to https://supabase.com
2. Create new project
3. Note your project URL and service key

### 2. Run Database Schema
1. Go to SQL Editor in Supabase Dashboard
2. Copy contents of `migrations/init.sql`
3. Execute

### 3. Update .env
```bash
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_KEY=YOUR_SERVICE_KEY
```

### 4. Create Owner
```bash
python scripts/create_owner.py
```

### 5. Deploy
For Supabase Edge Functions or any VPS with Docker:
```bash
docker build -t agregat-server .
docker run -p 8000:8000 --env-file .env agregat-server
```

---

## Project Structure

```
server/
├── .env                    ← YOUR secrets (never commit!)
├── .env.example            ← Template
├── docker-compose.yml      ← Local dev
├── Dockerfile              ← Production build
├── requirements.txt        ← Dependencies
├── app/
│   ├── main.py             ← FastAPI entry point
│   ├── config.py           ← Settings
│   ├── database.py         ← DB connection
│   ├── models/             ← SQLAlchemy models
│   ├── schemas/            ← Pydantic schemas
│   ├── api/                ← API routes
│   │   ├── v1/             ← Public endpoints
│   │   └── admin/          ← Admin endpoints
│   ├── services/           ← Business logic
│   ├── security/           ← Auth utilities
│   └── templates/          ← Admin HTML
├── migrations/
│   └── init.sql            ← Database schema
├── scripts/
│   ├── create_owner.py     ← Create admin account
│   └── generate_key.py     ← Generate license keys
├── tests/                  ← Server tests
├── API_DOCS.md             ← API reference
└── README.md               ← This file
```

---

## For Desktop Developers

### What you need to know:
1. Read `API_DOCS.md` for all endpoint details
2. The desktop app communicates with this server via HTTPS
3. Never hardcode server secrets in desktop code
4. Use `CapabilityManager.has("capability")` for feature checks

### What you should NOT do:
- ❌ Modify `app/security/` or `app/services/` without approval
- ❌ Share `.env` contents
- ❌ Bypass authentication in production
- ❌ Create `.dev_mode` files

---

## Migration to VPS

When outgrowing Supabase Free:

```bash
# 1. Dump database
pg_dump $SUPABASE_URL > backup.sql

# 2. Set up Hetzner VPS
ssh root@vps
docker-compose up -d

# 3. Restore
psql $HETZNER_URL < backup.sql

# 4. Update .env DATABASE_URL
# 5. Restart
docker-compose restart
```

Zero code changes needed.

---

## Owner Contact
- **Email:** alephxau@gmail.com
- **For security issues:** Contact owner directly