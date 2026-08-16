---
name: microsite-container
description: Deploy and update multi-file static websites, SPAs, large HTML applications, datasets, audio, and video assets through Microsite Container. Use when Codex needs to publish a site directory, host a static microsite, incrementally sync content-addressed assets, activate an immutable deployment, inspect hosted sites, or mentions microsite-container, large web pages, static-site hosting, audio/video hosting, or manifest-based deployment.
---

# Microsite Container

Use the bundled `scripts/deploy.py` CLI. It hashes local files, creates a manifest,
uploads only missing blobs, finalizes the immutable deployment, and atomically
activates it.

## Initialize

Before any deployment, run:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py check
```

Credentials live at `~/.config/microsite-container/credentials.env`:

```text
API_KEY=...
BASE_URL=https://microsites.example.com
```

If configuration is missing, ask the user for the API key and service URL. Never
print the key. If the check returns an API error, report it before attempting a
deployment.

## Deploy a directory

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py deploy \
  --slug vietnamese-learning \
  --title "Vietnamese Learning" \
  --dir ./dist \
  --entrypoint index.html
```

The site slug uses lowercase letters, digits, dots, dashes, or underscores. The
directory must contain the entrypoint. The CLI excludes `.git`, `__pycache__`, and
`.DS_Store`, rejects symlinks, streams large uploads, and prints the active URL as
JSON on success.

Deploy the built/static output directory, not a source tree that still requires a
development server. Preserve relative asset paths. For a single large HTML file,
place it in a directory as `index.html` and deploy that directory.

SPA history fallback is enabled by default. Use `--no-spa-fallback` for sites that
should return 404 for unknown extensionless paths.

## Inspect without publishing

Generate and validate the local manifest:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py manifest \
  --dir ./dist --entrypoint index.html
```

List hosted sites:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py list
```

## Deployment guarantees

- Treat every deployment as immutable after finalization.
- Upload only hashes returned in `missing_blobs`.
- Do not activate until finalization succeeds.
- Re-running `deploy` is safe; unchanged blobs are reused by SHA-256.
- Keep old deployment URLs valid for rollback and reproducibility.
- Do not use ZIP as the primary protocol. ZIP import is only an optional convenience
  when a directory-based manifest deployment is unavailable.
