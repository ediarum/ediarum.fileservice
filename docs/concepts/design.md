# Design

1. Every project's texts are files. The platform project registry's `xml`/`app` switch
   concerns `index` only: an `app`-led project keeps its registers in
   `index`'s database, but is written, filled and checked here like any
   other.
2. webdav sends the same `POST /files` to fileservice, `index` and `id`,
   one queued job per target. All three check per-project API keys.
3. Only fileservice writes to `/data/xml`. `index` and `id` mount it read-only.
4. There are no service-to-service notifications.
5. webdav is the source of truth; the git host is a backup of webdav's
   pushes. Git writes the volume only on the first fill of a project without
   a checkout (files posted before stay) and on an admin reload.
6. At `CHECK_TIMES` the backup check compares each project's files with the
   git remote (`check.py`) and records one of `in_sync`, `git_behind`
   (webdav's push hasn't arrived), `diverged` (usually a post in webdav's
   failed queue: resend it there; else reload) or `failed`. It never changes
   the files. See [webdav-led sync](webdav-led-sync.md).
7. `index` and `id` pick up changes with a periodic content-hash scan
   of the volume.

Requirements on the other services:

- `index` and `id`: ingesting unchanged content is a no-op; cross-file work
  reads the volume, not the request.
- webdav: events for one project and target are delivered in order.

This replaces the changeset and write-back parts of ediarum.index's former
`docs/architecture/`.
