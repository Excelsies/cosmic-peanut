// @ts-check
/** Test-only helper (not imported by any visualizer). */
// @ts-expect-error -- a Node builtin (tests run in Node); the web tsconfig has no @types/node.
import { readFileSync } from "node:fs";

/**
 * A WebGL2 stand-in that logs every call. Programs report the uniforms their shader sources
 * declare (so `createProgram`'s `u` map is real), locations are `{ name }`, constants are stable
 * numbers named by `name(n)`, and every extension in `extensions` is available.
 * @param {{ extensions?: string[] }} [opts]
 */
export function fakeGl(opts = {}) {
  const extensions = opts.extensions ?? ["EXT_color_buffer_float", "EXT_color_buffer_half_float"];
  /** @type {{ op: string, args: any[] }[]} */
  const log = [];
  /** @type {Map<string, number>} */
  const ids = new Map();
  /** @type {Map<number, string>} */
  const names = new Map();
  const constant = (/** @type {string} */ k) => {
    let id = ids.get(k);
    if (id === undefined) {
      id = 0x8000 + ids.size;
      ids.set(k, id);
      names.set(id, k);
    }
    return id;
  };
  /** @type {Record<string, (...a: any[]) => any>} */
  const impl = {
    getExtension: (n) => (extensions.includes(n) ? {} : null),
    createShader: () => ({ src: "" }),
    shaderSource: (s, src) => {
      s.src = src;
    },
    createProgram: () => ({ shaders: [], uniforms: [] }),
    attachShader: (p, s) => p.shaders.push(s),
    linkProgram: (p) => {
      for (const s of p.shaders) {
        for (const m of s.src.matchAll(/^\s*uniform\s+(?:\w+\s+)+?(\w+)\s*(\[[^\]]*\])?\s*;/gm)) {
          p.uniforms.push(m[2] ? `${m[1]}[0]` : m[1]);
        }
      }
    },
    getShaderParameter: () => true,
    getProgramParameter: (p, k) => (k === constant("ACTIVE_UNIFORMS") ? p.uniforms.length : true),
    getActiveUniform: (p, i) => ({ name: p.uniforms[i] }),
    getUniformLocation: (_, name) => ({ name: name.replace(/\[0\]$/, "") }),
    isContextLost: () => false,
  };
  const gl = new Proxy(
    {},
    {
      get(_, k) {
        if (typeof k !== "string") return undefined;
        if (/^[A-Z][A-Z0-9_]*$/.test(k)) return constant(k);
        return (/** @type {any[]} */ ...args) => {
          log.push({ op: k, args });
          if (impl[k]) return impl[k](...args);
          if (k.startsWith("create")) return { kind: k.slice(6) };
          return null;
        };
      },
    },
  );
  return {
    gl: /** @type {WebGL2RenderingContext} */ (/** @type {unknown} */ (gl)),
    log,
    /** The constant's name, e.g. name(gl.RG16F) === "RG16F". @param {number} n */
    name: (n) => names.get(n),
  };
}

/** `ctx.assets` reading the built-in repo's real files. */
export const builtinAssets = {
  text: async (/** @type {string} */ path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8"),
};
