(() => {
'use strict';
const tabs=[...document.querySelectorAll('.auth-tab')];
const panels=[...document.querySelectorAll('.form-panel')];
function activate(tab){
 tabs.forEach(item=>{const active=item===tab;item.classList.toggle('active',active);item.setAttribute('aria-selected',String(active));item.tabIndex=active?0:-1;});
 panels.forEach(panel=>panel.classList.toggle('active',panel.id===tab.dataset.target));
 const title=document.getElementById('auth-title');
 const register=tab.dataset.target==='registerPanel';
 if(title)title.textContent=register?'新的相遇，从这里开始。':'欢迎回到，你的热爱。';
 const subtitle=document.querySelector('.auth-subtitle');
 if(subtitle)subtitle.textContent=register?'加入 AniVerse，让每一种热爱都有回音。':'登录 AniVerse，继续探索你的动画宇宙。';
 document.title=(register?'加入':'欢迎回来')+' · AniVerse';
}
tabs.forEach((tab,index)=>{
 tab.addEventListener('click',event=>{event.preventDefault();activate(tab);history.replaceState(null,'',tab.href);});
 tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();let next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();});
});
const active=tabs.find(tab=>tab.classList.contains('active'));if(active)activate(active);
document.querySelectorAll('[data-password-toggle]').forEach(button=>button.addEventListener('click',()=>{
 const input=button.parentElement.querySelector('input');const show=input.type==='password';input.type=show?'text':'password';button.setAttribute('aria-label',show?'隐藏密码':'显示密码');button.setAttribute('aria-pressed',String(show));
}));
const password=document.getElementById('registerPassword');
const confirm=document.getElementById('confirmPassword');
function checkMatch(){if(confirm)confirm.setCustomValidity(confirm.value&&password.value!==confirm.value?'两次输入的密码不一致。':'');}
password?.addEventListener('input',checkMatch);confirm?.addEventListener('input',checkMatch);
const sendButton=document.getElementById('sendCodeBtn');
const emailField=document.getElementById('registerEmail');
const status=document.getElementById('codeStatus');
let countdown=null;
function startCountdown(seconds){
 clearInterval(countdown);
 let remaining=Math.max(1,Number(seconds)||60);
 sendButton.disabled=true;sendButton.textContent=remaining+'s 后重发';
 countdown=setInterval(()=>{remaining-=1;if(remaining<=0){clearInterval(countdown);sendButton.disabled=false;sendButton.textContent='重新发送';return;}sendButton.textContent=remaining+'s 后重发';},1000);
}
sendButton?.addEventListener('click',async()=>{
 const email=emailField.value.trim();
 if(!email||!emailField.checkValidity()){status.textContent='请先填写有效邮箱。';status.className='field-note status-error';emailField.reportValidity();return;}
 sendButton.disabled=true;sendButton.textContent='发送中…';status.textContent='';
 try{
  const response=await fetch(sendButton.dataset.url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
  let data;try{data=await response.json();}catch{throw new Error('服务暂时不可用，请稍后再试。');}
  if(!response.ok)throw data;
  status.textContent=data.message||'验证码已发送，请查收邮箱。';status.className='field-note status-success';startCountdown(data.cooldown||60);
 }catch(error){status.textContent=error.message||'验证码发送失败，请稍后再试。';status.className='field-note status-error';if(error.retry_after)startCountdown(error.retry_after);else{sendButton.disabled=false;sendButton.textContent='发送验证码';}}
});
})();