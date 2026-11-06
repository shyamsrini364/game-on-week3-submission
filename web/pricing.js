// Server-matched historical observation, never a current availability quote.
window.CourtPricing = {
  money(price, minor) { return `${price.currency || '$'}${price.currency ? ' ' : ''}${(minor / 100).toFixed(2)}`; },
  render(root, price) {
    root.replaceChildren(); root.className = 'court-pricing';
    const add = (tag, text) => { const el = document.createElement(tag); el.textContent = text; root.append(el); return el; };
    if (!price) { add('strong', 'Price to confirm'); return; }
    const money = minor => `${price.currency || '$'}${price.currency ? ' ' : ''}${(minor / 100).toFixed(2)}`;
    add('p', `${price.category} · ${price.court_name}`).className = 'court-identity';
    add('strong', `Total court cost: ${money(price.total_minor)}`).className = 'court-price-total';
    if (price.estimated_per_player_minor != null) add('p', `Estimated per player: ${money(price.estimated_per_player_minor)} (${price.participant_count} players; calculated split, rounded)`);
    const details = document.createElement('details'); details.className = 'price-details';
    const summary = document.createElement('summary'); summary.textContent = `Price details · cached estimate${price.stale ? ' · stale' : ''}${!price.currency ? ' · currency unverified' : ''}`;
    details.append(summary); root.append(details);
    const date = new Intl.DateTimeFormat('en-AU', {timeZone:'Australia/Melbourne',dateStyle:'medium',timeStyle:'short'}).format(new Date(price.observed_at));
    add('p', `Last checked: ${date} (Melbourne)${price.stale ? ' · Stale — over 14 days old' : price.refresh_due ? ' · Refresh due' : ''}`).className = 'small';
    if (price.refresh_status === 'failed') add('p', 'Latest refresh failed; previous observation retained.').className = 'small';
    add('p', 'Cached estimate for this court and time, including observed fees. Recheck the current total and availability with the venue.').className = 'small';
    if (!price.currency) add('p', 'Currency unverified: provider displayed $ without a currency code.').className = 'small';
    [...root.querySelectorAll(':scope > .small')].forEach(line => details.append(line));
  }
};
