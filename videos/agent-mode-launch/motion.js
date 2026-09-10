// T1: film-only choreography. A single native fixture replaces retimed footage.
(() => {
 'use strict';
 window.__timelines=window.__timelines||{};
 const product=document.getElementById('product'), stage=document.getElementById('native-stage');
 const world=document.getElementById('native-world'),pointer=document.getElementById('pointer'),ring=document.getElementById('click-ring');
 const cam={x:0,y:0,s:1},clock={t:0},travel=[],cameraPlan=[];
 let ready=false;
 const tl=gsap.timeline({paused:true});
 const mapping=[[0,0],[8,1.8],[8.8,2],[9.6,2.3],[10,2.31],[12.35,3.5],[12.8,3.6],[13.2,3.86],[13.8,4],[15.1,4.9],[20.6,6.85],[37.8,7],[38.1,7.2],[42.8,11],[47.9,14.9],[48.6,15],[49.3,15.3],[49.5,15.31],[52.5,16.95],[52.7,17],[53.3,17.45],[54.2,18],[61.7,20.85],[74.4,21],[79.8,25.8],[80.1,26],[85,27.9]];
 function sourceAt(t){for(let i=1;i<mapping.length;i++){const[b,v]=mapping[i];if(t<=b){const[a,u]=mapping[i-1];return u+(v-u)*(t-a)/(b-a);}}return 27.9;}
 function from(el,at,o={}){tl.fromTo(el,{opacity:0,x:o.x??0,y:o.y??32,rotation:o.rotation??0,scale:o.scale??1},{opacity:1,x:0,y:0,rotation:0,scale:1,duration:o.duration??.6,ease:'power3.out'},at);}
 function crop(at,x,y,width,duration=.75){cameraPlan.push({at,x,y,width,duration});}
 function pathDraw(selector,at,duration){const el=document.querySelector(selector),len=el.getTotalLength();tl.fromTo(el,{strokeDasharray:len,strokeDashoffset:len},{strokeDashoffset:0,duration,ease:'power2.inOut'},at);}
 function packet(pathId,circleId,at,duration){const path=document.querySelector(pathId),circle=document.querySelector(circleId),proxy={p:0},length=path.getTotalLength();tl.set(circle,{opacity:0},0);tl.set(circle,{opacity:1},at);tl.to(proxy,{p:1,duration,ease:'power1.inOut',onUpdate:()=>{const p=path.getPointAtLength(proxy.p*length);circle.setAttribute('cx',p.x);circle.setAttribute('cy',p.y);}},at);tl.to(circle,{opacity:0,duration:.15},at+duration);}

 from('#intro-label',0,{duration:.45});
 tl.fromTo('.opening-word',{y:135,opacity:0,rotation:3},{y:0,opacity:1,rotation:0,stagger:.2,duration:.7,ease:'power4.out'},.3);
 tl.fromTo('#task-strip span',{x:160,opacity:0},{x:0,opacity:1,duration:.6,stagger:.35,ease:'power3.out'},2.4);
 tl.fromTo('#opening-rule',{scaleX:0},{scaleX:1,duration:.7,ease:'power3.inOut'},4);
 tl.to('.opening-scene',{scale:1.07,x:-40,duration:.75,ease:'power3.inOut'},6.5);
 tl.to('#intro',{clipPath:'inset(0 0 100% 0)',duration:.65,ease:'power3.inOut'},7.75);

 tl.set(stage,{opacity:0},0);tl.to(stage,{opacity:1,duration:.45},7.8);
 tl.set(cam,{x:-380,y:-560,s:1.55},0);
 crop(8.25,925,115,515,.65);crop(9.35,315,890,785,.72);
 crop(13,315,190,785,.78);crop(17.5,265,145,910,.8);
 tl.to(stage,{opacity:0,scale:.84,duration:.7,ease:'power3.inOut'},20.6);
 tl.fromTo('#handoff',{opacity:0},{opacity:1,duration:.4,ease:'none'},20.6);

 from('#graph-title',21.15);from('#brief-node',20.9,{scale:1.4,y:160,duration:.8});
 tl.to('#brief-node',{y:-100,scale:.88,duration:.8,ease:'power3.inOut'},23);
 pathDraw('#path-left',23.25,1);pathDraw('#path-right',23.42,1);
 from('#scout-node',23.3,{x:280,y:-100,scale:.7,duration:.85});
 from('#archivist-node',23.45,{x:-280,y:-100,scale:.7,duration:.85});
 packet('#path-left','#packet-left',23.4,1);packet('#path-right','#packet-right',23.6,1);
 tl.fromTo('.scout-row',{x:-35,opacity:0},{x:0,opacity:1,duration:.45,stagger:.38,ease:'power3.out'},25);
 tl.fromTo('.archive-row',{x:35,opacity:0},{x:0,opacity:1,duration:.45,stagger:.38,ease:'power3.out'},25.25);
 pathDraw('#path-cross',27.1,.9);packet('#path-cross','#packet-cross',27.25,1.1);from('#exchange',27.9,{y:45,duration:.55});
 tl.to('#graph-world',{scale:1.06,x:-18,y:-18,duration:.65,ease:'power3.inOut'},29.8);
 tl.to(['#brief-node','#scout-node','#archivist-node','#exchange','#connections','#graph-title'],{opacity:0,scale:.82,duration:.65,stagger:.035,ease:'power3.in'},32.3);
 from('#finding',32.9,{scale:.72,y:45,duration:.8});
 tl.fromTo('.finding-rule',{scaleX:0},{scaleX:1,duration:.65,ease:'power3.inOut'},34.3);
 tl.to('#finding',{scale:1.8,opacity:0,duration:.7,ease:'power3.in'},37.35);
 tl.to('#handoff',{opacity:0,duration:.45},37.9);
 tl.set(stage,{scale:1},37.7);tl.to(stage,{opacity:1,duration:.5},37.7);
 crop(37.7,70,390,1110,0);crop(39,145,475,720,.8);crop(42.75,245,570,720,.65);crop(46.7,65,240,1170,.75);

 crop(48,910,185,525,.55);crop(49,315,885,785,.65);crop(52.95,335,520,735,.7);crop(57.1,310,465,790,.65);
 tl.to(stage,{scale:.8,opacity:0,duration:.7,ease:'power3.inOut'},61.6);
 tl.fromTo('#assembly',{opacity:0},{opacity:1,duration:.4,ease:'none'},61.7);
 from('#assembly-title',62.1,{duration:.5});
 from('#issues-sheet',62,{x:230,y:500,rotation:-12,duration:.85});
 from('#checklist-sheet',62.25,{y:540,rotation:8,duration:.85});
 from('#email-sheet',62.5,{x:-230,y:500,rotation:12,duration:.85});
 tl.to('#assembly-world',{scale:1.06,y:-20,duration:.75,ease:'power3.inOut'},64);
 tl.to('.check-symbol',{scale:1.18,duration:.18,stagger:.15,ease:'power2.out'},64.65);
 tl.to('.check-symbol',{scale:1,duration:.3,stagger:.15,ease:'power2.out'},64.85);
 tl.fromTo('#inspection',{clipPath:'circle(0% at 77% 54%)',opacity:1},{clipPath:'circle(80% at 50% 50%)',duration:.8,ease:'power3.inOut'},65.5);
 from('#inspection-inner',65.95,{y:40,duration:.55});pathDraw('#emphasis',66.7,.75);
 tl.to('#inspection',{clipPath:'circle(0% at 50% 50%)',duration:.75,ease:'power3.inOut'},68.5);
 tl.to('.evidence',{x:(i)=>[520,0,-520][i],y:-60,rotation:0,scale:.9,opacity:0,duration:.8,stagger:.06,ease:'power3.inOut'},68.65);
 from('#final-sheet',69.2,{y:240,scale:.74,duration:.85});
 tl.fromTo('.final-row',{x:50,opacity:0},{x:0,opacity:1,duration:.45,stagger:.52,ease:'power3.out'},70.1);
 tl.to('#assembly',{opacity:0,scale:1.15,duration:.65,ease:'power3.in'},73.75);
 tl.set(stage,{scale:1},73.75);tl.to(stage,{opacity:1,duration:.5},73.85);
 crop(73.8,315,465,790,0);crop(75,330,447,733,.65);
 tl.to(stage,{opacity:0,scale:.92,duration:.6,ease:'power3.inOut'},79.65);
 tl.fromTo('#outro',{opacity:0},{opacity:1,duration:.4,ease:'none'},79.7);
 from('#closing-line',80,{duration:.55});from('#brand-lockup',80.8,{y:55,scale:.9,duration:.65});from('#url',82,{y:18,duration:.45});
 tl.fromTo('#music',{volume:0},{volume:.92,duration:.16,ease:'none'},0);tl.to('#music',{volume:0,duration:1.5,ease:'power2.in'},83.5);

 const clickEvents=[{at:8.8,target:'agent-jarvis'},{at:9.6,target:'composer'},{at:12.8,target:'send'},{at:37.8,target:'close'},{at:48.6,target:'agent-scout'},{at:49.3,target:'composer'},{at:52.7,target:'send'},{at:74.4,target:'agent-jarvis'}];
 const cubic=(a,b,c,d,t)=>{const u=1-t;return u*u*u*a+3*u*u*t*b+3*u*t*t*c+t*t*t*d;};
 function cursorAt(t){
  const e=travel.find(e=>t>=e.at-.72&&t<=e.at+.28);if(!e)return null;
  const raw=Math.max(0,Math.min(1,(t-(e.at-.72))/.62));
  const p=raw<.5?4*raw*raw*raw:1-Math.pow(-2*raw+2,3)/2;
  const dx=e.x-e.fromX;
  const x=cubic(e.fromX,e.fromX+dx*.35,e.x-dx*.2,e.x,p),y=cubic(e.fromY,e.fromY-40,e.y+28,e.y,p);
  return{x,y,screenX:cam.x+x*cam.s,screenY:cam.y+y*cam.s,at:e.at,target:e.target,p};
 }
 function draw(t=clock.t){
  if(!ready)return;
  const nativeVisible=Number(gsap.getProperty(stage,'opacity'))>.005;
  let state=null;if(nativeVisible)state=product.contentWindow.renderDemoAt(sourceAt(t));
  world.style.transform=`translate(${cam.x}px,${cam.y}px) scale(${cam.s})`;
  const cursor=nativeVisible?cursorAt(t):null;pointer.style.opacity=cursor?'1':'0';ring.style.opacity='0';
  if(cursor){const age=t-cursor.at,press=age>=0&&age<.14?.84:1;pointer.style.transform=`translate(${cursor.screenX}px,${cursor.screenY}px) scale(${press})`;if(age>=0&&age<.3){ring.style.transform=`translate(${cursor.screenX-20}px,${cursor.screenY-20}px) scale(${.6+age*3})`;ring.style.opacity=String(.65*(1-age/.3));}}
  const caption=document.getElementById('native-caption'),label=t<21?'Give Jarvis the brief.':t<48?'Watch the agents exchange context.':t<62?'Go directly to the specialist.':'A briefing you can act on.';
  if(caption.textContent!==label)caption.textContent=label;
  window.__motionProof={time:t,sourceTime:sourceAt(t),cursor,camera:{x:cam.x,y:cam.y,s:cam.s},nativeVisible,selected:state?.selected??null};
 }
 tl.to(clock,{t:85,duration:85,ease:'none'},0);
 tl.eventCallback('onUpdate',()=>draw());
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin)return;
  if(event.data?.type==='jarvis-demo-error')throw new Error(event.data.message);
  if(event.data?.type!=='jarvis-demo-ready')return;
  const actual=product.contentWindow.demoClickLog;let last={x:960,y:830},index=0;
  for(const e of clickEvents){const recorded=actual[index++];if(!recorded||recorded.target!==e.target)throw new Error('Measured click sequence drift: '+e.target);travel.push({...e,x:recorded.x,y:recorded.y,fromX:last.x,fromY:last.y,bounds:recorded.bounds});last=recorded;}
  const doc=product.contentDocument;
  function union(elements){const r=elements.map(e=>e.getBoundingClientRect()).filter(r=>r.width>0&&r.height>0);if(!r.length)throw new Error('Missing native focus target');const x=Math.min(...r.map(r=>r.left)),y=Math.min(...r.map(r=>r.top));return{x,y,w:Math.max(...r.map(r=>r.right))-x,h:Math.max(...r.map(r=>r.bottom))-y};}
  function lines(source,patterns){product.contentWindow.renderDemoAt(source);const matches=e=>patterns.some(p=>e.textContent.trim().startsWith(p));return union([...doc.querySelectorAll('p,li,div')].filter(e=>matches(e)&&![...e.children].some(matches)));}
  const focus={request:lines(6,['Scout, check','On it.']),direct:lines(19,['Focus on','Put the setup','Explain that']),result:lines(23,['Launch briefing ready','Issues:','Checklist:','Next step:','I\'ve left'])};
  product.contentWindow.renderDemoAt(3.55);
  focus.composer=union([doc.querySelector('[data-testid="composer-chip-field"]'),doc.querySelector('button[aria-label="Send"]')].filter(Boolean));
  const selectFocus=at=>at===9.35||at===49?'composer':at===13||at===17.5?'request':at===52.95||at===57.1?'direct':at===73.8||at===75?'result':null;
  for(const shot of cameraPlan){
   let{x,y,width}=shot;const key=selectFocus(shot.at);
   if(key){const r={...focus[key]};if(key==='request'){r.y-=250;r.h+=250;}if(key==='direct'){r.y-=150;r.h+=150;}const padding=key==='composer'?1.3:1.16;width=Math.min(1440,Math.max(r.w*padding,r.h*(1920/960)*padding,700));const height=width*998/1920;x=Math.max(0,Math.min(1440-width,r.x+r.w/2-width/2));y=Math.max(0,Math.min(1264-height,r.y+r.h/2-height/2));}
   const s=1920/width;tl.to(cam,{x:-x*s,y:-y*s,s,duration:shot.duration,ease:'power3.inOut'},shot.at);
  }
  window.__nativeFocus=focus;
  ready=true;tl.time(.001,false);tl.time(0,false);draw(0);window.__timelines['agent-mode-v2']=tl;window.__hfForceTimelineRebind?.();
 });
 window.addEventListener('hf-seek',event=>{if(ready)draw(event.detail.time);});
})();
