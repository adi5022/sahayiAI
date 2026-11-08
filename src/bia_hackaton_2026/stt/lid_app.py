"""Record clips and benchmark the local language-identification model.   Run:

    uv run streamlit run src/bia_hackaton_2026/stt/lid_app.py

Clips are saved under data/lid_test/{english,malayalam,mixed}/ (git-ignored). Nothing here calls Sarvam unless you tick the box.
"""
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from bia_hackaton_2026.stt import lid

load_dotenv()
FOLDER = Path(os.environ.get("LID_TEST_DIR") or lid.ROOT / "data" / "lid_test")
LABELS = {"english": "en", "malayalam": "indic", "mixed": "indic"}   # what the router should decide for each folder

PROMPTS = {
    "english": [
        "Good morning, I am feeling fine today and I had my breakfast.",
        "I have a bad pain in my chest and I feel dizzy.",
        "Someone called saying he is from my bank and asked for my OTP.",
        "I fell in the bathroom and I cannot get up.",
        "Free talk for 15 seconds: tell me about your day.",
    ],
    "malayalam": [
        "ഗുഡ് മോർണിംഗ്, ഇന്ന് എനിക്ക് സുഖമാണ്, ഞാൻ പ്രഭാതഭക്ഷണം കഴിച്ചു.",
        "എനിക്ക് നെഞ്ചിൽ വേദനയുണ്ട്, തലകറക്കവും ഉണ്ട്.",
        "ബാങ്കിൽ നിന്നാണെന്ന് പറഞ്ഞ് ഒരാൾ വിളിച്ചു, എന്റെ ഒടിപി ചോദിച്ചു.",
        "ഞാൻ കുളിമുറിയിൽ വീണു, എനിക്ക് എഴുന്നേൽക്കാൻ വയ്യ.",
        "എന്റെ മകൻ ജോലിക്ക് പോയി, ഞാൻ വീട്ടിൽ ഒറ്റയ്ക്കാണ്.",
        "Free talk for 15 seconds in Malayalam about your day.",
    ],
    "mixed": [
        "എനിക്ക് chest pain ഉണ്ട്, and I am feeling dizzy.",
        "ഒരാൾ call ചെയ്തു, he asked for my OTP, ഞാൻ കൊടുത്തില്ല.",
        "Doctor പറഞ്ഞു medicine നിർത്താൻ, but ഞാൻ ഇപ്പോഴും കഴിക്കുന്നുണ്ട്.",
        "ഇന്ന് morning മുതൽ എനിക്ക് fever ഉണ്ട്.",
        "Free talk for 15 seconds, switching between Malayalam and English like you normally do.",
    ],
}
VARIETY = ["a woman's voice and a man's voice", "an older person's voice if you can", "a noisy room (TV or fan on)", "fast and slow speaking",
           "one very short clip (about 2 s)", "one long clip (40 s or more) to test long messages", "silence for 2 s before you start talking"]

st.set_page_config(page_title="Language ID test", page_icon="🎙", layout="wide")
st.title("Language identification: record and benchmark")
st.caption("A local model (SpeechBrain VoxLingua107) decides English or Malayalam from the audio, so Sarvam credits are not spent on English.")


@st.cache_resource(show_spinner="Loading the model (the first run downloads it)...")
def model():
    return lid.load_model()


def clips():
    rows = []
    for folder in LABELS:
        for f in sorted((FOLDER / folder).glob("*")):
            if f.suffix.lower() in lid.AUDIO_EXT:
                rows.append((folder, f))
    return rows


def save(folder: str, name: str, data: bytes, suffix: str) -> Path:
    d = FOLDER / folder
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now():%Y%m%d_%H%M%S}_{name}{suffix}"
    p.write_bytes(data)
    return p


tab_rec, tab_bench, tab_fix = st.tabs(["1. Record clips", "2. Benchmark", "3. Fix labels"])

with tab_rec:
    left, right = st.columns([2, 1])
    with right:
        st.subheader("What to record")
        st.write("Read each line naturally (a paraphrase is fine). Aim for 5 to 10 seconds each.")
        for label, items in PROMPTS.items():
            with st.expander(f"{label.title()} ({len(items)})", expanded=label == "english"):
                for i, t in enumerate(items, 1):
                    st.markdown(f"{i}. {t}")
        with st.expander("Variety to add"):
            for v in VARIETY:
                st.markdown(f"- {v}")
    with left:
        label = st.radio("Language of the clip you are about to record", list(LABELS), horizontal=True)
        note = st.text_input("Short note for the file name (optional)", placeholder="e.g. chest-pain-man").strip().replace(" ", "-")[:30] or "clip"
        rec = st.audio_input("Record", sample_rate=16000, key=f"rec-{label}")
        if rec is not None:
            key = f"saved-{rec.name}-{rec.size}"
            if not st.session_state.get(key):
                p = save(label, note, rec.getvalue(), ".wav")
                st.session_state[key] = str(p)
            st.success(f"Saved {Path(st.session_state[key]).name} in {label}/. Record another to add more.")
        up = st.file_uploader("...or upload recordings (mp3, m4a, ogg, wav, webm)", accept_multiple_files=True, key=f"up-{label}")
        for f in up or []:
            key = f"saved-up-{f.name}-{f.size}-{label}"
            if not st.session_state.get(key):
                st.session_state[key] = str(save(label, Path(f.name).stem[:30], f.getvalue(), Path(f.name).suffix.lower()))
        counts = {k: sum(1 for c in clips() if c[0] == k) for k in LABELS}
        st.write("**Saved so far:** " + " · ".join(f"{k} {v}" for k, v in counts.items()))

with tab_bench:
    c1, c2, c3 = st.columns(3)
    en_thr = c1.slider("English must be at least", 0.5, 0.99, lid.EN_CONFIDENT, 0.01, help="Below this the clip is not routed to Whisper")
    min_s = c2.slider("Ignore clips shorter than (s)", 0.0, 6.0, lid.MIN_SECONDS, 0.5)
    do_whisper = c3.checkbox("Also transcribe with Whisper (free)", value=False)
    do_sarvam = c3.checkbox("Also transcribe with Sarvam (uses credits, about ₹0.5 per minute)", value=False)
    if st.button("Run on all saved clips", type="primary"):
        items = clips()
        if not items:
            st.warning("No clips yet. Record or upload some in the first tab.")
        else:
            m = model()
            rows, bar = [], st.progress(0.0)
            for n, (folder, f) in enumerate(items, 1):
                try:
                    sig = lid.load_16k(f)
                    p = lid.predict(m, sig)
                except Exception as e:
                    rows.append({"folder": folder, "file": f.name, "decision": f"error: {e}"[:80]})
                    continue
                got = lid.decide(p["probs"], en_thr)
                want = LABELS[folder]
                short = p["seconds"] < min_s
                ok = got == want or (want == "indic" and got == "unsure")
                row = {"folder": folder, "file": f.name, "length s": round(p["seconds"], 1), "decision": got, "correct": "short" if short else ("yes" if ok else "NO"),
                       "top 3": "  ".join(f"{c} {v:.2f}" for c, v in p["top"]), "model s": round(p["infer_s"], 2)}
                if do_whisper or do_sarvam:
                    from bia_hackaton_2026.stt.transcribe import transcribe_audio
                    data = f.read_bytes()
                    if do_whisper:
                        row["whisper"] = transcribe_audio(data, f.name, "en")[0][:80]
                    if do_sarvam:
                        row["sarvam"] = transcribe_audio(data, f.name, "ml-mix")[0][:80]
                rows.append(row)
                bar.progress(n / len(items))
            st.session_state["bench"] = pd.DataFrame(rows)
    df = st.session_state.get("bench")
    if df is not None and len(df):
        scored = df[df.get("correct", "").isin(["yes", "NO"])] if "correct" in df else df.iloc[0:0]
        if len(scored):
            st.metric("Routed the right way", f"{(scored.correct == 'yes').sum()} / {len(scored)}")
            st.write("**By folder**")
            st.dataframe(scored.groupby("folder").apply(lambda g: f"{(g.correct == 'yes').sum()} / {len(g)}", include_groups=False).rename("right").to_frame(), width=300)
            st.write(f"Average time per clip: {scored['model s'].mean():.2f} s on this CPU")
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption("Correct means: English -> en; Malayalam or mixed -> indic or unsure (both go to Sarvam, which is the safe route). 'short' = under the length cutoff, not scored.")

with tab_fix:
    st.write("Listen to each clip and set the language it really is. Tick delete to remove a bad recording. Nothing changes until you press Apply.")
    items = clips()
    if not items:
        st.info("No clips yet.")
    else:
        with st.form("fix"):
            choices = {}
            for folder, f in items:
                a, b, c = st.columns([3, 2, 1])
                a.audio(str(f))
                a.caption(f"{folder}/{f.name}")
                choices[f] = (b.radio("Language", list(LABELS), index=list(LABELS).index(folder), horizontal=True, key=f"lab-{f}", label_visibility="collapsed"),
                              c.checkbox("Delete", key=f"del-{f}"))
            if st.form_submit_button("Apply", type="primary"):
                moved = deleted = 0
                for f, (new, delete) in choices.items():
                    if delete:
                        f.unlink(missing_ok=True)
                        deleted += 1
                    elif new != f.parent.name:
                        (FOLDER / new).mkdir(parents=True, exist_ok=True)
                        f.rename(FOLDER / new / f.name)
                        moved += 1
                st.success(f"Moved {moved}, deleted {deleted}. Rerun the benchmark.")
                st.session_state.pop("bench", None)
                st.rerun()
