# CI and release

## CI

`.gitlab-ci.yml` runs on every push, merge request and tag:

| Stage | Job | What |
|---|---|---|
| test | `test` | `pytest` |
| build | `docs-build` | the documentation site (mkdocs, strict) |
| build | `image-build` | tags `vX.Y.Z` only, on the `docker-build` runner: builds the image, pushes `$CI_REGISTRY_IMAGE:X.Y.Z` and `:latest` |
| deploy | `pages` | publishes the documentation site, from `main` |

## Release

A release is a tag `vX.Y.Z` that matches `version` in `pyproject.toml`; CI
builds and pushes the image.

1. On `main`, with a green pipeline: `uv version --bump patch` (or `minor`,
   `major`). Commit `pyproject.toml` and `uv.lock`.
2. `git tag -a v$(uv version --short) -m v$(uv version --short) && git push --follow-tags`
   (`--follow-tags` pushes annotated tags only).

   `scripts/release.sh patch|minor|major` does steps 1 and 2: it refuses
   unless on `main` with a clean tree, level with `origin/main` and the tag
   free, and asks before committing and pushing. It also does step 5.
3. The tag pipeline runs the tests, then `image-build`: it refuses a tag that
   differs from the version, builds the image and pushes
   `$CI_REGISTRY_IMAGE:X.Y.Z` and `:latest`.
4. Deploy: `FILESERVICE_VERSION=X.Y.Z` in ediarum.deploy's `.env`, then
   `docker compose pull && docker compose up -d`.
5. Publish to GitHub (`github.com/ediarum/ediarum.fileservice`):
   `scripts/publish.sh X.Y.Z`. Then create the GitHub release from the
   tag by hand.

Tags `v*` are protected (Settings → Repository → Protected tags): only
maintainers create releases, and the `docker-build` runner only runs for
protected refs.

Branch pipelines build no image, so build it locally after changing the
Dockerfile or dependencies: `docker build -t ediarum-fileservice:test .`

## The GitHub mirror

The public repository has no internal history. Each release is one snapshot
commit, "Release X.Y.Z" by TELOTA, on top of the previous one, tagged
`vX.Y.Z` there too. Its files are those of the internal tag `vX.Y.Z`, minus
`EXCLUDE` in `scripts/publish.sh` (`.gitlab-ci.yml`). Nothing is merged back.

`scripts/publish.sh` refuses a snapshot that contains internal hosts, repository
paths, home directories or token-like strings (`FORBIDDEN`, minus `ALLOWED`).
Fix the hit on `main`, or add it to `ALLOWED` if it is meant to be public.
`scripts/release.sh` runs this check (`publish.sh --check`) before it changes
anything, and publishes right after the GitLab push. If only the GitHub push
fails, rerun `scripts/publish.sh X.Y.Z`. `NO_GITHUB=1 scripts/release.sh …`
releases on GitLab only.

Set up once per clone:
`git remote add github git@github.com:ediarum/ediarum.fileservice.git`.
