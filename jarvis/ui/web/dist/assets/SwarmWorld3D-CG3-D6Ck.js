var Se=Object.defineProperty;var _e=(r,e,t)=>e in r?Se(r,e,{enumerable:!0,configurable:!0,writable:!0,value:t}):r[e]=t;var ie=(r,e,t)=>_e(r,typeof e!="symbol"?e+"":e,t);import{r as f,j as o}from"./index-BZ2bhsxs.js";import{w as pe,c as he,u as Ee,m as je,b as Me,C as Le,a as Ce}from"./useFigureAssets-CEe-QXtk.js";import{aV as Ae,aN as se,aW as Z,aB as R,aX as ze,B as V,aI as ge,V as A,r as Ue,U as oe,aY as re,o as ve,M as Be,s as k,aZ as Oe,a4 as De,av as Pe,C as H,S as Te}from"./three.module-Bqi6ddCx.js";import{u as Re}from"./useCanvasAwake-BUraffhB.js";import{H as xe,F as ke}from"./FigureRig-D0rGwnAX.js";import{u as K,e as N,r as He,w as Ne,a as Ge,m as We,s as Ie,D as ae,t as Fe}from"./UltraSwarmView-C6sAb1Fd.js";import{_ as ce}from"./extends-CF3RwP-h.js";import{O as Ve}from"./OrbitControls-LENYuvD5.js";const ye=pe>=125?"uv1":"uv2",le=new V,W=new A;class Q extends Ae{constructor(){super(),this.isLineSegmentsGeometry=!0,this.type="LineSegmentsGeometry";const e=[-1,2,0,1,2,0,-1,1,0,1,1,0,-1,0,0,1,0,0,-1,-1,0,1,-1,0],t=[-1,2,1,2,-1,1,1,1,-1,-1,1,-1,-1,-2,1,-2],s=[0,2,1,2,3,1,2,4,3,4,5,3,4,6,5,6,7,5];this.setIndex(s),this.setAttribute("position",new se(e,3)),this.setAttribute("uv",new se(t,2))}applyMatrix4(e){const t=this.attributes.instanceStart,s=this.attributes.instanceEnd;return t!==void 0&&(t.applyMatrix4(e),s.applyMatrix4(e),t.needsUpdate=!0),this.boundingBox!==null&&this.computeBoundingBox(),this.boundingSphere!==null&&this.computeBoundingSphere(),this}setPositions(e){let t;e instanceof Float32Array?t=e:Array.isArray(e)&&(t=new Float32Array(e));const s=new Z(t,6,1);return this.setAttribute("instanceStart",new R(s,3,0)),this.setAttribute("instanceEnd",new R(s,3,3)),this.computeBoundingBox(),this.computeBoundingSphere(),this}setColors(e,t=3){let s;e instanceof Float32Array?s=e:Array.isArray(e)&&(s=new Float32Array(e));const i=new Z(s,t*2,1);return this.setAttribute("instanceColorStart",new R(i,t,0)),this.setAttribute("instanceColorEnd",new R(i,t,t)),this}fromWireframeGeometry(e){return this.setPositions(e.attributes.position.array),this}fromEdgesGeometry(e){return this.setPositions(e.attributes.position.array),this}fromMesh(e){return this.fromWireframeGeometry(new ze(e.geometry)),this}fromLineSegments(e){const t=e.geometry;return this.setPositions(t.attributes.position.array),this}computeBoundingBox(){this.boundingBox===null&&(this.boundingBox=new V);const e=this.attributes.instanceStart,t=this.attributes.instanceEnd;e!==void 0&&t!==void 0&&(this.boundingBox.setFromBufferAttribute(e),le.setFromBufferAttribute(t),this.boundingBox.union(le))}computeBoundingSphere(){this.boundingSphere===null&&(this.boundingSphere=new ge),this.boundingBox===null&&this.computeBoundingBox();const e=this.attributes.instanceStart,t=this.attributes.instanceEnd;if(e!==void 0&&t!==void 0){const s=this.boundingSphere.center;this.boundingBox.getCenter(s);let i=0;for(let n=0,a=e.count;n<a;n++)W.fromBufferAttribute(e,n),i=Math.max(i,s.distanceToSquared(W)),W.fromBufferAttribute(t,n),i=Math.max(i,s.distanceToSquared(W));this.boundingSphere.radius=Math.sqrt(i),isNaN(this.boundingSphere.radius)&&console.error("THREE.LineSegmentsGeometry.computeBoundingSphere(): Computed radius is NaN. The instanced position data is likely to have NaN values.",this)}}toJSON(){}applyMatrix(e){return console.warn("THREE.LineSegmentsGeometry: applyMatrix() has been renamed to applyMatrix4()."),this.applyMatrix4(e)}}class we extends Q{constructor(){super(),this.isLineGeometry=!0,this.type="LineGeometry"}setPositions(e){const t=e.length-3,s=new Float32Array(2*t);for(let i=0;i<t;i+=3)s[2*i]=e[i],s[2*i+1]=e[i+1],s[2*i+2]=e[i+2],s[2*i+3]=e[i+3],s[2*i+4]=e[i+4],s[2*i+5]=e[i+5];return super.setPositions(s),this}setColors(e,t=3){const s=e.length-t,i=new Float32Array(2*s);if(t===3)for(let n=0;n<s;n+=t)i[2*n]=e[n],i[2*n+1]=e[n+1],i[2*n+2]=e[n+2],i[2*n+3]=e[n+3],i[2*n+4]=e[n+4],i[2*n+5]=e[n+5];else for(let n=0;n<s;n+=t)i[2*n]=e[n],i[2*n+1]=e[n+1],i[2*n+2]=e[n+2],i[2*n+3]=e[n+3],i[2*n+4]=e[n+4],i[2*n+5]=e[n+5],i[2*n+6]=e[n+6],i[2*n+7]=e[n+7];return super.setColors(i,t),this}fromLine(e){const t=e.geometry;return this.setPositions(t.attributes.position.array),this}}class ee extends Ue{constructor(e){super({type:"LineMaterial",uniforms:oe.clone(oe.merge([re.common,re.fog,{worldUnits:{value:1},linewidth:{value:1},resolution:{value:new ve(1,1)},dashOffset:{value:0},dashScale:{value:1},dashSize:{value:1},gapSize:{value:1}}])),vertexShader:`
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
					#include <${pe>=154?"colorspace_fragment":"encodings_fragment"}>
					#include <fog_fragment>
					#include <premultiplied_alpha_fragment>

				}
			`,clipping:!0}),this.isLineMaterial=!0,this.onBeforeCompile=function(){this.transparent?this.defines.USE_LINE_COLOR_ALPHA="1":delete this.defines.USE_LINE_COLOR_ALPHA},Object.defineProperties(this,{color:{enumerable:!0,get:function(){return this.uniforms.diffuse.value},set:function(t){this.uniforms.diffuse.value=t}},worldUnits:{enumerable:!0,get:function(){return"WORLD_UNITS"in this.defines},set:function(t){t===!0?this.defines.WORLD_UNITS="":delete this.defines.WORLD_UNITS}},linewidth:{enumerable:!0,get:function(){return this.uniforms.linewidth.value},set:function(t){this.uniforms.linewidth.value=t}},dashed:{enumerable:!0,get:function(){return"USE_DASH"in this.defines},set(t){!!t!="USE_DASH"in this.defines&&(this.needsUpdate=!0),t===!0?this.defines.USE_DASH="":delete this.defines.USE_DASH}},dashScale:{enumerable:!0,get:function(){return this.uniforms.dashScale.value},set:function(t){this.uniforms.dashScale.value=t}},dashSize:{enumerable:!0,get:function(){return this.uniforms.dashSize.value},set:function(t){this.uniforms.dashSize.value=t}},dashOffset:{enumerable:!0,get:function(){return this.uniforms.dashOffset.value},set:function(t){this.uniforms.dashOffset.value=t}},gapSize:{enumerable:!0,get:function(){return this.uniforms.gapSize.value},set:function(t){this.uniforms.gapSize.value=t}},opacity:{enumerable:!0,get:function(){return this.uniforms.opacity.value},set:function(t){this.uniforms.opacity.value=t}},resolution:{enumerable:!0,get:function(){return this.uniforms.resolution.value},set:function(t){this.uniforms.resolution.value.copy(t)}},alphaToCoverage:{enumerable:!0,get:function(){return"USE_ALPHA_TO_COVERAGE"in this.defines},set:function(t){!!t!="USE_ALPHA_TO_COVERAGE"in this.defines&&(this.needsUpdate=!0),t===!0?(this.defines.USE_ALPHA_TO_COVERAGE="",this.extensions.derivatives=!0):(delete this.defines.USE_ALPHA_TO_COVERAGE,this.extensions.derivatives=!1)}}}),this.setValues(e)}}const q=new k,de=new A,ue=new A,_=new k,E=new k,z=new k,X=new A,J=new De,j=new Oe,fe=new A,I=new V,F=new ge,U=new k;let B,P;function me(r,e,t){return U.set(0,0,-e,1).applyMatrix4(r.projectionMatrix),U.multiplyScalar(1/U.w),U.x=P/t.width,U.y=P/t.height,U.applyMatrix4(r.projectionMatrixInverse),U.multiplyScalar(1/U.w),Math.abs(Math.max(U.x,U.y))}function $e(r,e){const t=r.matrixWorld,s=r.geometry,i=s.attributes.instanceStart,n=s.attributes.instanceEnd,a=Math.min(s.instanceCount,i.count);for(let c=0,m=a;c<m;c++){j.start.fromBufferAttribute(i,c),j.end.fromBufferAttribute(n,c),j.applyMatrix4(t);const h=new A,p=new A;B.distanceSqToSegment(j.start,j.end,p,h),p.distanceTo(h)<P*.5&&e.push({point:p,pointOnLine:h,distance:B.origin.distanceTo(p),object:r,face:null,faceIndex:c,uv:null,[ye]:null})}}function qe(r,e,t){const s=e.projectionMatrix,n=r.material.resolution,a=r.matrixWorld,c=r.geometry,m=c.attributes.instanceStart,h=c.attributes.instanceEnd,p=Math.min(c.instanceCount,m.count),u=-e.near;B.at(1,z),z.w=1,z.applyMatrix4(e.matrixWorldInverse),z.applyMatrix4(s),z.multiplyScalar(1/z.w),z.x*=n.x/2,z.y*=n.y/2,z.z=0,X.copy(z),J.multiplyMatrices(e.matrixWorldInverse,a);for(let g=0,x=p;g<x;g++){if(_.fromBufferAttribute(m,g),E.fromBufferAttribute(h,g),_.w=1,E.w=1,_.applyMatrix4(J),E.applyMatrix4(J),_.z>u&&E.z>u)continue;if(_.z>u){const l=_.z-E.z,v=(_.z-u)/l;_.lerp(E,v)}else if(E.z>u){const l=E.z-_.z,v=(E.z-u)/l;E.lerp(_,v)}_.applyMatrix4(s),E.applyMatrix4(s),_.multiplyScalar(1/_.w),E.multiplyScalar(1/E.w),_.x*=n.x/2,_.y*=n.y/2,E.x*=n.x/2,E.y*=n.y/2,j.start.copy(_),j.start.z=0,j.end.copy(E),j.end.z=0;const M=j.closestPointToPointParameter(X,!0);j.at(M,fe);const w=Pe.lerp(_.z,E.z,M),b=w>=-1&&w<=1,S=X.distanceTo(fe)<P*.5;if(b&&S){j.start.fromBufferAttribute(m,g),j.end.fromBufferAttribute(h,g),j.start.applyMatrix4(a),j.end.applyMatrix4(a);const l=new A,v=new A;B.distanceSqToSegment(j.start,j.end,v,l),t.push({point:v,pointOnLine:l,distance:B.origin.distanceTo(v),object:r,face:null,faceIndex:g,uv:null,[ye]:null})}}}class be extends Be{constructor(e=new Q,t=new ee({color:Math.random()*16777215})){super(e,t),this.isLineSegments2=!0,this.type="LineSegments2"}computeLineDistances(){const e=this.geometry,t=e.attributes.instanceStart,s=e.attributes.instanceEnd,i=new Float32Array(2*t.count);for(let a=0,c=0,m=t.count;a<m;a++,c+=2)de.fromBufferAttribute(t,a),ue.fromBufferAttribute(s,a),i[c]=c===0?0:i[c-1],i[c+1]=i[c]+de.distanceTo(ue);const n=new Z(i,2,1);return e.setAttribute("instanceDistanceStart",new R(n,1,0)),e.setAttribute("instanceDistanceEnd",new R(n,1,1)),this}raycast(e,t){const s=this.material.worldUnits,i=e.camera;i===null&&!s&&console.error('LineSegments2: "Raycaster.camera" needs to be set in order to raycast against LineSegments2 while worldUnits is set to false.');const n=e.params.Line2!==void 0&&e.params.Line2.threshold||0;B=e.ray;const a=this.matrixWorld,c=this.geometry,m=this.material;P=m.linewidth+n,c.boundingSphere===null&&c.computeBoundingSphere(),F.copy(c.boundingSphere).applyMatrix4(a);let h;if(s)h=P*.5;else{const u=Math.max(i.near,F.distanceToPoint(B.origin));h=me(i,u,m.resolution)}if(F.radius+=h,B.intersectsSphere(F)===!1)return;c.boundingBox===null&&c.computeBoundingBox(),I.copy(c.boundingBox).applyMatrix4(a);let p;if(s)p=P*.5;else{const u=Math.max(i.near,I.distanceToPoint(B.origin));p=me(i,u,m.resolution)}I.expandByScalar(p),B.intersectsBox(I)!==!1&&(s?$e(this,t):qe(this,i,t))}onBeforeRender(e){const t=this.material.uniforms;t&&t.resolution&&(e.getViewport(q),this.material.uniforms.resolution.value.set(q.z,q.w))}}class Xe extends be{constructor(e=new we,t=new ee({color:Math.random()*16777215})){super(e,t),this.isLine2=!0,this.type="Line2"}}const Je=f.forwardRef(function({points:e,color:t=16777215,vertexColors:s,linewidth:i,lineWidth:n,segments:a,dashed:c,...m},h){var p,u;const g=he(b=>b.size),x=f.useMemo(()=>a?new be:new Xe,[a]),[y]=f.useState(()=>new ee),M=(s==null||(p=s[0])==null?void 0:p.length)===4?4:3,w=f.useMemo(()=>{const b=a?new Q:new we,S=e.map(l=>{const v=Array.isArray(l);return l instanceof A||l instanceof k?[l.x,l.y,l.z]:l instanceof ve?[l.x,l.y,0]:v&&l.length===3?[l[0],l[1],l[2]]:v&&l.length===2?[l[0],l[1],0]:l});if(b.setPositions(S.flat()),s){t=16777215;const l=s.map(v=>v instanceof H?v.toArray():v);b.setColors(l.flat(),M)}return b},[e,a,s,M]);return f.useLayoutEffect(()=>{x.computeLineDistances()},[e,x]),f.useLayoutEffect(()=>{c?y.defines.USE_DASH="":delete y.defines.USE_DASH,y.needsUpdate=!0},[c,y]),f.useEffect(()=>()=>{w.dispose(),y.dispose()},[w]),f.createElement("primitive",ce({object:x,ref:h},m),f.createElement("primitive",{object:w,attach:"geometry"}),f.createElement("primitive",ce({object:y,attach:"material",color:t,vertexColors:!!s,resolution:[g.width,g.height],linewidth:(u=i??n)!==null&&u!==void 0?u:1,dashed:c,transparent:M===4},m)))});function Ye(r,e){const[t,s]=f.useState(0),i=f.useRef(0);return f.useEffect(()=>{const n=r.current;if(!n)return;let a=null,c=!1,m=!1,h=0,p=0,u;const g=()=>{!c&&!m&&(m=!0,s(b=>b+1))},x=b=>{if(b.preventDefault(),!c){if(++i.current>2){e();return}u=setTimeout(g,250)}},y=()=>{clearTimeout(u),g()},M=()=>{if(a=n.querySelector("canvas"),!a){++p<120&&(h=requestAnimationFrame(M));return}a.addEventListener("webglcontextlost",x),a.addEventListener("webglcontextrestored",y)};M();const w=setTimeout(()=>{i.current=0},6e4);return()=>{var b;if(c=!0,cancelAnimationFrame(h),clearTimeout(u),clearTimeout(w),!!a){a.removeEventListener("webglcontextlost",x),a.removeEventListener("webglcontextrestored",y);try{const S=a.getContext("webgl2")??a.getContext("webgl");(b=S==null?void 0:S.getExtension("WEBGL_lose_context"))==null||b.loseContext()}catch{}}}},[t,r,e]),t}function Ze(r){const[e,t,s]=r.trim().split(/\s+/).map(parseFloat);return new H().setHSL((e||0)/360,(t||0)/100,(s||0)/100,Te)}function Ke({position:r,accent:e,artifacts:t,publications:s}){const{scene:i}=Ee(je),n=K(),{instance:a,scale:c,offset:m}=f.useMemo(()=>{const h=i.clone(!0),p=new V().setFromObject(h),u=p.getCenter(new A),g=p.getSize(new A),x=2.6/Math.max(g.x,g.y,g.z,.01);return{instance:h,scale:x,offset:[-u.x*x,-p.min.y*x,-u.z*x]}},[i]);return o.jsxs("group",{position:r,children:[o.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,.025,0],children:[o.jsx("ringGeometry",{args:[1.5,1.56,40]}),o.jsx("meshBasicMaterial",{color:e,transparent:!0,opacity:.4})]}),o.jsx("group",{position:m,children:o.jsx("primitive",{object:a,scale:c,dispose:null})}),o.jsx(xe,{position:[0,3.15,0],center:!0,style:{pointerEvents:"none"},children:o.jsxs("div",{className:"swarm-world-label swarm-memory-label",children:[o.jsx("strong",{children:n("memoryHouse")}),o.jsxs("span",{children:[N(t)," ",n("artifactCount")," · ",N(s)," ",n("publicationCount")]})]})})]})}const Qe={lead:{contract:1,archetype:"biped",base:"mage",parts:{}},coordinator:{contract:1,archetype:"biped",base:"knight",parts:{}},worker:{contract:1,archetype:"biped",base:"rogue",parts:{}}};function Y(r,e){const t=getComputedStyle(e).getPropertyValue(`--${r}`).trim();return Ze(t)}class et extends f.Component{constructor(){super(...arguments);ie(this,"state",{failed:!1})}static getDerivedStateFromError(){return{failed:!0}}componentDidCatch(t){console.warn("Swarm 3D scene unavailable",t),this.props.onError()}render(){return this.state.failed?null:this.props.children}}const tt=f.memo(function({node:e,paused:t,selected:s,onSelect:i,accent:n,surface:a}){var y,M,w,b,S,l;const c=K(),m=f.useRef(null),h=f.useRef(e.position),p=f.useRef({mode:"idle",speed:0}),u=f.useRef(!1),[g,,x]=e.position;return f.useEffect(()=>{var v;p.current.mode=!t&&((v=e.agent)==null?void 0:v.state)==="running"?"work":"idle"},[(y=e.agent)==null?void 0:y.state,t]),Ce((v,O)=>{var G;if(!m.current)return;const L=m.current.position,D=Math.hypot(g-L.x,x-L.z);if(D>.04&&!t){const d=Math.min(D,O*4);L.x+=(g-L.x)/D*d,L.z+=(x-L.z)/D*d,m.current.rotation.y=Math.atan2(g-L.x,x-L.z),p.current={mode:"walk",speed:4},u.current=!0}else L.set(g,0,x),u.current&&(p.current={mode:!t&&((G=e.agent)==null?void 0:G.state)==="running"?"work":"idle",speed:0},u.current=!1)}),o.jsxs("group",{ref:m,position:h.current,onClick:v=>{var O;v.stopPropagation(),i(((O=e.agent)==null?void 0:O.id)??e.id,e.group)},children:[o.jsxs("mesh",{position:[0,.06,0],children:[o.jsx("cylinderGeometry",{args:[e.group?1.05:.75,e.group?1.05:.75,.12,24]}),o.jsx("meshStandardMaterial",{color:a,roughness:1})]}),o.jsxs("mesh",{position:[0,.13,0],rotation:[-Math.PI/2,0,0],children:[o.jsx("ringGeometry",{args:[e.group?.98:.69,e.group?1.05:.76,32]}),o.jsx("meshBasicMaterial",{color:n,transparent:!0,opacity:s?1:.35})]}),o.jsx("group",{position:[0,.13,0],children:o.jsx(ke,{recipe:Qe[((M=e.agent)==null?void 0:M.role)??"coordinator"],heightM:1.8,drive:p,paused:t})}),((w=e.agent)==null?void 0:w.tool_activity)&&o.jsxs("mesh",{position:[1,.7,0],children:[o.jsx("boxGeometry",{args:[.4,.7,.25]}),o.jsx("meshStandardMaterial",{color:n})]}),o.jsx(xe,{position:[0,2.45,0],center:!0,style:{pointerEvents:"none"},children:o.jsxs("div",{className:"swarm-world-label","data-selected":s,children:[o.jsx("strong",{children:e.title}),o.jsx("span",{children:e.group?`${N(e.count??"0")} ${c("agents")}`:`${c(((b=e.agent)==null?void 0:b.role)??"worker")} · ${c(((S=e.agent)==null?void 0:S.state)??"idle")}`}),s&&o.jsxs("span",{children:[c("level")," ",e.level,(l=e.agent)!=null&&l.tool_activity?` · ${e.agent.tool_activity}`:""]})]})})]})});function nt({camera:r,radius:e,onChange:t}){const{camera:s,invalidate:i}=he(),n=f.useRef(null);f.useEffect(()=>{s.position.set(r.focus[0]+Math.cos(r.yaw)*e/r.zoom,e*(r.elevation??.9)/r.zoom,r.focus[1]+Math.sin(r.yaw)*e/r.zoom),s.lookAt(r.focus[0],.6,r.focus[1]),s.updateProjectionMatrix(),i()},[r,e,s,i]);const a=()=>{var u;const c=(u=n.current)==null?void 0:u.target;if(!c)return;const m=s.position.x-c.x,h=s.position.z-c.z,p=Math.max(.1,Math.hypot(m,h));t({yaw:Math.atan2(h,m),zoom:Math.max(.4,Math.min(3,e/p)),focus:[c.x,c.z],elevation:Math.max(.1,Math.min(99,s.position.y/p))})};return o.jsx(Ve,{ref:n,makeDefault:!0,target:[r.focus[0],.6,r.focus[1]],minDistance:5,maxDistance:180,maxPolarAngle:Math.PI/2.1,screenSpacePanning:!1,enableDamping:!1,onEnd:a})}function ft({snapshot:r,selected:e,onSelect:t,onUnavailable:s,live:i,onInspect:n}){const a=K(),c=f.useRef(null),m=Re(c),h=Me()??!1,p=Ye(c,s),[u,g]=f.useState(!1),[x,y]=f.useState(()=>He(r.team.id)),M=f.useCallback(d=>y(d),[]),[w,b]=f.useState(()=>({accent:new H,surface:new H,background:new H})),S=f.useMemo(()=>Ne(r),[r]),l=Ge(S),v=We(S),O=r.counts.artifacts??"",L=r.counts.publications??"",D=!m||!i||h||u||r.team.state!=="running";f.useEffect(()=>{Ie(r.team.id,x)},[r.team.id,x]),f.useEffect(()=>{const d=c.current;if(!d)return;const C=()=>b({accent:Y("primary",d),surface:Y("muted",d),background:Y("background",d)});C();const T=new MutationObserver(C);return T.observe(document.documentElement,{attributes:!0,attributeFilter:["class","style","data-theme"]}),()=>T.disconnect()},[]);const G=f.useMemo(()=>{const d=new Map(S.map(C=>[C.id,C.position]));return r.activity.flatMap(C=>{const T=C.agent_id&&d.get(C.agent_id),te=C.data.recipients;return!T||!Array.isArray(te)?[]:te.slice(0,16).flatMap(ne=>{const $=d.get(String(ne));return $?[{id:`${C.id}:${ne}`,points:[[T[0],.2,T[2]],[$[0],.2,$[2]]]}]:[]})}).slice(-32)},[S,r.activity]);return o.jsxs("div",{className:"swarm-world",ref:c,"data-team-id":r.team.id,children:[o.jsx(et,{onError:s,children:o.jsx(f.Suspense,{fallback:o.jsx("div",{className:"swarm-empty",children:a("loading")}),children:o.jsxs(Le,{dpr:u?1:[1,1.5],frameloop:D?"demand":"always",camera:{fov:45,near:.1,far:500},gl:{antialias:!u,powerPreference:"low-power"},children:[o.jsx("color",{attach:"background",args:[w.background]}),o.jsx("ambientLight",{intensity:1.05}),o.jsx("directionalLight",{position:[12,25,8],intensity:2}),o.jsx(nt,{camera:x,radius:l,onChange:M}),o.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.03,0],children:[o.jsx("circleGeometry",{args:[l*.9,64]}),o.jsx("meshStandardMaterial",{color:w.surface,roughness:1})]}),o.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.02,0],children:[o.jsx("ringGeometry",{args:[l*.9-.045,l*.9,64]}),o.jsx("meshBasicMaterial",{color:w.accent,transparent:!0,opacity:.18})]}),o.jsx(f.Suspense,{fallback:null,children:o.jsx(Ke,{position:v,accent:w.accent,artifacts:O,publications:L})}),G.map(d=>o.jsx(Je,{points:d.points,color:w.accent,lineWidth:1.5,dashed:!0},d.id)),S.map((d,C)=>o.jsx(tt,{node:d,paused:D||C>=32,selected:e===d.id,onSelect:t,accent:w.accent,surface:w.surface},d.id))]},p)})}),o.jsxs("div",{className:"swarm-world-controls",children:[o.jsxs("div",{role:"group","aria-label":a("camera"),children:[o.jsx("button",{"aria-label":a("rotateLeft"),onClick:()=>y(d=>({...d,yaw:d.yaw-.3})),children:"↶"}),o.jsx("button",{"aria-label":a("rotateRight"),onClick:()=>y(d=>({...d,yaw:d.yaw+.3})),children:"↷"}),o.jsx("button",{"aria-label":a("zoomIn"),onClick:()=>y(d=>({...d,zoom:Math.min(3,d.zoom*1.2)})),children:"+"}),o.jsx("button",{"aria-label":a("zoomOut"),onClick:()=>y(d=>({...d,zoom:Math.max(.4,d.zoom/1.2)})),children:"−"}),o.jsx("button",{onClick:()=>y({...ae,focus:[0,0]}),children:a("resetCamera")})]}),o.jsxs("label",{className:"swarm-check",children:[o.jsx("input",{type:"checkbox",checked:u,onChange:d=>g(d.target.checked)}),a("lowPower")]})]}),o.jsxs("div",{className:"swarm-world-memory",children:[o.jsx("strong",{children:a("memoryHouse")}),o.jsxs("div",{children:[o.jsxs("button",{onClick:()=>n==null?void 0:n("artifacts"),disabled:!n,children:[N(O)," ",a("artifactCount")]}),o.jsxs("button",{onClick:()=>n==null?void 0:n("publications"),disabled:!n,children:[N(L)," ",a("publicationCount")]})]})]}),o.jsxs("div",{className:"swarm-minimap","aria-label":a("minimap"),children:[o.jsxs("svg",{viewBox:"-50 -50 100 100",role:"img","aria-label":a("minimap"),children:[o.jsx("circle",{r:"45",fill:"none",stroke:"currentColor",opacity:".15"}),o.jsx("rect",{x:v[0]/l*65-2.5,y:v[2]/l*65-2.5,width:"5",height:"5",fill:"none",stroke:"var(--swarm-accent)",children:o.jsx("title",{children:a("memoryHouse")})}),S.map(d=>o.jsx("circle",{cx:d.position[0]/l*65,cy:d.position[2]/l*65,r:d.id===e?3:1.8,fill:"var(--swarm-accent)"},d.id))]}),o.jsx("button",{onClick:()=>y({...ae,focus:[0,0]}),children:a("overview")}),Fe(r.team.state)&&o.jsx("span",{className:"swarm-muted",children:a("finalWorld")})]})]})}export{ft as default};
