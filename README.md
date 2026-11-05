<a href="https://www.ediarum.org"><img src="docs/assets/ediarum-logo.png" alt="ediarum" width="236"></a>

# ediarum.fileservice

[![Documentation](https://github.com/ediarum/ediarum.fileservice/actions/workflows/docs.yml/badge.svg)](https://ediarum.github.io/ediarum.fileservice/)

Version: 0.1.2

© 2026 by Berlin-Brandenburg Academy of Sciences and Humanities

Part of ediarum (https://www.ediarum.org, ediarum@bbaw.de)

Developed by TELOTA (https://www.bbaw.de/telota, telota@bbaw.de), a DH
working group of the Berlin-Brandenburg Academy of Sciences and Humanities.

Developer of ediarum.fileservice:

* Malte Vogl

Keeps the XML of each project on the shared volume (`/data/xml/<slug>`)
in step with `ediarum.webdav` and git. It is the only service that writes to
the volume and the only one that runs git.

webdav posts every save here (`POST /files`); the file is written to the
volume at once. At `CHECK_TIMES` (default 03:00 and 15:00) a backup check
compares the volume with the git host and reports differences; it never
changes the files.

## Documentation

The full documentation is in [`docs/`](docs/index.md), published as a site at
https://ediarum.github.io/ediarum.fileservice/ (from each release). Build it locally with
`uv run --group docs mkdocs serve`.

| | |
|---|---|
| [Deploy](docs/operate/deploy.md) · [Operating](docs/operate/operating.md) | running it on the platform |
| [Develop](docs/develop/index.md) · [Running locally](docs/develop/running.md) · [Release](docs/develop/release.md) | working on the code |
| [Design](docs/concepts/design.md) · [webdav-led sync](docs/concepts/webdav-led-sync.md) | why it works this way |
| [Reference](docs/reference/routes.md) | routes, settings, CLI |
| [Python API](docs/api/index.md) | generated from the docstrings (site only) |

## Quick start

```bash
docker build -t ediarum-fileservice .
```

Running the container (volumes, git token, environment):
[docs/operate/deploy.md](docs/operate/deploy.md). The full stack is defined in `ediarum.deploy`. Local development:
[docs/develop/running.md](docs/develop/running.md).

## License

ediarum.fileservice is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at your
option) any later version. See [LICENSE](LICENSE).

Not covered by this license:

- **Logos** (`docs/assets/bbaw-logo-*.png`, `docs/assets/ediarum-logo.png`)
  are marks of the Berlin-Brandenburg Academy of Sciences and Humanities, all
  rights reserved. Forks must not use them to suggest endorsement.
- **Fonts** (`docs/assets/fonts/noto-sans-*.woff2`): Noto Sans, SIL Open
  Font License 1.1, see [docs/assets/fonts/OFL.txt](docs/assets/fonts/OFL.txt).

Third-party dependencies keep their own licenses (MIT, BSD, Apache-2.0,
MPL-2.0, …), all compatible with the AGPL-3.0.

## Cite this software

If you use this software, please cite it using the metadata in the file
[CITATION.cff](CITATION.cff) (for more information about CFF see
https://citation-file-format.github.io/).
