// Site scripts. Kept in one external file so the Content Security Policy can forbid inline scripts.

// 1. Render math with KaTeX, then keep trailing punctuation glued to inline formulas.
document.addEventListener("DOMContentLoaded", function () {
  if (typeof renderMathInElement !== "function") return;
  renderMathInElement(document.body, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false }
    ],
    macros: {
      "\\R": "\\mathbb{R}",
      "\\N": "\\mathbb{N}",
      "\\Z": "\\mathbb{Z}",
      "\\Q": "\\mathbb{Q}",
      "\\C": "\\mathbb{C}",
      "\\eps": "\\varepsilon",
      "\\abs": "\\left|#1\\right|",
      "\\norm": "\\left\\lVert#1\\right\\rVert",
      "\\ensuremath": "\\TextOrMath{$#1$}{#1}"
    },
    throwOnError: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"]
  });
  document.querySelectorAll(".katex:not(.katex-display .katex)").forEach(function (k) {
    var el = k;
    // Climb only through wrappers that hold nothing but the formula. A coloured sentence that ends in a
    // formula is not one of them: wrapping it whole would keep the entire sentence on one line.
    while (!el.nextSibling && !el.previousSibling && el.parentNode && el.parentNode.tagName === "SPAN") el = el.parentNode;
    var n = el.nextSibling;
    if (!n || n.nodeType !== 3) return;
    var m = /^[,.;:!?)\]]+/.exec(n.nodeValue);
    if (!m) return;
    var wrap = document.createElement("span");
    wrap.className = "nobr";
    el.parentNode.insertBefore(wrap, el);
    wrap.appendChild(el);
    wrap.appendChild(document.createTextNode(m[0]));
    n.nodeValue = n.nodeValue.slice(m[0].length);
  });
});

// 2. Display formulas wider than the page scroll sideways. Mark which edge continues so the
//    stylesheet can fade it; a hard cut looks like a truncated formula.
//    An inline formula never wraps, so one wider than its line is made to scroll the same way.
//    Otherwise it runs off the page, and in Hebrew text off the left edge, where nothing can scroll to it.
document.addEventListener("DOMContentLoaded", function () {
  var displays = document.querySelectorAll(".katex-display");
  var inlines = Array.prototype.filter.call(document.querySelectorAll(".katex"), function (k) {
    return !k.closest(".katex-display");
  });
  if (!displays.length && !inlines.length) return;
  function update(el) {
    var max = el.scrollWidth - el.clientWidth;
    el.classList.toggle("fade-start", el.scrollLeft > 1);
    el.classList.toggle("fade-end", el.scrollLeft < max - 1);
  }
  // An inline formula is too wide when it is wider than the block of text it sits in. A few pixels
  // over still fit in the page margin.
  function room(k) {
    var p = k.parentNode;
    while (/^(inline|contents)$/.test(getComputedStyle(p).display)) p = p.parentNode;
    var cs = getComputedStyle(p);
    return p.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  }
  function markWide() {
    var at = inlines.map(function (k) { return k.scrollLeft; });
    inlines.forEach(function (k) { k.classList.remove("wide", "fade-start", "fade-end"); });
    var wide = inlines.map(function (k) { return k.getBoundingClientRect().width > room(k) + 4; });
    inlines.forEach(function (k, i) { if (wide[i]) { k.classList.add("wide"); k.scrollLeft = at[i]; } });
  }
  function updateAll() {
    displays.forEach(update);
    inlines.forEach(function (k) { if (k.classList.contains("wide")) update(k); });
  }
  function relayout() { markWide(); updateAll(); }
  displays.forEach(function (el) {
    el.addEventListener("scroll", function () { update(el); }, { passive: true });
  });
  document.addEventListener("scroll", function (ev) {
    if (ev.target.classList && ev.target.classList.contains("wide")) update(ev.target);
  }, { passive: true, capture: true });
  relayout();
  var width = window.innerWidth;
  window.addEventListener("resize", function () {
    if (window.innerWidth === width) { updateAll(); return; }
    width = window.innerWidth;
    relayout();
  });
  if (document.fonts) document.fonts.ready.then(relayout);
  // A formula inside a closed folder has no size yet; measure again when one opens.
  var queued = false;
  document.addEventListener("toggle", function () {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; relayout(); });
  }, true);
});

// 3. Subscribe form: always submitted in place with fetch. The form has no action attribute (the
//    endpoint is in data-action) and the CSP sets form-action 'none', so the page can never navigate
//    to MailerLite. The button ships disabled and is enabled here, so without JavaScript nothing is sent.
document.querySelectorAll("form.subscribe-form").forEach(function (form) {
  if (!window.fetch || !window.FormData) return;
  var btn = form.querySelector("button[type=submit]");
  btn.disabled = false;
  var input = form.querySelector("input[type=email]");
  var fields = form.querySelector(".subscribe-fields");
  var msg = form.querySelector(".subscribe-msg");
  var idle = btn.textContent;
  function show(text, kind) { msg.textContent = text; msg.className = "subscribe-msg " + kind; msg.hidden = false; }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = form.dataset.busy || idle;
    msg.hidden = true;
    fetch(form.dataset.action, { method: "POST", body: new FormData(form), headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.success) {
          var f = d && d.errors && d.errors.fields && d.errors.fields.email;
          throw { field: f && f[0] };
        }
        fields.hidden = true;
        show(form.dataset.success, "is-success");
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = idle;
        show((e && e.field) || form.dataset.error, "is-error");
        input.focus();
      });
  });
});

// 4. Summary pages: "expand all / collapse all" for the lecture list.
document.querySelectorAll("nav.parts").forEach(function (nav) {
  var btn = nav.querySelector(".parts-toggle");
  var items = Array.prototype.slice.call(nav.querySelectorAll("details.part"));
  if (!btn || !items.length) return;
  function allOpen() { return items.every(function (d) { return d.open; }); }
  function refresh() { btn.textContent = allOpen() ? btn.dataset.collapse : btn.dataset.expand; }
  btn.addEventListener("click", function () {
    var open = !allOpen();
    items.forEach(function (d) { d.open = open; });
    refresh();
  });
  items.forEach(function (d) { d.addEventListener("toggle", refresh); });
  btn.hidden = false;
  refresh();
});
