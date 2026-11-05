# Contributing

The contributor documentation is in [`docs/develop/`](docs/develop/index.md):

- [Development and status](docs/develop/index.md)
- [Running it locally](docs/develop/running.md) — with the platform mock, or in Docker
- [CI and release](docs/develop/release.md)

Before a merge request: `uv run pytest`, and `uv run --group docs mkdocs build`
if you touched the docs or docstrings.
