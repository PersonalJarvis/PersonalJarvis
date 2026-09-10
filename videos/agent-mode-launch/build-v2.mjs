import {readFileSync,writeFileSync} from 'node:fs';
const html=readFileSync('motion-v2.html.in','utf8');
const script=readFileSync('motion.js','utf8');
writeFileSync('index.html',html.replace('<!-- MOTION_SCRIPT -->',`<script>\n${script}\n</script>`).trimEnd()+'\n');
console.log('Built native 60 fps motion composition.');

