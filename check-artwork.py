#!/usr/bin/env -S uv run --script
# SPDX-FileCopyrightText: 2026 Matthew Watkins <mwatkins@linuxfoundation.org>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# /// script
# requires-python = ">=3.11"
# dependencies = ["mutagen"]
# ///
"""Report audio files that have no artwork embedded in the file itself.

Apple Music can show album art held at the album level, but the Audi MMI
(like most other players) only reads art embedded in each individual file.

Usage:
    ./check-artwork.py [LIBRARY_DIR] [--csv missing-artwork.csv]

LIBRARY_DIR defaults to ~/Music/Music/Media.localized/Music and must use
the iTunes layout: Artist/Album/Track. Exit status is 1 if any file lacks
artwork or cannot be read. Nothing is modified.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from mutagen import File as MutagenFile
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_LIBRARY = os.path.expanduser("~/Music/Music/Media.localized/Music")
AUDIO_EXTS = {".m4a", ".mp3", ".m4p", ".aac", ".flac", ".ogg", ".opus"}
MP4_EXTS = (".m4a", ".m4p", ".aac")

OK, MISSING, ERROR = "ok", "missing", "error"

Track = tuple[str, str, str, str]  # artist, album, filename, full path
Problem = tuple[str, str, str, str, str, str]  # artist, album, track, status, detail, path


def _mp3_status(path: str) -> tuple[str, str]:
    """Return (status, detail) for an MP3: it needs an APIC (picture) frame."""
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        return MISSING, "no ID3 tag"
    return (OK, "") if id3.getall("APIC") else (MISSING, "no APIC frame")


def artwork_status(path: str) -> tuple[str, str]:
    """Return (status, detail) describing embedded artwork for one file.

    Only errors raised by mutagen or the filesystem count as "unreadable";
    anything else is a bug in this script and is allowed to propagate.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in MP4_EXTS:
            tags = MP4(path).tags
            covers = tags.get("covr") if tags else None
            result = (OK, "") if covers else (MISSING, "no covr atom")
        elif ext == ".mp3":
            result = _mp3_status(path)
        else:
            audio = MutagenFile(path)
            if audio is None:
                result = (ERROR, "unrecognised format")
            else:
                pictures = getattr(audio, "pictures", None)
                result = (OK, "") if pictures else (MISSING, "no embedded picture")
    except (MutagenError, OSError) as exc:  # corrupt, truncated or iCloud placeholder
        return ERROR, f"{type(exc).__name__}: {exc}"
    return result


def iter_tracks(root: str) -> Iterator[Track]:
    """Yield (artist, album, track name, path) for every audio file in the library."""
    for artist in sorted(os.listdir(root)):
        artist_path = os.path.join(root, artist)
        if not os.path.isdir(artist_path):
            continue
        for album in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album)
            if not os.path.isdir(album_path):
                continue
            for track in sorted(os.listdir(album_path)):
                path = os.path.join(album_path, track)
                if os.path.isfile(path) and os.path.splitext(track)[1].lower() in AUDIO_EXTS:
                    yield artist, album, track, path


def summarise_albums(
    tracks: list[Track], results: list[tuple[str, str]]
) -> tuple[dict[tuple[str, str], dict[str, int]], list[Problem]]:
    """Tally results per album and collect every file that has a problem."""
    albums: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, OK: 0, MISSING: 0, ERROR: 0}
    )
    problems: list[Problem] = []
    for (artist, album, track, path), (status, detail) in zip(tracks, results, strict=True):
        stats = albums[(artist, album)]
        stats["total"] += 1
        stats[status] += 1
        if status != OK:
            problems.append((artist, album, track, status, detail, path))
    return albums, problems


def print_album_report(
    albums: dict[tuple[str, str], dict[str, int]], problems: list[Problem]
) -> None:
    """Print the per-album findings and any unreadable files."""
    entirely = sorted(k for k, v in albums.items() if v[OK] == 0 and v["total"] > 0)
    partial = sorted(k for k, v in albums.items() if 0 < v[OK] < v["total"])
    print(
        f"Albums: {len(albums)}  entirely without artwork: {len(entirely)}  "
        f"partially: {len(partial)}"
    )

    if entirely:
        print("\n== Albums where NO file has embedded artwork ==")
        for artist, album in entirely:
            print(f"  {artist} / {album}  ({albums[(artist, album)]['total']} files)")
    if partial:
        print("\n== Albums where SOME files lack embedded artwork ==")
        for artist, album in partial:
            v = albums[(artist, album)]
            print(f"  {artist} / {album}  ({v[MISSING] + v[ERROR]} of {v['total']} files)")

    errors = [p for p in problems if p[3] == ERROR]
    if errors:
        print("\n== Unreadable files ==")
        for artist, album, track, _, detail, _ in errors:
            print(f"  {artist} / {album} / {track}: {detail}")


def write_csv(path: str, problems: list[Problem]) -> None:
    """Write every problem file to a CSV report."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["artist", "album", "track", "status", "detail", "path"])
        writer.writerows(problems)
    print(f"\nWrote {len(problems)} rows to {path}")


def main() -> int:
    """Scan the library and return the process exit status."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("library", nargs="?", default=DEFAULT_LIBRARY)
    parser.add_argument("--csv", help="write every problem file to this CSV path")
    args = parser.parse_args()

    if not os.path.isdir(args.library):
        sys.exit(f"Library not found: {args.library}")

    tracks = list(iter_tracks(args.library))
    print(f"Scanning {len(tracks)} files...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: artwork_status(t[3]), tracks))

    albums, problems = summarise_albums(tracks, results)
    totals = {s: sum(1 for r in results if r[0] == s) for s in (OK, MISSING, ERROR)}
    print(
        f"\nFiles: {len(tracks)}  with artwork: {totals[OK]}  "
        f"missing: {totals[MISSING]}  unreadable: {totals[ERROR]}"
    )
    print_album_report(albums, problems)
    if args.csv:
        write_csv(args.csv, problems)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
