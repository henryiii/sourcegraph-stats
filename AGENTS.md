# Agent instructions

## What this is

A small data pipeline that measures how projects use `scikit-build-core`. It takes a Sourcegraph CSV export of repositories with a `pyproject.toml` referencing `scikit_build_core`, enriches it with GitHub data, and stores everything in a local SQLite database (`scikit-build-core.db`) for querying.

## Pipeline (run in order)

```bash
python3 build_db.py      # CSV -> repos + files tables, fetches GitHub stars
python3 fetch_files.py   # populates file_contents with the actual TOML blobs
./compute-tool.py tool.scikit-build   # analyse keys across the fetched TOMLs
```

No dependencies beyond the stdlib (`tomllib` needs Python 3.11+). The scripts use `gh auth token` for the GitHub GraphQL API, so the `gh` CLI must be authenticated.

## Key architecture facts

- **The `.csv` and `.db` are gitignored** — only the three scripts are tracked. The DB is generated data, rebuilt from the CSV. `build_db.py` picks up whatever single `*.csv` is present via glob.
- **`build_db.py` rebuilds the DB from scratch each run** (drops and recreates), but `read_existing_contents()` preserves already-fetched `file_contents` across the rebuild, remapped by the stable `(repository, file_path)` pair since `files.id` is reassigned. So re-running it to refresh stars does not force a re-fetch of file contents.
- **`fetch_files.py` is resumable and batched** (~50 files/request via GraphQL aliases). Files already in `file_contents` are skipped; re-run after a rate limit. `status` distinguishes `ok` / `not_found` / `repo_missing` / `non_github`.
- **Only `github.com` repos are reachable** via the GraphQL API for stars and contents; the schema tolerates other hosts (`stars` NULL, `file_contents.status = 'non_github'`).
- **`EXCLUDE_REPOS` in `build_db.py`** drops the canonical scikit-build-core repo (templates/test fixtures skew stats). Add mirrors/forks there if they appear in future exports.

## Schema

Three tables joined via `repos.id` <- `files.repo_id`, and `files.id` <- `file_contents.file_id`:
`repos` (repository, host, owner, name, stars) / `files` (file_path, file_url, match metadata) / `file_contents` (content, byte_size, is_binary, status).

## compute-tool.py

Adapted from [henryiii/pystats](https://github.com/henryiii/pystats), but reads TOML straight from `file_contents.content` instead of a pickle cache. Takes a dotted key path (e.g. `tool.scikit-build.wheel`); `-c` picks what to count (`Values` keys / `Reprs` values / `Lengths` / `Lines` / `Any`), `-l` unpacks nested levels, `-b` "blames" by listing matching `repository file_path`. See README for worked examples.
