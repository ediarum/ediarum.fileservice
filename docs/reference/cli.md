# Command line

In Docker, prefix with `docker exec fileservice` (or `docker compose exec fileservice`).

| Command | Purpose |
|---|---|
| `ediarum-fileservice` | run the app on `$PORT` (default 8002; the Docker image sets 8000) |
| `ediarum-fileservice-create-key <slug> [--name N] [--created-by NAME]` | create a key for one project; prints it once, only its hash is stored |
| `ediarum-fileservice-create-key --admin [--name N] [--created-by NAME]` | create an admin key, valid for every project |
