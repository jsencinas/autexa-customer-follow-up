// Small progressive enhancements: upload progress and live updates without reloading the page.
(function () {
  "use strict";

  // Replace the page's <main> with the <main> of another HTML document, then re-initialise it.
  function swapMain(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var next = doc.querySelector("main");
    if (!next) return false;
    document.querySelector("main").replaceWith(next);
    if (doc.title) document.title = doc.title;
    init(next);
    return true;
  }

  // --- Upload forms with a progress bar ---------------------------------------------------
  function initUpload(form) {
    var input = form.querySelector('input[type="file"]');
    var box = form.querySelector(".upload-progress");
    var bar = box && box.querySelector("progress");
    var label = box && box.querySelector(".upload-label");
    var button = form.querySelector('button[type="submit"]');
    var maxBytes = parseInt(form.dataset.maxBytes || "0", 10);
    var error = form.querySelector(".upload-error");

    form.addEventListener("submit", function (event) {
      if (!window.FormData || !input.files.length) return;  // let the browser handle it
      event.preventDefault();
      error.textContent = "";
      if (maxBytes && input.files[0].size > maxBytes) {
        error.textContent = "La foto supera el tamaño máximo (" + Math.round(maxBytes / 1048576) + " MB).";
        return;
      }
      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.action);
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var pct = Math.round((e.loaded / e.total) * 100);
        bar.value = pct;
        label.textContent = pct < 100 ? "Subiendo foto… " + pct + " %" : "Foto subida, preparando…";
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          // The server redirects to the review page; show it without a full reload.
          if (xhr.responseURL && xhr.responseURL !== location.href) history.pushState({}, "", xhr.responseURL);
          swapMain(xhr.responseText);
          return;
        }
        reset();
        if (!swapMain(xhr.responseText)) error.textContent = "No se pudo subir la foto (error " + xhr.status + ").";
      };
      xhr.onerror = function () {
        reset();
        error.textContent = "Sin conexión. Comprueba la red e inténtalo de nuevo.";
      };
      box.hidden = false;
      bar.value = 0;
      label.textContent = "Subiendo foto… 0 %";
      button.disabled = true;
      xhr.send(new FormData(form));
    });

    function reset() {
      box.hidden = true;
      button.disabled = false;
    }
  }

  // --- Review page: wait for extraction without reloading ----------------------------------
  var pollTimer = null;

  function initLiveStatus(el) {
    if (pollTimer) clearTimeout(pollTimer);
    var url = el.dataset.statusUrl;
    var signature = el.dataset.signature;
    function poll() {
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (s) {
          if (s.signature !== signature) {
            // Something changed (finished, retrying, worker started/stopped): refresh just the content.
            return fetch(location.href, { credentials: "same-origin" })
              .then(function (r) { return r.text(); })
              .then(swapMain);
          }
          pollTimer = setTimeout(poll, 2000);
        })
        .catch(function () { pollTimer = setTimeout(poll, 5000); });
    }
    pollTimer = setTimeout(poll, 2000);
  }

  // Confirm stays disabled until the required fields look valid; the server validates again.
  function initReviewForm(form) {
    var btn = form.querySelector("#confirm-btn");
    var name = form.querySelector("#customer_name");
    var phone = form.querySelector("#customer_phone");
    if (!btn || !name || !phone) return;
    function check() {
      var digits = (phone.value.match(/\d/g) || []).length;
      btn.disabled = !(name.value.trim() && digits >= 9);
    }
    name.addEventListener("input", check);
    phone.addEventListener("input", check);
    check();
  }

  function init(root) {
    root.querySelectorAll("form[data-upload]").forEach(initUpload);
    root.querySelectorAll("form#review").forEach(initReviewForm);
    var live = root.querySelector("[data-status-url]");
    if (live) initLiveStatus(live);
    else if (pollTimer) clearTimeout(pollTimer);
  }

  window.addEventListener("popstate", function () { location.reload(); });
  document.addEventListener("DOMContentLoaded", function () { init(document); });
})();
