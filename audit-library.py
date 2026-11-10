#!/usr/bin/env -S uv run --script
# SPDX-FileCopyrightText: 2026 Matthew Watkins <mwatkins@linuxfoundation.org>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# /// script
# requires-python = ">=3.11"
# dependencies = ["mutagen"]
# ///
"""Audit an iTunes/Music library for problems that affect audi-mmi.sh.

Checks (run all by default, or pick with --check):

  sorting     Artist -> folder mapping: the "The ..." rule, accents, digits,
              symbols, names that reduce to nothing, and names that the
              original (buggy) algorithm would have mis-filed.
  structure   Layout problems: loose files, key collisions between artists or
              albums, .movpkg bundles, nested folders, odd extensions,
              albums with nothing copyable.
  duplicates  Tracks that look like copies of each other inside an album:
              same title ignoring accents/case/punctuation, a trailing " 1"
              suffix added by Music, and (with --tags) the same disc+track
              number under different titles.

Usage:
    ./audit-library.py [LIBRARY_DIR] [--check sorting,structure,duplicates]
                       [--tags] [--letters]

LIBRARY_DIR defaults to ~/Music/Music/Media.localized/Music and must use the
iTunes layout Artist/Album/Track. Exit status is 1 if any check reports an
ERROR-level finding (WARN and INFO never fail the run). Nothing is modified.

Artwork is checked separately by check-artwork.py.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from mutagen.mp4 import MP4, MP4Tags

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_LIBRARY = os.path.expanduser("~/Music/Music/Media.localized/Music")
AUDIO_EXTS = {".m4a", ".mp3", ".m4p", ".aac"}
MP4_EXTS = (".m4a", ".m4p", ".aac")
ALL_CHECKS = ("sorting", "structure", "duplicates")

THE_PREFIX = re.compile(r"^the\s+", re.IGNORECASE)


class Reporter:
    """Print findings and count the error-level ones."""

    def __init__(self) -> None:
        """Start with no errors recorded."""
        self.errors = 0

    def _emit(self, level: str, message: str) -> None:
        print(f"  [{level}] {message}")

    def error(self, message: str) -> None:
        """Record a finding that makes the audit fail."""
        self.errors += 1
        self._emit("ERROR", message)

    def warn(self, message: str) -> None:
        """Record a finding worth attention that does not fail the audit."""
        self._emit("WARN", message)

    def info(self, message: str) -> None:
        """Record a purely informational finding."""
        self._emit("INFO", message)


def heading(title: str) -> None:
    """Print a section heading."""
    print(f"\n== {title} ==")


# key(), ascii_key(), artist_folder() and legacy_artist_folder() mirror the
# folder-name logic in audi-mmi.sh. Keep them in step with it.
def key(name: str) -> str:
    """Mirror audi-mmi.sh make_key(): alphanumerics only, ASCII letters upper-cased."""
    return "".join(c for c in name if c.isalnum()).upper()


def ascii_key(name: str) -> str:
    """Return the key the script produces in a C/ASCII-only locale (worst case)."""
    return re.sub(r"[^A-Za-z0-9]", "", name).upper()


def artist_folder(artist: str) -> str:
    """Return the folder key: drop a leading whole-word 'The', or fall back."""
    return key(THE_PREFIX.sub("", artist, count=1)) or key(artist) or "UNKNOWN"


def legacy_artist_folder(artist: str) -> str:
    """Return what the original algorithm did: strip to alphanumerics, then drop 'THE'."""
    return key(artist).removeprefix("THE")


def iter_dirs(path: str) -> Iterator[tuple[str, str]]:
    """Yield (name, full path) for each sub-directory, sorted by name."""
    for entry in sorted(os.listdir(path)):
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            yield entry, full


def iter_audio(album_path: str) -> Iterator[tuple[str, str]]:
    """Yield (name, full path) for each audio file in an album folder."""
    for name in sorted(os.listdir(album_path)):
        full = os.path.join(album_path, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in AUDIO_EXTS:
            yield name, full


def audit_artist_name(artist: str, rep: Reporter) -> int:
    """Report sorting concerns for one artist; return how many were raised."""
    found = 0
    new = artist_folder(artist)
    old = legacy_artist_folder(artist)
    stripped = THE_PREFIX.sub("", artist, count=1)

    if new[:1] != old[:1]:
        found += 1
        rep.info(
            f"{artist!r}: the original (buggy) algorithm would have filed it "
            f"under [{old[:1]}]; correctly filed under [{new[:1]}]"
        )
    # Starts with 'The' but with no space after it -> must NOT be stripped.
    if re.match(r"(?i)the", artist) and not THE_PREFIX.match(artist):
        rep.info(
            f"{artist!r} starts with 'The' but is not 'The <word>'; "
            f"filed under [{new[:1]}] (correct, name is kept whole)"
        )
    # 'The' followed only by punctuation/symbols, e.g. 'The !!!'
    if THE_PREFIX.match(artist) and not key(stripped):
        found += 1
        rep.warn(f"{artist!r}: nothing left after dropping 'The'; falling back to [{new[:1]}]")

    c_locale = ascii_key(stripped) or ascii_key(artist) or "UNKNOWN"
    if not key(artist):
        found += 1
        rep.warn(f"{artist!r} has no letters/digits; filed under UNKNOWN")
    elif c_locale[:1] != new[:1]:
        found += 1
        rep.warn(
            f"{artist!r}: first character is non-ASCII; folder [{new[:1]}] "
            f"in a UTF-8 locale but [{c_locale[:1]}] in a C locale "
            f"(`cut -c1-1` can split multi-byte characters)"
        )
    if new[:1].isdigit():
        rep.info(f"{artist!r} is filed under digit folder [{new[:1]}]")
    return found


def check_sorting(root: str, show_letters: bool, rep: Reporter) -> None:  # noqa: FBT001
    """Check how each artist maps to a first-letter folder."""
    heading("Sorting: artist -> folder")
    artists = [a for a, _ in iter_dirs(root)]
    letters = Counter(artist_folder(a)[:1] for a in artists)
    found = sum(audit_artist_name(a, rep) for a in artists)

    if not found:
        print("  no sorting problems found")
    if show_letters:
        print("\n  Artists per folder:")
        for letter in sorted(letters):
            print(f"    {letter}: {letters[letter]}")
    print(f"  ({len(artists)} artists checked)")


def scan_album(
    artist: str, album: str, path: str, rep: Reporter, exts: Counter[str]
) -> tuple[int, int]:
    """Inspect one album folder; return (copyable file count, .movpkg bundle count)."""
    copyable = 0
    bundles = 0
    for name in sorted(os.listdir(path)):
        if name.startswith("."):
            continue
        entry = os.path.join(path, name)
        if os.path.isdir(entry):
            if name.endswith(".movpkg"):
                bundles += 1
            else:
                rep.warn(f"unexpected subfolder (not copied): {artist}/{album}/{name}")
            continue
        copyable += 1
        ext = os.path.splitext(name)[1].lower()
        exts[ext or "(none)"] += 1
        if ext not in AUDIO_EXTS:
            rep.warn(f"non-audio file will be copied: {artist}/{album}/{name}")
    return copyable, bundles


def scan_artist(
    artist: str,
    path: str,
    rep: Reporter,
    exts: Counter[str],
    movpkg_albums: list[tuple[str, str, int, int]],
) -> int:
    """Inspect one artist folder; return how many albums it holds."""
    n_albums = 0
    album_keys: defaultdict[str, list[str]] = defaultdict(list)

    for entry in sorted(os.listdir(path)):
        full = os.path.join(path, entry)
        if entry.startswith("."):
            continue
        if not os.path.isdir(full):
            rep.warn(f"file directly in artist folder (ignored): {artist}/{entry}")
            continue
        n_albums += 1
        album_keys[key(entry) or "UNKNOWN"].append(entry)
        if not key(entry):
            rep.warn(f"{artist} / {entry!r}: no letters/digits; filed under UNKNOWN")

        copyable, bundles = scan_album(artist, entry, full, rep, exts)
        if bundles:
            movpkg_albums.append((artist, entry, bundles, copyable))
        elif copyable == 0:
            rep.warn(f"{artist} / {entry}: folder is empty; nothing to copy")

    for k, names in album_keys.items():
        if len(names) > 1:
            rep.error(f"{artist}: albums {names} all map to folder {k!r} and would be merged")
    return n_albums


def report_movpkg(movpkg_albums: list[tuple[str, str, int, int]], rep: Reporter) -> None:
    """Report albums that hold Apple Music .movpkg bundles, which are not copied."""
    if not movpkg_albums:
        print("  no .movpkg bundles found")
        return
    only = [m for m in movpkg_albums if m[3] == 0]
    print(
        f"\n  Albums containing .movpkg bundles (Apple Music streaming/offline "
        f"packages; NOT copied): {len(movpkg_albums)}"
    )
    print(f"  ...of which have no real audio files at all (nothing copied): {len(only)}")
    for artist, album, bundles, files in movpkg_albums:
        tag = "NOTHING COPIED" if files == 0 else f"{files} file(s) copied"
        rep.warn(f"{artist} / {album}: {bundles} .movpkg bundle(s), {tag}")


def check_structure(root: str, rep: Reporter) -> None:
    """Check the library layout for things that confuse the copy script."""
    heading("Structure")
    artist_keys: defaultdict[str, list[str]] = defaultdict(list)
    movpkg_albums: list[tuple[str, str, int, int]] = []
    exts: Counter[str] = Counter()
    n_artists = 0
    n_albums = 0

    for entry in os.listdir(root):
        if not entry.startswith(".") and not os.path.isdir(os.path.join(root, entry)):
            rep.warn(f"loose file at top level (ignored by the script): {entry!r}")

    for artist, artist_path in iter_dirs(root):
        if artist.startswith("."):
            continue
        n_artists += 1
        artist_keys[artist_folder(artist)].append(artist)
        n_albums += scan_artist(artist, artist_path, rep, exts, movpkg_albums)

    for k, names in artist_keys.items():
        if len(names) > 1:
            rep.warn(
                f"artists {names} map to the same folder {k!r} and will be "
                f"merged (fine if intentional)"
            )

    report_movpkg(movpkg_albums, rep)
    print(
        f"\n  {n_artists} artists, {n_albums} albums, {sum(exts.values())} files. "
        f"Extensions: {dict(exts)}"
    )


def norm_title(filename: str) -> str:
    """Lower-case a file stem and strip accents and punctuation."""
    stem = os.path.splitext(filename)[0]
    d = unicodedata.normalize("NFKD", stem)
    d = "".join(c for c in d if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", d.lower())


def norm_title_no_suffix(filename: str) -> str:
    """Normalise a title, also ignoring the ' 1' / ' 2' suffix Music adds on a clash."""
    stem = re.sub(r"\s+\d+$", "", os.path.splitext(filename)[0])
    return norm_title(stem)


def title_duplicates(
    artist: str, album: str, album_path: str, names: list[str], rep: Reporter
) -> int:
    """Report tracks in one album whose titles match; return how many groups."""
    by_title: defaultdict[str, list[str]] = defaultdict(list)
    for name in names:
        by_title[norm_title_no_suffix(name)].append(name)
    found = 0
    for group in by_title.values():
        if len(group) > 1:
            found += 1
            sizes = ", ".join(
                f"{n!r}={os.path.getsize(os.path.join(album_path, n)) // 1024 // 1024}MB"
                for n in group
            )
            rep.error(f"{artist} / {album}: same title -> {sizes}")
    return found


def read_position(path: str) -> tuple[int, int]:
    """Return (disc, track) from an M4A file's tags; 0 means not set."""
    tags: MP4Tags | dict[str, list[tuple[int, int]]] = MP4(path).tags or {}
    trkn = (tags.get("trkn") or [(0, 0)])[0][0]
    disk = (tags.get("disk") or [(0, 0)])[0][0]
    return disk, trkn


def position_clashes(artist: str, album: str, tracks: list[tuple[str, str]], rep: Reporter) -> int:
    """Report different songs sharing one disc+track number; return how many."""
    by_pos: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
    for name, path in tracks:
        if not name.lower().endswith(MP4_EXTS):
            continue
        try:
            disk, trkn = read_position(path)
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the audit
            rep.warn(f"{artist} / {album} / {name}: could not read tags ({type(exc).__name__})")
            continue
        if trkn:
            by_pos[(disk, trkn)].append(name)

    found = 0
    for (disk, trkn), names in by_pos.items():
        titles = {norm_title_no_suffix(n) for n in names}
        if len(names) > 1 and len(titles) > 1:
            found += 1
            rep.warn(
                f"{artist} / {album}: disc {disk} track {trkn} is shared by "
                f"different songs {names} (missing or wrong disc numbers? "
                f"may play out of order)"
            )
    return found


def check_duplicates(root: str, use_tags: bool, rep: Reporter) -> None:  # noqa: FBT001
    """Look for copies of the same track inside each album."""
    heading("Duplicate-looking tracks inside an album")
    found = 0
    for artist, artist_path in iter_dirs(root):
        if artist.startswith("."):
            continue
        for album, album_path in iter_dirs(artist_path):
            tracks = list(iter_audio(album_path))
            found += title_duplicates(artist, album, album_path, [n for n, _ in tracks], rep)
            if use_tags:
                found += position_clashes(artist, album, tracks, rep)
    if not found:
        print("  no duplicate-looking tracks found")


def main() -> int:
    """Run the requested checks and return the process exit status."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("library", nargs="?", default=DEFAULT_LIBRARY)
    parser.add_argument(
        "--check",
        default=",".join(ALL_CHECKS),
        help="comma-separated: " + ",".join(ALL_CHECKS),
    )
    parser.add_argument(
        "--tags",
        action="store_true",
        help="duplicates: also read M4A tags to find clashing disc/track numbers",
    )
    parser.add_argument(
        "--letters",
        action="store_true",
        help="sorting: print how many artists land in each folder",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.library):
        sys.exit(f"Library not found: {args.library}")
    wanted = {c.strip() for c in args.check.split(",") if c.strip()}
    unknown = wanted - set(ALL_CHECKS)
    if unknown:
        sys.exit(f"Unknown check(s): {', '.join(sorted(unknown))}")

    rep = Reporter()
    print(f"Library: {args.library}")
    if "sorting" in wanted:
        check_sorting(args.library, args.letters, rep)
    if "structure" in wanted:
        check_structure(args.library, rep)
    if "duplicates" in wanted:
        check_duplicates(args.library, args.tags, rep)

    print(f"\n{'FAILED' if rep.errors else 'OK'}: {rep.errors} error-level finding(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
