#!/usr/bin/env bash
# The demo video, end to end (in WSL, from the project folder):
#   bash tools/demo_video/make.sh [output.mp4] [--voice]
#
# 1. fonts (Inter for titles, Kalam for the handwriting; both SIL Open Font License)
# 2. a demo-only world (made-up clients, one with a handwritten paper questionnaire) and its packet pictures
# 3. the voiceover: Chatterbox, speaking in a synthetic male reference voice made by Kokoro (VOICE below);
#    skipped when the audio is already there -- pass --voice to redo it
# 4. a portal and a review app on spare ports (8601, 8486), pointed at that world only
# 5. the recording, then the assembled MP4
# Needs ~/venvs/tts (chatterbox-tts, kokoro, torch cu128) and ~/venvs/video (playwright, imageio-ffmpeg).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:-$HOME/demo_video/i485_demo.mp4}"
W="$HOME/demo_video"
PY="$PWD/.venv-wsl/bin/python3"
VOICE="${VOICE:-am_onyx}"   # Kokoro voice the narrator is modelled on: am_onyx (deep), am_michael, bm_george, ...

mkdir -p "$W/fonts"
GF=https://github.com/google/fonts/raw/main/ofl
[[ -f "$W/fonts/Inter.ttf" ]] || curl -sSfL -o "$W/fonts/Inter.ttf" "$GF/inter/Inter%5Bopsz%2Cwght%5D.ttf"
[[ -f "$W/fonts/Kalam-Regular.ttf" ]] || curl -sSfL -o "$W/fonts/Kalam-Regular.ttf" "$GF/kalam/Kalam-Regular.ttf"

"$PY" tools/demo_video/world.py "$W/world" "$W/fonts/Kalam-Regular.ttf"
"$PY" src/packet.py demo-ana --data "$W/world/clients" --by "Demo Paralegal" >/dev/null
"$PY" tools/demo_video/packet_pages.py "$W/world/clients/demo-ana" "$W/pages" >/dev/null

if [[ "${2:-}" == "--voice" || ! -f "$W/audio/durations.json" ]]; then
  [[ -f "$W/voices/ref_$VOICE.wav" ]] || "$HOME/venvs/tts/bin/python3" tools/demo_video/voice_ref.py "$W/voices" "$VOICE"
  rm -rf "$W/audio"
  "$HOME/venvs/tts/bin/python3" tools/demo_video/tts.py "$W/audio" --voice "$W/voices/ref_$VOICE.wav"
fi

# servers for the recording only; stopped when this script ends
fuser -k 8601/tcp 8486/tcp 2>/dev/null || true
# the reminder scene opens the real link from the dry-run outbox: whole links there, for this local demo only
export PORTAL_OUTBOX_FULL_LINKS=1
PORTAL_DATA="$W/world/portal" PORTAL_BASE_URL=http://localhost:8601 "$PWD/.venv-wsl/bin/uvicorn" portal.app:app --app-dir src --port 8601 --log-level warning &
PORTAL_PID=$!
PORTAL_BASE_URL=http://localhost:8601 "$PY" src/review/server.py --port 8486 --data "$W/world/clients" --portal "$W/world/portal" \
  --users "$W/world/no_users.json" >/dev/null 2>&1 &
REVIEW_PID=$!
trap 'kill $PORTAL_PID $REVIEW_PID 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8486/api/me && curl -sf -o /dev/null http://localhost:8601/ && break; sleep 1; done

LINK=$(PORTAL_DATA="$W/world/portal" PORTAL_BASE_URL=http://localhost:8601 "$PY" src/portal/admin.py link demo-ana | cut -d' ' -f1)
rm -rf "$W/take"
"$HOME/venvs/video/bin/python3" tools/demo_video/record.py --link "$LINK" --review http://127.0.0.1:8486 \
  --audio "$W/audio" --pages "$W/pages" --world "$W/world" --font "$W/fonts/Inter.ttf" --out "$W/take"
"$HOME/venvs/video/bin/python3" tools/demo_video/assemble.py --take "$W/take" --audio "$W/audio" --out "$OUT"
