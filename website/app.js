'use strict';
document.documentElement.classList.add('js');
const toggle=document.querySelector('.nav-toggle');
const nav=document.querySelector('#main-nav');
if(toggle&&nav){
 toggle.hidden=false;
 const mobile=window.matchMedia('(max-width: 800px)');
 const close=()=>{toggle.setAttribute('aria-expanded','false');nav.classList.remove('is-open');};
 mobile.addEventListener('change',close);
 toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));nav.classList.toggle('is-open',open);});
 nav.addEventListener('click',event=>{if(event.target.closest('a'))close();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&toggle.getAttribute('aria-expanded')==='true'){close();toggle.focus();}});
 document.addEventListener('click',event=>{if(!event.target.closest('.nav-shell'))close();});
}
document.querySelectorAll('[data-copy-field][data-copy-status]').forEach(copy=>{
 const field=document.getElementById(copy.dataset.copyField);
 const status=document.getElementById(copy.dataset.copyStatus);
 if(!field||!status)return;
 copy.hidden=false;
 copy.addEventListener('click',async()=>{
  try{if(!navigator.clipboard?.writeText)throw Error('Clipboard unavailable');await navigator.clipboard.writeText(field.value);status.textContent='Repository URL copied.';}
  catch{field.focus();field.select();field.setSelectionRange(0,field.value.length);status.textContent='URL selected. Use your device’s copy command.';}
 });
});
const images=document.querySelectorAll('[data-reveal]');
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
const showImage=img=>img.classList.add('is-visible');
if(reduced.matches||!('IntersectionObserver' in window)){
 images.forEach(showImage);
}else{
 const observer=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{
   if(!entry.isIntersecting)return;
   const img=entry.target;
   observer.unobserve(img);
   // Lazy-loaded pixels must be ready before their one entrance is spent.
   const reveal=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>showImage(img)));
   if(img.complete&&img.naturalWidth>0)reveal();
   else img.decode().then(reveal,reveal);
  });
 },{threshold:0.18,rootMargin:'0px 0px -14% 0px'});
 images.forEach(img=>observer.observe(img));
 reduced.addEventListener('change',event=>{
  if(event.matches){observer.disconnect();images.forEach(showImage);}
 });
}

// Keep local posters and direct links useful until someone chooses a player.
document.querySelectorAll('.reel-poster[data-video]').forEach(poster=>{
 poster.querySelector('.reel-action').textContent='Load player';
 poster.setAttribute('aria-label','Load video player: '+poster.dataset.videoTitle);
 poster.addEventListener('click',event=>{
  // Modified clicks retain the direct YouTube link behavior.
  if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();
  const player=document.createElement('iframe');
  player.src='https://www.youtube-nocookie.com/embed/'+poster.dataset.video+'?playsinline=1&rel=0';
  player.title=poster.dataset.videoTitle;
  player.allow='encrypted-media; picture-in-picture; web-share';
  player.allowFullscreen=true;
  player.referrerPolicy='strict-origin-when-cross-origin';
  poster.hidden=true;
  poster.parentElement.append(player);
  player.focus();
 });
});
