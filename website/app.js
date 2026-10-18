'use strict';
document.documentElement.classList.add('js');
const toggle=document.querySelector('.nav-toggle');
const nav=document.querySelector('#main-nav');
if(toggle&&nav){
 toggle.hidden=false;
 const close=()=>{toggle.setAttribute('aria-expanded','false');nav.classList.remove('is-open');};
 toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));nav.classList.toggle('is-open',open);});
 nav.addEventListener('click',event=>{if(event.target.closest('a'))close();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&toggle.getAttribute('aria-expanded')==='true'){close();toggle.focus();}});
 document.addEventListener('click',event=>{if(!event.target.closest('.nav-shell'))close();});
}
const copy=document.querySelector('#copy-repository');
const field=document.querySelector('#repository-url');
const status=document.querySelector('#copy-status');
if(copy&&field&&status){copy.hidden=false;copy.addEventListener('click',async()=>{
 try{if(!navigator.clipboard?.writeText)throw Error('Clipboard unavailable');await navigator.clipboard.writeText(field.value);status.textContent='Repository URL copied.';}
 catch{field.focus();field.select();field.setSelectionRange(0,field.value.length);status.textContent='URL selected. Use your device’s copy command.';}
});}
const images=document.querySelectorAll('[data-reveal]');
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
if(reduced.matches||!('IntersectionObserver' in window)){images.forEach(img=>img.classList.add('is-visible'));}
else{const observer=new IntersectionObserver(entries=>{entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('is-visible');observer.unobserve(entry.target);}});},{threshold:0.12});images.forEach(img=>observer.observe(img));reduced.addEventListener('change',event=>{if(event.matches){observer.disconnect();images.forEach(img=>img.classList.add('is-visible'));}});}
