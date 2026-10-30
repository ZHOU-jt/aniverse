(() => {
'use strict';
const menuToggle=document.querySelector('[data-menu-toggle]');
const nav=document.getElementById('site-nav');
const scrim=document.querySelector('[data-menu-close]');
let previousFocus=null;
function setMenu(open){
 document.body.classList.toggle('nav-open',open);
 if(nav)nav.inert=matchMedia('(max-width:760px)').matches&&!open;
 if(menuToggle){menuToggle.setAttribute('aria-expanded',String(open));menuToggle.setAttribute('aria-label',open?'收起导航':'展开导航');}
 if(scrim)scrim.hidden=!open;
 if(open){previousFocus=document.activeElement;nav?.querySelector('a')?.focus();}
 else if(previousFocus){previousFocus.focus();previousFocus=null;}
}
menuToggle?.addEventListener('click',()=>setMenu(!document.body.classList.contains('nav-open')));
scrim?.addEventListener('click',()=>setMenu(false));
document.addEventListener('keydown',event=>{
 if(!document.body.classList.contains('nav-open'))return;
 if(event.key==='Escape'){setMenu(false);return;}
 if(event.key==='Tab'){
  const links=[...nav.querySelectorAll('a,button')];
  const first=links[0],last=links[links.length-1];
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
 }
});
const mobile=matchMedia('(max-width:760px)');
function navMode(){if(!mobile.matches)setMenu(false);if(nav){nav.inert=mobile.matches&&!document.body.classList.contains('nav-open');}}
mobile.addEventListener('change',navMode);
menuToggle?.addEventListener('click',()=>{if(nav)nav.inert=false;if(!document.body.classList.contains('nav-open'))navMode();});
scrim?.addEventListener('click',navMode);
document.addEventListener('keydown',event=>{if(event.key==='Escape')navMode();});
navMode();
document.querySelectorAll('[data-dismiss]').forEach(button=>button.addEventListener('click',()=>button.closest('.alert')?.remove()));
document.querySelectorAll('[data-cover]').forEach(img=>{
 function fallback(){if(img.dataset.fallback)return;img.dataset.fallback='true';img.src='/static/images/cover-placeholder.svg';}
 img.addEventListener('error',fallback);
 if(img.complete&&img.naturalWidth===0)fallback();
});
document.querySelectorAll('[data-counter]').forEach(field=>{
 const counter=document.getElementById(field.dataset.counter);
 const update=()=>{if(counter)counter.textContent=field.value.length+' / '+field.maxLength+' 个字符';};
 field.addEventListener('input',update);update();
});
const imageInput=document.querySelector('[data-image-input]');
const preview=document.querySelector('[data-image-preview]');
let previewUrl=null;
imageInput?.addEventListener('change',()=>{
 if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}
 imageInput.setCustomValidity('');
 if(preview){preview.hidden=true;preview.removeAttribute('src');}
 const file=imageInput.files?.[0];
 if(!file)return;
 if(file.size>=8*1024*1024){imageInput.setCustomValidity('图片需要小于 8 MB，请选择较小的图片。');imageInput.reportValidity();return;}
 if(preview&&file.type.startsWith('image/')){previewUrl=URL.createObjectURL(file);preview.src=previewUrl;preview.hidden=false;}
});
})();