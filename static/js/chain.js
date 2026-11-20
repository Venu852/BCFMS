(function () {
  "use strict";
  const { $, $$, escape, short, csrf, toast } = window.BCFMS;

  /* Render blocks into a .chain element. `blocks` come from the feed or the attack lab. */
  function renderChain(host, blocks, options) {
    options = options || {};
    const known = new Set($$(".block", host).map((el) => el.dataset.number));
    const html = blocks.map((block, index) => {
      const validated = typeof block.ok === "boolean";
      const state = validated ? (block.ok ? "ok" : "broken") : "";
      const fresh = options.markFresh && known.size && !known.has(String(block.number)) ? " fresh" : "";
      const tampered = block.tampered ? " tampered" : "";
      const errors = (block.errors || []).map((e) => "<li>" + escape(e) + "</li>").join("");
      const tag = options.link === false ? "div" : "a";
      const href = tag === "a" ? ' href="/ledger/block/' + block.number + '/"' : "";
      let link = "";
      if (index > 0) {
        const linked = block.parent_hash === blocks[index - 1].hash;
        link = '<div class="link ' + (validated ? (linked && block.ok ? "ok" : "broken") : "") + '"></div>';
      }
      return (
        link +
        "<" + tag + href + ' class="block ' + state + fresh + tampered + '" data-number="' + block.number + '">' +
        '<div class="no"><span>#' + block.number + "</span>" +
        (block.tx_ok === false ? '<span class="tag warn">reverted</span>' : "") + "</div>" +
        '<div class="act" title="' + escape(block.action) + '">' + escape(block.action) + "</div>" +
        '<div class="row"><span>hash</span><code>' + escape(short(block.hash, 5)) + "</code></div>" +
        '<div class="row"><span>prev</span><code>' + escape(short(block.parent_hash, 5)) + "</code></div>" +
        '<div class="by">' + escape(block.validator) + "</div>" +
        (errors ? '<ul class="errs">' + errors + "</ul>" : "") +
        "</" + tag + ">"
      );
    }).join("");
    host.innerHTML = html;
    if (options.scrollEnd !== false) host.scrollLeft = host.scrollWidth;
  }

  function initial(id) {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
  }

  /* ---- explorer: live strip + verification ---- */
  const live = $("#chain-live");
  if (live) {
    renderChain(live, initial("chain-data") || []);
    setInterval(() => {
      if (document.hidden) return;
      fetch(live.dataset.feed, { credentials: "same-origin" })
        .then((r) => r.json())
        .then((data) => renderChain(live, data.blocks, { markFresh: true, scrollEnd: false }))
        .catch(() => {});
    }, 5000);
  }

  $$("[data-verify]").forEach((button) => {
    button.addEventListener("click", () => {
      const out = $("#verify-result");
      const label = button.textContent;
      button.disabled = true;
      button.textContent = "Verifying…";
      const body = new URLSearchParams({ deep: button.dataset.verify });
      fetch(button.dataset.url, { method: "POST", headers: { "X-CSRFToken": csrf() }, body, credentials: "same-origin" })
        .then((r) => r.json())
        .then((report) => {
          const mode = report.deep ? "Deep verification (every transaction re-executed)" : "Standard verification";
          if (report.valid) {
            out.className = "notice good";
            out.innerHTML = "<div><b>Chain is valid</b><span>" + mode + ": " + report.blocks + " blocks and " +
              report.transactions + " transactions checked in " + report.elapsed_ms + " ms. Every hash, link, Merkle root and validator seal is intact.</span></div>";
          } else {
            out.className = "notice bad";
            out.innerHTML = "<div><b>Chain integrity FAILED</b><span>" + report.invalid_blocks.map((b) =>
              "Block #" + b.number + ": " + escape(b.errors.join("; "))).join("<br>") + "</span></div>";
          }
          out.hidden = false;
        })
        .catch(() => toast("Verification request failed.", "error"))
        .finally(() => { button.disabled = false; button.textContent = label; });
    });
  });

  /* ---- attack lab ---- */
  const lab = $("#attack-lab");
  if (!lab) return;
  const strip = $("#attack-chain");
  const honest = initial("chain-data") || [];
  const blockSelect = $("#attack-block");
  const result = $("#attack-result");
  let chosen = null;

  renderChain(strip, honest, { link: false });

  $$(".attack", lab).forEach((button) => {
    button.addEventListener("click", () => {
      $$(".attack", lab).forEach((b) => b.setAttribute("aria-pressed", b === button ? "true" : "false"));
      chosen = button.dataset.attack;
      $("#attack-block-field").hidden = button.dataset.needsBlock !== "1";
      $("#attack-run").disabled = false;
    });
  });

  $("#attack-reset").addEventListener("click", () => {
    renderChain(strip, honest, { link: false });
    result.hidden = true;
  });

  $("#attack-run").addEventListener("click", () => {
    if (!chosen) return;
    const params = new URLSearchParams({ attack: chosen, block: blockSelect.value });
    $("#attack-run").disabled = true;
    fetch(lab.dataset.url + "?" + params, { credentials: "same-origin" })
      .then((r) => r.json())
      .then(show)
      .catch(() => toast("The simulation could not be run.", "error"))
      .finally(() => { $("#attack-run").disabled = false; });
  });

  function show(data) {
    result.hidden = false;
    if (data.error) {
      result.innerHTML = '<div class="notice warn"><div><b>Nothing to attack yet</b><span>' + escape(data.error) + "</span></div></div>";
      return;
    }
    // Play it in two beats: the honest chain first, then the attacked copy.
    if (data.before) renderChain(strip, data.before, { link: false });
    setTimeout(() => renderChain(strip, data.blocks, { link: false, scrollEnd: false }), data.before ? 500 : 0);

    let html = '<div class="card"><header><div><h2>' + escape(data.title) + "</h2><p>Simulated on a copy. The real ledger was not changed.</p></div></header><div class=\"body stack\">";
    html += '<ol class="steps">' + data.steps.map((s) => "<li>" + escape(s).replace(/`([^`]+)`/g, "<code>$1</code>") + "</li>").join("") + "</ol>";

    if (data.edit) {
      html += '<dl class="diff"><dt>Field</dt><dd>' + escape(data.edit.field) + '</dd><dt>Original</dt><dd class="del">' +
        escape(data.edit.before) + '</dd><dt>Forged</dt><dd class="ins">' + escape(data.edit.after) + "</dd></dl>";
    }
    if (data.attempts) {
      html += '<div class="table-wrap"><table><thead><tr><th>Attempt</th><th>Signed by</th><th>Result</th></tr></thead><tbody>' +
        data.attempts.map((a) =>
          "<tr><td>" + escape(a.label) + '</td><td class="mono">' + escape(short(a.sender, 6)) + "</td><td>" +
          '<span class="tag ' + (a.status ? "bad" : "good") + '">' + (a.accepted ? (a.status ? "executed" : "reverted") : "rejected") + "</span>" +
          '<span class="sub">' + escape(a.message) + "</span></td></tr>").join("") + "</tbody></table></div>";
    }
    if (data.compare) {
      const c = data.compare;
      html += '<dl class="diff"><dt>FIR</dt><dd>' + escape(c.fir_number) + '</dd><dt>Sections</dt><dd><span class="del">' + escape(c.before) +
        '</span> → <span class="ins">' + escape(c.after) + '</span></dd><dt>On-chain</dt><dd>' + escape(c.onchain_hash) +
        "</dd><dt>Genuine</dt><dd>" + escape(c.genuine_hash) + (c.genuine_matches ? " ✓ matches" : "") +
        '</dd><dt>After edit</dt><dd class="del" style="text-decoration:none">' + escape(c.forged_hash) + " ✗ mismatch</dd></dl>";
    }
    const count = data.invalid_count ? " " + data.invalid_count + " block" + (data.invalid_count > 1 ? "s" : "") + " failed validation." : "";
    html += '<div class="verdict ' + (data.detected ? "good" : "bad") + '"><div><div class="big">' +
      (data.detected ? "Attack detected and rejected." : "Attack was NOT detected.") + escape(count) + "</div><div>" + escape(data.outcome) + "</div></div></div>";
    html += "</div></div>";
    result.innerHTML = html;
  }
})();
