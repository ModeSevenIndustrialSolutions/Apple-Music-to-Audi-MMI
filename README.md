<!--
SPDX-FileCopyrightText: 2026 Matthew Watkins <mwatkins@linuxfoundation.org>
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Apple Music to Audi MMI

Scripts that take your Apple Music (iTunes) library on a Mac and copy it to
an SD card or USB thumb drive in the folder layout the Audi MMI browses
best.

> **Tested on Audi RS models from model year 2022 onwards.** Nobody has
> tried it on other Audi models or MMI generations, and they may behave
> differently. Reports from other cars are welcome.

## What is in this repository

| Script             | What it does                                         |
| ------------------ | ---------------------------------------------------- |
| `audit-library.py` | Finds sorting, structure and duplicate-track faults  |
| `check-artwork.py` | Finds files with no artwork embedded in the file     |
| `audi-mmi.sh`      | Copies the library to the card in the MMI layout     |

None of the three scripts changes your library. The two Python scripts
read it, and `audi-mmi.sh` reads it and writes to the card.

## Why these scripts exist

Apple Music and the MMI treat a music library differently:

- The MMI shows artwork when the image sits inside the audio file itself.
  Apple Music can show artwork stored once for the whole album.
- The MMI browses by folder, so the folder an artist lands in decides how
  fast you find them.
- Apple Music streaming and offline downloads arrive as protected packages
  (`.movpkg`), not audio files. A card cannot hold them.

## What you need

- A Mac with the Music app and the library on the local disk.
- An SD card or USB thumb drive, named `MUSIC`, that your car can read.
  Check your owner's manual for the supported file systems and sizes.
- [`uv`](https://docs.astral.sh/uv/) to run the Python scripts. It fetches
  Python 3.11 or newer and the `mutagen` library on first run.

The scripts expect the standard library location:
`~/Music/Music/Media.localized/Music`, laid out as `Artist/Album/Track`.

## How to put Apple Music on the card

### 1. Check the library

```sh
./audit-library.py --tags
```

This reports:

| Check        | Finds                                                  |
| ------------ | ------------------------------------------------------ |
| `sorting`    | Artists that land in the wrong first-letter folder     |
| `structure`  | Name clashes, loose files, `.movpkg` bundles           |
| `duplicates` | Repeated titles in an album, and clashing track tags   |

Useful options:

```sh
./audit-library.py --check sorting     # run one check
./audit-library.py --letters           # count artists per letter
./audit-library.py /path/to/Music      # audit a different library
```

The script exits with status 1 if it finds an `ERROR`. Warnings and
information messages do not fail it.

Problems to act on:

- **`.movpkg` folders** are streaming packages. An album that holds
  nothing else copies nothing. Buy it or rip it to get it on the card.
- **A numeric suffix on a file name** (for example
  `01 Strange Days 1.m4a`) means Music found a name clash. Remove the
  extra track inside Music with Delete from Library, not in Finder.
- **Accent variants** (`Café` and `Cafe`) tend to appear after you edit
  tags or artwork. Remove the stale one inside Music.

### 2. Check the artwork

```sh
./check-artwork.py                             # print a report
./check-artwork.py --csv missing-artwork.csv   # also write a CSV
```

The script reads the artwork from each file (the `covr` atom in M4A, the
`APIC` frame in MP3). It reports albums with no embedded artwork, albums
where some tracks lack it, and files it cannot read. It exits with
status 1 if any file has no artwork.

To fix an album, open Music, select its tracks, choose Get Info, open the
Artwork tab and set the image. Music then writes the image into every
selected file. Run the check again afterwards.

### 3. Copy to the card

Insert the card, make sure it mounts as `/Volumes/MUSIC`, then run:

```sh
./audi-mmi.sh
```

The script copies each track to:

```text
/Volumes/MUSIC/<letter>/<ARTIST>/<ALBUM>/<counter>.<ext>
```

For example, `The Beatles / Abbey Road` becomes:

```text
/Volumes/MUSIC/B/BEATLES/ABBEYROAD/00000042.m4a
```

How it works:

1. It stops at once if the card is not mounted or the library is missing.
2. It lists every `Artist/Album` folder and skips hidden folders.
3. It builds the artist folder name from its letters and digits, in upper
   case. A leading whole word "The" gets dropped, so `The Beatles` files
   under `B`, while `Them Crooked Vultures` and `Thelonious Monk` stay
   under `T`.
4. The first letter of that name becomes the top-level folder.
5. It builds the album folder name the same way. Names with no letters or
   digits become `UNKNOWN`.
6. It copies the plain files in each album folder, in file-name order, and
   gives each a running eight-digit counter as its new name. The counter
   keeps names unique and in track order. Titles and artwork come from the
   tags inside each file.
7. It skips `.movpkg` bundles, which are folders, and hidden files such as
   `.DS_Store`.
8. It reports each failed copy (for example a full card) and exits with
   status 1 if any copy failed.

The script uses plain POSIX `sh`, so it needs no extra shell.

### 4. Use the card in the car

Eject the card in Finder before you remove it, then insert it into the car
and pick the card as the media source.

## Keeping the folder logic in step

The folder-name rules live in two places: `make_key` and the "The" rule in
`audi-mmi.sh`, and `key` and `artist_folder` in `audit-library.py`. Change
both together, or the audit will report folders the copy never creates.

## Development

The repository uses [`prek`](https://github.com/j178/prek) to run its
linters on every commit:

```sh
prek install
prek run --all-files
```

The hooks cover Python (`ruff`, `mypy`, `basedpyright`), shell
(`shellcheck`), Markdown (`markdownlint`, `write-good`), spelling
(`codespell`), commit messages (`gitlint`), secrets (`gitleaks`), licence
compliance (`reuse`) and code quality (`aislop`).

Commits need a DCO sign-off (`git commit -s`).

## Licence

This project is free software under the
[GNU General Public License, version 3 or later](LICENSE)
(`GPL-3.0-or-later`).

You may use, study, change and share it. If you share a modified copy or
build something that includes this code, you must release it under the same
licence and make the source available. Forks keep this licence.

The repository follows the [REUSE](https://reuse.software/) specification:
each file names its licence and copyright, and the full licence texts sit in
`LICENSES/`. Check compliance with `reuse lint`.

This software comes with no warranty. You copy files to a removable card at
your own risk, so keep a backup of your library.
