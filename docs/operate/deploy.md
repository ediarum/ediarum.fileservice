# Deploy

The build context is this directory:

```
docker build -t ediarum-fileservice .
```

Run:

```
docker run -d --name fileservice -p 8000:8000 \
  -v xml:/data/xml -v fileservice_data:/data \
  -v ./git_token:/run/secrets/git_token:ro \
  -e XML_MOUNT_ROOT=/data/xml \
  -e PLATFORM_PROJECTS_URL=https://platform.ediarum.org/api/auth/service/projects \
  -e PLATFORM_SERVICE_TOKEN=<token> \
  -e AUTH_VERIFY_URL=https://platform.ediarum.org/api/auth/verify \
  ediarum-fileservice
docker exec fileservice ediarum-fileservice-create-key <slug>
```

| Path | Content |
|---|---|
| `/data/xml` | The shared volume, one checkout per project; `index` and `id` mount it read-only |
| `/data` | `fileservice.db` (claims, API keys) |
| `/run/secrets/git_token` | Token with `read_repository` for the project repositories (`docker/git-credential-token`). Username `oauth2` unless `GIT_TOKEN_USER` is set (a GitLab deploy token needs its own) |

The full stack is defined in `ediarum.deploy`.
