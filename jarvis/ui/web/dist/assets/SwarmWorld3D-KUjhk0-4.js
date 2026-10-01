var ke=Object.defineProperty;var Ne=(n,e,t)=>e in n?ke(n,e,{enumerable:!0,configurable:!0,writable:!0,value:t}):n[e]=t;var ae=(n,e,t)=>Ne(n,typeof e!="symbol"?e+"":e,t);import{r as g,j as a}from"./index-TsHY2Z4c.js";import{v as _e,a as Me,l as Fe,m as je,n as We,u as Le,j as Ge,r as Ie,b as Ae,C as Ve}from"./Gltf-dYevWEIp.js";import{aE as $e,aF as ce,aG as ie,ar as F,aH as qe,B as $,ay as Ce,V as B,aI as Xe,aJ as le,aK as ue,u as ze,M as Ue,aL as W,aM as Je,X as Ke,al as Ye,C as I,ak as Qe,b as Ze,aN as de,af as K,aO as et,aP as Be,aQ as tt,i as nt,h as it,N as fe,j as Oe}from"./three.module-KT3vkVrh.js";import{u as st}from"./useCanvasAwake-DHTxLSAX.js";import{c as ot,m as rt}from"./memory-house-BCCIIQUb.js";import{u as se,e as V,r as at,w as ct,a as lt,m as ut,s as dt,D as me,t as ft}from"./UltraSwarmView-BcKoP5MD.js";import{H as De}from"./Html-CDIHE1PN.js";import{u as mt}from"./use-reduced-motion-CQSrHWmv.js";import{_ as pe}from"./extends-CF3RwP-h.js";import{O as pt}from"./OrbitControls-DoGl-Xdr.js";import"./select-DI_pkUBN.js";import"./combobox-BZ0aVNDr.js";import"./index-DqTMknzg.js";const Pe=_e>=125?"uv1":"uv2",he=new $,q=new B;class oe extends $e{constructor(){super(),this.isLineSegmentsGeometry=!0,this.type="LineSegmentsGeometry";const e=[-1,2,0,1,2,0,-1,1,0,1,1,0,-1,0,0,1,0,0,-1,-1,0,1,-1,0],t=[-1,2,1,2,-1,1,1,1,-1,-1,1,-1,-1,-2,1,-2],o=[0,2,1,2,3,1,2,4,3,4,5,3,4,6,5,6,7,5];this.setIndex(o),this.setAttribute("position",new ce(e,3)),this.setAttribute("uv",new ce(t,2))}applyMatrix4(e){const t=this.attributes.instanceStart,o=this.attributes.instanceEnd;return t!==void 0&&(t.applyMatrix4(e),o.applyMatrix4(e),t.needsUpdate=!0),this.boundingBox!==null&&this.computeBoundingBox(),this.boundingSphere!==null&&this.computeBoundingSphere(),this}setPositions(e){let t;e instanceof Float32Array?t=e:Array.isArray(e)&&(t=new Float32Array(e));const o=new ie(t,6,1);return this.setAttribute("instanceStart",new F(o,3,0)),this.setAttribute("instanceEnd",new F(o,3,3)),this.computeBoundingBox(),this.computeBoundingSphere(),this}setColors(e,t=3){let o;e instanceof Float32Array?o=e:Array.isArray(e)&&(o=new Float32Array(e));const s=new ie(o,t*2,1);return this.setAttribute("instanceColorStart",new F(s,t,0)),this.setAttribute("instanceColorEnd",new F(s,t,t)),this}fromWireframeGeometry(e){return this.setPositions(e.attributes.position.array),this}fromEdgesGeometry(e){return this.setPositions(e.attributes.position.array),this}fromMesh(e){return this.fromWireframeGeometry(new qe(e.geometry)),this}fromLineSegments(e){const t=e.geometry;return this.setPositions(t.attributes.position.array),this}computeBoundingBox(){this.boundingBox===null&&(this.boundingBox=new $);const e=this.attributes.instanceStart,t=this.attributes.instanceEnd;e!==void 0&&t!==void 0&&(this.boundingBox.setFromBufferAttribute(e),he.setFromBufferAttribute(t),this.boundingBox.union(he))}computeBoundingSphere(){this.boundingSphere===null&&(this.boundingSphere=new Ce),this.boundingBox===null&&this.computeBoundingBox();const e=this.attributes.instanceStart,t=this.attributes.instanceEnd;if(e!==void 0&&t!==void 0){const o=this.boundingSphere.center;this.boundingBox.getCenter(o);let s=0;for(let i=0,r=e.count;i<r;i++)q.fromBufferAttribute(e,i),s=Math.max(s,o.distanceToSquared(q)),q.fromBufferAttribute(t,i),s=Math.max(s,o.distanceToSquared(q));this.boundingSphere.radius=Math.sqrt(s),isNaN(this.boundingSphere.radius)&&console.error("THREE.LineSegmentsGeometry.computeBoundingSphere(): Computed radius is NaN. The instanced position data is likely to have NaN values.",this)}}toJSON(){}applyMatrix(e){return console.warn("THREE.LineSegmentsGeometry: applyMatrix() has been renamed to applyMatrix4()."),this.applyMatrix4(e)}}class Te extends oe{constructor(){super(),this.isLineGeometry=!0,this.type="LineGeometry"}setPositions(e){const t=e.length-3,o=new Float32Array(2*t);for(let s=0;s<t;s+=3)o[2*s]=e[s],o[2*s+1]=e[s+1],o[2*s+2]=e[s+2],o[2*s+3]=e[s+3],o[2*s+4]=e[s+4],o[2*s+5]=e[s+5];return super.setPositions(o),this}setColors(e,t=3){const o=e.length-t,s=new Float32Array(2*o);if(t===3)for(let i=0;i<o;i+=t)s[2*i]=e[i],s[2*i+1]=e[i+1],s[2*i+2]=e[i+2],s[2*i+3]=e[i+3],s[2*i+4]=e[i+4],s[2*i+5]=e[i+5];else for(let i=0;i<o;i+=t)s[2*i]=e[i],s[2*i+1]=e[i+1],s[2*i+2]=e[i+2],s[2*i+3]=e[i+3],s[2*i+4]=e[i+4],s[2*i+5]=e[i+5],s[2*i+6]=e[i+6],s[2*i+7]=e[i+7];return super.setColors(s,t),this}fromLine(e){const t=e.geometry;return this.setPositions(t.attributes.position.array),this}}class re extends Xe{constructor(e){super({type:"LineMaterial",uniforms:le.clone(le.merge([ue.common,ue.fog,{worldUnits:{value:1},linewidth:{value:1},resolution:{value:new ze(1,1)},dashOffset:{value:0},dashScale:{value:1},dashSize:{value:1},gapSize:{value:1}}])),vertexShader:`
				#include <common>
				#include <fog_pars_vertex>
				#include <logdepthbuf_pars_vertex>
				#include <clipping_planes_pars_vertex>

				uniform float linewidth;
				uniform vec2 resolution;

				attribute vec3 instanceStart;
				attribute vec3 instanceEnd;

				#ifdef USE_COLOR
					#ifdef USE_LINE_COLOR_ALPHA
						varying vec4 vLineColor;
						attribute vec4 instanceColorStart;
						attribute vec4 instanceColorEnd;
					#else
						varying vec3 vLineColor;
						attribute vec3 instanceColorStart;
						attribute vec3 instanceColorEnd;
					#endif
				#endif

				#ifdef WORLD_UNITS

					varying vec4 worldPos;
					varying vec3 worldStart;
					varying vec3 worldEnd;

					#ifdef USE_DASH

						varying vec2 vUv;

					#endif

				#else

					varying vec2 vUv;

				#endif

				#ifdef USE_DASH

					uniform float dashScale;
					attribute float instanceDistanceStart;
					attribute float instanceDistanceEnd;
					varying float vLineDistance;

				#endif

				void trimSegment( const in vec4 start, inout vec4 end ) {

					// trim end segment so it terminates between the camera plane and the near plane

					// conservative estimate of the near plane
					float a = projectionMatrix[ 2 ][ 2 ]; // 3nd entry in 3th column
					float b = projectionMatrix[ 3 ][ 2 ]; // 3nd entry in 4th column
					float nearEstimate = - 0.5 * b / a;

					float alpha = ( nearEstimate - start.z ) / ( end.z - start.z );

					end.xyz = mix( start.xyz, end.xyz, alpha );

				}

				void main() {

					#ifdef USE_COLOR

						vLineColor = ( position.y < 0.5 ) ? instanceColorStart : instanceColorEnd;

					#endif

					#ifdef USE_DASH

						vLineDistance = ( position.y < 0.5 ) ? dashScale * instanceDistanceStart : dashScale * instanceDistanceEnd;
						vUv = uv;

					#endif

					float aspect = resolution.x / resolution.y;

					// camera space
					vec4 start = modelViewMatrix * vec4( instanceStart, 1.0 );
					vec4 end = modelViewMatrix * vec4( instanceEnd, 1.0 );

					#ifdef WORLD_UNITS

						worldStart = start.xyz;
						worldEnd = end.xyz;

					#else

						vUv = uv;

					#endif

					// special case for perspective projection, and segments that terminate either in, or behind, the camera plane
					// clearly the gpu firmware has a way of addressing this issue when projecting into ndc space
					// but we need to perform ndc-space calculations in the shader, so we must address this issue directly
					// perhaps there is a more elegant solution -- WestLangley

					bool perspective = ( projectionMatrix[ 2 ][ 3 ] == - 1.0 ); // 4th entry in the 3rd column

					if ( perspective ) {

						if ( start.z < 0.0 && end.z >= 0.0 ) {

							trimSegment( start, end );

						} else if ( end.z < 0.0 && start.z >= 0.0 ) {

							trimSegment( end, start );

						}

					}

					// clip space
					vec4 clipStart = projectionMatrix * start;
					vec4 clipEnd = projectionMatrix * end;

					// ndc space
					vec3 ndcStart = clipStart.xyz / clipStart.w;
					vec3 ndcEnd = clipEnd.xyz / clipEnd.w;

					// direction
					vec2 dir = ndcEnd.xy - ndcStart.xy;

					// account for clip-space aspect ratio
					dir.x *= aspect;
					dir = normalize( dir );

					#ifdef WORLD_UNITS

						// get the offset direction as perpendicular to the view vector
						vec3 worldDir = normalize( end.xyz - start.xyz );
						vec3 offset;
						if ( position.y < 0.5 ) {

							offset = normalize( cross( start.xyz, worldDir ) );

						} else {

							offset = normalize( cross( end.xyz, worldDir ) );

						}

						// sign flip
						if ( position.x < 0.0 ) offset *= - 1.0;

						float forwardOffset = dot( worldDir, vec3( 0.0, 0.0, 1.0 ) );

						// don't extend the line if we're rendering dashes because we
						// won't be rendering the endcaps
						#ifndef USE_DASH

							// extend the line bounds to encompass  endcaps
							start.xyz += - worldDir * linewidth * 0.5;
							end.xyz += worldDir * linewidth * 0.5;

							// shift the position of the quad so it hugs the forward edge of the line
							offset.xy -= dir * forwardOffset;
							offset.z += 0.5;

						#endif

						// endcaps
						if ( position.y > 1.0 || position.y < 0.0 ) {

							offset.xy += dir * 2.0 * forwardOffset;

						}

						// adjust for linewidth
						offset *= linewidth * 0.5;

						// set the world position
						worldPos = ( position.y < 0.5 ) ? start : end;
						worldPos.xyz += offset;

						// project the worldpos
						vec4 clip = projectionMatrix * worldPos;

						// shift the depth of the projected points so the line
						// segments overlap neatly
						vec3 clipPose = ( position.y < 0.5 ) ? ndcStart : ndcEnd;
						clip.z = clipPose.z * clip.w;

					#else

						vec2 offset = vec2( dir.y, - dir.x );
						// undo aspect ratio adjustment
						dir.x /= aspect;
						offset.x /= aspect;

						// sign flip
						if ( position.x < 0.0 ) offset *= - 1.0;

						// endcaps
						if ( position.y < 0.0 ) {

							offset += - dir;

						} else if ( position.y > 1.0 ) {

							offset += dir;

						}

						// adjust for linewidth
						offset *= linewidth;

						// adjust for clip-space to screen-space conversion // maybe resolution should be based on viewport ...
						offset /= resolution.y;

						// select end
						vec4 clip = ( position.y < 0.5 ) ? clipStart : clipEnd;

						// back to clip space
						offset *= clip.w;

						clip.xy += offset;

					#endif

					gl_Position = clip;

					vec4 mvPosition = ( position.y < 0.5 ) ? start : end; // this is an approximation

					#include <logdepthbuf_vertex>
					#include <clipping_planes_vertex>
					#include <fog_vertex>

				}
			`,fragmentShader:`
				uniform vec3 diffuse;
				uniform float opacity;
				uniform float linewidth;

				#ifdef USE_DASH

					uniform float dashOffset;
					uniform float dashSize;
					uniform float gapSize;

				#endif

				varying float vLineDistance;

				#ifdef WORLD_UNITS

					varying vec4 worldPos;
					varying vec3 worldStart;
					varying vec3 worldEnd;

					#ifdef USE_DASH

						varying vec2 vUv;

					#endif

				#else

					varying vec2 vUv;

				#endif

				#include <common>
				#include <fog_pars_fragment>
				#include <logdepthbuf_pars_fragment>
				#include <clipping_planes_pars_fragment>

				#ifdef USE_COLOR
					#ifdef USE_LINE_COLOR_ALPHA
						varying vec4 vLineColor;
					#else
						varying vec3 vLineColor;
					#endif
				#endif

				vec2 closestLineToLine(vec3 p1, vec3 p2, vec3 p3, vec3 p4) {

					float mua;
					float mub;

					vec3 p13 = p1 - p3;
					vec3 p43 = p4 - p3;

					vec3 p21 = p2 - p1;

					float d1343 = dot( p13, p43 );
					float d4321 = dot( p43, p21 );
					float d1321 = dot( p13, p21 );
					float d4343 = dot( p43, p43 );
					float d2121 = dot( p21, p21 );

					float denom = d2121 * d4343 - d4321 * d4321;

					float numer = d1343 * d4321 - d1321 * d4343;

					mua = numer / denom;
					mua = clamp( mua, 0.0, 1.0 );
					mub = ( d1343 + d4321 * ( mua ) ) / d4343;
					mub = clamp( mub, 0.0, 1.0 );

					return vec2( mua, mub );

				}

				void main() {

					#include <clipping_planes_fragment>

					#ifdef USE_DASH

						if ( vUv.y < - 1.0 || vUv.y > 1.0 ) discard; // discard endcaps

						if ( mod( vLineDistance + dashOffset, dashSize + gapSize ) > dashSize ) discard; // todo - FIX

					#endif

					float alpha = opacity;

					#ifdef WORLD_UNITS

						// Find the closest points on the view ray and the line segment
						vec3 rayEnd = normalize( worldPos.xyz ) * 1e5;
						vec3 lineDir = worldEnd - worldStart;
						vec2 params = closestLineToLine( worldStart, worldEnd, vec3( 0.0, 0.0, 0.0 ), rayEnd );

						vec3 p1 = worldStart + lineDir * params.x;
						vec3 p2 = rayEnd * params.y;
						vec3 delta = p1 - p2;
						float len = length( delta );
						float norm = len / linewidth;

						#ifndef USE_DASH

							#ifdef USE_ALPHA_TO_COVERAGE

								float dnorm = fwidth( norm );
								alpha = 1.0 - smoothstep( 0.5 - dnorm, 0.5 + dnorm, norm );

							#else

								if ( norm > 0.5 ) {

									discard;

								}

							#endif

						#endif

					#else

						#ifdef USE_ALPHA_TO_COVERAGE

							// artifacts appear on some hardware if a derivative is taken within a conditional
							float a = vUv.x;
							float b = ( vUv.y > 0.0 ) ? vUv.y - 1.0 : vUv.y + 1.0;
							float len2 = a * a + b * b;
							float dlen = fwidth( len2 );

							if ( abs( vUv.y ) > 1.0 ) {

								alpha = 1.0 - smoothstep( 1.0 - dlen, 1.0 + dlen, len2 );

							}

						#else

							if ( abs( vUv.y ) > 1.0 ) {

								float a = vUv.x;
								float b = ( vUv.y > 0.0 ) ? vUv.y - 1.0 : vUv.y + 1.0;
								float len2 = a * a + b * b;

								if ( len2 > 1.0 ) discard;

							}

						#endif

					#endif

					vec4 diffuseColor = vec4( diffuse, alpha );
					#ifdef USE_COLOR
						#ifdef USE_LINE_COLOR_ALPHA
							diffuseColor *= vLineColor;
						#else
							diffuseColor.rgb *= vLineColor;
						#endif
					#endif

					#include <logdepthbuf_fragment>

					gl_FragColor = diffuseColor;

					#include <tonemapping_fragment>
					#include <${_e>=154?"colorspace_fragment":"encodings_fragment"}>
					#include <fog_fragment>
					#include <premultiplied_alpha_fragment>

				}
			`,clipping:!0}),this.isLineMaterial=!0,this.onBeforeCompile=function(){this.transparent?this.defines.USE_LINE_COLOR_ALPHA="1":delete this.defines.USE_LINE_COLOR_ALPHA},Object.defineProperties(this,{color:{enumerable:!0,get:function(){return this.uniforms.diffuse.value},set:function(t){this.uniforms.diffuse.value=t}},worldUnits:{enumerable:!0,get:function(){return"WORLD_UNITS"in this.defines},set:function(t){t===!0?this.defines.WORLD_UNITS="":delete this.defines.WORLD_UNITS}},linewidth:{enumerable:!0,get:function(){return this.uniforms.linewidth.value},set:function(t){this.uniforms.linewidth.value=t}},dashed:{enumerable:!0,get:function(){return"USE_DASH"in this.defines},set(t){!!t!="USE_DASH"in this.defines&&(this.needsUpdate=!0),t===!0?this.defines.USE_DASH="":delete this.defines.USE_DASH}},dashScale:{enumerable:!0,get:function(){return this.uniforms.dashScale.value},set:function(t){this.uniforms.dashScale.value=t}},dashSize:{enumerable:!0,get:function(){return this.uniforms.dashSize.value},set:function(t){this.uniforms.dashSize.value=t}},dashOffset:{enumerable:!0,get:function(){return this.uniforms.dashOffset.value},set:function(t){this.uniforms.dashOffset.value=t}},gapSize:{enumerable:!0,get:function(){return this.uniforms.gapSize.value},set:function(t){this.uniforms.gapSize.value=t}},opacity:{enumerable:!0,get:function(){return this.uniforms.opacity.value},set:function(t){this.uniforms.opacity.value=t}},resolution:{enumerable:!0,get:function(){return this.uniforms.resolution.value},set:function(t){this.uniforms.resolution.value.copy(t)}},alphaToCoverage:{enumerable:!0,get:function(){return"USE_ALPHA_TO_COVERAGE"in this.defines},set:function(t){!!t!="USE_ALPHA_TO_COVERAGE"in this.defines&&(this.needsUpdate=!0),t===!0?(this.defines.USE_ALPHA_TO_COVERAGE="",this.extensions.derivatives=!0):(delete this.defines.USE_ALPHA_TO_COVERAGE,this.extensions.derivatives=!1)}}}),this.setValues(e)}}const Y=new W,ge=new B,ve=new B,A=new W,C=new W,O=new W,Q=new B,Z=new Ke,z=new Je,xe=new B,X=new $,J=new Ce,D=new W;let P,k;function ye(n,e,t){return D.set(0,0,-e,1).applyMatrix4(n.projectionMatrix),D.multiplyScalar(1/D.w),D.x=k/t.width,D.y=k/t.height,D.applyMatrix4(n.projectionMatrixInverse),D.multiplyScalar(1/D.w),Math.abs(Math.max(D.x,D.y))}function ht(n,e){const t=n.matrixWorld,o=n.geometry,s=o.attributes.instanceStart,i=o.attributes.instanceEnd,r=Math.min(o.instanceCount,s.count);for(let c=0,f=r;c<f;c++){z.start.fromBufferAttribute(s,c),z.end.fromBufferAttribute(i,c),z.applyMatrix4(t);const y=new B,h=new B;P.distanceSqToSegment(z.start,z.end,h,y),h.distanceTo(y)<k*.5&&e.push({point:h,pointOnLine:y,distance:P.origin.distanceTo(h),object:n,face:null,faceIndex:c,uv:null,[Pe]:null})}}function gt(n,e,t){const o=e.projectionMatrix,i=n.material.resolution,r=n.matrixWorld,c=n.geometry,f=c.attributes.instanceStart,y=c.attributes.instanceEnd,h=Math.min(c.instanceCount,f.count),p=-e.near;P.at(1,O),O.w=1,O.applyMatrix4(e.matrixWorldInverse),O.applyMatrix4(o),O.multiplyScalar(1/O.w),O.x*=i.x/2,O.y*=i.y/2,O.z=0,Q.copy(O),Z.multiplyMatrices(e.matrixWorldInverse,r);for(let m=0,d=h;m<d;m++){if(A.fromBufferAttribute(f,m),C.fromBufferAttribute(y,m),A.w=1,C.w=1,A.applyMatrix4(Z),C.applyMatrix4(Z),A.z>p&&C.z>p)continue;if(A.z>p){const u=A.z-C.z,x=(A.z-p)/u;A.lerp(C,x)}else if(C.z>p){const u=C.z-A.z,x=(C.z-p)/u;C.lerp(A,x)}A.applyMatrix4(o),C.applyMatrix4(o),A.multiplyScalar(1/A.w),C.multiplyScalar(1/C.w),A.x*=i.x/2,A.y*=i.y/2,C.x*=i.x/2,C.y*=i.y/2,z.start.copy(A),z.start.z=0,z.end.copy(C),z.end.z=0;const _=z.closestPointToPointParameter(Q,!0);z.at(_,xe);const b=Ye.lerp(A.z,C.z,_),S=b>=-1&&b<=1,M=Q.distanceTo(xe)<k*.5;if(S&&M){z.start.fromBufferAttribute(f,m),z.end.fromBufferAttribute(y,m),z.start.applyMatrix4(r),z.end.applyMatrix4(r);const u=new B,x=new B;P.distanceSqToSegment(z.start,z.end,x,u),t.push({point:x,pointOnLine:u,distance:P.origin.distanceTo(x),object:n,face:null,faceIndex:m,uv:null,[Pe]:null})}}}class Re extends Ue{constructor(e=new oe,t=new re({color:Math.random()*16777215})){super(e,t),this.isLineSegments2=!0,this.type="LineSegments2"}computeLineDistances(){const e=this.geometry,t=e.attributes.instanceStart,o=e.attributes.instanceEnd,s=new Float32Array(2*t.count);for(let r=0,c=0,f=t.count;r<f;r++,c+=2)ge.fromBufferAttribute(t,r),ve.fromBufferAttribute(o,r),s[c]=c===0?0:s[c-1],s[c+1]=s[c]+ge.distanceTo(ve);const i=new ie(s,2,1);return e.setAttribute("instanceDistanceStart",new F(i,1,0)),e.setAttribute("instanceDistanceEnd",new F(i,1,1)),this}raycast(e,t){const o=this.material.worldUnits,s=e.camera;s===null&&!o&&console.error('LineSegments2: "Raycaster.camera" needs to be set in order to raycast against LineSegments2 while worldUnits is set to false.');const i=e.params.Line2!==void 0&&e.params.Line2.threshold||0;P=e.ray;const r=this.matrixWorld,c=this.geometry,f=this.material;k=f.linewidth+i,c.boundingSphere===null&&c.computeBoundingSphere(),J.copy(c.boundingSphere).applyMatrix4(r);let y;if(o)y=k*.5;else{const p=Math.max(s.near,J.distanceToPoint(P.origin));y=ye(s,p,f.resolution)}if(J.radius+=y,P.intersectsSphere(J)===!1)return;c.boundingBox===null&&c.computeBoundingBox(),X.copy(c.boundingBox).applyMatrix4(r);let h;if(o)h=k*.5;else{const p=Math.max(s.near,X.distanceToPoint(P.origin));h=ye(s,p,f.resolution)}X.expandByScalar(h),P.intersectsBox(X)!==!1&&(o?ht(this,t):gt(this,s,t))}onBeforeRender(e){const t=this.material.uniforms;t&&t.resolution&&(e.getViewport(Y),this.material.uniforms.resolution.value.set(Y.z,Y.w))}}class vt extends Re{constructor(e=new Te,t=new re({color:Math.random()*16777215})){super(e,t),this.isLine2=!0,this.type="Line2"}}const xt=g.forwardRef(function({points:e,color:t=16777215,vertexColors:o,linewidth:s,lineWidth:i,segments:r,dashed:c,...f},y){var h,p;const m=Me(S=>S.size),d=g.useMemo(()=>r?new Re:new vt,[r]),[w]=g.useState(()=>new re),_=(o==null||(h=o[0])==null?void 0:h.length)===4?4:3,b=g.useMemo(()=>{const S=r?new oe:new Te,M=e.map(u=>{const x=Array.isArray(u);return u instanceof B||u instanceof W?[u.x,u.y,u.z]:u instanceof ze?[u.x,u.y,0]:x&&u.length===3?[u[0],u[1],u[2]]:x&&u.length===2?[u[0],u[1],0]:u});if(S.setPositions(M.flat()),o){t=16777215;const u=o.map(x=>x instanceof I?x.toArray():x);S.setColors(u.flat(),_)}return S},[e,r,o,_]);return g.useLayoutEffect(()=>{d.computeLineDistances()},[e,d]),g.useLayoutEffect(()=>{c?w.defines.USE_DASH="":delete w.defines.USE_DASH,w.needsUpdate=!0},[c,w]),g.useEffect(()=>()=>{b.dispose(),w.dispose()},[b]),g.createElement("primitive",pe({object:d,ref:y},f),g.createElement("primitive",{object:b,attach:"geometry"}),g.createElement("primitive",pe({object:w,attach:"material",color:t,vertexColors:!!o,resolution:[m.width,m.height],linewidth:(p=s??i)!==null&&p!==void 0?p:1,dashed:c,transparent:_===4},f)))});function yt(n,e){const[t,o]=g.useState(0),s=g.useRef(0);return g.useEffect(()=>{const i=n.current;if(!i)return;let r=null,c=!1,f=!1,y=0,h=0,p;const m=()=>{!c&&!f&&(f=!0,o(S=>S+1))},d=S=>{if(S.preventDefault(),!c){if(++s.current>2){e();return}p=setTimeout(m,250)}},w=()=>{clearTimeout(p),m()},_=()=>{if(r=i.querySelector("canvas"),!r){++h<120&&(y=requestAnimationFrame(_));return}r.addEventListener("webglcontextlost",d),r.addEventListener("webglcontextrestored",w)};_();const b=setTimeout(()=>{s.current=0},6e4);return()=>{var S;if(c=!0,cancelAnimationFrame(y),clearTimeout(p),clearTimeout(b),!!r){r.removeEventListener("webglcontextlost",d),r.removeEventListener("webglcontextrestored",w);try{const M=r.getContext("webgl2")??r.getContext("webgl");(S=M==null?void 0:M.getExtension("WEBGL_lose_context"))==null||S.loseContext()}catch{}}}},[t,n,e]),t}const G={size:128,cellWidth:8,cellHeight:16};function we(n){const e=n.lastIndexOf("-");return e<0?"":n.slice(e+1)}const wt="sheet";function bt(n){var e,t;return((t=(e=n.parser.json.asset)==null?void 0:e.extras)==null?void 0:t.jarvis_figure)??null}function St(n){var e,t;return((t=(e=n.parser.json.asset)==null?void 0:e.extras)==null?void 0:t.jarvis_part)??null}function Et(n){n.magFilter=fe,n.minFilter=fe,n.generateMipmaps=!1,n.colorSpace=Oe,n.needsUpdate=!0}function _t(n,e){const t=n.image,o=document.createElement("canvas");o.width=t.width||G.size,o.height=t.height||G.size;const s=o.getContext("2d");s&&(s.imageSmoothingEnabled=!1,s.drawImage(t,0,0),Fe.forEach((r,c)=>{s.fillStyle=e[r],s.fillRect(c*G.cellWidth,0,G.cellWidth,G.cellHeight)}));const i=new nt(o);return i.flipY=!1,Et(i),i}function be(n,e,t){e.side=t?it:n.side,e.transparent=n.transparent,e.opacity=n.opacity,e.alphaTest=n.alphaTest,e.depthWrite=n.depthWrite}function ee(n){return Array.isArray(n.material)?n.material[0]:n.material}function Mt(n,e,t,o=[],s=null){const i=bt(n);if(!i)return null;const r=new Qe;r.name="figure";const c=ot(n.scene),f=i.height_m>0?t/i.height_m:1;c.scale.setScalar(f),r.add(c);const y=[];let h=null;const p=[],m=[];c.traverse(v=>{if(v.name==="FWD"&&(v.visible=!1),!(v instanceof Ue))return;const E=ee(v),l=E.map??null;l&&!h&&(h=_t(l,e));const j=we(E.name)==="marks"?new Ze({map:h??l,color:16777215}):new de({map:h??l,color:16777215});j.name=E.name,be(E,j,!1),v.material=j,v.frustumCulled=!1,v.castShadow=!1,v.receiveShadow=!1,y.push(j),m.push(v),v instanceof K&&p.push(v)}),h&&y.push(h);const d=p[0]??null,w=new Set,_=[];for(const v of o){const E=St(v);if(!(!d||!E||E.archetype!==i.archetype)){v.scene.traverse(l=>{var N;if(!(l instanceof K))return;const j=ee(l),T=h??j.map??void 0,H=new de({map:T,color:16777215});H.name=`part-${E.slot}`,be(j,H,E.two_sided===!0);const R=new K(l.geometry,H);R.name=`part:${E.slot}`,R.frustumCulled=!1,R.bind(d.skeleton,d.bindMatrix),(N=d.parent)==null||N.add(R),y.push(H),_.push(R)});for(const l of E.hides??[])w.add(l)}}for(const v of m){const E=we(ee(v).name??"");E&&E!==wt&&w.has(E)&&(v.visible=!1)}const b=new $;for(const v of[...m,..._]){if(!v.visible)continue;v.geometry.computeBoundingBox();const E=v.geometry.boundingBox;E&&b.union(E)}const S=b.isEmpty()?t:Math.max(t,b.max.y*f),M=b.isEmpty()?null:b.getSize(new B),u=M?Math.max(S,M.x*f,M.z*f):S,x=new et(c),U={},L=n.animations.length>0?n.animations:(s==null?void 0:s.animations)??[];for(const v of L){const E=x.clipAction(v,c),l=i.clips[v.name];E.loop=l&&!l.loop?Be:tt,E.clampWhenFinished=!0,U[v.name]=E}return{root:r,mixer:x,actions:U,extras:i,scale:f,renderedHeightM:S,renderedSpanM:u,dispose(){x.stopAllAction(),x.uncacheRoot(c);for(const v of y)v.dispose();r.clear()}}}function He(n,e,t="idle",o=.2){const s=n.actions[e]??n.actions[t];if(!s)return null;for(const[i,r]of Object.entries(n.actions))r!==s&&r.isRunning()&&r.fadeOut(o);if(s.reset().fadeIn(o).play(),s.loop===Be){const i=n.mixer,r=c=>{c.action===s&&(i.removeEventListener("finished",r),He(n,t,t,o))};i.addEventListener("finished",r)}return s}function jt(n){const e=je(n),t=We(n),o=(e==null?void 0:e.clipsUrl)??null,s=[(e==null?void 0:e.url)??"",...o?[o]:[],...t.map(c=>c.url)].filter(Boolean),i=Le(s),r=o?2:1;return{base:i[0],clips:o?i[1]:null,parts:t.map((c,f)=>({gltf:i[f+r],part:c.part})),defaultHeightM:(e==null?void 0:e.defaultHeightM)??1.75}}const Se=.7,te=1.4;function Lt(n){return je(n.recipe)?a.jsx(g.Suspense,{fallback:null,children:a.jsx(At,{...n})}):null}function At({recipe:n,drive:e,paused:t,heightM:o,onReady:s}){const i=jt(n),r=o??n.heightM??i.defaultHeightM,c=g.useRef(null),f=g.useRef(null),y=g.useRef({clip:"",timeScale:1}),h=Ge(n);return g.useEffect(()=>{const p=Ie(n),m=Mt(i.base,p,r,i.parts.map(w=>w.gltf),i.clips),d=c.current;return f.current=m,y.current={clip:"",timeScale:1},m&&d&&d.add(m.root),m&&(s==null||s(m)),()=>{m&&d&&d.remove(m.root),m==null||m.dispose(),f.current=null}},[i.base,h,r]),Ae((p,m)=>{var M;const d=f.current;if(!d)return;const w=e.current;let _=w.mode,b=1;if(w.mode==="walk"){const u=d.extras.clips.walk,x=d.extras.clips.run,U=((u==null?void 0:u.stride_m)??0)>0?(u.stride_m??0)*d.scale/u.duration:0,L=U>0?w.speed/U:1;if(x&&x.stride_m&&L>te){const v=x.stride_m*d.scale/x.duration;_="run",b=Ee(w.speed/v,Se,te)}else b=Ee(L,Se,te);w.speed<=.05&&(_="idle")}d.actions[_]||(_="idle");const S=y.current;S.clip!==_&&(He(d,_,"idle"),S.clip=_),Math.abs(S.timeScale-b)>.01&&((M=d.actions[_])==null||M.setEffectiveTimeScale(b),S.timeScale=b),t||d.mixer.update(Math.min(m,.1))}),a.jsx("group",{ref:c})}function Ee(n,e,t){return Math.min(t,Math.max(e,n))}function Ct(n){const[e,t,o]=n.trim().split(/\s+/).map(parseFloat);return new I().setHSL((e||0)/360,(t||0)/100,(o||0)/100,Oe)}function zt({position:n,accent:e,artifacts:t,publications:o}){const{scene:s}=Le(rt),i=se(),{instance:r,scale:c,offset:f}=g.useMemo(()=>{const y=s.clone(!0),h=new $().setFromObject(y),p=h.getCenter(new B),m=h.getSize(new B),d=2.6/Math.max(m.x,m.y,m.z,.01);return{instance:y,scale:d,offset:[-p.x*d,-h.min.y*d,-p.z*d]}},[s]);return a.jsxs("group",{position:n,children:[a.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,.025,0],children:[a.jsx("ringGeometry",{args:[1.5,1.56,40]}),a.jsx("meshBasicMaterial",{color:e,transparent:!0,opacity:.4})]}),a.jsx("group",{position:f,children:a.jsx("primitive",{object:r,scale:c,dispose:null})}),a.jsx(De,{position:[0,3.15,0],center:!0,style:{pointerEvents:"none"},children:a.jsxs("div",{className:"swarm-world-label swarm-memory-label",children:[a.jsx("strong",{children:i("memoryHouse")}),a.jsxs("span",{children:[V(t)," ",i("artifactCount")," · ",V(o)," ",i("publicationCount")]})]})})]})}const Ut={lead:{contract:1,archetype:"biped",base:"mage",parts:{}},coordinator:{contract:1,archetype:"biped",base:"knight",parts:{}},worker:{contract:1,archetype:"biped",base:"rogue",parts:{}}};function ne(n,e){const t=getComputedStyle(e).getPropertyValue(`--${n}`).trim();return Ct(t)}class Bt extends g.Component{constructor(){super(...arguments);ae(this,"state",{failed:!1})}static getDerivedStateFromError(){return{failed:!0}}componentDidCatch(t){console.warn("Swarm 3D scene unavailable",t),this.props.onError()}render(){return this.state.failed?null:this.props.children}}const Ot=g.memo(function({node:e,paused:t,selected:o,onSelect:s,accent:i,surface:r}){var w,_,b,S,M,u;const c=se(),f=g.useRef(null),y=g.useRef(e.position),h=g.useRef({mode:"idle",speed:0}),p=g.useRef(!1),[m,,d]=e.position;return g.useEffect(()=>{var x;h.current.mode=!t&&((x=e.agent)==null?void 0:x.state)==="running"?"work":"idle"},[(w=e.agent)==null?void 0:w.state,t]),Ae((x,U)=>{var E;if(!f.current)return;const L=f.current.position,v=Math.hypot(m-L.x,d-L.z);if(v>.04&&!t){const l=Math.min(v,U*4);L.x+=(m-L.x)/v*l,L.z+=(d-L.z)/v*l,f.current.rotation.y=Math.atan2(m-L.x,d-L.z),h.current={mode:"walk",speed:4},p.current=!0}else L.set(m,0,d),p.current&&(h.current={mode:!t&&((E=e.agent)==null?void 0:E.state)==="running"?"work":"idle",speed:0},p.current=!1)}),a.jsxs("group",{ref:f,position:y.current,onClick:x=>{var U;x.stopPropagation(),s(((U=e.agent)==null?void 0:U.id)??e.id,e.group)},children:[a.jsxs("mesh",{position:[0,.06,0],children:[a.jsx("cylinderGeometry",{args:[e.group?1.05:.75,e.group?1.05:.75,.12,24]}),a.jsx("meshStandardMaterial",{color:r,roughness:1})]}),a.jsxs("mesh",{position:[0,.13,0],rotation:[-Math.PI/2,0,0],children:[a.jsx("ringGeometry",{args:[e.group?.98:.69,e.group?1.05:.76,32]}),a.jsx("meshBasicMaterial",{color:i,transparent:!0,opacity:o?1:.35})]}),a.jsx("group",{position:[0,.13,0],children:a.jsx(Lt,{recipe:Ut[((_=e.agent)==null?void 0:_.role)??"coordinator"],heightM:1.8,drive:h,paused:t})}),((b=e.agent)==null?void 0:b.tool_activity)&&a.jsxs("mesh",{position:[1,.7,0],children:[a.jsx("boxGeometry",{args:[.4,.7,.25]}),a.jsx("meshStandardMaterial",{color:i})]}),a.jsx(De,{position:[0,2.45,0],center:!0,style:{pointerEvents:"none"},children:a.jsxs("div",{className:"swarm-world-label","data-selected":o,children:[a.jsx("strong",{children:e.title}),a.jsx("span",{children:e.group?`${V(e.count??"0")} ${c("agents")}`:`${c(((S=e.agent)==null?void 0:S.role)??"worker")} · ${c(((M=e.agent)==null?void 0:M.state)??"idle")}`}),o&&a.jsxs("span",{children:[c("level")," ",e.level,(u=e.agent)!=null&&u.tool_activity?` · ${e.agent.tool_activity}`:""]})]})})]})});function Dt({camera:n,radius:e,onChange:t}){const{camera:o,invalidate:s}=Me(),i=g.useRef(null);g.useEffect(()=>{o.position.set(n.focus[0]+Math.cos(n.yaw)*e/n.zoom,e*(n.elevation??.9)/n.zoom,n.focus[1]+Math.sin(n.yaw)*e/n.zoom),o.lookAt(n.focus[0],.6,n.focus[1]),o.updateProjectionMatrix(),s()},[n,e,o,s]);const r=()=>{var p;const c=(p=i.current)==null?void 0:p.target;if(!c)return;const f=o.position.x-c.x,y=o.position.z-c.z,h=Math.max(.1,Math.hypot(f,y));t({yaw:Math.atan2(y,f),zoom:Math.max(.4,Math.min(3,e/h)),focus:[c.x,c.z],elevation:Math.max(.1,Math.min(99,o.position.y/h))})};return a.jsx(pt,{ref:i,makeDefault:!0,target:[n.focus[0],.6,n.focus[1]],minDistance:5,maxDistance:180,maxPolarAngle:Math.PI/2.1,screenSpacePanning:!1,enableDamping:!1,onEnd:r})}function Jt({snapshot:n,selected:e,onSelect:t,onUnavailable:o,live:s,onInspect:i}){const r=se(),c=g.useRef(null),f=st(c),y=mt()??!1,h=yt(c,o),[p,m]=g.useState(!1),[d,w]=g.useState(()=>at(n.team.id)),_=g.useCallback(l=>w(l),[]),[b,S]=g.useState(()=>({accent:new I,surface:new I,background:new I})),M=g.useMemo(()=>ct(n),[n]),u=lt(M),x=ut(M),U=n.counts.artifacts??"",L=n.counts.publications??"",v=!f||!s||y||p||n.team.state!=="running";g.useEffect(()=>{dt(n.team.id,d)},[n.team.id,d]),g.useEffect(()=>{const l=c.current;if(!l)return;const j=()=>S({accent:ne("primary",l),surface:ne("muted",l),background:ne("background",l)});j();const T=new MutationObserver(j);return T.observe(document.documentElement,{attributes:!0,attributeFilter:["class","style","data-theme"]}),()=>T.disconnect()},[]);const E=g.useMemo(()=>{const l=new Map(M.map(j=>[j.id,j.position]));return n.activity.flatMap(j=>{const T=j.agent_id&&l.get(j.agent_id),H=j.data.recipients;return!T||!Array.isArray(H)?[]:H.slice(0,16).flatMap(R=>{const N=l.get(String(R));return N?[{id:`${j.id}:${R}`,points:[[T[0],.2,T[2]],[N[0],.2,N[2]]]}]:[]})}).slice(-32)},[M,n.activity]);return a.jsxs("div",{className:"swarm-world",ref:c,"data-team-id":n.team.id,children:[a.jsx(Bt,{onError:o,children:a.jsx(g.Suspense,{fallback:a.jsx("div",{className:"swarm-empty",children:r("loading")}),children:a.jsxs(Ve,{dpr:p?1:[1,1.5],frameloop:v?"demand":"always",camera:{fov:45,near:.1,far:500},gl:{antialias:!p,powerPreference:"low-power"},children:[a.jsx("color",{attach:"background",args:[b.background]}),a.jsx("ambientLight",{intensity:1.05}),a.jsx("directionalLight",{position:[12,25,8],intensity:2}),a.jsx(Dt,{camera:d,radius:u,onChange:_}),a.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.03,0],children:[a.jsx("circleGeometry",{args:[u*.9,64]}),a.jsx("meshStandardMaterial",{color:b.surface,roughness:1})]}),a.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.02,0],children:[a.jsx("ringGeometry",{args:[u*.9-.045,u*.9,64]}),a.jsx("meshBasicMaterial",{color:b.accent,transparent:!0,opacity:.18})]}),a.jsx(g.Suspense,{fallback:null,children:a.jsx(zt,{position:x,accent:b.accent,artifacts:U,publications:L})}),E.map(l=>a.jsx(xt,{points:l.points,color:b.accent,lineWidth:1.5,dashed:!0},l.id)),M.map((l,j)=>a.jsx(Ot,{node:l,paused:v||j>=32,selected:e===l.id,onSelect:t,accent:b.accent,surface:b.surface},l.id))]},h)})}),a.jsxs("div",{className:"swarm-world-controls",children:[a.jsxs("div",{role:"group","aria-label":r("camera"),children:[a.jsx("button",{"aria-label":r("rotateLeft"),onClick:()=>w(l=>({...l,yaw:l.yaw-.3})),children:"↶"}),a.jsx("button",{"aria-label":r("rotateRight"),onClick:()=>w(l=>({...l,yaw:l.yaw+.3})),children:"↷"}),a.jsx("button",{"aria-label":r("zoomIn"),onClick:()=>w(l=>({...l,zoom:Math.min(3,l.zoom*1.2)})),children:"+"}),a.jsx("button",{"aria-label":r("zoomOut"),onClick:()=>w(l=>({...l,zoom:Math.max(.4,l.zoom/1.2)})),children:"−"}),a.jsx("button",{onClick:()=>w({...me,focus:[0,0]}),children:r("resetCamera")})]}),a.jsxs("label",{className:"swarm-check",children:[a.jsx("input",{type:"checkbox",checked:p,onChange:l=>m(l.target.checked)}),r("lowPower")]})]}),a.jsxs("div",{className:"swarm-world-memory",children:[a.jsx("strong",{children:r("memoryHouse")}),a.jsxs("div",{children:[a.jsxs("button",{onClick:()=>i==null?void 0:i("artifacts"),disabled:!i,children:[V(U)," ",r("artifactCount")]}),a.jsxs("button",{onClick:()=>i==null?void 0:i("publications"),disabled:!i,children:[V(L)," ",r("publicationCount")]})]})]}),a.jsxs("div",{className:"swarm-minimap","aria-label":r("minimap"),children:[a.jsxs("svg",{viewBox:"-50 -50 100 100",role:"img","aria-label":r("minimap"),children:[a.jsx("circle",{r:"45",fill:"none",stroke:"currentColor",opacity:".15"}),a.jsx("rect",{x:x[0]/u*65-2.5,y:x[2]/u*65-2.5,width:"5",height:"5",fill:"none",stroke:"var(--swarm-accent)",children:a.jsx("title",{children:r("memoryHouse")})}),M.map(l=>a.jsx("circle",{cx:l.position[0]/u*65,cy:l.position[2]/u*65,r:l.id===e?3:1.8,fill:"var(--swarm-accent)"},l.id))]}),a.jsx("button",{onClick:()=>w({...me,focus:[0,0]}),children:r("overview")}),ft(n.team.state)&&a.jsx("span",{className:"swarm-muted",children:r("finalWorld")})]})]})}export{Jt as default};
