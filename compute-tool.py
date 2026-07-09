#!/usr/bin/env python3
"""Count / inspect pyproject.toml keys across the fetched files.

Adapted from henryiii/pystats compute-tool.py. Reads TOML straight from the
SQLite database (file_contents.content) instead of a pickle cache -- the file
count here is small enough that parsing on the fly is instant.

Examples:
    ./compute-tool.py tool.scikit-build              # keys under [tool.scikit-build]
    ./compute-tool.py tool.scikit-build.wheel -l1    # one level deeper
    ./compute-tool.py build-system.build-backend -c Reprs
    ./compute-tool.py tool.scikit-build -c Any        # count files that have it
    ./compute-tool.py build-system.build-backend -b '"scikit_build_core.build"'
"""

import argparse
import contextlib
import enum
import sqlite3
import sys
from collections import Counter
from collections.abc import Generator
from typing import Any

import tomllib

DB_PATH = "scikit-build-core.db"


class Contents(enum.Enum):
    Values = enum.auto()
    Reprs = enum.auto()
    Lengths = enum.auto()
    Lines = enum.auto()
    Any = enum.auto()


def dig(value: Any, key: str, *keys: str) -> Any:
    res = value.get(key, {})
    return dig(res, *keys) if keys else res


def all_keys(
    d: dict[str, Any], level: int, *prefixes: str
) -> Generator[str, None, None]:
    for key, value in d.items():
        if isinstance(value, dict) and level > 0:
            yield from all_keys(value, level - 1, *prefixes, key)
        else:
            yield ".".join([*prefixes, key])


def get_tomls(db: str) -> Generator[tuple[str, str, dict[str, Any]], None, None]:
    """Yield (repository, file_path, parsed_toml) for every successfully
    fetched file whose contents parse as TOML."""
    cmd = """
        SELECT r.repository, f.file_path, c.content
        FROM file_contents c
        JOIN files f ON f.id = c.file_id
        JOIN repos r ON r.id = f.repo_id
        WHERE c.status = 'ok' AND c.content IS NOT NULL
        ORDER BY r.repository, f.file_path
    """
    with contextlib.closing(sqlite3.connect(db)) as con:
        for repository, file_path, content in con.execute(cmd):
            with contextlib.suppress(tomllib.TOMLDecodeError):
                yield repository, file_path, tomllib.loads(content)


def main(tool: str, get_contents: Contents, level: int = 0) -> None:
    if tool:
        match get_contents:
            case Contents.Reprs:
                print(f"{tool} contents:")
            case Contents.Lengths:
                print(f"{tool} lengths:")
            case Contents.Lines:
                print(f"{tool} lines:")
            case Contents.Values:
                print(tool + ".*" * (level + 1) + ":")
            case Contents.Any:
                print(f"{tool}:")
    else:
        if get_contents != Contents.Values:
            raise AssertionError("Can't get contents with no section")

        print("*:")

    if get_contents != Contents.Values and level > 0:
        raise AssertionError("Can't use level with contents")

    counter = Counter()
    for _, _, toml in get_tomls(DB_PATH):
        item = dig(toml, *tool.split(".")) if tool else toml
        match item, get_contents:
            case None | "", _:
                pass
            case _, Contents.Any:
                counter[tool] += 1
            case _, Contents.Values:
                counter += Counter(all_keys(item, level=level))
            case list(), Contents.Reprs:
                for x in item:
                    counter[repr(x)] += 1
            case _, Contents.Reprs:
                counter[repr(item)] += 1
            case _, Contents.Lengths:
                counter[len(item)] += 1
            case str(), Contents.Lines:
                counter[item.count("\n")] += 1
            case {}, Contents.Lines:
                counter[-1] += 1

    if get_contents in {Contents.Lengths, Contents.Lines}:
        for k, v in sorted(counter.items()):
            print(f"{k}: {v}")
    else:
        for k, v in counter.most_common():
            print(f"{k}: {v}")


def blame(tool: str, string: str, contents: Contents) -> None:
    if string and contents == Contents.Reprs:
        print(tool, "=", string)
    elif string and contents == Contents.Lengths:
        print(tool, "=", string, "chars")
    elif string and contents == Contents.Lines:
        print(tool, "=", string, "lines")
    elif contents == Contents.Any:
        print("Projects with", tool, file=sys.stderr)
    else:
        print(tool, "= ...")

    for repository, file_path, toml in get_tomls(DB_PATH):
        item = dig(toml, *tool.split(".")) if tool else toml
        if contents == Contents.Any:
            if item:
                print(repository, file_path)
        elif not string and item:
            print(repository, file_path, "=", repr(item))
        elif contents == Contents.Lengths and len(item) == int(string):
            print(repository, file_path)
        elif contents == Contents.Reprs and repr(item) == string:
            print(repository, file_path)
        elif (
            contents == Contents.Lines
            and isinstance(item, str)
            and item.count("\n") == int(string)
        ):
            print(repository, file_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", help="Tool to processes")
    parser.add_argument(
        "-c", "--contents", default="Values", choices={c.name for c in Contents}
    )
    parser.add_argument(
        "-l", "--level", type=int, default=0, help="Unpack nested levels"
    )
    parser.add_argument(
        "-b",
        "--blame",
        help="print matching project names, empty string to print any value (careful)",
    )
    args = parser.parse_args()
    if args.blame is not None:
        assert args.level == 0
        blame(args.tool, args.blame, Contents[args.contents])
    else:
        main(args.tool, Contents[args.contents], args.level)
