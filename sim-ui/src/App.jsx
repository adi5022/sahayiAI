import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import Settings from "./Settings.jsx";
import { VoiceOverlay, useVoice } from "./Voice.jsx";
import { applyTheme, initialTheme } from "./theme.js";

const COLORS = ["#b86b5e", "#6f9a72", "#b89b5e", "#5f8fae", "#8a7cb0", "#a8688c", "#5f9a9a", "#b98558"];
const colorOf = (id) => COLORS[Number(id) % COLORS.length];
// Overlapping polls can fetch the same messages twice; merging by id keeps each message once.
const merge = (old = [], add = []) => {
  const ids = new Set(old.map((m) => m.id));
  return [...old, ...add.filter((m) => !ids.has(m.id))];
};
const timeOf =(ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Avatar({ user, size = 48 }) {
  return (
    <div className="avatar" style={{ width: size, height: size, background: colorOf(user.chat_id), fontSize: size * 0.42 }}>
      {(user.name || "?").trim().charAt(0).toUpperCase()}
    </div>
  );
}

// Turns /commands in a bot message into links, like Telegram.
function Linkified({ text, onCommand }) {
  const parts = text.split(/(^|\s)(\/[A-Za-z]+)(?=\s|$|[.,:;!?])/g);
  const out = [];
  for (let i = 0; i < parts.length; i += 3) {
    out.push(parts[i]);
    if (parts[i + 1] !== undefined) out.push(parts[i + 1]);
    if (parts[i + 2]) {
      out.push(
        <a key={i} className="cmd-link" onClick={() => onCommand(parts[i + 2])}>
          {parts[i + 2]}
        </a>
      );
    }
  }
  return <>{out}</>;
}

const Icon = ({ d }) => <svg viewBox="0 0 24 24" aria-hidden="true">{d}</svg>;
const CLIP = <path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.4 3.4 0 0 1 4.8 4.8l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9" />;
const MIC = <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>;
const SEND = <path d="M5 12h14M13 6l6 6-6 6" />;

function Lightbox({ src, onClose }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="lightbox" onClick={onClose}>
      <img src={src} alt="" onClick={(e) => e.stopPropagation()} />
      <button className="tool" title="Close (Esc)" onClick={onClose}>✕</button>
    </div>
  );
}

function Pane({ user, commands, messages, typing, onSend, onPhoto, onClose, onDelete, closable }) {
  const [text, setText] = useState("");
  const [menu, setMenu] = useState(false);
  const bottom = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const [pending, setPending] = useState(null);   // a photo waiting to be sent: { file, url }
  const [viewer, setViewer] = useState(null);     // url of the photo shown full size

  const stage = (file) => {
    if (!file || !file.type.startsWith("image/")) return;
    setPending((old) => { if (old) URL.revokeObjectURL(old.url); return { file, url: URL.createObjectURL(file) }; });
    inputRef.current?.focus();
  };
  const unstage = () => setPending((old) => { if (old) URL.revokeObjectURL(old.url); return null; });
  const voice = useVoice();

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, typing]);

  const typingCommand = text.startsWith("/") && !text.includes(" ");
  const suggestions = useMemo(() => {
    if (menu) return commands;
    if (!typingCommand) return [];
    return commands.filter((c) => ("/" + c.command).startsWith(text.toLowerCase()));
  }, [menu, typingCommand, text, commands]);

  const submit = (value) => {
    if (value === undefined && pending) {   // the staged photo is sent now, with the typed text as its caption
      onPhoto(pending.file, text.trim());
      unstage();
      setText("");
      return;
    }
    const v = (value ?? text).trim();
    if (!v) return;
    onSend(v);
    setText("");
    setMenu(false);
  };

  const pick = (c) => {
    if (c.args) {
      setText(`/${c.command} `);
      setMenu(false);
      inputRef.current?.focus();
    } else submit(`/${c.command}`);
  };

  const onCommandLink = (cmd) => {
    const c = commands.find((x) => "/" + x.command === cmd);
    if (c) pick(c);
  };

  return (
    <section className="pane"
             onDragOver={(e) => e.dataTransfer?.types?.includes("Files") && e.preventDefault()}
             onDrop={(e) => {
               const img = [...(e.dataTransfer?.files || [])].find((f) => f.type.startsWith("image/"));
               if (!img) return;
               e.preventDefault();
               stage(img);
             }}>
      <header className="pane-head">
        <Avatar user={user} size={40} />
        <div className="pane-title">
          <div className="name">{user.name}</div>
          <div className="sub">{typing ? "typing…" : "bot chat"}</div>
        </div>
        <button className="icon" title="Delete this simulated user" onClick={onDelete}>🗑</button>
        {closable && <button className="icon" title="Close pane" onClick={onClose}>✕</button>}
      </header>

      <div className="messages">
        {messages.length === 0 && <div className="hint">Send /start to begin.</div>}
        {messages.map((m) => (
          <div key={m.id} className={`row ${m.direction === "out" ? "out" : "in"}`}>
            <div className="bubble">
              {m.media && <img className="photo" src={`/api/media/${m.media}`} alt="" onClick={() => setViewer(`/api/media/${m.media}`)} />}
              <span className="text">
                {m.direction === "in" ? <Linkified text={m.text} onCommand={onCommandLink} /> : m.text}
              </span>
              <span className="time">{timeOf(m.ts)}</span>
            </div>
          </div>
        ))}
        {typing && (
          <div className="row in">
            <div className="bubble typing"><i /><i /><i /></div>
          </div>
        )}
        <div ref={bottom} />
      </div>

      {suggestions.length > 0 && (
        <div className="suggest">
          {suggestions.map((c) => (
            <div key={c.command} className="suggest-item" onClick={() => pick(c)}>
              <span className="c">/{c.command}</span>
              <span className="d">{c.description}</span>
            </div>
          ))}
        </div>
      )}

      {pending && (
        <div className="staged">
          <img src={pending.url} alt="" onClick={() => setViewer(pending.url)} />
          <div className="staged-info"><b>Photo ready</b><span>Add a caption if you like, then press Send</span></div>
          <button type="button" className="tool" title="Remove the photo" onClick={unstage}>✕</button>
        </div>
      )}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input ref={fileRef} type="file" accept="image/*" hidden
               onChange={(e) => { stage(e.target.files[0]); e.target.value = ""; }} />
        <button type="button" className="tool" title="Attach a photo" onClick={() => fileRef.current?.click()}><Icon d={CLIP} /></button>
        <button type="button" className={`menu-btn ${menu ? "on" : ""}`} onClick={() => setMenu(!menu)}>Menu</button>
        <input ref={inputRef} className="msg" value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" autoFocus
               onPaste={(e) => {   // Ctrl+V with an image on the clipboard stages it above the box; Send (or Enter) sends it
                 const img = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
                 if (!img) return;
                 e.preventDefault();
                 stage(img);
               }} />
        <button type="button" className={`tool ${voice.phase === "recording" ? "on" : ""}`} title="Click to record a voice message, click again to finish"
                onClick={voice.toggle}><Icon d={MIC} /></button>
        <button type="submit" className="send" disabled={!text.trim() && !pending}><Icon d={SEND} /></button>
      </form>
      {viewer && <Lightbox src={viewer} onClose={() => setViewer(null)} />}
      {voice.phase !== "idle" && (
        <VoiceOverlay phase={voice.phase} elapsed={voice.elapsed} result={voice.result} error={voice.error} getData={voice.getData}
                      onChangeText={(t) => voice.setResult((r) => ({ ...r, text: t }))}
                      onSend={(t) => { if (t.trim()) onSend(t.trim()); voice.close(); }} onClose={voice.close} onStop={voice.stop}
                      lang={voice.lang} setLang={voice.setLang} onRetry={voice.retry} />
      )}
    </section>
  );
}

export default function App() {
  const [users, setUsers] = useState([]);
  const [commands, setCommands] = useState([]);
  const [open, setOpen] = useState([]);                 // chat ids shown in panes
  const [layout, setLayout] = useState("single");       // "single" | "split"
  const [msgs, setMsgs] = useState({});                 // chat id -> messages
  const [typing, setTyping] = useState({});
  const [seen, setSeen] = useState({});                 // chat id -> last message id the user has seen
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [theme, setThemeState] = useState(initialTheme);
  const [settings, setSettings] = useState(() => {
    const q = new URLSearchParams(window.location.search).get("settings");
    return q === "usage" || q === "appearance" ? q : null;
  });
  const [preview] = useState(() => new URLSearchParams(window.location.search).get("preview"));
  const setTheme = (id) => setThemeState(id);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const msgsRef = useRef(msgs);
  msgsRef.current = msgs;
  const openRef = useRef(open);
  openRef.current = open;

  useEffect(() => {
    api.commands().then(setCommands).catch(() => {});
  }, []);

  const poll = useCallback(async () => {
    try {
      const list = await api.users();
      setUsers(list);
      setError("");
      const nextTyping = {};
      list.forEach((u) => (nextTyping[u.chat_id] = u.typing));
      setTyping(nextTyping);
      for (const id of openRef.current) {
        const have = msgsRef.current[id];
        if (!have) continue; // history is loaded when the chat is opened
        const after = have.length ? have[have.length - 1].id : 0;
        const r = await api.messages(id, after);
        if (r.messages.length) setMsgs((m) => ({ ...m, [id]: merge(m[id], r.messages) }));
      }
    } catch {
      setError("Cannot reach the simulator server. Start it with: uv run python -m bia_hackaton_2026.sim.server");
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 1200);
    return () => clearInterval(t);
  }, [poll]);

  // Opening a chat loads its history; chats shown in a pane count as seen.
  useEffect(() => {
    open.forEach(async (id) => {
      if (!msgsRef.current[id]) {
        const r = await api.messages(id, 0);
        setMsgs((m) => (m[id] ? m : { ...m, [id]: r.messages }));
      }
    });
  }, [open]);
  useEffect(() => {
    setSeen((s) => {
      const next = { ...s };
      open.forEach((id) => {
        const list = msgs[id];
        if (list?.length) next[id] = list[list.length - 1].id;
      });
      return next;
    });
  }, [msgs, open]);

  // Deep link for demos and screenshots: ?layout=split&chats=Lakshmi%20Amma,Ravi opens those chats once the list has loaded.
  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current || users.length === 0) return;
    deepLinked.current = true;
    const q = new URLSearchParams(window.location.search);
    const names = (q.get("chats") || "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    if (q.get("layout") === "split") setLayout("split");
    const ids = names.map((n) => users.find((u) => u.name.toLowerCase() === n)?.chat_id).filter(Boolean);
    if (ids.length) setOpen(ids.slice(0, 4));
  }, [users]);

  const select = (id) => {
    if (layout === "single") setOpen([id]);
    else setOpen((o) => (o.includes(id) ? o.filter((x) => x !== id) : [...o, id].slice(-4)));
  };

  const switchLayout = (l) => {
    setLayout(l);
    if (l === "single") setOpen((o) => o.slice(0, 1));
  };

  const addUser = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    const u = await api.addUser(name.trim());
    setName("");
    setAdding(false);
    await poll();
    select(u.chat_id);
  };

  const remove = async (id) => {
    if (!confirm("Delete this simulated user and their chat?")) return;
    await api.deleteUser(id);
    setOpen((o) => o.filter((x) => x !== id));
    setMsgs((m) => { const n = { ...m }; delete n[id]; return n; });
    poll();
  };

  const send = async (id, text) => {
    await api.send(id, text);
    poll();
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="side-head">
          <div className="side-title">Chats</div>
          <div className="layout-toggle" title="Show one chat or several side by side">
            <button className={layout === "single" ? "on" : ""} onClick={() => switchLayout("single")}>▢</button>
            <button className={layout === "split" ? "on" : ""} onClick={() => switchLayout("split")}>▥</button>
          </div>
          <button className="icon" title="Settings and usage" onClick={() => setSettings("appearance")}>⚙</button>
          <button className="icon add" title="New simulated user" onClick={() => setAdding(true)}>＋</button>
        </div>
        <div className="chat-list">
          {users.length === 0 && <div className="hint pad">No users yet. Click ＋ to add one.</div>}
          {users.map((u) => {
            const unread = u.last && u.last.direction === "in" && u.last.id > (seen[u.chat_id] || 0);
            return (
              <div key={u.chat_id} className={`chat-item ${open.includes(u.chat_id) ? "active" : ""}`} onClick={() => select(u.chat_id)}>
                <Avatar user={u} />
                <div className="meta">
                  <div className="top">
                    <span className="n">{u.name}</span>
                    <span className="t">{u.last ? timeOf(u.last.ts) : ""}</span>
                  </div>
                  <div className="bottom">
                    <span className="p">{u.typing ? "typing…" : u.last ? u.last.text.replace(/\s+/g, " ") : "No messages"}</span>
                    {unread && <span className="dot" />}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        {layout === "split" && <div className="side-foot">Side by side: click chats to add or remove (up to 4).</div>}
      </aside>

      <main className="main">
        {error && <div className="error">{error}</div>}
        {open.length === 0 ? (
          <div className="empty"><span>Select a chat to start messaging</span></div>
        ) : (
          <div className="panes">
            {open.map((id) => {
              const user = users.find((u) => u.chat_id === id);
              if (!user) return null;
              return (
                <Pane
                  key={id}
                  user={user}
                  commands={commands}
                  messages={msgs[id] || []}
                  typing={typing[id]}
                  closable={open.length > 1}
                  onSend={(t) => send(id, t)}
                  onPhoto={async (f, cap) => { await api.sendPhoto(id, f, cap); poll(); }}
                  onClose={() => setOpen((o) => o.filter((x) => x !== id))}
                  onDelete={() => remove(id)}
                />
              );
            })}
          </div>
        )}
      </main>

      {settings && <Settings onClose={() => setSettings(null)} theme={theme} setTheme={setTheme} initialTab={settings} />}
      {preview && (
        <VoiceOverlay preview lang="auto" setLang={() => {}} phase={preview === "review" ? "review" : "recording"} elapsed={7.4} getData={() => null} onClose={() => {}}
                      result={{ text: "എനിക്ക് ഇന്ന് രാവിലെ മുതൽ നെഞ്ചിൽ വേദന ഉണ്ട്, കൂടെ തലകറക്കവും ഉണ്ട്. I am alone at home.", language: "ml-mix", provider: "sarvam", seconds: 2.1 }}
                      onChangeText={() => {}} onSend={() => {}} />
      )}
      {adding && (
        <div className="modal-bg" onClick={() => setAdding(false)}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={addUser}>
            <h3>New contact</h3>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Ravi)" />
            <div className="actions">
              <button type="button" onClick={() => setAdding(false)}>Cancel</button>
              <button type="submit" className="primary" disabled={!name.trim()}>Create</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
