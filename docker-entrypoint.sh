#!/usr/bin/env bash
# ⬢ ARASAKA // GAWAIN ENGINE — container entrypoint
set -euo pipefail

OLLAMA_HOST="${OLLAMA_BASE_URL:-http://ollama:11434}"
MODEL="${OLLAMA_MODEL:-llama3.1:latest}"
OLLAMA_WAIT_RETRIES="${OLLAMA_WAIT_RETRIES:-30}"
OLLAMA_AUTO_PULL="${OLLAMA_AUTO_PULL:-1}"

echo "⬢ ARASAKA // GAWAIN ENGINE starting..."
echo "   Ollama URL : ${OLLAMA_HOST}"
echo "   Model      : ${MODEL}"
echo "   DB         : ${DB_SERVER:-<unset>} / ${DB_DATABASE:-<unset>}"
echo "   ODBC       : $(odbcinst -q -d | tr -d '[]' | paste -sd ', ' -)"

# Wait for Ollama, then make sure the model is present.
# Never fatal: the UI and SQL paths must come up even with no LLM available.
if [[ "${OLLAMA_AUTO_PULL}" == "1" ]]; then
  echo "⏳ Waiting for Ollama at ${OLLAMA_HOST} ..."
  ollama_up=0
  for i in $(seq 1 "${OLLAMA_WAIT_RETRIES}"); do
    if curl -fsS --max-time 3 "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
      echo "✅ Ollama online"
      ollama_up=1
      break
    fi
    echo "   ... waiting (${i}/${OLLAMA_WAIT_RETRIES})"
    sleep 2
  done

  if [[ "${ollama_up}" == "1" ]]; then
    # Match the model as a JSON string value, not a bare substring. The old
    # `grep -q "$MODEL"` matched "llama3.1:latest" against any tag containing
    # it, and an empty tag list still matched on some shells.
    if curl -fsS --max-time 5 "${OLLAMA_HOST}/api/tags" \
        | grep -q "\"name\":\"${MODEL}\""; then
      echo "✅ Model ${MODEL} already present"
    else
      echo "📥 Pulling model ${MODEL} (may take several minutes) ..."
      curl -fsS -X POST "${OLLAMA_HOST}/api/pull" \
        -H 'Content-Type: application/json' \
        -d "{\"name\":\"${MODEL}\",\"stream\":false}" \
        || echo "⚠️  Pull failed — will retry lazily on first request"
    fi
  else
    echo "⚠️  Ollama unreachable — starting anyway (chat features will degrade)"
  fi
fi

echo "🚀 Starting FastAPI on :8000"
exec uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'