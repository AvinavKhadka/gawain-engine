# Gawain — Model Training Guide

Two approaches to customize the LLM for your SQL database:

| Approach | GPU needed | Time | Quality gain |
|----------|-----------|------|-------------|
| **Modelfile** (Ollama) | No | ~5 minutes | Medium — better prompting |
| **LoRA fine-tuning** (llama.cpp) | Recommended | 1–4 hours | High — model learns your schema |

---

## Approach 1: Ollama Modelfile (Quick Start)

A Modelfile bakes your system prompt and few-shot examples directly into the model.
No training. No GPU. Works immediately.

### Step 1 — Generate training examples from your live database

```bash
# From project root with venv active
cd ..
python train/prepare_data.py

# This creates: train/data/examples.jsonl
# Edit it to add, remove, or correct examples before building the model
```

### Step 2 — Review and edit examples

Open `train/data/examples.jsonl`. Each line is one Q&A pair:
```json
{"question": "Show total revenue by year", "sql": "SELECT dd.CalendarYear..."}
```

Add your own domain-specific pairs. More examples = more accurate model.
Aim for **50–200 pairs** covering the questions your users actually ask.

### Step 3 — Build the custom Ollama model

```bash
# From project root
ollama create gawain-sql -f train/Modelfile

# Test it
ollama run gawain-sql "Show me revenue by product category"

# Update .env to use it
# OLLAMA_MODEL=gawain-sql
```

### Step 4 — Iterate

After testing, edit `train/Modelfile` to:
- Add more `MESSAGE` few-shot examples
- Tighten the `SYSTEM` prompt with domain rules
- Adjust `PARAMETER temperature` (lower = more deterministic SQL)

Then rebuild: `ollama create gawain-sql -f train/Modelfile`

---

## Approach 2: llama.cpp LoRA Fine-tuning

This actually adjusts the model's weights to deeply learn your schema and SQL patterns.
Produces the highest quality results for domain-specific databases.

### Prerequisites

- **GPU**: NVIDIA with 8GB+ VRAM recommended (RTX 3060 or better)
  - CPU-only works but takes 10–20× longer
- **Disk**: ~15GB free (base model + training artefacts)
- **RAM**: 16GB+ recommended

### Step 1 — Install llama.cpp

**Windows (PowerShell):**
```powershell
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp

# CPU only
cmake -B build -DGGML_NATIVE=ON
cmake --build build --config Release -j8

# NVIDIA GPU (recommended)
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j8
```

**Linux/Mac:**
```bash
git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp
make -j8
# GPU: make GGML_CUDA=1 -j8
```

Copy the `llama.cpp/` folder path — you'll need it in the next step.

### Step 2 — Download a base model (GGUF format)

```bash
pip install huggingface_hub

# Recommended: Llama-3.1-8B (good balance of size and quality)
huggingface-cli download \
  bartowski/Meta-Llama-3.1-8B-Instruct-GGUF \
  Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
  --local-dir train/models/

# Smaller/faster: Llama-3.2-3B (good for CPU or limited VRAM)
huggingface-cli download \
  bartowski/Llama-3.2-3B-Instruct-GGUF \
  Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --local-dir train/models/
```

### Step 3 — Generate and review training data

```bash
# Generate Q&A pairs from your live database
python train/prepare_data.py

# This produces train/data/examples.jsonl
# Review and add your own examples (aim for 100+ total)
```

Format of `examples.jsonl`:
```json
{"question": "Show total revenue by year", "sql": "SELECT dd.CalendarYear, SUM(fis.SalesAmount) AS Revenue FROM dbo.FactInternetSales fis JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey GROUP BY dd.CalendarYear ORDER BY dd.CalendarYear"}
```

### Step 4 — Run fine-tuning

**Windows:**
```bat
# Edit finetune.bat first — set LLAMA_DIR to your llama.cpp path
train\finetune.bat
```

**Linux/Mac:**
```bash
# Edit finetune.sh first — set LLAMA_DIR to your llama.cpp path
chmod +x train/finetune.sh
./train/finetune.sh
```

Both scripts will:
1. Convert `examples.jsonl` → training format
2. Run LoRA fine-tuning (~1–4 hours depending on GPU)
3. Merge LoRA adapter into a new GGUF file

Output: `train/models/gawain-finetuned.gguf`

### Step 5 — Import into Ollama

```bash
# Build Ollama model from fine-tuned weights
ollama create gawain-finetuned -f train/Modelfile.finetuned

# Test it
ollama run gawain-finetuned "Why did Bikes revenue drop in 2013?"

# Update project .env
# OLLAMA_MODEL=gawain-finetuned
```

---

## Tips for Better Training Data

**Coverage**: Include examples for every major query pattern your users need:
- Aggregations (SUM, COUNT, AVG)
- Time-series (GROUP BY year/month/quarter)
- Joins (multiple tables)
- Filters (WHERE clauses)
- Rankings (TOP N, ORDER BY)
- Comparisons (CASE WHEN, year-over-year)

**Quality over quantity**: 50 excellent examples beat 500 sloppy ones.
Test every SQL in your database before adding it to training data.

**Correct errors**: If the model generates wrong SQL, add a corrected example.
Run `python train/prepare_data.py --from-history` to pull recent questions
and generated SQL from the app's history database as starting points.

**Domain rules**: Add example pairs that demonstrate your specific constraints:
- Which columns to use for dates
- Required JOINs (e.g., always go via DimProductSubcategory)
- Preferred column aliases

---

## File Reference

| File | Purpose |
|------|---------|
| `Modelfile` | Ollama model config (system prompt + few-shot examples) |
| `Modelfile.finetuned` | Ollama model config pointing to fine-tuned GGUF |
| `prepare_data.py` | Generates training pairs from your live SQL Server database |
| `example_pairs.jsonl` | Seed examples — add your own here |
| `finetune.bat` | Windows llama.cpp training script |
| `finetune.sh` | Linux/Mac llama.cpp training script |
| `data/` | Generated training files (gitignored) |
| `models/` | Downloaded base models + fine-tuned outputs (gitignored) |
