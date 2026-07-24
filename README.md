# Gawain Engine

![Gawain Engine](https://img.shields.io/badge/GAWAIN-ENGINE-05070d?style=for-the-badge&labelColor=ff003c)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local_LLM-000000?style=flat-square&logo=ollama&logoColor=white)](https://ollama.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker&logoColor=white)](./DOCKER.md)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](./README.md)

![Hero](frontend/src/assets/hero.png)

> **Natural language → T-SQL → KPI cards, tables & charts — all locally, no cloud.**
> Visual theme: **Arasaka** — red `#ff003c` / black `#05070d` / authentic triskele emblem — アラサカ

---

## Overview

**Gawain Engine** is a production-grade **Retrieval-Augmented Generation (RAG)** system for **SQL Server**. 

You ask in English: *"Why did Bikes revenue drop 12% in 2013 vs 2012?"*

It:
1. 🔍 Retrieves relevant schema via **TF-IDF** on `INFORMATION_SCHEMA`
2. 🧠 Generates validated **T-SQL** via **Ollama LLM** (locally)
3. 🛡️ Validates with `SET NOEXEC ON`, auto-fixes on error
4. ⚡ Executes against SQL Server → **pandas DataFrame**
5. 📊 Returns **KPI cards**, **AG Grid table** (+ CSV), **Chart.js charts**, and a **streaming analysis** with conversation memory

Built for real businesses — works with **any SQL Server DB**, not just the demo.

**Frontend theme** is inspired by Arasaka Corporation from Cyberpunk 2077 — dark `NIGHT_OPS` `#05070d` + `DAY_PROTOCOL` light mode, cut-corner tactical UI, JetBrains Mono + Orbitron, authentic triskele emblem `⬢`. The theme is purely visual; the project itself is **Gawain Engine**.

---

## ✨ Features

| 🧩 Module | 📝 What it does |
|-----------|-----------------|
| **🗣️ NL → SQL** | Any local Ollama model — `llama3.1`, `codellama`, custom finetunes |
| **🛡️ Pre-Execution Validation** | `SET NOEXEC ON` check before hitting data |
| **🔧 Auto SQL Repair** | LLM retries with error context |
| **🧠 Multi-Turn Memory** | Last 6 turns as context |
| **🔍 Dynamic Schema Retrieval** | TF-IDF — only relevant tables per question, scales to 1000+ tables |
| **🏢 Any-Database** | `DB_TABLE_FILTER` whitelist — point at any SQL Server DB |
| **🗺️ Multi-Step Planning** | Decomposes `vs / and / correlation` into sub-queries |
| **📈 Chart Auto-Detect** | Line, Bar, Stacked Bar, Doughnut, Scatter |
| **✏️ SQL Editor** | Edit generated SQL in-browser and re-run |
| **⬇️ CSV Export** | One-click from any grid |
| **📜 Query History** | SQLite log + favorites — アーカイブ |
| **📌 Dashboard** | Pin charts/tables → persistent (localStorage) |
| **🌊 Streaming UI** | NDJSON token-by-token — React + AG Grid + Chart.js |
| **🎯 Driver Analysis** | DuckDB extract for attribution & changepoint detection — `DRIVERS` panel |
| **🎨 Theme** | Arasaka-inspired dark/light toggle `◑ / ◐` — red chrome, grid + scanlines |

---

## 🏗 Architecture

```mermaid
flowchart TD
    A[🗣️ User Question] --> B[🔍 TF-IDF Schema Retrieval]
    B -.-> C[(🏛️ SQL Server INFORMATION_SCHEMA)]
    B --> D[🧠 Ollama LLM]
    D -- generates --> E[📜 T-SQL]
    E --> F{🛡️ SET NOEXEC ON}
    F -- fail --> G[🔧 Auto-Fix]
    G --> E
    F -- pass --> H[⚡ SQL Execution]
    H --> I[🐼 DataFrame]
    I --> J[📊 KPI]
    I --> K[🗃️ Grid + CSV]
    I --> L[📈 Chart]
    J & K & L --> M[🧠 Streaming Analysis]
    M --> N[💬 NDJSON → React]
```

**Request flow:**
- `POST /api/chat` → streams `session → step → sql → kpi → grid → chart → token* → done`
- Client: `useChat.ts` parses NDJSON line-by-line for instant UI updates
- History: SQLite `storage/history.db` + in-memory session store (last 6 turns)

---

## 🛠 Tech Stack

| Layer | Tech | Details |
|-------|------|---------|
| **Backend** | Python 3.11 + FastAPI 0.115 + Uvicorn | `main.py` mounts `static/` + `/assets` |
| **DB Access** | pyodbc 5.2 + ODBC Driver 17 | `SET NOEXEC ON` validation, pandas execution |
| **LLM** | Ollama (local) — llama3.1 / codellama / finetuned | No cloud calls |
| **Analysis** | DuckDB 1.1 + scikit-learn + pandas | Driver attribution, key influencers, changepoint |
| **Frontend** | React 19 + TypeScript 6 + Vite 8 | `frontend/` → `static/` |
| **UI Libs** | AG Grid 35 + Chart.js 4 + JetBrains Mono + Orbitron | Tables + charts |
| **Storage** | SQLite (history) + DuckDB (analytics extract) | `storage/` dir, gitignored |
| **Theme** | Arasaka-inspired — CSS custom props, clip-path tactical, grid + scanlines | `App.css` 900+ lines — アラサカデザイン |
| **DevOps** | Docker multi-stage (Node 20 → Python 3.11 slim + msodbcsql17) | `Dockerfile` + `docker-compose.yml` |

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Version | Check |
|-------------|---------|-------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| SQL Server | 2019+ | Express works |
| ODBC Driver 17 | | [Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| Ollama | latest | [ollama.com](https://ollama.com) |
| Docker (optional) | 24+ | `docker --version` |

### 1️⃣ SQL Server — Demo DB

```powershell
# Download AdventureWorksDW2019.bak from:
# https://github.com/Microsoft/sql-server-samples/releases/tag/adventureworks

# Restore in SSMS - New Query:
RESTORE DATABASE AdventureWorksDW2019
FROM DISK = 'C:\SQLBackups\AdventureWorksDW2019.bak'
WITH MOVE 'AdventureWorksDW2019' TO 'C:\Data\AdventureWorksDW2019.mdf',
     MOVE 'AdventureWorksDW2019_log' TO 'C:\Data\AdventureWorksDW2019_log.ldf',
     REPLACE;

# Verify:
# USE AdventureWorksDW2019; SELECT COUNT(*) FROM dbo.FactInternetSales; -- 60398
```

Or point `DB_DATABASE` at your own DB — set `DB_TABLE_FILTER` to whitelist tables.

### 2️⃣ Ollama — Local LLM

```bash
ollama pull llama3.1:latest   # ~5GB recommended
ollama list
ollama serve                  # auto-starts on Windows
```

### 3️⃣ Backend

```bash
cd gawain-engine
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux
pip install -r requirements.txt

copy .env.example .env        # Windows
# cp .env.example .env

# Edit .env:
# DB_SERVER=.\SQLEXPRESS
# DB_DATABASE=AdventureWorksDW2019
# DB_USER=  (blank = Windows Auth)
# DB_PASSWORD=
# OLLAMA_BASE_URL=http://localhost:11434
```

### 4️⃣ Frontend

```bash
cd frontend
npm install
npm run build   # → ../static/ — 295KB CSS
cd ..
```

Dev mode:
```bash
# Terminal 1 — backend :8000
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend :5173 HMR
cd frontend
npm run dev
```

### 5️⃣ Run

```bat
start.bat   # Windows one-click
# or
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# → http://localhost:8000
```

Verify:
```bash
curl http://localhost:8000/api/health
# → {"ollama": true, "database": true}
```

---

## 🐳 Docker — Production Deployment

Full-stack: **FastAPI + React (Arasaka theme) + Ollama** — no DB needed at build time, client plugs their SQL Server at runtime.

### Quick Start

```bash
cp .env.docker.example .env
# Edit .env: DB_SERVER=host.docker.internal\SQLEXPRESS, DB_USER=sa, DB_PASSWORD=...

docker compose up --build -d
# → http://localhost:8000  UI
# → http://localhost:11434 Ollama

docker compose logs -f app
```

**Stack:** `app` (Python 3.11 slim + ODBC 17 + Node build, :8000) + `ollama` (ollama/ollama:latest, :11434) + optional `mssql` (uncomment in compose for local SQL testing).

Healthchecks are lenient — `app` shows `Up` even if DB offline, UI loads with `DB: OFFLINE` badge — perfect for demo/product.

### 🏢 For Clients — Connect YOUR SQL Database

**Decision:**
- Already have SQL Server (`SERVER\INSTANCE`)? → **Option A**
- No SQL / new laptop / demo? → **Option B** (SQL inside Docker)

#### Option A — Existing SQL Server

1. **On SQL machine:**
   - SSMS → Server Properties → Security → **SQL Server and Windows Auth** → Restart service
   - Create login:
     ```sql
     CREATE LOGIN gawain WITH PASSWORD = 'Gawain!2026';
     CREATE USER gawain FOR LOGIN gawain;
     ALTER ROLE db_datareader ADD MEMBER gawain;
     ```
   - SQL Server Configuration Manager → Protocols → Enable **TCP/IP** → Restart
   - Firewall allow `1433`

2. **Find server name (no sqlcmd needed):**
   ```powershell
   Get-Service | Where-Object { $_.Name -like "MSSQL*" }
   Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL"
   $env:COMPUTERNAME
   ```

3. **Client `.env`:**
   ```ini
   # Same PC as Docker:
   DB_SERVER=host.docker.internal\SQLEXPRESS
   # Or LAN IP:
   # DB_SERVER=192.168.1.50\SQLEXPRESS

   DB_DATABASE=YourBusinessDB
   DB_USER=gawain
   DB_PASSWORD=Gawain!2026
   OLLAMA_BASE_URL=http://ollama:11434
   ```

4. **Test from container:**
   ```powershell
   docker exec -it arasaka-gawain /opt/mssql-tools18/bin/sqlcmd -S host.docker.internal\SQLEXPRESS -U gawain -P "Gawain!2026" -Q "SELECT DB_NAME()" -C
   ```

#### Option B — No SQL Server? Docker mssql

```yaml
# Uncomment in docker-compose.yml:
mssql:
  image: mcr.microsoft.com/mssql/server:2022-latest
  environment:
    ACCEPT_EULA: "Y"
    SA_PASSWORD: "YourStrong!Passw0rd123"
  ports: ["1433:1433"]
```

```powershell
docker compose up -d mssql ollama
# Put .bak in ./backups/
docker compose exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "YourStrong!Passw0rd123" -Q "RESTORE DATABASE AdventureWorksDW2019 FROM DISK = '/backups/AdventureWorksDW2019.bak' WITH REPLACE" -C
# .env: DB_SERVER=mssql, DB_DATABASE=AdventureWorksDW2019
docker compose up -d app
```

#### Build Once, Run Anywhere (B2B)

```bash
docker build -t arasaka-gawain .
docker save arasaka-gawain -o arasaka.tar
# Client: docker load -i arasaka.tar + their .env → docker compose up -d
```

**Licensing:** Developer Edition = free forever for dev/test, Express = free up to 10GB prod. You build without DB, client brings their own — data never leaves premises — アラサカ — 安全なデータ分析 🔒

Full guide: **[DOCKER.md](DOCKER.md)** 🐳

---

## ⚙ Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_SERVER` | `IMPOSSIBLEISNOT\MSSQLSERVER2019` | SQL Server instance — `HOST\INSTANCE` or `HOST,PORT` |
| `DB_DATABASE` | `AdventureWorksDW2019` | Database name |
| `DB_DRIVER` | `ODBC Driver 17 for SQL Server` | ODBC driver |
| `DB_USER` | _(empty)_ | SQL auth user — blank = Windows Auth |
| `DB_PASSWORD` | _(empty)_ | SQL password |
| `DB_TABLE_FILTER` | _(empty)_ | Whitelist: `FactInternetSales,DimDate` — empty = all |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL |
| `OLLAMA_MODEL` | `llama3.1:latest` | Model name |
| `STAR_FACT` | `dbo.FactInternetSales` | Fact table for driver analysis |
| `STAR_MEASURES` | `SalesAmount,OrderQuantity,TotalProductCost` | Measures to attribute |

Config lives in `config/settings.py` — chart colors, LLM params, keyword sets — アラサカ設定

---

## 📁 Project Structure

```
gawain-engine/
├── Dockerfile               # 🐳 Multi-stage: Node + Python + ODBC
├── docker-compose.yml       # 🐳 app + ollama + optional mssql
├── docker-entrypoint.sh     # 🐳 Wait for Ollama + pull model
├── .dockerignore
├── .env.docker.example
├── DOCKER.md                # 🐳 Full Docker guide
│
├── main.py                  # 🚪 FastAPI entry — serves static + API
├── requirements.txt
├── start.bat                # ⚡ Windows launcher
├── .env / .env.example
│
├── config/
│   ├── settings.py          # DB, Ollama, chart colors
│   └── prompts.py           # 🧠 System prompt
│
├── server/
│   ├── routes.py            # 🛣️ Endpoints + NDJSON streaming
│   ├── llm.py               # 🤖 SQL generation & analysis
│   ├── database.py          # 🗄️ Execution, schema, chart detection
│   ├── history.py           # 📜 SQLite history
│   ├── drivers.py           # 🎯 DuckDB driver analysis
│   └── schema_retrieval.py  # 🔍 TF-IDF ranking
│
├── frontend/
│   ├── index.html
│   ├── public/
│   │   ├── favicon.svg              # ⬢ Authentic triskele — アラサカ
│   │   ├── arasaka-emblem.svg       # Arasaka emblem vector
│   │   └── arasaka-wordmark.svg     # arasaka wordmark
│   └── src/
│       ├── App.tsx
│       ├── App.css          # 🔴 Arasaka visual theme — 900+ lines, grid + scanlines
│       ├── components/
│       │   ├── Header.tsx           # 🏢 Authentic triskele — fixed viewBox, tight left
│       │   ├── ChatInput.tsx
│       │   ├── Dashboard.tsx
│       │   ├── DataGrid.tsx
│       │   ├── HistoryPanel.tsx
│       │   ├── MessageBubble.tsx
│       │   └── TrendChart.tsx       # 📈 Arasaka neon palette
│       └── hooks/
│
├── static/                  # 📦 Build output (gitignored)
├── storage/                 # 💾 history.db + analytics.duckdb (gitignored)
│
└── train/
    ├── README.md            # 📚 Training guide — アラサカ学習
    ├── Modelfile
    ├── prepare_data.py
    └── finetune scripts
```

---

## 🔌 API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | 🟢 Ollama + DB status |
| `GET` | `/api/schema` | 📖 Full schema context |
| `POST` | `/api/schema/refresh` | 🔄 Reload schema cache |
| `POST` | `/api/chat` | 💬 Main chat (NDJSON stream) |
| `POST` | `/api/chat/run-sql` | ✏️ Execute edited SQL |
| `GET` | `/api/history` | 📜 List history |
| `POST` | `/api/history/favorite` | ⭐ Toggle favorite |
| `DELETE` | `/api/history/{id}` | 🗑️ Delete entry |
| `GET` | `/api/drivers/status` | 🎯 Driver extract meta |
| `POST` | `/api/drivers/rebuild` | 🔧 Rebuild DuckDB extract |
| `POST` | `/api/train/save` | 🧬 Save Q&A as training pair |

**NDJSON Stream Events:**
```
session → string       🆔 Session UUID
step    → string       🔄 Progress
sql     → string       📜 T-SQL
kpi     → [{label,value}]  📊 KPIs
grid    → {columns,rows,total}  🗃️ Table
chart   → {type,title,labels,datasets}  📈 Chart
token   → string       ✍️ Analysis token
error   → string       💥 Error
done    → ""           ✅ End
```

---

## 🧪 Troubleshooting

### 🔴 `DB Error` / `DB: OFFLINE`
- Check `DB_SERVER` — e.g. `.\SQLEXPRESS` not `YOUR_PC\...`
- SSMS can connect?
- `Get-Service | Where-Object { $_.Name -like "MSSQL*" }` + `Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL`
- Browser: `curl http://localhost:8000/api/health` → `database: false` = DB issue

### 🔴 `Ollama Offline`
- `ollama serve` in separate terminal
- `ollama list` has model?
- `curl http://localhost:11434/api/tags`
- Docker: `docker exec -it arasaka-ollama ollama list`

### 🐌 Wrong / slow SQL
- Slow: `ollama pull llama3.2:3b` or enable GPU
- Wrong: narrows context `DB_TABLE_FILTER`, or edit SQL in UI `EDIT // RE-EXECUTE` → `◈ TRAIN_CORE` to save as training pair
- See `train/README.md`

### 🎨 Old Barclays blue instead of Arasaka red
```bash
cd frontend
npm run build
# Browser Ctrl+Shift+R + localStorage.clear()
```
Dev bypass: `npm run dev` → `:5173`

### 🟥 Logo cut off / incomplete
- Emblem is clean `viewBox 0 0 100 100` rebuild — no potrace crop
- CSS fix: `.arasaka-mark-auth { flex:0 0 52px; overflow:visible; }` + `.arasaka-emblem-svg { transform:scale(1.75); }`
- Max fit in 62px header: `42px + scale(1.0)` — larger needs `scale` + `overflow:visible`

### 🐳 Docker `exec /app/docker-entrypoint.sh: no such file`
- Heredoc `COPY --chmod=755 <<'SH'` not supported on older Docker → use separate file `docker-entrypoint.sh` + `COPY` + `RUN chmod +x` — fixed in repo

### 🐳 Docker `unhealthy` dependency failed
- Healthchecks now lenient: `start_period 60s`, `retries 10`, checks `/` not `/api/health`, `depends_on: - ollama` (no healthy condition)
- `docker compose down && docker compose up --build -d`

---

## 🗺 Roadmap

- [ ] 🔐 Auth — API keys / JWT for multi-tenant
- [ ] 📊 More charts — treemap, funnel, geo map
- [ ] 🤖 Agent mode — multi-turn tool use (run SQL, then fetch docs)
- [ ] 🧠 RAG on docs — join SQL results with Confluence/Notion
- [ ] 🌐 Multi-DB — Postgres, MySQL, BigQuery adapters
- [ ] 📱 Mobile responsive audit
- [ ] 🧪 E2E tests — Playwright + pytest
- [ ] 📦 Helm chart — K8s deployment

---

## 📄 License

MIT — free for commercial use. Arasaka visual theme (red `#ff003c`, triskele emblem traced from Cyberpunk 2077) is fan-art inspired, not affiliated with CD PROJEKT RED. 

**Stack:** FastAPI + Ollama + SQL Server + React + AG Grid + Chart.js + DuckDB  
**Fonts:** JetBrains Mono + Orbitron + Rajdhani — with アラサカ  
**Principle:** Secure, local-first, no cloud dependency 🔒💾 — アラサカ — 安全なデータ分析
