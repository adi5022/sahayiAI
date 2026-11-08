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

// Every model Sahayi can call. Rows for Groq are repeated for each configured key (Key 2 and 3 appear as "#2", "#3").
const GROQ_BASE = [
  ["openai/gpt-oss-120b", "Chat"], ["openai/gpt-oss-20b", "Chat"], ["qwen/qwen3.8-27b", "Chat, photos"], ["whisper-large-v3", "Speech"],
];
const OTHER_BASE = [["gemini", "gemini-3.5-flash", "Chat, photos, audio"], ["gemini", "gemini-3.5-flash-lite", "Photos"]];
const pretty = (m) => m.replace("openai/", "").replace("qwen/", "");
const num = (n) => Math.round(n).toLocaleString();

function Pill({ tone, children }) { return <span className={`pill ${tone || ""}`}>{children}</span>; }

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

  const rows = useMemo(() => {
    if (!u) return [];
    const m = {};
    const blank = (provider, model, what) => ({ provider, model, what, calls: 0, ok: 0, tokens: 0, audio: 0, lat: 0, last: null });
    const keys = u.config?.groq_keys || 1;
    for (let k = 1; k <= keys; k++) GROQ_BASE.forEach(([model, what]) => { const label = k === 1 ? model : `${model}#${k}`; m[`groq|${label}`] = blank("groq", label, what); });
    OTHER_BASE.forEach(([p, model, what]) => { if (p !== "gemini" || u.config?.gemini) m[`${p}|${model}`] = blank(p, model, what); });
    (u.models || []).forEach((r) => {
      const e = (m[`${r.provider}|${r.model}`] ||= blank(r.provider, r.model, r.kind === "stt" ? "Speech" : r.kind === "vision" ? "Photos" : "Chat"));
      e.calls += r.calls; e.ok += r.ok; e.tokens += r.tokens; e.audio += r.audio; e.lat += r.avg_s * r.calls;
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

  const used = rows.filter((r) => r.calls > 0);
  const calls = used.reduce((a, r) => a + r.calls, 0) + (sarRow ? sarRow.calls : 0);
  const failed = used.reduce((a, r) => a + (r.calls - r.ok), 0);
  // A model is "out" when Groq says its daily tokens are (nearly) all used, or it was rate limited in the last two minutes.
  const dailyFull = (r) => { const l = u.limits?.[r.model]; return l && l.abbr === "TPD" && l.used / l.limit >= 0.98 && now - l.ts < 3600; };
  const recent429 = (r) => r.last && !r.last.ok && r.last.status === 429 && now - r.last.ts < 120;
  const out = (r) => dailyFull(r) || recent429(r);
  const chatRows = rows.filter((r) => r.provider === "groq" && r.what !== "Speech");
  const outRows = chatRows.filter(out);
  const health = outRows.length === 0 ? ["ok", "All good", "no limits hit"]
    : outRows.length === chatRows.length ? ["bad", "Out of tokens", "every Groq chat model is limited"]
    : ["warn", "Partly limited", `${outRows.length} of ${chatRows.length} Groq chat models limited`];

  const status = (r) => {
    if (!r.calls && !dailyFull(r)) return <Pill>Idle</Pill>;
    if (dailyFull(r)) return <Pill tone="bad">Daily limit</Pill>;
    if (recent429(r)) return <Pill tone="warn">Limited</Pill>;
    if (r.last && !r.last.ok) return <Pill tone="bad">Error</Pill>;
    return <Pill tone="ok">OK</Pill>;
  };
  const section = (title, list, note) => list.length > 0 && (
    <div className="u-section">
      <h4>{title}{note && <small>{note}</small>}</h4>
      <div className="u-table">
        <div className="u-row u-head"><span>Model</span><span>Used today</span><span>Status</span><span>Calls</span><span>Failed</span><span>Avg time</span><span>Last call</span></div>
        {list.map((r) => {
          const lim = u.limits?.[r.model];
          const abbr = lim?.abbr;
          const cap = r.provider === "groq" && r.what !== "Speech" ? u.groq_daily_tokens : 0;
          const usedTok = abbr === "TPD" ? lim.used : r.tokens;
          const capTok = abbr === "TPD" ? lim.limit : cap;
          const lat = r.calls ? r.lat / r.calls : 0;
          const [name, key] = pretty(r.model).split("#");
          return (
            <div className={`u-row ${r.calls ? "" : "idle"}`} key={`${r.provider}${r.model}`}>
              <span className="u-model"><b>{name}</b>{key && <em>key {key}</em>}<small>{r.what}</small></span>
              <span className="u-used">
                {r.what === "Speech" ? <b>{(r.audio / 60).toFixed(1)} min audio</b> : capTok ? (
                  <>
                    <b>{kfmt(usedTok)} / {kfmt(capTok)}</b>
                    <div className="bar"><i style={{ width: `${Math.min(100, (usedTok / capTok) * 100)}%`, background: colorFor(1 - usedTok / capTok) }} /></div>
                    {abbr === "TPD" && <small>Groq says · {ago(now - lim.ts)}</small>}
                  </>
                ) : <b>{r.tokens ? `${kfmt(r.tokens)} tokens` : "–"}</b>}
              </span>
              <span>{status(r)}</span>
              <span className="n">{r.calls || "–"}</span>
              <span className={`n ${r.calls - r.ok ? "bad" : ""}`}>{r.calls ? r.calls - r.ok : "–"}</span>
              <span className="n">{r.calls ? `${lat.toFixed(1)}s` : "–"}</span>
              <span className="n">{r.last ? ago(now - r.last.ts) : "–"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );

  return (
    <div>
      <div className="u-top"><span className="live" /> Live, refreshes every 3 seconds · today so far · includes the Telegram bot and this console</div>
      <div className="u-tiles">
        <div className={`u-tile ${health[0]}`}><span>Right now</span><b>{health[1]}</b><small>{health[2]}</small></div>
        <div className="u-tile"><span>Calls today</span><b>{num(calls)}</b><small>{failed} failed ({calls ? Math.round((failed / calls) * 100) : 0}%)</small></div>
        <div className="u-tile"><span>Groq keys</span><b>{u.config?.groq_keys || 1}</b><small>{(u.config?.groq_keys || 1) > 1 ? "tried in order" : "add GROQ_API_KEY_2 for more"}</small></div>
        <div className="u-tile"><span>Sarvam credits</span><b>{base != null ? `₹${sar.estimated_left.toFixed(1)}` : "not set"}</b><small>{(sar.audio_seconds_total / 60).toFixed(1)} min transcribed</small></div>
      </div>

      {section("Groq", rows.filter((r) => r.provider === "groq"), "Limits: 200k tokens a day and 8k a minute per model and key")}
      {section("Gemini", rows.filter((r) => r.provider === "gemini"), "Used for photos and as a fallback")}
      {section("NVIDIA", rows.filter((r) => r.provider === "nvidia"), "Off unless NVIDIA_FALLBACK=1")}

      <div className="u-section">
        <h4>Sarvam<small>Malayalam speech</small></h4>
        <div className="u-card">
          <div className="rings">
            {base != null ? <Ring frac={sar.estimated_left / Math.max(base, 1)} big={`₹${sar.estimated_left.toFixed(1)}`} small={`of ₹${base}`} caption="estimated credits left" />
              : <Ring frac={1} big="?" small="not set" caption="enter your balance" color="var(--line)" />}
            <Ring frac={Math.min(sar.audio_seconds_total / 60 / (sar.free_credits / sar.price_per_min), 1)} color="#2aa7a1"
                  big={(sar.audio_seconds_total / 60).toFixed(1)} small="minutes" caption="transcribed in total" />
          </div>
          <div className="stat-row"><span>Spent since you set the balance</span><b>₹{(sar.spent_since ?? 0).toFixed(2)}</b></div>
          <div className="stat-row"><span>Price</span><b>₹{sar.price_per_min}/min of audio</b></div>
          <div className="stat-row"><span>Last call</span>
            {sarRow?.last ? <Pill tone={sarRow.last.status === 200 ? "ok" : "bad"}>{sarRow.last.status === 402 ? "402 credits used up" : sarRow.last.status === 200 ? "200 OK" : `error ${sarRow.last.status}`} · {ago(now - sarRow.last.ts)}</Pill> : <Pill>none yet</Pill>}
          </div>
          <div className="bal-form">
            <input type="number" min="0" step="0.01" value={bal} onChange={(e) => setBal(e.target.value)} placeholder="Credits left (₹) from your dashboard" />
            <button onClick={saveBal} disabled={saving || bal === ""}>Save</button>
          </div>
          <div className="u-note">Sarvam has no API for the balance, so this is an estimate. Read “Credits Left” in the <a href="https://dashboard.sarvam.ai" target="_blank" rel="noreferrer">Sarvam dashboard</a> (Settings → Billing), enter it once, and Sahayi subtracts the audio it transcribes after that.</div>
        </div>
      </div>

      <Activity series={u.series} now={now} />
      <div className="u-foot">
        Counts only calls made through Sahayi. Where Groq has said how much of a daily limit is used (“Groq says”), that figure is shown instead, because it also includes anything else using the same key.
        {u.tracking?.total_calls > 0 && ` Saved in the database, so it survives restarts · ${u.tracking.total_calls} calls recorded since ${new Date(u.tracking.since * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}.`}
      </div>
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
