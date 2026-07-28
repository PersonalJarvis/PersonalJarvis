import{c as i,u as c,a as s,j as r,e as d}from"./index-BCyFl0L1.js";/**
 * @license lucide-react v0.445.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const m=i("Shield",[["path",{d:"M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",key:"oel41y"}]]);function x({tabs:a}){const t=c(),o=s(e=>e.activeSection),n=s(e=>e.setActiveSection);return r.jsx("div",{className:"flex items-center gap-6 border-b border-border px-6",children:a.map(e=>r.jsx(u,{label:t(e.labelKey),active:o===e.id,onClick:()=>n(e.id)},e.id))})}function u({label:a,active:t,onClick:o}){return r.jsxs("button",{type:"button",onClick:o,"aria-current":t?"page":void 0,className:d("relative py-3 text-sm font-medium transition-colors",t?"text-foreground":"text-muted-foreground hover:text-foreground"),children:[a,t&&r.jsx("span",{"aria-hidden":!0,className:"absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary shadow-[0_0_8px_rgba(255,214,10,0.6)]"})]})}export{m as S,x as a};
