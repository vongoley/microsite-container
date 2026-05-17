from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import sqlite3
import uuid
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "html_store.db"
UPLOADS_DIR = BASE_DIR / "data" / "uploads"
TEMPLATES_DIR = BASE_DIR / "templates"

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-production")
SESSION_COOKIE = "admin_session"
SESSION_TTL_HOURS = 24


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def make_session_token(username: str) -> str:
    payload = f"{username}:{datetime.utcnow().isoformat()}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> bool:
    try:
        parts = token.rsplit(":", 1)
        if len(parts) != 2:
            return False
        payload, sig = parts
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        _, ts = payload.split(":", 1)
        created = datetime.fromisoformat(ts)
        if datetime.utcnow() - created > timedelta(hours=SESSION_TTL_HOURS):
            return False
        return True
    except Exception:
        return False


def get_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            file_path TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def require_admin(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Set default password if none configured
    global ADMIN_PASSWORD_HASH
    if not ADMIN_PASSWORD_HASH:
        ADMIN_PASSWORD_HASH = hash_password("admin123")
        print("WARNING: Using default password 'admin123'. Set ADMIN_PASSWORD_HASH env var in production.")
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Public: view shared page ──────────────────────────────────────────────────

@app.get("/view/{page_id}", response_class=HTMLResponse)
async def view_page(page_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found")
    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    return HTMLResponse(content=file_path.read_text(encoding="utf-8"))


# ── Admin: login ──────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/admin/login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == ADMIN_USERNAME and hmac.compare_digest(
        hash_password(password), ADMIN_PASSWORD_HASH
    ):
        token = make_session_token(username)
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL_HOURS * 3600)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)


@app.get("/admin/logout")
async def logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── Admin: dashboard ──────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request, db: sqlite3.Connection = Depends(get_db), _: bool = Depends(require_admin)):
    rows = db.execute("SELECT * FROM pages ORDER BY uploaded_at DESC").fetchall()
    pages = [dict(r) for r in rows]
    return templates.TemplateResponse("admin.html", {"request": request, "pages": pages})


@app.post("/admin/upload")
async def upload_html(
    request: Request,
    title: str = Form(...),
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
    _: bool = Depends(require_admin),
):
    if not file.filename.endswith(".html"):
        raise HTTPException(status_code=400, detail="Only .html files allowed")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    page_id = str(uuid.uuid4())[:8]
    # ensure uniqueness
    while db.execute("SELECT 1 FROM pages WHERE id = ?", (page_id,)).fetchone():
        page_id = str(uuid.uuid4())[:8]

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    file_path = UPLOADS_DIR / f"{page_id}_{safe_name}"
    file_path.write_bytes(content)

    db.execute(
        "INSERT INTO pages (id, title, original_filename, uploaded_at, file_path) VALUES (?, ?, ?, ?, ?)",
        (page_id, title, file.filename, datetime.utcnow().isoformat(), str(file_path)),
    )
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete/{page_id}")
async def delete_page(
    page_id: str,
    db: sqlite3.Connection = Depends(get_db),
    _: bool = Depends(require_admin),
):
    row = db.execute("SELECT file_path FROM pages WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    fp = Path(row["file_path"])
    if fp.exists():
        fp.unlink()
    db.execute("DELETE FROM pages WHERE id = ?", (page_id,))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return RedirectResponse(url="/admin/login")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
