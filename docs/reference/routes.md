# HTTP routes

| Route | Caller | Auth |
|---|---|---|
| `POST /files` | `ediarum.webdav`, on every save | Per-project API key (`Authorization: Bearer`) |
| `GET /projects/{slug}/status` | Platform admins; the index and id watchers | Platform session with role `admin` (via `verify`), or an API key for the project |
| `GET /projects/status` | Admins: every project in the platform project registry with its last backup check | Platform admin, or an admin API key |
| `POST /projects/{slug}/check` | Admins: run the backup check now (debugging, confirming a fix) | Platform admin, or an admin API key |
| `POST /projects/{slug}/reload` | Admins: reset the project to git's version (`?force=true` while git is behind) | Platform admin, or an admin API key |
| `GET /health` | – | none |

`POST /files` takes the form fields `event` (`created`, `modified`, `moved`,
`deleted`), `project`, `commit`, `path`, `previous_path` and `file`. It writes
the change to the volume and records `commit` as claimed.
