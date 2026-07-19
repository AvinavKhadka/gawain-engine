# Gawain — SQL RAG Data Intelligence

A production-grade Retrieval-Augmented Generation (RAG) system that translates natural-language questions into T-SQL, executes them against SQL Server, and streams back KPI cards, interactive tables, charts, and a written analysis — all powered by a locally-run Ollama LLM (no cloud API required).

```
User question
    │
    ▼
TF-IDF schema retrieval  ←─ SQL Server schema (INFORMATION_SCHEMA)
    │
    ▼
Ollama LLM  ──generates──▶  T-SQL query
    │
    ▼
SET NOEXEC ON validation  ──fail──▶  LLM auto-fix retry
    │ pass
    ▼
SQL Server execution
    │
    ▼
pandas DataFrame
    │
    ├──▶ KPI cards
    ├──▶ AG Grid table  (+ CSV export)
    ├──▶ Chart.js chart (line / bar / stacked bar / doughnut / scatter)
    └──▶ Ollama LLM streaming analysis  (with conversation history)
```

---

## Features

| Feature | Detail |
|---------|--------|
| Natural language → SQL | Ollama LLM (any locally-installed model) |
| Pre-execution validation | `SET NOEXEC ON` syntax check before hitting data |
| Auto SQL repair | LLM re-tries on runtime error |
| Multi-turn memory | Last 6 conversation turns sent as context |
| Dynamic schema retrieval | TF-IDF ranks only relevant tables per question |
| Any-database support | Works with any SQL Server DB via `DB_TABLE_FILTER` env var |
| Multi-step planning | LLM decomposes complex questions into sub-queries |
| Chart auto-detection | Line, bar, stacked bar, doughnut, scatter |
| SQL editor | Edit generated SQL in-browser and re-run |
| CSV export | One-click export from any result table |
| Query history | SQLite log of every question + star favorites |
| Dashboard | Pin charts/tables to a persistent dashboard (localStorage) |
| Streaming UI | NDJSON token-by-token streaming, React + AG Grid + Chart.js |


## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| SQL Server | 2019+ | Express edition works |
| ODBC Driver 17 | for SQL Server | [Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| Ollama | latest | [ollama.com](https://ollama.com) |


## 1. SQL Server Setup

### Option A — AdventureWorksDW2019 (default demo database)

1. Download the `.bak` file from Microsoft:
   ```
   https://github.com/Microsoft/sql-server-samples/releases/tag/adventureworks
   ```
   Get: `AdventureWorksDW2019.bak`

2. Restore in SQL Server Management Studio (SSMS):
   ```sql
   RESTORE DATABASE AdventureWorksDW2019
   FROM DISK = 'C:\path\to\AdventureWorksDW2019.bak'
   WITH MOVE 'AdventureWorksDW2019' TO 'C:\Data\AdventureWorksDW2019.mdf',
        MOVE 'AdventureWorksDW2019_log' TO 'C:\Data\AdventureWorksDW2019_log.ldf',
        REPLACE;
   ```

3. Verify:
   ```sql
   USE AdventureWorksDW2019;
   SELECT COUNT(*) FROM dbo.FactInternetSales;  -- should return 60398
   ```

### Option B — Your own database

Point `DB_DATABASE` in `.env` at any SQL Server database.
Optionally set `DB_TABLE_FILTER` to a comma-separated list of tables to include in the schema context (leave empty to include all).


## 2. Ollama Setup

```bash
# Install Ollama (Windows — download installer from ollama.com)
# Then pull a model:
ollama pull llama3.1:latest      # recommended ~5GB
# or a smaller/faster option:
ollama pull llama3.2:3b          # ~2GB, faster on CPU
# or a SQL-focused option:
ollama pull codellama:13b        # good at code/SQL

# Verify it's running
ollama list
ollama serve                     # start the server (auto-starts on Windows after install)
```

> To use a custom fine-tuned model see the [Training Guide](train/README.md).


## 3. Python Backend Setup

```bash
# Clone / navigate to project root
cd "gawain-engine"

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

### Configure `.env`

Copy the template and fill in your values:

```bash
copy .env.example .env          # Windows
# cp .env.example .env          # Linux/Mac
```

Edit `.env`:

```ini
# SQL Server — use your actual server\instance name
DB_SERVER=YOUR_PC\MSSQLSERVER2019
DB_DATABASE=AdventureWorksDW2019
DB_DRIVER=ODBC Driver 17 for SQL Server

# Windows auth (recommended) — leave DB_USER and DB_PASSWORD blank
DB_USER=
DB_PASSWORD=

# SQL auth — fill both if not using Windows auth
# DB_USER=sa
# DB_PASSWORD=your_password

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:latest

# Optional: comma-separated table whitelist (empty = all tables)
DB_TABLE_FILTER=
```


## 4. Frontend Setup

```bash
cd frontend
npm install
npm run build          # compiles React → ../static/ (served by FastAPI)
cd ..
```

For live-reload development (optional):
```bash
# Terminal 1 — backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend dev server (http://localhost:5173)
cd frontend && npm run dev
```


## 5. Running the Application

### Quick start (Windows)

```bat
start.bat
```

### Manual start

```bash
# Activate venv first
.venv\Scripts\activate

# Start server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000** in your browser.


## 6. Verifying Everything Works

```bash
# Check backend health
curl http://localhost:8000/api/health
# Expected: {"ollama": true, "database": true}

# Run integration test
python test_pipeline.py

# Or just open the app and type:
# "Show me total revenue by year"
```


## 7. Project Structure

```
gawain-engine/
│
├── main.py                  # FastAPI entry point
├── requirements.txt
├── start.bat                # Windows one-click launcher
├── .env                     # Your local config (gitignored)
├── .env.example             # Template — commit this
│
├── config/                  # All configuration in one place
│   ├── settings.py          # DB, Ollama, LLM params, paths, keyword sets
│   └── prompts.py           # LLM system prompt (edit to change AI behaviour)
│
├── server/                  # Python backend logic
│   ├── routes.py            # All HTTP endpoints + streaming chat handler
│   ├── llm.py               # Ollama calls: SQL generation, planning, analysis
│   ├── database.py          # SQL execution, schema discovery, chart detection
│   ├── history.py           # SQLite query history
│   └── schema_retrieval.py  # TF-IDF schema relevance ranking
│
├── frontend/                # React + TypeScript source
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── ChatInput.tsx
│       │   ├── Dashboard.tsx      # Pinned charts/tables
│       │   ├── DataGrid.tsx       # AG Grid wrapper + CSV export
│       │   ├── Header.tsx
│       │   ├── HistoryPanel.tsx   # Query history sidebar
│       │   ├── MessageBubble.tsx  # Renders all block types
│       │   └── TrendChart.tsx     # Chart.js (line/bar/doughnut/scatter/stacked)
│       ├── hooks/
│       │   ├── useChat.ts         # Streaming NDJSON parser + session
│       │   ├── useHealth.ts
│       │   └── useHistory.ts
│       └── types.ts
│
├── static/                  # Production build output (gitignored, from npm run build)
├── storage/                 # SQLite history DB lives here (gitignored)
│
└── train/                   # Model training tools
    ├── README.md            # Full training guide
    ├── Modelfile            # Ollama custom model (prompt + params)
    ├── prepare_data.py      # Generates training pairs from your live DB
    ├── example_pairs.jsonl  # Seed Q&A pairs
    ├── finetune.bat         # Windows llama.cpp fine-tuning script
    └── finetune.sh          # Linux/Mac llama.cpp fine-tuning script
```


## 8. API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/api/health` | Ollama + DB connectivity status |
| `GET`  | `/api/schema` | Full schema context string |
| `POST` | `/api/schema/refresh` | Clear schema cache and reload |
| `POST` | `/api/chat` | Main streaming chat (NDJSON) |
| `POST` | `/api/chat/run-sql` | Execute user-edited SQL (NDJSON) |
| `GET`  | `/api/history` | List query history (SQLite) |
| `POST` | `/api/history/favorite` | Toggle favorite `{id: number}` |
| `DELETE` | `/api/history/{id}` | Delete history entry |

### Chat stream events

```
session  → string       session UUID (first event)
step     → string       progress message
sql      → string       generated T-SQL
kpi      → [{label, value}]   headline metric cards
grid     → {columns, rows, total, _title?}   AG Grid data
chart    → {type, title, labels, datasets}   Chart.js config
token    → string       streamed analysis text chunk
error    → string       error message
done     → ""           end of stream
```


## 9. Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_SERVER` | `IMPOSSIBLEISNOT\MSSQLSERVER2019` | SQL Server instance |
| `DB_DATABASE` | `AdventureWorksDW2019` | Database name |
| `DB_DRIVER` | `ODBC Driver 17 for SQL Server` | ODBC driver name |
| `DB_USER` | _(empty)_ | SQL auth user (blank = Windows auth) |
| `DB_PASSWORD` | _(empty)_ | SQL auth password |
| `DB_TABLE_FILTER` | _(empty)_ | Comma-separated table whitelist (empty = all) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `llama3.1:latest` | Model to use for all LLM calls |


## 10. Troubleshooting

### `DB Error` badge in the UI

### `Ollama Offline` badge

### SQL generation is wrong or slow

### Frontend not loading


## 11. Custom Model Training

See **[train/README.md](train/README.md)** for:
