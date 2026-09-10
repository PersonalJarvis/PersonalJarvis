import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';

// T1: local video composition. Source pixels and product behavior are unchanged.
// Camera geometry is adapted from the installed ui-focus-zoom registry component:
// one world wrapper, anchored translation and scale, with stationary reading holds.
mkdirSync('compositions/frames',{recursive:true});
const shots=[];
function frame(id,name,duration,content,motion){
  const file=`compositions/frames/${id}-${name}.html`;
  const key=`s${id}`;
  writeFileSync(file,`<template><link rel="stylesheet" href="../../style.css"><div id="${key}" data-composition-id="${key}" data-width="1920" data-height="1080" data-duration="${duration}" class="scene"><div class="ground clip" data-start="0" data-duration="${duration}" data-track-index="0"></div>${content}<script>(function(){const tl=gsap.timeline({paused:true});const root=document.getElementById('${key}');const q=s=>root.querySelector(s);${motion}window.__timelines['${key}']=tl;})();</script></div></template>`);
  const normalized=readFileSync(file,'utf8').replaceAll('../../assets/','assets/').replaceAll('../../style.css','style.css').replace(`id="${key}" data-composition-id`, 'id="root" data-composition-id').replace('class="scene"','style="width:1920px;height:1080px;position:relative;overflow:hidden"').replace(`document.getElementById('${key}')`,"document.getElementById('root')").replace('class="ground clip"',`id="${key}-ground" class="ground clip"`);
  writeFileSync(file,normalized.replaceAll(`id="${id}-video"`,`id="s${id}-video"`).replace('class="product-layout"',`class="product-layout shot${id}"`));
  shots.push({id:key,file,duration});
}
function arrival(selector,at=0,duration=.7){return `tl.fromTo(q('${selector}'),{opacity:0,y:28},{opacity:1,y:0,duration:${duration},ease:'power3.out'},${at});`;}
const kicker=t=>`<div class="eyebrow">${t}</div>`;
frame('01','tomorrow',6.4,`<div class="title-layout">${kicker('A launch-day story')}<h1 class="hook">Launch is <span class="gold">tomorrow.</span></h1><div class="tasks"><span>Open issues.</span><span>Release checklist.</span><span>Welcome email.</span></div><div class="rule"></div><p class="question">Where do you start?</p></div>`,arrival('.eyebrow',0)+arrival('.hook',.3)+`tl.fromTo(q('.tasks').children,{opacity:0,y:18},{opacity:1,y:0,stagger:.48,duration:.65,ease:'power3.out'},1.25);tl.fromTo(q('.rule'),{scaleX:0},{scaleX:1,duration:.8,ease:'power3.inOut'},3.2);`+arrival('.question',3.7));
frame('02','brief',4,`<div class="title-layout center">${kicker('Personal Jarvis')}<h1 class="promise">Give your agents<br class="headline-break">the brief.</h1><p class="support">One team. A shared context.</p></div>`,arrival('.eyebrow',0)+arrival('.promise',.15)+arrival('.support',1.55));

function camera(x,y,w){const scale=1728/w;return {x:-x*scale,y:-y*scale,scale,transformOrigin:'0 0'};}
function film(id,name,duration,title,step,start,end,poses,footer){
  const content=`<div class="product-layout"><div class="film-heading"><h2>${title}</h2><span class="chapter">${step}</span></div><div class="viewport"><div class="camera" data-layout-allow-overflow><video id="${id}-video" class="clip source" data-start="0" data-duration="${duration}" data-media-start="${start}" data-playback-rate="${(end-start)/duration}" data-track-index="1" src="../../assets/agents-source.mp4" muted playsinline></video></div></div><div class="film-footer"><span>${footer}</span><span class="demo">Illustrative workflow · actual product UI</span></div></div>`;
  let motion=`tl.set(q('.camera'),${JSON.stringify(camera(...poses[0].crop))},0);`;
  for(const p of poses.slice(1))motion+=`tl.to(q('.camera'),${JSON.stringify({...camera(...p.crop),duration:p.duration??.9,ease:'power3.inOut'})},${p.at});`;
  motion+=arrival('.film-heading',0,.5)+arrival('.film-footer',1.2,.5);
  frame(id,name,duration,content,motion);
}
film('03','request',14.4,'Start with the outcome.','01 / THE BRIEF',2.14,6.98,[{crop:[480,1370,1135]},{at:5.1,crop:[480,275,1135],duration:1.05}],'Ask Jarvis to coordinate the work.');
film('04','handoff',19.2,'Let the team connect the dots.','02 / THE HANDOFF',7.16,14.98,[{crop:[90,560,1540]},{at:2.0,crop:[195,715,1110],duration:1.0},{at:9.45,crop:[340,850,1110],duration:.85}],'Scout checks the blockers. Archivist brings the checklist.');
film('05','direction',17.6,'Go straight to the specialist.','03 / YOUR DIRECTION',15.16,20.98,[{crop:[480,1370,1135]},{at:5.25,crop:[500,785,1100],duration:1.05}],'Focus the task. Keep the conversation in context.');
film('06','result',13.6,'A briefing you can act on.','04 / THE RESULT',21.16,25.98,[{crop:[495,680,1100]}],'Clear findings. One explicit next step.');
frame('07','agent-mode',9.8,`<div class="title-layout center"><div class="closing-copy"><p class="closing-one">Your agents move the work forward.</p><p class="closing-two gold">You keep the final call.</p></div><div class="lockup"><img src="../../assets/gigi.png" alt="Personal Jarvis ghost mark"><div><div class="brand-name">Personal Jarvis</div><h1>Agent Mode</h1></div></div><p class="cta">github.com/PersonalJarvis/PersonalJarvis</p></div>`,arrival('.closing-one',0)+arrival('.closing-two',1.5)+`tl.to(q('.closing-copy'),{opacity:0,y:-20,duration:.5,ease:'power3.in'},3.6);`+arrival('.lockup',3.85)+arrival('.cta',5.8));

let at=0;
const hosts=shots.map(s=>{const html=`<div id="${s.id}" data-composition-id="${s.id}" class="clip" data-start="${at.toFixed(1)}" data-duration="${s.duration}" data-track-index="1" data-composition-src="${s.file}"></div>`;at+=s.duration;return html;}).join('\n');
if(Math.abs(at-85)>1e-7)throw new Error(`Duration drift: ${at}`);
writeFileSync('index.html',`<!doctype html><html lang="en"><head><meta charset="UTF-8"><title>Personal Jarvis — Agent Mode</title><link rel="stylesheet" href="style.css"><script src="assets/gsap.min.js"></script></head><body><div id="agent-mode-launch" data-composition-id="agent-mode-launch" data-width="1920" data-height="1080" data-duration="85">${hosts}<audio id="music" src="assets/music.wav" data-start="0" data-duration="85" data-track-index="8"></audio></div><script>const tl=gsap.timeline({paused:true});tl.fromTo('#music',{volume:0},{volume:.9,duration:.2,ease:'none'},0);tl.to('#music',{volume:0,duration:2.2,ease:'power2.in'},82.8);window.__timelines['agent-mode-launch']=tl;</script></body></html>`);
const board=readFileSync('STORYBOARD.md','utf8').replaceAll('status: outline','status: animated').replace('duration: 10.2s','duration: 9.8s').replace('7–10.2s','7–9.8s');
writeFileSync('STORYBOARD.md',board);
writeFileSync('edit.json',JSON.stringify({duration:85,fps:30,width:1920,height:1080,shots},null,2));
console.log(`Built ${shots.length} shots, ${at.toFixed(1)} seconds.`);
