(function () {
  "use strict";
  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

  window.BCFMS = {
    $, $$,
    escape(text) {
      const node = document.createElement("div");
      node.textContent = text == null ? "" : String(text);
      return node.innerHTML;
    },
    short(value, size) {
      size = size || 6;
      value = String(value || "");
      return value.length <= size * 2 + 5 ? value : value.slice(0, size + 2) + "…" + value.slice(-size);
    },
    csrf() {
      const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
      return match ? decodeURIComponent(match[1]) : "";
    },
    toast(message, kind) {
      let host = $(".toasts");
      if (!host) {
        host = document.createElement("div");
        host.className = "toasts";
        document.body.appendChild(host);
      }
      const toast = document.createElement("div");
      toast.className = "toast " + (kind || "");
      toast.setAttribute("role", "status");
      toast.textContent = message;
      host.appendChild(toast);
      dismissLater(toast);
    },
  };

  function dismissLater(toast) {
    const life = toast.classList.contains("error") ? 9000 : 5000;
    setTimeout(() => {
      toast.classList.add("leaving");
      setTimeout(() => toast.remove(), 320);
    }, life);
  }
  $$(".toast").forEach(dismissLater);

  // Theme
  const themeButton = $("[data-theme-toggle]");
  if (themeButton) {
    themeButton.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("bcfms-theme", next); } catch (e) { /* storage unavailable */ }
    });
  }

  // Mobile navigation
  const side = $(".side");
  const menuButton = $("[data-menu]");
  if (side && menuButton) {
    menuButton.addEventListener("click", () => side.classList.toggle("open"));
    document.addEventListener("click", (event) => {
      if (side.classList.contains("open") && !side.contains(event.target) && !menuButton.contains(event.target)) {
        side.classList.remove("open");
      }
    });
  }

  // Tabs: the open tab is kept in the URL hash so links and reloads land on it.
  $$("[data-tabs]").forEach((tabs) => {
    const buttons = $$("button[data-tab]", tabs);
    const show = (name, updateHash) => {
      if (!buttons.some((b) => b.dataset.tab === name)) return false;
      buttons.forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false"));
      $$(".tab-panel").forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
      if (updateHash) history.replaceState(null, "", "#" + name);
      return true;
    };
    buttons.forEach((b) => b.addEventListener("click", () => show(b.dataset.tab, true)));
    const fromHash = () => show(location.hash.slice(1), false);
    if (!fromHash()) show(buttons[0].dataset.tab, false);
    window.addEventListener("hashchange", fromHash);
  });

  // Copy-to-clipboard on hash chips
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    navigator.clipboard.writeText(button.dataset.copy).then(() => {
      const label = button.textContent;
      button.textContent = "copied";
      setTimeout(() => { button.textContent = label; }, 1200);
    });
  });

  // In-page confirmation for destructive forms
  document.addEventListener("submit", (event) => {
    const form = event.target;
    const message = form.dataset.confirm;
    if (!message || form.dataset.confirmed) return;
    event.preventDefault();
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true"><h2>Please confirm</h2><p></p>' +
      '<div class="actions"><button type="button" class="btn" data-no>Cancel</button>' +
      '<button type="button" class="btn primary" data-yes>Confirm</button></div></div>';
    $("p", back).textContent = message;
    document.body.appendChild(back);
    const close = () => back.remove();
    $("[data-no]", back).addEventListener("click", close);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    $("[data-yes]", back).addEventListener("click", () => {
      form.dataset.confirmed = "1";
      close();
      form.requestSubmit();
    });
    $("[data-yes]", back).focus();
  });

  // Demo account shortcuts on the sign-in page
  $$("[data-demo-user]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#id_username").value = button.dataset.demoUser;
      $("#id_password").value = button.dataset.demoPass;
      $("#id_password").focus();
    });
  });
})();
