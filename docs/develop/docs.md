# Documentation

[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) over the
Markdown in `docs/`, configured in `mkdocs.yml`. The public site,
https://ediarum.github.io/ediarum.fileservice/, is built by `.github/workflows/docs.yml`
on the GitHub mirror, so it shows the latest release. The `pages` CI job
publishes the current `main` to GitLab Pages as an internal preview.

```bash
uv run --group docs mkdocs serve    # http://127.0.0.1:8000, rebuilds on save
uv run --group docs mkdocs build    # strict: fails on a broken link or anchor
```

- **One source.** Every page lives in `docs/`; `README.md` and
  `CONTRIBUTING.md` only point here. The pages are plain Markdown, so they read
  on GitLab too.
- **Navigation** is the `nav` in `mkdocs.yml`: a new page needs an entry there.
- **Links** between pages are relative (`../reference/settings.md#…`). Link to
  code by its path in backticks — files outside `docs/` aren't part of the site.
- **Look** as in ediarum.index: colours and Noto Sans in
  `docs/stylesheets/ediarum.css`, footer in
  `docs/overrides/partials/copyright.html`, logos and fonts in `docs/assets/`.
- **Diagrams** are Mermaid (` ```mermaid `), which GitLab renders too.
- **Python API** pages (`docs/api/`) are `::: ediarum_fileservice.<module>` directives
  for [mkdocstrings](https://mkdocstrings.github.io/). Docstrings use the Google
  style for `Args:` / `Returns:`; types come from the signature.
