#!/usr/bin/env python3
"""HTML Container CLI - upload, list, delete HTML pages."""

import json
import sys
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import quote

CREDENTIALS_PATH = Path.home() / ".config" / "html-container" / "credentials.env"


def load_config():
    if not CREDENTIALS_PATH.exists():
        return None
    config = {}
    for line in CREDENTIALS_PATH.read_text().strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()
    return config


def api_request(method, path, config, data=None, content_type=None):
    base_url = config.get("BASE_URL", "https://html.orcacalf.site").rstrip("/")
    url = f"{base_url}{path}"
    req = Request(url, method=method)
    req.add_header("Authorization", f"Bearer {config['API_KEY']}")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        resp = urlopen(req, data=data, timeout=30)
        return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        return {"error": detail, "status_code": e.code}
    except URLError as e:
        return {"error": str(e.reason)}


def build_multipart(fields, files):
    boundary = "----HtmlContainerBoundary9876543210"
    body = b""
    for key, value in fields.items():
        # A list value becomes repeated form-data parts (FastAPI list[str] = Form()).
        values = value if isinstance(value, (list, tuple)) else [value]
        for v in values:
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
            body += f"{v}\r\n".encode()
    for key, (filename, content) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: text/html\r\n\r\n"
        body += content
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def cmd_check():
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "missing_config", "message": "Credentials not configured"}))
        return

    result = api_request("GET", "/api/pages", config)
    if "error" in result:
        print(json.dumps({"status": "api_error", "error": result["error"]}))
    else:
        print(json.dumps({"status": "ok", "pages_count": len(result)}))


def fetch_users(config):
    """Return the list of active users [{id, username, role}, ...] from the server."""
    result = api_request("GET", "/api/users", config)
    if isinstance(result, dict) and "error" in result:
        return None
    return result


def resolve_user_ids(config, users_arg):
    """Map a comma-separated list of usernames-or-ids to server user IDs.

    Unknown names are silently dropped; use `list-users` to see valid names."""
    if not users_arg:
        return []
    wanted = [u.strip() for u in users_arg.split(",") if u.strip()]
    if not wanted:
        return []
    users = fetch_users(config)
    if not users:
        return []
    by_username = {u["username"]: u["id"] for u in users}
    valid_ids = {u["id"] for u in users}
    resolved = []
    for w in wanted:
        if w in by_username:
            resolved.append(by_username[w])
        elif w in valid_ids:
            resolved.append(w)
    # De-dupe while preserving order.
    seen = set()
    return [x for x in resolved if not (x in seen or seen.add(x))]


def cmd_list_users(args):
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "error", "message": "Run 'upload.py check' first to configure credentials"}))
        sys.exit(1)
    users = fetch_users(config)
    if users is None:
        print(json.dumps({"status": "error", "message": "Failed to fetch users (server may not support /api/users)"}))
        sys.exit(1)
    print(json.dumps(users, ensure_ascii=False, indent=2))


def cmd_put(args):
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "error", "message": "Run 'upload.py check' first to configure credentials"}))
        sys.exit(1)

    slug = None
    title = None
    file_path = None
    visibility = None
    view_password = None
    users = None

    i = 0
    while i < len(args):
        if args[i] == "--slug" and i + 1 < len(args):
            slug = args[i + 1]
            i += 2
        elif args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        elif args[i] == "--file" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
        elif args[i] == "--visibility" and i + 1 < len(args):
            visibility = args[i + 1]
            i += 2
        elif args[i] == "--view-password" and i + 1 < len(args):
            view_password = args[i + 1]
            i += 2
        elif args[i] == "--users" and i + 1 < len(args):
            users = args[i + 1]
            i += 2
        else:
            i += 1

    if not slug:
        print(json.dumps({"status": "error", "message": "--slug is required"}))
        sys.exit(1)
    if not file_path:
        print(json.dumps({"status": "error", "message": "--file is required"}))
        sys.exit(1)

    VALID_VISIBILITY = {"public", "private", "password", "users_all", "users_specific"}
    if visibility is not None and visibility not in VALID_VISIBILITY:
        print(json.dumps({"status": "error", "message": f"--visibility must be one of {sorted(VALID_VISIBILITY)}"}))
        sys.exit(1)
    if visibility == "password" and not view_password:
        print(json.dumps({"status": "error", "message": "--view-password is required when --visibility password (min 8 chars)"}))
        sys.exit(1)

    fp = Path(file_path)
    if not fp.exists():
        print(json.dumps({"status": "error", "message": f"File not found: {file_path}"}))
        sys.exit(1)

    content = fp.read_bytes()
    filename = fp.name

    fields = {}
    if title:
        fields["title"] = title
    if visibility is not None:
        fields["visibility"] = visibility
    if view_password:
        fields["view_password"] = view_password
    # Resolve usernames/ids to user IDs when granting specific users.
    if visibility == "users_specific":
        user_ids = resolve_user_ids(config, users)
        if not user_ids:
            print(json.dumps({"status": "error", "message": "--users is required (comma-separated usernames or ids) for users_specific"}))
            sys.exit(1)
        fields["allowed_user_ids"] = user_ids

    files = {"file": (filename, content)}
    body, content_type = build_multipart(fields, files)

    encoded_slug = quote(slug, safe="/")
    base_url = config.get("BASE_URL", "https://html.orcacalf.site").rstrip("/")
    url = f"{base_url}/api/pages/{encoded_slug}"

    req = Request(url, method="PUT", data=body)
    req.add_header("Authorization", f"Bearer {config['API_KEY']}")
    req.add_header("Content-Type", content_type)

    try:
        resp = urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())
        result["status"] = "ok"
        result["url"] = f"{base_url}/view/{slug}"
        print(json.dumps(result))
    except HTTPError as e:
        body_resp = e.read().decode()
        try:
            detail = json.loads(body_resp).get("detail", body_resp)
        except Exception:
            detail = body_resp
        print(json.dumps({"status": "error", "message": detail}))
        sys.exit(1)
    except URLError as e:
        print(json.dumps({"status": "error", "message": str(e.reason)}))
        sys.exit(1)


def cmd_list():
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "error", "message": "Run 'upload.py check' first to configure credentials"}))
        sys.exit(1)

    result = api_request("GET", "/api/pages", config)
    if "error" in result:
        print(json.dumps({"status": "error", "message": result["error"]}))
        sys.exit(1)

    base_url = config.get("BASE_URL", "https://html.orcacalf.site").rstrip("/")
    for item in result:
        item["url"] = f"{base_url}/view/{item.get('slug') or item['id']}"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_set_slug(args):
    """Set or change slug for an existing page (by ID or old slug)."""
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "error", "message": "Run 'upload.py check' first to configure credentials"}))
        sys.exit(1)

    page_id = None
    new_slug = None
    title = None

    i = 0
    while i < len(args):
        if args[i] == "--id" and i + 1 < len(args):
            page_id = args[i + 1]
            i += 2
        elif args[i] == "--new-slug" and i + 1 < len(args):
            new_slug = args[i + 1]
            i += 2
        elif args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        else:
            i += 1

    if not page_id:
        print(json.dumps({"status": "error", "message": "--id is required (page ID or current slug)"}))
        sys.exit(1)
    if not new_slug:
        print(json.dumps({"status": "error", "message": "--new-slug is required"}))
        sys.exit(1)

    base_url = config.get("BASE_URL", "https://html.orcacalf.site").rstrip("/")
    download_url = f"{base_url}/view/{quote(page_id, safe='/')}"
    try:
        req = Request(download_url)
        resp = urlopen(req, timeout=30)
        content = resp.read()
    except (HTTPError, URLError) as e:
        print(json.dumps({"status": "error", "message": f"Failed to download page: {e}"}))
        sys.exit(1)

    fields = {}
    if title:
        fields["title"] = title

    files = {"file": ("page.html", content)}
    body, content_type = build_multipart(fields, files)

    encoded_slug = quote(new_slug, safe="/")
    url = f"{base_url}/api/pages/{encoded_slug}"
    req = Request(url, method="PUT", data=body)
    req.add_header("Authorization", f"Bearer {config['API_KEY']}")
    req.add_header("Content-Type", content_type)

    try:
        resp = urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())
        result["status"] = "ok"
        result["url"] = f"{base_url}/view/{new_slug}"
    except HTTPError as e:
        body_resp = e.read().decode()
        try:
            detail = json.loads(body_resp).get("detail", body_resp)
        except Exception:
            detail = body_resp
        print(json.dumps({"status": "error", "message": detail}))
        sys.exit(1)
    except URLError as e:
        print(json.dumps({"status": "error", "message": str(e.reason)}))
        sys.exit(1)

    # Delete old page (by ID) to avoid duplicates
    old_encoded = quote(page_id, safe="/")
    del_result = api_request("DELETE", f"/api/pages/{old_encoded}", config)
    if "error" not in del_result:
        result["old_page_deleted"] = True
    else:
        result["old_page_deleted"] = False
        result["delete_note"] = "Could not delete old page; you may have a duplicate."

    print(json.dumps(result, ensure_ascii=False))


def cmd_delete(args):
    config = load_config()
    if not config or "API_KEY" not in config:
        print(json.dumps({"status": "error", "message": "Run 'upload.py check' first to configure credentials"}))
        sys.exit(1)

    slug = None
    i = 0
    while i < len(args):
        if args[i] == "--slug" and i + 1 < len(args):
            slug = args[i + 1]
            i += 2
        else:
            i += 1

    if not slug:
        print(json.dumps({"status": "error", "message": "--slug is required"}))
        sys.exit(1)

    encoded_slug = quote(slug, safe="/")
    result = api_request("DELETE", f"/api/pages/{encoded_slug}", config)
    if "error" in result:
        print(json.dumps({"status": "error", "message": result["error"]}))
        sys.exit(1)
    result["status"] = "ok"
    print(json.dumps(result))


def main():
    if len(sys.argv) < 2:
        print("Usage: upload.py <check|put|list|delete|set-slug|list-users> [options]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "check":
        cmd_check()
    elif cmd == "put":
        cmd_put(sys.argv[2:])
    elif cmd == "list":
        cmd_list()
    elif cmd == "delete":
        cmd_delete(sys.argv[2:])
    elif cmd == "set-slug":
        cmd_set_slug(sys.argv[2:])
    elif cmd == "list-users":
        cmd_list_users(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
