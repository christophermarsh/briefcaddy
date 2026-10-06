"""A contact sheet of the finished video (one frame every few seconds), to
check a take at a glance.

    python tools/demo_video/sheet.py demo.mp4 sheet.png [every_seconds]
"""

import subprocess
import sys

import imageio_ffmpeg

video, out = sys.argv[1], sys.argv[2]
every = float(sys.argv[3]) if len(sys.argv) > 3 else 3.5
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video,
                "-vf", f"fps=1/{every},scale=384:-1,tile=5x5:padding=4", "-frames:v", "1", out], check=True)
print(out)
