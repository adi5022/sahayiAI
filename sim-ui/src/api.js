const j = async (r) => {
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
const post = (url, body) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j);

export const api = {
  commands: () => fetch("/api/commands").then(j),
  users: () => fetch("/api/users").then(j),
  addUser: (name) => post("/api/users", { name }),
  deleteUser: (id) => fetch(`/api/users/${id}`, { method: "DELETE" }).then(j),
  messages: (id, after) => fetch(`/api/users/${id}/messages?after=${after}`).then(j),
  send: (id, text) => post(`/api/users/${id}/send`, { text }),
};
