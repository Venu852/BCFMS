(function () {
  "use strict";
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // Text-size controls (A- / A / A+). The choice is remembered on this device.
  const SIZES = { "-1": "93.75%", "0": "100%", "1": "112.5%" };
  const setSize = (key) => {
    document.documentElement.style.fontSize = SIZES[key] || "100%";
    try { localStorage.setItem("bcfms-text-size", key); } catch (e) { /* storage unavailable */ }
  };
  try { setSize(localStorage.getItem("bcfms-text-size") || "0"); } catch (e) { /* storage unavailable */ }
  $$("[data-size]").forEach((button) => button.addEventListener("click", () => setSize(button.dataset.size)));

  /* ---- 3D node network behind the hero: points on a sphere, perspective-projected ---- */
  const canvas = $("#net");
  if (canvas) {
    const ctx = canvas.getContext("2d");
    const COUNT = 110, RADIUS = 1, FOCAL = 2.6, LINK = 0.42;
    const points = [];
    for (let i = 0; i < COUNT; i++) {
      // Fibonacci sphere gives an even spread without clumping at the poles.
      const y = 1 - (i / (COUNT - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const a = i * Math.PI * (3 - Math.sqrt(5));
      points.push({ x: Math.cos(a) * r * RADIUS, y: y * RADIUS, z: Math.sin(a) * r * RADIUS, gold: i % 9 === 0 });
    }
    // The shape is rigid, so which points are linked never changes: compute once.
    const links = [];
    for (let i = 0; i < COUNT; i++) {
      for (let j = i + 1; j < COUNT; j++) {
        const dx = points[i].x - points[j].x, dy = points[i].y - points[j].y, dz = points[i].z - points[j].z;
        if (dx * dx + dy * dy + dz * dz < LINK * LINK) links.push([i, j]);
      }
    }
    let width = 0, height = 0, visible = true, angle = 0, tiltTarget = 0, tilt = 0;
    const projected = new Array(COUNT);

    function resize() {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function draw() {
      ctx.clearRect(0, 0, width, height);
      const size = Math.min(width, height) * 0.62;
      const cx = width * 0.74, cy = height * 0.46;
      const cosY = Math.cos(angle), sinY = Math.sin(angle);
      const cosX = Math.cos(0.32 + tilt), sinX = Math.sin(0.32 + tilt);
      for (let i = 0; i < COUNT; i++) {
        const p = points[i];
        const x = p.x * cosY - p.z * sinY;
        const z0 = p.x * sinY + p.z * cosY;
        const y = p.y * cosX - z0 * sinX;
        const z = p.y * sinX + z0 * cosX;
        const scale = FOCAL / (FOCAL + z);
        projected[i] = { x: cx + x * size * scale, y: cy + y * size * scale, depth: (1 - z) / 2, scale };
      }
      ctx.lineWidth = 1;
      for (const [i, j] of links) {
        const a = projected[i], b = projected[j];
        const alpha = Math.min(a.depth, b.depth) * 0.16;
        if (alpha < 0.015) continue;
        ctx.strokeStyle = "rgba(20,56,127," + alpha.toFixed(3) + ")";
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      for (let i = 0; i < COUNT; i++) {
        const p = projected[i];
        const alpha = 0.08 + p.depth * 0.42;
        ctx.fillStyle = points[i].gold ? "rgba(255,153,51," + (alpha + 0.2).toFixed(3) + ")" : "rgba(20,56,127," + alpha.toFixed(3) + ")";
        ctx.beginPath();
        ctx.arc(p.x, p.y, (points[i].gold ? 2.6 : 1.7) * p.scale, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function frame() {
      if (visible && !document.hidden) {
        angle += 0.0016;
        tilt += (tiltTarget - tilt) * 0.04;
        draw();
      }
      requestAnimationFrame(frame);
    }

    window.addEventListener("resize", () => { resize(); draw(); });
    new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; }).observe(canvas);
    window.addEventListener("pointermove", (e) => { tiltTarget = (e.clientY / window.innerHeight - 0.5) * 0.25; }, { passive: true });
    resize();
    draw();
    if (!still) requestAnimationFrame(frame);
  }

  /* ---- hero product scene follows the pointer ---- */
  const scene = $("#scene"), stage = $("#stage");
  if (scene && stage && !still) {
    let rx = 9, ry = -18, tx = 9, ty = -18, running = false;
    const step = () => {
      rx += (tx - rx) * 0.08;
      ry += (ty - ry) * 0.08;
      stage.style.setProperty("--rx", rx.toFixed(2) + "deg");
      stage.style.setProperty("--ry", ry.toFixed(2) + "deg");
      if (Math.abs(tx - rx) > 0.02 || Math.abs(ty - ry) > 0.02) requestAnimationFrame(step);
      else running = false;
    };
    const aim = (x, y) => {
      tx = x; ty = y;
      if (!running) { running = true; requestAnimationFrame(step); }
    };
    const hero = $(".hero");
    hero.addEventListener("pointermove", (e) => {
      const box = hero.getBoundingClientRect();
      const px = (e.clientX - box.left) / box.width - 0.5;
      const py = (e.clientY - box.top) / box.height - 0.5;
      aim(9 - py * 12, -18 + px * 20);
    });
    hero.addEventListener("pointerleave", () => aim(9, -18));
  }

  /* ---- cards tilt toward the pointer ---- */
  if (!still) {
    $$(".tilt").forEach((card) => {
      card.addEventListener("pointermove", (e) => {
        const box = card.getBoundingClientRect();
        const px = (e.clientX - box.left) / box.width - 0.5;
        const py = (e.clientY - box.top) / box.height - 0.5;
        card.style.transform = "perspective(900px) rotateX(" + (-py * 7).toFixed(2) + "deg) rotateY(" + (px * 9).toFixed(2) + "deg) translateY(-4px)";
      });
      card.addEventListener("pointerleave", () => { card.style.transform = ""; });
    });
  }

  /* ---- reveal on scroll, count-up figures, typed answer ---- */
  function countUp(node) {
    const target = Number(node.dataset.count);
    if (still || !target) return;
    const started = performance.now();
    const tick = (now) => {
      const t = Math.min((now - started) / 1100, 1);
      node.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function type(node) {
    const text = node.dataset.text;
    if (still) { node.textContent = text; node.classList.add("done"); return; }
    let i = 0;
    const timer = setInterval(() => {
      i += 2;
      node.textContent = text.slice(0, i);
      if (i >= text.length) { clearInterval(timer); node.classList.add("done"); }
    }, 22);
  }

  const seen = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const el = entry.target;
      seen.unobserve(el);
      if (el.classList.contains("reveal")) el.classList.add("in");
      if (el.dataset.count) countUp(el);
      if (el.id === "typed") type(el);
    });
  }, { threshold: 0.12 });
  $$(".reveal, [data-count], #typed").forEach((el) => seen.observe(el));
})();
