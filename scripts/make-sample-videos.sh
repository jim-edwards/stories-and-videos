#!/usr/bin/env bash
# Makes a few tiny demo videos (with titles and descriptions inside the files) so you can try the
# app without real content:   scripts/make-sample-videos.sh [output-folder]
set -euo pipefail

out="${1:-sample-videos}"
command -v ffmpeg >/dev/null || { echo "ffmpeg is needed to make sample videos" >&2; exit 1; }

# Prefer x264; fall back to OpenH264 where that's what the ffmpeg build offers.
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then codec=libx264; else codec=libopenh264; fi

make() {  # make <file> <title> <description> <track> <seconds> <colour>
  local file="$out/$1" title="$2" desc="$3" track="$4" secs="$5" colour="$6"
  mkdir -p "$(dirname "$file")"
  ffmpeg -v error -y \
    -f lavfi -i "color=c=$colour:s=640x360:r=25:d=$secs,drawtext=text='$title':fontcolor=white:fontsize=36:x=(w-text_w)/2:y=(h-text_h)/2" \
    -f lavfi -i "sine=frequency=$((300 + track * 60)):d=$secs" \
    -c:v "$codec" -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart \
    -metadata title="$title" -metadata description="$desc" -metadata track="$track" "$file"
}

make "The Farm Years/01 - The Barn Cat.mp4"    "The Barn Cat"       "Grandpa tells how a stray cat moved into the barn and never left."  1 20 darkgreen
make "The Farm Years/02 - Harvest Time.mp4"    "Harvest Time"       "The whole family helps bring in the corn in the autumn of 1962."    2 20 saddlebrown
make "The Farm Years/03 - The Big Storm.mp4"   "The Big Storm"      "The night the wind took the roof off the old hay shed."             3 20 midnightblue
make "The Farm Years/04 - Market Day.mp4"      "Market Day"         "Selling eggs and honey in town every Saturday morning."             4 20 darkred
make "The Farm Years/05 - Christmas Morning.mp4" "Christmas Morning" "A snowy Christmas on the farm, with pie for everyone."            5 20 teal
make "Grandma's Kitchen/01 - Apple Pie.mp4"    "Apple Pie"          "Grandma's secret to a flaky crust."                                 1 15 firebrick
make "Grandma's Kitchen/02 - Sunday Soup.mp4"  "Sunday Soup"        "A big pot of chicken soup, just like she always made."              2 15 goldenrod
make "Short Tales/The Lighthouse.mp4"          "The Lighthouse"     "A short tale about the lighthouse keeper and his lamp."             0 10 slategray
make "Short Tales/The Little Boat.mp4"         "The Little Boat"    "A short tale about a little boat that wanted to see the sea."       0 10 steelblue

"$(dirname "$0")/make-thumbnails.sh" "$out" >/dev/null
echo "Made sample videos (with pictures) in $out/"
