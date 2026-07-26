#!/usr/bin/env bash
# Emit Jellyfin media library folder sizes as a node_exporter textfile metric.
# Written by ansible (monitoring/jellyfin_library_size.yml). Runs on emperor.
set -euo pipefail

TEXTFILE_DIR=/var/lib/node_exporter/textfile
OUT="${TEXTFILE_DIR}/jellyfin_library_size.prom"
TMP="$(mktemp "${OUT}.XXXXXX")"
BASE=/data/jellyfin

# Media libraries to report (folder name under $BASE == library label).
# Operational dirs (completed, incomplete, metadata, transmission-home, …) are excluded.
LIBRARIES=(Movies TV Music Documentaries Kids-Movies Stand-Up Ebooks)

{
  echo "# HELP jellyfin_library_bytes Size in bytes of a Jellyfin media library folder on disk."
  echo "# TYPE jellyfin_library_bytes gauge"
  for lib in "${LIBRARIES[@]}"; do
    dir="${BASE}/${lib}"
    [ -d "$dir" ] || continue
    bytes="$(du -sb "$dir" 2>/dev/null | cut -f1)"
    [ -n "$bytes" ] || continue
    printf 'jellyfin_library_bytes{library="%s"} %s\n' "$lib" "$bytes"
  done
} > "$TMP"

chmod 644 "$TMP"
mv "$TMP" "$OUT"
