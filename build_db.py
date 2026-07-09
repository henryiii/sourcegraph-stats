#!/usr/bin/env python3
"""Build a SQLite database from the Sourcegraph CSV export.

Two tables:
  repos  - one row per repository (with GitHub star counts)
  files  - one row per matched file, linked to repos via repo_id
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
import subprocess
import sys

CSV_PATH = next(iter(glob.glob("*.csv")))
DB_PATH = "scikit-build-core.db"

# scikit-build-core itself only contributes templates and test fixtures, which
# skew the stats. No forks or content-identical mirrors exist in the export, so
# excluding the canonical repo is enough; add copies here if any ever appear.
EXCLUDE_REPOS = {"github.com/scikit-build/scikit-build-core"}


def load_rows() -> list[dict]:
    with open(CSV_PATH, newline="") as f:
        return [r for r in csv.DictReader(f) if r["Repository"] not in EXCLUDE_REPOS]


def split_repo(repository: str) -> tuple[str, str, str]:
    """github.com/owner/name -> (host, owner, name)."""
    host, _, rest = repository.partition("/")
    owner, _, name = rest.partition("/")
    return host, owner, name


def fetch_stars(repos: list[str]) -> dict[str, int]:
    """Fetch stargazer counts for github.com repos via the GraphQL API."""
    token = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()

    gh = [r for r in repos if r.startswith("github.com/")]
    stars: dict[str, int] = {}
    batch = 50
    for i in range(0, len(gh), batch):
        chunk = gh[i : i + batch]
        parts = []
        for j, repo in enumerate(chunk):
            _, owner, name = split_repo(repo)
            parts.append(
                f'r{j}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) '
                f'{{ nameWithOwner stargazerCount }}'
            )
        query = "query {\n" + "\n".join(parts) + "\n}"
        out = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True,
            text=True,
        )
        data = json.loads(out.stdout or "{}").get("data", {})
        for j, repo in enumerate(chunk):
            node = data.get(f"r{j}")
            if node:
                stars[repo] = node["stargazerCount"]
        print(f"  stars: {min(i + batch, len(gh))}/{len(gh)}", file=sys.stderr)
    return stars


def read_existing_contents(path: str) -> dict[tuple[str, str], tuple]:
    """Preserve fetched file_contents across a rebuild, keyed by the stable
    (repository, file_path) pair since file ids are reassigned."""
    if not os.path.exists(path):
        return {}
    con = sqlite3.connect(path)
    try:
        con.execute("SELECT 1 FROM file_contents LIMIT 1")
    except sqlite3.OperationalError:
        con.close()
        return {}
    saved = {
        (repository, file_path): (content, byte_size, is_binary, status)
        for repository, file_path, content, byte_size, is_binary, status in con.execute(
            """
            SELECT r.repository, f.file_path,
                   c.content, c.byte_size, c.is_binary, c.status
            FROM file_contents c
            JOIN files f ON f.id = c.file_id
            JOIN repos r ON r.id = f.repo_id
            """
        )
    }
    con.close()
    return saved


def main() -> None:
    rows = load_rows()
    repositories = sorted({r["Repository"] for r in rows})
    print(f"{len(rows)} rows, {len(repositories)} repos", file=sys.stderr)

    saved_contents = read_existing_contents(DB_PATH)
    if saved_contents:
        print(f"preserving {len(saved_contents)} fetched files", file=sys.stderr)

    stars = fetch_stars(repositories)
    print(f"got stars for {len(stars)} repos", file=sys.stderr)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE repos (
            id            INTEGER PRIMARY KEY,
            repository    TEXT NOT NULL UNIQUE,
            host          TEXT NOT NULL,
            owner         TEXT,
            name          TEXT,
            external_url  TEXT,
            stars         INTEGER
        );
        CREATE TABLE files (
            id            INTEGER PRIMARY KEY,
            repo_id       INTEGER NOT NULL REFERENCES repos(id),
            file_path     TEXT NOT NULL,
            file_url      TEXT,
            path_matches  TEXT,
            chunk_matches TEXT
        );
        CREATE INDEX idx_files_repo_id ON files(repo_id);
        CREATE TABLE file_contents (
            file_id    INTEGER PRIMARY KEY REFERENCES files(id),
            content    TEXT,
            byte_size  INTEGER,
            is_binary  INTEGER,
            status     TEXT NOT NULL
        );
        """
    )

    repo_id: dict[str, int] = {}
    ext_url = {r["Repository"]: r["Repository external URL"] for r in rows}
    for repository in repositories:
        host, owner, name = split_repo(repository)
        cur = con.execute(
            "INSERT INTO repos (repository, host, owner, name, external_url, stars)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (repository, host, owner, name, ext_url[repository], stars.get(repository)),
        )
        repo_id[repository] = cur.lastrowid

    for r in rows:
        cur = con.execute(
            "INSERT INTO files (repo_id, file_path, file_url, path_matches, chunk_matches)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                repo_id[r["Repository"]],
                r["File path"],
                r["File URL"],
                r["Path matches [path [start end]]"] or None,
                r["Chunk matches [line [start end]]"] or None,
            ),
        )
        key = (r["Repository"], r["File path"])
        if key in saved_contents:
            con.execute(
                "INSERT OR IGNORE INTO file_contents"
                " (file_id, content, byte_size, is_binary, status) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, *saved_contents[key]),
            )

    restored = con.execute("SELECT count(*) FROM file_contents").fetchone()[0]
    con.commit()
    con.close()
    print(f"wrote {DB_PATH} (restored {restored} file contents)", file=sys.stderr)


if __name__ == "__main__":
    main()
