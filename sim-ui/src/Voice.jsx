import { useCallback, useEffect, useRef, useState } from "react";

export const LANG_NAMES = { ml: "Malayalam", en: "English", "ml-mix": "Malayalam + English", unknown: "Unknown" };
const CHOICES = [["auto", "Auto"], ["en", "English"], ["ml", "Malayalam"]];
const MIN_SECONDS = 0.6;        // shorter than this counts as an accidental tap
const SEGMENT_MS = 180000;      // pieces of 3 minutes: the server uses Sarvam's batch API for anything over 30 s, so no cutting mid-word
const TRANSCRIBE_TIMEOUT = 90;  // seconds before giving up, so the screen can never hang
const LANG_KEY = "sahayi-sim-voice-lang";

// Click-to-record (click again, or press Done, to finish).
// phase: idle | recording | transcribing | review | error. The recorded audio is kept, so a failed or wrong transcription can be retried.
export function useVoice() {
  const [phase, setPhase] = useState("idle");
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState(null);   // { text, language, provider, seconds }
  const [error, setError] = useState("");
  const [lang, setLangState] = useState(() => { try { return localStorage.getItem(LANG_KEY) || "auto"; } catch { return "auto"; } });
  const r = useRef({});                          // stream, audio context, analyser, segments, timers
  const audio = useRef([]);                      // finished segments [{ blob, seconds }], kept for retry
  const abort = useRef(null);
  const langRef = useRef(lang);
  langRef.current = lang;

  const setLang = useCallback((l) => { setLangState(l); try { localStorage.setItem(LANG_KEY, l); } catch { /* ignore */ } }, []);

  const cleanup = useCallback(() => {
    const s = r.current;
    clearTimeout(s.timer);
    s.stream?.getTracks().forEach((t) => t.stop());
    s.ctx?.close().catch(() => {});
    r.current = {};
  }, []);

  const beginSegment = useCallback(() => {
    const s = r.current;
    const rec = new MediaRecorder(s.stream, s.mime ? { mimeType: s.mime } : undefined);
    const seg = { chunks: [], t0: performance.now(), seconds: 0, blob: null };
    seg.finished = new Promise((res) => {
      rec.onstop = () => { seg.seconds = (performance.now() - seg.t0) / 1000; seg.blob = new Blob(seg.chunks, { type: s.mime || "audio/webm" }); res(seg); };
    });
    rec.ondataavailable = (e) => e.data.size && seg.chunks.push(e.data);
    rec.start(250);
    s.segs.push(seg);
    s.cur = rec;
    s.timer = setTimeout(() => {          // roll over to a new segment without a noticeable gap
      if (!r.current.cur) return;
      const old = r.current.cur;
      beginSegment();
      old.stop();
    }, SEGMENT_MS);
  }, []);

  const start = useCallback(async () => {
    if (r.current.stream) return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.55;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
      r.current = { stream, ctx, analyser, mime, segs: [], t0: performance.now() };
      audio.current = [];
      beginSegment();
      setElapsed(0);
      setPhase("recording");
    } catch (e) {
      cleanup();
      setError(e.name === "NotAllowedError" ? "Microphone access was blocked. Allow it for this site and try again." : `Could not start the microphone: ${e.message}`);
      setPhase("error");
    }
  }, [beginSegment, cleanup]);

  const transcribe = useCallback(async () => {
    const parts = audio.current;
    if (!parts.length) { setError("There is no recording to transcribe. Record again."); setPhase("error"); return; }
    setPhase("transcribing");
    const ctl = new AbortController();
    abort.current = ctl;
    const timer = setTimeout(() => ctl.abort("timeout"), TRANSCRIBE_TIMEOUT * 1000);
    try {
      const f = new FormData();
      parts.forEach((p, i) => f.append("audio", p.blob, `voice${i}.${p.blob.type.includes("ogg") ? "ogg" : p.blob.type.includes("mp4") ? "m4a" : "webm"}`));
      f.append("durations", parts.map((p) => p.seconds.toFixed(2)).join(","));
      f.append("language", langRef.current);
      const res = await fetch("/api/transcribe", { method: "POST", body: f, signal: ctl.signal });
      if (!res.ok) throw new Error(await res.text());
      const j = await res.json();
      if (!j.text) throw new Error(j.error || "Nothing was heard. Try again, closer to the microphone.");
      setResult(j);
      setPhase("review");
    } catch (e) {
      if (ctl.signal.aborted && ctl.signal.reason !== "timeout") return;   // cancelled by the user
      setError(ctl.signal.aborted ? "Transcription took too long." : String(e.message || e).slice(0, 260));
      setPhase("error");
    } finally {
      clearTimeout(timer);
    }
  }, []);

  const stop = useCallback(async () => {
    const s = r.current;
    if (!s.stream || !s.segs) return;
    clearTimeout(s.timer);
    const segs = s.segs;
    const last = s.cur;
    r.current = { ...s, cur: null };
    if (last && last.state !== "inactive") last.stop();
    const done = await Promise.all(segs.map((g) => g.finished));
    cleanup();
    const parts = done.filter((g) => g.seconds >= MIN_SECONDS).map((g) => ({ blob: g.blob, seconds: g.seconds }));
    if (!parts.length) { setPhase("idle"); return; }   // accidental tap
    audio.current = parts;
    transcribe();
  }, [cleanup, transcribe]);

  const close = useCallback(() => {
    abort.current?.abort("cancel");
    const s = r.current;
    r.current = { ...s, cur: null };
    segsStop(s);
    cleanup();
    audio.current = [];
    setPhase("idle"); setResult(null); setError("");
  }, [cleanup]);

  const toggle = useCallback(() => {
    if (phase === "idle") start();
    else if (phase === "recording") stop();
  }, [phase, start, stop]);

  useEffect(() => {
    if (phase !== "recording") return;
    const t = setInterval(() => setElapsed((performance.now() - r.current.t0) / 1000), 100);
    return () => clearInterval(t);
  }, [phase]);

  const getData = useCallback(() => {
    const a = r.current.analyser;
    if (!a) return null;
    const d = new Uint8Array(a.frequencyBinCount);
    a.getByteFrequencyData(d);
    return d;
  }, []);

  return { phase, elapsed, result, error, lang, setLang, start, stop, close, toggle, retry: transcribe, getData, setResult };
}

function segsStop(s) {
  try { if (s.cur && s.cur.state !== "inactive") { s.cur.onstop = null; s.cur.stop(); } } catch { /* already stopped */ }
}

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#d9d6cd";
const K = 72;   // points of the spectrum wrapped around the circle

function LangPicker({ lang, setLang }) {
  return (
    <div className="lang-pick" role="group" aria-label="Speech language">
      {CHOICES.map(([id, name]) => <button key={id} className={lang === id ? "on" : ""} onClick={() => setLang(id)}>{name}</button>)}
    </div>
  );
}

// Full-screen circular visualiser. The outline of three thin rings is shaped by the live frequency spectrum
// (voice low and high pitch move different parts of the circle); the outer rings echo it a few frames later.
export function VoiceOverlay({ phase, elapsed, result, error, lang, setLang, getData, onSend, onClose, onStop, onRetry, onChangeText, preview }) {
  const canvas = useRef(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "Enter" && phase === "recording") { e.preventDefault(); onStop?.(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, onClose, onStop]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    let raf, w = 0, h = 0;
    const size = () => {
      const dpr = window.devicePixelRatio || 1;
      w = c.clientWidth; h = c.clientHeight;
      c.width = w * dpr; c.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    window.addEventListener("resize", size);
    const t0 = performance.now();
    const band = new Float32Array(K);
    const hist = [];
    let loud = 0;

    const draw = () => {
      const t = (performance.now() - t0) / 1000;
      const col = cssVar("--text").startsWith("#") ? cssVar("--text") : "#d9d6cd";
      ctx.clearRect(0, 0, w, h);
      const m = Math.min(w, h), cx = w / 2, cy = h / 2, R = m * 0.28;
      const raw = phaseRef.current === "recording" ? getData?.() : null;
      let sum = 0;
      for (let i = 0; i < K; i++) {
        const half = i < K / 2 ? i : K - 1 - i;            // mirror: the circle is symmetric, low pitch at the top
        const f = half / (K / 2);
        let v;
        if (raw) v = Math.min(1, Math.pow((raw[1 + Math.floor(Math.pow(f, 0.85) * 46)] || 0) / 255, 1.1) * (1.3 + f * 1.3));
        else if (preview) v = 0.12 + 0.34 * Math.abs(Math.sin(t * 1.9 + f * 5)) * (1 - f * 0.5) + 0.08 * Math.sin(t * 4 + i);
        else if (phaseRef.current === "transcribing") v = 0.1 + 0.08 * Math.sin(t * 2.2 + f * 6);
        else v = 0.02;
        band[i] += (v - band[i]) * (v > band[i] ? 0.6 : 0.09);   // quick attack, slow release
        sum += band[i];
      }
      loud += (sum / K - loud) * 0.2;
      hist.push(Float32Array.from(band));
      if (hist.length > 16) hist.shift();
      const spin = t * 0.04;
      for (let ring = 0; ring < 3; ring++) {
        const frame = hist[Math.max(0, hist.length - 1 - ring * 5)];
        const base = R * (1 - ring * 0.05) * (1 + loud * 0.05);
        const gain = m * (0.17 - ring * 0.035);
        ctx.beginPath();
        const N = 240;
        for (let p = 0; p <= N; p++) {
          const a = (p / N) * Math.PI * 2;
          let pos = ((p / N + spin) * K) % K; if (pos < 0) pos += K;
          const i0 = Math.floor(pos), fr = pos - i0, s = fr * fr * (3 - 2 * fr);
          const val = frame[i0 % K] * (1 - s) + frame[(i0 + 1) % K] * s;
          const rr = base + val * gain + Math.sin(t * 1.2 + a * 2 + ring) * m * 0.002;
          const x = cx + Math.cos(a - Math.PI / 2) * rr, y = cy + Math.sin(a - Math.PI / 2) * rr;
          p ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.closePath();
        ctx.lineWidth = ring === 0 ? 1.8 : 1.1;
        ctx.strokeStyle = col + ["dd", "70", "38"][ring];
        ctx.stroke();
      }
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => { cancelAnimationFrame(raf); window.removeEventListener("resize", size); };
  }, [getData, preview]);

  return (
    <div className="voice" onClick={(e) => e.target === e.currentTarget && phase !== "recording" && onClose()}>
      <canvas ref={canvas} />
      <div className="voice-center">
        {phase === "recording" && (
          <>
            <div className="voice-time">{fmt(elapsed)}</div>
            <LangPicker lang={lang} setLang={setLang} />
            <div className="voice-hint">Listening · press Done when finished</div>
            <div className="voice-actions"><button onClick={onClose}>Cancel</button><button className="primary" onClick={onStop}>Done</button></div>
          </>
        )}
        {phase === "transcribing" && (
          <>
            <div className="voice-time">…</div>
            <div className="voice-hint">Transcribing{lang === "en" ? " (English, Whisper)" : lang === "ml" ? " (Malayalam, Sarvam)" : ""} · long recordings take a few seconds more</div>
            <div className="voice-actions"><button onClick={onClose}>Cancel</button></div>
          </>
        )}
        {phase === "review" && result && (
          <>
            <div className="voice-chip">{LANG_NAMES[result.language] || result.language} · {result.provider === "sarvam" ? "Sarvam" : "Whisper"}</div>
            <textarea className="voice-text" value={result.text} onChange={(e) => onChangeText(e.target.value)} autoFocus rows={4} />
            <LangPicker lang={lang} setLang={setLang} />
            <div className="voice-actions">
              <button onClick={onClose}>Discard</button>
              <button onClick={onRetry}>Redo</button>
              <button className="primary" onClick={() => onSend(result.text)}>Send</button>
            </div>
          </>
        )}
        {phase === "error" && (
          <>
            <div className="voice-err">{error}</div>
            <LangPicker lang={lang} setLang={setLang} />
            <div className="voice-actions"><button onClick={onClose}>Close</button><button className="primary" onClick={onRetry}>Retry</button></div>
          </>
        )}
      </div>
    </div>
  );
}
