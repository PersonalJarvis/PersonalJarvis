/** One geometry pass, crisp nearest upscale and optional depth silhouettes. */
import { DepthTexture, NearestFilter, ShaderMaterial, UnsignedIntType, Vector2, WebGLRenderTarget,
  type Camera, type Scene, type WebGLRenderer } from "three";
import { FullScreenQuad, Pass } from "three/examples/jsm/postprocessing/Pass.js";

export class PixelScenePass extends Pass {
  private target = new WebGLRenderTarget(1, 1, { minFilter: NearestFilter, magFilter: NearestFilter });
  private material = new ShaderMaterial({
    uniforms: { image: { value: null }, depth: { value: null }, texel: { value: new Vector2(1, 1) } },
    vertexShader: "varying vec2 uv0; void main(){uv0=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}",
    fragmentShader: `uniform sampler2D image; uniform sampler2D depth; uniform vec2 texel; varying vec2 uv0;
      void main(){vec4 c=texture2D(image,uv0); float d=texture2D(depth,uv0).r;
      float edge=max(abs(d-texture2D(depth,uv0+vec2(texel.x,0.)).r),abs(d-texture2D(depth,uv0+vec2(0.,texel.y)).r));
      c.rgb*=1.-.10*step(.0012,edge); gl_FragColor=c;}`,
    depthTest: false, depthWrite: false,
  });
  private quad = new FullScreenQuad(this.material);
  constructor(private scene: Scene, private camera: Camera, private pixelSize = 2) {
    super(); this.target.depthTexture = new DepthTexture(1, 1, UnsignedIntType);
    this.material.uniforms.image.value = this.target.texture;
    this.material.uniforms.depth.value = this.target.depthTexture;
  }
  setSize(width: number, height: number): void {
    const w = Math.max(1, Math.floor(width / this.pixelSize)), h = Math.max(1, Math.floor(height / this.pixelSize));
    this.target.setSize(w, h); this.material.uniforms.texel.value.set(1 / w, 1 / h);
  }
  render(renderer: WebGLRenderer, writeBuffer: WebGLRenderTarget): void {
    renderer.setRenderTarget(this.target); renderer.clear(); renderer.render(this.scene, this.camera);
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer); this.quad.render(renderer);
  }
  dispose(): void { this.target.dispose(); this.material.dispose(); this.quad.dispose(); }
}
