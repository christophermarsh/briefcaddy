"""The voiceover: one WAV per scene of script.json, read by Chatterbox
(open source, MIT licence, runs on this machine's GPU -- no text leaves it).

    python tools/demo_video/tts.py <out dir> [--voice reference.wav]

--voice: a 10-20 second recording of someone's voice to read in that voice
(only with their permission); without it, Chatterbox's own voice.
Writes <out>/<scene>.wav and <out>/durations.json.
"""

import argparse
import json
from pathlib import Path

import torchaudio as ta

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--voice", type=Path)
    ap.add_argument("--only", nargs="*", help="regenerate just these scenes")
    args = ap.parse_args()
    from chatterbox.tts import ChatterboxTTS

    model = ChatterboxTTS.from_pretrained(device="cuda")
    scenes = json.loads((HERE / "script.json").read_text(encoding="utf-8"))["scenes"]
    args.out.mkdir(parents=True, exist_ok=True)
    durations = json.loads((args.out / "durations.json").read_text()) if (args.out / "durations.json").exists() else {}
    for scene in scenes:
        if args.only and scene["id"] not in args.only:
            continue
        # calm, even narration: a little less expressive and slower than the defaults
        wav = model.generate(scene["say"], audio_prompt_path=str(args.voice) if args.voice else None, exaggeration=0.4, cfg_weight=0.4)
        ta.save(str(args.out / f"{scene['id']}.wav"), wav, model.sr)
        durations[scene["id"]] = round(wav.shape[-1] / model.sr, 2)
        print(scene["id"], durations[scene["id"]], "s")
    (args.out / "durations.json").write_text(json.dumps(durations, indent=1))


if __name__ == "__main__":
    main()
