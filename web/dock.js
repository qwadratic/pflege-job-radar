/* Autopilot dock — self-contained side inbox for the job-board pages (docs/autopilot.md "The dock").
   No globals except window.PJDock{open,close,setPosition}. No dependencies. Never breaks the host page
   if /api/autopilot is down: every fetch is wrapped, failure just shows "Autopilot offline".
   Gated on the public "chats_dock" feature flag (off by default, Settings > Feature flags): while it is
   off this file does nothing at all -- no DOM, no polling, no request to /api/autopilot/* -- so turning
   the flag on is the only way to make the dock appear again on the next page load. */
(function () {
  if (window.PJDock) return;
  fetch("/api/flags").then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })
    .then(function (flags) { if (flags && flags.chats_dock) boot(); });

  function boot() {
  var API = "/api/autopilot";
  var LS_KEY = "pj_dock";
  var POLL_MS = 30000;

  // ---- i18n (host's own LANG choice: localStorage 'lang', same key both job-board pages use) --------------
  var I18N = {
    de: { chats: "Chats", offline: "Autopilot offline", all: "Alle", reply: "Antwort", manager: "Manager", paused: "Pausiert",
      search_ph: "Suchen …", empty: "Nichts gefunden.", back: "← zurück", send: "Senden", suggest: "Luna vorschlagen",
      approve: "Freigeben", reject: "Ablehnen", ctx_clinic: "Diese Klinik an … senden", ctx_job: "Diese Stelle an … senden",
      ctx_matches: "passende Kandidatinnen", ctx_pick_ph: "Kandidatin suchen …", ctx_send: "Senden", sent_ok: "Gesendet.",
      sent_err: "Fehler beim Senden.", mode_luna: "Luna", mode_paused: "Pausiert", mode_human: "Mensch", mode_manager: "Manager",
      mode_stopped: "Gestoppt", esc: "Esc schließt", loading: "Lade …" },
    en: { chats: "Chats", offline: "Autopilot offline", all: "All", reply: "Reply", manager: "Manager", paused: "Paused",
      search_ph: "Search …", empty: "Nothing found.", back: "← back", send: "Send", suggest: "Suggest (Luna)",
      approve: "Approve", reject: "Reject", ctx_clinic: "Send this hospital to …", ctx_job: "Send this job to …",
      ctx_matches: "matching candidates", ctx_pick_ph: "Search a candidate …", ctx_send: "Send", sent_ok: "Sent.",
      sent_err: "Failed to send.", mode_luna: "Luna", mode_paused: "Paused", mode_human: "Human", mode_manager: "Manager",
      mode_stopped: "Stopped", esc: "Esc closes", loading: "Loading …" }
  };
  function lang() { var l = (localStorage.getItem("lang") || navigator.language || "de").slice(0, 2); return I18N[l] ? l : "de"; }
  function t(k) { return I18N[lang()][k] || k; }

  // ---- tiny DOM helper, same idiom as the host pages ------------------------------------------------------
  var el = function (tag, a, k) {
    a = a || {}; var e = document.createElement(tag);
    for (var x in a) { var y = a[x]; if (y == null || y === false) continue;
      if (x === "text") e.textContent = y; else if (x === "html") e.innerHTML = y;
      else if (x.indexOf("on") === 0) e.addEventListener(x.slice(2), y); else e.setAttribute(x, y === true ? "" : y); }
    (k || []).forEach(function (c) { if (c == null || c === false) return; e.append(c.nodeType ? c : document.createTextNode(c)); });
    return e;
  };

  // ---- state -----------------------------------------------------------------------------------------------
  var DEFAULT_STATE = { position: "right", open: false, width: 380 };
  var state;
  try { state = Object.assign({}, DEFAULT_STATE, JSON.parse(localStorage.getItem(LS_KEY) || "{}")); }
  catch (e) { state = Object.assign({}, DEFAULT_STATE); }
  var offline = false;
  var overview = null;
  var view = { tab: "all", q: "", thread: null }; // thread = conversation id or null (list view)

  function persist() { try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch (e) {} }

  // ---- fetch helpers: never throw into the host page --------------------------------------------------------
  function api(path, opt) {
    return fetch(API + path, Object.assign({ headers: { "content-type": "application/json" } }, opt || {}))
      .then(function (r) { if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) { return Promise.reject(b.error || r.status); }); return r.json(); })
      .then(function (j) { offline = false; return j; })
      .catch(function (e) { offline = true; throw e; });
  }
  function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body || {}) }); }

  // ---- DOM scaffold -------------------------------------------------------------------------------------------
  var root = el("div", { id: "pj-dock", role: "complementary", "aria-label": "Autopilot" });
  var launcher = el("button", { id: "pj-launcher", "aria-label": "Autopilot" }, [
    el("span", { class: "dot" }), el("span", { id: "pj-launcher-txt", text: t("chats") })]);
  var posBtns = {};
  var POSITIONS = [["right", "⟩|"], ["left", "|⟨"], ["bottom", "▁"], ["minimised", "—"], ["hidden", "×"]];
  var posGroup = el("span", { id: "pj-pos" }, POSITIONS.map(function (p) {
    var b = el("button", { type: "button", title: p[0], onclick: function () { setPosition(p[0]); } }, [p[1]]);
    posBtns[p[0]] = b; return b;
  }));
  var hdTitle = el("span", { class: "ttl", id: "pj-hd-title" }, [t("chats")]);
  var closeBtn = el("button", { id: "pj-close", "aria-label": "close", onclick: close }, ["×"]);
  var hd = el("div", { id: "pj-hd" }, [hdTitle, posGroup, closeBtn]);
  var ctxBox = el("div", { id: "pj-ctx", style: "display:none" });
  var tabsBox = el("div", { id: "pj-tabs" });
  var searchInput = el("input", { placeholder: t("search_ph"), "aria-label": "search" });
  var searchBox = el("div", { id: "pj-search" }, [searchInput]);
  var body = el("div", { id: "pj-body" });
  var panel = el("div", { id: "pj-panel" }, [hd, ctxBox, tabsBox, searchBox, body]);
  root.append(launcher, panel);
  document.body.appendChild(root);
  launcher.addEventListener("click", open);

  var TABS = [["all", "all"], ["needs_reply", "reply"], ["manager", "manager"], ["paused", "paused"]];
  var tabBtns = {};
  TABS.forEach(function (tb) {
    var b = el("button", { type: "button", onclick: function () { view.tab = tb[0]; view.thread = null; renderTabs(); loadList(); } });
    tabBtns[tb[0]] = b; tabsBox.append(b);
  });
  var searchDeb;
  searchInput.addEventListener("input", function () {
    clearTimeout(searchDeb); var v = searchInput.value;
    searchDeb = setTimeout(function () { view.q = v; if (!view.thread) loadList(); }, 250);
  });

  // ---- position / visibility -------------------------------------------------------------------------------
  // "position" is where the panel docks when shown (one of the 5 header buttons, incl. "hidden" = pill only,
  // chosen explicitly); "open" is whether it is currently shown at all (X / Esc close it without forgetting
  // the docked position, so the launcher reopens it in the same place).
  function visible() { return state.open && state.position !== "hidden"; }
  function applyPosition() {
    root.className = "pos-" + state.position + (visible() ? "" : " hidden");
    Object.keys(posBtns).forEach(function (k) { posBtns[k].setAttribute("aria-pressed", k === state.position); });
    var hostBar = document.querySelector(".topbar, header.top");
    var topOff = hostBar ? Math.round(hostBar.getBoundingClientRect().bottom) : 0;
    panel.style.setProperty("--pj-top", topOff + "px");
    panel.style.setProperty("--pj-w", (state.width || 380) + "px");
  }
  function setPosition(p) { state.position = p; state.open = true; persist(); applyPosition(); if (visible()) loadList(); }
  function open() { if (state.position === "hidden") state.position = "right"; state.open = true; persist(); applyPosition(); loadList(); }
  function close() { state.open = false; persist(); applyPosition(); }
  window.PJDock = { open: open, close: close, setPosition: setPosition };
  applyPosition();

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && visible()) { close(); return; }
    if (e.key !== "c") return;
    var a = document.activeElement, tag = a && a.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (a && a.isContentEditable)) return;
    visible() ? close() : open();
  });

  // ---- launcher / overview poll -----------------------------------------------------------------------------
  function refreshOverview() {
    api("/overview").then(function (o) {
      overview = o;
      var n = o.counts.needs_reply || 0;
      document.getElementById("pj-launcher-txt").textContent = t("chats") + " · " + n;
      launcher.classList.toggle("has-alert", (o.counts.manager || 0) > 0);
      renderTabs();
    }).catch(function () {
      document.getElementById("pj-launcher-txt").textContent = t("offline");
      if (!view.thread) renderOffline();
    });
  }
  function renderTabs() {
    TABS.forEach(function (tb) {
      var key = tb[0], label = t(tb[1]);
      var n = key === "all" ? null : (overview && overview.counts[key]);
      tabBtns[key].textContent = label + (n != null ? " " + n : "");
      tabBtns[key].setAttribute("aria-pressed", view.tab === key);
    });
  }
  refreshOverview(); setInterval(refreshOverview, POLL_MS);

  // ---- helpers: avatar, time, tag ---------------------------------------------------------------------------
  function ago(s) {
    if (!s) return "–";
    var d = (Date.now() - new Date(s)) / 864e5;
    if (d < 1) return lang() === "de" ? "heute" : "today";
    if (d < 2) return lang() === "de" ? "gestern" : "yesterday";
    return Math.round(d) + (lang() === "de" ? "T" : "d");
  }
  function tagFor(r) {
    if (r.mode === "manager") return ["MANAGER", "manager"];
    if (r.mode === "stopped") return null;
    if (r.next_actor === "us") return [lang() === "de" ? "ANTWORT NÖTIG" : "REPLY NEEDED", "reply"];
    if (r.mode === "luna") return [lang() === "de" ? "LUNA AKTIV" : "LUNA ACTIVE", "active"];
    if (r.mode === "paused") return [lang() === "de" ? "LUNA PAUSIERT" : "LUNA PAUSED", "paused"];
    return null;
  }
  function avatar(r) { return el("div", { class: "pj-av", style: "background:" + (r.avatar_color || "#ccc") }, [r.initials || "?"]); }

  // ---- list view --------------------------------------------------------------------------------------------
  function renderOffline() { body.replaceChildren(el("div", { id: "pj-offline", text: t("offline") })); }
  function loadList() {
    body.replaceChildren(el("p", { class: "pj-empty", text: t("loading") }));
    var qs = "?limit=40" + (view.tab !== "all" ? "&view=" + view.tab : "") + (view.q ? "&q=" + encodeURIComponent(view.q) : "");
    api("/conversations" + qs).then(function (res) {
      if (view.thread) return; // a row was clicked while this was in flight
      if (!res.rows.length) { body.replaceChildren(el("p", { class: "pj-empty", text: t("empty") })); return; }
      body.replaceChildren.apply(body, res.rows.map(rowEl));
    }).catch(renderOffline);
  }
  function rowEl(r) {
    var tag = tagFor(r);
    return el("button", { type: "button", class: "pj-row", onclick: function () { openThread(r.id); } }, [
      avatar(r),
      el("div", { class: "main" }, [
        el("div", { class: "l1" }, [el("span", { class: "name", text: r.name || "" }), el("span", { class: "time", text: ago(r.last_at) })]),
        el("div", { class: "phone", text: r.phone_masked || r.email || "" }),
        el("div", { class: "prev", text: r.last_preview || "" }),
        tag ? el("span", { class: "pj-tag " + tag[1], text: tag[0] }) : null])]);
  }

  // ---- thread view ------------------------------------------------------------------------------------------
  var MODE_BTNS = ["luna", "paused", "human", "manager", "stopped"];
  function openThread(id) {
    view.thread = id;
    body.replaceChildren(el("p", { class: "pj-empty", text: t("loading") }));
    loadThread();
  }
  function loadThread() {
    var id = view.thread;
    post("/conversations/" + id + "/action", { action: "mark_read" }).catch(function () {});
    api("/conversations/" + id).then(function (d) { if (view.thread === id) renderThread(d); }).catch(renderOffline);
  }
  function renderThread(d) {
    var cv = d.conversation;
    var msgs = el("div", { id: "pj-msgs" });
    (d.messages || []).forEach(function (m) {
      var appr = m.status === "pending_approval" ? (d.approvals || []).filter(function (a) { return (a.context || {}).message_id === m.id; })[0] : null;
      var bub = el("div", { class: "pj-b " + (m.dir === "out" ? "out" : "in") + (m.status === "pending_approval" ? " pending" : ""), text: m.text }, []);
      bub.append(el("span", { class: "meta", text: (m.author || "") + " · " + ago(m.at) + (m.status && m.status !== "sent" ? " · " + m.status : "") }));
      if (appr) {
        var actions = el("div", { class: "pj-appr" }, [
          el("button", { class: "ok", onclick: function () { decideApproval(appr.id, "approve"); } }, [t("approve")]),
          el("button", { class: "no", onclick: function () { decideApproval(appr.id, "reject"); } }, [t("reject")])]);
        bub.append(actions);
      }
      msgs.append(bub);
    });
    var modes = el("div", { id: "pj-modes" }, MODE_BTNS.map(function (m) {
      return el("button", { type: "button", "aria-pressed": cv.mode === m, onclick: function () { changeMode(m); } }, [t("mode_" + m)]);
    }));
    var thHd = el("div", { id: "pj-th-hd" }, [
      el("button", { class: "back", type: "button", onclick: function () { view.thread = null; loadList(); } }, [t("back")]),
      el("div", { class: "who" }, [el("span", { class: "name", text: cv.name || (d.candidate && d.candidate.name) || (d.clinic_thread && d.clinic_thread.clinic_name) || "" }), el("span", { class: "state", text: cv.state || "" })]),
      modes]);
    var ta = el("textarea", { placeholder: lang() === "de" ? "Nachricht …" : "Message …" });
    var composer = el("div", { id: "pj-composer" }, [ta, el("div", { class: "row" }, [
      el("button", { class: "sug", type: "button", onclick: function () {
        post("/conversations/" + cv.id + "/action", { action: "suggest" }).then(function (r) { ta.value = (r.result || {}).text || ""; }).catch(function () {});
      } }, [t("suggest")]),
      el("button", { class: "send", type: "button", onclick: function () {
        var txt = ta.value.trim(); if (!txt) return;
        post("/conversations/" + cv.id + "/send", { text: txt, as: "operator" }).then(function () { ta.value = ""; loadThread(); }).catch(function () {});
      } }, [t("send")])])]);
    body.replaceChildren(el("div", { id: "pj-thread" }, [thHd, msgs, composer]));
    msgs.scrollTop = msgs.scrollHeight;
  }
  function changeMode(mode) {
    if (!view.thread) return;
    post("/conversations/" + view.thread + "/mode", { mode: mode }).then(loadThread).catch(function () {});
  }
  function decideApproval(id, decision) {
    post("/approvals/" + id, { decision: decision }).then(loadThread).catch(function () {});
  }

  // ---- context: read the HOST page's location.hash (#/clinic/:id, #/job/:id) --------------------------------
  function toast(msg) {
    var el2 = document.getElementById("pj-toast") || el("div", { id: "pj-toast" });
    el2.textContent = msg; if (!el2.parentNode) body.appendChild(el2);
    clearTimeout(toast._h); toast._h = setTimeout(function () { el2.remove(); }, 3200);
  }
  function renderCtx() {
    var h = location.hash;
    var mClinic = h.match(/^#\/clinic\/([^/?]+)/);
    var mJob = h.match(/^#\/job\/(\d+)/);
    if (!mClinic && !mJob) { ctxBox.style.display = "none"; ctxBox.replaceChildren(); return; }
    ctxBox.style.display = "";
    var clinicId = mClinic ? decodeURIComponent(mClinic[1]) : null;
    var postingId = mJob ? mJob[1] : null;
    var picked = el("div", {});
    var search = el("input", { placeholder: t("ctx_pick_ph") });
    var matchesLink = el("p", {}, []);
    var box = el("div", {}, [el("p", { class: "h", text: clinicId ? t("ctx_clinic") : t("ctx_job") }), search, picked, matchesLink]);
    ctxBox.replaceChildren(box);
    var qParam = clinicId ? "clinic_id=" + encodeURIComponent(clinicId) : "posting_id=" + postingId;
    api("/matches?" + qParam + "&status=proposed,clinic_interested,sent").catch(function () { return { rows: [] }; })
      .then(function (r) { r = r || { rows: [] }; var n = (r.rows || []).length;
        matchesLink.replaceChildren(el("a", { href: "/autopilot#/matching" }, [n + " " + t("ctx_matches")])); });
    var deb;
    function loadCandidates(q) {
      picked.replaceChildren(el("p", { class: "pj-empty", text: t("loading") }));
      api("/candidates?stage=qualified,matching&limit=20" + (q ? "&q=" + encodeURIComponent(q) : "")).then(function (r) {
        picked.replaceChildren.apply(picked, (r.rows || []).map(function (c) {
          return el("div", { class: "cand" }, [el("span", {}, [c.initials + " · " + (c.role_class || "") + " · " + (c.city || "")]),
            el("button", { type: "button", onclick: function () { sendToCandidate(c); } }, [t("ctx_send")])]);
        }));
        if (!(r.rows || []).length) picked.replaceChildren(el("p", { class: "pj-empty", text: t("empty") }));
      }).catch(function () { picked.replaceChildren(el("p", { class: "pj-empty", text: t("offline") })); });
    }
    function sendToCandidate(c) {
      if (!c.conversation_id) { toast(t("sent_err")); return; }
      var params = clinicId ? { action: "share_posting", clinic_id: clinicId } : { action: "share_posting", posting_id: postingId };
      post("/conversations/" + c.conversation_id + "/action", params).then(function () { toast(t("sent_ok")); }).catch(function () { toast(t("sent_err")); });
    }
    search.addEventListener("input", function () { clearTimeout(deb); var v = search.value; deb = setTimeout(function () { loadCandidates(v); }, 250); });
    loadCandidates("");
  }
  window.addEventListener("hashchange", renderCtx);
  renderCtx();

  // ---- boot --------------------------------------------------------------------------------------------------
  renderTabs();
  if (visible()) loadList();
  } // end boot() -- gated on the chats_dock feature flag, see top of file
})();
