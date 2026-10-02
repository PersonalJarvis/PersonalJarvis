const t="jarvis:apikeys-tab";let e=null;function n(s){e=s,window.dispatchEvent(new CustomEvent(t,{detail:s}))}function a(){return e}function i(){e=null}export{t as A,a,i as c,n as r};
