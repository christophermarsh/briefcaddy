#!/usr/bin/env bash
# Stills from a video at given seconds, plus its audio level: frames_at.sh video.mp4 outdir 41 71 ...
FF=$("$HOME/venvs/video/bin/python3" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
video=$1; out=$2; shift 2
for t in "$@"; do "$FF" -y -loglevel error -ss "$t" -i "$video" -frames:v 1 "$out/f$t.png"; done
"$FF" -hide_banner -i "$video" -af volumedetect -f null - 2>&1 | grep -E "Stream|mean_volume|max_volume"
