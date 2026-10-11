// @ts-check
/** Test-only helper (not imported by any visualizer). */

/**
 * A WebGL2 stand-in that keeps the state uploads depend on: per-program uniform values (parsed
 * from the shader sources), the active texture unit and each unit's binding, and each texture's
 * internal format. Every draw records a snapshot of what the current program would read and
 * throws if one of its uniforms was never set. Uniform calls on another program's location throw,
 * like GL's INVALID_OPERATION. `calls` counts every method call by name.
 * @param {{ extensions?: string[] }} [opts]
 */
export function fakeWebGL2({ extensions = ["EXT_color_buffer_float"] } = {}) {
  /** @type {Map<string, number>} */
  const consts = new Map([
    ["TEXTURE0", 0x84c0],
    ["FRAMEBUFFER_COMPLETE", 0x8cd5],
  ]);
  /** @param {string} k */
  const constant = (k) => {
    const unit = /^TEXTURE(\d+)$/.exec(k);
    if (unit) return 0x84c0 + Number(unit[1]);
    if (!consts.has(k)) consts.set(k, 0x100000 + consts.size);
    return /** @type {number} */ (consts.get(k));
  };
  const nameOf = (/** @type {number} */ v) => [...consts].find(([, n]) => n === v)?.[0];

  /** @typedef {{ src: string }} Shader */
  /** @typedef {{ shaders: Shader[], names: string[], values: Map<string, number[]>, label: string }} Program */
  /** @typedef {{ format?: string }} Texture */
  /** @type {Program | null} */
  let program = null;
  let unit = 0;
  /** @type {(Texture | null)[]} */
  const units = [];
  /** @type {Map<string, number>} */
  const calls = new Map();
  /** @type {{ program: string, uniforms: Record<string, number[]>, textures: Record<string, Texture | null> }[]} */
  const draws = [];
  /** @type {{ program: string, name: string, values: number[] }[]} */
  const uploads = [];

  /** @param {{ program: Program, name: string } | null} loc @param {number[]} values */
  function upload(loc, values) {
    if (!loc) return;
    if (loc.program !== program) throw new Error(`uniform ${loc.name} set while another program is current`);
    loc.program.values.set(loc.name, values);
    uploads.push({ program: loc.program.label, name: loc.name, values });
  }

  function draw() {
    const p = /** @type {Program} */ (program);
    /** @type {Record<string, number[]>} */
    const uniforms = {};
    /** @type {Record<string, Texture | null>} */
    const textures = {};
    for (const name of p.names) {
      const v = p.values.get(name);
      if (!v) throw new Error(`${p.label}: uniform ${name} never set`);
      uniforms[name] = v;
      if (p.shaders.some((s) => new RegExp(`uniform\\s+(?:[a-z]+p\\s+)?sampler2D\\s+${name}\\b`).test(s.src))) {
        textures[name] = units[v[0]] ?? null;
      }
    }
    draws.push({ program: p.label, uniforms, textures });
  }

  /** @type {Record<string, (...a: any[]) => unknown>} */
  const methods = {
    getExtension: (/** @type {string} */ name) => (extensions.includes(name) ? {} : null),
    isContextLost: () => false,
    createShader: () => ({ src: "" }),
    shaderSource: (/** @type {Shader} */ s, /** @type {string} */ src) => {
      s.src = src;
    },
    getShaderParameter: () => true,
    createProgram: () => ({ shaders: [], names: [], values: new Map(), label: "" }),
    attachShader: (/** @type {Program} */ p, /** @type {Shader} */ s) => p.shaders.push(s),
    linkProgram: (/** @type {Program} */ p) => {
      const fs = p.shaders[p.shaders.length - 1].src;
      p.label = /^\/\/ (.*)$/m.exec(fs)?.[1] ?? "";
      for (const s of p.shaders) {
        for (const m of s.src.matchAll(/^\s*uniform\s+(?:[a-z]+p\s+)?\w+\s+([^;]+);/gm)) {
          for (const n of m[1].split(",")) {
            const name = n.trim().replace(/\[.*$/, "");
            if (!p.names.includes(name)) p.names.push(name);
          }
        }
      }
    },
    getProgramParameter: (/** @type {Program} */ p, /** @type {number} */ k) =>
      k === constant("ACTIVE_UNIFORMS") ? p.names.length : true,
    getActiveUniform: (/** @type {Program} */ p, /** @type {number} */ i) => ({ name: p.names[i] }),
    getUniformLocation: (/** @type {Program} */ p, /** @type {string} */ name) => ({ program: p, name }),
    useProgram: (/** @type {Program | null} */ p) => {
      program = p;
    },
    createTexture: () => ({}),
    activeTexture: (/** @type {number} */ u) => {
      unit = u - constant("TEXTURE0");
    },
    bindTexture: (/** @type {number} */ _t, /** @type {Texture | null} */ tex) => {
      units[unit] = tex;
    },
    texImage2D: (/** @type {number} */ _t, /** @type {number} */ _l, /** @type {number} */ internal) => {
      const tex = units[unit];
      if (tex) tex.format = nameOf(internal);
    },
    texStorage2D: (/** @type {number} */ _t, /** @type {number} */ _l, /** @type {number} */ internal) => {
      const tex = units[unit];
      if (tex) tex.format = nameOf(internal);
    },
    checkFramebufferStatus: () => constant("FRAMEBUFFER_COMPLETE"),
    drawArrays: draw,
    drawArraysInstanced: draw,
  };
  for (const n of ["1i", "1ui", "1f", "2f", "3f", "4f"]) {
    methods[`uniform${n}`] = (loc, ...v) => upload(loc, v);
  }
  for (const n of ["1fv", "2fv", "3fv", "4fv", "Matrix3fv"]) {
    methods[`uniform${n}`] = (loc, ...v) => upload(loc, [...v[v.length - 1]]);
  }

  const gl = new Proxy(
    {},
    {
      get(_, k) {
        if (typeof k !== "string") return undefined;
        if (/^[A-Z0-9_]+$/.test(k)) return constant(k);
        return (/** @type {unknown[]} */ ...a) => {
          calls.set(k, (calls.get(k) ?? 0) + 1);
          const m = methods[k];
          if (m) return m(...a);
          if (k.startsWith("create")) return {};
          return undefined;
        };
      },
    },
  );
  return { gl: /** @type {WebGL2RenderingContext} */ (/** @type {unknown} */ (gl)), calls, draws, uploads };
}
