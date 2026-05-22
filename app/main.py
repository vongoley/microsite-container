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
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import asynccontextmanager

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "html_store.db"
UPLOADS_DIR = BASE_DIR / "data" / "uploads"
TEMPLATES_DIR = BASE_DIR / "templates"

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-me-in-production")
API_KEY = os.environ.get("API_KEY", "")
SESSION_COOKIE = "admin_session"
SESSION_TTL_HOURS = 24
TZ_BEIJING = timezone(timedelta(hours=8))
ALLOWED_EXTENSIONS = (".html", ".md")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def make_session_token(user_id: str, username: str) -> str:
    ts = int(datetime.now(TZ_BEIJING).timestamp())
    payload = f"{user_id}:{username}:{ts}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str):
    try:
        parts = token.rsplit(":", 1)
        if len(parts) != 2:
            return None
        payload, sig = parts
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        segments = payload.split(":", 2)
        if len(segments) != 3:
            return None
        user_id, username, ts_str = segments
        created = int(ts_str)
        now = int(datetime.now(TZ_BEIJING).timestamp())
        if now - created > SESSION_TTL_HOURS * 3600:
            return None
        return user_id
    except Exception:
        return None


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
    con.row_factory = sqlite3.Row

    con.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            file_path TEXT NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL,
            created_by TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            used_at TEXT,
            used_by TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS user_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            token TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cols = [r[1] for r in con.execute("PRAGMA table_info(pages)").fetchall()]
    if "slug" not in cols:
        con.execute("ALTER TABLE pages ADD COLUMN slug TEXT")
    if "owner_id" not in cols:
        con.execute("ALTER TABLE pages ADD COLUMN owner_id TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_slug ON pages(slug) WHERE slug IS NOT NULL")

    existing_super = con.execute("SELECT id FROM users WHERE role = 'super_admin' LIMIT 1").fetchone()
    if not existing_super:
        global ADMIN_PASSWORD_HASH
        if not ADMIN_PASSWORD_HASH:
            ADMIN_PASSWORD_HASH = hash_password("admin123")
            print("WARNING: Using default password 'admin123'. Set ADMIN_PASSWORD_HASH env var in production.")
        super_id = str(uuid.uuid4())[:8]
        con.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'super_admin', ?)",
            (super_id, ADMIN_USERNAME, ADMIN_PASSWORD_HASH, datetime.now(TZ_BEIJING).isoformat()),
        )
        con.execute("UPDATE pages SET owner_id = ? WHERE owner_id IS NULL", (super_id,))
    else:
        super_id = existing_super["id"]
        con.execute("UPDATE pages SET owner_id = ? WHERE owner_id IS NULL", (super_id,))

    con.commit()
    con.close()


def get_current_user(request: Request, db: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    user_id = verify_session_token(token)
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    user = db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return dict(user)


def require_super_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    return user


def get_api_user(request: Request, db: sqlite3.Connection = Depends(get_db)):
    """Authenticate API requests. Supports per-user tokens and legacy global API_KEY."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API token")
    token = auth[7:]

    # Check per-user tokens first
    row = db.execute(
        "SELECT ut.user_id FROM user_tokens ut JOIN users u ON ut.user_id = u.id WHERE ut.token = ? AND ut.is_active = 1 AND u.is_active = 1",
        (token,),
    ).fetchone()
    if row:
        return row["user_id"]

    # Fallback to legacy global API_KEY
    if API_KEY and hmac.compare_digest(token, API_KEY):
        super_admin = db.execute("SELECT id FROM users WHERE role = 'super_admin' LIMIT 1").fetchone()
        return super_admin["id"] if super_admin else None

    raise HTTPException(status_code=401, detail="Invalid API token")


MARKDOWN_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-light.min.css">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #fff; }}
  .layout {{ display: flex; max-width: 1200px; margin: 0 auto; min-height: 100vh; }}
  .toc {{
    position: sticky; top: 0; align-self: flex-start;
    width: 240px; flex-shrink: 0; padding: 2rem 1rem 2rem 1.5rem;
    height: 100vh; overflow-y: auto; border-right: 1px solid #e5e7eb;
    font-size: .85rem; line-height: 1.6;
  }}
  .toc ul {{ list-style: none; padding: 0; }}
  .toc li {{ margin-bottom: .25rem; }}
  .toc a {{ color: #6b7280; text-decoration: none; display: block; padding: .15rem 0; border-radius: 3px; transition: color .15s; }}
  .toc a:hover {{ color: #4f46e5; }}
  .toc a.active {{ color: #4f46e5; font-weight: 500; }}
  .toc .toc-h2 {{ padding-left: .75rem; }}
  .toc .toc-h3 {{ padding-left: 1.5rem; font-size: .8rem; }}
  .main-content {{ flex: 1; min-width: 0; padding: 2rem 2.5rem; }}
  .markdown-body {{ font-size: 1rem; }}
  @media (max-width: 900px) {{
    .toc {{ display: none; }}
    .main-content {{ padding: 1.5rem 1rem; }}
  }}
</style>
</head>
<body>
<div class="layout">
  <nav class="toc" id="toc"></nav>
  <article class="main-content markdown-body" id="content"></article>
</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const b64 = "{content_b64}";
const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
const text = new TextDecoder('utf-8').decode(bytes);
document.getElementById('content').innerHTML = marked.parse(text);

// Generate TOC
const headings = document.querySelectorAll('#content h1, #content h2, #content h3');
const tocEl = document.getElementById('toc');
let tocHtml = '<ul>';
headings.forEach((h, i) => {{
  const id = 'heading-' + i;
  h.id = id;
  const level = h.tagName.toLowerCase();
  tocHtml += '<li><a href="#' + id + '" class="toc-' + level + '">' + h.textContent + '</a></li>';
}});
tocHtml += '</ul>';
tocEl.innerHTML = tocHtml;

// Highlight active TOC item on scroll
const tocLinks = tocEl.querySelectorAll('a');
const observer = new IntersectionObserver(entries => {{
  entries.forEach(entry => {{
    if (entry.isIntersecting) {{
      tocLinks.forEach(a => a.classList.remove('active'));
      const active = tocEl.querySelector('a[href="#' + entry.target.id + '"]');
      if (active) active.classList.add('active');
    }}
  }});
}}, {{ rootMargin: '0px 0px -70% 0px' }});
headings.forEach(h => observer.observe(h));
</script>
</body>
</html>"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Public: view shared page ──────────────────────────────────────────────────

@app.get("/view/{page_id:path}", response_class=HTMLResponse)
async def view_page(page_id: str, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    if not row:
        row = db.execute("SELECT * FROM pages WHERE slug = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found")
    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing")

    content = file_path.read_text(encoding="utf-8")
    filename = row["original_filename"]

    if filename.endswith(".md"):
        import base64
        content_b64 = base64.b64encode(content.encode()).decode()
        html = MARKDOWN_TEMPLATE.format(title=row["title"], content_b64=content_b64)
        return HTMLResponse(content=html)

    return HTMLResponse(content=content)


# ── Admin: login ──────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/admin/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),
):
    user = db.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)).fetchone()
    if user and hmac.compare_digest(hash_password(password), user["password_hash"]):
        token = make_session_token(user["id"], user["username"])
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL_HOURS * 3600)
        return resp
    return templates.TemplateResponse(request, "login.html", {"error": "用户名或密码错误"}, status_code=401)


@app.get("/admin/logout")
async def logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── Admin: dashboard ──────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_index(request: Request, db: sqlite3.Connection = Depends(get_db), user: dict = Depends(get_current_user)):
    if user["role"] == "super_admin":
        rows = db.execute(
            "SELECT p.*, u.username as owner_name FROM pages p LEFT JOIN users u ON p.owner_id = u.id ORDER BY p.uploaded_at DESC"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT p.*, u.username as owner_name FROM pages p LEFT JOIN users u ON p.owner_id = u.id WHERE p.owner_id = ? ORDER BY p.uploaded_at DESC",
            (user["id"],),
        ).fetchall()
    pages = [dict(r) for r in rows]
    return templates.TemplateResponse(request, "admin.html", {"pages": pages, "user": user})


@app.post("/admin/upload")
async def upload_html(
    request: Request,
    title: str = Form(...),
    file: UploadFile = File(...),
    slug: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if not any(file.filename.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="仅支持 .html 和 .md 文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    slug = slug.strip() or None
    if slug and not re.match(r"^[\w][\w./-]*$", slug, re.UNICODE):
        raise HTTPException(status_code=400, detail="Slug 格式无效")

    if slug:
        existing = db.execute("SELECT id, file_path, owner_id FROM pages WHERE slug = ?", (slug,)).fetchone()
        if existing:
            if user["role"] != "super_admin" and existing["owner_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="无权替换此页面")
            old_fp = Path(existing["file_path"])
            if old_fp.exists():
                old_fp.unlink()
            safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
            file_path = UPLOADS_DIR / f"{existing['id']}_{safe_name}"
            file_path.write_bytes(content)
            db.execute(
                "UPDATE pages SET title=?, original_filename=?, uploaded_at=?, file_path=? WHERE slug=?",
                (title, file.filename, datetime.now(TZ_BEIJING).isoformat(), str(file_path), slug),
            )
            db.commit()
            return RedirectResponse(url="/admin", status_code=303)
    else:
        base_slug = re.sub(r"[^\w]+", "-", title, flags=re.UNICODE).strip("-")
        if not base_slug:
            base_slug = "page-" + str(uuid.uuid4())[:6]
        slug = base_slug
        suffix = 2
        while db.execute("SELECT 1 FROM pages WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

    page_id = str(uuid.uuid4())[:8]
    while db.execute("SELECT 1 FROM pages WHERE id = ?", (page_id,)).fetchone():
        page_id = str(uuid.uuid4())[:8]

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    file_path = UPLOADS_DIR / f"{page_id}_{safe_name}"
    file_path.write_bytes(content)

    db.execute(
        "INSERT INTO pages (id, title, original_filename, uploaded_at, file_path, slug, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (page_id, title, file.filename, datetime.now(TZ_BEIJING).isoformat(), str(file_path), slug, user["id"]),
    )
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete/{page_id}")
async def delete_page(
    page_id: str,
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    row = db.execute("SELECT file_path, owner_id FROM pages WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    if user["role"] != "super_admin" and row["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="无权删除此页面")
    fp = Path(row["file_path"])
    if fp.exists():
        fp.unlink()
    db.execute("DELETE FROM pages WHERE id = ?", (page_id,))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ── Admin: API Token management ──────────────────────────────────────────────

@app.get("/admin/token", response_class=HTMLResponse)
async def token_page(request: Request, db: sqlite3.Connection = Depends(get_db), user: dict = Depends(get_current_user)):
    tokens = [dict(r) for r in db.execute(
        "SELECT * FROM user_tokens WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()]
    return templates.TemplateResponse(request, "token.html", {"tokens": tokens, "user": user})


@app.post("/admin/token/create")
async def create_token(
    request: Request,
    name: str = Form("default"),
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    token_id = str(uuid.uuid4())[:8]
    token_value = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO user_tokens (id, user_id, token, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (token_id, user["id"], token_value, name.strip() or "default", datetime.now(TZ_BEIJING).isoformat()),
    )
    db.commit()
    return RedirectResponse(url="/admin/token", status_code=303)


@app.post("/admin/token/{token_id}/delete")
async def delete_token(
    token_id: str,
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    db.execute("DELETE FROM user_tokens WHERE id = ? AND user_id = ?", (token_id, user["id"]))
    db.commit()
    return RedirectResponse(url="/admin/token", status_code=303)


# ── Admin: user management (super admin only) ────────────────────────────────

@app.get("/admin/users", response_class=HTMLResponse)
async def users_page(request: Request, db: sqlite3.Connection = Depends(get_db), user: dict = Depends(require_super_admin)):
    users = [dict(r) for r in db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()]
    invitations = [dict(r) for r in db.execute(
        "SELECT i.*, u.username as creator_name FROM invitations i LEFT JOIN users u ON i.created_by = u.id ORDER BY i.created_at DESC"
    ).fetchall()]
    return templates.TemplateResponse(request, "users.html", {"users": users, "invitations": invitations, "user": user})


@app.post("/admin/users/invite")
async def create_invitation(
    request: Request,
    role: str = Form("admin"),
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(require_super_admin),
):
    if role not in ("admin", "super_admin"):
        role = "admin"
    invite_id = str(uuid.uuid4())[:8]
    code = secrets.token_urlsafe(16)
    db.execute(
        "INSERT INTO invitations (id, code, role, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
        (invite_id, code, role, datetime.now(TZ_BEIJING).isoformat(), user["id"]),
    )
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/invite/{invite_id}/delete")
async def delete_invitation(
    invite_id: str,
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(require_super_admin),
):
    db.execute("DELETE FROM invitations WHERE id = ? AND used_at IS NULL", (invite_id,))
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
async def toggle_user(
    user_id: str,
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(require_super_admin),
):
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="不能禁用自己")
    new_status = 0 if target["is_active"] else 1
    db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/role")
async def change_role(
    user_id: str,
    role: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),
    user: dict = Depends(require_super_admin),
):
    if role not in ("admin", "super_admin"):
        raise HTTPException(status_code=400, detail="Invalid role")
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


# ── Public: registration via invite ──────────────────────────────────────────

@app.get("/admin/register/{code}", response_class=HTMLResponse)
async def register_page(request: Request, code: str, db: sqlite3.Connection = Depends(get_db)):
    invite = db.execute("SELECT * FROM invitations WHERE code = ? AND used_at IS NULL", (code,)).fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请链接无效或已被使用")
    return templates.TemplateResponse(request, "register.html", {"code": code, "role": invite["role"], "error": None})


@app.post("/admin/register/{code}")
async def register(
    request: Request,
    code: str,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),
):
    invite = db.execute("SELECT * FROM invitations WHERE code = ? AND used_at IS NULL", (code,)).fetchone()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请链接无效或已被使用")

    if password != password_confirm:
        return templates.TemplateResponse(request, "register.html", {"code": code, "role": invite["role"], "error": "两次密码不一致"}, status_code=400)

    if len(username) < 2:
        return templates.TemplateResponse(request, "register.html", {"code": code, "role": invite["role"], "error": "用户名至少2个字符"}, status_code=400)

    if len(password) < 6:
        return templates.TemplateResponse(request, "register.html", {"code": code, "role": invite["role"], "error": "密码至少6个字符"}, status_code=400)

    existing = db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return templates.TemplateResponse(request, "register.html", {"code": code, "role": invite["role"], "error": "用户名已被占用"}, status_code=400)

    user_id = str(uuid.uuid4())[:8]
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, hash_password(password), invite["role"], datetime.now(TZ_BEIJING).isoformat(), invite["created_by"]),
    )
    db.execute(
        "UPDATE invitations SET used_at = ?, used_by = ? WHERE code = ?",
        (datetime.now(TZ_BEIJING).isoformat(), user_id, code),
    )
    db.commit()
    return RedirectResponse(url="/admin/login", status_code=303)


# ── API: programmatic upload/replace ─────────────────────────────────────────

@app.put("/api/pages/{slug:path}")
async def api_upsert_page(
    slug: str,
    title: str = Form(None),
    file: UploadFile = File(...),
    db: sqlite3.Connection = Depends(get_db),
    owner_id: str = Depends(get_api_user),
):
    if not re.match(r"^[\w][\w./-]*$", slug, re.UNICODE):
        raise HTTPException(status_code=400, detail="Invalid slug")

    if not any((file.filename or "").endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="仅支持 .html 和 .md 文件")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    page_title = title or slug
    existing = db.execute("SELECT id, file_path FROM pages WHERE slug = ?", (slug,)).fetchone()

    if existing:
        old_fp = Path(existing["file_path"])
        if old_fp.exists():
            old_fp.unlink()
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "page.html")
        file_path = UPLOADS_DIR / f"{existing['id']}_{safe_name}"
        file_path.write_bytes(content)
        db.execute(
            "UPDATE pages SET title=?, original_filename=?, uploaded_at=?, file_path=? WHERE slug=?",
            (page_title, file.filename or "page.html", datetime.now(TZ_BEIJING).isoformat(), str(file_path), slug),
        )
        db.commit()
        page_id = existing["id"]
    else:
        page_id = str(uuid.uuid4())[:8]
        while db.execute("SELECT 1 FROM pages WHERE id = ?", (page_id,)).fetchone():
            page_id = str(uuid.uuid4())[:8]
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename or "page.html")
        file_path = UPLOADS_DIR / f"{page_id}_{safe_name}"
        file_path.write_bytes(content)
        db.execute(
            "INSERT INTO pages (id, title, original_filename, uploaded_at, file_path, slug, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (page_id, page_title, file.filename or "page.html", datetime.now(TZ_BEIJING).isoformat(), str(file_path), slug, owner_id),
        )
        db.commit()

    return JSONResponse({"slug": slug, "id": page_id, "url": f"/view/{slug}"})


@app.get("/api/pages")
async def api_list_pages(
    db: sqlite3.Connection = Depends(get_db),
    owner_id: str = Depends(get_api_user),
):
    rows = db.execute("SELECT id, title, slug, uploaded_at FROM pages WHERE owner_id = ? ORDER BY uploaded_at DESC", (owner_id,)).fetchall()
    return JSONResponse([{"id": r["id"], "title": r["title"], "slug": r["slug"], "uploaded_at": r["uploaded_at"]} for r in rows])


@app.delete("/api/pages/{slug:path}")
async def api_delete_page(
    slug: str,
    db: sqlite3.Connection = Depends(get_db),
    owner_id: str = Depends(get_api_user),
):
    row = db.execute("SELECT id, file_path, owner_id FROM pages WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found")
    if row["owner_id"] != owner_id:
        user = db.execute("SELECT role FROM users WHERE id = ?", (owner_id,)).fetchone()
        if not user or user["role"] != "super_admin":
            raise HTTPException(status_code=403, detail="无权删除此页面")
    fp = Path(row["file_path"])
    if fp.exists():
        fp.unlink()
    db.execute("DELETE FROM pages WHERE id = ?", (row["id"],))
    db.commit()
    return JSONResponse({"deleted": slug})


# ── Skill install endpoint ────────────────────────────────────────────────────

SKILL_DIR = BASE_DIR / "skill"

@app.get("/api/install-skill")
async def install_skill(request: Request, token: str = "", os: str = "unix"):
    if not token:
        raise HTTPException(status_code=400, detail="Missing token parameter")
    base_url = str(request.base_url).rstrip("/")
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    upload_py = (SKILL_DIR / "upload.py").read_text(encoding="utf-8")

    if os == "win":
        # PowerShell script for Windows
        skill_md_escaped = skill_md.replace("'", "''")
        upload_py_escaped = upload_py.replace("'", "''")
        script = f"""$ErrorActionPreference = "Stop"

$SkillDir = "$env:USERPROFILE\\.claude\\skills\\html-container"
$ScriptsDir = "$SkillDir\\scripts"
$ConfigDir = "$env:USERPROFILE\\.config\\html-container"

Write-Host "Installing HTML Container skill..."

New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

@'
{skill_md_escaped}
'@ | Set-Content -Path "$SkillDir\\SKILL.md" -Encoding UTF8

@'
{upload_py_escaped}
'@ | Set-Content -Path "$ScriptsDir\\upload.py" -Encoding UTF8

@"
API_KEY={token}
BASE_URL={base_url}
"@ | Set-Content -Path "$ConfigDir\\credentials.env" -Encoding UTF8

Write-Host ""
Write-Host "Done! Skill installed successfully."
Write-Host "   Skill: $SkillDir"
Write-Host "   Config: $ConfigDir\\credentials.env"
Write-Host "   Server: {base_url}"
Write-Host ""
Write-Host "Claude Code can now upload HTML/Markdown files from any workspace."
"""
    else:
        script = f"""#!/usr/bin/env bash
set -e

SKILL_DIR="$HOME/.claude/skills/html-container"
SCRIPTS_DIR="$SKILL_DIR/scripts"
CONFIG_DIR="$HOME/.config/html-container"

echo "Installing HTML Container skill..."

mkdir -p "$SCRIPTS_DIR"
mkdir -p "$CONFIG_DIR"

cat > "$SKILL_DIR/SKILL.md" << 'SKILL_EOF'
{skill_md}
SKILL_EOF

cat > "$SCRIPTS_DIR/upload.py" << 'SCRIPT_EOF'
{upload_py}
SCRIPT_EOF

cat > "$CONFIG_DIR/credentials.env" << 'CRED_EOF'
API_KEY={token}
BASE_URL={base_url}
CRED_EOF

chmod +x "$SCRIPTS_DIR/upload.py"

echo ""
echo "Done! Skill installed successfully."
echo "   Skill: $SKILL_DIR"
echo "   Config: $CONFIG_DIR/credentials.env"
echo "   Server: {base_url}"
echo ""
echo "Claude Code can now upload HTML/Markdown files from any workspace."
"""
    return Response(content=script, media_type="text/plain")


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return RedirectResponse(url="/admin/login")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
