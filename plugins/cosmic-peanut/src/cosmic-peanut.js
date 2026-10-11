// @ts-check
/**
 * Cosmic Peanut — each moment's waveform becomes a ring on an invisible sphere; rings drift from
 * the north pole to the south pole while the camera slowly orbits. `webgl2` renderer.
 *
 * All geometry is computed on the GPU from gl_VertexID / gl_InstanceID (point j, ring i) and an
 * M × N R32F history texture used as a circular buffer, so a frame is a few uniforms, at most a few
 * one-row uploads, and one drawArraysInstanced call (plus a blit in Soft lines mode). State that
 * only changes with params is set in params(). The logic lives in ./lib (pure, tested); this file
 * is the GL glue. frame() allocates nothing.
 */
import { cameraDistance, createCamera, RADIUS } from "./lib/camera.js";
import { createProgram } from "./lib/gl.js";
import { createHistory } from "./lib/history.js";
import { GENTLE, gentle } from "./lib/motion.js";
import { densityRings, paletteIndex, renderPath, ringGain } from "./lib/params.js";
import { createPulse } from "./lib/pulse.js";

/** Points per ring: high, so the waveform stays jagged. */
const M = 512;

/** @type {import('../tidalviz').CreateVisualizer} */
export default async function create(ctx) {
  const gl = /** @type {WebGL2RenderingContext} */ (ctx.gl);
  const [ringVs, ringFs, blitVs, blitFs, manifest] = await Promise.all([
    ctx.assets.text("shaders/rings.vert"),
    ctx.assets.text("shaders/rings.frag"),
    ctx.assets.text("shaders/blit.vert"),
    ctx.assets.text("shaders/blit.frag"),
    ctx.assets.json("tidalviz.json"),
  ]);
  // Manifest defaults decide whether the user has touched Orbit speed / Bass pulse (reduced motion).
  const specs = /** @type {import('../tidalviz').Manifest} */ (manifest).visualizers[0].params ?? [];
  /** @param {string} id */
  const defaultOf = (id) => Number(specs.find((p) => p.id === id)?.default);
  const orbitDefault = defaultOf("orbit");
  const pulseDefault = defaultOf("pulse");

  const rings = createProgram(gl, ringVs, ringFs, "shaders/rings");
  const blit = createProgram(gl, blitVs, blitFs, "shaders/blit");
  const U = rings.u;

  const vao = /** @type {WebGLVertexArrayObject} */ (gl.createVertexArray()); // no attributes
  const hist = gl.createTexture();
  // This context is ours alone, so constant state is set once (texture unit 0 stays active).
  gl.bindVertexArray(vao);
  gl.clearColor(0, 0, 0, 0); // transparent canvas: the shell supplies black or the desktop
  gl.useProgram(blit.program);
  gl.uniform1i(blit.u.uSrc, 0);
  gl.useProgram(rings.program);
  gl.uniform1i(U.uHist, 0);
  gl.uniform1i(U.uM, M);
  gl.uniform1f(U.uRadius, RADIUS);

  const history = createHistory(M, densityRings(ctx.params.density), (row, ring) => {
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, row, M, 1, gl.RED, gl.FLOAT, ring);
  });
  const camera = createCamera();
  const pulse = createPulse();

  function setParamUniforms() {
    const p = ctx.params;
    const fine = renderPath(p.lines, p.antialias) === "canvas";
    gl.useProgram(rings.program);
    gl.uniform1i(U.uN, history.N);
    gl.uniform1f(U.uAmp, Number(p.amp));
    gl.uniform1f(U.uGain, ringGain(Number(p.bright), history.N, fine));
    gl.uniform1f(U.uPalette, paletteIndex(p.palette));
  }
  setParamUniforms();

  /** Allocate a sentinel-filled history texture for the current density. */
  function allocate() {
    gl.bindTexture(gl.TEXTURE_2D, hist);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, M, history.N, 0, gl.RED, gl.FLOAT, history.blank());
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  }
  allocate();

  // Soft lines: rings go to a 1× (CSS-pixel) multisampled RGBA8 buffer (the prototype's canvas had
  // antialias on; the host's doesn't), resolve into a texture, then upscale linearly to the canvas.
  // 2× MSAA matches the prototype's smoothness side by side at ~20% less GPU time than 4×.
  // RGBA8 end to end: the rings' premultiplied alpha survives the resolve and the blit.
  const samples = Math.min(2, gl.getParameter(gl.MAX_SAMPLES));
  const msaaRb = gl.createRenderbuffer();
  const msaaFbo = gl.createFramebuffer();
  const softTex = gl.createTexture();
  const softFbo = gl.createFramebuffer();
  let softW = 0;
  let softH = 0;
  let softPath = "";
  /** Rings go straight to the canvas: Fine lines, or plain Soft lines whose upscale would be 1:1. */
  let direct = false;
  function resizeSoft() {
    const w = Math.max(1, Math.min(ctx.size.width, Math.round(ctx.size.cssWidth)));
    const h = Math.max(1, Math.min(ctx.size.height, Math.round(ctx.size.cssHeight)));
    const path = renderPath(ctx.params.lines, ctx.params.antialias);
    const toCanvas = path === "canvas" || (path === "soft" && w === ctx.size.width && h === ctx.size.height);
    if (w === softW && h === softH && path === softPath && toCanvas === direct) return;
    softW = w;
    softH = h;
    softPath = path;
    direct = toCanvas;
    const msaaOn = path === "msaa";
    const texOn = !direct;
    // Buffers a path doesn't use keep only a 1×1 placeholder, so their memory is freed.
    gl.bindTexture(gl.TEXTURE_2D, softTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, texOn ? w : 1, texOn ? h : 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, softFbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, softTex, 0);
    gl.bindRenderbuffer(gl.RENDERBUFFER, msaaRb);
    gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, gl.RGBA8, msaaOn ? w : 1, msaaOn ? h : 1);
    gl.bindFramebuffer(gl.FRAMEBUFFER, msaaFbo);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, msaaRb);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }
  resizeSoft();

  return {
    frame(audio, time) {
      const p = ctx.params;
      const dt = time.dt;
      const orbit = gentle(Number(p.orbit), orbitDefault, ctx.reduceMotion, GENTLE.orbit);
      const strength = gentle(Number(p.pulse), pulseDefault, ctx.reduceMotion, GENTLE.pulse);
      const amp = Number(p.amp);
      const path = renderPath(p.lines, p.antialias);

      // Push the rings that are due (fixed rate), one texSubImage2D row each.
      gl.bindTexture(gl.TEXTURE_2D, hist);
      history.step(audio.waveform, dt, Number(p.travel), Number(p.detail));
      const pulseAmt = pulse.step(audio, dt) * strength;

      camera.update(dt, orbit);
      const { width, height } = ctx.size;
      const aspect = width / height;
      const dist = cameraDistance(amp, aspect);
      camera.writeMatrices(aspect, dist);

      if (direct) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, width, height);
      } else {
        gl.bindFramebuffer(gl.FRAMEBUFFER, path === "msaa" ? msaaFbo : softFbo);
        gl.viewport(0, 0, softW, softH);
      }
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.ONE, gl.ONE); // additive: overlapping rings glow
      gl.useProgram(rings.program);
      gl.uniform1i(U.uHead, history.head);
      gl.uniform1f(U.uFrac, history.frac);
      gl.uniform1f(U.uCamDist, dist);
      gl.uniform1f(U.uPulse, pulseAmt);
      gl.uniformMatrix4fv(U.uProj, false, camera.proj);
      gl.uniformMatrix4fv(U.uView, false, camera.view);
      gl.drawArraysInstanced(gl.LINE_STRIP, 0, M + 1, history.N - 1); // ring N − 1 has faded out
      gl.disable(gl.BLEND);

      if (!direct) {
        if (path === "msaa") {
          gl.bindFramebuffer(gl.READ_FRAMEBUFFER, msaaFbo);
          gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, softFbo);
          gl.blitFramebuffer(0, 0, softW, softH, 0, 0, softW, softH, gl.COLOR_BUFFER_BIT, gl.NEAREST);
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, width, height);
        gl.useProgram(blit.program);
        gl.bindTexture(gl.TEXTURE_2D, softTex);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }
    },

    resize() {
      resizeSoft();
    },

    params(changed) {
      if ("antialias" in changed || "lines" in changed) resizeSoft();
      if ("density" in changed && history.setDensity(densityRings(changed.density))) allocate();
      setParamUniforms();
    },

    pointer(e) {
      camera.pointer(e);
    },

    dispose() {
      gl.deleteTexture(hist);
      gl.deleteTexture(softTex);
      gl.deleteFramebuffer(softFbo);
      gl.deleteFramebuffer(msaaFbo);
      gl.deleteRenderbuffer(msaaRb);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(rings.program);
      gl.deleteProgram(blit.program);
    },
  };
}
