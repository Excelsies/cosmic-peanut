// @ts-check
/** Test-only helper (not imported by any visualizer). */

/** Records its construction and color stops, so two identically built gradients compare equal. */
class FakeGradient {
  /** @param {string} kind @param {number[]} args */
  constructor(kind, args) {
    this.kind = kind;
    this.args = args;
    /** @type {[number, string][]} */
    this.stops = [];
  }

  /** @param {number} offset @param {string} color */
  addColorStop(offset, color) {
    this.stops.push([offset, color]);
  }
}

/** A Path2D stand-in that records its ops; install it with `vi.stubGlobal("Path2D", FakePath2D)`. */
export class FakePath2D {
  static created = 0;
  /** @type {{ op: string, x?: number, y?: number }[]} */
  ops = [];

  /** @param {FakePath2D} [from] */
  constructor(from) {
    FakePath2D.created++;
    if (from) this.ops = from.ops.slice();
  }

  /** @param {number} x @param {number} y */
  moveTo(x, y) {
    this.ops.push({ op: "move", x, y });
  }

  /** @param {number} x @param {number} y */
  lineTo(x, y) {
    this.ops.push({ op: "line", x, y });
  }

  closePath() {
    this.ops.push({ op: "close" });
  }
}

/**
 * A 2d-context stand-in that records path, fill and stroke calls with the state at the time;
 * save/restore keep a state stack like the real thing. Only translations are supported: logged
 * points are in canvas pixels, and drawing a FakePath2D logs `begin` (with the path) and its ops.
 */
export function fakeContext() {
  /** @type {{ op: string, x?: number, y?: number, w?: number, h?: number, gco?: string, alpha?: number, style?: unknown, width?: number, path?: FakePath2D }[]} */
  const log = [];
  /** @type {any[][]} */
  const stack = [];
  let tx = 0;
  let ty = 0;
  /** @param {FakePath2D} [path] */
  const drawPath = (path) => {
    if (!path) return;
    log.push({ op: "begin", path });
    for (const e of path.ops) log.push(e.x === undefined ? { ...e } : { op: e.op, x: e.x + tx, y: /** @type {number} */ (e.y) + ty });
  };
  const g = {
    globalCompositeOperation: "source-over",
    globalAlpha: 1,
    strokeStyle: /** @type {unknown} */ ("#000"),
    fillStyle: /** @type {unknown} */ ("#000"),
    lineWidth: 1,
    lineJoin: "miter",
    clearRect: (/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h) =>
      log.push({ op: "clear", x: x + tx, y: y + ty, w, h }),
    fillRect: (/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h) =>
      log.push({ op: "fillRect", x: x + tx, y: y + ty, w, h, alpha: g.globalAlpha, style: g.fillStyle }),
    createLinearGradient: (/** @type {number[]} */ ...args) => new FakeGradient("linear", args),
    createRadialGradient: (/** @type {number[]} */ ...args) => new FakeGradient("radial", args),
    setTransform: (
      /** @type {number} */ a,
      /** @type {number} */ b,
      /** @type {number} */ c,
      /** @type {number} */ d,
      /** @type {number} */ e,
      /** @type {number} */ f,
    ) => {
      if (a !== 1 || b !== 0 || c !== 0 || d !== 1) throw new Error("fakeContext supports translations only");
      [tx, ty] = [e, f];
    },
    save: () => {
      log.push({ op: "save" });
      stack.push([g.globalCompositeOperation, g.globalAlpha, g.strokeStyle, g.fillStyle, g.lineWidth, g.lineJoin, tx, ty]);
    },
    restore: () => {
      log.push({ op: "restore" });
      const s = stack.pop();
      if (s) [g.globalCompositeOperation, g.globalAlpha, g.strokeStyle, g.fillStyle, g.lineWidth, g.lineJoin, tx, ty] = s;
    },
    clip: () => log.push({ op: "clip" }),
    rect: (/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h) =>
      log.push({ op: "rect", x: x + tx, y: y + ty, w, h }),
    beginPath: () => log.push({ op: "begin" }),
    closePath: () => log.push({ op: "close" }),
    moveTo: (/** @type {number} */ x, /** @type {number} */ y) => log.push({ op: "move", x: x + tx, y: y + ty }),
    lineTo: (/** @type {number} */ x, /** @type {number} */ y) => log.push({ op: "line", x: x + tx, y: y + ty }),
    fill: (/** @type {FakePath2D} */ path) => {
      drawPath(path);
      log.push({ op: "fill", gco: g.globalCompositeOperation, alpha: g.globalAlpha });
    },
    stroke: (/** @type {FakePath2D} */ path) => {
      drawPath(path);
      log.push({ op: "stroke", gco: g.globalCompositeOperation, alpha: g.globalAlpha, style: g.strokeStyle, width: g.lineWidth });
    },
  };
  return { g, log, ctx: /** @type {CanvasRenderingContext2D} */ (/** @type {unknown} */ (g)) };
}
