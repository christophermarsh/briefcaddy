#!/usr/bin/env bash
# The narrator test lines (voice_ref.py) to a folder you can listen to: copy_samples.sh <dest>
dest=$1; mkdir -p "$dest"
for f in "$HOME"/demo_video/voices/cb_*.wav; do cp "$f" "$dest/$(basename "$f" .wav | sed 's/^cb_//').wav"; done
ls "$dest"
