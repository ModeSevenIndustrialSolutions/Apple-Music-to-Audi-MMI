#!/bin/sh
# SPDX-FileCopyrightText: 2026 Matthew Watkins <mwatkins@linuxfoundation.org>
# SPDX-License-Identifier: GPL-3.0-or-later

#set -x

# Copy the iTunes/Music library onto a flash card laid out for the Audi MMI:
#   <card>/<first letter>/<ARTIST>/<ALBUM>/<8-digit counter>.<ext>

# Define variables
DST="/Volumes/MUSIC"
SRC="${HOME}/Music/Music/Media.localized/Music"
TMPFILE=$(mktemp) || exit 1
trap 'rm -f "${TMPFILE}"' EXIT
# echo "Temp file: ${TMPFILE}"

# Upper-case alphanumeric-only version of a name, used for folder names.
# Explicit a-z/A-Z is deliberate: [:lower:]/[:upper:] would turn accented
# first letters into multi-byte folder names, which `cut -c` can split
# into broken bytes under a non-UTF-8 locale.
make_key() {
  # shellcheck disable=SC2018,SC2019
  printf '%s' "$1" | tr -cd '[:alnum:]' | tr 'a-z' 'A-Z'
}

# Fail early if the flash card or library is not where we expect
if [ ! -d "${DST}" ]; then
  echo "Destination not found (is the flash card mounted?): ${DST}" >&2
  exit 1
fi
if [ ! -d "${SRC}" ]; then
  echo "Music library not found: ${SRC}" >&2
  exit 1
fi

# Enumerate artists and album titles (as "Artist/Album", skipping hidden
# folders)
cd "${SRC}" || exit 1
find . -mindepth 2 -maxdepth 2 -type d ! -path './.*' | sed 's#^\./##' | sort >"${TMPFILE}"

# Counter used to build unique, zero-padded filenames
COUNTER=0
FAILED=0

while IFS= read -r CONTENT; do
  # Parse artists and album names
  ARTIST="${CONTENT%%/*}"
  ALBUM="${CONTENT#*/}"
  printf 'Artist: %s     Album: %s\n' "${ARTIST}" "${ALBUM}"

  # Define the artist folder name
  # Drop a leading "The" so "The Beatles" is filed under B. This must be
  # matched against the original name, as a whole word ("The" followed by
  # whitespace). Once spaces are stripped, "Them Crooked Vultures" and
  # "Thelonious Monk" look just like "The M..." and "The L...".
  SORT_NAME=$(printf '%s' "${ARTIST}" | sed -E 's/^[Tt][Hh][Ee][[:space:]]+//')
  ARTIST_DIR=$(make_key "${SORT_NAME}")
  if [ -z "${ARTIST_DIR}" ]; then
    # Nothing usable left (e.g. "The !!!"): fall back to the full name,
    # then to a placeholder, so we never write to the top of ${DST}
    ARTIST_DIR=$(make_key "${ARTIST}")
    if [ -z "${ARTIST_DIR}" ]; then
      ARTIST_DIR="UNKNOWN"
    fi
  fi
  # echo "Artist dir: ${ARTIST_DIR}"

  # Define primary (alphanumeric) folder location
  PRIMARY_DIR=$(printf '%s' "${ARTIST_DIR}" | cut -c1-1)
  # echo "Primary folder: ${PRIMARY_DIR}"

  # Create secondary alphabetical album directory
  ALBUM_DIR=$(make_key "${ALBUM}")
  if [ -z "${ALBUM_DIR}" ]; then
    # Titles with no alphanumerics (e.g. non-Latin names in a C locale)
    # would otherwise put the tracks directly in the artist folder
    ALBUM_DIR="UNKNOWN"
  fi
  # echo "Album dir: ${ALBUM_DIR}"

  ALBUM_DST="${DST}/${PRIMARY_DIR}/${ARTIST_DIR}/${ALBUM_DIR}"

  # Copy files from album folder (the glob skips hidden files such as
  # .DS_Store)
  for TRACK_PATH in "${SRC}/${CONTENT}"/*; do
    # Skip anything that is not a plain file (e.g. Apple Music .movpkg
    # bundles are folders and cannot be copied with a plain cp)
    [ -f "${TRACK_PATH}" ] || continue

    # Create the album directory on first use, so albums with nothing
    # to copy do not leave empty folders on the card
    if [ ! -d "${ALBUM_DST}" ]; then
      if ! mkdir -p "${ALBUM_DST}"; then
        echo "ERROR: cannot create ${ALBUM_DST}" >&2
        FAILED=$((FAILED + 1))
        continue
      fi
    fi

    # Increment counter used for filenames
    COUNTER=$((COUNTER + 1))
    EXT="${TRACK_PATH##*/}"
    EXT="${EXT##*.}"
    FILE=$(printf '%08d' "${COUNTER}")
    FILENAME="${FILE}.${EXT}"
    # echo "Source filename: ${TRACK_PATH}"
    # echo "Destination filename: ${ALBUM_DST}/${FILENAME}"
    if ! cp "${TRACK_PATH}" "${ALBUM_DST}/${FILENAME}"; then
      echo "ERROR: failed to copy ${TRACK_PATH}" >&2
      FAILED=$((FAILED + 1))
    fi
  done
done <"${TMPFILE}"

echo "Script completed: ${COUNTER} tracks processed, ${FAILED} failure(s)"
if [ "${FAILED}" -gt 0 ]; then
  exit 1
fi
