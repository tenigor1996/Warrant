/* WARRANT — vitals ribbon.
   ----------------------------------------------------------------------------
   A shader read of service health that sits in the Service Status panel. It
   looks ambient, but every input is real: filament amplitude comes from the
   failure rate, turbulence from error volume, drift speed from p95 latency,
   and hue from the service status. It is the peripheral signal you can read
   from across a room while the numbers beside it carry the precision.

   Display only. app.js hands it health via WarrantVitals.setHealth(); nothing
   here reads state/, decides anything, or writes back.

   Discipline (per the webgl-components skill):
     - one module-level context and one rAF loop, never per instance
     - IntersectionObserver gating + pause when the tab is hidden
     - 30fps cap: slow drift gains nothing above it and an uncapped loop pins
       a core, twice over on a 120Hz display
     - failIfMajorPerformanceCaveat, so a blocklisted GPU or a machine with
       hardware acceleration switched off takes the DOM fallback instead of
       freezing on a software rasterizer
     - prefers-reduced-motion paints one still frame and never starts the loop
     - context loss flips to the fallback rather than leaving a dead canvas
     - deterministic: no Math.random in the render path, so the ribbon looks
       the same in every run of the demo
     - theme-aware: every environment constant secretly assumes a background,
       so the page's own brightness arrives as a uniform (uAmbient, read from
       the --gl-ambient token) and the ribbon repaints when the theme changes.
       On a dark page the filament emits light; on paper the same filament
       reads as ink, because an emissive trace over an off-white surface is
       invisible and a dark-studio rim would draw a hard edge.
     - it takes the GPU from the boot orb rather than sharing it: mounting
       waits on WarrantBoot.whenReleased(), so only one context is ever live.
   Force the fallback branch on a healthy GPU with ?vitals=fallback. */
(function () {
  "use strict";

  var FPS_CAP = 30;
  var FRAME_MS = 1000 / FPS_CAP;
  var DPR_CAP = 2; // the fragment shader is cheap, so render near real density

  /* Time constant for colour and amplitude changes, in milliseconds: a status
     change glides rather than snapping. Deliberately time-based rather than a
     per-frame rate, so a slow renderer takes exactly as long to settle as a
     fast one instead of visibly dragging. */
  var SETTLE_TAU = 380;

  /* Dispersion is the one number pushed well past reality. Real glass
     separates far too little to see at this scale, so this is the exaggeration
     and every other constant follows from it. */
  var SPECTRAL_SPREAD = 0.05;
  var SPECTRAL_TAPS = 10;
  /* How much raw spectrum survives the status tint. Kept low on purpose: the
     ribbon has to read as the status colour from across the room, so the
     fringing is a texture at the filament edges rather than a second signal
     competing with it. Above ~0.25 a degraded service reads white-orange and
     stops agreeing with the pill beside it. */
  var FRINGE = 0.15;

  /* ---------- status to colour ---------- */

  // Emission colours, not surface colours: these are what the filament gives
  // off, so they sit brighter than the CSS tokens of the same name.
  var TINTS = {
    healthy: [0.1, 0.52, 0.9], // Dell blue
    recovering: [1.0, 0.56, 0.0], // amber
    degraded: [1.0, 0.14, 0.1], // red
    recovered: [0.46, 0.73, 0.0], // NVIDIA green
    unknown: [0.26, 0.3, 0.36], // slate: no signal should recede, not glow
  };

  /* The non-WebGL fallback carries the same state the shader would show, so
     both branches read as the same entity: the colour is the signal and the
     shader is only how it is drawn. The colours themselves live in styles.css
     keyed on this attribute, so the fallback follows the theme for free. */

  /* ---------- shaders ---------- */

  var VERT = [
    "attribute vec2 aPos;",
    "varying vec2 vUv;",
    "void main() {",
    "  vUv = aPos * 0.5 + 0.5;",
    "  gl_Position = vec4(aPos, 0.0, 1.0);",
    "}",
  ].join("\n");

  /* Three filaments whose centre lines are analytic sums of sines. The centre
     lines depend only on x, so they are computed once and hoisted out of the
     wavelength loop — that is what makes ten taps cost about what one did.
     Each wavelength then needs one distance and one exp. */
  var FRAG = [
    "precision highp float;",
    "varying vec2 vUv;",
    "uniform vec2 uRes;",
    "uniform float uTime;",
    "uniform vec3 uTint;",
    "uniform float uSeverity;", // 0 calm .. 1 violent
    "uniform float uTurb;", // extra high-frequency content
    "uniform float uSpeed;", // horizontal drift rate
    // How bright the page behind the canvas is: 0 a dark room, 1 white paper.
    "uniform float uAmbient;",
    "",
    "const float SPREAD = " + SPECTRAL_SPREAD.toFixed(4) + ";",
    "const int TAPS = " + SPECTRAL_TAPS + ";",
    "const float FRINGE = " + FRINGE.toFixed(3) + ";",
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
    "// One filament's centre offset at this x. Independent of wavelength.",
    "float centre(float x, float phase, float scale) {",
    "  float t = uTime * uSpeed;",
    "  float a = sin(x * 2.7 * scale + t * 0.9 + phase);",
    "  float b = sin(x * 6.1 * scale - t * 1.37 + phase * 2.1);",
    "  float c = sin(x * 13.3 * scale + t * 2.05 + phase * 3.7);",
    "  // Calm service: a nearly flat trace. Degraded: the trace tears up.",
    "  float amp = 0.035 + uSeverity * 0.3;",
    "  return (a * 0.6 + b * 0.3 * (0.35 + uTurb) + c * 0.14 * uTurb) * amp;",
    "}",
    "",
    "void main() {",
    "  vec2 uv = vUv;",
    "  float aspect = uRes.x / max(uRes.y, 1.0);",
    "  float x = uv.x * aspect;",
    "  float y = uv.y - 0.5;",
    "",
    "  // Hoisted: centre lines and per-filament width/weight do not vary by",
    "  // wavelength, so they are computed once for all ten taps.",
    "  float c0 = centre(x, 0.0, 1.0);",
    "  float c1 = centre(x, 2.399, 0.74);",
    "  float c2 = centre(x, 4.798, 1.31);",
    "  float w0 = 0.014 + uSeverity * 0.01;",
    "  float w1 = 0.021 + uSeverity * 0.014;",
    "  float w2 = 0.009 + uSeverity * 0.007;",
    "",
    "  // Dispersion widens with amplitude, the way real glass spreads more",
    "  // where it is thicker.",
    "  float spread = SPREAD * (0.45 + uSeverity * 1.5);",
    "",
    "  vec3 col = vec3(0.0);",
    "  vec3 norm = vec3(0.0);",
    "  for (int i = 0; i < TAPS; i++) {",
    "    float w = float(i) / float(TAPS - 1);",
    "    float off = (w - 0.5) * spread;",
    "    float yy = y + off;",
    "    float d0 = (yy - c0) / w0;",
    "    float d1 = (yy - c1) / w1;",
    "    float d2 = (yy - c2) / w2;",
    "    float inten = exp(-d0 * d0) * 1.0",
    "                + exp(-d1 * d1) * 0.5",
    "                + exp(-d2 * d2) * 0.72;",
    "    vec3 s = spectral(w);",
    "    col += inten * s;",
    "    norm += s;",
    "  }",
    "  col /= max(norm, vec3(0.0001));",
    "",
    "  // Instrument ruling: fixed vertical ticks so the trace reads as a",
    "  // measurement rather than an ambient gradient.",
    "  float ticks = smoothstep(0.985, 1.0, cos(uv.x * aspect * 78.0));",
    "  float base = ticks * 0.05;",
    "",
    "  // A faint baseline the trace departs from.",
    "  base += exp(-abs(y) * 190.0) * 0.1;",
    "",
    "  // The overlapping core sums to white and only the edges fringe, which",
    "  // is the real behaviour; FRINGE decides how much of that survives the",
    "  // brand tint.",
    "  vec3 lit = mix(col * uTint * 1.9, col, FRINGE);",
    "  lit += uTint * base;",
    "",
    "  // Edges fade so the ribbon sits in its frame instead of butting it.",
    "  float edge = smoothstep(0.0, 0.1, uv.x) * smoothstep(1.0, 0.9, uv.x);",
    "  lit *= edge;",
    "",
    "  float cover = clamp(max(max(lit.r, lit.g), lit.b), 0.0, 1.0);",
    "",
    "  /* On a dark page the filament emits and `lit` is already the answer.",
    "     On paper the same shape has to read as ink instead, or an emissive",
    "     trace over an off-white surface simply disappears. The fringe is",
    "     kept by letting the per-wavelength colour tint the ink's hue, so",
    "     the spectrum is still derived rather than dropped. */",
    "  float ink = smoothstep(0.30, 0.62, uAmbient);",
    "  vec3 hue = col / max(max(max(col.r, col.g), col.b), 0.0001);",
    "  vec3 inkCol = uTint * 0.42 * mix(vec3(1.0), hue, FRINGE * 1.6);",
    "",
    "  // Premultiplied either way: emission adds light and blocks only where",
    "  // it is bright; ink covers the surface in the status colour.",
    "  vec3 rgb = mix(lit, inkCol * cover, ink);",
    "  rgb /= max(1.0, max(max(rgb.r, rgb.g), rgb.b));",
    "  gl_FragColor = vec4(rgb, cover);",
    "}",
  ].join("\n");

  /* ---------- module state ---------- */

  var frame = null; // .vitals element
  var canvas = null;
  var fallbackEl = null;
  var gl = null;
  var program = null;
  var loc = null;
  var rafId = 0;
  var lastDraw = 0;
  var startedAt = 0;
  var visible = false;
  var dead = false; // context lost or never created: fallback is permanent
  var reduceMotion = false;
  var painted = false;
  var ambient = 0.05;

  // Targets come from health; current values chase them so a status change
  // glides instead of snapping.
  var target = { tint: TINTS.unknown.slice(), sev: 0.12, turb: 0.2, speed: 0.5 };
  var cur = { tint: TINTS.unknown.slice(), sev: 0.12, turb: 0.2, speed: 0.5 };
  var tintKey = "unknown";

  /* ---------- the page behind the canvas ---------- */

  /* Every environment constant in the shader secretly assumes a background.
     --gl-ambient is the palette's own statement of how bright the page is, so
     reading it keeps one source of truth for the theme. */
  function readAmbient() {
    if (window.WarrantTheme && window.WarrantTheme.ambient) {
      return window.WarrantTheme.ambient();
    }
    var raw = getComputedStyle(document.documentElement).getPropertyValue("--gl-ambient");
    var n = parseFloat(raw);
    return isFinite(n) ? Math.min(1, Math.max(0, n)) : 0.05;
  }

  // A theme change moves the background out from under the shader, so the
  // ribbon has to repaint even when no loop is running.
  function onThemeChange() {
    ambient = readAmbient();
    painted = false;
    if (!dead && gl) draw();
    ensureLoop();
  }

  /* ---------- health mapping ---------- */

  function num(v) {
    if (v === null || v === undefined || v === "" || typeof v === "boolean") {
      return null;
    }
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /* Which emission colour this health reading calls for.

     Live health always wins. The ribbon sits inside the Service Status panel
     and must never contradict the pill beside it: a report claiming RECOVERED
     while health still reads DEGRADED means the service is degraded, and the
     ribbon stays red. final_status only promotes an already-healthy service
     from blue to NVIDIA green, marking a recovery the agent confirmed. */
  function keyFor(status, finalStatus) {
    var s = String(status || "").trim().toLowerCase();
    var confirmed = String(finalStatus || "").trim().toUpperCase() === "RECOVERED";

    if (s === "degraded") return "degraded";
    if (s === "recovering") return "recovering";
    if (s === "healthy") return confirmed ? "recovered" : "healthy";
    // No usable status: the report is the only thing left to go on.
    if (confirmed) return "recovered";
    return "unknown";
  }

  function setHealth(health, finalStatus) {
    var h = health || {};
    var key = keyFor(h.status, finalStatus);
    tintKey = key;
    target.tint = (TINTS[key] || TINTS.unknown).slice();

    // Failure rate drives amplitude. A healthy trace is nearly flat; anything
    // at or past 40% failure is pinned at maximum violence.
    var fail = num(h.failure_rate);
    var sev = fail === null ? null : Math.min(1, Math.max(0, fail / 0.4));

    // Error volume adds high-frequency tearing, on a log scale so the trace
    // keeps responding past a few hundred per minute.
    var errs = num(h.errors_last_minute);
    var turb = errs === null ? null : Math.min(1, Math.log10(1 + Math.max(0, errs)) / 2.4);

    // Latency drives drift speed: a slow service visibly slows the trace down.
    var lat = num(h.latency_p95_ms);
    var speed = lat === null ? null : 0.4 + Math.min(1.5, lat / 900);

    // A status we trust but metrics we do not: keep the colour, and show a
    // floor of motion so DEGRADED never looks calm because a field was null.
    var floor = key === "degraded" ? 0.6 : key === "recovering" ? 0.3 : 0.1;

    target.sev = sev === null ? floor : Math.max(sev, floor);
    target.turb = turb === null ? (key === "degraded" ? 0.7 : 0.2) : turb;
    target.speed = speed === null ? 0.6 : speed;

    paintFallback();
    // A still frame has to be repainted on change, since no loop is running.
    if (reduceMotion && !dead) {
      snapToTarget();
      draw();
    }
    ensureLoop();
  }

  function snapToTarget() {
    cur.tint = target.tint.slice();
    cur.sev = target.sev;
    cur.turb = target.turb;
    cur.speed = target.speed;
  }

  // Keeps the fallback carrying the same state the shader would show. The
  // attribute is the whole contract; styles.css maps it to per-theme colours.
  function paintFallback() {
    if (!frame) return;
    frame.setAttribute("data-tint", TINTS[tintKey] ? tintKey : "unknown");
  }

  /* ---------- GL setup ---------- */

  function compile(type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.warn("[warrant/vitals] shader compile failed: " + gl.getShaderInfoLog(sh));
      gl.deleteShader(sh);
      return null;
    }
    return sh;
  }

  function initGL() {
    /* Refuse software rasterization: on a blocklisted GPU or with hardware
       acceleration off, a heavy pass takes seconds and freezes the tab, and
       the flat fallback is strictly the better widget there.

       ?glforce=1 drops that guard. It exists for one situation: if the demo
       machine's GPU turns out to be blocklisted but its software rasterizer is
       fast enough, we would rather have the ribbon than lose it on stage. It
       is not the default, and it is the switch to reach for only after seeing
       the fallback on the actual box. */
    var relaxed = /[?&]glforce=1\b/.test(window.location.search);

    var opts = {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
      failIfMajorPerformanceCaveat: !relaxed,
    };

    if (relaxed) {
      console.warn("[warrant/vitals] glforce=1 - software rasterization allowed");
    }

    gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts);
    if (!gl) return false;

    var vs = compile(gl.VERTEX_SHADER, VERT);
    var fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return false;

    program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn("[warrant/vitals] link failed: " + gl.getProgramInfoLog(program));
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
      tint: gl.getUniformLocation(program, "uTint"),
      sev: gl.getUniformLocation(program, "uSeverity"),
      turb: gl.getUniformLocation(program, "uTurb"),
      speed: gl.getUniformLocation(program, "uSpeed"),
      ambient: gl.getUniformLocation(program, "uAmbient"),
    };

    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); // source is premultiplied
    gl.enable(gl.BLEND);
    gl.clearColor(0, 0, 0, 0);

    canvas.addEventListener("webglcontextlost", onContextLost, false);
    return true;
  }

  // Routine under GPU resets and tab pressure, not exotic.
  function onContextLost(ev) {
    ev.preventDefault();
    dead = true;
    visible = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    gl = null;
    if (frame) frame.removeAttribute("data-gl");
    console.warn("[warrant/vitals] context lost - showing DOM fallback");
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
      painted = false; // a resized still frame needs one more paint
    }
  }

  /* ---------- draw ---------- */

  function draw() {
    if (!gl || dead) return;
    resize();
    var t = (performance.now() - startedAt) / 1000;
    gl.uniform2f(loc.res, canvas.width, canvas.height);
    gl.uniform1f(loc.time, reduceMotion ? 0 : t);
    gl.uniform3f(loc.tint, cur.tint[0], cur.tint[1], cur.tint[2]);
    gl.uniform1f(loc.sev, cur.sev);
    gl.uniform1f(loc.turb, cur.turb);
    gl.uniform1f(loc.speed, cur.speed);
    gl.uniform1f(loc.ambient, ambient);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    painted = true;
    if (frame) frame.setAttribute("data-gl", "on");
  }

  function approach(a, b, k) {
    return a + (b - a) * k;
  }

  function step(now) {
    rafId = 0;
    if (dead || !visible || reduceMotion) return;

    var dt = now - lastDraw;
    if (dt >= FRAME_MS) {
      // Exponential approach over elapsed time, not frames: a 10fps machine
      // settles in the same wall-clock time a 60fps one does.
      var k = 1 - Math.exp(-Math.min(dt, 500) / SETTLE_TAU);
      lastDraw = now;
      cur.tint[0] = approach(cur.tint[0], target.tint[0], k);
      cur.tint[1] = approach(cur.tint[1], target.tint[1], k);
      cur.tint[2] = approach(cur.tint[2], target.tint[2], k);
      cur.sev = approach(cur.sev, target.sev, k);
      cur.turb = approach(cur.turb, target.turb, k);
      cur.speed = approach(cur.speed, target.speed, k);
      draw();
    }
    rafId = requestAnimationFrame(step);
  }

  function ensureLoop() {
    if (dead || rafId) return;

    // Reduced motion keeps full visual fidelity and draws exactly one frame.
    if (reduceMotion) {
      if (!painted && gl) draw();
      return;
    }
    if (!visible || document.visibilityState !== "visible") return;
    rafId = requestAnimationFrame(step);
  }

  function stopLoop() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  /* ---------- mount ---------- */

  function mount() {
    frame = document.getElementById("vitals");
    if (!frame) return;

    canvas = frame.querySelector(".vitals-canvas");
    fallbackEl = frame.querySelector(".vitals-fallback");
    paintFallback();

    var forced = /[?&]vitals=fallback\b/.test(window.location.search);
    if (forced) {
      dead = true;
      console.log("[warrant/vitals] fallback forced by query string");
      return;
    }

    if (!canvas || !window.WebGLRenderingContext) {
      dead = true;
      return;
    }

    if (!initGL()) {
      dead = true;
      gl = null;
      console.log("[warrant/vitals] no usable WebGL context - showing DOM fallback");
      return;
    }

    startedAt = performance.now();
    ambient = readAmbient();
    window.addEventListener("warrant:theme", onThemeChange);

    var mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    if (mq) {
      reduceMotion = mq.matches;
      var onMq = function () {
        reduceMotion = mq.matches;
        painted = false;
        stopLoop();
        ensureLoop();
      };
      if (mq.addEventListener) mq.addEventListener("change", onMq);
      else if (mq.addListener) mq.addListener(onMq);
    }

    // Offscreen means no drawing at all.
    if (window.IntersectionObserver) {
      new IntersectionObserver(
        function (entries) {
          visible = entries[0].isIntersecting;
          if (visible) ensureLoop();
          else stopLoop();
        },
        { threshold: 0.01 }
      ).observe(frame);
    } else {
      visible = true;
    }

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") ensureLoop();
      else stopLoop();
    });

    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        if (reduceMotion) {
          painted = false;
          ensureLoop();
        }
      }).observe(frame);
    }

    snapToTarget();
    draw();
    ensureLoop();
  }

  window.WarrantVitals = { setHealth: setHealth };

  /* The boot orb holds a context of its own while it plays, and two
     full-screen shaders contending for the GPU in front of a judge is not a
     trade worth making. boot.js releases its context with
     WEBGL_lose_context and only then calls back, so exactly one context is
     ever live. Until that happens the ribbon shows its DOM fallback, which is
     invisible underneath the overlay anyway, and setHealth keeps accumulating
     targets so the first drawn frame already carries the current reading.

     With no boot.js present, or with ?boot=0, this resolves immediately. */
  function startWhenGpuIsFree() {
    if (window.WarrantBoot && typeof window.WarrantBoot.whenReleased === "function") {
      window.WarrantBoot.whenReleased(mount);
    } else {
      mount();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startWhenGpuIsFree);
  } else {
    startWhenGpuIsFree();
  }
})();
