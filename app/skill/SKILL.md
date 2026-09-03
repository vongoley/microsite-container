---
name: microsite-container
description: Deploy and update multi-file static websites, SPAs, large HTML applications, datasets, audio, and video assets through Microsite Container, and connect deployed sites to mutable Runtime Data or server-scheduled data producers. Use when Codex needs to publish a site directory, host a static microsite, incrementally sync content-addressed assets, activate an immutable deployment, inspect hosted sites, configure Runtime Data, or arrange recurring server-side data refreshes without redeploying the frontend.
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

## Runtime data

For data edited by the deployed page, use the platform Runtime Data capability instead
of embedding deployment API tokens or rewriting HTML. Add `microsite.json` to the site
directory, declare site-scoped documents under `runtimeData.documents`, and include any
referenced JSON Schema and seed JSON files. The deploy command includes these files in
the manifest automatically; finalize validates them and activation registers them.

Browser code loads `/_microsite/sdk/v1.js`, calls
`MicrositeData.document("document-key").get()`, then saves with the document object's
`save(value)` method. Writes use the browser login session and optimistic revisions.
Never put the deployment API key in site source code. Runtime seeds initialize only
missing documents; redeployment does not overwrite saved runtime data.

Runtime Data is a real HTTP API, not an embedded static JSON file. Public documents can
be read without credentials. Browser writes use the login session; machine writes use a
separate token restricted to exactly one site and one document.

## Server-scheduled data

For a dashboard or report whose data must refresh on a schedule, deploy the frontend
once, declare a Runtime Data document, and have a server cron job or isolated worker
write new JSON to that document. Do not rebuild or redeploy the site just to update data.

After the deployment containing `microsite.json` is active, create the scoped writer
token with the normal deployment credential:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token create \
  --slug investment-report \
  --document latest-analysis \
  --name daily-market-job \
  --save-token /secure/path/investment-report-runtime.env
```

`--save-token` creates a new mode-0600 env file and keeps the secret out of stdout. The
token is stored hashed by the service and cannot deploy code or write any other site or
document. Never commit, embed, or paste it into frontend source.

The scheduled producer should atomically generate a complete JSON file, then publish it:

```bash
set -a
. /secure/path/investment-report-runtime.env
set +a
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime put \
  --slug investment-report \
  --document latest-analysis \
  --file /srv/investment-report/latest.json
```

The CLI performs a fresh GET and uses its revision as `If-Match`; concurrent changes fail
with a conflict instead of being silently overwritten. To read or verify data:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime get \
  --slug investment-report --document latest-analysis
```

Manage scoped credentials with:

```bash
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token list \
  --slug investment-report
python3 ~/.codex/skills/microsite-container/scripts/deploy.py runtime-token revoke \
  --slug investment-report --token-id TOKEN_ID
```

Use the host's cron, systemd timer, CI runner, cloud function, or a dedicated worker for
the calculation. Keep market-data credentials in that worker's secret store. The web
container does not execute Python or Node files included in an immutable deployment and
does not provide an arbitrary-code scheduler. This separation is intentional.

For a recurring job, verify all of the following before declaring it complete:

- the source API returned data for the expected market date;
- the computation and JSON Schema validation succeeded;
- `runtime put` returned the new revision;
- logs and non-zero exit status are retained by the scheduler;
- overlapping runs are prevented and failures are retried or alerted.

## Capability boundaries

- Supported: immutable static deployments, incremental blob upload, atomic activation,
  rollback, Runtime Data storage, public/owner reads, browser owner writes, scoped
  machine writes, Schema validation, optimistic revisions, and retained history.
- External by design: scheduled calculation execution, arbitrary Python/Node runtime,
  job isolation, third-party API credentials, retries, alerts, and job logs.
- Do not claim the Runtime Data API is absent. It exists; choose browser-session writes
  for interactive editing and scoped writer-token writes for automated producers.

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
