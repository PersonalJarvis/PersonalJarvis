var zt=Object.defineProperty;var Ut=(r,t,i)=>t in r?zt(r,t,{enumerable:!0,configurable:!0,writable:!0,value:i}):r[t]=i;var $e=(r,t,i)=>Ut(r,typeof t!="symbol"?t+"":t,i);import{jk as J,jl as ee,jm as Qe,gv as Je,aY as T,g3 as Se,bE as B,g4 as _e,jn as Rt,jo as Nt,jp as It,h7 as et,jq as Pe,g_ as te,jr as Bt,bh as ge,h5 as dt,bJ as kt,bt as tt,js as nt,ba as Ht,bK as ne,jt as Ft,gt as Wt,gU as Gt,r as y,b6 as re,bf as Yt,j as l}from"./index-DoT9i5u3.js";import{v as ft,u as G,b as pt,a as Vt,H as mt,m as Zt,c as Xt,C as Kt}from"./useFigureAssets-p1whck9x.js";import{u as qt}from"./useCanvasAwake-CPRlU0Ki.js";import{F as $t}from"./FigureRig-hbcYaL5e.js";import{u as Ce,e as ce,r as Qt,w as Jt,a as en,m as tn,s as nn,D as it,t as on}from"./UltraSwarmView-Bq_5wnsu.js";import{_ as Oe}from"./extends-CF3RwP-h.js";const ht=ft>=125?"uv1":"uv2";var sn=Object.defineProperty,an=(r,t,i)=>t in r?sn(r,t,{enumerable:!0,configurable:!0,writable:!0,value:i}):r[t]=i,rn=(r,t,i)=>(an(r,t+"",i),i);class cn{constructor(){rn(this,"_listeners")}addEventListener(t,i){this._listeners===void 0&&(this._listeners={});const e=this._listeners;e[t]===void 0&&(e[t]=[]),e[t].indexOf(i)===-1&&e[t].push(i)}hasEventListener(t,i){if(this._listeners===void 0)return!1;const e=this._listeners;return e[t]!==void 0&&e[t].indexOf(i)!==-1}removeEventListener(t,i){if(this._listeners===void 0)return;const s=this._listeners[t];if(s!==void 0){const o=s.indexOf(i);o!==-1&&s.splice(o,1)}}dispatchEvent(t){if(this._listeners===void 0)return;const e=this._listeners[t.type];if(e!==void 0){t.target=this;const s=e.slice(0);for(let o=0,u=s.length;o<u;o++)s[o].call(this,t);t.target=null}}}var ln=Object.defineProperty,un=(r,t,i)=>t in r?ln(r,t,{enumerable:!0,configurable:!0,writable:!0,value:i}):r[t]=i,m=(r,t,i)=>(un(r,typeof t!="symbol"?t+"":t,i),i);const fe=new Rt,ot=new Nt,dn=Math.cos(70*(Math.PI/180)),st=(r,t)=>(r%t+t)%t;let fn=class extends cn{constructor(t,i){super(),m(this,"object"),m(this,"domElement"),m(this,"enabled",!0),m(this,"target",new T),m(this,"minDistance",0),m(this,"maxDistance",1/0),m(this,"minZoom",0),m(this,"maxZoom",1/0),m(this,"minPolarAngle",0),m(this,"maxPolarAngle",Math.PI),m(this,"minAzimuthAngle",-1/0),m(this,"maxAzimuthAngle",1/0),m(this,"enableDamping",!1),m(this,"dampingFactor",.05),m(this,"enableZoom",!0),m(this,"zoomSpeed",1),m(this,"enableRotate",!0),m(this,"rotateSpeed",1),m(this,"enablePan",!0),m(this,"panSpeed",1),m(this,"screenSpacePanning",!0),m(this,"keyPanSpeed",7),m(this,"zoomToCursor",!1),m(this,"autoRotate",!1),m(this,"autoRotateSpeed",2),m(this,"reverseOrbit",!1),m(this,"reverseHorizontalOrbit",!1),m(this,"reverseVerticalOrbit",!1),m(this,"keys",{LEFT:"ArrowLeft",UP:"ArrowUp",RIGHT:"ArrowRight",BOTTOM:"ArrowDown"}),m(this,"mouseButtons",{LEFT:J.ROTATE,MIDDLE:J.DOLLY,RIGHT:J.PAN}),m(this,"touches",{ONE:ee.ROTATE,TWO:ee.DOLLY_PAN}),m(this,"target0"),m(this,"position0"),m(this,"zoom0"),m(this,"_domElementKeyEvents",null),m(this,"getPolarAngle"),m(this,"getAzimuthalAngle"),m(this,"setPolarAngle"),m(this,"setAzimuthalAngle"),m(this,"getDistance"),m(this,"getZoomScale"),m(this,"listenToKeyEvents"),m(this,"stopListenToKeyEvents"),m(this,"saveState"),m(this,"reset"),m(this,"update"),m(this,"connect"),m(this,"dispose"),m(this,"dollyIn"),m(this,"dollyOut"),m(this,"getScale"),m(this,"setScale"),this.object=t,this.domElement=i,this.target0=this.target.clone(),this.position0=this.object.position.clone(),this.zoom0=this.object.zoom,this.getPolarAngle=()=>f.phi,this.getAzimuthalAngle=()=>f.theta,this.setPolarAngle=n=>{let c=st(n,2*Math.PI),b=f.phi;b<0&&(b+=2*Math.PI),c<0&&(c+=2*Math.PI);let j=Math.abs(c-b);2*Math.PI-j<j&&(c<b?c+=2*Math.PI:b+=2*Math.PI),p.phi=c-b,e.update()},this.setAzimuthalAngle=n=>{let c=st(n,2*Math.PI),b=f.theta;b<0&&(b+=2*Math.PI),c<0&&(c+=2*Math.PI);let j=Math.abs(c-b);2*Math.PI-j<j&&(c<b?c+=2*Math.PI:b+=2*Math.PI),p.theta=c-b,e.update()},this.getDistance=()=>e.object.position.distanceTo(e.target),this.listenToKeyEvents=n=>{n.addEventListener("keydown",we),this._domElementKeyEvents=n},this.stopListenToKeyEvents=()=>{this._domElementKeyEvents.removeEventListener("keydown",we),this._domElementKeyEvents=null},this.saveState=()=>{e.target0.copy(e.target),e.position0.copy(e.object.position),e.zoom0=e.object.zoom},this.reset=()=>{e.target.copy(e.target0),e.object.position.copy(e.position0),e.object.zoom=e.zoom0,e.object.updateProjectionMatrix(),e.dispatchEvent(s),e.update(),d=a.NONE},this.update=(()=>{const n=new T,c=new T(0,1,0),b=new Je().setFromUnitVectors(t.up,c),j=b.clone().invert(),D=new T,V=new Je,K=2*Math.PI;return function(){const qe=e.object.position;b.setFromUnitVectors(t.up,c),j.copy(b).invert(),n.copy(qe).sub(e.target),n.applyQuaternion(b),f.setFromVector3(n),e.autoRotate&&d===a.NONE&&q(le()),e.enableDamping?(f.theta+=p.theta*e.dampingFactor,f.phi+=p.phi*e.dampingFactor):(f.theta+=p.theta,f.phi+=p.phi);let Z=e.minAzimuthAngle,X=e.maxAzimuthAngle;isFinite(Z)&&isFinite(X)&&(Z<-Math.PI?Z+=K:Z>Math.PI&&(Z-=K),X<-Math.PI?X+=K:X>Math.PI&&(X-=K),Z<=X?f.theta=Math.max(Z,Math.min(X,f.theta)):f.theta=f.theta>(Z+X)/2?Math.max(Z,f.theta):Math.min(X,f.theta)),f.phi=Math.max(e.minPolarAngle,Math.min(e.maxPolarAngle,f.phi)),f.makeSafe(),e.enableDamping===!0?e.target.addScaledVector(S,e.dampingFactor):e.target.add(S),e.zoomToCursor&&h||e.object.isOrthographicCamera?f.radius=ye(f.radius):f.radius=ye(f.radius*w),n.setFromSpherical(f),n.applyQuaternion(j),qe.copy(e.target).add(n),e.object.matrixAutoUpdate||e.object.updateMatrix(),e.object.lookAt(e.target),e.enableDamping===!0?(p.theta*=1-e.dampingFactor,p.phi*=1-e.dampingFactor,S.multiplyScalar(1-e.dampingFactor)):(p.set(0,0,0),S.set(0,0,0));let oe=!1;if(e.zoomToCursor&&h){let se=null;if(e.object instanceof _e&&e.object.isPerspectiveCamera){const ae=n.length();se=ye(ae*w);const de=ae-se;e.object.position.addScaledVector(I,de),e.object.updateMatrixWorld()}else if(e.object.isOrthographicCamera){const ae=new T(N.x,N.y,0);ae.unproject(e.object),e.object.zoom=Math.max(e.minZoom,Math.min(e.maxZoom,e.object.zoom/w)),e.object.updateProjectionMatrix(),oe=!0;const de=new T(N.x,N.y,0);de.unproject(e.object),e.object.position.sub(de).add(ae),e.object.updateMatrixWorld(),se=n.length()}else console.warn("WARNING: OrbitControls.js encountered an unknown camera type - zoom to cursor disabled."),e.zoomToCursor=!1;se!==null&&(e.screenSpacePanning?e.target.set(0,0,-1).transformDirection(e.object.matrix).multiplyScalar(se).add(e.object.position):(fe.origin.copy(e.object.position),fe.direction.set(0,0,-1).transformDirection(e.object.matrix),Math.abs(e.object.up.dot(fe.direction))<dn?t.lookAt(e.target):(ot.setFromNormalAndCoplanarPoint(e.object.up,e.target),fe.intersectPlane(ot,e.target))))}else e.object instanceof Se&&e.object.isOrthographicCamera&&(oe=w!==1,oe&&(e.object.zoom=Math.max(e.minZoom,Math.min(e.maxZoom,e.object.zoom/w)),e.object.updateProjectionMatrix()));return w=1,h=!1,oe||D.distanceToSquared(e.object.position)>A||8*(1-V.dot(e.object.quaternion))>A?(e.dispatchEvent(s),D.copy(e.object.position),V.copy(e.object.quaternion),oe=!1,!0):!1}})(),this.connect=n=>{e.domElement=n,e.domElement.style.touchAction="none",e.domElement.addEventListener("contextmenu",Xe),e.domElement.addEventListener("pointerdown",Ve),e.domElement.addEventListener("pointercancel",ie),e.domElement.addEventListener("wheel",Ze)},this.dispose=()=>{var n,c,b,j,D,V;e.domElement&&(e.domElement.style.touchAction="auto"),(n=e.domElement)==null||n.removeEventListener("contextmenu",Xe),(c=e.domElement)==null||c.removeEventListener("pointerdown",Ve),(b=e.domElement)==null||b.removeEventListener("pointercancel",ie),(j=e.domElement)==null||j.removeEventListener("wheel",Ze),(D=e.domElement)==null||D.ownerDocument.removeEventListener("pointermove",xe),(V=e.domElement)==null||V.ownerDocument.removeEventListener("pointerup",ie),e._domElementKeyEvents!==null&&e._domElementKeyEvents.removeEventListener("keydown",we)};const e=this,s={type:"change"},o={type:"start"},u={type:"end"},a={NONE:-1,ROTATE:0,DOLLY:1,PAN:2,TOUCH_ROTATE:3,TOUCH_PAN:4,TOUCH_DOLLY_PAN:5,TOUCH_DOLLY_ROTATE:6};let d=a.NONE;const A=1e-6,f=new Qe,p=new Qe;let w=1;const S=new T,_=new B,C=new B,L=new B,P=new B,O=new B,g=new B,E=new B,M=new B,x=new B,I=new T,N=new B;let h=!1;const v=[],k={};function le(){return 2*Math.PI/60/60*e.autoRotateSpeed}function H(){return Math.pow(.95,e.zoomSpeed)}function q(n){e.reverseOrbit||e.reverseHorizontalOrbit?p.theta+=n:p.theta-=n}function ze(n){e.reverseOrbit||e.reverseVerticalOrbit?p.phi+=n:p.phi-=n}const Ue=(()=>{const n=new T;return function(b,j){n.setFromMatrixColumn(j,0),n.multiplyScalar(-b),S.add(n)}})(),Re=(()=>{const n=new T;return function(b,j){e.screenSpacePanning===!0?n.setFromMatrixColumn(j,1):(n.setFromMatrixColumn(j,0),n.crossVectors(e.object.up,n)),n.multiplyScalar(b),S.add(n)}})(),Q=(()=>{const n=new T;return function(b,j){const D=e.domElement;if(D&&e.object instanceof _e&&e.object.isPerspectiveCamera){const V=e.object.position;n.copy(V).sub(e.target);let K=n.length();K*=Math.tan(e.object.fov/2*Math.PI/180),Ue(2*b*K/D.clientHeight,e.object.matrix),Re(2*j*K/D.clientHeight,e.object.matrix)}else D&&e.object instanceof Se&&e.object.isOrthographicCamera?(Ue(b*(e.object.right-e.object.left)/e.object.zoom/D.clientWidth,e.object.matrix),Re(j*(e.object.top-e.object.bottom)/e.object.zoom/D.clientHeight,e.object.matrix)):(console.warn("WARNING: OrbitControls.js encountered an unknown camera type - pan disabled."),e.enablePan=!1)}})();function be(n){e.object instanceof _e&&e.object.isPerspectiveCamera||e.object instanceof Se&&e.object.isOrthographicCamera?w=n:(console.warn("WARNING: OrbitControls.js encountered an unknown camera type - dolly/zoom disabled."),e.enableZoom=!1)}function ue(n){be(w/n)}function ve(n){be(w*n)}function Ne(n){if(!e.zoomToCursor||!e.domElement)return;h=!0;const c=e.domElement.getBoundingClientRect(),b=n.clientX-c.left,j=n.clientY-c.top,D=c.width,V=c.height;N.x=b/D*2-1,N.y=-(j/V)*2+1,I.set(N.x,N.y,1).unproject(e.object).sub(e.object.position).normalize()}function ye(n){return Math.max(e.minDistance,Math.min(e.maxDistance,n))}function Ie(n){_.set(n.clientX,n.clientY)}function vt(n){Ne(n),E.set(n.clientX,n.clientY)}function Be(n){P.set(n.clientX,n.clientY)}function yt(n){C.set(n.clientX,n.clientY),L.subVectors(C,_).multiplyScalar(e.rotateSpeed);const c=e.domElement;c&&(q(2*Math.PI*L.x/c.clientHeight),ze(2*Math.PI*L.y/c.clientHeight)),_.copy(C),e.update()}function xt(n){M.set(n.clientX,n.clientY),x.subVectors(M,E),x.y>0?ue(H()):x.y<0&&ve(H()),E.copy(M),e.update()}function wt(n){O.set(n.clientX,n.clientY),g.subVectors(O,P).multiplyScalar(e.panSpeed),Q(g.x,g.y),P.copy(O),e.update()}function Et(n){Ne(n),n.deltaY<0?ve(H()):n.deltaY>0&&ue(H()),e.update()}function St(n){let c=!1;switch(n.code){case e.keys.UP:Q(0,e.keyPanSpeed),c=!0;break;case e.keys.BOTTOM:Q(0,-e.keyPanSpeed),c=!0;break;case e.keys.LEFT:Q(e.keyPanSpeed,0),c=!0;break;case e.keys.RIGHT:Q(-e.keyPanSpeed,0),c=!0;break}c&&(n.preventDefault(),e.update())}function ke(){if(v.length==1)_.set(v[0].pageX,v[0].pageY);else{const n=.5*(v[0].pageX+v[1].pageX),c=.5*(v[0].pageY+v[1].pageY);_.set(n,c)}}function He(){if(v.length==1)P.set(v[0].pageX,v[0].pageY);else{const n=.5*(v[0].pageX+v[1].pageX),c=.5*(v[0].pageY+v[1].pageY);P.set(n,c)}}function Fe(){const n=v[0].pageX-v[1].pageX,c=v[0].pageY-v[1].pageY,b=Math.sqrt(n*n+c*c);E.set(0,b)}function _t(){e.enableZoom&&Fe(),e.enablePan&&He()}function Mt(){e.enableZoom&&Fe(),e.enableRotate&&ke()}function We(n){if(v.length==1)C.set(n.pageX,n.pageY);else{const b=Ee(n),j=.5*(n.pageX+b.x),D=.5*(n.pageY+b.y);C.set(j,D)}L.subVectors(C,_).multiplyScalar(e.rotateSpeed);const c=e.domElement;c&&(q(2*Math.PI*L.x/c.clientHeight),ze(2*Math.PI*L.y/c.clientHeight)),_.copy(C)}function Ge(n){if(v.length==1)O.set(n.pageX,n.pageY);else{const c=Ee(n),b=.5*(n.pageX+c.x),j=.5*(n.pageY+c.y);O.set(b,j)}g.subVectors(O,P).multiplyScalar(e.panSpeed),Q(g.x,g.y),P.copy(O)}function Ye(n){const c=Ee(n),b=n.pageX-c.x,j=n.pageY-c.y,D=Math.sqrt(b*b+j*j);M.set(0,D),x.set(0,Math.pow(M.y/E.y,e.zoomSpeed)),ue(x.y),E.copy(M)}function jt(n){e.enableZoom&&Ye(n),e.enablePan&&Ge(n)}function At(n){e.enableZoom&&Ye(n),e.enableRotate&&We(n)}function Ve(n){var c,b;e.enabled!==!1&&(v.length===0&&((c=e.domElement)==null||c.ownerDocument.addEventListener("pointermove",xe),(b=e.domElement)==null||b.ownerDocument.addEventListener("pointerup",ie)),Tt(n),n.pointerType==="touch"?Ot(n):Lt(n))}function xe(n){e.enabled!==!1&&(n.pointerType==="touch"?Ct(n):Pt(n))}function ie(n){var c,b,j;Dt(n),v.length===0&&((c=e.domElement)==null||c.releasePointerCapture(n.pointerId),(b=e.domElement)==null||b.ownerDocument.removeEventListener("pointermove",xe),(j=e.domElement)==null||j.ownerDocument.removeEventListener("pointerup",ie)),e.dispatchEvent(u),d=a.NONE}function Lt(n){let c;switch(n.button){case 0:c=e.mouseButtons.LEFT;break;case 1:c=e.mouseButtons.MIDDLE;break;case 2:c=e.mouseButtons.RIGHT;break;default:c=-1}switch(c){case J.DOLLY:if(e.enableZoom===!1)return;vt(n),d=a.DOLLY;break;case J.ROTATE:if(n.ctrlKey||n.metaKey||n.shiftKey){if(e.enablePan===!1)return;Be(n),d=a.PAN}else{if(e.enableRotate===!1)return;Ie(n),d=a.ROTATE}break;case J.PAN:if(n.ctrlKey||n.metaKey||n.shiftKey){if(e.enableRotate===!1)return;Ie(n),d=a.ROTATE}else{if(e.enablePan===!1)return;Be(n),d=a.PAN}break;default:d=a.NONE}d!==a.NONE&&e.dispatchEvent(o)}function Pt(n){if(e.enabled!==!1)switch(d){case a.ROTATE:if(e.enableRotate===!1)return;yt(n);break;case a.DOLLY:if(e.enableZoom===!1)return;xt(n);break;case a.PAN:if(e.enablePan===!1)return;wt(n);break}}function Ze(n){e.enabled===!1||e.enableZoom===!1||d!==a.NONE&&d!==a.ROTATE||(n.preventDefault(),e.dispatchEvent(o),Et(n),e.dispatchEvent(u))}function we(n){e.enabled===!1||e.enablePan===!1||St(n)}function Ot(n){switch(Ke(n),v.length){case 1:switch(e.touches.ONE){case ee.ROTATE:if(e.enableRotate===!1)return;ke(),d=a.TOUCH_ROTATE;break;case ee.PAN:if(e.enablePan===!1)return;He(),d=a.TOUCH_PAN;break;default:d=a.NONE}break;case 2:switch(e.touches.TWO){case ee.DOLLY_PAN:if(e.enableZoom===!1&&e.enablePan===!1)return;_t(),d=a.TOUCH_DOLLY_PAN;break;case ee.DOLLY_ROTATE:if(e.enableZoom===!1&&e.enableRotate===!1)return;Mt(),d=a.TOUCH_DOLLY_ROTATE;break;default:d=a.NONE}break;default:d=a.NONE}d!==a.NONE&&e.dispatchEvent(o)}function Ct(n){switch(Ke(n),d){case a.TOUCH_ROTATE:if(e.enableRotate===!1)return;We(n),e.update();break;case a.TOUCH_PAN:if(e.enablePan===!1)return;Ge(n),e.update();break;case a.TOUCH_DOLLY_PAN:if(e.enableZoom===!1&&e.enablePan===!1)return;jt(n),e.update();break;case a.TOUCH_DOLLY_ROTATE:if(e.enableZoom===!1&&e.enableRotate===!1)return;At(n),e.update();break;default:d=a.NONE}}function Xe(n){e.enabled!==!1&&n.preventDefault()}function Tt(n){v.push(n)}function Dt(n){delete k[n.pointerId];for(let c=0;c<v.length;c++)if(v[c].pointerId==n.pointerId){v.splice(c,1);return}}function Ke(n){let c=k[n.pointerId];c===void 0&&(c=new B,k[n.pointerId]=c),c.set(n.pageX,n.pageY)}function Ee(n){const c=n.pointerId===v[0].pointerId?v[1]:v[0];return k[c.pointerId]}this.dollyIn=(n=H())=>{ve(n),e.update()},this.dollyOut=(n=H())=>{ue(n),e.update()},this.getScale=()=>w,this.setScale=n=>{be(n),e.update()},this.getZoomScale=()=>H(),i!==void 0&&this.connect(i),this.update()}};const at=new ge,pe=new T;class Te extends It{constructor(){super(),this.isLineSegmentsGeometry=!0,this.type="LineSegmentsGeometry";const t=[-1,2,0,1,2,0,-1,1,0,1,1,0,-1,0,0,1,0,0,-1,-1,0,1,-1,0],i=[-1,2,1,2,-1,1,1,1,-1,-1,1,-1,-1,-2,1,-2],e=[0,2,1,2,3,1,2,4,3,4,5,3,4,6,5,6,7,5];this.setIndex(e),this.setAttribute("position",new et(t,3)),this.setAttribute("uv",new et(i,2))}applyMatrix4(t){const i=this.attributes.instanceStart,e=this.attributes.instanceEnd;return i!==void 0&&(i.applyMatrix4(t),e.applyMatrix4(t),i.needsUpdate=!0),this.boundingBox!==null&&this.computeBoundingBox(),this.boundingSphere!==null&&this.computeBoundingSphere(),this}setPositions(t){let i;t instanceof Float32Array?i=t:Array.isArray(t)&&(i=new Float32Array(t));const e=new Pe(i,6,1);return this.setAttribute("instanceStart",new te(e,3,0)),this.setAttribute("instanceEnd",new te(e,3,3)),this.computeBoundingBox(),this.computeBoundingSphere(),this}setColors(t,i=3){let e;t instanceof Float32Array?e=t:Array.isArray(t)&&(e=new Float32Array(t));const s=new Pe(e,i*2,1);return this.setAttribute("instanceColorStart",new te(s,i,0)),this.setAttribute("instanceColorEnd",new te(s,i,i)),this}fromWireframeGeometry(t){return this.setPositions(t.attributes.position.array),this}fromEdgesGeometry(t){return this.setPositions(t.attributes.position.array),this}fromMesh(t){return this.fromWireframeGeometry(new Bt(t.geometry)),this}fromLineSegments(t){const i=t.geometry;return this.setPositions(i.attributes.position.array),this}computeBoundingBox(){this.boundingBox===null&&(this.boundingBox=new ge);const t=this.attributes.instanceStart,i=this.attributes.instanceEnd;t!==void 0&&i!==void 0&&(this.boundingBox.setFromBufferAttribute(t),at.setFromBufferAttribute(i),this.boundingBox.union(at))}computeBoundingSphere(){this.boundingSphere===null&&(this.boundingSphere=new dt),this.boundingBox===null&&this.computeBoundingBox();const t=this.attributes.instanceStart,i=this.attributes.instanceEnd;if(t!==void 0&&i!==void 0){const e=this.boundingSphere.center;this.boundingBox.getCenter(e);let s=0;for(let o=0,u=t.count;o<u;o++)pe.fromBufferAttribute(t,o),s=Math.max(s,e.distanceToSquared(pe)),pe.fromBufferAttribute(i,o),s=Math.max(s,e.distanceToSquared(pe));this.boundingSphere.radius=Math.sqrt(s),isNaN(this.boundingSphere.radius)&&console.error("THREE.LineSegmentsGeometry.computeBoundingSphere(): Computed radius is NaN. The instanced position data is likely to have NaN values.",this)}}toJSON(){}applyMatrix(t){return console.warn("THREE.LineSegmentsGeometry: applyMatrix() has been renamed to applyMatrix4()."),this.applyMatrix4(t)}}class gt extends Te{constructor(){super(),this.isLineGeometry=!0,this.type="LineGeometry"}setPositions(t){const i=t.length-3,e=new Float32Array(2*i);for(let s=0;s<i;s+=3)e[2*s]=t[s],e[2*s+1]=t[s+1],e[2*s+2]=t[s+2],e[2*s+3]=t[s+3],e[2*s+4]=t[s+4],e[2*s+5]=t[s+5];return super.setPositions(e),this}setColors(t,i=3){const e=t.length-i,s=new Float32Array(2*e);if(i===3)for(let o=0;o<e;o+=i)s[2*o]=t[o],s[2*o+1]=t[o+1],s[2*o+2]=t[o+2],s[2*o+3]=t[o+3],s[2*o+4]=t[o+4],s[2*o+5]=t[o+5];else for(let o=0;o<e;o+=i)s[2*o]=t[o],s[2*o+1]=t[o+1],s[2*o+2]=t[o+2],s[2*o+3]=t[o+3],s[2*o+4]=t[o+4],s[2*o+5]=t[o+5],s[2*o+6]=t[o+6],s[2*o+7]=t[o+7];return super.setColors(s,i),this}fromLine(t){const i=t.geometry;return this.setPositions(i.attributes.position.array),this}}class De extends kt{constructor(t){super({type:"LineMaterial",uniforms:tt.clone(tt.merge([nt.common,nt.fog,{worldUnits:{value:1},linewidth:{value:1},resolution:{value:new B(1,1)},dashOffset:{value:0},dashScale:{value:1},dashSize:{value:1},gapSize:{value:1}}])),vertexShader:`
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
					#include <${ft>=154?"colorspace_fragment":"encodings_fragment"}>
					#include <fog_fragment>
					#include <premultiplied_alpha_fragment>

				}
			`,clipping:!0}),this.isLineMaterial=!0,this.onBeforeCompile=function(){this.transparent?this.defines.USE_LINE_COLOR_ALPHA="1":delete this.defines.USE_LINE_COLOR_ALPHA},Object.defineProperties(this,{color:{enumerable:!0,get:function(){return this.uniforms.diffuse.value},set:function(i){this.uniforms.diffuse.value=i}},worldUnits:{enumerable:!0,get:function(){return"WORLD_UNITS"in this.defines},set:function(i){i===!0?this.defines.WORLD_UNITS="":delete this.defines.WORLD_UNITS}},linewidth:{enumerable:!0,get:function(){return this.uniforms.linewidth.value},set:function(i){this.uniforms.linewidth.value=i}},dashed:{enumerable:!0,get:function(){return"USE_DASH"in this.defines},set(i){!!i!="USE_DASH"in this.defines&&(this.needsUpdate=!0),i===!0?this.defines.USE_DASH="":delete this.defines.USE_DASH}},dashScale:{enumerable:!0,get:function(){return this.uniforms.dashScale.value},set:function(i){this.uniforms.dashScale.value=i}},dashSize:{enumerable:!0,get:function(){return this.uniforms.dashSize.value},set:function(i){this.uniforms.dashSize.value=i}},dashOffset:{enumerable:!0,get:function(){return this.uniforms.dashOffset.value},set:function(i){this.uniforms.dashOffset.value=i}},gapSize:{enumerable:!0,get:function(){return this.uniforms.gapSize.value},set:function(i){this.uniforms.gapSize.value=i}},opacity:{enumerable:!0,get:function(){return this.uniforms.opacity.value},set:function(i){this.uniforms.opacity.value=i}},resolution:{enumerable:!0,get:function(){return this.uniforms.resolution.value},set:function(i){this.uniforms.resolution.value.copy(i)}},alphaToCoverage:{enumerable:!0,get:function(){return"USE_ALPHA_TO_COVERAGE"in this.defines},set:function(i){!!i!="USE_ALPHA_TO_COVERAGE"in this.defines&&(this.needsUpdate=!0),i===!0?(this.defines.USE_ALPHA_TO_COVERAGE="",this.extensions.derivatives=!0):(delete this.defines.USE_ALPHA_TO_COVERAGE,this.extensions.derivatives=!1)}}}),this.setValues(t)}}const Me=new ne,rt=new T,ct=new T,z=new ne,U=new ne,F=new ne,je=new T,Ae=new Wt,R=new Ft,lt=new T,me=new ge,he=new dt,W=new ne;let Y,$;function ut(r,t,i){return W.set(0,0,-t,1).applyMatrix4(r.projectionMatrix),W.multiplyScalar(1/W.w),W.x=$/i.width,W.y=$/i.height,W.applyMatrix4(r.projectionMatrixInverse),W.multiplyScalar(1/W.w),Math.abs(Math.max(W.x,W.y))}function pn(r,t){const i=r.matrixWorld,e=r.geometry,s=e.attributes.instanceStart,o=e.attributes.instanceEnd,u=Math.min(e.instanceCount,s.count);for(let a=0,d=u;a<d;a++){R.start.fromBufferAttribute(s,a),R.end.fromBufferAttribute(o,a),R.applyMatrix4(i);const A=new T,f=new T;Y.distanceSqToSegment(R.start,R.end,f,A),f.distanceTo(A)<$*.5&&t.push({point:f,pointOnLine:A,distance:Y.origin.distanceTo(f),object:r,face:null,faceIndex:a,uv:null,[ht]:null})}}function mn(r,t,i){const e=t.projectionMatrix,o=r.material.resolution,u=r.matrixWorld,a=r.geometry,d=a.attributes.instanceStart,A=a.attributes.instanceEnd,f=Math.min(a.instanceCount,d.count),p=-t.near;Y.at(1,F),F.w=1,F.applyMatrix4(t.matrixWorldInverse),F.applyMatrix4(e),F.multiplyScalar(1/F.w),F.x*=o.x/2,F.y*=o.y/2,F.z=0,je.copy(F),Ae.multiplyMatrices(t.matrixWorldInverse,u);for(let w=0,S=f;w<S;w++){if(z.fromBufferAttribute(d,w),U.fromBufferAttribute(A,w),z.w=1,U.w=1,z.applyMatrix4(Ae),U.applyMatrix4(Ae),z.z>p&&U.z>p)continue;if(z.z>p){const g=z.z-U.z,E=(z.z-p)/g;z.lerp(U,E)}else if(U.z>p){const g=U.z-z.z,E=(U.z-p)/g;U.lerp(z,E)}z.applyMatrix4(e),U.applyMatrix4(e),z.multiplyScalar(1/z.w),U.multiplyScalar(1/U.w),z.x*=o.x/2,z.y*=o.y/2,U.x*=o.x/2,U.y*=o.y/2,R.start.copy(z),R.start.z=0,R.end.copy(U),R.end.z=0;const C=R.closestPointToPointParameter(je,!0);R.at(C,lt);const L=Gt.lerp(z.z,U.z,C),P=L>=-1&&L<=1,O=je.distanceTo(lt)<$*.5;if(P&&O){R.start.fromBufferAttribute(d,w),R.end.fromBufferAttribute(A,w),R.start.applyMatrix4(u),R.end.applyMatrix4(u);const g=new T,E=new T;Y.distanceSqToSegment(R.start,R.end,E,g),i.push({point:E,pointOnLine:g,distance:Y.origin.distanceTo(E),object:r,face:null,faceIndex:w,uv:null,[ht]:null})}}}class bt extends Ht{constructor(t=new Te,i=new De({color:Math.random()*16777215})){super(t,i),this.isLineSegments2=!0,this.type="LineSegments2"}computeLineDistances(){const t=this.geometry,i=t.attributes.instanceStart,e=t.attributes.instanceEnd,s=new Float32Array(2*i.count);for(let u=0,a=0,d=i.count;u<d;u++,a+=2)rt.fromBufferAttribute(i,u),ct.fromBufferAttribute(e,u),s[a]=a===0?0:s[a-1],s[a+1]=s[a]+rt.distanceTo(ct);const o=new Pe(s,2,1);return t.setAttribute("instanceDistanceStart",new te(o,1,0)),t.setAttribute("instanceDistanceEnd",new te(o,1,1)),this}raycast(t,i){const e=this.material.worldUnits,s=t.camera;s===null&&!e&&console.error('LineSegments2: "Raycaster.camera" needs to be set in order to raycast against LineSegments2 while worldUnits is set to false.');const o=t.params.Line2!==void 0&&t.params.Line2.threshold||0;Y=t.ray;const u=this.matrixWorld,a=this.geometry,d=this.material;$=d.linewidth+o,a.boundingSphere===null&&a.computeBoundingSphere(),he.copy(a.boundingSphere).applyMatrix4(u);let A;if(e)A=$*.5;else{const p=Math.max(s.near,he.distanceToPoint(Y.origin));A=ut(s,p,d.resolution)}if(he.radius+=A,Y.intersectsSphere(he)===!1)return;a.boundingBox===null&&a.computeBoundingBox(),me.copy(a.boundingBox).applyMatrix4(u);let f;if(e)f=$*.5;else{const p=Math.max(s.near,me.distanceToPoint(Y.origin));f=ut(s,p,d.resolution)}me.expandByScalar(f),Y.intersectsBox(me)!==!1&&(e?pn(this,i):mn(this,s,i))}onBeforeRender(t){const i=this.material.uniforms;i&&i.resolution&&(t.getViewport(Me),this.material.uniforms.resolution.value.set(Me.z,Me.w))}}class hn extends bt{constructor(t=new gt,i=new De({color:Math.random()*16777215})){super(t,i),this.isLine2=!0,this.type="Line2"}}const gn=y.forwardRef(function({points:t,color:i=16777215,vertexColors:e,linewidth:s,lineWidth:o,segments:u,dashed:a,...d},A){var f,p;const w=G(P=>P.size),S=y.useMemo(()=>u?new bt:new hn,[u]),[_]=y.useState(()=>new De),C=(e==null||(f=e[0])==null?void 0:f.length)===4?4:3,L=y.useMemo(()=>{const P=u?new Te:new gt,O=t.map(g=>{const E=Array.isArray(g);return g instanceof T||g instanceof ne?[g.x,g.y,g.z]:g instanceof B?[g.x,g.y,0]:E&&g.length===3?[g[0],g[1],g[2]]:E&&g.length===2?[g[0],g[1],0]:g});if(P.setPositions(O.flat()),e){i=16777215;const g=e.map(E=>E instanceof re?E.toArray():E);P.setColors(g.flat(),C)}return P},[t,u,e,C]);return y.useLayoutEffect(()=>{S.computeLineDistances()},[t,S]),y.useLayoutEffect(()=>{a?_.defines.USE_DASH="":delete _.defines.USE_DASH,_.needsUpdate=!0},[a,_]),y.useEffect(()=>()=>{L.dispose(),_.dispose()},[L]),y.createElement("primitive",Oe({object:S,ref:A},d),y.createElement("primitive",{object:L,attach:"geometry"}),y.createElement("primitive",Oe({object:_,attach:"material",color:i,vertexColors:!!e,resolution:[w.width,w.height],linewidth:(p=s??o)!==null&&p!==void 0?p:1,dashed:a,transparent:C===4},d)))}),bn=y.forwardRef(({makeDefault:r,camera:t,regress:i,domElement:e,enableDamping:s=!0,keyEvents:o=!1,onChange:u,onStart:a,onEnd:d,...A},f)=>{const p=G(x=>x.invalidate),w=G(x=>x.camera),S=G(x=>x.gl),_=G(x=>x.events),C=G(x=>x.setEvents),L=G(x=>x.set),P=G(x=>x.get),O=G(x=>x.performance),g=t||w,E=e||_.connected||S.domElement,M=y.useMemo(()=>new fn(g),[g]);return pt(()=>{M.enabled&&M.update()},-1),y.useEffect(()=>(o&&M.connect(o===!0?E:o),M.connect(E),()=>void M.dispose()),[o,E,i,M,p]),y.useEffect(()=>{const x=h=>{p(),i&&O.regress(),u&&u(h)},I=h=>{a&&a(h)},N=h=>{d&&d(h)};return M.addEventListener("change",x),M.addEventListener("start",I),M.addEventListener("end",N),()=>{M.removeEventListener("start",I),M.removeEventListener("end",N),M.removeEventListener("change",x)}},[u,a,d,M,p,C]),y.useEffect(()=>{if(r){const x=P().controls;return L({controls:M}),()=>L({controls:x})}},[r,M]),y.createElement("primitive",Oe({ref:f,object:M,enableDamping:s},A))});function vn(r,t){const[i,e]=y.useState(0),s=y.useRef(0);return y.useEffect(()=>{const o=r.current;if(!o)return;let u=null,a=!1,d=!1,A=0,f=0,p;const w=()=>{!a&&!d&&(d=!0,e(P=>P+1))},S=P=>{if(P.preventDefault(),!a){if(++s.current>2){t();return}p=setTimeout(w,250)}},_=()=>{clearTimeout(p),w()},C=()=>{if(u=o.querySelector("canvas"),!u){++f<120&&(A=requestAnimationFrame(C));return}u.addEventListener("webglcontextlost",S),u.addEventListener("webglcontextrestored",_)};C();const L=setTimeout(()=>{s.current=0},6e4);return()=>{var P;if(a=!0,cancelAnimationFrame(A),clearTimeout(p),clearTimeout(L),!!u){u.removeEventListener("webglcontextlost",S),u.removeEventListener("webglcontextrestored",_);try{const O=u.getContext("webgl2")??u.getContext("webgl");(P=O==null?void 0:O.getExtension("WEBGL_lose_context"))==null||P.loseContext()}catch{}}}},[i,r,t]),i}function yn(r){const[t,i,e]=r.trim().split(/\s+/).map(parseFloat);return new re().setHSL((t||0)/360,(i||0)/100,(e||0)/100,Yt)}function xn({position:r,accent:t,artifacts:i,publications:e}){const{scene:s}=Vt(Zt),o=Ce(),{instance:u,scale:a,offset:d}=y.useMemo(()=>{const A=s.clone(!0),f=new ge().setFromObject(A),p=f.getCenter(new T),w=f.getSize(new T),S=2.6/Math.max(w.x,w.y,w.z,.01);return{instance:A,scale:S,offset:[-p.x*S,-f.min.y*S,-p.z*S]}},[s]);return l.jsxs("group",{position:r,children:[l.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,.025,0],children:[l.jsx("ringGeometry",{args:[1.5,1.56,40]}),l.jsx("meshBasicMaterial",{color:t,transparent:!0,opacity:.4})]}),l.jsx("group",{position:d,children:l.jsx("primitive",{object:u,scale:a,dispose:null})}),l.jsx(mt,{position:[0,3.15,0],center:!0,style:{pointerEvents:"none"},children:l.jsxs("div",{className:"swarm-world-label swarm-memory-label",children:[l.jsx("strong",{children:o("memoryHouse")}),l.jsxs("span",{children:[ce(i)," ",o("artifactCount")," · ",ce(e)," ",o("publicationCount")]})]})})]})}const wn={lead:{contract:1,archetype:"biped",base:"mage",parts:{}},coordinator:{contract:1,archetype:"biped",base:"knight",parts:{}},worker:{contract:1,archetype:"biped",base:"rogue",parts:{}}};function Le(r,t){const i=getComputedStyle(t).getPropertyValue(`--${r}`).trim();return yn(i)}class En extends y.Component{constructor(){super(...arguments);$e(this,"state",{failed:!1})}static getDerivedStateFromError(){return{failed:!0}}componentDidCatch(i){console.warn("Swarm 3D scene unavailable",i),this.props.onError()}render(){return this.state.failed?null:this.props.children}}const Sn=y.memo(function({node:t,paused:i,selected:e,onSelect:s,accent:o,surface:u}){var _,C,L,P,O,g;const a=Ce(),d=y.useRef(null),A=y.useRef(t.position),f=y.useRef({mode:"idle",speed:0}),p=y.useRef(!1),[w,,S]=t.position;return y.useEffect(()=>{var E;f.current.mode=!i&&((E=t.agent)==null?void 0:E.state)==="running"?"work":"idle"},[(_=t.agent)==null?void 0:_.state,i]),pt((E,M)=>{var N;if(!d.current)return;const x=d.current.position,I=Math.hypot(w-x.x,S-x.z);if(I>.04&&!i){const h=Math.min(I,M*4);x.x+=(w-x.x)/I*h,x.z+=(S-x.z)/I*h,d.current.rotation.y=Math.atan2(w-x.x,S-x.z),f.current={mode:"walk",speed:4},p.current=!0}else x.set(w,0,S),p.current&&(f.current={mode:!i&&((N=t.agent)==null?void 0:N.state)==="running"?"work":"idle",speed:0},p.current=!1)}),l.jsxs("group",{ref:d,position:A.current,onClick:E=>{var M;E.stopPropagation(),s(((M=t.agent)==null?void 0:M.id)??t.id,t.group)},children:[l.jsxs("mesh",{position:[0,.06,0],children:[l.jsx("cylinderGeometry",{args:[t.group?1.05:.75,t.group?1.05:.75,.12,24]}),l.jsx("meshStandardMaterial",{color:u,roughness:1})]}),l.jsxs("mesh",{position:[0,.13,0],rotation:[-Math.PI/2,0,0],children:[l.jsx("ringGeometry",{args:[t.group?.98:.69,t.group?1.05:.76,32]}),l.jsx("meshBasicMaterial",{color:o,transparent:!0,opacity:e?1:.35})]}),l.jsx("group",{position:[0,.13,0],children:l.jsx($t,{recipe:wn[((C=t.agent)==null?void 0:C.role)??"coordinator"],heightM:1.8,drive:f,paused:i})}),((L=t.agent)==null?void 0:L.tool_activity)&&l.jsxs("mesh",{position:[1,.7,0],children:[l.jsx("boxGeometry",{args:[.4,.7,.25]}),l.jsx("meshStandardMaterial",{color:o})]}),l.jsx(mt,{position:[0,2.45,0],center:!0,style:{pointerEvents:"none"},children:l.jsxs("div",{className:"swarm-world-label","data-selected":e,children:[l.jsx("strong",{children:t.title}),l.jsx("span",{children:t.group?`${ce(t.count??"0")} ${a("agents")}`:`${a(((P=t.agent)==null?void 0:P.role)??"worker")} · ${a(((O=t.agent)==null?void 0:O.state)??"idle")}`}),e&&l.jsxs("span",{children:[a("level")," ",t.level,(g=t.agent)!=null&&g.tool_activity?` · ${t.agent.tool_activity}`:""]})]})})]})});function _n({camera:r,radius:t,onChange:i}){const{camera:e,invalidate:s}=G(),o=y.useRef(null);y.useEffect(()=>{e.position.set(r.focus[0]+Math.cos(r.yaw)*t/r.zoom,t*(r.elevation??.9)/r.zoom,r.focus[1]+Math.sin(r.yaw)*t/r.zoom),e.lookAt(r.focus[0],.6,r.focus[1]),e.updateProjectionMatrix(),s()},[r,t,e,s]);const u=()=>{var p;const a=(p=o.current)==null?void 0:p.target;if(!a)return;const d=e.position.x-a.x,A=e.position.z-a.z,f=Math.max(.1,Math.hypot(d,A));i({yaw:Math.atan2(A,d),zoom:Math.max(.4,Math.min(3,t/f)),focus:[a.x,a.z],elevation:Math.max(.1,Math.min(99,e.position.y/f))})};return l.jsx(bn,{ref:o,makeDefault:!0,target:[r.focus[0],.6,r.focus[1]],minDistance:5,maxDistance:180,maxPolarAngle:Math.PI/2.1,screenSpacePanning:!1,enableDamping:!1,onEnd:u})}function zn({snapshot:r,selected:t,onSelect:i,onUnavailable:e,live:s,onInspect:o}){const u=Ce(),a=y.useRef(null),d=qt(a),A=Xt()??!1,f=vn(a,e),[p,w]=y.useState(!1),[S,_]=y.useState(()=>Qt(r.team.id)),C=y.useCallback(h=>_(h),[]),[L,P]=y.useState(()=>({accent:new re,surface:new re,background:new re})),O=y.useMemo(()=>Jt(r),[r]),g=en(O),E=tn(O),M=r.counts.artifacts??"",x=r.counts.publications??"",I=!d||!s||A||p||r.team.state!=="running";y.useEffect(()=>{nn(r.team.id,S)},[r.team.id,S]),y.useEffect(()=>{const h=a.current;if(!h)return;const v=()=>P({accent:Le("primary",h),surface:Le("muted",h),background:Le("background",h)});v();const k=new MutationObserver(v);return k.observe(document.documentElement,{attributes:!0,attributeFilter:["class","style","data-theme"]}),()=>k.disconnect()},[]);const N=y.useMemo(()=>{const h=new Map(O.map(v=>[v.id,v.position]));return r.activity.flatMap(v=>{const k=v.agent_id&&h.get(v.agent_id),le=v.data.recipients;return!k||!Array.isArray(le)?[]:le.slice(0,16).flatMap(H=>{const q=h.get(String(H));return q?[{id:`${v.id}:${H}`,points:[[k[0],.2,k[2]],[q[0],.2,q[2]]]}]:[]})}).slice(-32)},[O,r.activity]);return l.jsxs("div",{className:"swarm-world",ref:a,"data-team-id":r.team.id,children:[l.jsx(En,{onError:e,children:l.jsx(y.Suspense,{fallback:l.jsx("div",{className:"swarm-empty",children:u("loading")}),children:l.jsxs(Kt,{dpr:p?1:[1,1.5],frameloop:I?"demand":"always",camera:{fov:45,near:.1,far:500},gl:{antialias:!p,powerPreference:"low-power"},children:[l.jsx("color",{attach:"background",args:[L.background]}),l.jsx("ambientLight",{intensity:1.05}),l.jsx("directionalLight",{position:[12,25,8],intensity:2}),l.jsx(_n,{camera:S,radius:g,onChange:C}),l.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.03,0],children:[l.jsx("circleGeometry",{args:[g*.9,64]}),l.jsx("meshStandardMaterial",{color:L.surface,roughness:1})]}),l.jsxs("mesh",{rotation:[-Math.PI/2,0,0],position:[0,-.02,0],children:[l.jsx("ringGeometry",{args:[g*.9-.045,g*.9,64]}),l.jsx("meshBasicMaterial",{color:L.accent,transparent:!0,opacity:.18})]}),l.jsx(y.Suspense,{fallback:null,children:l.jsx(xn,{position:E,accent:L.accent,artifacts:M,publications:x})}),N.map(h=>l.jsx(gn,{points:h.points,color:L.accent,lineWidth:1.5,dashed:!0},h.id)),O.map((h,v)=>l.jsx(Sn,{node:h,paused:I||v>=32,selected:t===h.id,onSelect:i,accent:L.accent,surface:L.surface},h.id))]},f)})}),l.jsxs("div",{className:"swarm-world-controls",children:[l.jsxs("div",{role:"group","aria-label":u("camera"),children:[l.jsx("button",{"aria-label":u("rotateLeft"),onClick:()=>_(h=>({...h,yaw:h.yaw-.3})),children:"↶"}),l.jsx("button",{"aria-label":u("rotateRight"),onClick:()=>_(h=>({...h,yaw:h.yaw+.3})),children:"↷"}),l.jsx("button",{"aria-label":u("zoomIn"),onClick:()=>_(h=>({...h,zoom:Math.min(3,h.zoom*1.2)})),children:"+"}),l.jsx("button",{"aria-label":u("zoomOut"),onClick:()=>_(h=>({...h,zoom:Math.max(.4,h.zoom/1.2)})),children:"−"}),l.jsx("button",{onClick:()=>_({...it,focus:[0,0]}),children:u("resetCamera")})]}),l.jsxs("label",{className:"swarm-check",children:[l.jsx("input",{type:"checkbox",checked:p,onChange:h=>w(h.target.checked)}),u("lowPower")]})]}),l.jsxs("div",{className:"swarm-world-memory",children:[l.jsx("strong",{children:u("memoryHouse")}),l.jsxs("div",{children:[l.jsxs("button",{onClick:()=>o==null?void 0:o("artifacts"),disabled:!o,children:[ce(M)," ",u("artifactCount")]}),l.jsxs("button",{onClick:()=>o==null?void 0:o("publications"),disabled:!o,children:[ce(x)," ",u("publicationCount")]})]})]}),l.jsxs("div",{className:"swarm-minimap","aria-label":u("minimap"),children:[l.jsxs("svg",{viewBox:"-50 -50 100 100",role:"img","aria-label":u("minimap"),children:[l.jsx("circle",{r:"45",fill:"none",stroke:"currentColor",opacity:".15"}),l.jsx("rect",{x:E[0]/g*65-2.5,y:E[2]/g*65-2.5,width:"5",height:"5",fill:"none",stroke:"var(--swarm-accent)",children:l.jsx("title",{children:u("memoryHouse")})}),O.map(h=>l.jsx("circle",{cx:h.position[0]/g*65,cy:h.position[2]/g*65,r:h.id===t?3:1.8,fill:"var(--swarm-accent)"},h.id))]}),l.jsx("button",{onClick:()=>_({...it,focus:[0,0]}),children:u("overview")}),on(r.team.state)&&l.jsx("span",{className:"swarm-muted",children:u("finalWorld")})]})]})}export{zn as default};
