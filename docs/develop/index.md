# Develop

```
uv sync
uv run pytest
```

`ediarum.sdk` comes from PyPI (`ediarum-sdk` in `pyproject.toml`).

- New SDK release: `uv lock --upgrade-package ediarum-sdk` (widen the version
  range in `pyproject.toml` for a new minor version).
- Local SDK changes: `uv pip install -e ../ediarum.sdk` (reverted by the next `uv sync`).

## Status

Implemented and tested: git sync (sparse partial shallow clone/fetch),
`POST /files` with path-traversal protection, the first fill, the backup
check and its scheduler (`check.run_scheduled`, started by the app; a failing
project doesn't stop the others), the status and admin routes, API keys,
platform admin auth.

Not implemented:

- No admin UI and no HTTP API for keys.
- Run a single process: the scheduler runs inside the app, so several
  workers would each check.

Known bugs:

- `POST /files` is `async` but blocks (platform request, lock with 30 s
  timeout, file I/O). A lock timeout returns 500 instead of 409/503.
