// Presentation only: existing server selections and approval checks remain authoritative.
window.CourtFlow = {
  init() {
    this.side = document.querySelector('.workspace-side');
    if (matchMedia('(max-width:700px)').matches) document.querySelector('#court-map-details').open=false;
    const summary = document.createElement('button');
    summary.type = 'button'; summary.className = 'secondary coordination-summary';
    summary.onclick = () => { const open = this.side.classList.toggle('expanded'); summary.setAttribute('aria-expanded', String(open)); };
    summary.setAttribute('aria-expanded', 'false'); this.side.before(summary); this.summary = summary;
    document.querySelectorAll('#game-steps li').forEach(li => {
      const button = document.createElement('button'); button.type = 'button';
      button.append(...li.childNodes); li.append(button);
      button.onclick = () => this.go(li.dataset.step);
    });
    document.querySelector('#change-court').onclick = () => this.go('court');
    document.querySelector('#handoff-other-court').onclick = () => this.go('court');
    document.querySelector('#handoff-review-times').onclick = () => this.go('time');
    window.addEventListener('popstate', () => this.update());
  },
  go(step) {
    const url = new URL(location.href);
    if (!url.searchParams.has('step')) {
      url.searchParams.set('step', step === 'handoff' ? 'court' : this.step || 'crew');
      history.replaceState(null, '', url);
    }
    url.searchParams.set('step', step);
    history.pushState(null, '', url); this.update(); window.scrollTo({top:0});
    document.querySelector('#game-heading').focus({preventScroll:true});
  },
  update() {
    if (!this.side || !currentGame) return;
    const natural = selectedHandoff ? 'handoff' : currentReady ? 'court' : currentGame.players.length >= currentGame.min_players ? 'time' : 'crew';
    const requested = new URLSearchParams(location.search).get('step');
    let step = ['crew','time','court','handoff'].includes(requested) ? requested : natural;
    if (step === 'handoff' && !selectedHandoff) step = natural;
    if (step === 'court' && !currentReady) step = natural;
    this.step = step; ErnieGamePlan.show(); document.querySelector('#game-view').dataset.step = step;
    document.querySelector('#handoff-panel').hidden = step !== 'handoff' || !selectedHandoff;
    document.querySelector('#venues-panel').hidden = step === 'handoff' || step === 'time';
    document.querySelector('#planning-entry').hidden = step !== 'court';
    this.summary.hidden = !['court','handoff'].includes(step);
    this.summary.textContent = `${currentGame.players.length} players · View availability and group progress`;
    const order = ['crew','time','court','handoff'];
    document.querySelectorAll('#game-steps li').forEach(li => {
      const name = li.dataset.step;
      li.classList.toggle('active', name === step);
      li.classList.toggle('done', order.indexOf(name) < order.indexOf(natural));
      li.querySelector('button').disabled = name === 'handoff' ? !selectedHandoff : name === 'court' ? !currentReady : false;
      if(name === step) li.setAttribute('aria-current','step'); else li.removeAttribute('aria-current');
    });
    if(this.bar) this.bar.hidden = step !== 'court' || !Ernie.hasRecommendation;
  },
  render(result, output, approve) {
    this.barObserver?.disconnect(); this.bar?.remove(); this.dialog?.remove();
    const add = (tag, text, parent, cls) => { const e=document.createElement(tag); e.textContent=text; if(cls)e.className=cls; parent.append(e); return e; };
    const first=result.recommendations[0];
    document.querySelector('#venues-panel h2').textContent='Choose a court';
    document.querySelector('#venues-panel > p.small').textContent=`${timeLabel(first.starts_at)} · ${first.duration_minutes} minutes · ${first.player_count} players`;
    const list=add('div','',output,'court-options'); list.setAttribute('role','radiogroup'); list.setAttribute('aria-label','Choose a court');
    const bar=add('div','',output,'court-action-bar'); this.bar=bar;
    this.barObserver=new ResizeObserver(()=>document.querySelector('#game-view').style.setProperty('--court-bar-height',`${bar.getBoundingClientRect().height+48}px`)); this.barObserver.observe(bar);
    const summary=add('strong','',bar);
    const actions=add('div','',bar,'share-actions');
    const accept=add('button','Approve & continue →',actions,'primary'); accept.type='button';
    const compare=add('button','Compare',actions,'text-button'); compare.type='button';
    const error=add('p','',bar); error.setAttribute('role','status');
    const money=p=>p ? `${CourtPricing.money(p,p.total_minor)}${p.estimated_per_player_minor == null ? '' : ` (${CourtPricing.money(p,p.estimated_per_player_minor)} each)`}` : 'Price to confirm';
    let selected=first, busy=false;
    const cards=[];
    const highlight=id=>{const map=document.querySelector('#group-map'); map.dispatchEvent(new CustomEvent('venue-select',{detail:id}));};
    const choose=(v,focus=false)=>{
      if(busy)return;
      selected=v; this.selected=v;
      if (selectedHandoff && selectedHandoff.venue_id !== v.venue_id) clearHandoff();
      cards.forEach((card,i)=>{const checked=result.recommendations[i]===v; card.setAttribute('aria-checked',String(checked)); card.tabIndex=checked?0:-1; card.querySelector('.selected-pill').hidden=!checked; card.querySelector('.choice-circle').textContent=checked?'✓':''; if(checked&&focus)card.focus();});
      summary.textContent=`${v.name} · ${v.suburb} · ${money(v.pricing)}`; accept.disabled=!v.selection; highlight(v.venue_id);
    };
    result.recommendations.forEach((v,i)=>{
      const card=add('article','',list,`venue-card plan-card${i===0?' plan-top':' plan-alternative'}`); cards.push(card);
      card.dataset.venueId=v.venue_id; card.setAttribute('role','radio'); card.setAttribute('aria-label',`${v.name}, ${money(v.pricing)}`);
      add('span','',card,'choice-circle').setAttribute('aria-hidden','true');
      if(i===0){add('span','★ Ernie’s pick',card,'pill'); add('p','Ranked for your shared time, suburbs and saved preferences.',card,'small');}
      add('h3',`${v.name} · ${v.suburb}`,card); add('span','Selected',card,'pill selected-pill');
      add('p',v.venue.address || `${v.suburb} · Exact address unverified`,card,'court-address');
      CourtPricing.render(add('div','',card),v.pricing);
      add('p',`Average ${v.average_distance_km} km · Longest ${v.longest_distance_km} km`,card,'distance-chips');
      if(v.court_availability){
        const a=v.court_availability;
        add('strong',`${a.category} · ${a.court_name}`,card,'court-address');
        add('p',`Offered at ${timeLabel(a.observed_at)}. Live selection-page observation; not held. Checkout price requires verification.`,card,'small');
      }else add('p','Availability not checked',card,'small');
      card.onclick=()=>choose(v);
      card.onmouseenter=()=>highlight(v.venue_id); card.onmouseleave=()=>highlight(selected.venue_id);
      card.onkeydown=e=>{if(e.target!==card)return;let n=i;if(['ArrowRight','ArrowDown'].includes(e.key))n=(i+1)%cards.length;else if(['ArrowLeft','ArrowUp'].includes(e.key))n=(i+cards.length-1)%cards.length;else if(!['Enter',' '].includes(e.key))return;e.preventDefault();choose(result.recommendations[n],true);};
    });
    const dialog=add('dialog','',document.body,'court-compare'); this.dialog=dialog;
    const title=add('h2','Compare courts',dialog); title.id='compare-courts-title';dialog.setAttribute('aria-labelledby',title.id);
    const close=add('button','Close comparison',dialog,'secondary');close.onclick=()=>dialog.close();
    const wrap=add('div','',dialog,'comparison-scroll');const table=add('table','',wrap);
    const tr=add('tr','',add('thead','',table));['Venue','Total','Per player','Average km','Longest km','Availability',''].forEach(s=>add('th',s,tr));
    const body=add('tbody','',table);
    result.recommendations.forEach(v=>{const row=add('tr','',body);[v.name,v.pricing?CourtPricing.money(v.pricing,v.pricing.total_minor):'Price to confirm',v.pricing?.estimated_per_player_minor!=null?CourtPricing.money(v.pricing,v.pricing.estimated_per_player_minor):'—',v.average_distance_km,v.longest_distance_km,v.court_availability?'Offered at observation; recheck':'Not checked'].forEach(s=>add('td',String(s),row));const b=add('button','Choose this',add('td','',row),'secondary');b.onclick=()=>{choose(v);dialog.close();};});
    compare.onclick=()=>dialog.showModal(); dialog.onclose=()=>compare.focus();
    choose(first); Ernie.selectVenue=id=>{const v=result.recommendations.find(v=>v.venue_id===id);if(v)choose(v);};
    accept.onclick=async()=>{if(busy||Ernie.currentRevision!==result.revision||!Ernie.planAllowed)return;busy=true;accept.disabled=true;compare.disabled=true;try{await approve(selected.selection);}catch(e){error.textContent=e.message||'Approval failed. Run a fresh plan.';}finally{busy=false;accept.disabled=false;compare.disabled=false;}};
    this.update();
  },
  calendar(data) {
    const escape=s=>String(s||'').replace(/\\/g,'\\\\').replace(/\r?\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;');
    const stamp=s=>new Date(s).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z');
    const court=data.pricing ? `${data.pricing.category} · ${data.pricing.court_name}` : 'Court to confirm';
    const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Game On//Court Plan//EN','CALSCALE:GREGORIAN','BEGIN:VEVENT',`UID:${crypto.randomUUID()}@game-on.local`,`DTSTAMP:${stamp(new Date())}`,`DTSTART:${stamp(data.starts_at)}`,`DTEND:${stamp(data.ends_at)}`,`SUMMARY:${escape(`Pickleball · ${data.venue_name} (not booked)`)}`,`LOCATION:${escape(data.venue_address||data.venue_suburb)}`,`DESCRIPTION:${escape(`${court}. Planned game only — not booked. Reselect the court and time and verify availability and price with the venue.${data.booking_url?' '+data.booking_url:''}`)}`,'STATUS:TENTATIVE','END:VEVENT','END:VCALENDAR'];
    // RFC 5545 folding counts UTF-8 octets, not JS characters.
    const fold=line=>{let out='',part='',bytes=0;for(const ch of line){const size=new TextEncoder().encode(ch).length;if(bytes+size>75){out+=part+'\r\n';part=' ';bytes=1;}part+=ch;bytes+=size;}return out+part;};
    return lines.map(fold).join('\r\n')+'\r\n';
  }
};
