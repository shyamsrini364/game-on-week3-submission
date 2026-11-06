// A device-local planning shortlist, separate from authenticated group favourites.
window.VenueShortlist = {
  key: 'gameon-venue-shortlist',
  read() {
    try { const value=JSON.parse(localStorage.getItem(this.key)||'[]');return Array.isArray(value)?value.filter(v=>typeof v==='string').slice(0,10):[]; }
    catch (_) { return []; }
  },
  write(ids) { try {localStorage.setItem(this.key,JSON.stringify([...new Set(ids)].slice(0,10)));return true;} catch (_) {return false;} }
};
