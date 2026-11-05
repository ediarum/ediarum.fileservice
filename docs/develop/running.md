# Running locally

Uses the `ediarum-platform` dev server (http://localhost:5173) as auth provider
and a static file as platform project registry. `data/` is git-ignored.

```
# platform project registry (separate terminal)
mkdir -p data/platform && cat > data/platform/projects <<'EOF'
[{"slug": "schleiermacher", "label": "Schleiermacher in Berlin",
  "repo_url": "https://example.org/schleiermacher.git", "branch": "main",
  "sparse_path": "data", "index_source_of_truth": "xml"},
 {"slug": "umfeldbriefe", "label": "Umfeldbriefe",
  "repo_url": "https://example.org/umfeldbriefe.git", "branch": "main",
  "sparse_path": "data", "index_source_of_truth": "app"}]
EOF
python3 -m http.server 8901 -d data/platform

# platform: add to ../ediarum-platform/.env.local, then `npm run dev`
#   EDIARUM_DEV_BACKENDS=fileservice=http://localhost:8002

# fileservice
export XML_MOUNT_ROOT=$PWD/data/xml \
       DATABASE_URL=sqlite:///$PWD/data/fileservice.db \
       PLATFORM_PROJECTS_URL=http://127.0.0.1:8901/projects \
       AUTH_VERIFY_URL=http://localhost:5173/api/auth/verify
KEY=$(uv run ediarum-fileservice-create-key schleiermacher)
uv run ediarum-fileservice                      # http://localhost:8002/docs (PORT=…)

# as webdav
curl -H "Authorization: Bearer $KEY" -F event=created -F project=schleiermacher \
     -F commit=abc123 -F path=data/a.xml -F file=@some.xml localhost:8002/files
```

As admin: log in at http://localhost:5173 as `admin@example.org` / `password`
and open http://localhost:5173/api/fileservice/projects/schleiermacher/status.
`ada@example.org` gets 403, no session 401.

The same in Docker, with the platform project registry as a second container
(its file above must exist; port 8002, data in `./data`):

```
docker compose -p ediarum-fileservice-dev -f docker-compose.dev.yml up -d --build
docker compose -p ediarum-fileservice-dev -f docker-compose.dev.yml exec fileservice \
  ediarum-fileservice-create-key --admin
```
