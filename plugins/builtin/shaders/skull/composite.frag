// Skull Trip: full-resolution op-art stripes with the skull layer over them.
// Prepended at load: #version, precision, the shape table and opart.glsl.

in vec2 v_uv;
out vec4 o_color;

uniform sampler2D u_skull; // premultiplied skull layer (possibly at reduced resolution)
uniform float u_circle;    // skull bounding circle radius, screen units (skip the fetch outside)

// The layer is opaque at all 8 taps one texel round uv. Those taps' footprints cover every texel a
// neighbour in this pixel's quad samples (each with weight >= 1/4), so if it holds, the neighbours
// hide their stripes too and the fwidth they share with a pixel that skips them never shows.
bool opaqueRound(vec2 uv) {
  vec2 t = 1.0 / vec2(textureSize(u_skull, 0));
  float a = 1.0;
  for (int y = -1; y <= 1; y++) {
    for (int x = -1; x <= 1; x++) {
      if (x != 0 || y != 0) a = min(a, textureLod(u_skull, uv + vec2(x, y) * t, 0.0).a);
    }
  }
  return a >= 0.999;
}

void main() {
  vec2 p = (v_uv * 2.0 - 1.0) * u_view;
  vec4 skull = length(p) < u_circle * 1.02 + 0.01 ? texture(u_skull, v_uv) : vec4(0.0);
  // Under the opaque skull the stripes don't show.
  if (skull.a >= 0.999 && opaqueRound(v_uv)) {
    o_color = skull;
    return;
  }
  float s = opStripe(opPhase(p, true));
  // Premultiplied stripes: ink is opaque; paper has u_paperAlpha ("black only" → transparent).
  vec4 stripes = vec4(mix(u_ink, u_paper * u_paperAlpha, s), mix(1.0, u_paperAlpha, s));
  o_color = skull + (1.0 - skull.a) * stripes;
}
