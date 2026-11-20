(function () {
  "use strict";
  const { $, $$, escape, csrf } = window.BCFMS;
  const root = $("#assistant");
  if (!root) return;
  const log = $("#chat-log");
  const form = $("#chat-form");
  const input = $("#chat-question");
  const scope = $("#chat-scope");
  const send = $("#chat-send");
  let counter = 0;

  function add(node) {
    const welcome = $("#chat-welcome");
    if (welcome) welcome.remove();
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  function sourceCard(source, id) {
    return (
      '<a class="source' + (source.cited ? "" : " uncited") + '" id="' + id + "-" + source.tag + '" href="' + escape(source.url) + '" target="_blank" rel="noopener">' +
      '<div class="head"><span class="cite">' + source.tag + "</span><span>" + escape(source.fir_number) + "</span>" +
      '<span class="muted">' + escape(source.label) + "</span></div>" +
      '<div class="snip">' + escape(source.snippet) + "</div></a>"
    );
  }

  function renderAnswer(data) {
    const id = "a" + ++counter;
    // Escape first, then turn [S1] / [S1, S2] markers into clickable citation chips.
    let body = escape(data.answer).replace(/\[((?:S\d+)(?:\s*,\s*S\d+)*)\]/g, (match, group) =>
      group.split(/\s*,\s*/).map((tag) => '<button type="button" class="cite" data-jump="' + id + "-" + tag + '">' + tag + "</button>").join(" "));
    body = body.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/^\s*[*-]\s+/gm, "• ");

    const cited = data.sources.filter((s) => s.cited);
    const others = data.sources.filter((s) => !s.cited);
    const primary = data.mode === "generated" ? cited : data.sources;
    let sources = "";
    if (primary.length) sources += '<div class="sources">' + primary.map((s) => sourceCard(s, id)).join("") + "</div>";
    if (data.mode === "generated" && others.length) {
      sources += '<details class="more-sources"><summary>' + others.length + " more passage" + (others.length > 1 ? "s" : "") +
        ' retrieved but not cited</summary><div class="sources">' + others.map((s) => sourceCard(s, id)).join("") + "</div></details>";
    }
    const modes = { generated: "Gemma 4 · answered from case files", extractive: "Model unavailable · passages only", none: "No matching passages" };
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = '<div class="bubble">' + body + "</div>" + sources +
      '<div class="meta-line"><span>' + modes[data.mode] + "</span><span>" + (data.latency_ms / 1000).toFixed(1) + " s</span>" +
      (data.model_error ? "<span>" + escape(data.model_error) + "</span>" : "") + "</div>";
    return el;
  }

  log.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-jump]");
    if (!chip) return;
    const target = document.getElementById(chip.dataset.jump);
    if (!target) return;
    const hidden = target.closest("details");
    if (hidden) hidden.open = true;
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
    target.classList.add("flash");
    setTimeout(() => target.classList.remove("flash"), 1400);
  });

  function ask(question) {
    question = question.trim();
    if (!question || send.disabled) return;
    const mine = document.createElement("div");
    mine.className = "msg user";
    mine.textContent = question;
    add(mine);
    const waiting = document.createElement("div");
    waiting.className = "msg bot";
    waiting.innerHTML = '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span> <span class="muted small">Searching the case files you can access…</span></div>';
    add(waiting);
    input.value = "";
    send.disabled = true;

    fetch(root.dataset.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ question: question, case: scope.value }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        waiting.remove();
        if (!ok) {
          const err = document.createElement("div");
          err.className = "msg bot";
          err.innerHTML = '<div class="bubble">' + escape(data.error || "Something went wrong. Please try again.") + "</div>";
          add(err);
        } else {
          add(renderAnswer(data));
        }
      })
      .catch(() => {
        waiting.remove();
        const err = document.createElement("div");
        err.className = "msg bot";
        err.innerHTML = '<div class="bubble">The assistant could not be reached. Check that the server is running and try again.</div>';
        add(err);
      })
      .finally(() => { send.disabled = false; input.focus(); });
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(input.value); }
  });
  $$("[data-ask]").forEach((b) => b.addEventListener("click", () => ask(b.dataset.ask)));
})();
