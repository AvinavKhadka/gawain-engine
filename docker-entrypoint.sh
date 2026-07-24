#!/bin/bash
set -e

# Default Ollama URL inside docker-compose is http://ollama:11434
OLLAMA_HOST=${OLLAMA_BASE_URL:-http://ollama:11434}
MODEL=${OLLAMA_MODEL:-llama3.1:latest}

echo "⬢ ARASAKA // GAWAIN ENGINE starting..."
echo "   Ollama URL: $OLLAMA_HOST"
echo "   Model: $MODEL"
echo "   DB: $DB_SERVER / $DB_DATABASE"

# Wait for Ollama (if using docker-compose ollama service)
if [[ "$OLLAMA_HOST" == *"ollama:11434"* ]]; then
  echo "⏳ Waiting for Ollama at $OLLAMA_HOST..."
  for i in {1..30}; do
    if curl -s "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
      echo "✅ Ollama online"
      break
    fi
    echo "   ... waiting ($i/30)"
    sleep 2
  done

  # Pull model if not exists (don't fail container if pull fails)
  if curl -s "$OLLAMA_HOST/api/tags" | grep -q "$MODEL" 2>/dev/null; then
    echo "✅ Model $MODEL already present"
  else
    echo "📥 Pulling model $MODEL (may take minutes)..."
    curl -s -X POST "$OLLAMA_HOST/api/pull" -d "{\"name\":\"$MODEL\"}" || echo "⚠️ Pull maybe failed, will try on first request"
  fi
fi

echo "🚀 Starting FastAPI on :8000"
exec uvicorn main:app --host 0.0.0.0 --port 8000
