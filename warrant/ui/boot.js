/* WARRANT — boot orb.
   ----------------------------------------------------------------------------
   A full-screen overlay carrying one glass orb, which settles into the vitals
   ribbon's position as it fades. The dashboard underneath is already live the
   whole time: app.js polls on its own DOMContentLoaded handler and this file
   never touches it, so nothing here can delay the first reading.

   THE OPTICS ARE DERIVED, NOT PAINTED
     - Disc normal      N = normalize(vec3(p, sqrt(1 - r*r)))
     - Fresnel          0.04 + 0.96 * pow(1 - dot(N, V), 5.0)
     - Specular rim     a Fresnel-weighted env(reflect(-V, N)) and nothing
                        else. There is no smoothstep rim term layered on top;
                        at grazing angles the Fresnel weight goes to 1 and the
                        edge shows pure environment, which IS the rim.
     - Environment      a direction-to-colour function: one soft key lobe, a
                        vertical gradient and a floor bounce. No cubemap, no
                        texture upload, so it costs a handful of pow() and no
                        GPU memory.
     - Dispersion       12 wavelengths, each with its own index of refraction,
                        weighted by a cheap eye response, so the spectrum
                        emerges in the right order and widens where the glass
                        is thick. Not a hand-authored ramp.
     - Rotation         the sampled directions rotate, not a texture.

   WHY TWELVE TAPS ARE AFFORDABLE
   Everything that does not vary per wavelength is hoisted out of the loop. The
   transmitted environment is positioned once with a mid wavelength, and only
   the sharp caustic core is sampled per wavelength. Better still, the loop
   never calls refract(): for the fixed view direction the dot product of the
   refracted ray with a constant direction has a closed form (see
   REFRACTED DOT below), so a tap costs one sqrt and a couple of multiplies.

   BEHAVIOUR
     - Runs HOLD_MS then transitions out. Total stays under 3.1s, and a hard
       cap forces the handover even if a transition event never arrives.
     - Skippable: pointer down, any key, Escape.
     - prefers-reduced-motion: the inline <head> script sets data-boot="still",
       which draws exactly one frame and fades on opacity alone. Never animates.
     - ?boot=0 skips the sequence entirely. The inline <head> script simply
       never sets data-boot, so no overlay is ever painted and this file hands
       straight over.
     - failIfMajorPerformanceCaveat: true. A refused or lost context skips the
       sequence immediately rather than showing a broken half-state.
     - The context is released with WEBGL_lose_context BEFORE the vitals ribbon
       mounts, so the two never contend. vitals.js waits on
       WarrantBoot.whenReleased().
     - The canvas and the whole overlay are aria-hidden, hold nothing
       focusable, and are removed from layout on handover.

   Display only. Nothing here reads state/ or decides anything. */
(function () {
  "use strict";

  var HOLD_MS = 2400; // orb on screen before the exit begins
  var EXIT_MS = 640; // must match the .boot-stage transition in styles.css
  var STILL_MS = 760; // reduced motion: one frame, held briefly
  var SKIP_EXIT_MS = 220; // must match .boot-skip in styles.css
  var HARD_CAP_MS = 3400; // nothing holds the dashboard past this
  var FPS_CAP = 60;
  var FRAME_MS = 1000 / FPS_CAP;
  var DPR_CAP = 1.75; // the loop is moderate, so render near real density
  var REVEAL_MS = 900;
  var ROT_SPEED = 0.4; // rad/s: a full turn takes about sixteen seconds

  /* ---------- handover ---------- */

  var released = false;
  var waiting = [];

  function release() {
    if (released) return;
    released = true;
    var queued = waiting;
    waiting = [];
    for (var i = 0; i < queued.length; i++) {
      try {
        queued[i]();
      } catch (err) {
        console.warn("[warrant/boot] handover listener failed: " + err.message);
      }
    }
  }

  // Published synchronously, before vitals.js runs, so it can always find it.
  window.WarrantBoot = {
    whenReleased: function (cb) {
      if (typeof cb !== "function") return;
      if (released) cb();
      else waiting.push(cb);
    },
    released: function () {
      return released;
    },
  };

  var root = document.documentElement;
  var mode = root.getAttribute("data-boot");

  // ?boot=0, or an older browser that never ran the inline script: nothing to
  // do, and the ribbon can have the GPU straight away.
  if (mode !== "on" && mode !== "still") {
    release();
    return;
  }

  /* ---------- shaders ---------- */

  var VERT = [
    "attribute vec2 aPos;",
    "void main() { gl_Position = vec4(aPos, 0.0, 1.0); }",
  ].join("\n");

  var FRAG = [
    "precision highp float;",
    "uniform vec2 uRes;",
    "uniform float uTime;",
    "uniform float uReveal;",
    // How bright the page behind the canvas is: 0 a dark room, 1 white paper.
    // Driven from the --gl-ambient token. Without it a dark studio reflected
    // onto a light page draws a hard black ring at the rim.
    "uniform float uAmbient;",
    "",
    "const float ORB_FILL = 0.74;",
    "const float IOR = 1.46;",
    /* Real glass separates far too little to see at this scale, so the index
       spread is the one number pushed well past reality. It is the single
       exaggeration here and every other constant follows from it. */
    "const float SPREAD = 0.09;",
    /* A ray crosses two surfaces on its way through a sphere, and the exit
       bends it again by about as much as the entry did. One stronger
       effective index models both for the price of one refract, and it is
       what lets the interior sweep enough of the environment to carry the
       blue-to-green gradient. A single-surface bend only ever reaches the
       dark middle of the sky, which renders as a flat dark ball. */
    "const float IOR_EFF = 2.0 * IOR - 1.0;",
    "const int TAPS = 12;",
    "",
    // Dell blue through NVIDIA green. No purple, no magenta.
    "const vec3 C_DEEP = vec3(0.004, 0.030, 0.068);",
    "const vec3 C_BLUE = vec3(0.000, 0.345, 0.690);",
    "const vec3 C_GREEN = vec3(0.300, 0.608, 0.000);",
    "const vec3 C_KEY = vec3(0.780, 0.910, 1.000);",
    "",
    "// Cheap spectral response: three lobes standing in for the eye's curves.",
    "vec3 spectral(float w) {",
    "  float r = exp(-22.0 * (w - 0.86) * (w - 0.86))",
    "          + exp(-18.0 * (w - 0.08) * (w - 0.08)) * 0.42;",
    "  float g = exp(-16.0 * (w - 0.52) * (w - 0.52));",
    "  float b = exp(-18.0 * (w - 0.16) * (w - 0.16));",
    "  return vec3(r, g, b);",
    "}",
    "",
    "mat3 spin(float a, float tilt) {",
    "  float c = cos(a), s = sin(a);",
    "  float ct = cos(tilt), st = sin(tilt);",
    "  mat3 ry = mat3(c, 0.0, -s, 0.0, 1.0, 0.0, s, 0.0, c);",
    "  mat3 rx = mat3(1.0, 0.0, 0.0, 0.0, ct, st, 0.0, -st, ct);",
    "  return ry * rx;",
    "}",
    "",
    "/* The environment as a direction-to-colour function. One soft key lobe",
    "   high and to the left, a vertical gradient, and a floor bounce coming",
    "   back up in NVIDIA green. uAmbient lifts the whole room toward the",
    "   page's own brightness rather than adding on top of it, so the hues",
    "   survive on paper instead of washing to white. */",
    "vec3 env(vec3 d) {",
    "  float up = d.y * 0.5 + 0.5;",
    "  vec3 c = mix(C_DEEP, C_BLUE, pow(up, 1.2));",
    "  float key = max(dot(d, normalize(vec3(-0.48, 0.70, 0.53))), 0.0);",
    "  c += C_KEY * pow(key, 26.0) * 1.6;",
    "  c += C_KEY * pow(key, 3.0) * 0.11;",
    "  c += C_GREEN * pow(max(-d.y, 0.0), 1.7) * 0.92;",
    "  vec3 room = vec3(0.70, 0.72, 0.74) * uAmbient;",
    "  return c * mix(1.0, 0.62, uAmbient) + room * (0.42 + 0.58 * up);",
    "}",
    "",
    "void main() {",
    "  float m = min(uRes.x, uRes.y);",
    "  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / (0.5 * m * ORB_FILL);",
    "  // Free-floating: a very slow drift, not a bob.",
    "  p.y -= sin(uTime * 0.55) * 0.016;",
    "  p.x -= sin(uTime * 0.37 + 1.1) * 0.010;",
    "",
    "  float px = 2.0 / (m * ORB_FILL); // one device pixel, in p units",
    "  float r = length(p);",
    "",
    "  // Clamp onto the disc so the optics stay valid outside it too: the",
    "  // surround then reuses the exact rim sample for its scattered halo.",
    "  vec2 q = p / max(r, 1.0);",
    "  float rc = min(r, 1.0);",
    "  vec3 N = normalize(vec3(q, sqrt(max(1.0 - rc * rc, 1e-6))));",
    "  vec3 V = vec3(0.0, 0.0, 1.0);",
    "",
    "  float fres = 0.04 + 0.96 * pow(1.0 - max(dot(N, V), 0.0), 5.0);",
    "",
    "  mat3 rot = spin(uTime * " + ROT_SPEED.toFixed(3) + ", 0.22);",
    "",
    "  // A Fresnel-weighted reflection of the environment. This is the whole",
    "  // specular rim; there is no separate rim term.",
    "  vec3 spec = env(rot * reflect(-V, N));",
    "",
    "  /* Hoisted out of the wavelength loop: the transmitted environment is",
    "     positioned once with a mid wavelength, because position and",
    "     separation want separate controls. Only the sharp caustic core is",
    "     worth sampling per wavelength. */",
    "  vec3 through = env(rot * refract(-V, N, 1.0 / IOR_EFF));",
    "",
    "  /* REFRACTED DOT. rot is orthonormal, so rotating the core direction",
    "     back into surface space replaces twelve mat3 multiplies with one.",
    "     Then for I = -V = (0,0,-1):",
    "        T = (0,0,-eta) - (sqrt(k) - eta*N.z) * N,  k = 1 - eta^2(1-N.z^2)",
    "     so dot(T, c) is closed form and a tap costs one sqrt. That is what",
    "     makes twelve taps cost about what one did. */",
    "  vec3 core = normalize(vec3(0.24, -0.18, -0.94));",
    "  vec3 cl = core * rot; // transpose(rot) * core, for an orthonormal rot",
    "  float ndc = dot(N, cl);",
    "  float s2 = 1.0 - N.z * N.z;",
    "",
    "  // The spread widens toward the rim, where the ray bends hardest, which",
    "  // is what a hand-authored ramp cannot do on its own.",
    "  float spread = SPREAD * (0.35 + 1.5 * rc * rc) * uReveal;",
    "",
    "  vec3 acc = vec3(0.0);",
    "  vec3 wsum = vec3(0.0);",
    "  for (int i = 0; i < TAPS; i++) {",
    "    float w = float(i) / float(TAPS - 1);",
    "    float eta = 1.0 / (IOR_EFF + (w - 0.5) * spread);",
    "    float k = sqrt(max(1.0 - eta * eta * s2, 0.0));",
    "    float d = -eta * cl.z - (k - eta * N.z) * ndc;",
    "    d = max(d, 0.0);",
    "    float caustic = pow(d, 24.0) + pow(d, 5.0) * 0.17;",
    "    vec3 s = spectral(w);",
    "    acc += caustic * s;",
    "    wsum += s;",
    "  }",
    "  // Normalised by the same weights, so a white core stays white in the",
    "  // middle and only its edges fringe.",
    "  vec3 interior = through * 0.98 + C_KEY * (acc / max(wsum, vec3(1e-4))) * 3.1;",
    "",
    "  vec3 col = interior * (1.0 - fres) + spec * fres;",
    "",
    "  float aa = px * 1.3;",
    "  float disc = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, r);",
    "  // Scattering in the air around the orb, carrying the rim's own colour",
    "  // because it is the same env sample.",
    "  float glow = exp(-(r - 1.0) * 7.0) * (1.0 - disc) * 0.6;",
    "",
    "  vec3 rgb = col * (disc + glow);",
    "  // Hue-preserving ceiling: no input can blow the orb out to white.",
    "  rgb /= max(1.0, max(max(rgb.r, rgb.g), rgb.b));",
    "",
    "  float cover = clamp(disc + glow * 0.35, 0.0, 1.0);",
    "  // Premultiplied: inside the disc the orb is opaque glass, and outside",
    "  // it only adds its scattered light to whatever the page is.",
    "  gl_FragColor = vec4(rgb * uReveal, cover * uReveal);",
    "}",
  ].join("\n");

  /* ---------- state ---------- */

  var overlay = document.getElementById("boot");
  var stage = document.getElementById("boot-stage");
  var canvas = document.getElementById("boot-canvas");
  var gl = null;
  var loc = null;
  var rafId = 0;
  var lastDraw = 0;
  var startedAt = 0;
  var finished = false;
  var exiting = false;
  var still = mode === "still";
  var timers = [];

  function later(fn, ms) {
    timers.push(setTimeout(fn, ms));
  }

  function clearTimers() {
    for (var i = 0; i < timers.length; i++) clearTimeout(timers[i]);
    timers = [];
  }

  function ambient() {
    if (window.WarrantTheme && window.WarrantTheme.ambient) {
      return window.WarrantTheme.ambient();
    }
    var raw = getComputedStyle(root).getPropertyValue("--gl-ambient");
    var n = parseFloat(raw);
    return isFinite(n) ? Math.min(1, Math.max(0, n)) : 0.05;
  }

  /* ---------- GL ---------- */

  function compile(type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.warn("[warrant/boot] shader compile failed: " + gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  function initGL() {
    if (!canvas || !window.WebGLRenderingContext) return false;

    var opts = {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
      // Mandatory: on a blocklisted GPU or with hardware acceleration off, a
      // full-screen pass crawls, and skipping the sequence is strictly better
      // than a frozen tab in front of the dashboard.
      failIfMajorPerformanceCaveat: true,
    };

    gl =
      canvas.getContext("webgl", opts) ||
      canvas.getContext("experimental-webgl", opts);
    if (!gl) return false;

    var vs = compile(gl.VERTEX_SHADER, VERT);
    var fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return false;

    var program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn("[warrant/boot] link failed: " + gl.getProgramInfoLog(program));
      return false;
    }

    gl.useProgram(program);

    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW
    );
    var aPos = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    loc = {
      res: gl.getUniformLocation(program, "uRes"),
      time: gl.getUniformLocation(program, "uTime"),
      reveal: gl.getUniformLocation(program, "uReveal"),
      ambient: gl.getUniformLocation(program, "uAmbient"),
    };

    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); // source is premultiplied
    gl.enable(gl.BLEND);
    gl.clearColor(0, 0, 0, 0);

    canvas.addEventListener("webglcontextlost", onContextLost, false);
    return true;
  }

  // Routine under GPU resets and tab pressure. There is no half-state to show,
  // so hand the dashboard over at once.
  function onContextLost(ev) {
    ev.preventDefault();
    gl = null;
    console.warn("[warrant/boot] context lost - skipping the boot sequence");
    finish();
  }

  function resize() {
    if (!gl || !canvas) return;
    var dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    var w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    var h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }

  function draw(t) {
    if (!gl) return;
    resize();
    var reveal = still ? 1 : Math.min(1, t / (REVEAL_MS / 1000));
    gl.uniform2f(loc.res, canvas.width, canvas.height);
    gl.uniform1f(loc.time, still ? 0 : t);
    // Eased so the orb arrives rather than ramping linearly.
    gl.uniform1f(loc.reveal, 1 - Math.pow(1 - reveal, 3));
    gl.uniform1f(loc.ambient, ambient());
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  function step(now) {
    rafId = 0;
    if (finished || !gl) return;
    if (now - lastDraw >= FRAME_MS) {
      lastDraw = now;
      draw((now - startedAt) / 1000);
    }
    rafId = requestAnimationFrame(step);
  }

  /* ---------- exit ---------- */

  /* Aim the stage at the vitals ribbon so the orb settles into the slot the
     ribbon is about to occupy. One continuous motion into the dashboard
     instead of a cut. */
  function aimAtRibbon() {
    if (!stage || !overlay) return;
    var ribbon = document.getElementById("vitals") || document.getElementById("panel-status");
    if (!ribbon) return;
    var s = stage.getBoundingClientRect();
    var b = ribbon.getBoundingClientRect();
    if (!s.width || !s.height || !b.width || !b.height) return;
    overlay.style.setProperty(
      "--boot-dx",
      (b.left + b.width / 2 - (s.left + s.width / 2)).toFixed(1) + "px"
    );
    overlay.style.setProperty(
      "--boot-dy",
      (b.top + b.height / 2 - (s.top + s.height / 2)).toFixed(1) + "px"
    );
    overlay.style.setProperty(
      "--boot-scale",
      Math.max(0.08, Math.min(1, b.height / s.height)).toFixed(3)
    );
  }

  function beginExit(fast) {
    if (exiting || finished) return;
    exiting = true;
    if (overlay) {
      aimAtRibbon();
      if (fast) overlay.classList.add("boot-skip");
      overlay.classList.add("boot-out");
    }
    // Keep drawing through the travel: the orb is live as it settles.
    later(finish, fast ? SKIP_EXIT_MS + 60 : EXIT_MS + 60);
  }

  function finish() {
    if (finished) return;
    finished = true;
    clearTimers();

    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }

    document.removeEventListener("keydown", onKey, true);
    if (overlay) overlay.removeEventListener("pointerdown", onPointer);

    // Out of layout and out of the paint path before the ribbon mounts.
    root.removeAttribute("data-boot");

    /* Release the context explicitly rather than waiting for the canvas to be
       collected, so the ribbon's context is the only live one. */
    if (gl) {
      var ext = gl.getExtension("WEBGL_lose_context");
      if (ext) ext.loseContext();
      gl = null;
    }

    release();
  }

  function onKey() {
    beginExit(true);
  }

  function onPointer() {
    beginExit(true);
  }

  /* ---------- run ---------- */

  if (!overlay || !stage || !canvas || !initGL()) {
    gl = null;
    console.log("[warrant/boot] no usable WebGL context - skipping the boot sequence");
    finish();
    return;
  }

  // Any key, Escape included. Captured so nothing downstream can swallow it.
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("pointerdown", onPointer);

  startedAt = performance.now();
  lastDraw = startedAt - FRAME_MS;
  draw(0);

  if (still) {
    // Reduced motion: one frame, held, then an opacity-only fade. No loop.
    later(function () {
      beginExit(true);
    }, STILL_MS);
  } else {
    rafId = requestAnimationFrame(step);
    later(function () {
      beginExit(false);
    }, HOLD_MS);
  }

  // Nothing holds the dashboard past this, whatever a transition does.
  later(finish, HARD_CAP_MS);
})();
