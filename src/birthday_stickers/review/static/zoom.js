/* Temporary two-finger zoom, isolated from review and navigation gestures. */
window.createPinchZoom = function ({
  surface,
  getImage,
  canStart,
  onStart,
  onChange,
}) {
  let active = false,
    settling = false,
    fingers = 0;
  let overlay = null,
    source = null,
    initial = null,
    restoreTimer = null;
  let suppressClickUntil = 0;
  const midpoint = (touches) => ({
    x: (touches[0].clientX + touches[1].clientX) / 2,
    y: (touches[0].clientY + touches[1].clientY) / 2,
  });
  const distance = (touches) =>
    Math.hypot(
      touches[1].clientX - touches[0].clientX,
      touches[1].clientY - touches[0].clientY,
    );
  function release() {
    overlay?.remove();
    if (source) source.style.visibility = "";
    overlay = source = null;
    settling = false;
    active = fingers > 0;
    onChange();
  }
  function bounce() {
    if (!overlay || settling) return;
    settling = true;
    suppressClickUntil = Date.now() + 500;
    const reduced = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    overlay.style.transition = reduced
      ? "none"
      : "transform 320ms cubic-bezier(.22,1.35,.36,1)";
    overlay.style.transform = "translate(0px, 0px) scale(1)";
    restoreTimer = setTimeout(release, reduced ? 0 : 340);
  }
  surface.addEventListener(
    "touchstart",
    (event) => {
      fingers = event.touches.length;
      if (active) {
        event.preventDefault();
        return;
      }
      if (fingers !== 2 || !canStart()) return;
      const image = getImage();
      if (!image || image.hidden || !image.complete || !image.naturalWidth)
        return;
      onStart();
      const rect = image.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      source = image;
      initial = {
        distance: Math.max(1, distance(event.touches)),
        center: midpoint(event.touches),
      };
      overlay = image.cloneNode(false);
      overlay.removeAttribute("id");
      overlay.removeAttribute("hidden");
      overlay.setAttribute("aria-hidden", "true");
      overlay.className = "pinch-image";
      overlay.style.cssText = `left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px;transform-origin:${initial.center.x - rect.left}px ${initial.center.y - rect.top}px;`;
      document.body.append(overlay);
      source.style.visibility = "hidden";
      active = true;
      event.preventDefault();
      onChange();
    },
    { passive: false, capture: true },
  );
  surface.addEventListener(
    "touchmove",
    (event) => {
      if (!active) return;
      event.preventDefault();
      if (settling || !overlay || event.touches.length < 2) return;
      const center = midpoint(event.touches);
      const scale = Math.max(
        1,
        Math.min(3, distance(event.touches) / initial.distance),
      );
      overlay.style.transform = `translate(${center.x - initial.center.x}px, ${center.y - initial.center.y}px) scale(${scale})`;
    },
    { passive: false, capture: true },
  );
  function end(event) {
    fingers = event.touches.length;
    if (!active) return;
    event.preventDefault();
    if (fingers < 2) bounce();
    if (!fingers && !overlay) {
      active = false;
      onChange();
    }
  }
  surface.addEventListener("touchend", end, { passive: false, capture: true });
  surface.addEventListener("touchcancel", end, {
    passive: false,
    capture: true,
  });
  surface.addEventListener(
    "click",
    (event) => {
      if (active || Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true,
  );
  window.addEventListener("blur", () => {
    fingers = 0;
    clearTimeout(restoreTimer);
    release();
  });
  return {
    get active() {
      return active;
    },
  };
};
