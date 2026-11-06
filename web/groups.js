// Group-scoped bearer credentials never go in query strings or invitation links.
const $ = s => document.querySelector(s);
const groupKey = id => `gameon-group:${id}`;
let groupId = new URLSearchParams(location.search).get('group');
let credential = null, current = null, venues = [], pendingInvitation = null;
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('invite')) {
  pendingInvitation = {code: fragment.get('code'), invitation_token: fragment.get('invite')};
  sessionStorage.setItem('gameon-pending-invitation', JSON.stringify(pendingInvitation));
  history.replaceState(null, '', '/groups.html'); // remove secret from visible URL/history immediately
} else {
  try { pendingInvitation = JSON.parse(sessionStorage.getItem('gameon-pending-invitation')); } catch (_) {}
}
function flash(text) { $('#flash').textContent = text; $('#flash').hidden = false; }
async function api(path, data, token = credential?.member_token) {
  const authHeaders = await GameOnAuth.headers();
  const r = await fetch(path, {method: data === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {}), ...authHeaders},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const result = await r.json();
  if (!r.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function save(result, name) {
  credential = {member_token: result.member_token, name, code: result.code}; groupId = result.group_id;
  if(result.member_token)localStorage.setItem(groupKey(groupId), JSON.stringify(credential));
  history.replaceState(null, '', `/groups.html?group=${encodeURIComponent(groupId)}`);
}
const pickers = new Map();
function choices(selector, selected = []) {
  pickers.set(selector, createVenuePicker($(selector), venues, selected));
}
function selected(selector) { return pickers.get(selector).selected(); }
function selectedAvatar(selector) { return $(selector).querySelector('input:checked')?.value || 'auto'; }
function invitation(token) {
  $('#invitation-output').hidden = !token;
  // Invitation tokens are only shown once, so after a reload the organiser creates a fresh link.
  $('#new-invitation').textContent = token ? 'Create a new link' : 'Create invitation link';
  // One primary action: invite while the crew is still forming, otherwise start a game.
  const forming = (current?.members.length || 0) < 2;
  $('#new-invitation').className = token ? 'text-button' : forming ? 'primary' : 'secondary';
  $('#create-game').className = forming ? 'secondary' : 'primary';
  $('#invitation-link').value = token ? `${location.origin}/groups.html#code=${encodeURIComponent(current.code)}&invite=${encodeURIComponent(token)}` : '';
}
function listLinks(selector, items, href, {action = 'Open →', empty = 'None yet.'} = {}) {
  const root = $(selector); root.replaceChildren();
  for (const item of items) {
    const a = document.createElement('a'); a.className = 'group-link'; a.href = href(item);
    const name = document.createElement('strong'); name.textContent = item.title || item.name;
    const go = document.createElement('span'); go.textContent = action;
    a.append(name, go); root.append(a);
  }
  if (!items.length) { const p = document.createElement('p'); p.className = 'small'; p.textContent = empty; root.append(p); }
}
const gameListOptions = () => ({action: 'Open game →', empty: current?.me.role === 'owner' ? 'No games yet. Start one and offer a few times.' : 'No games yet. Your organiser will start one.'});
async function loadGroup() {
  current = await api(`/api/groups/${encodeURIComponent(groupId)}`);
  credential ||= {};
  credential.name = current.name; credential.code = current.code;
  if(credential.member_token && !GameOnAuth.signedIn)localStorage.setItem(groupKey(groupId), JSON.stringify(credential));
  $('#backup').closest('section').hidden=GameOnAuth.signedIn;
  $('#group-title').textContent = current.name; document.title = `${current.name} — Game On!`;
  $('#group-code').textContent = `Group code: ${current.code} (not a password)`;
  $('#groups-landing').hidden = true;
  GameOnUI.renderCourt($('#group-court'),current.members);
  let guide=$('#ernie-group');if(!guide){guide=document.createElement('aside');guide.id='ernie-group';$('#group-court').after(guide);}
  Ernie.show(guide,'coach','Your group stays together across games. Open a game to vote on times; joining the group alone isn’t a vote.');
  $('#group-organiser').textContent = `Organiser: ${current.members.find(m => m.role === 'owner')?.name || 'Unavailable'}`;
  $('#group-role').textContent = `You are ${current.me.name}, ${current.me.role === 'owner' ? 'the organiser' : 'a member'}.`;
  for (const id of ['create-panel', 'invite-panel']) $(`#${id}`).hidden = true;
  $('#group-panel').hidden = false; $('#owner-panel').hidden = current.me.role !== 'owner';
  $('#create-game').hidden = current.me.role !== 'owner'; $('#create-game').href = `/?group=${encodeURIComponent(groupId)}`;
  if ($('#invitation-output').hidden) invitation(null);
  $('#members').textContent = current.members.map(m => `${m.name}${m.role === 'owner' ? ' (organiser)' : ''}`).join(', ');
  listLinks('#games', current.games, g => `/?game=${encodeURIComponent(g.id)}&group=${encodeURIComponent(groupId)}`, gameListOptions());
  $('#profile-name').value = current.me.name; $('#profile-suburb').value = current.me.suburb;
  GameOnUI.avatarPicker($('#profile-avatar'),current.me.avatar_id,current.members.find(m=>m.id===current.me.id)?.avatar_seed);
  $('#settings-name').value = current.name; $('#restrict').checked = current.restrict_venues;
  choices('#profile-favourites', current.me.favourites); choices('#group-favourites', current.favourites);
}
function on(selector, event, task) {
  $(selector).addEventListener(event, async e => {
    e.preventDefault(); $('#flash').hidden = true;
    const button = event === 'submit' ? e.target.querySelector('button[type="submit"]') : e.currentTarget;
    button.disabled = true;
    try { await task(); } catch(error) { flash(error.message); } finally { button.disabled = false; }
  });
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); flash('Copied.'); }
  catch (_) { window.prompt('Copy and keep this safely:', text); }
}
async function setup() {
  await GameOnAuth.ready;
  const auth=GameOnAuth;
  if(auth.enabled && !auth.signedIn && pendingInvitation){auth.login();return;}
  const directory = await api('/api/venues', undefined, null); venues = directory.venues;
  for (const suburb of directory.suburbs) { const option = document.createElement('option'); option.value = suburb; $('#suburbs').append(option); }
  choices('#join-favourites');
  GameOnUI.avatarPicker($('#creator-avatar')); GameOnUI.avatarPicker($('#join-avatar'));
  if(auth.enabled){
    $('#create-group > p.small').textContent='Sign in to keep your group and profile across devices.';
    $('#saved-panel h2').textContent='My groups';
  }
  if(auth.signedIn){
    $('#restore-panel').hidden=true;
    if(auth.profile){
      for(const prefix of ['creator','join']){
        $(`#${prefix}-name`).value=auth.profile.name;$(`#${prefix}-suburb`).value=auth.profile.suburb;
        $(`#${prefix}-name`).closest('.field-row').hidden=true;
        GameOnUI.avatarPicker($(`#${prefix}-avatar`),auth.profile.avatar_id);
        $(`#${prefix}-avatar`).closest('fieldset').hidden=true;
      }
      choices('#join-favourites',auth.profile.favourites);
      $('#join-favourites').closest('details').hidden=true;
    }
    // Linking is explicit; possession of a saved key never silently claims membership.
    const legacy=[];
    for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith('gameon-group:'))continue;try{const value=JSON.parse(localStorage.getItem(key));if(value?.member_token && !auth.groups.some(g=>g.id===key.slice(13)))legacy.push({id:key.slice(13),...value});}catch(_){}}
    for(const old of legacy){
      const button=document.createElement('button');button.className='secondary';button.textContent=`Link saved membership: ${old.name || 'Group'}`;
      button.onclick=async()=>{button.disabled=true;try{await api('/api/account/link',{group_id:old.id,member_token:old.member_token});location.href='/groups.html?group='+encodeURIComponent(old.id);}catch(e){flash(e.message);button.disabled=false;}};
      $('#saved-panel').append(button);
    }
  }
  on('#create-group', 'submit', async () => {
    if(auth.enabled && !auth.signedIn){auth.login();return;}
    const result = await api('/api/groups', {name: $('#group-name').value, player_name: $('#creator-name').value, suburb: $('#creator-suburb').value, avatar_id: selectedAvatar('#creator-avatar')}, null);
    save(result, $('#group-name').value); await loadGroup(); invitation(result.invitation_token);
    flash(auth.signedIn?'Group created! Your membership is saved to your account.':'Group created! Keep your private access key safely.');
  });
  on('#join-group', 'submit', async () => {
    const result = await api('/api/invitations/join', {...pendingInvitation, name: $('#join-name').value, suburb: $('#join-suburb').value, favourites: selected('#join-favourites'), avatar_id: selectedAvatar('#join-avatar')}, null);
    sessionStorage.removeItem('gameon-pending-invitation'); pendingInvitation = null;
    save(result, 'Group'); await loadGroup(); flash(auth.signedIn?'You’re in! This group will appear whenever you sign in.':'You’re in! Save your private access key so you can return on another device.');
  });
  on('#profile', 'submit', async () => { await api(`/api/groups/${groupId}/me`, {name: $('#profile-name').value, suburb: $('#profile-suburb').value, favourites: selected('#profile-favourites'), avatar_id: selectedAvatar('#profile-avatar')}); await loadGroup(); flash('Your profile is saved for this group’s games.'); });
  on('#settings', 'submit', async () => { await api(`/api/groups/${groupId}/settings`, {name: $('#settings-name').value, favourites: selected('#group-favourites'), restrict_venues: $('#restrict').checked}); await loadGroup(); flash('Group settings saved.'); });
  on('#new-invitation', 'click', async () => { const result = await api(`/api/groups/${groupId}/invitation`, {action: 'rotate'}); invitation(result.invitation_token); });
  on('#revoke-invitation', 'click', async () => { await api(`/api/groups/${groupId}/invitation`, {action:'revoke'}); invitation(null); flash('Invitation revoked. Existing members keep access.'); });
  on('#copy-invitation', 'click', () => copy($('#invitation-link').value));
  on('#whatsapp-invitation', 'click', async () => { window.open(`https://wa.me/?text=${encodeURIComponent(`Join ${current.name} on Game On!\n${$('#invitation-link').value}`)}`, '_blank', 'noopener,noreferrer'); });
  on('#backup', 'click', () => copy(credential.member_token));
  on('#rotate-credential', 'click', async () => {
    const result = await api(`/api/groups/${groupId}/credential`, {});
    credential.member_token = result.member_token; localStorage.setItem(groupKey(groupId), JSON.stringify(credential));
    flash('Your old key no longer works. Copy and save your new key now.');
  });
  on('#restore-group', 'submit', async () => {
    const token = $('#restore-token').value.trim();
    const result = await api('/api/groups/restore', {code: $('#restore-code').value.trim()}, token);
    save({...result, member_token:token}, 'Group'); $('#restore-token').value = ''; await loadGroup();
  });
  if (groupId) { credential = JSON.parse(localStorage.getItem(groupKey(groupId)) || 'null'); if (auth.signedIn || credential) await loadGroup(); else if(auth.enabled)auth.login(); else flash('Restore membership below or open your group invitation.'); }
  else if (pendingInvitation) {
    // Reopening your own invitation must never replace an owner's saved credential.
    for (let i=0; !auth.signedIn && i<localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key.startsWith('gameon-group:')) continue;
      let saved;
      try { saved = JSON.parse(localStorage.getItem(key)); } catch (_) { continue; }
      if (saved?.code === pendingInvitation.code) {
        groupId = key.slice('gameon-group:'.length); credential = saved;
        sessionStorage.removeItem('gameon-pending-invitation'); pendingInvitation = null;
        history.replaceState(null, '', `/groups.html?group=${encodeURIComponent(groupId)}`);
        await loadGroup(); return;
      }
    }
    const preview = await api('/api/invitations/preview', pendingInvitation, null);
    $('#groups-landing').hidden = true; $('#create-panel').hidden = true; $('#invite-panel').hidden = false; $('#invite-heading').textContent = `You’re invited to ${preview.name}`;
  } else {
    const saved = auth.signedIn ? [...auth.groups] : [];
    for (let i=0; i<localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!auth.signedIn && key.startsWith('gameon-group:')) { try { saved.push({id:key.slice('gameon-group:'.length), name: JSON.parse(localStorage.getItem(key)).name || 'Group'}); } catch (_) {} }
    }
    listLinks('#saved-groups', saved, g => `/groups.html?group=${encodeURIComponent(g.id)}`, {action: 'Open group →', empty: 'No groups on this device yet. Create one, or open an invitation from a friend.'});
  }
}
setup().catch(e => flash(e.message));
// Refresh shared membership/game lists without overwriting profile/settings edits.
setInterval(async () => {
  if(!groupId || !credential || $('#group-panel').hidden)return;
  try {
    const latest=await api(`/api/groups/${encodeURIComponent(groupId)}`);
    current = latest;
    $('#group-organiser').textContent = `Organiser: ${latest.members.find(m => m.role === 'owner')?.name || 'Unavailable'}`;
    GameOnUI.renderCourt($('#group-court'),latest.members);
    $('#members').textContent=latest.members.map(m=>`${m.name}${m.role==='owner'?' (organiser)':''}`).join(', ');
    listLinks('#games',latest.games,g=>`/?game=${encodeURIComponent(g.id)}&group=${encodeURIComponent(groupId)}`,gameListOptions());
  } catch (_) { flash('Group updates are unavailable. Refresh or restore access to try again.'); }
},10000);
