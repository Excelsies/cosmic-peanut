// @ts-check
/**
 * Where the pack's lines are drawn (params.js `renderPath`). Fine lines go straight to the canvas.
 * Soft lines go to a 1× (CSS-pixel) RGBA8 buffer, 2× multisampled when Antialias is on (the
 * prototype's canvas had antialias on; the host's doesn't), resolved into a texture and upscaled
 * linearly to the canvas. 2× MSAA matches the prototype side by side at ~20% less GPU time than 4×.
 * RGBA8 end to end: the lines' premultiplied alpha survives the resolve and the blit. When the 1×
 * buffer would be canvas-sized (DPR 1, or a lowered render scale) the upscale is a texel-exact
 * copy, so plain Soft lines skip the texture and the blit. Buffers a path doesn't use keep a 1×1
 * placeholder, so their memory is freed. GL glue shared by both entry modules; `begin` and `end`
 * allocate nothing. The context is the module's alone, so the clear colour and the blit's sampler
 * are set once here, and callers leave texture unit 0 active.
 */
import { createProgram } from "./gl.js";
import { renderPath } from "./params.js";

/**
 * @param {WebGL2RenderingContext} gl
 * @param {import('../../tidalviz').VisualizerContext} ctx
 * @param {string} blitVs shaders/blit.vert @param {string} blitFs shaders/blit.frag
 */
export function createSoftTarget(gl, ctx, blitVs, blitFs) {
  const blit = createProgram(gl, blitVs, blitFs, "shaders/blit");
  const blitVao = /** @type {WebGLVertexArrayObject} */ (gl.createVertexArray());
  const samples = Math.min(2, gl.getParameter(gl.MAX_SAMPLES));
  const msaaRb = gl.createRenderbuffer();
  const msaaFbo = gl.createFramebuffer();
  const softTex = gl.createTexture();
  const softFbo = gl.createFramebuffer();
  let softW = 0;
  let softH = 0;
  let softPath = "";
  /** Lines go straight to the canvas: Fine lines, or plain Soft lines whose upscale would be 1:1. */
  let direct = false;

  gl.clearColor(0, 0, 0, 0); // transparent canvas: the shell supplies black or the desktop
  gl.useProgram(blit.program);
  gl.uniform1i(blit.u.uSrc, 0);

  const target = {
    /** Size the buffers to the canvas and the Lines / Antialias params. Allocates; on change only. */
    resize() {
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
    },
    /** Bind where this frame's lines go and clear it to transparent. @param {"canvas" | "msaa" | "soft"} path */
    begin(path) {
      if (direct) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, ctx.size.width, ctx.size.height);
      } else {
        gl.bindFramebuffer(gl.FRAMEBUFFER, path === "msaa" ? msaaFbo : softFbo);
        gl.viewport(0, 0, softW, softH);
      }
      gl.clear(gl.COLOR_BUFFER_BIT);
    },
    /** Resolve and upscale soft lines onto the canvas (nothing for fine lines). @param {"canvas" | "msaa" | "soft"} path */
    end(path) {
      if (direct) return;
      if (path === "msaa") {
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, msaaFbo);
        gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, softFbo);
        gl.blitFramebuffer(0, 0, softW, softH, 0, 0, softW, softH, gl.COLOR_BUFFER_BIT, gl.NEAREST);
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, ctx.size.width, ctx.size.height);
      gl.useProgram(blit.program);
      gl.bindTexture(gl.TEXTURE_2D, softTex);
      gl.bindVertexArray(blitVao);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    dispose() {
      gl.deleteTexture(softTex);
      gl.deleteFramebuffer(softFbo);
      gl.deleteFramebuffer(msaaFbo);
      gl.deleteRenderbuffer(msaaRb);
      gl.deleteVertexArray(blitVao);
      gl.deleteProgram(blit.program);
    },
  };
  target.resize();
  return target;
}
