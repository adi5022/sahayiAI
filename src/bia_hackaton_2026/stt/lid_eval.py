"""Try a local spoken-language-identification model on your own recordings (no Sarvam credits used).

Put clips in  data/lid_test/english/   data/lid_test/malayalam/   data/lid_test/mixed/   (mp3, m4a, ogg, wav, webm...), then run:

    uv run python -m bia_hackaton_2026.stt.lid_eval

Model: speechbrain/lang-id-voxlingua107-ecapa (107 languages, runs on the CPU). Audio is converted to 16 kHz mono with ffmpeg.
The decision is: "en" if English is clearly ahead, "indic" (-> Sarvam) if Malayalam, Tamil, Telugu or Kannada together are ahead,
otherwise "unsure" (-> Sarvam too, because mixed speech is the hard case). Clips shorter than 3 s are marked unreliable.
"""
import sys
import time

from bia_hackaton_2026.stt.lid import (AUDIO_EXT, INDIC, MIN_SECONDS, ROOT, decide, load_16k, load_model, predict)  # noqa: F401

FOLDER = ROOT / "data" / "lid_test"
EXPECT = {"english": "en", "malayalam": "indic", "mixed": "indic"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    model = load_model()
    print(f"model ready in {time.time() - t0:.1f}s\n")
    right = total = 0
    for folder, want in EXPECT.items():
        for f in sorted((FOLDER / folder).glob("*")):
            if f.suffix.lower() not in AUDIO_EXT:
                continue
            try:
                p = predict(model, load_16k(f))
            except Exception as e:
                print(f"{folder:10} {f.name:28} could not read: {e}")
                continue
            got = decide(p["probs"])
            ok = got == want or (want == "indic" and got == "unsure")   # unsure goes to Sarvam, which is the safe route
            total += 1
            right += ok
            print(f"{folder:10} {f.name[:28]:28} {p['seconds']:5.1f}s -> {got:6} {'ok  ' if ok else 'WRONG'} {p['infer_s']:.2f}s  "
                  + " ".join(f"{c}={v:.2f}" for c, v in p["top"]) + ("  (short clip, unreliable)" if p["seconds"] < MIN_SECONDS else ""))
    print(f"\nroutes the right way: {right}/{total}" if total else f"No clips found in {FOLDER}.")


if __name__ == "__main__":
    main()
