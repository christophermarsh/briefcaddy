"""Puts the take together: the recorded frames, timed as they were painted,
become a 30 fps H.264 video; each scene's voiceover starts when its scene
started; out comes one MP4.

    python tools/demo_video/assemble.py --take ~/demo_video/take --audio ~/demo_video/audio --out demo.mp4
"""

import argparse
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", type=Path, required=True)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    take, audio = args.take.expanduser(), args.audio.expanduser()
    frames = json.loads((take / "frames.json").read_text())
    scenes = json.loads((take / "scenes.json").read_text())
    t0, t_end = scenes[0]["start"], scenes[-1]["start"]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    # frames -> a concat list with each frame held until the next one was painted
    frames = sorted(frames, key=lambda f: f[1])
    before = [f for f in frames if f[1] <= t0]
    shown = ([(before[-1][0], t0)] if before else []) + [f for f in frames if t0 < f[1] < t_end]
    lines = []
    for (name, ts), nxt in zip(shown, shown[1:] + [(None, t_end)]):
        lines += [f"file '{take / 'frames' / name}'", f"duration {max(nxt[1] - ts, 0.001):.4f}"]
    lines.append(f"file '{take / 'frames' / shown[-1][0]}'")  # the concat demuxer drops the last entry's duration
    (take / "frames.txt").write_text("\n".join(lines) + "\n")

    # voiceover: each scene's line placed at its scene's start
    voiced = [s for s in scenes if (audio / f"{s['id']}.wav").exists()]
    inputs, filters = [], []
    for i, s in enumerate(voiced):
        inputs += ["-i", str(audio / f"{s['id']}.wav")]
        delay = int(round((s["start"] - t0) * 1000)) + 250
        filters.append(f"[{i + 1}:a]aresample=48000,adelay={delay}|{delay}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(voiced))) + f"amix=inputs={len(voiced)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[voice]"
    duration = t_end - t0
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(take / "frames.txt"), *inputs,
           "-filter_complex", ";".join(filters + [mix]) + ";[0:v]fps=30,scale=1920:1080:flags=lanczos,format=yuv420p[v]",
           "-map", "[v]", "-map", "[voice]", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-t", f"{duration:.2f}", "-movflags", "+faststart", str(args.out)]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"{args.out}: {duration:.1f} s")


if __name__ == "__main__":
    main()
