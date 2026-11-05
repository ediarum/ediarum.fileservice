# Operating

## API keys

API keys are created with the [CLI](../reference/cli.md). The key is printed once; only its hash is stored.

```
uv run ediarum-fileservice-create-key <slug>    # or --admin for all projects
```

## Startup and backup checks

At startup the app clones every project that has no checkout yet;
at each of `CHECK_TIMES`, `docker logs` shows a line per project and one per
round (`check: 12 in_sync, 1 diverged`). Run a single container: the
scheduler runs inside the app.

Check results and what to do: [Design](../concepts/design.md), rule 6.

## Reload

Only an admin reload converges a checkout to git: `reset --hard` plus
`clean -fd`, so files that only arrived by `POST /files` and never reached
git are removed — hence the 409 while the last check says `git_behind`.
