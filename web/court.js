// Illustrated court and avatar presets. Public visual seeds have no authority and are never credentials.
// Stored avatar IDs are unchanged (auto, rally, ace, sunny, river, sky, blaze); only the artwork is new.
window.GameOnUI = (() => {
  const presets = [
    {id:'rally', name:'Rally'},
    {id:'ace', name:'Ace'},
    {id:'sunny', name:'Sunny'},
    {id:'river', name:'River'},
    {id:'sky', name:'Sky'},
    {id:'blaze', name:'Blaze'}
  ];
  // Automatic members get a stable character from their public seed; with no seed yet, the picker shows the Automatic portrait.
  const artwork = ['auto', ...presets.map(p => p.id)];
  const src = id => `/assets/avatars/${id}.webp`;
  function artworkFor(person = {}) {
    if (presets.some(p => p.id === person.avatar_id)) return person.avatar_id;
    const seed = /^[a-f0-9]{12}$/.test(person.avatar_seed || '') ? person.avatar_seed : '';
    return seed ? artwork[parseInt(seed.slice(0, 8), 16) % artwork.length] : 'auto';
  }
  function avatar(person) {
    const img = document.createElement('img');
    img.className = 'human-avatar'; img.alt = ''; img.width = 120; img.height = 120;
    img.decoding = 'async'; img.loading = 'lazy';
    img.src = src(artworkFor(person));
    return img;
  }
  // Spot order fills both sides of the net: left-back, right-back, left-front, right-front.
  const SPOTS = ['spot-lt', 'spot-rt', 'spot-lb', 'spot-rb'];
  function spot(person, index, isGame) {
    const item = document.createElement('div');
    item.className = `court-spot ${SPOTS[index]}`;
    if (!person) {
      item.classList.add('open-spot');
      const plus = document.createElement('span'); plus.className = 'spot-plus'; plus.setAttribute('aria-hidden', 'true'); plus.textContent = '+';
      const label = document.createElement('span'); label.className = 'visually-hidden'; label.textContent = 'Open spot';
      item.append(plus, label); return item;
    }
    item.append(player(person, isGame)); return item;
  }
  function player(person, isGame) {
    const item = document.createElement('div'); item.className = 'court-player';
    item.dataset.avatarSeed = person.avatar_seed || ''; item.dataset.avatarId = person.avatar_id || 'auto';
    const portrait = document.createElement('div');
    portrait.className = 'avatar-ring' + (isGame && person.has_voted ? ' voted' : '');
    portrait.append(avatar(person));
    const name = document.createElement('strong'); name.textContent = person.name;
    const status = document.createElement('span'); status.className = 'avatar-status';
    status.textContent = isGame ? (person.has_voted ? '✓ Voted' : 'Not voted yet') : (person.role === 'owner' ? 'Organiser' : 'Member');
    if (isGame && person.role === 'owner') {
      status.classList.add('organiser');
      status.textContent = `Organiser · ${status.textContent}`;
    }
    item.append(portrait, name, status); return item;
  }
  function renderCourt(root, people, {game=false, ready=false, minPlayers=4} = {}) {
    const state = JSON.stringify([people, game, ready, minPlayers]);
    if (root.dataset.state === state) return;
    root.dataset.state = state;
    const expanded = root.querySelector('details')?.open || false;
    root.replaceChildren(); root.className = 'crew-court' + (ready ? ' court-ready' : '');
    const stage = document.createElement('div'); stage.className = 'court-stage';
    const art = document.createElement('img'); art.className = 'court-art'; art.src = '/assets/court/court-empty.webp';
    stage.dataset.populated = String(people.length > 0);
    stage.setAttribute('aria-label', people.length ? 'Your joined crew on court' : 'Empty court — no players have joined');
    art.alt = ''; art.width = 1400; art.height = 700; art.decoding = 'async';
    const spots = document.createElement('div'); spots.className = 'court-spots';
    spots.setAttribute('role', 'list'); spots.setAttribute('aria-label', game ? 'Players in this game' : 'Group members');
    for (let i = 0; i < 4; i++) { const s = spot(people[i], i, game); s.setAttribute('role', 'listitem'); spots.append(s); }
    stage.append(art, spots); root.append(stage);

    const caption = document.createElement('div'); caption.className = 'court-caption';
    const count = document.createElement('h2'); count.className = 'court-count';
    const voted = people.filter(p => p.has_voted).length, pending = people.length - voted;
    const number = document.createElement('strong');
    if (game) {
      number.textContent = people.length < minPlayers ? `${people.length} of ${minPlayers}` : String(people.length);
      count.append(number, ` player${people.length === 1 && people.length >= minPlayers ? '' : 's'} joined`);
    } else {
      number.textContent = String(people.length);
      count.append(number, ` member${people.length === 1 ? '' : 's'} in your crew`);
    }
    const note = document.createElement('p'); note.className = 'court-guidance';
    note.textContent = game
      ? (ready ? 'A shared time works. Next: ask Ernie to find our court.'
        : people.length < minPlayers ? `${minPlayers - people.length} more player${minPlayers - people.length === 1 ? '' : 's'} to get this game going.`
        : pending ? `Waiting on ${pending} vote${pending === 1 ? '' : 's'}.`
        : 'Everyone has voted. See the times below.')
      : (people.length < 2 ? 'Invite friends to build your crew.' : 'Your crew stays together for every game.');
    caption.append(count, note);
    if (game) {
      const stats = document.createElement('p'); stats.className = 'court-stats';
      stats.textContent = `${people.length} joined · ${voted} voted · ${pending} still to vote`;
      caption.append(stats);
    }
    root.append(caption);
    if (people.length > 4) {
      const more = document.createElement('details'); more.open = expanded; more.className = 'more-crew';
      const summary = document.createElement('summary'); summary.textContent = `Rest of the crew (+${people.length - 4})`;
      const list = document.createElement('div'); list.className = 'extra-players';
      for (const person of people.slice(4)) list.append(player(person, game));
      more.append(summary, list); root.append(more);
    }
  }
  function avatarPicker(root, selected='auto', seed) {
    root.replaceChildren(); root.className = 'avatar-picker';
    for (const preset of [{id:'auto', name:'Automatic'}, ...presets]) {
      const label = document.createElement('label'); label.className = 'avatar-choice';
      const input = document.createElement('input'); input.type = 'radio'; input.name = root.id; input.value = preset.id; input.checked = preset.id === (selected || 'auto');
      const name = document.createElement('span'); name.textContent = preset.name;
      label.append(input, avatar({avatar_id: preset.id, avatar_seed: seed}), name); root.append(label);
    }
  }
  return {avatar, renderCourt, avatarPicker};
})();
