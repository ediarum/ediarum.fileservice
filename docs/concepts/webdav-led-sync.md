# Plan: webdav-led sync — git as backup, checked, not enforced

Status: implemented (2026-10-16, `check.py`). Since then `app`-led projects
are handled like any other ([Design](design.md), rule 1); the "`xml`-led"
and "409 for `app`-led" below are out of date. Replaces commit-aware
reconciliation (`reconcile.py`, architecture plan §2.8). Where the
implementation differs from the first draft, this text follows the
implementation.

## Why

Today git is the source of truth: every `RECONCILE_INTERVAL_SECONDS` (300)
fileservice fetches every project, and a webdav save whose commit is not on
the remote after `RECONCILE_GRACE_PERIOD_SECONDS` (300) is reset to git's
version (`reset --hard` + `clean -fd`). In practice:

- **webdav is the source of truth.** Editors save through webdav, which
  sends fileservice every change as `POST /files`. The git host
  receives webdav's pushes as a backup.
- **A slow or unreachable git host reverts valid saves.** Five minutes
  without the pushed commit is enough; the save disappears from the volume
  and, after the next ingest, from index and id.
- **The polling load is high**: 14 projects every 5 minutes. Failed fetches
  have already led to gitup blocking this host for a while.

## Decision

For every `xml`-led project:

1. `POST /files` stays as it is: the change is written to the volume
   immediately, its commit recorded as claimed.
2. Git is **read only once or twice a day** and **compared** with the
   volume. Differences are **reported, never corrected** automatically.
3. Git is used to **write** the volume in two cases only: the first fill of
   a project without a checkout, and an admin's explicit **reload**.

There is no git-led mode: nothing commits to the project repositories
except webdav (see below). The interval loop, the grace period and their
settings go.

## What webdav guarantees (clarified 2026-10-16)

- **Retries:** a failed `POST /files` is retried a number of times, then
  kept in webdav's failed queue. → A save fileservice misses for good
  shows up at the next check as `diverged` (git has it, the volume
  doesn't); an admin reloads the project.
- **Push to git** is retried as well. → `git_behind` is normally
  temporary; one that persists across checks means webdav's push is stuck.
- **No other writers:** no project gets commits from anywhere but webdav.
  → A difference is always webdav → fileservice or webdav → git going
  wrong, never a legitimate edit made in git.
- **Order:** a project's posts arrive in order, retries included. → The
  volume can't be corrupted by a late `modified` after a `moved`/`deleted`;
  replaying posts in sequence reproduces webdav's state.
- **Queue interface:** webdav has an interface that shows the queue status
  and resends failed posts. → The first fix for a `diverged` project is
  resending from there; a reload from git is the fallback.

## Behaviour

### First fill

At startup and before each check: a project with no `.git` under
`XML_MOUNT_ROOT/<slug>` is cloned (`git_sync.ensure_clone`), and
`verified_commit` is recorded. Files `POST /files` wrote before (e.g.
while the git host was unreachable) are webdav's, so they win: set aside,
git's version checked out, put back on top; the next check reports them if
git doesn't have them yet. This is the only automatic write from git. An
existing checkout is never touched.

### The check

At each time in `CHECK_TIMES` (in `CHECK_TIMEZONE`), per project. Two
lock sets: the git lock per project for the whole check (git operations
never overlap), the mount lock that `POST /files` holds only for the
comparison — so a slow fetch doesn't block saves:

1. `git fetch --depth=1 --filter=blob:none origin <branch>` — updates only
   `origin/<branch>`; `HEAD`, index and working tree stay as they are.
2. Compare the working tree with `origin/<branch>`, limited to
   `sparse_path` if set, **without touching the real index**: with a
   temporary `GIT_INDEX_FILE`, `read-tree origin/<branch>`, `add -A`, then
   `diff --cached --name-status origin/<branch>`. Catches modified, added
   (untracked) and deleted files (tried on a test repository: correct
   result, `HEAD`/index/files untouched). It compares blob ids only, so it
   should need no blob downloads despite the partial clone — to verify
   against gitup.
3. Classify:

   | Result | Condition | Meaning |
   |---|---|---|
   | `in_sync` | no differences | git has what the volume has |
   | `git_behind` | differences, and the last claim is newer than `origin/<branch>`'s commit time | webdav's push hasn't arrived yet: backup lag |
   | `diverged` | differences otherwise | a post that never reached fileservice (webdav's failed queue), or a manual change on the volume |
   | `failed` | fetch or compare failed | git host unreachable, no access, … (the error is kept) |

4. Record the result (see "Data"); on `in_sync` also record
   `verified_commit` = the remote commit (the mount's content is that
   commit's). `verified` now means "confirmed in git after the last post":
   `verified_at >= claimed_at`, not equal commit ids (after a reload the
   last claimed commit can be an older one).
5. Log one line per project, `WARNING` for `diverged`, `failed`, and for a
   `git_behind` that already was `git_behind` at the previous check.

No reset, no clean, no checkout. Two checks a day means one fetch per
project per check: 28 requests a day instead of ~4000.

### Admin reload

`POST /projects/{slug}/reload` (platform admins only): fetch, `reset
--hard origin/<branch>`, `clean -fd`, record `verified_commit`, then run
the check (result `in_sync`). This is today's correction, now only on
request — the fallback for a reported `diverged` that resending from
webdav's queue interface doesn't fix (nothing failed there, or the
difference has another cause, e.g. a manual change on the volume).

Refused with 409 while the last check says `git_behind` (the reset would
discard saves that only exist on the volume), unless `?force=true`.

### What stays

- `POST /files`, its auth, locking, 409 for `app`-led projects.
- `GET /projects/{slug}/status`: existing fields keep their meaning for
  the index and id watchers, which only compare `claimed_commit` /
  `verified_commit` against their last check to skip unchanged projects —
  every post still changes `claimed_commit`, a first fill or reload
  changes `verified_commit`. No change needed in index or id.

## Data

New table `git_check` (one row per project, replaced at each check) rather
than new columns on `sync_state`: `init_db`'s `create_all` creates a new
table in an existing database, but never adds columns, and fileservice has
no migrations.

| Column | |
|---|---|
| `project` | primary key |
| `checked_at` | when the check ran |
| `result` | `in_sync`, `git_behind`, `diverged`, `failed` |
| `remote_commit` | `origin/<branch>` at the check |
| `differences` | JSON list of `{status, path}`, capped at 200 entries |
| `difference_count` | the uncapped count |
| `error` | for `failed` |
| `result_since` | when the current result first appeared (persisting `git_behind`) |

## API

- `GET /projects/{slug}/status` gains `check`: the `git_check` row
  (`null` before the first check).
- **New** `GET /projects/status` (platform admins): every registry project
  with claimed/verified commit and its last check — the overview that is
  missing today.
- **New** `POST /projects/{slug}/check` (admins): run the check for one
  project now, outside `CHECK_TIMES` — for debugging and to confirm a fix.
  A git error is the `failed` result (200), not an HTTP error.
- **New** `POST /projects/{slug}/reload` (admins), see above; 502 if git
  can't be read, 503 if the project is busy.
- The admin routes accept platform admins and admin API keys
  (`create-key --admin`), so they work with curl.

## Configuration

| Variable | Default | |
|---|---|---|
| `CHECK_TIMES` | `03:00,15:00` | times of the checks; empty = no checks |
| `CHECK_TIMEZONE` | `Europe/Berlin` | time zone of `CHECK_TIMES` (independent of the container's `TZ`) |

Removed: `RECONCILE_INTERVAL_SECONDS`, `RECONCILE_GRACE_PERIOD_SECONDS`.
A leftover value is ignored with a warning at startup, so an old env file
doesn't fail the start.

A restart doesn't trigger a check (no burst of fetches on every deploy);
only the first fill runs at startup.

## Code

- `config.py`: `check_times`; drop `grace_period`, `reconcile_interval`.
- `git_sync.py`: `fetch_remote(…)` (fetch only, returns the remote commit
  and its time), `compare_with_remote(…)` (the temporary-index diff),
  `reset_to_remote(…)` (today's `pull` without the clone, for reload);
  `fetch_remote_head` goes; `pull` stays as the reload.
- `reconcile.py` → `check.py`: `first_fill`, `check_project`, `check_all`,
  and a scheduler that sleeps until the next `CHECK_TIMES` entry. The
  grace-period logic goes.
- `db.py`: `GitCheck`, `record_check`; `SyncState`'s docstring updated
  (verified = confirmed in git at a check, first fill or reload).
- `app.py`: lifespan runs the first fill, starts the scheduler; the status
  field, the two new routes.
- README: routes, configuration, the "Design" section; ediarum.deploy's
  `env/fileservice.env.example` (the two removed settings, `CHECK_TIMES`,
  `CHECK_TIMEZONE`) and its "Sync timing" open point (resolved by this plan).

## Tests

`test_reconcile.py` is replaced by `test_check.py`:

- check: `in_sync`; `diverged` for a modified, an added and a deleted
  file; `git_behind` after a post newer than the remote commit; `failed`
  with an unreachable remote; `sparse_path` limits the comparison.
- the check never changes `HEAD`, the index or a file (assert the tree and
  a file's bytes before and after).
- first fill clones a missing project, leaves existing ones alone, and
  fills a directory that `POST /files` wrote to before (today's
  `test_first_sync_into_directory_posted_to_before`).
- reload resets and records; 409 on `git_behind` without `force`.
- one failing project doesn't stop the others; a failed round is retried
  at the next time.
- scheduler: next run computed from `CHECK_TIMES` across midnight.
- routes: overview and reload are admin-only; status includes `check`.

## Rollout

1. Implement; release a fileservice image.
2. Deploy with `CHECK_TIMES` and `CHECK_TIMEZONE` set; the first checks show where
   volume and git differ today.
3. Work through `diverged` projects: resend failed posts in webdav's queue
   interface first, reload from git only if that doesn't clear it at the
   next check (or right away: `POST /projects/<slug>/check`). Then watch
   the overview after each check.

## Open

- Where reports go besides the log and the overview: a notice in the
  platform, mail to admins?
- Linking the two interfaces: the overview could point to webdav's queue
  interface for a `diverged` project.
- Automatic reload of a `diverged` project is left out on purpose: with
  webdav as the only writer it would usually be right, but it would bring
  back the "git overrides the volume" behaviour this plan removes. Revisit
  once the checks have run for a while.
