"""A narrator voice without borrowing anyone's: Kokoro's synthetic male voices
read a reference passage, Chatterbox speaks a test line in each, and the
median pitch of every result is measured (an adult male voice sits around
85-155 Hz). Writes ref_<voice>.wav and cb_<voice>.wav to the out folder.

    python tools/demo_video/voice_ref.py <out dir> am_michael am_onyx bm_george
"""

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch
import torchaudio as ta

REFERENCE = ("Good afternoon. This is a short recording of my speaking voice. I'm reading slowly and clearly, "
             "with a calm and steady tone, the way a narrator would introduce a new product to a room of attorneys.")
TEST = "Everything she sends is read automatically, on the firm's own computers."


def pitch(path: Path) -> float:
    y, sr = librosa.load(str(path), sr=16000)
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
    return float(np.nanmedian(f0[voiced])) if voiced.any() else float("nan")


def main() -> None:
    out = Path(sys.argv[1]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    from chatterbox.tts import ChatterboxTTS
    from kokoro import KPipeline

    pipes = {}
    model = ChatterboxTTS.from_pretrained(device="cuda")
    for voice in sys.argv[2:]:
        lang = voice[0]  # a = American, b = British
        pipes.setdefault(lang, KPipeline(lang_code=lang))
        audio = torch.cat([torch.as_tensor(a) for _, _, a in pipes[lang](REFERENCE, voice=voice, speed=0.95)])
        ref = out / f"ref_{voice}.wav"
        sf.write(str(ref), audio.numpy(), 24000)
        wav = model.generate(TEST, audio_prompt_path=str(ref), exaggeration=0.35, cfg_weight=0.45)
        test = out / f"cb_{voice}.wav"
        ta.save(str(test), wav, model.sr)
        print(f"{voice:12} reference {pitch(ref):6.1f} Hz   chatterbox {pitch(test):6.1f} Hz")


if __name__ == "__main__":
    main()
