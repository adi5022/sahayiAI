import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";

const COLORS = ["#e17076", "#7bc862", "#e5ca77", "#65aadd", "#a695e7", "#ee7aae", "#6ec9cb", "#faa774"];
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

function Pane({ user, commands, messages, typing, onSend, onPhoto, onClose, onDelete, closable }) {
  const [text, setText] = useState("");
  const [menu, setMenu] = useState(false);
  const bottom = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);

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
    <section className="pane">
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
              {m.media && <img className="photo" src={`/api/media/${m.media}`} alt="" />}
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

      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input ref={fileRef} type="file" accept="image/*" hidden
               onChange={(e) => { const f = e.target.files[0]; if (f) onPhoto(f, text.trim()); setText(""); e.target.value = ""; }} />
        <button type="button" className="icon" title="Send a photo" onClick={() => fileRef.current?.click()}>📎</button>
        <button type="button" className={`menu-btn ${menu ? "on" : ""}`} onClick={() => setMenu(!menu)}>☰ Menu</button>
        <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" autoFocus />
        <button type="submit" className="send" disabled={!text.trim()}>➤</button>
      </form>
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
