// Curated catalogue UI; stable venue IDs remain the persistence contract.
// A future provider can supply search results without changing saved favourites.
function venueMatches(venue, query) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const text = `${venue.name} ${venue.suburb}`.toLocaleLowerCase();
  return words.every(word => text.includes(word));
}
function createVenuePicker(root, venues, selected = []) {
  const chosen = new Set(selected);
  root.replaceChildren();
  const label = document.createElement('label');
  const search = document.createElement('input');
  search.type = 'search'; search.id = `${root.id}-search`; search.maxLength = 100;
  label.htmlFor = search.id; label.textContent = 'Search venues by name or suburb';
  const selectedLabel = document.createElement('p'); selectedLabel.textContent = 'Selected favourites';
  const saved = document.createElement('div'); saved.className = 'selected-venues';
  const status = document.createElement('p'); status.className = 'small'; status.setAttribute('role', 'status');
  const results = document.createElement('div'); results.className = 'venue-search-results';
  root.append(label, search, selectedLabel, saved, status, results);
  if(window.VenueShortlist){
    const use=document.createElement('button');use.type='button';use.className='secondary';use.textContent='Use my browsing shortlist';
    use.onclick=()=>{const ids=VenueShortlist.read().filter(id=>venues.some(v=>v.id===id));for(const id of ids){if(chosen.size<10)chosen.add(id);}render();status.textContent=ids.length?'Browsing preferences added. Save this form to apply them.':'No browsing preferences saved on this device. Open Venues to choose some.';};root.append(use);
  }
  function render() {
    saved.replaceChildren(); results.replaceChildren();
    for (const id of chosen) {
      const venue = venues.find(v => v.id === id);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary';
      remove.textContent = `${venue ? venue.name : 'Previously saved venue'} - Remove`;
      remove.setAttribute('aria-label', `Remove ${venue ? venue.name : 'previously saved venue'} from favourites`);
      remove.onclick = () => { chosen.delete(id); render(); search.focus(); };
      saved.append(remove);
    }
    if (!chosen.size) saved.textContent = 'No favourites selected yet.';
    const matches = venues.filter(v => venueMatches(v, search.value));
    status.textContent = matches.length ? `${matches.length} venues found. Select any favourites below. Save the form to keep changes.` :
      'No venues match. Try another name or suburb. Our directory is limited; this does not mean there are no courts nearby.';
    for (const venue of matches) {
      const option = document.createElement('label'); option.className = 'vote-option';
      const check = document.createElement('input'); check.type = 'checkbox'; check.value = venue.id; check.checked = chosen.has(venue.id);
      const text = document.createElement('span'); text.textContent = `${venue.name} · ${venue.suburb}`;
      check.onchange = () => {
        if (check.checked) chosen.add(venue.id); else chosen.delete(venue.id);
        render();
        // Keep keyboard navigation on the changed option after rendering.
        [...results.querySelectorAll('input')].find(i => i.value === venue.id)?.focus();
      };
      option.append(check, text); results.append(option);
    }
  }
  search.addEventListener('input', render);
  render();
  return {selected: () => [...chosen]};
}
