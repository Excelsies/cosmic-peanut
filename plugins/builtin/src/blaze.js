// @ts-check
/**
 * Blaze — a real-time fire simulation fed by the music, for the `webgl2` renderer.
 *
 * Per frame (CPU, allocation-free): build a 256-texel fuel line (lib/fuel.js, lib/spectro.js).
 * With Feed = "spectrogram" (default) the 1,024-bin spectrum is laid on a log-frequency axis
 * (Layout "mirrored": bass the central columns, highs at both edges; "linear": low → high, left →
 * right), max-resampled per column, turned into dB levels (gate + slow per-frequency auto-gain),
 * and each column gets heat ∝ its level and a flame speed
 *   rise = RISE_MIN + (RISE_MAX − RISE_MIN) · level^(1 + Reactivity)   (0.04 … 2.4 heights/s)
 * that the sim drives its gas toward, so it reads as a live spectrogram with time flowing up and
 * loud frequencies racing ahead. Speed scales sim time, so on screen that's rise × Speed. Attacks
 * in 32 frequency groups throw jets (upward velocity + heat, ~300 ms). Feed = "waveform" is the
 * original feed: the resampled waveform × a slow auto-gain, no level-driven rise. A saved value
 * of the retired 64-band "spectrum" feed runs the spectrogram.
 * Then step the music drive (bass stoke + onset flare, flash-limited, × Reactivity) and run a
 * fixed 120 Hz simulation on a grid of canvas × Detail:
 *   curl      vorticity of the velocity field
 *   velocity  self-advection, buoyancy, curl-noise turbulence, vorticity confinement, jets,
 *             each column pulled toward its rise speed
 *   scalar    advect temperature + fuel, feed fuel from the seed along the bottom, jet heat, burn, cool
 * and composite the temperature through the palette ramp onto the transparent canvas (the iron
 * and nickel palettes add sparks on top, lib/sparks.js). Rendering
 * "blackbody" (default) builds that ramp from Tetraballs' fire model; "simple" uses hand-keyed
 * ramps (lib/fire.js).
 *
 * Sim targets are half floats when the GPU can render to them (EXT_color_buffer_float or
 * EXT_color_buffer_half_float): RG16F for velocity and temperature + fuel, R16F for vorticity,
 * divergence and pressure. Without either they fall back to RGBA8: velocity is stored
 * offset-encoded (±4 heights/s in 8 bits), temperature clamps at 1 and vorticity confinement is
 * off, so the fire is coarser and calmer but still works.
 */
import { createDrive, createStepper, fillRampLut, motion, simSize, sparkKind } from "./lib/fire.js";
import { createFeed, resolveFeed } from "./lib/fuel.js";
import { createProgram } from "./lib/gl.js";
import { createSparks, SPARK_FLOATS } from "./lib/sparks.js";

const SEED = 256;
const SIM_RATE = 120; // sim steps per second
const MAX_STEPS = 4;
const JACOBI = 8; // even, so the warm start stays in pres[0]
const JET_VEL = 3.6; // upward speed a full-strength jet drives its column to, heights/s
const JET_HEAT = 1.5; // heat a full-strength jet adds at its root
const RISE_PULL = 14; // how hard (1/s) a column's gas is pulled toward its rise speed
const SPARK_CAP = 1024; // most sparks alive at once (iron and nickel palettes)
const SPARK_WIDTH = 0.0055; // streak width, canvas heights

/** @type {import('../tidalviz').CreateVisualizer} */
export default async function create(ctx) {
  const gl = /** @type {WebGL2RenderingContext} */ (ctx.gl);
  const dir = "shaders/blaze/";
  const files = ["fullscreen.vert", "noise.glsl", "curl.frag", "velocity.frag", "scalar.frag", "composite.frag"];
  files.push("divergence.frag", "pressure.frag", "project.frag", "spark.vert", "spark.frag");
  const [vs, noise, curlFs, velFs, scalFs, compFs, divFs, presFs, projFs, sparkVs, sparkFs] = await Promise.all(
    files.map((f) => ctx.assets.text(dir + f)),
  );
  const inc = (/** @type {string} */ src) => src.replace("// #include noise", noise);
  const curlP = createProgram(gl, vs, curlFs, dir + "curl.frag");
  const velP = createProgram(gl, vs, inc(velFs), dir + "velocity.frag");
  const scalP = createProgram(gl, vs, inc(scalFs), dir + "scalar.frag");
  const compP = createProgram(gl, vs, inc(compFs), dir + "composite.frag");
  const divP = createProgram(gl, vs, divFs, dir + "divergence.frag");
  const presP = createProgram(gl, vs, presFs, dir + "pressure.frag");
  const projP = createProgram(gl, vs, projFs, dir + "project.frag");
  const sparkP = createProgram(gl, sparkVs, sparkFs, dir + "spark.frag");

  // Sparks: a CPU particle pool (lib/sparks.js) streamed into one dynamic vertex buffer.
  const sparks = createSparks(SPARK_CAP);
  const sparkVerts = new Float32Array(SPARK_CAP * 6 * SPARK_FLOATS);
  const sparkVao = gl.createVertexArray();
  const sparkBuf = gl.createBuffer();
  gl.bindVertexArray(sparkVao);
  gl.bindBuffer(gl.ARRAY_BUFFER, sparkBuf);
  gl.bufferData(gl.ARRAY_BUFFER, sparkVerts.byteLength, gl.DYNAMIC_DRAW);
  const stride = SPARK_FLOATS * 4;
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 4, gl.FLOAT, false, stride, 8);
  gl.enableVertexAttribArray(2);
  gl.vertexAttribPointer(2, 1, gl.FLOAT, false, stride, 24);
  gl.bindVertexArray(null);

  const half =
    Boolean(gl.getExtension("EXT_color_buffer_float")) || Boolean(gl.getExtension("EXT_color_buffer_half_float"));
  // stored = v * VENC[0] + VENC[1]
  const VENC_X = half ? 1 : 1 / 8;
  const VENC_Y = half ? 0 : 0.5;

  const vao = gl.createVertexArray();

  /** A sim texture + framebuffer. @param {boolean} linear */
  function target(linear) {
    const tex = /** @type {WebGLTexture} */ (gl.createTexture());
    const fbo = /** @type {WebGLFramebuffer} */ (gl.createFramebuffer());
    gl.bindTexture(gl.TEXTURE_2D, tex);
    const f = linear ? gl.LINEAR : gl.NEAREST;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return { tex, fbo };
  }
  const scal = [target(true), target(true)];
  const vel = [target(true), target(true)];
  const curl = target(false);
  const div = target(false);
  const pres = [target(false), target(false)];
  let sw = 0;
  let sh = 0;
  let cur = 0; // scalar ping-pong
  let vcur = 0; // velocity ping-pong

  /** (Re)allocate one target (`one`: a single channel) and clear it to `r, g`. @param {{tex: WebGLTexture, fbo: WebGLFramebuffer}} t @param {number} r @param {number} g */
  function alloc(t, r, g, one = false) {
    gl.bindTexture(gl.TEXTURE_2D, t.tex);
    if (!half) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, sw, sh, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    else if (one) gl.texImage2D(gl.TEXTURE_2D, 0, gl.R16F, sw, sh, 0, gl.RED, gl.HALF_FLOAT, null);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG16F, sw, sh, 0, gl.RG, gl.HALF_FLOAT, null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t.tex, 0);
    gl.clearColor(r, g, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  function resize() {
    const s = simSize(ctx.size.width, ctx.size.height, Number(ctx.params.detail));
    if (s.width === sw && s.height === sh) return;
    sw = s.width;
    sh = s.height;
    for (const t of scal) alloc(t, 0, 0);
    for (const t of vel) alloc(t, VENC_Y, VENC_Y);
    alloc(curl, 0, 0, true); // unused without half floats, but kept complete for the sampler
    if (half) for (const t of [div, ...pres]) alloc(t, 0, 0, true);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.useProgram(curlP.program);
    gl.uniform1f(curlP.u.u_inv, sh);
    gl.useProgram(velP.program);
    gl.uniform1f(velP.u.u_aspect, sw / sh);
    gl.uniform1f(velP.u.u_texelH, 1 / sh);
    gl.useProgram(scalP.program);
    gl.uniform2f(scalP.u.u_texel, 1 / sw, 1 / sh);
    gl.uniform1f(scalP.u.u_aspect, sw / sh);
    gl.useProgram(compP.program);
    gl.uniform2f(compP.u.u_simTexel, 1 / sw, 1 / sh);
  }

  // Uniform values persist per program, so samplers and constants are set once, size-dependent
  // ones in resize() and param-dependent ones in applyParams(); a sim step only sends its clock.
  gl.useProgram(curlP.program);
  gl.uniform1i(curlP.u.u_vel, 0);
  gl.uniform2f(curlP.u.u_venc, VENC_X, VENC_Y);
  gl.useProgram(velP.program);
  gl.uniform1i(velP.u.u_vel, 0);
  gl.uniform1i(velP.u.u_scal, 1);
  gl.uniform1i(velP.u.u_seed, 2);
  gl.uniform1i(velP.u.u_curl, 3);
  gl.uniform1f(velP.u.u_jetVel, JET_VEL);
  gl.uniform2f(velP.u.u_venc, VENC_X, VENC_Y);
  gl.uniform1f(velP.u.u_damp, 2.5);
  gl.useProgram(divP.program);
  gl.uniform1i(divP.u.u_vel, 0);
  gl.useProgram(presP.program);
  gl.uniform1i(presP.u.u_p, 0);
  gl.uniform1i(presP.u.u_div, 1);
  gl.useProgram(projP.program);
  gl.uniform1i(projP.u.u_vel, 0);
  gl.uniform1i(projP.u.u_p, 1);
  gl.useProgram(scalP.program);
  gl.uniform1i(scalP.u.u_scal, 0);
  gl.uniform1i(scalP.u.u_vel, 1);
  gl.uniform1i(scalP.u.u_seed, 2);
  gl.uniform2f(scalP.u.u_venc, VENC_X, VENC_Y);
  gl.uniform1f(scalP.u.u_jetVel, JET_VEL);
  gl.uniform1f(scalP.u.u_burn, 3);
  gl.useProgram(compP.program);
  gl.uniform1i(compP.u.u_scal, 0);
  gl.uniform1i(compP.u.u_lut, 1);

  function applyParams() {
    const p = ctx.params;
    const spectro = resolveFeed(p.feed) === "spectrogram" ? 1 : 0;
    const intensity = Number(p.intensity);
    gl.useProgram(velP.program);
    gl.uniform1f(velP.u.u_rise, spectro * RISE_PULL);
    // In the spectrogram the column's rise speed carries the gas; buoyancy only adds a lift.
    gl.uniform1f(velP.u.u_buoy, 3 - 1.8 * spectro);
    gl.useProgram(scalP.program);
    gl.uniform1f(scalP.u.u_ember, 0.08 * intensity);
    gl.uniform1f(scalP.u.u_jetHeat, JET_HEAT * intensity);
    gl.uniform1f(scalP.u.u_rise, spectro);
    gl.uniform1f(scalP.u.u_cool, 9 / Math.max(0.3, Number(p.height)));
    gl.useProgram(compP.program);
    gl.uniform1f(compP.u.u_glow, Number(p.glow));
  }

  // Fuel line: 256×1 RGBA16F (r = fuel, g = jet strength, b = rise speed), linear across the
  // bottom of the sim.
  const seedBuf = new Float32Array(SEED * 4);
  const seedTex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, seedTex);
  gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA16F, SEED, 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  // Palette ramp: 256×1 RGBA8, premultiplied.
  const lut = new Uint8Array(256 * 4);
  const lutTex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, lutTex);
  gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, 256, 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  function applyPalette() {
    fillRampLut(String(ctx.params.palette), lut, String(ctx.params.rendering));
    gl.bindTexture(gl.TEXTURE_2D, lutTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 256, 1, gl.RGBA, gl.UNSIGNED_BYTE, lut);
  }

  const feed = createFeed(SEED);
  const drive = createDrive();
  const stepper = createStepper(SIM_RATE, MAX_STEPS);
  const mo = { turbulence: 1, speed: 1, reactivity: 1 };
  let clock = 0;

  applyPalette();
  applyParams();
  resize();

  /** Draw a fullscreen pass into `t` (or the canvas when null). @param {{fbo: WebGLFramebuffer} | null} t */
  function into(t) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fbo : null);
  }
  let active = -1;
  /** @param {number} unit @param {WebGLTexture | null} tex */
  function bind(unit, tex) {
    if (unit !== active) gl.activeTexture(gl.TEXTURE0 + (active = unit));
    gl.bindTexture(gl.TEXTURE_2D, tex);
  }

  return {
    frame(audio, time) {
      const p = ctx.params;
      motion(p, ctx.reduceMotion, mo);
      const intensity = Number(p.intensity);

      // 1. Audio → fuel line + jets (CPU, no allocation).
      feed.step(audio, time.dt, p.feed, p.layout, mo.reactivity, ctx.reduceFlashing);
      const fuel = feed.fuel;
      const jet = feed.jet;
      const rise = feed.rise;
      for (let i = 0; i < SEED; i++) {
        seedBuf[4 * i] = fuel[i];
        seedBuf[4 * i + 1] = jet[i];
        seedBuf[4 * i + 2] = rise[i];
      }
      bind(2, seedTex);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, SEED, 1, gl.RGBA, gl.FLOAT, seedBuf);

      drive.step(audio, time.dt, ctx.reduceFlashing, mo.reactivity);
      const react = mo.reactivity > 0 ? Math.min(2, mo.reactivity) : 0;
      const boost = drive.boost;
      sparks.step(time.dt * mo.speed, sparkKind(String(p.palette)), fuel, Math.min(1, drive.stoke * intensity), drive.flare);

      gl.bindVertexArray(vao);
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.BLEND);
      gl.viewport(0, 0, sw, sh);
      const dtSim = (1 / SIM_RATE) * mo.speed;
      const n = stepper.step(time.dt);

      for (let k = 0; k < n; k++) {
        clock = (clock + dtSim) % 1000;
        const s0 = scal[cur];
        const s1 = scal[cur ^ 1];
        const v0 = vel[vcur];
        const v1 = vel[vcur ^ 1];

        // Vorticity.
        if (half) {
          into(curl);
          gl.useProgram(curlP.program);
          bind(0, v0.tex);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
        }

        // Velocity.
        into(v1);
        gl.useProgram(velP.program);
        bind(0, v0.tex);
        bind(1, s0.tex);
        bind(3, curl.tex);
        const u = velP.u;
        // Motion follows the live reduceMotion, so it's per frame (identical across the steps).
        if (k === 0) {
          gl.uniform1f(u.u_dt, dtSim);
          gl.uniform1f(u.u_turb, 2.5 * mo.turbulence);
          gl.uniform1f(u.u_vort, half ? 10 * mo.turbulence : 0);
        }
        gl.uniform1f(u.u_time, clock);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        let vOut = v1;

        // Pressure projection (half floats only: pressure is signed): divergence, a few Jacobi
        // iterations warm-started from the previous step, subtract the gradient back into v0.
        if (half) {
          into(div);
          gl.useProgram(divP.program);
          bind(0, v1.tex);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          gl.useProgram(presP.program);
          bind(1, div.tex);
          for (let j = 0; j < JACOBI; j++) {
            into(pres[(j + 1) & 1]);
            bind(0, pres[j & 1].tex);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
          }
          into(v0);
          gl.useProgram(projP.program);
          bind(0, v1.tex);
          bind(1, pres[JACOBI & 1].tex);
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          vOut = v0;
        } else {
          vcur ^= 1;
        }

        // Temperature + fuel.
        into(s1);
        gl.useProgram(scalP.program);
        bind(0, s0.tex);
        bind(1, vOut.tex);
        const w = scalP.u;
        if (k === 0) {
          gl.uniform1f(w.u_dt, dtSim);
          gl.uniform1f(w.u_inject, 2.6 * intensity * (0.7 + 0.6 * boost));
          // The flash-limited share of the boost that isn't bass stoke: the onset flare.
          const flare = boost - 0.5 * react * drive.stoke;
          gl.uniform1f(w.u_flare, 0.6 * intensity * (flare > 0 ? flare : 0));
        }
        gl.uniform1f(w.u_time, clock);
        gl.drawArrays(gl.TRIANGLES, 0, 3);

        cur ^= 1;
      }

      // Composite onto the transparent canvas.
      const { width: cw, height: ch } = ctx.size;
      into(null);
      gl.viewport(0, 0, cw, ch);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(compP.program);
      bind(0, scal[cur].tex);
      bind(1, lutTex);
      const c = compP.u;
      gl.uniform1f(c.u_aspect, cw / ch);
      gl.uniform1f(c.u_time, clock);
      gl.uniform1f(c.u_exposure, 1 + 0.3 * boost);
      gl.drawArrays(gl.TRIANGLES, 0, 3);

      // Sparks, added over the fire (premultiplied).
      if (sparks.count > 0) {
        const nv = sparks.fill(sparkVerts, cw / ch, SPARK_WIDTH);
        gl.useProgram(sparkP.program);
        gl.bindVertexArray(sparkVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, sparkBuf);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, sparkVerts, 0, nv * SPARK_FLOATS);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE);
        gl.drawArrays(gl.TRIANGLES, 0, nv);
        gl.disable(gl.BLEND);
        gl.bindVertexArray(vao);
      }
    },

    resize,

    params(changed) {
      if ("palette" in changed || "rendering" in changed) applyPalette();
      if ("detail" in changed) resize();
      applyParams();
    },

    dispose() {
      for (const t of [...scal, ...vel, curl, div, ...pres]) {
        gl.deleteTexture(t.tex);
        gl.deleteFramebuffer(t.fbo);
      }
      gl.deleteTexture(seedTex);
      gl.deleteTexture(lutTex);
      gl.deleteVertexArray(vao);
      gl.deleteVertexArray(sparkVao);
      gl.deleteBuffer(sparkBuf);
      for (const pr of [curlP, velP, scalP, compP, divP, presP, projP, sparkP]) gl.deleteProgram(pr.program);
    },
  };
}
