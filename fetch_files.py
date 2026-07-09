#!/usr/bin/env python3
"""Fetch the referenced file contents into the SQLite database.

Populates a `file_contents` table (one row per files.id) by pulling each blob
from its repo's default branch through the GitHub GraphQL API, batched with
aliases so ~50 files cost a single request. Resumable: files already fetched
are skipped, so it can be re-run after hitting a rate limit.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

DB_PATH = "scikit-build-core.db"
BATCH = 50


def gh_token() -> str:
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


def graphql(query: str) -> dict:
    out = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout or "{}").get("data") or {}


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS file_contents (
            file_id    INTEGER PRIMARY KEY REFERENCES files(id),
            content    TEXT,
            byte_size  INTEGER,
            is_binary  INTEGER,
            status     TEXT NOT NULL   -- ok | not_found | repo_missing | non_github
        );
        """
    )

    # Only GitHub repos are reachable via the GraphQL API.
    todo = con.execute(
        """
        SELECT f.id, r.owner, r.name, f.file_path
        FROM files f
        JOIN repos r ON r.id = f.repo_id
        WHERE r.host = 'github.com'
          AND f.id NOT IN (SELECT file_id FROM file_contents)
        ORDER BY f.id
        """
    ).fetchall()
    print(f"{len(todo)} files to fetch", file=sys.stderr)

    for start in range(0, len(todo), BATCH):
        chunk = todo[start : start + BATCH]
        parts = []
        for j, (_fid, owner, name, path) in enumerate(chunk):
            expr = json.dumps(f"HEAD:{path}")
            parts.append(
                f"f{j}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) "
                f"{{ object(expression:{expr}) {{ ... on Blob "
                f"{{ text byteSize isBinary }} }} }}"
            )
        data = graphql("query {\n" + "\n".join(parts) + "\n}")

        for j, (fid, *_rest) in enumerate(chunk):
            node = data.get(f"f{j}", "MISSING")
            if node is None:
                row = (fid, None, None, None, "repo_missing")
            elif node == "MISSING" or node.get("object") is None:
                row = (fid, None, None, None, "not_found")
            else:
                obj = node["object"]
                row = (
                    fid,
                    obj.get("text"),
                    obj.get("byteSize"),
                    int(bool(obj.get("isBinary"))),
                    "ok",
                )
            con.execute(
                "INSERT OR REPLACE INTO file_contents "
                "(file_id, content, byte_size, is_binary, status) VALUES (?,?,?,?,?)",
                row,
            )
        con.commit()
        print(f"  {min(start + BATCH, len(todo))}/{len(todo)}", file=sys.stderr)

    # Record the one non-GitHub repo (Fedora) as unreachable via this path.
    con.execute(
        """
        INSERT OR IGNORE INTO file_contents (file_id, status)
        SELECT f.id, 'non_github'
        FROM files f JOIN repos r ON r.id = f.repo_id
        WHERE r.host != 'github.com'
        """
    )
    con.commit()

    counts = dict(
        con.execute("SELECT status, count(*) FROM file_contents GROUP BY status")
    )
    print("status:", counts, file=sys.stderr)
    con.close()


if __name__ == "__main__":
    main()
