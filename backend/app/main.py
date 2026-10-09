from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import router
from app.articles import router as article_router
from app.auth import router as auth_router, session_key
from app.config import get_settings
from app.database import get_session

app = FastAPI(title="AutoLog Technical Blog API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(router)
app.include_router(article_router)
app.include_router(auth_router)


@app.get("/openapi-action.json", include_in_schema=False)
def action_schema() -> dict:
    import copy
    schema = copy.deepcopy(app.openapi())
    schema["info"]["description"] = "Write complete Markdown technical articles from conversations in your AI client. Search existing topics before creating or updating. No server OpenAI API is called."
    schema["servers"] = [{"url": get_settings().action_server_url.rstrip("/")}]
    schema["paths"] = {path: item for path, item in schema["paths"].items()
                       if path.startswith("/api/articles") or path in {"/api/categories", "/api/tags"}}
    return schema


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready(session: Session = Depends(get_session)) -> dict[str, str]:
    if not get_settings().api_token or not get_settings().api_token.get_secret_value():
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    if session_key() is None:
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    try:
        session.execute(text("SELECT id FROM entries LIMIT 1"))
        session.execute(text("SELECT id FROM categories LIMIT 1"))
        session.execute(text("SELECT id FROM articles LIMIT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database not ready") from None
    return {"status": "ready"}
