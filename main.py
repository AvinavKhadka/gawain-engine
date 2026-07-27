import os
import threading

# Configure logging before importing anything that might log on import, so no
# record is emitted through an unconfigured root handler.
from config.observability import setup_logging, get_logger, preflight

setup_logging()

from fastapi import FastAPI                              # noqa: E402
from fastapi.staticfiles import StaticFiles              # noqa: E402
from fastapi.responses import FileResponse               # noqa: E402

from server.routes import router                         # noqa: E402
from server.history import init_db                       # noqa: E402
from server.llm import warmup                            # noqa: E402
from config.settings import STATIC_DIR                   # noqa: E402

log = get_logger(__name__)

app = FastAPI(title="Gawain")
app.include_router(router)

if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")


@app.on_event("startup")
def startup():
    log.info("⬢ Gawain Engine starting")

    # Report misconfiguration loudly. Non-fatal by design — the UI must serve
    # even with no database and no LLM. Set GAWAIN_STRICT_ENV=1 to hard-fail.
    preflight()

    try:
        init_db()
        log.info("history store ready")
    except Exception:
        # Previously this would kill startup with a bare traceback.
        log.exception("history store failed to initialise — history disabled")

    threading.Thread(target=warmup, daemon=True).start()
    log.info("startup complete — LLM warmup running in background")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str = ""):
    if full_path.startswith("api/"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
