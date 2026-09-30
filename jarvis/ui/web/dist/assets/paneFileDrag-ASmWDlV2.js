import{k as c,r as t,aW as p,b2 as n}from"./index-M0INqnws.js";/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const l=c("ArrowUp",[["path",{d:"m5 12 7-7 7 7",key:"hav0vg"}],["path",{d:"M12 19V5",key:"x0mq9r"}]]);/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const i=c("Paperclip",[["path",{d:"m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48",key:"1u3ebp"}]]);function g(u){const[s,e]=t.useState(!1),a=t.useRef(0),o=t.useCallback(()=>{a.current=0,e(!1)},[]);return p(s,o),{dragging:s,handlers:{onDragEnter:r=>{r.preventDefault(),n(r.dataTransfer)&&(a.current+=1,e(!0))},onDragOver:r=>{r.preventDefault(),r.dataTransfer.dropEffect=n(r.dataTransfer)?"copy":"none"},onDragLeave:()=>{a.current=Math.max(0,a.current-1),a.current===0&&e(!1)},onDrop:r=>{r.preventDefault(),o(),n(r.dataTransfer)&&u(r.dataTransfer)}}}}export{l as A,i as P,g as u};
