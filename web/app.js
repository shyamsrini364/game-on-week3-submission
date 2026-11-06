// Game On! — intentionally vanilla JavaScript, no frontend framework.
const $ = (selector) => document.querySelector(selector);
const $all = (selector) => [...document.querySelectorAll(selector)];
const params = new URLSearchParams(location.search);
let gameId = params.get('game');
const groupId = params.get('group');
let groupCredential = null;
try { groupCredential = JSON.parse(localStorage.getItem(`gameon-group:${groupId}`) || 'null'); } catch (_) {}
let currentGame = null;
let groupMembers = [];
let venueDiscovery = null;
let suburbLocations = {};
let latestPlan = null;
let playerIdentity = null;
let selectedHandoff = null;
let handoffData = null;
let recommendationRevision = null;
let handoffBusy = false;
let currentReady = false;
let groupName = '';
const SLOT_LIMIT = 6;

function flash(message) {
  const box = $('#flash');
  box.textContent = message;
  box.hidden = false;
  window.scrollTo({top: 0, behavior: 'smooth'});
}
function clearFlash() { $('#flash').hidden = true; }
async function api(path, options = {}) {
  const authHeaders=await GameOnAuth.headers();
  const response = await fetch(path, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(groupCredential ? {Authorization: `Bearer ${groupCredential.member_token}`} : {}), ...(options.headers || {}), ...authHeaders},
  });
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.error || 'Something went wrong.'); error.status = response.status; throw error; }
  return data;
}
function buttonBusy(btn, busy) { btn.disabled = busy; }
function localIdentityKey() { return `gameon-player:${gameId}`; }
function timeLabel(iso) {
  return new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Melbourne', weekday:'short', day:'numeric', month:'short', hour:'numeric', minute:'2-digit', hour12:true}).format(new Date(iso));
}
function clockLabel(iso) {
  return new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Melbourne', hour:'numeric', minute:'2-digit', hour12:true}).format(new Date(iso));
}
function appendTimeInput(value = '') {
  if ($all('.time-row').length >= SLOT_LIMIT) return;
  const row = document.createElement('div');
  row.className = 'time-row';
  const input = document.createElement('input');
  input.type = 'datetime-local';
  input.required = true;
  input.setAttribute('aria-label', 'Proposed Melbourne date and time');
  input.value = value;
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'remove-time';
  remove.textContent = '×';
  remove.title = 'Remove this time';
  remove.setAttribute('aria-label', 'Remove this proposed time');
  remove.onclick = () => { if ($all('.time-row').length > 2) { row.remove(); clearFlash(); updateTimeControls(); } };
  input.addEventListener('input', clearFlash);
  row.append(input, remove);
  $('#time-inputs').append(row);
  clearFlash(); updateTimeControls();
}
function updateTimeControls() {
  const count = $all('.time-row').length;
  for (const button of $all('.remove-time')) {
    button.disabled = count <= 2;
    button.title = count <= 2 ? 'Keep two starting times so friends have a choice.' : 'Remove this time';
  }
  $('#add-time').disabled = count >= SLOT_LIMIT;
}
function setDefaultDates() {
  // Date strings are interpreted in the user's browser timezone; the MVP assumes Melbourne players.
  const now = new Date();
  const nextSaturday = new Date(now);
  let days = (6 - nextSaturday.getDay() + 7) % 7;
  if (days === 0 && now.getHours() >= 9) days = 7;
  nextSaturday.setDate(now.getDate() + days);
  nextSaturday.setHours(10, 0, 0, 0);
  const nextSunday = new Date(nextSaturday);
  nextSunday.setDate(nextSunday.getDate() + 1);
  nextSunday.setHours(9, 0, 0, 0);
  const formatLocal = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  appendTimeInput(formatLocal(nextSaturday));
  appendTimeInput(formatLocal(nextSunday));
}
async function createGame(event) {
  event.preventDefault(); clearFlash();
  const btn = $('#create-submit'); buttonBusy(btn, true);
  try {
    const starts = $all('.time-row input').map(i => i.value);
    const duration_minutes = Number($('#game-duration').value);
    const min_players = Number($('#game-format').value);
    const data = await api('/api/games', {method: 'POST', body: JSON.stringify({starts, duration_minutes, min_players, ...(groupId ? {group_id: groupId} : {})})});
    location.href = `/?game=${encodeURIComponent(data.game_id)}${groupId ? `&group=${encodeURIComponent(groupId)}` : ''}`;
  } catch(e) { flash(e.message); buttonBusy(btn, false); }
}
function renderVotes(game, saved = []) {
  const root = $('#vote-times'); root.replaceChildren();
  for (const slot of game.slots) {
    const label = document.createElement('label');
    label.className = 'vote-option';
    const check = document.createElement('input');
    check.type = 'checkbox'; check.value = slot.id; check.checked = saved.includes(slot.id);
    const span = document.createElement('span');
    const when = document.createElement('strong'); when.textContent = timeLabel(slot.starts_at);
    const range = document.createElement('small'); range.textContent = `${clockLabel(slot.starts_at)}–${clockLabel(slot.ends_at || new Date(new Date(slot.starts_at).getTime() + game.duration_minutes * 60000).toISOString())}`;
    span.append(when, range);
    label.append(check, span);
    root.append(label);
  }
}
async function restorePlayer() {
  playerIdentity = null;
  try { playerIdentity = JSON.parse(localStorage.getItem(localIdentityKey()) || 'null'); } catch (_) {}
  if(GameOnAuth.signedIn && groupId){const me=await api(`/api/games/${encodeURIComponent(gameId)}/me`);playerIdentity=me.player_id?{player_id:me.player_id}:null;}
  if (!playerIdentity) return;
  try {
    const player = await api(`/api/games/${encodeURIComponent(gameId)}/players/${encodeURIComponent(playerIdentity.player_id)}`, {
      method: 'POST', body: JSON.stringify({player_token: playerIdentity.player_token})
    });
    const suburb = currentGame.players.find(p => p.id === playerIdentity.player_id)?.suburb || $('#player-suburb').value;
    $('#player-suburb').value = suburb;
    venueDiscovery?.setSuburb(suburb);
    $('#join-panel').hidden = true;
    $('#vote-panel').hidden = false;
    $('#greeting').textContent = `${player.name}, when can you play?`;
    renderVotes(currentGame, player.slot_ids);
    updateVoteButton();
    renderSteps();
  } catch(e) {
    localStorage.removeItem(localIdentityKey()); playerIdentity = null;
  }
}
async function joinGame(event) {
  event.preventDefault(); clearFlash();
  const btn = $('#join-form button'); buttonBusy(btn, true);
  try {
    const data = await api(`/api/games/${encodeURIComponent(gameId)}/players`, {
      method: 'POST', body: JSON.stringify({name: $('#player-name').value, suburb: $('#player-suburb').value})
    });
    if(data.player_token)localStorage.setItem(localIdentityKey(), JSON.stringify(data));
    playerIdentity = data;
    await refresh();
    await restorePlayer();
  } catch(e) { flash(e.message); }
  finally { buttonBusy(btn, false); }
}
async function vote(event) {
  event.preventDefault(); clearFlash();
  if (!playerIdentity) return flash('Please join this game first.');
  const button = $('#vote-form button'); buttonBusy(button, true);
  try {
    const slot_ids = $all('#vote-times input:checked').map(input => input.value);
    await api(`/api/games/${encodeURIComponent(gameId)}/votes`, {
      method: 'POST', body: JSON.stringify({...playerIdentity, slot_ids})
    });
    await refresh();
    button.textContent = 'Saved ✓';
    setTimeout(updateVoteButton, 1500);
  } catch(e) { flash(e.message); }
  finally { buttonBusy(button, false); }
}
function renderGame(game) {
  currentGame = game;
  const futureReady = game.status === 'ready' && game.ranking.some(r => r.available_count >= game.min_players && new Date(game.slots.find(s=>s.id===r.slot_id).starts_at)>new Date());
  currentReady = futureReady;
  // Public avatar seeds match the existing member records for display only.
  // Permissions continue to be enforced by the server, never by this label.
  const courtPlayers=game.players.map(player=>({...player,role:groupMembers.find(member=>member.avatar_seed===player.avatar_seed)?.role}));
  GameOnUI.renderCourt($('#session-court'),courtPlayers,{game:true,ready:futureReady,minPlayers:game.min_players});
  let guide = $('#ernie-game');
  if (!guide) { guide = document.createElement('aside'); guide.id='ernie-game'; $('#session-court').after(guide); }
  Ernie.game(guide,game,futureReady);
  $('#game-eyebrow').textContent = groupName ? `${groupName} · your next game` : 'Your next game';
  $('#game-heading').textContent = futureReady ? 'A time works. Let’s find a court.' : 'Let’s get your crew on court.';
  document.title = `${game.title || 'Your game'} — Game On!`;
  const format = game.min_players === 2 ? 'Singles' : game.min_players === 4 ? 'Doubles' : `${game.min_players}+ players`;
  const span = dateSpan(game.slots);
  $('#game-duration-label').textContent = [span, format, `${game.duration_minutes} minutes`, 'All times Melbourne'].filter(Boolean).join(' · ');
  $('#player-count').textContent = `${game.players.length} joined`;
  const status = $('#status');
  status.className = 'status' + (futureReady ? ' ready' : '');
  status.textContent = futureReady ? '✓ Enough players share a time. Time to choose a court.' : game.status === 'ready' ? 'The shared times have passed. The organiser can start a new game with future times.' :
    game.status === 'waiting_for_responses' ? 'Waiting for players to join and vote.' : 'No shared time yet. The organiser can start a new game with other times.';
  const people = $('#player-list'); people.replaceChildren();
  for (const p of game.players) {
    const chip = document.createElement('div'); chip.className = 'person';
    chip.textContent = `${p.has_voted ? '✓' : '○'} ${p.name} · ${p.suburb}${p.has_voted ? '' : ' · not voted yet'}`;
    people.append(chip);
  }
  const ranks = $('#rankings'); ranks.replaceChildren();
  for (const result of game.ranking) {
    const slot = game.slots.find(s=>s.id === result.slot_id);
    const row = document.createElement('div'); row.className = 'rank';
    const left = document.createElement('div');
    const heading = document.createElement('strong'); heading.textContent = timeLabel(slot.starts_at);
    const detail = document.createElement('small'); detail.textContent = result.available_names.join(', ') || 'No one yet';
    left.append(heading, detail);
    const score = document.createElement('span'); score.className = 'score';
    score.textContent = `${result.available_count} of ${game.min_players}`;
    row.append(left, score); ranks.append(row);
  }
  $('#next-step').textContent = game.explanation;
  updateVoteButton();
  renderSteps();
}
function dateSpan(slots) {
  if (!slots?.length) return '';
  const day = iso => new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Melbourne', day:'numeric', month:'short'}).format(new Date(iso));
  const sorted = slots.map(s => s.starts_at).sort();
  const first = day(sorted[0]), last = day(sorted[sorted.length - 1]);
  return first === last ? first : `${first} – ${last}`;
}
function updateVoteButton() {
  const button = $('#vote-form button');
  if (!button) return;
  const me = currentGame?.players.find(p => p.id === playerIdentity?.player_id);
  const voted = Boolean(me?.has_voted);
  button.textContent = voted ? 'Update availability' : 'Save availability';
  button.className = `${voted ? 'secondary' : 'primary'} full`;
}
// A handoff is an enquiry step, never evidence of a provider-confirmed booking.
function renderSteps() {
  if (!currentGame) return;
  const enough = currentGame.players.length >= currentGame.min_players;
  const current = selectedHandoff ? 'handoff' : currentReady ? 'court' : enough ? 'time' : 'crew';
  const order = ['crew', 'time', 'court', 'handoff'];
  for (const item of $all('#game-steps li')) {
    const index = order.indexOf(item.dataset.step), active = order.indexOf(current);
    item.classList.toggle('done', index < active);
    item.classList.toggle('active', index === active);
    if (index === active) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
  }
  // One primary action: share while gathering the crew (once you have joined and voted), otherwise the step's own action.
  const me = currentGame.players.find(p => p.id === playerIdentity?.player_id);
  $('#whatsapp').className = current === 'crew' && me?.has_voted ? 'primary' : 'secondary';
  $('#venue-mode').textContent = currentReady ? 'For your shared time' : 'Explore anytime';
  CourtFlow.update();
}
async function refresh() {
  if (!gameId) return;
  let game;
  try { game = await api(`/api/games/${encodeURIComponent(gameId)}`); }
  catch(e) { handleRefreshFailure(e); throw e; }
  renderGame(game);
  try { renderRecommendations(await api(`/api/games/${encodeURIComponent(gameId)}/recommendations`)); }
  catch(e) { handleRefreshFailure(e); $('#venue-status').textContent = 'Updates temporarily unavailable. The previous plan is still shown; actions recheck it before continuing.'; }
}
function handleRefreshFailure(error) {
  // A transient poll failure must not erase an otherwise useful plan.
  // Revoked access is different: remove private recommendations immediately.
  if ([401,403,404].includes(error.status)) {
    clearHandoff(); latestPlan=null; Ernie.planRevision=null; Ernie.hasRecommendation=false; Ernie.selectVenue=null;
    ErnieGamePlan.clear(); Ernie.planOutput?.replaceChildren(); CourtFlow.bar?.remove(); CourtFlow.dialog?.close(); renderGroupMap();
  } else if (selectedHandoff) {
    $('#handoff-panel').dataset.refreshFailed = 'true';
    $('#handoff-status').textContent='Updates temporarily unavailable. This previous plan will be rechecked before you continue. Nothing booked yet.';
  }
}
function renderGroupMap(plan = latestPlan) {
  const root = $('#group-map');
  if (!currentGame) return;
  const proposals = plan?.recommendations || [];
  const slot = proposals[0]?.selection?.slot_id;
  const ids = slot ? currentGame.ranking.find(r => r.slot_id === slot)?.available_player_ids || [] : null;
  const people = currentGame.players.filter(p => !ids || ids.includes(p.id)).map(p => ({...p, location:suburbLocations[p.suburb.trim().toLowerCase()]}));
  const key = JSON.stringify([people, proposals]);
  if (root.dataset.renderKey === key) return;
  root.dataset.renderKey = key; VenueView.dispose(root); root.replaceChildren();
  VenueView.enhance(root, {recommendations:proposals.map(p=>({venue:p.venue})), mode:'group_planning'}, {mode:'map',selected:proposals[0]?.venue_id}, null, null,
    {players:people, onSelect:id=>Ernie.selectVenue?.(id), planning:true});
}
function renderRecommendations(result) {
  if ($('#handoff-panel').dataset.refreshFailed === 'true') {
    delete $('#handoff-panel').dataset.refreshFailed;
    if (selectedHandoff) $('#handoff-status').textContent = 'Reselect the court and time on the booking page, then recheck availability and price. Nothing booked yet.';
  }
  Ernie.planning(result.can_prepare_handoff, () => api(`/api/games/${encodeURIComponent(gameId)}/plan`, {method: 'POST'}),
    selection => selectHandoff(selection, {fromErnie:true}), result.revision, plan => { latestPlan = plan; renderGroupMap(); });
  $('#planning-eligibility').textContent = !result.can_prepare_handoff ? (groupId ? 'Only the organiser can run Ernie and approve a venue. Everyone can join and vote.' : 'Ernie planning needs an organiser-owned group game.') : !currentReady ? 'Ernie can plan when submitted votes identify a qualifying shared future time.' : '';
  Ernie.setPlanningReady(currentReady);
  renderGroupMap();
  recommendationRevision = result.revision;
  if (selectedHandoff && (selectedHandoff.revision !== result.revision || result.status !== 'recommended' || !result.can_prepare_handoff)) {
    clearHandoff(); flash('The game or suggestions changed. Select a venue again for a fresh handoff.');
  }
  const root = $('#venue-results');
  // Unchanged polls must not replace focused keyboard controls or map state.
  const renderKey=JSON.stringify([result.revision,result.status,result.can_prepare_handoff,result.explanation]);
  if(root.dataset.renderKey===renderKey)return;
  root.dataset.renderKey=renderKey;
  const viewState = window.VenueView?.capture(root);
  VenueView.dispose(root);root.replaceChildren();
  $('#venue-status').textContent = result.recommendations.length ? 'Ranked by fair travel for everyone and your saved favourites.' : result.explanation;
  $('#venue-time').textContent = result.slot_starts_at ? `For ${timeLabel(result.slot_starts_at)}, ${result.duration_minutes} minutes` : '';
  $('#recommendations-heading').textContent = result.recommendations.length ? `Suggested for your game (${result.recommendations.length})` : 'Suggested for your game';

  let venueGuide=$('#ernie-venues');
  if(!venueGuide){venueGuide=document.createElement('aside');venueGuide.id='ernie-venues';root.before(venueGuide);}
  Ernie.show(venueGuide,'planner',result.recommendations.length ? `${result.recommendations.length} venue suggestions for ${result.duration_minutes} minutes. Compare preferences and approximate distances; availability and prices still need checking.` : result.explanation);
  for (const recommendation of result.recommendations) {
    const card = document.createElement('article'); card.className = 'venue-card'; card.dataset.venueId = recommendation.venue.id;
    const title = document.createElement('h3'); title.textContent = `${recommendation.venue.name} · ${recommendation.venue.suburb}`;
    const address = document.createElement('p'); address.className = 'small'; address.textContent = recommendation.venue.address;
    const favourite = document.createElement('p'); favourite.className = 'venue-tags';
    favourite.textContent = [recommendation.group_preferred ? 'Group favourite' : '',
      recommendation.individual_preference_count ? `Preferred by ${recommendation.individual_preference_count} player${recommendation.individual_preference_count === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ') || 'Alternative venue';
    const distance = document.createElement('p'); distance.className = 'small';
    distance.textContent = `About ${recommendation.average_distance_km.toFixed(1)} km on average · ${recommendation.longest_distance_km.toFixed(1)} km for the furthest player`;
    const reason = document.createElement('details'); reason.className = 'small why';
    const why = document.createElement('summary'); why.textContent = 'Why it’s suggested';
    const reasonText = document.createElement('p'); reasonText.textContent = recommendation.reason; reason.append(why, reasonText);
    const link = document.createElement('a'); link.href = recommendation.venue.booking_url || recommendation.venue.website_url;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Venue website ↗';
    const source = document.createElement('p'); source.className = 'small source-line'; source.textContent = `Venue details checked ${recommendation.venue.checked_on}`;
    card.append(title, address, favourite, distance, reason, link, source); root.append(card);
    if (result.can_prepare_handoff) {
      const select = document.createElement('button'); select.type = 'button'; select.className = 'secondary handoff-select';
      select.textContent = 'Choose this venue';
      select.disabled = handoffBusy;
      select.onclick = () => selectHandoff({revision: result.revision, slot_id: result.slot_id, venue_id: recommendation.venue.id});
      card.append(select);
    }
  }
  if (!result.can_prepare_handoff && result.recommendations.length) {
    const note = document.createElement('p'); note.className = 'small';
    note.textContent = groupId ? 'Your organiser chooses the venue and takes the plan to the venue to book.' : 'Booking handoffs are for groups. Voting and venue links still work here.';
    root.append(note);
  }
  window.VenueView?.enhance(root, result, viewState, selectHandoff, null, {listOnly: true});
}
function clearHandoff() {
  selectedHandoff = null; handoffData = null;
  $('#handoff-panel').hidden = true;
  $('#handoff-next').hidden = true;
  $('#handoff-brief').value = '';
  $('.brief-details').open = false;
  renderSteps();
}
function setHandoffBusy(busy) {
  handoffBusy = busy;
  for (const button of $all('.handoff-select, #handoff-open, #handoff-copy')) button.disabled = busy;
}
async function validatedHandoff(selection) {
  return api(`/api/games/${encodeURIComponent(gameId)}/booking-handoff`, {method: 'POST', body: JSON.stringify(selection)});
}
function renderHandoff(data) {
  selectedHandoff = data.selection; handoffData = data;
  $('#handoff-panel').hidden = false;
  $('#handoff-summary').textContent = `${data.venue_name} · ${timeLabel(data.starts_at)} to ${timeLabel(data.ends_at)} (Melbourne) · ${data.duration_minutes} minutes · ${data.player_count} players available (${data.session_player_count} joined)`;
  $('#handoff-venue').textContent = data.venue_name;
  const address = data.venue_address?.trim();
  $('#handoff-address').textContent = address && address.toLowerCase() !== data.venue_suburb?.toLowerCase()
    ? address : `${data.venue_suburb || 'Location'} · Exact address unverified`;
  $('#handoff-when').textContent = `${timeLabel(data.starts_at)}–${clockLabel(data.ends_at)}`;
  const format = currentGame?.min_players === 2 ? 'Singles' : currentGame?.min_players === 4 ? 'Doubles' : 'Game';
  $('#handoff-format').textContent = `${format} · ${data.duration_minutes} min`;
  $('#handoff-players').textContent = `${data.player_count} available of ${data.session_player_count} joined`;
  $('#handoff-status').textContent = 'Reselect the court and time on the booking page, then recheck availability and price. Nothing booked yet.';
  CourtPricing.render($('#handoff-pricing'), data.pricing);
  const avatars = $('#handoff-avatars'); avatars.replaceChildren();
  for (const avatar of $all('#session-court .human-avatar')) { const copy = avatar.cloneNode(true); avatars.append(copy); }
  $('#handoff-copy').textContent = 'Send to Personal Assistant';
  $('#handoff-next').hidden = true;
  $('#handoff-copy').hidden = false;
  $('#handoff-open').textContent = 'Continue to booking';
  renderSteps();
  $('#handoff-brief').value = data.brief;
  $('#handoff-open').hidden = !data.booking_url;
  $('#handoff-missing').hidden = Boolean(data.booking_url);
}
async function selectHandoff(selection, {fromErnie=false} = {}) {
  if (handoffBusy) { if (fromErnie) throw new Error('A handoff is already being prepared. Try again shortly.'); return; }
  clearHandoff(); clearFlash(); setHandoffBusy(true);
  try {
    const data = await validatedHandoff(selection);
    if (recommendationRevision !== selection.revision) throw new Error('Suggestions changed. Select again.');
    renderHandoff(data);
    CourtFlow.go('handoff');
  } catch(e) { clearHandoff(); flash(e.message); if (fromErnie) throw e; }
  finally { setHandoffBusy(false); }
}
async function useHandoff(action) {
  if (!selectedHandoff || handoffBusy) return;
  const selection = selectedHandoff;
  // Open a blank tab within the click gesture; never navigate until server revalidation succeeds.
  const popup = action === 'open' ? window.open('about:blank', '_blank') : null;
  if (popup) popup.opener = null;
  setHandoffBusy(true); clearFlash();
  try {
    const data = await validatedHandoff(selection);
    if (!selectedHandoff || recommendationRevision !== selection.revision) throw new Error('Suggestions changed. Select again.');
    renderHandoff(data);
    if (action === 'open') {
      if (!data.booking_url) throw new Error('No trusted booking page is listed. Select a venue again.');
      if (!popup) throw new Error('Allow a new tab in your browser, then select the venue and try again.');
      popup.location.replace(data.booking_url);
      $('#handoff-next').hidden = false; $('#handoff-next-title').textContent = `Continue with ${data.venue_name}`;
      $('#handoff-next-description').textContent = 'Use the new tab to check availability and complete your booking with the venue. Game On’s planning step is complete. Your court is not booked yet.';
      $('#handoff-status').textContent = '';
      $('#handoff-open').textContent = 'Open website again ↗';
      $('#handoff-copy').hidden = false;
      $('.brief-details').open = false;
    } else {
      try {
        await navigator.clipboard.writeText(data.brief);
        $('#handoff-next').hidden = false; $('#handoff-next-title').textContent = 'Copied!'; $('#handoff-copy').textContent = 'Copied!';
        $('#handoff-next-description').textContent = 'Paste it into your AI assistant to continue with the venue. Game On’s planning step is complete. Availability and price are unchecked; your court is not booked yet.';
        $('#handoff-status').textContent = '';
        $('.brief-details').open = false;
      } catch (_) {
        $('.brief-details').open = true; $('#handoff-brief').focus(); $('#handoff-brief').select();
        $('#handoff-status').textContent = 'Clipboard unavailable. Copy the selected instructions manually. No reservation made.';
      }
    }
  } catch(e) { if (popup) popup.close(); clearHandoff(); flash(e.message); }
  finally { setHandoffBusy(false); }
}
async function copyShareLink() {
  try { await navigator.clipboard.writeText(location.href); $('#copy-link').textContent = 'Copied ✓'; setTimeout(() => { $('#copy-link').textContent = 'Copy link'; }, 2000); }
  catch(_) { window.prompt('Copy this session link:', location.href); }
}
function whatsappShare() {
  const text = `🏓 ${currentGame.title}\nPick the times you can play and enter your suburb:\n${location.href}`;
  window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, '_blank', 'noopener,noreferrer');
}
function savedGroups() {
  if(GameOnAuth.signedIn)return GameOnAuth.groups;
  const saved = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith('gameon-group:')) continue;
      try { saved.push({id: key.slice('gameon-group:'.length), name: JSON.parse(localStorage.getItem(key))?.name || 'Your group'}); } catch (_) {}
    }
  } catch (_) {}
  return saved;
}
function renderHome() {
  $('#home-view').hidden = false;
  Ernie.show($('#ernie-welcome'),'player','Hey! Ready to rally your crew? Create a group, invite your friends, then choose times together.');
  const saved = savedGroups(), list = $('#home-group-list');
  $('#home-groups').hidden = !saved.length;
  list.replaceChildren();
  for (const group of saved) {
    const link = document.createElement('a'); link.className = 'group-link';
    link.href = `/groups.html?group=${encodeURIComponent(group.id)}`;
    const name = document.createElement('strong'); name.textContent = group.name;
    const go = document.createElement('span'); go.textContent = 'Open group →';
    link.append(name, go); list.append(link);
  }
}
async function setup() {
  try { await GameOnAuth.ready; }catch(e){flash(e.message);return;}
  if(groupId && GameOnAuth.enabled && !GameOnAuth.signedIn && !groupCredential){GameOnAuth.login();return;}
  $('#create-form').addEventListener('submit', createGame);
  $('#add-time').addEventListener('click', () => appendTimeInput());
  $('#join-form').addEventListener('submit', joinGame);
  $('#vote-form').addEventListener('submit', vote);
  $('#copy-link').addEventListener('click', copyShareLink);
  $('#whatsapp').addEventListener('click', whatsappShare);
  $('#handoff-open').addEventListener('click', () => useHandoff('open'));
  $('#handoff-copy').addEventListener('click', () => useHandoff('copy'));
  Ernie.mountPlanner($('#planning-entry'), $('#planning-output'));
  try { suburbLocations = (await api('/api/venues')).suburb_locations || {}; } catch (_) { /* Map explains unknown locations; voting remains available. */ }
  // Home is groups-first: games start from inside a group. Older standalone game links still open below.
  if (!gameId && !groupId) { renderHome(); return; }
  if (groupId) {
    try {
      const group = await api(`/api/groups/${encodeURIComponent(groupId)}`);
      groupMembers = group.members || [];
      $('#create-heading').textContent = 'Put a game in play';
      groupName = group.name;
      $('#group-context').hidden = false; $('#group-context').textContent = group.name;
      $('#player-name').value = group.me.name; $('#player-suburb').value = group.me.suburb;
      $('#player-name').readOnly = true; $('#player-suburb').readOnly = true;
      $('#share-note').textContent = 'This link works for members of the group. Friends who haven’t joined need the group invitation first.';
    } catch(e) { flash(e.message + ' Open My groups to restore access.'); return; }
  }
  if (!gameId) {
    $('#create-view').hidden = false;
    setDefaultDates();
    return;
  }
  $('#game-view').hidden = false;
  try {
    await refresh();
    await restorePlayer();
    venueDiscovery = VenueDiscovery.mount($('#venue-discovery'), $('#player-suburb').value);
    $('#player-suburb').addEventListener('input', () => venueDiscovery.setSuburb($('#player-suburb').value, false));
    $('#player-suburb').addEventListener('change', () => venueDiscovery.search());
    setInterval(() => refresh().catch(console.warn), 10000);
  } catch(e) { flash(e.message); }
}
setup();

CourtFlow.init();
$('#handoff-calendar').onclick = async () => {
  if (!selectedHandoff || handoffBusy) return;
  setHandoffBusy(true);
  try {
    const data = await validatedHandoff(selectedHandoff);
    if (!selectedHandoff || data.selection.revision !== recommendationRevision) throw new Error('The game changed. Review your court plan again.');
    const url = URL.createObjectURL(new Blob([CourtFlow.calendar(data)], {type:'text/calendar;charset=utf-8'}));
    const link = document.createElement('a'); link.href=url; link.download='game-on-court-plan.ics'; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  } catch(e) { flash(e.message || 'Calendar download unavailable.'); }
  finally { setHandoffBusy(false); }
};

$('#handoff-whatsapp').onclick = whatsappShare;
$('#handoff-share').onclick = async () => { await copyShareLink(); $('#handoff-share').textContent = $('#copy-link').textContent; };
