// Managed Neon cookies hold the session; short-lived API JWTs stay in memory only.
window.GameOnAuth = (() => {
  const state = {enabled:false, signedIn:false, profile:null, groups:[], client:null, config:null};
  const destination = () => location.pathname + location.search;
  function safeDestination(value) {
    try { const u = new URL(value,location.origin); return u.origin === location.origin && ['/', '/groups.html'].includes(u.pathname) ? u.pathname+u.search : '/groups.html'; }
    catch (_) { return '/groups.html'; }
  }
  async function headers() {
    if (!state.signedIn) return {};
    // @neon/auth 0.8.2 injects set-auth-jwt into session.token and refreshes its cache.
    const {data,error} = await state.client.getSession();
    const token=data?.session?.token;
    if (error || !token) { state.signedIn=false; throw new Error('Your session expired. Please sign in again.'); }
    return {Authorization:`Bearer ${token}`};
  }
  async function refreshAccount() {
    const r = await fetch('/api/account',{headers:await headers()});
    if (!r.ok) throw new Error('Your account could not be loaded. Please sign in again.');
    const data = await r.json(); state.profile=data.profile; state.groups=data.groups;
    return data;
  }
  function login() { location.href='/login.html?next='+encodeURIComponent(destination()); }
  function navigation() {
    const nav=document.querySelector('.topbar nav'); if(!nav || !state.enabled)return;
    nav.closest('.topbar').classList.add('has-account');
    if(state.signedIn) {
      const name=document.createElement('span'); name.textContent=state.profile?.name || 'Signed in';
      name.className='account-name';
      if(state.profile?.avatar_id && window.GameOnUI) {
        name.prepend(GameOnUI.avatar(state.profile));
        name.setAttribute('aria-label', `${name.textContent}, avatar ${state.profile.avatar_id}`);
      }
      const out=document.createElement('button');out.className='text-button';out.textContent='Sign out';
      out.onclick=async()=>{out.disabled=true;try { const result=await state.client.signOut();if(result.error)throw new Error();state.signedIn=false;location.href='/login.html?signedout=1'; } catch(_){out.disabled=false;out.textContent='Sign out failed — retry';}};
      nav.append(name,out);
    } else { const a=document.createElement('a');a.href='/login.html?next='+encodeURIComponent(destination());a.textContent='Sign in';nav.append(a); }
  }
  state.ready=(async()=>{
    const r=await fetch('/api/auth/config'); if(!r.ok)throw new Error('Sign-in configuration unavailable.');
    state.config=await r.json();state.enabled=state.config.enabled;
    if(!state.enabled)return;
    state.client=NeonAuth.createAuthClient(state.config.url);
    const {data,error}=await state.client.getSession();
    if(error)throw new Error('Sign-in service unavailable. Please retry.');
    state.signedIn=!!data?.session;
    if(state.signedIn)await refreshAccount();
    navigation();
  })();
  // Consumers display errors; suppress an early unhandled rejection before their setup runs.
  state.ready.catch(()=>{});
  return Object.assign(state,{headers,refreshAccount,login,safeDestination});
})();
