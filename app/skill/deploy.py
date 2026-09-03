#!/usr/bin/env python3
"""Dependency-free CLI for manifest-based Microsite Container deployments."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import mimetypes
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


CONFIG_PATH = Path.home() / ".config" / "microsite-container" / "credentials.env"
EXCLUDED_DIRS = {".git", "__pycache__"}
EXCLUDED_FILES = {".DS_Store", ".microsite-origin.json"}
SOURCE_EXCLUDED_DIRS = EXCLUDED_DIRS | {
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
}
SOURCE_EXCLUDED_FILES = EXCLUDED_FILES | {".microsite-origin.json", "credentials.env"}
SOURCE_SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
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


def download_api_file(
    base_url: str,
    api_key: str,
    path: str,
    destination: Path,
) -> dict[str, str]:
    request = Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/zip"},
        method="GET",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=300) as response, destination.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=CHUNK_SIZE)
            return {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload).get("detail", payload)
        except json.JSONDecodeError:
            detail = payload
        raise CliError(f"API {exc.code}: {detail}") from exc
    except (OSError, URLError) as exc:
        destination.unlink(missing_ok=True)
        reason = exc.reason if isinstance(exc, URLError) else exc
        raise CliError(f"cannot download from {base_url}: {reason}") from exc


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


def _source_file_is_excluded(name: str) -> bool:
    if name in SOURCE_EXCLUDED_FILES:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return Path(name).suffix.lower() in SOURCE_SECRET_SUFFIXES


def build_source_archive(root: Path) -> tuple[Path, dict]:
    """Build a deterministic private ZIP containing development source only."""
    root = root.resolve()
    if not root.is_dir():
        raise CliError(f"source directory does not exist: {root}")
    files: list[tuple[Path, str]] = []
    for current, dir_names, file_names in os.walk(root):
        current_path = Path(current)
        included_dirs = []
        for name in sorted(dir_names):
            directory = current_path / name
            if directory.is_symlink():
                raise CliError(f"symlinks are not supported in source snapshots: {directory}")
            if name not in SOURCE_EXCLUDED_DIRS:
                included_dirs.append(name)
        dir_names[:] = included_dirs
        for name in sorted(file_names):
            if _source_file_is_excluded(name):
                continue
            source_path = current_path / name
            if source_path.is_symlink():
                raise CliError(f"symlinks are not supported in source snapshots: {source_path}")
            if source_path.is_file():
                files.append((source_path, source_path.relative_to(root).as_posix()))
    if not files:
        raise CliError(f"source directory contains no files after exclusions: {root}")

    descriptor, raw_path = tempfile.mkstemp(prefix="microsite-source-", suffix=".zip")
    os.close(descriptor)
    archive_path = Path(raw_path)
    try:
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            for source_path, relative in sorted(files, key=lambda item: item[1]):
                mode = 0o755 if source_path.stat().st_mode & 0o111 else 0o644
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | mode) << 16
                with source_path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=CHUNK_SIZE)
        digest, size = hash_file(archive_path)
        return archive_path, {"sha256": digest, "size": size, "format": "zip"}
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def _safe_extract_source(archive_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            for info in entries:
                value = info.filename
                normalized_value = value[:-1] if info.is_dir() and value.endswith("/") else value
                path = Path(normalized_value)
                pure_parts = normalized_value.split("/")
                mode = info.external_attr >> 16
                if (
                    not normalized_value
                    or "\\" in value
                    or path.is_absolute()
                    or any(part in ("", ".", "..") for part in pure_parts)
                    or stat.S_ISLNK(mode)
                ):
                    raise CliError(f"source archive contains an unsafe path: {value!r}")
            for info in entries:
                target = destination.joinpath(*Path(info.filename).parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=CHUNK_SIZE)
                mode = info.external_attr >> 16
                if mode & 0o111:
                    target.chmod(target.stat().st_mode | 0o111)
    except (OSError, zipfile.BadZipFile) as exc:
        raise CliError(f"cannot extract source archive: {exc}") from exc


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
    publish_dir = Path(args.publish_dir)
    manifest, blob_sources = build_manifest(publish_dir, args.entrypoint, args.spa_fallback)
    source_archive, source_descriptor = build_source_archive(Path(args.source_dir))
    manifest["source"] = source_descriptor
    try:
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
            relative = source.relative_to(publish_dir.resolve())
            print(f"uploading asset {index}/{len(missing)} {relative}", file=sys.stderr)
            upload_blob(base_url, api_key, args.slug, deployment["id"], digest, source, size)
        if deployment.get("missing_source_blob"):
            print(
                f"uploading private source snapshot ({source_descriptor['size']} bytes)",
                file=sys.stderr,
            )
            upload_blob(
                base_url,
                api_key,
                args.slug,
                deployment["id"],
                source_descriptor["sha256"],
                source_archive,
                source_descriptor["size"],
            )
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
    finally:
        source_archive.unlink(missing_ok=True)


def command_pull(args):
    base_url, api_key = load_config()
    slug = args.slug.strip().lower()
    destination = Path(args.out).expanduser().resolve()
    if destination.exists():
        if not destination.is_dir():
            raise CliError(f"output path is not a directory: {destination}")
        if any(destination.iterdir()):
            raise CliError(f"output directory is not empty: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f"{slug}-source-", suffix=".zip")
    os.close(descriptor)
    archive_path = Path(raw_path)
    extract_path = Path(tempfile.mkdtemp(prefix=f".{destination.name}-pull-", dir=destination.parent))
    try:
        headers = download_api_file(
            base_url,
            api_key,
            f"/api/sites/{quote(slug, safe='')}/source",
            archive_path,
        )
        expected_digest = headers.get("x-microsite-source-sha256")
        if expected_digest and hash_file(archive_path)[0] != expected_digest:
            raise CliError("downloaded source archive does not match its SHA-256")
        _safe_extract_source(archive_path, extract_path)
        origin = {
            "formatVersion": 1,
            "slug": slug,
            "baseUrl": base_url,
            "deploymentId": headers.get("x-microsite-deployment-id"),
            "sourceMode": headers.get("x-microsite-source-mode", "source"),
            "sourceSha256": expected_digest,
        }
        (extract_path / ".microsite-origin.json").write_text(
            json.dumps(origin, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            destination.rmdir()
        os.replace(extract_path, destination)
        return {
            "status": "ok",
            "slug": slug,
            "path": str(destination),
            "deployment_id": origin["deploymentId"],
            "source_mode": origin["sourceMode"],
        }
    except Exception:
        shutil.rmtree(extract_path, ignore_errors=True)
        raise
    finally:
        archive_path.unlink(missing_ok=True)


def runtime_document_path(slug: str, document: str) -> str:
    return (
        f"/api/runtime/sites/{quote(slug.strip().lower(), safe='')}/documents/"
        f"{quote(document.strip(), safe='')}"
    )


def command_runtime_get(args):
    base_url, token = load_runtime_config(args.token)
    result = runtime_json(
        base_url,
        token,
        "GET",
        runtime_document_path(args.slug, args.document),
    )
    output = getattr(args, "out", None)
    if not output:
        return result
    output_path = Path(output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.get("value"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "slug": args.slug.strip().lower(),
        "document": args.document,
        "revision": result.get("revision"),
        "output": str(output_path),
    }


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
    deploy.add_argument(
        "--source-dir",
        required=True,
        help="Private development source directory (required for every deployment)",
    )
    deploy.add_argument(
        "--publish-dir",
        required=True,
        help="Built static-site directory exposed to browsers",
    )
    deploy.add_argument("--entrypoint", default="index.html")
    deploy.add_argument("--no-spa-fallback", dest="spa_fallback", action="store_false")
    deploy.set_defaults(spa_fallback=True)
    deploy.set_defaults(handler=command_deploy)
    pull = subparsers.add_parser("pull", help="Restore private source by site slug")
    pull.add_argument("--slug", required=True)
    pull.add_argument("--out", required=True, help="New or empty output directory")
    pull.set_defaults(handler=command_pull)

    runtime = subparsers.add_parser("runtime", help="Read or update Runtime Data")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_get = runtime_commands.add_parser("get")
    runtime_get.add_argument("--slug", required=True)
    runtime_get.add_argument("--document", required=True)
    runtime_get.add_argument("--token", help="Document-scoped writer token")
    runtime_get.add_argument("--out", help="Write only the current JSON value to this file")
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
