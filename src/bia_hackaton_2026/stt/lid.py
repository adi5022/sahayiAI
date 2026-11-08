"""Local spoken-language identification (no Sarvam credits): speechbrain/lang-id-voxlingua107-ecapa, 107 languages, CPU.

decide() turns the model's probabilities into a route:
  "en"     English is clearly ahead            -> transcribe with Whisper
  "indic"  Malayalam/Tamil/Telugu/Kannada lead -> transcribe with Sarvam
  "unsure" neither                             -> Sarvam as well (mixed speech is the hard case, so take the careful route)
Clips shorter than MIN_SECONDS are unreliable for this model and should not be trusted.
"""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = ROOT / "data" / "models" / "lid-voxlingua107"
INDIC = ("ml", "ta", "te", "kn")
EN_CONFIDENT = 0.80
MIN_SECONDS = 3.0
MAX_SECONDS = 15     # the first seconds are enough, and it keeps the check fast
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".webm", ".aac", ".flac", ".mp4"}


def load_model():
    """Returns (classifier, language codes in model order). The first call downloads the model (~100 MB) to data/models."""
    import torch
    from speechbrain.inference.classifiers import EncoderClassifier
    clf = EncoderClassifier.from_hparams(source="speechbrain/lang-id-voxlingua107-ecapa", savedir=str(MODEL_DIR), run_opts={"device": "cpu"})
    codes = [str(clf.hparams.label_encoder.decode_ndim(torch.tensor(i))).split(":")[0] for i in range(107)]
    return clf, codes


def load_16k(path: str | Path):
    """Any audio file -> mono 16 kHz float32 (first MAX_SECONDS), via ffmpeg."""
    import numpy as np
    import soundfile as sf
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-t", str(MAX_SECONDS), str(out)], check=True)
        data, _ = sf.read(out, dtype="float32")
    return data.astype(np.float32)


def predict(model, signal) -> dict:
    """signal: mono 16 kHz float32 numpy array. Returns {probs: {code: p}, top: [(code, p)...], seconds, infer_s}."""
    import time
    import torch
    clf, codes = model
    sig = torch.from_numpy(signal)[None]
    t = time.time()
    p = clf.classify_batch(sig)[0].exp()[0]
    probs = {codes[i]: float(p[i]) for i in range(len(codes))}
    return {"probs": probs, "top": sorted(probs.items(), key=lambda kv: -kv[1])[:3], "seconds": signal.shape[0] / 16000, "infer_s": time.time() - t}


def decide(probs: dict[str, float], en_confident: float = EN_CONFIDENT) -> str:
    if probs.get("en", 0) >= en_confident:
        return "en"
    if sum(probs.get(k, 0) for k in INDIC) >= 0.5:
        return "indic"
    return "unsure"
