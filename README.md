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

## Schema

Two related tables:

- **`repos`** — one row per repository:
  `id`, `repository` (e.g. `github.com/owner/name`), `host`, `owner`, `name`,
  `external_url`, `stars` (`NULL` for non-GitHub or deleted/private repos).
- **`files`** — one row per matched file, linked via `repo_id` → `repos.id`:
  `id`, `repo_id`, `file_path`, `file_url`, `path_matches`, `chunk_matches`.

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
