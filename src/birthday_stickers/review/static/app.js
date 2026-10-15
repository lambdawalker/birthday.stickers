const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const pipeline = params.get("pipeline") || "image_generation";
const category = params.get("category") || "pending";
const name = location.pathname.startsWith("/user/")
  ? decodeURIComponent(location.pathname.slice(6))
  : null;
const categories = {
  pending: "To review",
  accepted: "Accepted",
  rejected: "Rejected",
  favorites: "Favorites",
  tshirt: "T-shirts",
};
let images = [],
  index = 0,
  busy = false,
  inGrid = false,
  pending = null;
const query = (cat) => "?" + new URLSearchParams({ pipeline, category: cat });
const userURL = (user, cat) => "/user/" + encodeURIComponent(user) + query(cat);
const api =
  "/api/user/" +
  encodeURIComponent(name) +
  "?" +
  new URLSearchParams({ pipeline });
const imageURL = (item) =>
  "/images/" +
  encodeURIComponent(pipeline) +
  "/" +
  encodeURIComponent(item.filename);
const storageKey = "pending-review:" + name + ":" + pipeline;
function error(e) {
  $("error").textContent = e.message || String(e);
}
async function request(url, body) {
  const response = await fetch(
    url,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  return data;
}
function token() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
function controls() {
  document
    .querySelectorAll("[data-action]")
    .forEach(
      (b) => (b.disabled = busy || !!pending || !images[index] || inGrid),
    );
  $("undo").disabled = busy || !!pending;
  $("next").disabled = busy;
}
function show() {
  inGrid = false;
  $("grid").hidden = true;
  $("review").hidden = false;
  const item = images[index];
  $("empty").hidden = !!item;
  $("hero").hidden = !item;
  $("next").hidden = category === "pending" || !item;
  if (item) {
    $("hero").src = imageURL(item);
    $("hero").alt = "Illustration " + (index + 1) + " for " + name;
    $("favorite").classList.toggle("selected", !!item.favorite);
    $("tshirt").classList.toggle("selected", !!item.tshirt);
    $("favorite").setAttribute("aria-pressed", String(!!item.favorite));
    $("tshirt").setAttribute("aria-pressed", String(!!item.tshirt));
    if (images[index + 1]) {
      const preload = new Image();
      preload.src = imageURL(images[index + 1]);
    }
  }
  controls();
}
function grid() {
  inGrid = true;
  $("review").hidden = true;
  $("grid").hidden = false;
  $("grid").replaceChildren();
  images.forEach((item, i) => {
    const button = document.createElement("button");
    button.setAttribute("aria-label", "Review image " + (i + 1));
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = imageURL(item);
    img.alt = "Reviewed illustration";
    button.append(img);
    button.onclick = () => {
      index = i;
      show();
    };
    $("grid").append(button);
  });
  if (!images.length) $("grid").textContent = "No images in this category yet.";
}
async function load(asGrid = false) {
  $("error").textContent = "";
  try {
    const data = await request(api + "&" + new URLSearchParams({ category }));
    $("empty").querySelector("h2").textContent = "You’re all caught up.";
    images = data.images;
    index = 0;
    const done = (data.counts.accepted || 0) + (data.counts.rejected || 0);
    $("progress").textContent = done + " of " + data.total + " reviewed";
    if (asGrid) grid();
    else show();
  } catch (e) {
    images = [];
    show();
    $("empty").querySelector("h2").textContent = "Could not load the gallery.";
    error(e);
  }
}
async function send() {
  if (busy || !pending) return;
  busy = true;
  controls();
  try {
    await request(api, pending);
    localStorage.removeItem(storageKey);
    pending = null;
    $("retry").hidden = true;
    await load(false);
  } catch (e) {
    error(
      Error(
        "Your choice could not be confirmed. Retry to save it safely. " +
          e.message,
      ),
    );
    $("retry").hidden = false;
  } finally {
    busy = false;
    controls();
  }
}
async function act(action) {
  if (busy || pending) return;
  if (action !== "undo" && !images[index]) return;
  pending = { hash: images[index]?.hash, action, token: token() };
  try {
    localStorage.setItem(storageKey, JSON.stringify(pending));
  } catch (e) {
    pending = null;
    error(
      Error(
        "Browser storage is unavailable; enable it to safely save reviews.",
      ),
    );
    return;
  }
  await send();
}
$("retry").onclick = send;
$("undo").onclick = () => act("undo");
$("reload").onclick = () => load();
$("next").onclick = () => {
  index = (index + 1) % images.length;
  show();
};
document
  .querySelectorAll("[data-action]")
  .forEach((b) => (b.onclick = () => act(b.dataset.action)));
$("hero").onerror = () =>
  error(
    Error("This image could not load. Check the gallery files or connection."),
  );
let start = null;
$("stage").addEventListener("pointerdown", (e) => {
  if (busy || pending || !images[index] || e.target.closest("button")) return;
  start = { x: e.clientX, y: e.clientY };
  $("stage").setPointerCapture(e.pointerId);
});
$("stage").addEventListener("pointermove", (e) => {
  if (!start) return;
  const x = e.clientX - start.x,
    y = e.clientY - start.y;
  $("hero").style.transform =
    `translate(${x * 0.35}px,${y * 0.35}px) rotate(${x * 0.02}deg)`;
  const action =
    Math.abs(x) > Math.abs(y)
      ? x > 0
        ? "Accept"
        : "Reject"
      : y < 0
        ? "Favorite"
        : "T-shirt";
  $("gesture").textContent = action;
  $("gesture").style.opacity = Math.min(
    1,
    Math.max(Math.abs(x), Math.abs(y)) / 90,
  );
});
function clearGesture() {
  start = null;
  $("hero").style.transform = "";
  $("gesture").style.opacity = 0;
}
$("stage").addEventListener("pointerup", (e) => {
  if (!start) return;
  const x = e.clientX - start.x,
    y = e.clientY - start.y;
  clearGesture();
  if (Math.max(Math.abs(x), Math.abs(y)) < 70) return;
  act(
    Math.abs(x) > Math.abs(y)
      ? x > 0
        ? "accept"
        : "reject"
      : y < 0
        ? "favorite"
        : "tshirt",
  );
});
$("stage").addEventListener("pointercancel", clearGesture);
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input,button,a") || inGrid || !name) return;
  const action = {
    ArrowRight: "accept",
    ArrowLeft: "reject",
    ArrowUp: "favorite",
    ArrowDown: "tshirt",
    z: "undo",
  }[e.key];
  if (action) {
    e.preventDefault();
    act(action);
  }
});
async function sessions() {
  $("review").hidden = true;
  $("categories").hidden = true;
  $("sessions").hidden = false;
  $("title").textContent = "Everyone’s picks.";
  $("eyebrow").textContent = "REVIEW SESSIONS";
  try {
    const rows = await request(
      "/api/sessions?" + new URLSearchParams({ pipeline }),
    );
    if (!rows.length)
      $("sessions").textContent =
        "No reviews yet. Open /user/your-name to start.";
    for (const row of rows) {
      const card = document.createElement("article"),
        title = document.createElement("h2"),
        summary = document.createElement("p"),
        nav = document.createElement("nav");
      title.textContent = row.name;
      summary.textContent = `${row.reviewed} reviewed · ${row.accepted} accepted · ${row.rejected} rejected · ${row.favorites} favorites · ${row.tshirt} T-shirts`;
      for (const [cat, label] of Object.entries(categories)) {
        const link = document.createElement("a");
        link.href = userURL(row.name, cat);
        link.textContent = label;
        nav.append(link);
      }
      card.append(title, summary, nav);
      $("sessions").append(card);
    }
  } catch (e) {
    error(e);
  }
}
if (name) {
  $("title").textContent = name + "’s little adventures.";
  $("sessions-link").href = "/sessions?" + new URLSearchParams({ pipeline });
  for (const [cat, label] of Object.entries(categories)) {
    const link = document.createElement("a");
    link.href = userURL(name, cat);
    link.textContent = label;
    link.classList.toggle("active", category === cat);
    $("categories").append(link);
  }
  try {
    pending = JSON.parse(localStorage.getItem(storageKey) || "null");
  } catch (e) {
    error(e);
  }
  load(category !== "pending").then(() => {
    if (pending) {
      $("retry").hidden = false;
      error(Error("An earlier choice is waiting to be saved. Tap Retry."));
    }
  });
} else {
  sessions();
}
