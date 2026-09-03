#!/usr/bin/env python3
"""Dependency-free CLI for manifest-based Microsite Container deployments."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


CONFIG_PATH = Path.home() / ".config" / "microsite-container" / "credentials.env"
EXCLUDED_DIRS = {".git", "__pycache__"}
EXCLUDED_FILES = {".DS_Store"}
CHUNK_SIZE = 1024 * 1024


class CliError(RuntimeError):
    pass


def load_config_values() -> dict[str, str]:
    if not CONFIG_PATH.is_file():
        raise CliError(f"missing config: {CONFIG_PATH}")
    values: dict[str, str] = {}
    for raw_line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config() -> tuple[str, str]:
    values = load_config_values()
    base_url = values.get("BASE_URL", "").rstrip("/")
    api_key = values.get("API_KEY", "")
    if not base_url or not api_key:
        raise CliError(f"BASE_URL and API_KEY are required in {CONFIG_PATH}")
    return base_url, api_key


def load_runtime_config(explicit_token: str | None = None) -> tuple[str, str | None]:
    values = load_config_values()
    base_url = values.get("BASE_URL", "").rstrip("/")
    if not base_url:
        raise CliError(f"BASE_URL is required in {CONFIG_PATH}")
    token = explicit_token or os.environ.get("MICROSITE_RUNTIME_TOKEN") or values.get("RUNTIME_TOKEN")
    return base_url, token


def api_json(base_url: str, api_key: str, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload).get("detail", payload)
        except json.JSONDecodeError:
            detail = payload
        raise CliError(f"API {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CliError(f"cannot connect to {base_url}: {exc.reason}") from exc


def runtime_json(
    base_url: str,
    token: str | None,
    method: str,
    path: str,
    body=None,
    *,
    revision: int | None = None,
):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    if revision is not None:
        headers["If-Match"] = f'"rev-{revision}"'
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload).get("detail", payload)
        except json.JSONDecodeError:
            detail = payload
        raise CliError(f"Runtime API {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CliError(f"cannot connect to {base_url}: {exc.reason}") from exc


def upload_blob(
    base_url: str,
    api_key: str,
    site_slug: str,
    deployment_id: str,
    digest: str,
    file_path: Path,
    size: int,
):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CliError("BASE_URL must be an http or https URL")
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = connection_class(parsed.hostname, port, timeout=300)
    base_path = parsed.path.rstrip("/")
    endpoint = (
        f"{base_path}/api/sites/{quote(site_slug, safe='')}/deployments/"
        f"{quote(deployment_id, safe='')}/blobs/{digest}"
    )
    try:
        connection.putrequest("PUT", endpoint)
        connection.putheader("Authorization", f"Bearer {api_key}")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(size))
        connection.endheaders()
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                connection.send(chunk)
        response = connection.getresponse()
        payload = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            try:
                detail = json.loads(payload).get("detail", payload)
            except json.JSONDecodeError:
                detail = payload
            raise CliError(f"blob upload {response.status}: {detail}")
        return json.loads(payload) if payload else None
    except OSError as exc:
        raise CliError(f"blob upload failed for {file_path}: {exc}") from exc
    finally:
        connection.close()


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def build_manifest(root: Path, entrypoint: str, spa_fallback: bool) -> tuple[dict, dict[str, Path]]:
    root = root.resolve()
    if not root.is_dir():
        raise CliError(f"site directory does not exist: {root}")
    files: list[dict] = []
    blob_sources: dict[str, Path] = {}
    for current, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(name for name in dir_names if name not in EXCLUDED_DIRS)
        current_path = Path(current)
        for name in sorted(file_names):
            if name in EXCLUDED_FILES:
                continue
            path = current_path / name
            if path.is_symlink():
                raise CliError(f"symlinks are not supported: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            digest, size = hash_file(path)
            files.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "size": size,
                    "content_type": mimetypes.guess_type(relative)[0],
                }
            )
            blob_sources.setdefault(digest, path)
    paths = {item["path"] for item in files}
    if entrypoint not in paths:
        raise CliError(f"entrypoint is missing from site directory: {entrypoint}")
    return {
        "entrypoint": entrypoint,
        "spa_fallback": spa_fallback,
        "files": files,
    }, blob_sources


def ensure_site(base_url: str, api_key: str, slug: str, title: str) -> dict:
    sites = api_json(base_url, api_key, "GET", "/api/sites")
    for site in sites:
        if site["slug"] == slug:
            return site
    return api_json(base_url, api_key, "POST", "/api/sites", {"slug": slug, "title": title})


def command_check(_args) -> dict:
    try:
        base_url, api_key = load_config()
    except CliError as exc:
        return {"status": "missing_config", "error": str(exc)}
    try:
        sites = api_json(base_url, api_key, "GET", "/api/sites")
        return {"status": "ok", "base_url": base_url, "site_count": len(sites)}
    except CliError as exc:
        return {"status": "api_error", "error": str(exc)}


def command_list(_args):
    base_url, api_key = load_config()
    return api_json(base_url, api_key, "GET", "/api/sites")


def command_manifest(args):
    manifest, _ = build_manifest(Path(args.dir), args.entrypoint, args.spa_fallback)
    unique_bytes = sum({item["sha256"]: item["size"] for item in manifest["files"]}.values())
    return {
        "entrypoint": manifest["entrypoint"],
        "spa_fallback": manifest["spa_fallback"],
        "file_count": len(manifest["files"]),
        "total_bytes": sum(item["size"] for item in manifest["files"]),
        "unique_bytes": unique_bytes,
        "files": manifest["files"],
    }


def command_deploy(args):
    base_url, api_key = load_config()
    args.slug = args.slug.strip().lower()
    manifest, blob_sources = build_manifest(Path(args.dir), args.entrypoint, args.spa_fallback)
    ensure_site(base_url, api_key, args.slug, args.title or args.slug)
    deployment = api_json(
        base_url,
        api_key,
        "POST",
        f"/api/sites/{quote(args.slug, safe='')}/deployments",
        manifest,
    )
    missing = deployment["missing_blobs"]
    for index, digest in enumerate(missing, start=1):
        source = blob_sources[digest]
        size = source.stat().st_size
        print(f"uploading {index}/{len(missing)} {source.relative_to(Path(args.dir).resolve())}", file=sys.stderr)
        upload_blob(base_url, api_key, args.slug, deployment["id"], digest, source, size)
    api_json(
        base_url,
        api_key,
        "POST",
        f"/api/sites/{quote(args.slug, safe='')}/deployments/{quote(deployment['id'], safe='')}/finalize",
    )
    return api_json(
        base_url,
        api_key,
        "POST",
        f"/api/sites/{quote(args.slug, safe='')}/deployments/{quote(deployment['id'], safe='')}/activate",
    )


def runtime_document_path(slug: str, document: str) -> str:
    return (
        f"/api/runtime/sites/{quote(slug.strip().lower(), safe='')}/documents/"
        f"{quote(document.strip(), safe='')}"
    )


def command_runtime_get(args):
    base_url, token = load_runtime_config(args.token)
    return runtime_json(
        base_url,
        token,
        "GET",
        runtime_document_path(args.slug, args.document),
    )


def _read_json_input(path_value: str):
    try:
        if path_value == "-":
            return json.load(sys.stdin)
        with Path(path_value).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read JSON input {path_value!r}: {exc}") from exc


def command_runtime_put(args):
    base_url, token = load_runtime_config(args.token)
    if not token:
        raise CliError(
            "runtime writes require --token, MICROSITE_RUNTIME_TOKEN, or RUNTIME_TOKEN in credentials.env"
        )
    path = runtime_document_path(args.slug, args.document)
    revision = args.revision
    if revision is None:
        current = runtime_json(base_url, token, "GET", path)
        revision = current["revision"]
    value = _read_json_input(args.file)
    return runtime_json(
        base_url,
        token,
        "PUT",
        path,
        {"value": value},
        revision=revision,
    )


def command_runtime_token_create(args):
    base_url, api_key = load_config()
    path = runtime_document_path(args.slug, args.document) + "/writer-tokens"
    result = api_json(base_url, api_key, "POST", path, {"name": args.name})
    save_token = getattr(args, "save_token", None)
    if not save_token:
        return result
    secret_path = Path(save_token).expanduser()
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"MICROSITE_RUNTIME_TOKEN={result['token']}\n")
    except OSError as exc:
        try:
            slug = quote(args.slug.strip().lower(), safe="")
            api_json(
                base_url,
                api_key,
                "DELETE",
                f"/api/runtime/sites/{slug}/writer-tokens/{quote(result['id'], safe='')}",
            )
        except CliError:
            pass
        raise CliError(f"cannot save runtime token to {secret_path}: {exc}") from exc
    safe_result = {key: value for key, value in result.items() if key != "token"}
    safe_result["token_file"] = str(secret_path)
    return safe_result


def command_runtime_token_list(args):
    base_url, api_key = load_config()
    slug = quote(args.slug.strip().lower(), safe="")
    return api_json(base_url, api_key, "GET", f"/api/runtime/sites/{slug}/writer-tokens")


def command_runtime_token_revoke(args):
    base_url, api_key = load_config()
    slug = quote(args.slug.strip().lower(), safe="")
    token_id = quote(args.token_id.strip(), safe="")
    return api_json(
        base_url,
        api_key,
        "DELETE",
        f"/api/runtime/sites/{slug}/writer-tokens/{token_id}",
    )


def add_directory_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dir", required=True, help="Built static-site directory")
    parser.add_argument("--entrypoint", default="index.html")
    parser.add_argument("--no-spa-fallback", dest="spa_fallback", action="store_false")
    parser.set_defaults(spa_fallback=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy immutable multi-file static sites")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check").set_defaults(handler=command_check)
    subparsers.add_parser("list").set_defaults(handler=command_list)
    manifest = subparsers.add_parser("manifest")
    add_directory_args(manifest)
    manifest.set_defaults(handler=command_manifest)
    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--slug", required=True)
    deploy.add_argument("--title")
    add_directory_args(deploy)
    deploy.set_defaults(handler=command_deploy)

    runtime = subparsers.add_parser("runtime", help="Read or update Runtime Data")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_get = runtime_commands.add_parser("get")
    runtime_get.add_argument("--slug", required=True)
    runtime_get.add_argument("--document", required=True)
    runtime_get.add_argument("--token", help="Document-scoped writer token")
    runtime_get.set_defaults(handler=command_runtime_get)
    runtime_put = runtime_commands.add_parser("put")
    runtime_put.add_argument("--slug", required=True)
    runtime_put.add_argument("--document", required=True)
    runtime_put.add_argument("--file", required=True, help="JSON file, or - for stdin")
    runtime_put.add_argument("--revision", type=int, help="Expected revision; defaults to a fresh GET")
    runtime_put.add_argument("--token", help="Document-scoped writer token")
    runtime_put.set_defaults(handler=command_runtime_put)

    writer_tokens = subparsers.add_parser(
        "runtime-token", help="Manage document-scoped machine writer tokens"
    )
    writer_token_commands = writer_tokens.add_subparsers(
        dest="runtime_token_command", required=True
    )
    writer_token_create = writer_token_commands.add_parser("create")
    writer_token_create.add_argument("--slug", required=True)
    writer_token_create.add_argument("--document", required=True)
    writer_token_create.add_argument("--name", default="scheduled-writer")
    writer_token_create.add_argument(
        "--save-token",
        help="Write MICROSITE_RUNTIME_TOKEN to a new mode-0600 env file and hide it from stdout",
    )
    writer_token_create.set_defaults(handler=command_runtime_token_create)
    writer_token_list = writer_token_commands.add_parser("list")
    writer_token_list.add_argument("--slug", required=True)
    writer_token_list.set_defaults(handler=command_runtime_token_list)
    writer_token_revoke = writer_token_commands.add_parser("revoke")
    writer_token_revoke.add_argument("--slug", required=True)
    writer_token_revoke.add_argument("--token-id", required=True)
    writer_token_revoke.set_defaults(handler=command_runtime_token_revoke)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CliError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
