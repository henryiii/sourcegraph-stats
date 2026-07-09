# scikit-build-core usage stats

Repositories with a `pyproject.toml` that references `scikit_build_core`, found
via Sourcegraph and enriched with GitHub star counts.

## Source data

The CSV is a [Sourcegraph search][] export ("Export the file"):

```
context:global file:pyproject.toml scikit_build_core select:file count:11000
```

[Sourcegraph search]: https://sourcegraph.com/search?q=context:global+file:pyproject.toml+scikit_build_core+select:file+count:11000&patternType=keyword&sm=0

## Building the database

```bash
python3 build_db.py
```

This reads the CSV, fetches stargazer counts for every GitHub repo through the
GraphQL API (batched, using the `gh` CLI's token), and writes
`scikit-build-core.db`. It rebuilds from scratch each run, so re-run it to
refresh stars.

## Fetching file contents

```bash
python3 fetch_files.py
```

This pulls each referenced file's contents into the `file_contents` table via
the GitHub GraphQL API, batched ~50 files per request (the full ~980 files cost
about 120 of the 5000 points/hour limit). It's resumable — already-fetched
files are skipped — so re-run it if interrupted. A later `build_db.py` rebuild
preserves already-fetched contents (remapped by repository + path), so you only
need to re-run this for files that are new or were missing.

## Schema

Three related tables:

- **`repos`** — one row per repository:
  `id`, `repository` (e.g. `github.com/owner/name`), `host`, `owner`, `name`,
  `external_url`, `stars` (`NULL` for non-GitHub or deleted/private repos).
- **`files`** — one row per matched file, linked via `repo_id` → `repos.id`:
  `id`, `repo_id`, `file_path`, `file_url`, `path_matches`, `chunk_matches`.
- **`file_contents`** — the fetched blob, keyed by `file_id` → `files.id`:
  `content`, `byte_size`, `is_binary`, `status`
  (`ok` / `not_found` / `repo_missing` / `non_github`).

Look up the files for a repo with a join:

```sql
SELECT f.file_path
FROM files f JOIN repos r ON r.id = f.repo_id
WHERE r.repository = 'github.com/microsoft/LightGBM';
```

Rank repos by popularity:

```sql
SELECT name, stars FROM repos ORDER BY stars DESC LIMIT 10;
```

## Analysing pyproject.toml keys

`compute-tool.py` (adapted from [henryiii/pystats][]) counts and inspects keys
across the fetched TOML files, reading straight from `file_contents`:

```bash
./compute-tool.py tool.scikit-build                 # keys under [tool.scikit-build]
./compute-tool.py tool.scikit-build.wheel -l1       # count one level deeper
./compute-tool.py build-system.build-backend -c Reprs   # distinct values, by count
./compute-tool.py tool.scikit-build -c Any          # how many files have the section
./compute-tool.py build-system.build-backend -c Reprs -b "'scikit_build_core.build'"
```

`-c` selects what to count (`Values` keys, `Reprs` values, `Lengths`, `Lines`,
`Any`); `-b` "blames" — lists the `repository file_path` of each match.

[henryiii/pystats]: https://github.com/henryiii/pystats
