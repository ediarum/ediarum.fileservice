# Settings

| Variable | Purpose |
|---|---|
| `XML_MOUNT_ROOT` | Shared volume, e.g. `/data/xml` (required) |
| `PORT` | Default 8002; the Docker image sets 8000 |
| `DATABASE_URL` | Default `sqlite:////data/fileservice.db` |
| `PLATFORM_PROJECTS_URL` | The platform project registry, `…/api/auth/service/projects` (required) |
| `PLATFORM_SERVICE_TOKEN` | Service token for the platform project registry |
| `AUTH_VERIFY_URL` | Platform auth-verify endpoint, for the admin routes |
| `AUTH_DEV_USER` | Treat every admin-route request as this admin user. Development only; mutually exclusive with `AUTH_VERIFY_URL` |
| `CHECK_TIMES` | Default `03:00,15:00`. When the backup check compares every project with git (`HH:MM`, comma-separated); empty = no scheduled checks |
| `CHECK_TIMEZONE` | Default `Europe/Berlin`. Time zone of `CHECK_TIMES` |

`RECONCILE_INTERVAL_SECONDS` and `RECONCILE_GRACE_PERIOD_SECONDS` are no
longer used; a leftover value is ignored with a warning.
