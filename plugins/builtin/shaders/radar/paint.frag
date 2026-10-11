#version 300 es
// Polar phosphor update. The texture is bearing (x, 0 = north, clockwise, one turn) × radius
// (y, 0 = center, 1 = rim; frequency on a log scale, lib/radar.js radiusOfFreq), stored as
// brightness · e^clock (lib/radar.js createDecay). Texels inside the wedge the arm swept since last
// frame, [u_start, u_start + u_span), are rewritten with one spectrogram slice: last frame's column
// at the wedge start blending to this frame's at the arm, so fast sweeps show no steps. In place
// (scissored to the wedge) every other texel is left alone; a full pass (u_full, ping-pong) also
// copies them, faded by u_decay. Exact and deterministic: empty spectrum → exactly 0.
precision highp float;
out vec4 o;
uniform sampler2D u_prev;
uniform sampler2D u_column; // R32F, width = phosphor height: row 0 last frame's, row 1 this frame's
uniform bool u_full;
uniform float u_decay;
uniform float u_scale;      // e^clock of the texels written now
uniform float u_start;
uniform float u_span;

void main() {
  ivec2 px = ivec2(gl_FragCoord.xy);
  float v = u_full ? texelFetch(u_prev, px, 0).r * u_decay : 0.0;
  float bearing = gl_FragCoord.x / float(textureSize(u_prev, 0).x);
  float d = bearing - u_start;
  d -= floor(d);
  if (u_span >= 1.0 || d < u_span) {  // lib/radar.js inWedge
    float t = (u_span >= 1.0 || u_span <= 0.0) ? 1.0 : min(1.0, d / u_span);  // lib/radar.js wedgeMix
    float a = texelFetch(u_column, ivec2(px.y, 0), 0).r;
    float b = texelFetch(u_column, ivec2(px.y, 1), 0).r;
    v = mix(a, b, t) * u_scale;
  } else if (!u_full) {
    discard;
  }
  o = vec4(v, 0.0, 0.0, 1.0);
}
