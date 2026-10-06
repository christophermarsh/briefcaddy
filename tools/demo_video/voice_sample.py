"""Quick voice check: one sentence from Chatterbox and from Kokoro."""
import sys
import time
from pathlib import Path

import torch
import torchaudio as ta

out = Path(sys.argv[1]).expanduser()
out.mkdir(parents=True, exist_ok=True)
line = "Georges Cote has about eighteen hundred clients who need an I-485 filed by November first. Here is how this system gets them there."

t = time.time()
from chatterbox.tts import ChatterboxTTS
model = ChatterboxTTS.from_pretrained(device="cuda")
wav = model.generate(line, exaggeration=0.45, cfg_weight=0.45)
ta.save(str(out / "chatterbox.wav"), wav, model.sr)
print("chatterbox", round(time.time() - t, 1), "s, audio", round(wav.shape[-1] / model.sr, 1), "s")

t = time.time()
import soundfile as sf
from kokoro import KPipeline
pipe = KPipeline(lang_code="a")
for voice in ("af_heart", "am_michael"):
    audio = torch.cat([torch.as_tensor(a) for _, _, a in pipe(line, voice=voice)])
    sf.write(str(out / f"kokoro_{voice}.wav"), audio.numpy(), 24000)
print("kokoro", round(time.time() - t, 1), "s")
