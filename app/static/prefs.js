// Small helpers used on every page: remembered text size and light/dark colors, "show password", confirmation prompts.
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

  // Light or dark: with nothing saved ("Auto") the page follows the device (the stylesheet does that by
  // itself); once the person picks Light or Dark, it is saved and wins until they pick Auto again.
  var THEME_KEY = "sv-theme";
  var THEMES = ["light", "dark"];
  var root = document.documentElement;
  var chosenNow = null;  // the pick made on this page, for browsers that won't let us save it

  function savedTheme() {
    try {
      var saved = localStorage.getItem(THEME_KEY);
      if (THEMES.indexOf(saved) >= 0) return saved;
    } catch (e) { /* fall through */ }
    return chosenNow;
  }

  function applyTheme() {
    var saved = savedTheme();
    if (saved) root.setAttribute("data-theme", saved);
    else root.removeAttribute("data-theme");
    var choice = saved || "auto";
    var buttons = document.querySelectorAll("[data-set-theme]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-set-theme") === choice ? "true" : "false");
    }
  }

  // Set straight away (this script runs in <head>) so the page never flashes at the wrong size or colors.
  apply(load());
  applyTheme();

  document.addEventListener("DOMContentLoaded", function () {
    apply(load());
    applyTheme();

    document.addEventListener("click", function (event) {
      var target = event.target.closest ? event.target : event.target.parentNode;
      var button = target && target.closest ? target.closest(".size-btn") : null;
      if (button) {
        var size = button.getAttribute("data-size");
        try { localStorage.setItem(KEY, size); } catch (e) { /* private mode: just don't remember */ }
        apply(size);
        return;
      }
      button = target && target.closest ? target.closest("[data-set-theme]") : null;
      if (button) {
        var pick = button.getAttribute("data-set-theme");
        chosenNow = THEMES.indexOf(pick) >= 0 ? pick : null;  // anything else is "Auto"
        try {
          if (chosenNow) localStorage.setItem(THEME_KEY, chosenNow);
          else localStorage.removeItem(THEME_KEY);
        } catch (e) { /* private mode: just don't remember */ }
        applyTheme();
      }
    });

    // Follow the device while nothing has been chosen, and follow a choice made in another tab.
    var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    if (query && query.addEventListener) query.addEventListener("change", applyTheme);
    window.addEventListener("storage", function (event) {
      if (event.key === THEME_KEY) applyTheme();
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
