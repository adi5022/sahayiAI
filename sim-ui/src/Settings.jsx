import { useEffect, useMemo, useState } from "react";
import { THEMES } from "./theme.js";

const PROVIDERS = { groq: "#e8743b", sarvam: "#2aa7a1", nvidia: "#76b900", gemini: "#4c8bf5" };
const PNAME = { groq: "Groq", sarvam: "Sarvam", nvidia: "NVIDIA", gemini: "Gemini" };
const colorFor = (f) => (f > 0.5 ? "#6fbf73" : f > 0.2 ? "#e0a84a" : "#e0675a");
const kfmt = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : `${Math.round(n)}`);

function Ring({ frac, big, small, caption, color }) {
  const r = 40, c = 2 * Math.PI * r;
  const f = Math.max(0, Math.min(1, frac ?? 0));
  return (
    <div>
      <div className="ring">
        <svg width="96" height="96" viewBox="0 0 96 96">
          <circle className="trk" cx="48" cy="48" r={r} fill="none" strokeWidth="9" />
          <circle className="val" cx="48" cy="48" r={r} fill="none" strokeWidth="9" stroke={color || colorFor(f)}
                  strokeDasharray={c} strokeDashoffset={c * (1 - f)} />
        </svg>
        <div className="lbl"><b>{big}</b><span>{small}</span></div>
      </div>
      <div className="ring-cap">{caption}</div>
    </div>
  );
}

function Appearance({ theme, setTheme }) {
  return (
    <div className="themes">
      {THEMES.map((t) => (
        <button key={t.id} className={`theme ${theme === t.id ? "on" : ""}`} onClick={() => setTheme(t.id)}>
          <div className="sw">{t.sw.map((c) => <i key={c} style={{ background: c }} />)}</div>
          <div className="nm">{t.name}{theme === t.id ? "  ✓" : ""}</div>
          <div className="nt">{t.note}</div>
        </button>
      ))}
    </div>
  );
}

function ago(s) {
  if (s < 5) return "just now";
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function Activity({ series, now }) {
  const W = 600, H = 130, N = 60, bw = W / N;
  const provs = Object.keys(PROVIDERS).filter((p) => series.some((s) => s.provider === p));
  const grid = Array.from({ length: N }, () => ({}));
  series.forEach((s) => { if (s.m >= 0 && s.m < N) grid[s.m][s.provider] = s.calls; });
  const max = Math.max(1, ...grid.map((g) => Object.values(g).reduce((a, b) => a + b, 0)));
  return (
    <div className="chart">
      <h4>Calls per minute, last hour
        <span className="legend">{provs.map((p) => <span key={p}><i style={{ background: PROVIDERS[p] }} />{PNAME[p]}</span>)}</span>
      </h4>
      <svg viewBox={`0 0 ${W} ${H + 16}`} width="100%" role="img" aria-label="Calls per minute">
        {[0.25, 0.5, 0.75, 1].map((g) => <line key={g} x1="0" x2={W} y1={H - g * (H - 8)} y2={H - g * (H - 8)} stroke="var(--line)" strokeWidth="1" />)}
        {grid.map((g, i) => {
          let y = H;
          return provs.map((p) => {
            const v = g[p] || 0;
            if (!v) return null;
            const h = (v / max) * (H - 8);
            y -= h;
            return <rect key={`${i}${p}`} x={i * bw + 1} y={y} width={bw - 2} height={h} rx="2" fill={PROVIDERS[p]} opacity=".9">
              <title>{`${PNAME[p]}: ${v} call${v > 1 ? "s" : ""}, ${N - i} min ago`}</title></rect>;
          });
        })}
        <text x="0" y={H + 13} fontSize="10" fill="var(--muted)">60 min ago</text>
        <text x={W} y={H + 13} fontSize="10" fill="var(--muted)" textAnchor="end">now</text>
        <text x="0" y="10" fontSize="10" fill="var(--muted)">{max} / min</text>
      </svg>
    </div>
  );
}

// Models Sahayi can call, so the panel lists them all even before the first call of the day.
const KNOWN = [
  ["groq", "openai/gpt-oss-120b", "chat"], ["groq", "openai/gpt-oss-20b", "chat"], ["groq", "qwen/qwen3.8-27b", "chat, vision"],
  ["groq", "whisper-large-v3", "stt"], ["gemini", "gemini-3.5-flash-lite", "vision"], ["nvidia", "meta/muse-glimmer-30b", "chat"],
];

function Usage() {
  const [u, setU] = useState(null);
  const [err, setErr] = useState("");
  const [bal, setBal] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const r = await fetch("/api/usage");
      if (!r.ok) throw new Error(await r.text());
      setU(await r.json()); setErr("");
    } catch (e) { setErr("Usage is unavailable: " + String(e.message).slice(0, 120)); }
  };
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);

  const models = useMemo(() => {
    const m = {};
    KNOWN.forEach(([provider, model, kinds]) => { m[`${provider}|${model}`] = { provider, model, calls: 0, ok: 0, tokens: 0, audio: 0, lat: 0, last: null, kinds: kinds.split(", ") }; });
    (u?.models || []).forEach((r) => {
      const k = `${r.provider}|${r.model}`;
      const e = (m[k] ||= { provider: r.provider, model: r.model, calls: 0, ok: 0, tokens: 0, audio: 0, lat: 0, last: null, kinds: [] });
      if (e.calls === 0) e.kinds = [];
      e.calls += r.calls; e.ok += r.ok; e.tokens += r.tokens; e.audio += r.audio; e.lat += r.avg_s * r.calls;
      if (!e.kinds.includes(r.kind)) e.kinds.push(r.kind);
      if (r.last && (!e.last || r.last.ts > e.last.ts)) e.last = r.last;
    });
    return Object.values(m).filter((e) => e.provider !== "sarvam");
  }, [u]);

  if (!u) return <div className="hint pad">{err || "Loading usage…"}</div>;
  const now = u.now;
  const sar = u.sarvam;
  const sarRow = (u.models || []).find((r) => r.provider === "sarvam");
  const base = sar.baseline;
  const saveBal = async () => {
    const v = parseFloat(bal);
    if (Number.isNaN(v) || v < 0) return;
    setSaving(true);
    await fetch("/api/usage/sarvam-credits", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ credits_left: v }) });
    setBal(""); setSaving(false); load();
  };

  return (
    <div>
      <div className="u-top"><span className="live" /> Live, refreshes every 3 seconds · today so far · usage from the Telegram bot and this console</div>
      <div className="u-grid">
        <div className="u-card">
          <h4><span className="pv" style={{ background: PROVIDERS.sarvam }} />Sarvam credits <small>speech to text</small></h4>
          <div className="rings">
            {base != null ? (
              <Ring frac={sar.estimated_left / Math.max(base, 1)} big={`₹${sar.estimated_left.toFixed(1)}`} small={`of ₹${base}`} caption="estimated credits left" />
            ) : (
              <Ring frac={1} big="?" small="not set" caption="enter your balance below" color="var(--line)" />
            )}
            <Ring frac={Math.min(sar.audio_seconds_total / 60 / (sar.free_credits / sar.price_per_min), 1)} color="#2aa7a1"
                  big={`${(sar.audio_seconds_total / 60).toFixed(1)}`} small="minutes" caption={`transcribed in total`} />
          </div>
          <div className="stat-row"><span>Spent since you set the balance</span><b>₹{(sar.spent_since ?? 0).toFixed(2)}</b></div>
          <div className="stat-row"><span>Price</span><b>₹{sar.price_per_min}/min of audio</b></div>
          <div className="stat-row"><span>Last call</span>
            {sarRow?.last ? <span className={`pill ${sarRow.last.status === 200 ? "ok" : "bad"}`}>{sarRow.last.status === 402 ? "402 credits used up" : sarRow.last.status === 200 ? "200 OK" : `error ${sarRow.last.status}`} · {ago(now - sarRow.last.ts)}</span> : <span className="pill">none yet</span>}
          </div>
          <div className="bal-form">
            <input type="number" min="0" step="0.01" value={bal} onChange={(e) => setBal(e.target.value)} placeholder="Credits left (₹) from your dashboard" />
            <button onClick={saveBal} disabled={saving || bal === ""}>Save</button>
          </div>
          <div className="u-note">Sarvam has no API for the balance, so it is an estimate. Read “Credits Left” in the <a href="https://dashboard.sarvam.ai" target="_blank" rel="noreferrer">Sarvam dashboard</a> (Settings → Billing), enter it once, and Sahayi subtracts the audio it transcribes after that. Calls made elsewhere are not counted.</div>
        </div>

        {models.map((m) => {
          const rl = m.last?.rl;
          const age = rl ? now - rl.ts : null;
          const tokEff = rl ? (age > 60 ? rl.tok_lim : rl.tok_rem) : null;
          const lat = m.calls ? m.lat / m.calls : 0;
          const okPct = m.calls ? Math.round((m.ok / m.calls) * 100) : 0;
          return (
            <div className={`u-card ${m.calls ? "" : "idle"}`} key={`${m.provider}${m.model}`}>
              <h4><span className="pv" style={{ background: PROVIDERS[m.provider] || "#999" }} />{m.model.replace("openai/", "").replace("qwen/", "")}
                <small>{PNAME[m.provider] || m.provider} · {m.kinds.join(", ")}</small></h4>
              {!m.calls ? <div className="u-empty">No calls yet today</div> : rl ? (
                <div className="rings">
                  <Ring frac={tokEff / rl.tok_lim} big={kfmt(tokEff)} small={`of ${kfmt(rl.tok_lim)}`} caption="tokens left this minute" />
                  <Ring frac={rl.req_rem / rl.req_lim} big={kfmt(rl.req_rem)} small={`of ${kfmt(rl.req_lim)}`} caption="requests left today" />
                </div>
              ) : (
                <div className="rings">
                  <Ring frac={m.calls ? m.ok / m.calls : 0} big={`${okPct}%`} small="succeeded" caption={`${m.calls} call${m.calls === 1 ? "" : "s"} today`} />
                  <Ring frac={Math.min(lat / 20, 1)} color="#4c8bf5" big={`${lat.toFixed(1)}s`} small="average" caption="response time" />
                </div>
              )}
              {m.calls > 0 && m.provider === "groq" && m.tokens > 0 && (
                <>
                  <div className="stat-row"><span>Tokens used today</span><b>{kfmt(m.tokens)} / {kfmt(u.groq_daily_tokens)}</b></div>
                  <div className="bar"><i style={{ width: `${Math.min(100, (m.tokens / u.groq_daily_tokens) * 100)}%`, background: colorFor(1 - m.tokens / u.groq_daily_tokens) }} /></div>
                </>
              )}
              {m.calls > 0 && <div className="stat-row"><span>Calls today</span><b>{m.calls} ({m.calls - m.ok} failed)</b></div>}
              {m.audio > 0 && <div className="stat-row"><span>Audio transcribed</span><b>{(m.audio / 60).toFixed(1)} min</b></div>}
              {m.calls > 0 && <div className="stat-row"><span>Last call</span>
                {m.last ? <span className={`pill ${m.last.ok ? "ok" : "bad"}`}>{m.last.ok ? "OK" : m.last.status === 429 ? "rate limited" : `error ${m.last.status || ""}`} · {ago(now - m.last.ts)}</span> : <span className="pill">none yet</span>}
              </div>}
              {rl && <div className="u-note">Limits as reported by Groq on its latest reply ({ago(age)}). The daily token cap is from Groq's documentation.</div>}
            </div>
          );
        })}
      </div>
      <Activity series={u.series} now={now} />
      {u.tracking?.total_calls > 0 && <div className="u-foot">Saved in the database, so it survives restarts · {u.tracking.total_calls} calls recorded since {new Date(u.tracking.since * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</div>}
      {err && <div className="error" style={{ marginTop: 10 }}>{err}</div>}
    </div>
  );
}

export default function Settings({ onClose, theme, setTheme, initialTab = "appearance" }) {
  const [tab, setTab] = useState(initialTab);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal settings" onClick={(e) => e.stopPropagation()}>
        <div className="settings-head">
          <h3>Settings</h3>
          <div className="tabs">
            <button className={tab === "appearance" ? "on" : ""} onClick={() => setTab("appearance")}>Appearance</button>
            <button className={tab === "usage" ? "on" : ""} onClick={() => setTab("usage")}>Usage</button>
          </div>
          <button className="icon" onClick={onClose} title="Close">✕</button>
        </div>
        <div className="settings-body">
          {tab === "appearance" ? <Appearance theme={theme} setTheme={setTheme} /> : <Usage />}
        </div>
      </div>
    </div>
  );
}
