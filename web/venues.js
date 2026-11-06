window.VenueDiscovery = (() => {
 function mount(container, initialSuburb = '') {
  container.innerHTML = `<div><form id="suburb-search"><label for="browse-suburb">Which suburb would you like to play near?</label><input type="search" id="browse-suburb" list="browse-suburbs" maxlength="100" placeholder="e.g. Clyde or Lyndhurst" aria-describedby="browse-help"><datalist id="browse-suburbs"></datalist><p id="browse-help" class="small">Use a suburb, not a home address.</p><button type="submit" class="primary" id="browse-submit">Find venues</button> <button type="button" class="text-button" id="browse-all">Show all venues</button></form>
<p id="browse-status" role="status" aria-live="polite"></p><p id="browse-error" role="alert" hidden></p><button id="browse-retry" class="secondary" type="button" hidden>Try again</button><p id="browse-source" class="small"></p></div>
<div aria-labelledby="shortlist-heading"><h2 id="shortlist-heading">Your preferred venues</h2><p id="shortlist-status" role="status"></p><p class="small">Saved in this browser only. To add them to a group, open My groups and choose “Use my browsing shortlist”.</p><a href="/groups.html">Open My groups</a></div>
<div aria-label="Venue directory"><div id="browse-results"></div></div>`;
  const inline = Boolean(container.closest('#game-view'));
  const $=s=>container.querySelector(s), root=$('#browse-results');
  if (inline) {
    container.classList.add('court-discovery');
    $('label[for="browse-suburb"]').textContent = 'Play near';
    $('#browse-suburb').placeholder = 'Search a suburb, e.g. Berwick';
    $('#browse-submit').textContent = 'Search';
    $('#browse-submit').className = 'secondary';
    $('#browse-all').textContent = 'Show all venues';
    root.dataset.defaultMode = 'map';
    const status = $('#shortlist-status');
    status.className = 'visually-hidden';
    $('[aria-labelledby="shortlist-heading"]').replaceWith(status);
  }
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  let chosen=new Set(VenueShortlist.read()), catalogue=[], sequence=0, persisted=true;
  function updateShortlist(saved=persisted){
    if (inline) $('#shortlist-status').className = saved ? 'visually-hidden' : 'small';
    $('#shortlist-status').textContent=(chosen.size?`${chosen.size} preferred: ${catalogue.filter(v=>chosen.has(v.id)).map(v=>v.name).join(', ')}`:'No preferred venues selected yet.')+(saved?'':' Browser storage is unavailable; selections last only while this page is open.');
    root.querySelectorAll('.browse-prefer').forEach(b=>{const active=chosen.has(b.dataset.venueId);b.setAttribute('aria-pressed',String(active));b.textContent=active?'♥ Saved':'♡ Save venue';});
  }
  function preferenceButton(venue){const b=el('button','','secondary browse-prefer');b.type='button';b.dataset.venueId=venue.id;
    const active=chosen.has(venue.id);b.setAttribute('aria-pressed',String(active));b.textContent=active?'♥ Saved':'♡ Save venue';b.setAttribute('aria-label',`Save ${venue.name} to your preferred venues`);
    b.onclick=()=>{if(chosen.has(venue.id))chosen.delete(venue.id);else if(chosen.size<10)chosen.add(venue.id);persisted=VenueShortlist.write([...chosen]);updateShortlist();};return b;}
  async function search(){
    const request=++sequence;$('#browse-error').hidden=true;$('#browse-retry').hidden=true;$('#browse-status').textContent='Loading venue directory…';root.setAttribute('aria-busy','true');
    try{
      const r=await fetch('/api/venues/browse?'+new URLSearchParams({suburb:$('#browse-suburb').value}));if(!r.ok)throw new Error('unavailable');const data=await r.json();if(request!==sequence)return;
      catalogue=data.venues.map(v=>v.venue);chosen=new Set([...chosen].filter(id=>catalogue.some(v=>v.id===id)));
      const state=VenueView.capture(root);VenueView.dispose(root);root.replaceChildren();$('#browse-status').textContent=data.status==='located'?`${data.venues.length} venues, nearest first.`:data.status==='unknown_suburb'?data.message:`Showing all ${data.venues.length} venues. Enter a suburb to sort by distance.`;
      $('#browse-suburbs').replaceChildren(...data.suburbs.map(name=>{const o=el('option');o.value=name;return o;}));
      $('#browse-source').replaceChildren();if(data.location_source_url){const a=el('a','Suburb reference point');a.href=data.location_source_url;a.target='_blank';a.rel='noopener noreferrer';$('#browse-source').append(a);}
      for(const entry of data.venues){const v=entry.venue,card=el('article',null,'venue-card');card.dataset.venueId=v.id;
        card.append(el('h3',v.name),el('p',v.suburb+' · '+v.address,'small'));if(entry.approximate_distance_km!==null)card.append(el('p',`About ${entry.approximate_distance_km.toFixed(1)} km away`,'venue-distance'));card.append(el('p',`Details checked ${v.checked_on}`,'small source-line'));
        const source=el('a','Source ↗');source.href=v.sources[0];source.target='_blank';source.rel='noopener noreferrer';card.append(source,preferenceButton(v));root.append(card);
      }
      VenueView.enhance(root,{recommendations:data.venues,can_prepare_handoff:false,mode:'general_browse',search_location:data.search_location},state,null,preferenceButton);
      if(!catalogue.length)root.append(el('p','The directory is currently empty. Try again later; this does not mean no venues exist.'));
      updateShortlist();
    }catch(_){if(request!==sequence)return;$('#browse-status').textContent='Venue search is temporarily unavailable.';$('#browse-error').textContent=catalogue.length?'Couldn’t refresh venues. Your previous results are still below. That doesn’t mean there are no courts.':'Couldn’t load venues right now. That doesn’t mean there are no courts. You can still vote.';$('#browse-error').hidden=false;$('#browse-retry').hidden=false;}
    finally{if(request===sequence)root.removeAttribute('aria-busy');}
  }
  $('#suburb-search').onsubmit=e=>{e.preventDefault();search();};$('#browse-all').onclick=()=>{$('#browse-suburb').value='';search();};$('#browse-retry').onclick=search;
  root.addEventListener('click',()=>updateShortlist());
  let edited = false;
  $('#browse-suburb').addEventListener('input', () => { edited = true; });
  $('#browse-all').addEventListener('click', () => { edited = true; });
  const setSuburb = (suburb, runSearch = true) => {
    if (edited || $('#browse-suburb').value === suburb) return;
    $('#browse-suburb').value = suburb;
    if (runSearch) search();
  };
  $('#browse-suburb').value = initialSuburb;
  search();
  return {setSuburb, search};
 }
 return {mount};
})();

const standaloneDiscovery = document.querySelector('#venue-discovery[data-auto-mount]');
if (standaloneDiscovery) VenueDiscovery.mount(standaloneDiscovery);
