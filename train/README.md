# 🧬 GAWAIN ENGINE — Model Training Guide
### 🏋️ Customizing LLM for Your Database — アラサカ学習
### アラサカ — モデル学習ガイド

Two proven approaches to make Gawain understand *your* SQL schema, not just AdventureWorks:

| 🛣️ Approach | 🎮 GPU Needed? | ⏱️ Time | 📈 Quality Gain | 🧠 Best For |
|-------------|---------------|---------|----------------|-------------|
| **📄 Modelfile** (Ollama) | ❌ No | ~5 min ⚡ | **Medium** | Quick improvement, no hardware |
| **🔥 LoRA Fine-tuning** (llama.cpp) | ✅ Recommended 8GB+ | 1–4h 🏋️ | **High** | Production, domain-specific DB |

---

## 🚀 Approach 1: Ollama Modelfile — Quick Start

A Modelfile bakes your **system prompt + few-shot examples** directly into the model. No GPU. No training. Works immediately.

### 🧪 Step 1 — Generate Examples from Live DB

```bash
# From project root with venv active
python train/prepare_data.py

# Creates:
# train/data/examples.jsonl
```

- Connects to SQL Server via `.env`
- Discovers fact/dimension tables
- Generates realistic Q&A pairs with correct JOINs

### ✏️ Step 2 — Review & Edit — Quality > Quantity

Open `train/data/examples.jsonl`. Each line:

```json
{"question": "Show total revenue by year", "sql": "SELECT dd.CalendarYear, SUM(fis.SalesAmount) AS Revenue FROM dbo.FactInternetSales fis JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey GROUP BY dd.CalendarYear ORDER BY dd.CalendarYear"}
```

**Guidelines:**
- 🎯 **50–200 pairs** covering real user questions
- 🧹 Remove incorrect pairs
- ➕ Add domain-specific: profit margin, territory vs region, customer segments
- ✅ Test every SQL in SSMS before keeping

**Good additional examples:**
```json
{"question": "Why did Bikes revenue drop 12% in 2013 vs 2012?", "sql": "SELECT dpc.EnglishProductCategoryName, dd.CalendarYear, SUM(fis.SalesAmount) AS Revenue FROM dbo.FactInternetSales fis JOIN dbo.DimDate dd ON fis.OrderDateKey=dd.DateKey JOIN dbo.DimProduct dp ON fis.ProductKey=dp.ProductKey JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey=dps.ProductSubcategoryKey JOIN dbo.DimProductCategory dpc ON dps.ProductCategoryKey=dpc.ProductCategoryKey WHERE dpc.EnglishProductCategoryName='Bikes' AND dd.CalendarYear IN (2012,2013) GROUP BY dpc.EnglishProductCategoryName, dd.CalendarYear ORDER BY dd.CalendarYear"}
{"question": "Top 5 products by gross margin in 2013", "sql": "SELECT TOP 5 dp.EnglishProductName, SUM(fis.SalesAmount - fis.TotalProductCost) AS GrossProfit FROM dbo.FactInternetSales fis JOIN dbo.DimProduct dp ON fis.ProductKey=dp.ProductKey JOIN dbo.DimDate dd ON fis.OrderDateKey=dd.DateKey WHERE dd.CalendarYear=2013 GROUP BY dp.EnglishProductName ORDER BY GrossProfit DESC"}
```

### 🏗️ Step 3 — Build Custom Model

```bash
ollama create gawain-sql -f train/Modelfile

ollama run gawain-sql "Show me revenue by product category"

# Use it in project:
# .env → OLLAMA_MODEL=gawain-sql
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Modelfile sets system prompt, embeds few-shot examples, locks `temperature 0.1` for deterministic SQL.

### 🔄 Step 4 — Iterate

1. Wrong SQL in UI? Add corrected pair
2. Need stricter JOIN rules? Edit `SYSTEM` in Modelfile — e.g., "Always JOIN via DimProductSubcategory"
3. Too creative? Lower `temperature` to `0.0`
4. Rebuild: `ollama create gawain-sql -f train/Modelfile` — ~10 sec ⚡

---

## 🔥 Approach 2: LoRA Fine-tuning — Deep Learning

Adjusts **model weights** to deeply learn your schema. Highest quality for custom databases.

### 📋 Prerequisites

| 🧩 | Requirement | 📝 Details |
|----|-------------|-----------|
| **🎮 GPU** | NVIDIA 8GB+ VRAM | RTX 3060+ recommended — CPU-only is 10–20× slower |
| **💾 Disk** | ~15GB free | Base model + artefacts + output |
| **🧠 RAM** | 16GB+ | 32GB recommended |
| **🐍 Python** | 3.11+ | With `huggingface_hub` |

### 🛠️ Step 1 — Install llama.cpp

**Windows PowerShell:**
```powershell
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
# CPU:
cmake -B build -DGGML_NATIVE=ON
cmake --build build --config Release -j8
# GPU:
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j8
```

**Linux/Mac:**
```bash
git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp
make -j8
# GPU: make GGML_CUDA=1 -j8
```

Save the `llama.cpp/` path — needed for finetune scripts.

### ⬇️ Step 2 — Download Base Model (GGUF)

```bash
pip install huggingface_hub

# 🟢 Recommended: Llama-3.1-8B
huggingface-cli download bartowski/Meta-Llama-3.1-8B-Instruct-GGUF Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf --local-dir train/models/

# ⚡ Smaller: Llama-3.2-3B — good for limited VRAM
huggingface-cli download bartowski/Llama-3.2-3B-Instruct-GGUF Llama-3.2-3B-Instruct-Q4_K_M.gguf --local-dir train/models/
```

### 🧪 Step 3 — Generate Training Data

```bash
python train/prepare_data.py

# Optional: pull real user questions from history DB:
python train/prepare_data.py --from-history
# Reads storage/history.db → converts to training format
```

**Aim for 100+ pairs for LoRA** — include hard examples: multi-JOIN, CASE WHEN, YoY comparisons.

### 🏋️ Step 4 — Run Fine-tuning

**Windows:**
```bat
# Edit finetune.bat → set LLAMA_DIR to your llama.cpp path
train\finetune.bat
```

**Linux/Mac:**
```bash
# Edit finetune.sh → set LLAMA_DIR
chmod +x train/finetune.sh
./train/finetune.sh
```

Scripts:
1. 📄 Convert `examples.jsonl` → training format
2. 🏋️ Run LoRA — 1–4 hours
3. 🔗 Merge adapter → `train/models/gawain-finetuned.gguf`

**Watch loss:** should drop from ~2.5 → ~0.8. If ~0.1, you're overfitting — add more varied data.

### 📦 Step 5 — Import into Ollama

```bash
ollama create gawain-finetuned -f train/Modelfile.finetuned

ollama run gawain-finetuned "Why did Bikes revenue drop in 2013?"

# .env → OLLAMA_MODEL=gawain-finetuned
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🎯 Tips for Better Training Data — アラサカ品質基準

### 📊 Coverage — Every Query Pattern

| 🔍 Pattern | 📝 Example | 🧩 SQL Feature |
|------------|-----------|----------------|
| **Aggregations** | "Total revenue, orders" | `SUM, COUNT, AVG` |
| **Time-series** | "Monthly revenue 2013" | `GROUP BY year/month/quarter` |
| **Joins** | "Revenue by category" | 2-JOIN via Subcategory |
| **Filters** | "Bikes in Germany 2013" | `WHERE` |
| **Rankings** | "Top 10 customers" | `TOP N, ORDER BY` |
| **Comparisons** | "2012 vs 2013 by territory" | `CASE WHEN, YoY` |
| **Segmentation** | "Sales by occupation" | `EnglishOccupation` |

### ✅ Quality > Quantity

**50 excellent examples beat 500 sloppy ones** 💎

- Test every SQL in SSMS
- Use exact column names — no hallucinations
- Keep questions natural, how users ask

### 🔧 Correct Errors

If model generates wrong SQL:

1. In UI: `EDIT // RE-EXECUTE` → fix → `◈ TRAIN_CORE` to save
2. Or add corrected pair to `examples.jsonl`
3. Run `python train/prepare_data.py --from-history` to pull history as starters

### 📜 Domain Rules

Add pairs that teach non-obvious rules:

```json
{"question": "Sales by product name", "sql": "SELECT dp.EnglishProductName ... -- EnglishProductName NOT Name"}
{"question": "Customer segment", "sql": "SELECT dc.EnglishOccupation ... -- EnglishOccupation NOT CustomerSegment"}
{"question": "Filter 2013", "sql": "SELECT ... WHERE dd.CalendarYear=2013 -- use dim date, NEVER GETDATE()"}
```

---

## 📂 File Reference — アーカイブ

| 📄 File | 🎯 Purpose | ✏️ Edit? |
|---------|-----------|----------|
| `Modelfile` | Ollama config — system prompt + few-shots + params | ✅ Add MESSAGES |
| `Modelfile.finetuned` | Config pointing to finetuned GGUF | ✅ Update path |
| `prepare_data.py` | Generates pairs from live DB + history | ✅ Can tweak |
| `example_pairs.jsonl` | 🌱 Seed examples — safe to commit | ✅ Add yours |
| `finetune.bat` / `.sh` | Training scripts — set `LLAMA_DIR` | ✅ Set path |
| `data/` | Generated `examples.jsonl` (gitignored) | Generated |
| `models/` | Base + finetuned models (gitignored, ~15GB) | Downloaded |
| `../storage/history.db` | App history — source for `--from-history` | Source |

---

## 🧠 Which Level Should You Use? — アラサカレベル

| Level | Method | Time | GPU | Accuracy | When |
|-------|--------|------|-----|----------|------|
| **LVL 1** | Base model | 0 min | No | ⭐⭐ | Demo |
| **LVL 2** | Modelfile + 50 ex | 5 min | No | ⭐⭐⭐ | Most teams |
| **LVL 3** | Modelfile + 200 ex | 30 min | No | ⭐⭐⭐½ | Complex schema |
| **LVL 4** | LoRA + 100 ex | 2h | Yes | ⭐⭐⭐⭐ | Production |
| **LVL 5** | LoRA + 500 ex + Modelfile wrapper | 4h | Yes | ⭐⭐⭐⭐⭐ | Maximum accuracy — アラサカ最高品質 |

---

## 🚀 Next Steps — アラサカ次のステップ

1. `prepare_data.py` → generate examples
2. Edit `examples.jsonl` → 100+ quality pairs
3. `ollama create gawain-sql -f train/Modelfile` → test
4. If needed: finetune → `gawain-finetuned`
5. `.env` → `OLLAMA_MODEL=gawain-...`
6. Restart backend → test in UI: `QUARTERLY_REVENUE_TREND 2010→2014`

Good luck — アラサカはあなたと共にあります 🏢
