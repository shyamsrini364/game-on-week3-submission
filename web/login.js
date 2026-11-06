(async()=>{
  const auth=GameOnAuth, status=document.querySelector('#login-status'), form=document.querySelector('#login-form');
  const params=new URLSearchParams(location.search), next=auth.safeDestination(params.get('next') || sessionStorage.getItem('gameon-login-next'));
  function message(text){status.textContent=text;}
  try {
    await auth.ready;
    if(!auth.enabled){message('Email sign-in is not configured in this environment yet. Existing group access still works.');return;}
    if(auth.signedIn){message('Signed in successfully. Your groups are ready.');sessionStorage.removeItem('gameon-login-next');const link=document.querySelector('#continue');link.hidden=false;link.href=next;link.textContent='Continue';location.replace(next);return;}
    form.hidden=false;
    document.querySelector('#otp-form').hidden=!auth.config.email_otp;
    const error=(params.get('error') || '').toUpperCase();
    message(params.has('signedout')?'You’re signed out.':error.includes('EXPIRED')?'This sign-in link has expired. Request a new one.':error?'This sign-in link is invalid or has already been used. Request a new one.':'Enter your email to get a secure sign-in link.');
    function email(){const input=document.querySelector('#email');if(!input.reportValidity())throw new Error('Enter a valid email address.');return input.value.trim();}
    const linkButton=form.querySelector('button'), linkLabel=linkButton.textContent;
    document.querySelector('#email').addEventListener('input',()=>{if(!linkButton.disabled)linkButton.textContent=linkLabel;});
    async function busy(button,work){button.disabled=true;try{await work();}catch(_){message('The sign-in request could not be completed. Check your connection and try again. Email delivery has not been confirmed.');}finally{button.disabled=false;}}
    form.onsubmit=e=>{e.preventDefault();if(linkButton.disabled)return;busy(linkButton,async()=>{
      const address=email();sessionStorage.setItem('gameon-login-next',next);message('Sending your sign-in email…');
      linkButton.textContent='Sending…';
      const callbackURL=location.origin+'/login.html';
      try {
        const result=await auth.client.signIn.magicLink({email:address,callbackURL,errorCallbackURL:callbackURL});
        if(result.error)throw new Error();
        linkButton.textContent=document.querySelector('#email').value.trim()===address?'✓ Sent':linkLabel;
        message('Sign-in email requested. Check your inbox and spam folder. If it does not arrive, request another link or use a code when available.');
      } catch(error) {linkButton.textContent=linkLabel;throw error;}
    });};
    document.querySelector('#send-code').onclick=e=>busy(e.target,async()=>{const result=await auth.client.emailOtp.sendVerificationOtp({email:email(),type:'sign-in'});if(result.error)throw new Error();message('Sign-in code requested. Check your email.');});
    document.querySelector('#otp-form').onsubmit=e=>{e.preventDefault();busy(e.target.querySelector('button[type="submit"]'),async()=>{const result=await auth.client.signIn.emailOtp({email:email(),otp:document.querySelector('#otp').value.trim()});if(result.error){message('That code is invalid or expired. Request a new code.');return;}location.replace('/login.html?next='+encodeURIComponent(next));});};
  }catch(_){message('Sign-in is unavailable. Please retry. If this browser blocks cross-site cookies, try another browser.');}
})();
