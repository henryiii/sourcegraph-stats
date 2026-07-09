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


def load_rows() -> list[dict]:
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


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


def main() -> None:
    rows = load_rows()
    repositories = sorted({r["Repository"] for r in rows})
    print(f"{len(rows)} rows, {len(repositories)} repos", file=sys.stderr)

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
        con.execute(
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

    con.commit()
    con.close()
    print(f"wrote {DB_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
