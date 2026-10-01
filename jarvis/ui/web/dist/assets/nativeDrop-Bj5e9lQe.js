import{o as f,r as i,aM as p,aV as u}from"./index-rzMldg_f.js";/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const D=f("ArrowUp",[["path",{d:"m5 12 7-7 7 7",key:"hav0vg"}],["path",{d:"M12 19V5",key:"x0mq9r"}]]);/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const E=f("Paperclip",[["path",{d:"m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48",key:"1u3ebp"}]]);function g(n){const[s,t]=i.useState(!1),r=i.useRef(0),a=i.useCallback(()=>{r.current=0,t(!1)},[]);return p(s,a),{dragging:s,handlers:{onDragEnter:e=>{e.preventDefault(),u(e.dataTransfer)&&(r.current+=1,t(!0))},onDragOver:e=>{e.preventDefault(),e.dataTransfer.dropEffect=u(e.dataTransfer)?"copy":"none"},onDragLeave:()=>{r.current=Math.max(0,r.current-1),r.current===0&&t(!1)},onDrop:e=>{e.preventDefault(),a(),u(e.dataTransfer)&&n(e.dataTransfer)}}}}const c="jarvis-native-drop",m=2e3;function w(){const n=window;return!!(n.__JARVIS_EMBEDDED_DESKTOP||n.pywebview)}function h(n={}){if(!w())return Promise.resolve(null);const s=n.timeoutMs??m;return new Promise(t=>{let r;const a=l=>{const e=l.detail,d=Array.isArray(e==null?void 0:e.paths)?e.paths:[],o=Array.isArray(e==null?void 0:e.names)?e.names:[];d.length!==0&&(n.name&&o.length>0&&!o.includes(n.name)||(window.removeEventListener(c,a),r!==void 0&&window.clearTimeout(r),t({paths:d,names:o})))};window.addEventListener(c,a),r=window.setTimeout(()=>{window.removeEventListener(c,a),t(null)},s)})}export{D as A,E as P,w as i,g as u,h as w};
