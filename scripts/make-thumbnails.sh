#!/bin/sh
# Saves a picture next to every video that doesn't have one yet, using the same name:
#     Apple Pie.mp4  ->  Apple Pie.jpg
# Existing pictures (.jpg, .jpeg or .png) are never touched, so it is safe to run again whenever
# you add videos. Needs ffmpeg and a folder you can write to.
#
#     scripts/make-thumbnails.sh /path/to/videos
set -eu

dir="${1:-}"
[ -d "$dir" ] || { echo "Usage: $0 /path/to/videos" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is needed to make pictures" >&2; exit 1; }
dir=$(cd "$dir" && pwd)

# Skip hidden files and NAS housekeeping folders, like the app does.
find "$dir" \( -name '.*' -o -name '@*' -o -name '#*' \) -prune -o -type f \
     \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mov' -o -iname '*.webm' -o -iname '*.mkv' -o -iname '*.ogv' \) \
     -exec sh -c '
  made=0; skipped=0; failed=0
  for video in "$@"; do
    base="${video%.*}"
    have=""
    for ext in jpg jpeg png JPG JPEG PNG; do [ -e "$base.$ext" ] && have=1; done
    if [ -n "$have" ]; then skipped=$((skipped + 1)); continue; fi

    # Take the frame a tenth of the way in (at most a minute), which usually skips black intros.
    duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$video" 2>/dev/null || true)
    seek=$(awk -v d="${duration:-0}" "BEGIN { s = d * 0.1; if (s > 60) s = 60; if (s < 0) s = 0; printf \"%.1f\", s }")
    if ffmpeg -v error -y -ss "$seek" -i "$video" -frames:v 1 -vf scale=640:-2 -q:v 3 "$base.jpg" </dev/null \
       && [ -s "$base.jpg" ]; then
      made=$((made + 1)); echo "made    $base.jpg"
    else
      rm -f "$base.jpg"; failed=$((failed + 1)); echo "FAILED  $video" >&2
    fi
  done
  echo "Done: $made made, $skipped already had one, $failed failed."
' sh {} +
