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


def load_config() -> tuple[str, str]:
    if not CONFIG_PATH.is_file():
        raise CliError(f"missing config: {CONFIG_PATH}")
    values: dict[str, str] = {}
    for raw_line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    base_url = values.get("BASE_URL", "").rstrip("/")
    api_key = values.get("API_KEY", "")
    if not base_url or not api_key:
        raise CliError(f"BASE_URL and API_KEY are required in {CONFIG_PATH}")
    return base_url, api_key


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
