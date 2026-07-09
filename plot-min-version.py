#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["packaging", "matplotlib"]
# ///
"""Plot the minimum scikit-build-core version each project pins itself to.

Two independent floors live in a pyproject.toml:
  * ``tool.scikit-build.minimum-version`` -- the feature floor. May be a real
    version, absent, or the sentinel ``"build-system.requires"`` (meaning
    "derive it from the build requirement").
  * the ``>=`` / ``~=`` / ``==`` bound on scikit-build-core in
    ``build-system.requires`` -- the actually-installable floor.

We take the explicit ``minimum-version`` when it is a real version and fall
back to the build-requirement bound otherwise, so every project resolves to a
single effective floor tagged by where it came from.

    ./plot-min-version.py                 # writes min-version.png
    ./plot-min-version.py -o out.svg      # pick the output file
    ./plot-min-version.py --show          # open an interactive window
"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
from collections import Counter

import tomllib
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

DB_PATH = "scikit-build-core.db"
PACKAGE = "scikit-build-core"
# minimum-version may point back at the requirement floor instead of naming one.
SENTINEL = "build-system.requires"

# Lower-bound operators, in the order we trust them to express "the floor".
LOWER_BOUND_OPS = (">=", "~=", "==")


def get_tomls(db: str):
    """Yield parsed TOML for every successfully fetched pyproject.toml."""
    cmd = """
        SELECT c.content
        FROM file_contents c
        WHERE c.status = 'ok' AND c.content IS NOT NULL
    """
    with contextlib.closing(sqlite3.connect(db)) as con:
        for (content,) in con.execute(cmd):
            with contextlib.suppress(tomllib.TOMLDecodeError):
                yield tomllib.loads(content)


def requires_floor(requires: list) -> Version | None:
    """Lowest scikit-build-core version accepted by build-system.requires."""
    for entry in requires:
        try:
            req = Requirement(entry)
        except InvalidRequirement:
            continue
        if canonicalize_name(req.name) != PACKAGE:
            continue
        for op in LOWER_BOUND_OPS:
            bounds = [s.version for s in req.specifier if s.operator == op]
            if bounds:
                with contextlib.suppress(InvalidVersion):
                    return min(Version(b) for b in bounds)
    return None


def explicit_floor(value) -> Version | None:
    """A real version from tool.scikit-build.minimum-version, else None."""
    if not isinstance(value, str) or value == SENTINEL:
        return None
    with contextlib.suppress(InvalidVersion):
        return Version(value)
    return None


# Sources of a floor, in stacking order, each with its bar label and colour.
SOURCES = [
    ("minimum-version", "tool.scikit-build.minimum-version", "#4c72b0"),
    ("sentinel", 'minimum-version = "build-system.requires"', "#55a868"),
    ("requires", "build-system.requires bound", "#dd8452"),
]


def effective_floor(toml: dict) -> tuple[Version, str] | None:
    """(floor, source) for a project, or None if no floor is declared.

    An explicit ``minimum-version`` wins; the ``build-system.requires``
    sentinel and a bare requirement both fall back to the requirement bound,
    but are tagged apart so the sentinel gets its own colour."""
    minimum = toml.get("tool", {}).get("scikit-build", {}).get("minimum-version")
    explicit = explicit_floor(minimum)
    if explicit is not None:
        return explicit, "minimum-version"
    requires = toml.get("build-system", {}).get("requires", [])
    floor = requires_floor(requires)
    if floor is not None:
        return floor, "sentinel" if minimum == SENTINEL else "requires"
    return None


def bucket(v: Version) -> str:
    """Group patch releases under their major.minor line (0.10.7 -> 0.10)."""
    return f"{v.major}.{v.minor}"


def collect(db: str) -> dict[str, Counter]:
    """{source: Counter(major.minor -> n)} across all projects."""
    counts: dict[str, Counter] = {key: Counter() for key, _, _ in SOURCES}
    for toml in get_tomls(db):
        result = effective_floor(toml)
        if result is not None:
            floor, source = result
            counts[source][bucket(floor)] += 1
    return counts


def plot(counts: dict[str, Counter], out: str, show: bool) -> None:
    import matplotlib.pyplot as plt

    buckets = sorted(
        {b for c in counts.values() for b in c}, key=lambda s: Version(s)
    )
    totals = [0] * len(buckets)
    total = sum(sum(c.values()) for c in counts.values())

    fig, ax = plt.subplots(figsize=(10, 6))
    for key, label, color in SOURCES:
        heights = [counts[key][b] for b in buckets]
        ax.bar(buckets, heights, bottom=totals, label=label, color=color)
        totals = [t + h for t, h in zip(totals, heights)]
    for i, t in enumerate(totals):
        if t:
            ax.text(i, t, str(t), ha="center", va="bottom", fontsize=9)

    ax.set_xlabel("Minimum scikit-build-core version (major.minor)")
    ax.set_ylabel("Projects")
    ax.set_title(f"Minimum scikit-build-core version pinned by {total} projects")
    ax.legend()
    ax.margins(y=0.1)
    fig.tight_layout()

    if show:
        plt.show()
    else:
        fig.savefig(out, dpi=150)
        print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--out", default="min-version.png", help="output image")
    parser.add_argument("--show", action="store_true", help="show instead of saving")
    args = parser.parse_args()
    plot(collect(DB_PATH), args.out, args.show)


if __name__ == "__main__":
    main()
