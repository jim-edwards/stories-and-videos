// Small helpers used on every page: remembered text size, "show password", confirmation prompts.
(function () {
  var KEY = "sv-text-size";
  var SIZES = ["normal", "large", "xlarge"];

  function load() {
    try {
      var saved = localStorage.getItem(KEY);
      return SIZES.indexOf(saved) >= 0 ? saved : "normal";
    } catch (e) {
      return "normal";
    }
  }

  function apply(size) {
    document.documentElement.setAttribute("data-text", size);
    var buttons = document.querySelectorAll(".size-btn");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-size") === size ? "true" : "false");
    }
  }

  // Set straight away (this script runs in <head>) so the page never flashes at the wrong size.
  apply(load());

  document.addEventListener("DOMContentLoaded", function () {
    apply(load());

    document.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest(".size-btn") : null;
      if (!button) return;
      var size = button.getAttribute("data-size");
      try { localStorage.setItem(KEY, size); } catch (e) { /* private mode: just don't remember */ }
      apply(size);
    });

    var toggles = document.querySelectorAll("[data-show-password]");
    Array.prototype.forEach.call(toggles, function (toggle) {
      toggle.addEventListener("change", function () {
        var field = document.getElementById(toggle.getAttribute("data-show-password"));
        if (field) field.type = toggle.checked ? "text" : "password";
      });
    });

    document.addEventListener("submit", function (event) {
      var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
      if (message && !window.confirm(message)) event.preventDefault();
    });
  });
})();
