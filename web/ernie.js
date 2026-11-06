// Local bounded chat; saved game rules remain authoritative.
window.Ernie = {
  contexts: new Map(),
  init() {
    if (this.widget) return;
    const widget = document.createElement('aside');
    widget.className = 'ernie-widget';
    widget.innerHTML = `<button class="ernie-launcher" aria-expanded="false" aria-controls="ernie-chat" aria-label="Chat with Ernie · AI assistant"><img src="/assets/ernie/planner.png" alt="" width="64" height="43"><span>Ernie <small>AI assistant</small></span></button>
      <section id="ernie-chat" role="dialog" aria-labelledby="ernie-title" hidden>
        <header><div><strong id="ernie-title">Ernie · AI assistant</strong><small>Let's get your crew on court</small></div><button class="ernie-close" aria-label="Close Ernie chat">×</button></header>
        <p class="ernie-disclosure">Built-in help, not model-generated chat. I can help with Game On! steps, but can't check live availability or book courts.</p>

        <div class="ernie-conversation"><div class="erne-planning" hidden><button type="button" class="primary erne-plan">Ernie, find the best court for us</button><details class="small erne-mode"><summary>How Ernie plans</summary><p>Ernie compares your submitted game data and directory venues. Each plan identifies whether AI or built-in rules guided it. Availability and prices are not checked.</p></details><div class="erne-plan-result" role="status" aria-live="polite"></div></div><div class="ernie-context"></div><div class="ernie-messages" role="log" aria-label="Conversation with Ernie" aria-live="polite" aria-relevant="additions"></div></div>
        <div class="ernie-questions"><button type="button">What next?</button><button type="button">How do I vote?</button><button type="button">Help with venues</button></div>
        <form class="ernie-form"><label for="ernie-question">Ask about your game</label><div><input id="ernie-question" maxlength="500" autocomplete="off" placeholder="How can I invite friends?"><button type="submit">Send</button></div></form>
      </section>`;
    document.body.append(widget); this.widget = widget;
    const launcher = widget.querySelector('.ernie-launcher'), chat = widget.querySelector('#ernie-chat');
    const toggle = open => {
      chat.hidden = !open; launcher.setAttribute('aria-expanded', String(open));
      (open ? widget.querySelector('.ernie-close') : launcher).focus();
      if (open) this.renderContext();
    };
    launcher.onclick = () => toggle(chat.hidden);
    widget.querySelector('.ernie-close').onclick = () => toggle(false);
    widget.addEventListener('keydown', e => { if(e.key === 'Escape' && !chat.hidden) { e.preventDefault(); toggle(false); } });
    widget.querySelector('img').onerror = e => { e.target.hidden = true; };
    widget.querySelector('.ernie-form').onsubmit = e => {
      e.preventDefault(); const input = widget.querySelector('input');
      const question = input.value.trim(); if (!question) return;
      this.ask(question); input.value = ''; input.focus();
    };
    widget.querySelectorAll('.ernie-questions button').forEach(b => b.onclick = () => this.ask(b.textContent));
    this.renderContext();
  },
  activeContexts() {
    return [...this.contexts.values()].filter(({root}) => root.isConnected && !root.parentElement.closest('[hidden]'));
  },
  renderContext() {
    if (!this.widget) return;
    const text = this.activeContexts().map(c => c.message).join('\n\n') || 'Hi, I’m Ernie. Create or join a group, invite your friends, then choose game times together. What would you like help with?';
    const context = this.widget.querySelector('.ernie-context');
    if (context.textContent !== text) context.textContent = text;
  },
  ask(question) {
    const q = question.toLowerCase(), contexts = this.activeContexts();
    const get = id => contexts.find(c => c.root.id === id)?.message;
    let answer;
    if (/book|reserv|price|cost|pay|availab/.test(q)) answer = 'Check actual availability and prices on the venue website. The organiser can review a venue and use the booking handoff. Copying instructions or opening a website does not confirm a reservation. I cannot book or pay for you.';
    else if (/venue|court|distance|map/.test(q)) answer = get('ernie-venues') || 'After everyone votes and a future time meets the player minimum, review venue suggestions. Compare preferences and approximate distances. Availability and prices still need checking with the venue.';
    else if (/vote|time|respond/.test(q)) answer = [get('ernie-game'), 'Join the game session, select every start time that works for the full session duration, then save your votes. You can update them later. Joining a group alone does not count as voting.'].filter(Boolean).join(' ');
    else if (/invit|friend|share/.test(q)) answer = 'In My groups, the organiser can copy the group invitation for friends. Existing members can use the session link to join a game and vote. Keep recovery and management credentials private.';
    else if (/next|status|player|ready/.test(q)) answer = get('ernie-game') || get('ernie-group') || 'Create or join a group in My groups, invite friends, then create a game with proposed times.';
    else answer = 'I can help with inviting friends, voting, venue suggestions and booking steps. Try one of the suggested questions. This chat uses built-in guidance, so I cannot answer general questions or change your game.';
    const log = this.widget.querySelector('.ernie-messages');
    for (const [speaker, text] of [['You', question], ['Ernie', answer]]) {
      const p = document.createElement('p'); p.className = speaker === 'You' ? 'ernie-user' : 'ernie-answer';
      const label = document.createElement('strong'); label.textContent = speaker;
      p.append(label, document.createTextNode(text)); log.append(p);
    }
    while (log.children.length > 40) log.firstElementChild.remove();
    log.lastElementChild.scrollIntoView({block:'nearest'});
  },
  mountPlanner(entry, output) {
    ErnieGamePlan.init();
    this.planArea = this.widget.querySelector('.erne-planning');
    entry.append(this.planArea);
    output.append(this.planArea.querySelector('.erne-plan-result'));
    this.planOutput = output.querySelector('.erne-plan-result');
    this.planArea.querySelector('.erne-mode').remove();
    const intro = document.createElement('div'); intro.className = 'planning-intro';
    intro.innerHTML = '<img src="/assets/ernie/planner.png" alt="" width="84" height="56"><p><strong>A court that works for your crew.</strong>Ernie can compare convenient options for everyone sharing a time.</p>';
    this.planArea.prepend(intro);
    const button = this.planArea.querySelector('.erne-plan');
    button.textContent = 'Ask Ernie to find our court'; button.id = 'ask-ernie';
    const controls = document.createElement('div'); controls.className = 'planning-controls'; controls.append(button);
    const view = document.createElement('button'); view.type = 'button'; view.className = 'secondary view-plan'; view.textContent = 'View Ernie’s recommendation'; view.hidden = true;
    view.onclick = () => { CourtFlow.go('court'); output.scrollIntoView({block:'start'}); output.focus({preventScroll:true}); };
    const again = document.createElement('button'); again.type = 'button'; again.className = 'text-button run-plan-again'; again.textContent = 'Run again'; again.hidden = true;
    again.onclick = () => button.click();
    controls.append(view, again); this.planArea.append(controls);
  },
  setPlanningReady(ready = this.planReady) {
    this.planReady = ready;
    const valid = Boolean(this.planRevision && this.hasRecommendation);
    this.planArea.querySelector('.erne-plan').hidden = !ready || valid;
    this.planArea.querySelector('.planning-intro').hidden = !ready || valid;
    this.planArea.querySelector('.view-plan').hidden = !valid;
    this.planArea.querySelector('.run-plan-again').hidden = !ready || !valid;
  },
  planning(allowed, run, approve, revision, onResult) {
    const area = this.planArea, output = this.planOutput;
    area.hidden = !allowed; this.planAllowed = allowed;
    if (this.planRevision && (this.planRevision !== revision || !allowed)) {
      this.planRevision = null; this.hasRecommendation = false; this.selectVenue = null;
      output.textContent = 'The game changed. Ask Ernie for a fresh recommendation before approving.';
      onResult?.(null); CourtFlow.bar?.remove(); CourtFlow.dialog?.close(); ErnieGamePlan.invalidate();
    }
    ErnieGamePlan.show();
    this.currentRevision = revision; this.approvePlan = approve;
    const button = area.querySelector('.erne-plan');
    button.onclick = async () => {
      if (button.disabled || !this.planAllowed || !this.planReady) return;
      button.disabled = true; this.planRevision = null; this.hasRecommendation = false; this.selectVenue = null; onResult?.(null);
      this.setPlanningReady(); output.setAttribute('aria-busy', 'true');
      output.textContent = 'Ernie is comparing court options for your group.';
      button.textContent = 'Ernie is working…'; ErnieGamePlan.start();
      try {
        const result = await run();
        if (!this.planAllowed || result.revision !== this.currentRevision) throw new Error('The game changed. Ask Ernie for a fresh recommendation.');
        ErnieGamePlan.finish(result);
        output.replaceChildren(); this.planRevision = result.revision;
        const add = (tag, text, root = output) => { const el = document.createElement(tag); el.textContent = text; root.append(el); return el; };
        // Execution mode, trace, activities and timing remain in the existing API and safe test evidence.
        // Explain the source without exposing internal traces or developer diagnostics.
        add('p', result.llm_used ? 'AI-guided plan, checked against your group data.' : result.execution_mode === 'mock_model' ? 'Test plan using simulated AI responses.' : result.llm_call_attempted ? 'AI was contacted but did not produce a usable decision.' : 'Plan based on built-in rules.').className = 'small';
        if (!result.recommendations.length) {
          add('p', result.execution_status === 'failed' ? 'Ernie could not finish this plan. Your votes are unchanged. Try again shortly.' : 'No suitable court recommendation yet. Check the group’s votes and suburbs, or explore the directory.');
        } else {
          this.hasRecommendation = true;
          CourtFlow.render(result, output, selection => this.approvePlan(selection));
        }
        onResult?.(result);
        if (this.hasRecommendation) document.querySelector('#planning-output').scrollIntoView({block:'start'});
      } catch(error) { ErnieGamePlan.fail(); output.textContent = error.message || 'Planning is unavailable. Your votes are unchanged.'; }
      finally { button.disabled = false; button.textContent = 'Ask Ernie to find our court'; output.setAttribute('aria-busy','false'); this.setPlanningReady(); }
    };
  },
  show(root, persona, message) {
    if (!root) return;
    this.init(); root.replaceChildren();
    // Tips marked .ernie-tip show inline beside the step; other contexts only feed the chat.
    if (root.classList.contains('ernie-tip') && message) {
      root.hidden = false;
      const img = document.createElement('img'); img.src = '/assets/ernie/planner.png'; img.alt = ''; img.width = 96; img.height = 64;
      const bubble = document.createElement('p'); bubble.textContent = message;
      root.append(img, bubble);
    } else root.hidden = true;
    this.contexts.set(root.id, {root, message}); this.renderContext();
  },
  openPlanner() {
    this.init();
    if(this.planArea) { this.planArea.scrollIntoView({behavior:'smooth'}); this.planArea.querySelector('.erne-plan').focus(); return; }
    const chat = this.widget.querySelector('#ernie-chat');
    if (chat.hidden) this.widget.querySelector('.ernie-launcher').click();
    this.widget.querySelector('.erne-plan')?.focus();
  },
  game(root, game, ready) {
    const voted = game.players.filter(p => p.has_voted).length, pending = game.players.length - voted;
    const message = ready ? 'A time works for enough of you. Ask Ernie below the game steps to compare courts for the available players.' : game.players.length < game.min_players ? `${game.players.length} of ${game.min_players} players are in. Share the game link to bring your crew together.` : pending ? `${voted} of ${game.players.length} players have voted. Waiting on ${pending} more. You can explore courts while you wait.` : 'Everyone has voted, but no time works for enough players yet. The organiser can start a new game with other times.';
    this.show(root, 'assistant', message);
  }
};
window.Ernie.init();
