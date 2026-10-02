/**
 * The WebGL half of voice mode's glow (components/home/VoiceGlow): one
 * full-surface fragment shader that paints an aurora rising from the bottom
 * edge.
 *
 * Layers, bottom to top of the maths:
 *  - a HAZE whose crest is a domain-warped noise line, tallest in the middle
 *    and sinking toward the sides; the voice lifts it;
 *  - soft vertical CURTAINS drifting upward through the haze — the shimmer;
 *  - a hot CORE hugging the bottom edge that flares with each syllable
 *    (`pulse`, the fast envelope) while the haze follows the slower `level`;
 *  - while the assistant THINKS, two beams of light glide along the bottom
 *    edge in opposite directions, part and meet again in the middle — the
 *    light keeps working while nobody speaks.
 *
 * Colour is the theme's accent: deep in the haze, lifted toward white in the
 * core. On paper (light themes) the lift is smaller and the whole light
 * fainter, because there a glow reads as a tint, not a light source.
 *
 * The output is premultiplied alpha with a 1/255 dither, so the long dark
 * gradient never bands. It renders into a small backing store (the light is
 * soft, the browser's upscale costs nothing) — a few hundred thousand pixels
 * of cheap noise per frame.
 *
 * Built on raw WebGL 1 rather than three.js: one quad and one program do not
 * need a scene graph, and a WebGL 1 context exists on every GPU a WebView
 * runs on.
 */

const VERTEX = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

const FRAGMENT = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 vUv;
uniform vec2 uRes;
uniform float uTime;
uniform float uLevel;
uniform float uPulse;
uniform float uPower;
uniform float uLight;
uniform float uSpan;
uniform float uThink;
uniform float uSweep;
uniform vec3 uColor;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

// Value noise over a lattice wrapped at 289 cells: still continuous (the
// wrap happens on whole lattice points), and the hash never sees a number
// large enough to lose its precision, however long a call runs.
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = p - i;
  i = mod(i, 289.0);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(mod(i + vec2(1.0, 0.0), 289.0));
  float c = hash(mod(i + vec2(0.0, 1.0), 289.0));
  float d = hash(mod(i + vec2(1.0, 1.0), 289.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 4; i++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(1.7, 9.2);
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / max(uRes.y, 1.0);
  float x = (uv.x - 0.5) * aspect;
  float y = uv.y;
  float t = uTime;

  vec2 q = vec2(
    fbm(vec2(x * 0.9 - t * 0.11, t * 0.07)),
    fbm(vec2(x * 0.9 + 3.1 + t * 0.09, 1.7 - t * 0.05))
  );
  float crest = fbm(vec2(x * 1.1 + q.x * 1.6 - t * 0.06, q.y * 1.4 + t * 0.12));

  float dx = (uv.x - 0.5) / uSpan;
  float dome = exp(-dx * dx * 1.4);

  // Thinking: two beams mirrored about the middle. Screen-blended, not
  // summed, so where they meet the light swells instead of blowing out.
  float beamWidth = uSpan * 0.32;
  float b1 = exp(-pow((uv.x - (0.5 + uSpan * 0.95 * uSweep)) / beamWidth, 2.0));
  float b2 = exp(-pow((uv.x - (0.5 - uSpan * 0.95 * uSweep)) / beamWidth, 2.0));
  float beams = (1.0 - (1.0 - b1) * (1.0 - b2)) * uThink;
  float beam = beams * (exp(-y / 0.13) + 0.4 * exp(-y / 0.4));

  float h = (1.0 + 0.7 * beams) * (0.34 + 0.42 * uLevel) * (0.6 + 0.8 * crest) * (0.3 + 0.7 * dome);
  float body = exp(-pow(y / max(h, 0.02), 1.35) * 1.7);

  float rays = smoothstep(0.2, 0.85, fbm(vec2(x * 2.6 + q.x * 2.2, y * 0.9 - t * 0.35)));
  float shimmer = 0.75 + 0.5 * rays;

  float core = exp(-y / (0.05 + 0.1 * uPulse)) * dome;

  float a = body * shimmer * (0.3 + 0.7 * dome) + core * (0.2 + 0.6 * uPulse) + beam * 0.9;
  a *= (0.3 + 0.7 * uPower) * (0.65 + 0.55 * uLevel);
  a *= smoothstep(0.0, 0.12, uv.x) * (1.0 - smoothstep(0.88, 1.0, uv.x)) * (1.0 - smoothstep(0.45, 1.0, y));

  float lift = clamp(core * 0.8 + beam * 0.7 + rays * body * 0.35, 0.0, 1.0);
  vec3 deep = uColor * (0.75 + 0.25 * uLight);
  vec3 bright = mix(uColor, vec3(1.0), 0.55 - 0.35 * uLight);
  vec3 col = mix(deep, bright, lift);

  a = a * (0.55 - 0.2 * uLight);
  a += (hash(gl_FragCoord.xy + fract(t)) - 0.5) / 255.0;
  a = clamp(a, 0.0, 1.0);
  gl_FragColor = vec4(col * a, a);
}
`;

export interface GlowFrame {
  /** Seconds of flow — accumulated by the caller, faster while someone speaks. */
  time: number;
  /** Slow voice level 0..1: how high the light stands. */
  level: number;
  /** Fast voice envelope 0..1: how hard the core flares. */
  pulse: number;
  /** 0..1: how far the thinking beams have faded in. */
  think: number;
  /** -1..1: where the thinking beams stand (mirrored about the middle). */
  sweep: number;
  /** 0 = resting wash, 1 = call open. */
  power: number;
  /** True on a light theme. */
  light: boolean;
  /** The accent, as 0..255 channels. */
  color: readonly [number, number, number];
}

export interface GlowRenderer {
  /** Match the backing store to the element's size. Returns true when it changed. */
  resize(cssWidth: number, cssHeight: number): boolean;
  render(frame: GlowFrame): void;
  /** Delete the GPU objects. The context itself is released by the caller. */
  dispose(): void;
}

/** Backing-store pixels per CSS pixel. The light is soft; the upscale is free. */
const RESOLUTION = 0.5;
/** Cap on the backing store's width, for very wide windows. */
const MAX_WIDTH = 900;
/** Half-width of the light's brightest part, in CSS pixels. */
const SPAN_PX = 520;

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS) && !gl.isContextLost()) {
    console.warn("[voice-glow] shader failed to compile:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/**
 * Build the renderer on `canvas`, or return null where WebGL is unavailable
 * (no GPU, a blocklisted driver, the page out of contexts) — the caller then
 * paints the CSS fallback.
 *
 * `dispose()` frees the program and buffer only. The caller owns the context
 * and hands it back with `releaseWebglContext` after unmount (VoiceGlow does,
 * deferred so a StrictMode remount can reuse it) — AP-32.
 */
export function createGlowRenderer(canvas: HTMLCanvasElement): GlowRenderer | null {
  let gl: WebGLRenderingContext | null = null;
  try {
    gl = canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      preserveDrawingBuffer: false,
      powerPreference: "low-power",
    });
  } catch (err) {
    console.warn("[voice-glow] WebGL context unavailable:", err);
    return null;
  }
  if (!gl || gl.isContextLost()) return null;

  const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    if (!gl.isContextLost()) {
      console.warn("[voice-glow] shader program failed to link:", gl.getProgramInfoLog(program));
    }
    gl.deleteProgram(program);
    return null;
  }

  // One triangle that covers the whole surface.
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(program, "aPos");

  const ctx = gl;
  const u = (name: string) => ctx.getUniformLocation(program, name);
  const uRes = u("uRes");
  const uTime = u("uTime");
  const uLevel = u("uLevel");
  const uPulse = u("uPulse");
  const uPower = u("uPower");
  const uLight = u("uLight");
  const uSpan = u("uSpan");
  const uColor = u("uColor");
  const uThink = u("uThink");
  const uSweep = u("uSweep");

  let cssWidth = 1;
  let cssHeight = 1;

  return {
    resize(width, height) {
      const scale = Math.min(RESOLUTION, MAX_WIDTH / Math.max(width, 1));
      const w = Math.max(1, Math.round(width * scale));
      const h = Math.max(1, Math.round(height * scale));
      cssWidth = Math.max(width, 1);
      cssHeight = Math.max(height, 1);
      if (canvas.width === w && canvas.height === h) return false;
      canvas.width = w;
      canvas.height = h;
      return true;
    },
    render(frame) {
      if (!gl || gl.isContextLost()) return;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
      gl.uniform2f(uRes, cssWidth, cssHeight);
      gl.uniform1f(uTime, frame.time);
      gl.uniform1f(uLevel, frame.level);
      gl.uniform1f(uPulse, frame.pulse);
      gl.uniform1f(uPower, frame.power);
      gl.uniform1f(uThink, frame.think);
      gl.uniform1f(uSweep, frame.sweep);
      gl.uniform1f(uLight, frame.light ? 1 : 0);
      gl.uniform1f(uSpan, Math.min(0.45, SPAN_PX / cssWidth));
      gl.uniform3f(uColor, frame.color[0] / 255, frame.color[1] / 255, frame.color[2] / 255);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
    dispose() {
      if (!gl || gl.isContextLost()) return;
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
    },
  };
}
