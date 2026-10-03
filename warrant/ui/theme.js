/* WARRANT — theme control.
   ----------------------------------------------------------------------------
   Three states, stated rather than implied: Auto follows the system, Light and
   Dark override it. The choice lives in localStorage under "warrant.theme".

   The attribute, not this file, is the source of truth:

     html[data-theme="light"|"dark"]   an explicit choice
     no data-theme                     Auto; styles.css resolves it through
                                       prefers-color-scheme

   That split is what lets the inline script in <head> apply a stored choice
   before first paint without having to know anything about media queries, and
   it means Auto keeps tracking the system with no JavaScript involved.

   Everything that needs to know the *resolved* theme (vitals.js, boot.js)
   reads the --gl-ambient token off the root element rather than asking this
   module, so there is one source of truth and a theme change is one token.
   This module just tells them when to look again, via a "warrant:theme"
   event on window.

   Display only. Nothing here reads state/ or decides anything. */
(function () {
  "use strict";

  var KEY = "warrant.theme";
  var CHOICES = { auto: 1, light: 1, dark: 1 };

  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  /* localStorage throws outright in a private window, and getItem can return
     anything a previous version wrote, so both sides are guarded and fall back
     to Auto. */
  function readChoice() {
    try {
      var stored = localStorage.getItem(KEY);
      return CHOICES[stored] ? stored : "auto";
    } catch (err) {
      return "auto";
    }
  }

  function writeChoice(choice) {
    try {
      if (choice === "auto") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch (err) {
      // A session that cannot persist still gets the theme it asked for.
    }
  }

  function resolved() {
    var attr = root.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    return media && media.matches ? "light" : "dark";
  }

  function announce(choice) {
    var theme = resolved();
    root.setAttribute("data-resolved-theme", theme);
    window.dispatchEvent(
      new CustomEvent("warrant:theme", { detail: { theme: theme, choice: choice } })
    );
  }

  function apply(choice, persist) {
    if (!CHOICES[choice]) choice = "auto";
    if (choice === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    if (persist) writeChoice(choice);
    announce(choice);
  }

  function mount() {
    var choice = readChoice();
    var group = document.getElementById("theme-switch");

    // The markup is static, so the control has to be told which state it is in.
    var input = document.getElementById("theme-" + choice);
    if (input) input.checked = true;

    if (group) {
      group.addEventListener("change", function (ev) {
        var target = ev.target;
        if (target && target.name === "warrant-theme") apply(target.value, true);
      });
    }

    // In Auto the stylesheet already switched itself; this only lets the
    // shaders know the ambient level moved under them.
    if (media) {
      var onSystem = function () {
        if (readChoice() === "auto") announce("auto");
      };
      if (media.addEventListener) media.addEventListener("change", onSystem);
      else if (media.addListener) media.addListener(onSystem);
    }

    announce(choice);
  }

  // Exposed so the shaders can read the resolved theme without duplicating the
  // attribute-plus-media-query logic.
  window.WarrantTheme = {
    resolved: resolved,
    choice: readChoice,
    /* Background luminance for the shaders, 0 a dark room and 1 white paper.
       Driven by the --gl-ambient token so the palette owns the number. */
    ambient: function () {
      var raw = getComputedStyle(root).getPropertyValue("--gl-ambient");
      var n = parseFloat(raw);
      return isFinite(n) ? Math.min(1, Math.max(0, n)) : 0.05;
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
