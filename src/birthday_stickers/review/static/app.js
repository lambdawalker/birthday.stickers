const $ = (id) => document.getElementById(id);
const swipeThreshold = 140;
const params = new URLSearchParams(location.search);
const pipeline = params.get("pipeline") || "image_generation";
const requestedBuffer = Number(params.get("preload") ?? 3);
const bufferSize = Number.isFinite(requestedBuffer)
  ? Math.max(1, Math.min(20, Math.floor(requestedBuffer)))
  : 3;
const imageBuffer = new Map();
const recentImages = [];
const galleryMode = params.get("mode") === "gallery";
const category = params.get("category") || (galleryMode ? "all" : "pending");
let exiting = false;
let reviewedCount = 0;
let totalCount = 0;
const name = location.pathname.startsWith("/user/")
  ? decodeURIComponent(location.pathname.slice(6))
  : null;
const categories = {
  all: "All",
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
const query = (cat) =>
  "?" +
  new URLSearchParams({
    pipeline,
    category: cat,
    preload: bufferSize,
    mode: galleryMode ? "gallery" : "swipe",
  });
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
const positionKey = "review-position:" + name + ":" + pipeline + ":" + category;
let loadedPosition = false;
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
      (b) => (b.disabled = busy || !!pending || !images[index] || exiting),
    );
  $("undo").disabled = busy || !!pending || exiting;
  $("next").disabled = busy || exiting || !!pending;
  for (const id of ["previous-image", "next-image"])
    $(id).disabled = busy || exiting || !!pending || images.length < 2;
}
function show() {
  $("stage").hidden = galleryMode;
  $("carousel").hidden = !galleryMode;
  inGrid = false;
  $("grid").hidden = true;
  $("review").hidden = false;
  const item = images[index];
  $("empty").hidden = !!item;
  $("hero").hidden = !item;
  $("next").hidden = true;
  if (item) {
    $("hero").src = imageURL(item);
    $("hero").alt = "Illustration " + (index + 1) + " for " + name;
    $("favorite").classList.toggle("selected", !!item.favorite);
    $("tshirt").classList.toggle("selected", !!item.tshirt);
    $("favorite").setAttribute("aria-pressed", String(!!item.favorite));
    $("tshirt").setAttribute("aria-pressed", String(!!item.tshirt));
  }
  const underneath = images[index + 1];
  $("underneath").hidden = !underneath;
  if (underneath) $("underneath").src = imageURL(underneath);
  else $("underneath").removeAttribute("src");
  updateCounter();
  warmImages();
  controls();
}
function warmImages() {
  const nearby = images.slice(
    Math.max(0, index - bufferSize),
    index + bufferSize + 1,
  );
  const wanted = new Set([...recentImages, ...nearby].map(imageURL));
  for (const url of imageBuffer.keys()) {
    if (!wanted.has(url)) imageBuffer.delete(url);
  }
  for (const url of wanted) {
    if (imageBuffer.has(url)) continue;
    const image = new Image();
    image.src = url;
    imageBuffer.set(url, image);
    image.decode?.().catch(() => {});
  }
}
const labels = {
  accept: ["😊", "Accept"],
  reject: ["😢", "Reject"],
  favorite: ["⭐", "Favorite"],
  tshirt: ["👕", "T-shirt"],
};
function choiceBadge(element, action) {
  const [symbol, label] = labels[action];
  const icon = document.createElement("span");
  icon.textContent = symbol;
  const caption = document.createElement("span");
  caption.className = "choice-caption";
  caption.textContent = label;
  element.replaceChildren(icon, caption);
}
function positionFeedback() {
  const surface = galleryMode ? $("carousel") : $("stage");
  const bounds = surface.getBoundingClientRect?.();
  if (!bounds) return;
  const padding = Math.max(12, window.innerHeight - bounds.bottom + 12) + "px";
  $("gesture").style.paddingBottom = padding;
  $("confirmation").style.paddingBottom = padding;
}
function previewChoice(action, strength) {
  positionFeedback();
  choiceBadge($("gesture"), action);
  $("gesture").style.opacity = strength;
  $("gesture").style.transform = `scale(${0.65 + strength * 0.35})`;
}
function confirmChoice(action) {
  positionFeedback();
  if (!labels[action]) return;
  const [, label] = labels[action];
  const pop = document.createElement("div");
  pop.className = "confirmation-pop";
  pop.setAttribute("aria-hidden", "true");
  choiceBadge(pop, action);
  pop.addEventListener("animationend", () => pop.remove());
  $("gesture").style.opacity = 0;
  $("confirmation").replaceChildren(pop);
  $("announcement").textContent = label + " saved";
}
function updateCounter() {
  $("progress").textContent =
    (images.length ? index + 1 : 0) + " / " + images.length;
  if (images[index]) {
    try {
      localStorage.setItem(
        positionKey,
        JSON.stringify({ hash: images[index].hash, index }),
      );
    } catch (_) {}
  }
  $("progress").title = reviewedCount + " of " + totalCount + " reviewed";
}
function buildCarousel() {
  clearTimeout(scrollTimer);
  const rail = $("carousel");
  rail.replaceChildren();
  if (!images.length) {
    rail.textContent = "No images in this category yet.";
    return;
  }
  const items =
    images.length > 1 ? [images.at(-1), ...images, images[0]] : images;
  items.forEach((item, position) => {
    const card = document.createElement("div");
    card.className = "gallery-card";
    const image = document.createElement("img");
    image.src = imageURL(item);
    image.loading = "lazy";
    image.draggable = false;
    image.alt =
      "Illustration " + (((position - 1 + images.length) % images.length) + 1);
    const badge = document.createElement("span");
    badge.className = "category-badge";
    badge.textContent = [
      item.status === "accepted"
        ? "😊 Accepted"
        : item.status === "rejected"
          ? "😢 Rejected"
          : "To review",
      item.favorite ? "⭐ Favorite" : "",
      item.tshirt ? "👕 T-shirt" : "",
    ]
      .filter(Boolean)
      .join(" ");
    badge.setAttribute(
      "aria-label",
      [
        item.status || "Pending",
        item.favorite ? "Favorite" : "",
        item.tshirt ? "T-shirt" : "",
      ]
        .filter(Boolean)
        .join(", "),
    );
    card.append(image, badge);
    rail.append(card);
  });
  rail.scrollLeft = (index + (images.length > 1 ? 1 : 0)) * rail.clientWidth;
}
let scrollTimer;
$("carousel").addEventListener("scroll", () => {
  const rail = $("carousel");
  if (!rail.clientWidth || !images.length) return;
  const slot = Math.round(rail.scrollLeft / rail.clientWidth);
  index = images.length > 1 ? (slot - 1 + images.length) % images.length : 0;
  show();
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => {
    if (images.length > 1 && (slot === 0 || slot === images.length + 1)) {
      rail.scrollLeft = (index + 1) * rail.clientWidth;
    }
  }, 120);
});
window.addEventListener("resize", () => {
  if (galleryMode)
    $("carousel").scrollLeft =
      (index + (images.length > 1 ? 1 : 0)) * $("carousel").clientWidth;
});
async function load() {
  let previousHash = images[index]?.hash;
  let previousIndex = index;
  if (!loadedPosition) {
    try {
      const saved = JSON.parse(localStorage.getItem(positionKey) || "null");
      if (saved) {
        previousHash = saved.hash;
        previousIndex = saved.index || 0;
      }
    } catch (_) {}
  }
  $("error").textContent = "";
  try {
    const data = await request(api + "&" + new URLSearchParams({ category }));
    $("empty").querySelector("h2").textContent = "You’re all caught up.";
    images = data.images;
    index = images.findIndex((item) => item.hash === previousHash);
    if (index < 0)
      index = Math.min(previousIndex, Math.max(0, images.length - 1));
    loadedPosition = true;
    const counts = [
      data.counts.accepted || 0,
      data.counts.rejected || 0,
      data.counts.favorites || 0,
      data.counts.tshirt || 0,
    ];
    $("category-progress").textContent =
      counts.join("/") +
      " - " +
      counts.reduce((a, b) => a + b, 0) +
      "/" +
      data.total;
    const done = (data.counts.accepted || 0) + (data.counts.rejected || 0);
    $("progress").textContent = done + " / " + data.total;
    $("progress").title = done + " of " + data.total + " reviewed";
    reviewedCount = done;
    totalCount = data.total;
    if (exiting && images[index]) {
      const next = new Image();
      next.src = imageURL(images[index]);
      if (next.decode) await next.decode();
    }
    show();
    if (galleryMode) buildCarousel();
  } catch (e) {
    images = [];
    show();
    if (galleryMode) buildCarousel();
    $("empty").querySelector("h2").textContent = "Could not load the gallery.";
    error(e);
  }
}
async function send() {
  if (busy || !pending) return;
  busy = true;
  controls();
  try {
    const action = pending.action;
    const current = images.find((item) => item.hash === pending.hash);
    await request(api, pending);
    if (current && action !== "undo") {
      recentImages.push(current);
      recentImages.splice(0, Math.max(0, recentImages.length - bufferSize));
    }
    confirmChoice(action);
    localStorage.removeItem(storageKey);
    pending = null;
    $("retry").hidden = true;
    if (!galleryMode && category === "all" && action !== "undo" && current) {
      index = (images.findIndex((item) => item.hash === current.hash) + 1) % images.length;
    }
    await load();
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
async function act(action, fromSwipe = false) {
  if (busy || pending || (exiting && !fromSwipe)) return;
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
function navigateImage(direction) {
  if (busy || pending || exiting || images.length < 2) return;
  index = (index + direction + images.length) % images.length;
  show();
  if (galleryMode)
    $("carousel").scrollLeft = (index + 1) * $("carousel").clientWidth;
}
$("previous-image").onclick = () => navigateImage(-1);
$("next-image").onclick = () => navigateImage(1);
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
  if (
    galleryMode ||
    exiting ||
    busy ||
    pending ||
    !images[index] ||
    e.target.closest("button")
  )
    return;
  if (
    start ||
    e.isPrimary === false ||
    (e.button !== undefined && e.button !== 0)
  )
    return;
  start = { x: e.clientX, y: e.clientY, pointerId: e.pointerId };
  $("stage").classList.add("dragging");
  $("stage").setPointerCapture(e.pointerId);
});
$("stage").addEventListener("pointermove", (e) => {
  if (!start || e.pointerId !== start.pointerId) return;
  const x = e.clientX - start.x,
    y = e.clientY - start.y;
  const strength = Math.min(1, Math.max(Math.abs(x), Math.abs(y)) / 140);
  const reducedMotion = window.matchMedia?.(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  const tilt = reducedMotion ? 0 : Math.max(-14, Math.min(14, x / 18));
  $("hero").style.transform = `translate(${x}px,${y}px) rotate(${tilt}deg)`;
  $("hero").style.opacity = 1 - strength * 0.1;
  const action =
    Math.abs(x) > Math.abs(y)
      ? x > 0
        ? "accept"
        : "reject"
      : y < 0
        ? "favorite"
        : "tshirt";
  previewChoice(
    action,
    Math.max(Math.abs(x), Math.abs(y)) >= swipeThreshold ? 1 : 0,
  );
});
function clearGesture() {
  if (exiting) return;
  start = null;
  $("stage").classList.remove("dragging");
  $("hero").style.transform = "";
  $("hero").style.opacity = "";
  $("gesture").style.opacity = 0;
}
$("stage").addEventListener("pointerup", async (e) => {
  if (!start || e.pointerId !== start.pointerId) return;
  const x = e.clientX - start.x,
    y = e.clientY - start.y;
  if (Math.max(Math.abs(x), Math.abs(y)) < swipeThreshold) {
    clearGesture();
    if (Math.max(Math.abs(x), Math.abs(y)) < 10) {
      const rect = $("hero").getBoundingClientRect?.();
      if (rect && rect.width) {
        const edge = Math.min(64, rect.width * 0.18);
        if (e.clientX <= rect.left + edge) navigateImage(-1);
        else if (e.clientX >= rect.right - edge) navigateImage(1);
      }
    }
    return;
  }
  start = null;
  exiting = true;
  controls();
  const distance =
    Math.max(window.innerWidth || 1000, window.innerHeight || 1000) * 1.5;
  const scale = distance / Math.max(Math.abs(x), Math.abs(y));
  $("hero").style.transition = "transform 240ms ease-out";
  $("hero").style.transform =
    `translate(${x * scale}px,${y * scale}px) rotate(${Math.sign(x) * 18}deg)`;
  await new Promise((resolve) =>
    setTimeout(
      resolve,
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 240,
    ),
  );
  await act(
    Math.abs(x) > Math.abs(y)
      ? x > 0
        ? "accept"
        : "reject"
      : y < 0
        ? "favorite"
        : "tshirt",
    true,
  );
  exiting = false;
  $("hero").style.transition = "none";
  clearGesture();
  controls();
  // Commit the off-screen-to-center reset with transitions disabled before
  // restoring the normal drag-cancel transition. Otherwise browsers may
  // coalesce both style changes and animate the old card back into view.
  void $("hero").offsetWidth;
  $("hero").style.transition = "";
});
$("stage").addEventListener("pointercancel", clearGesture);
$("stage").addEventListener("lostpointercapture", clearGesture);
window.addEventListener("blur", clearGesture);
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input,button,a") || galleryMode || !name) return;
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
  $("title").textContent = "Review sessions";
  $("sessions-link").hidden = true;
  $("mode-toggle").hidden = true;
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
  const targetMode = galleryMode ? "swipe" : "gallery";
  const modeIcon = document.createElement("img");
  modeIcon.src = galleryMode ? "/pointing-hand.png" : "/view.png";
  modeIcon.alt = "";
  $("mode-toggle").replaceChildren(modeIcon);
  $("mode-toggle").setAttribute(
    "aria-label",
    "Switch to " + targetMode + " mode",
  );
  $("mode-toggle").title = "Switch to " + targetMode + " mode";
  $("mode-toggle").href =
    "/user/" +
    encodeURIComponent(name) +
    "?" +
    new URLSearchParams({
      pipeline,
      category:
        targetMode === "gallery" && category === "pending" ? "all" : category,
      preload: bufferSize,
      mode: targetMode,
    });
  $("title").textContent = name + " Space";
  document.title = name + " Space";
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
  load().then(() => {
    if (pending) {
      $("retry").hidden = false;
      error(Error("An earlier choice is waiting to be saved. Tap Retry."));
    }
  });
} else {
  sessions();
}
