#version 300 es
// One instanced line strip per ring: point j = gl_VertexID (0..M, M closes it), ring age index
// i = gl_InstanceID (0 = newest). Position comes from the history texture and uniforms only.
precision highp float;
precision highp sampler2D;

uniform sampler2D uHist;       // M x N waveform history, circular in rows
uniform int uHead, uN, uM;
uniform float uFrac, uAmp, uRadius, uPulse;
uniform mat4 uProj, uView;
out float vAge, vDepth, vSample, vEmpty;

void main() {
  int i = gl_InstanceID;
  int j = gl_VertexID % uM;
  int row = (uHead - i + uN) % uN;
  float s = texelFetch(uHist, ivec2(j, row), 0).r;
  vEmpty = step(1e5, s);                                   // rows not yet written stay invisible
  s *= 1.0 - vEmpty;
  float age = (float(i) + uFrac) / float(uN - 1);         // 0 north pole .. 1 south pole
  float theta = clamp(age, 0.0, 1.0) * 3.14159265;
  float phi = float(gl_VertexID) / float(uM) * 6.28318531;
  float r = (uRadius + uAmp * s) * (1.0 + uPulse * 0.06); // waveform along the normal; bass swells it all, gently
  vec3 p = r * vec3(sin(theta) * cos(phi), cos(theta), sin(theta) * sin(phi));
  vec4 v = uView * vec4(p, 1.0);
  vAge = age;
  vDepth = -v.z;
  vSample = s;
  gl_Position = uProj * v;
}
