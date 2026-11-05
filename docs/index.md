# ediarum.fileservice

Keeps the XML of each project on the shared volume (`/data/xml/<slug>`)
in step with `ediarum.webdav` and git. It is the only service that writes to
the volume and the only one that runs git.

## How it fits into the platform

```mermaid
flowchart TB
  oxygen["oXygen + ediarum-webdav plugin"]
  webdav["ediarum.webdav"]
  git[("git host")]
  fileservice["ediarum.fileservice<br/>writes the file to /data/xml (rw)"]
  register["ediarum.index<br/>ingests the posted content"]
  id["ediarum.id<br/>ingests the posted content"]
  volume[("files on the volume<br/>/data/xml")]

  oxygen -- "save" --> webdav
  webdav -- "commit, then push" --> git
  webdav -- "POST /files (one queued job per target)" --> fileservice
  webdav -- "POST /files" --> register
  webdav -- "POST /files" --> id
  fileservice --> volume
  git -. "backup check (report only)" .-> fileservice
  volume -- "periodic hash scan (ro)" --> register
  volume -- "periodic hash scan (ro)" --> id
```

## Where to read on

| If you want to… | Read |
|---|---|
| deploy or run it | [Operate](operate/deploy.md) |
| work on the code | [Develop](develop/index.md) |
| know why it works this way | [Design](concepts/design.md) · [webdav-led sync](concepts/webdav-led-sync.md) |
| look up a route, setting or command | [Reference](reference/routes.md) |
| read a module's or function's docstring | [Python API](api/index.md) |
