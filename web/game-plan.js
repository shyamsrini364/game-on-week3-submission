// Public, allowlisted activity only. Never render raw agent traces or model prose.
window.ErnieGamePlan = {
  init() {
    const root=document.createElement('section');root.id='ernie-game-plan';root.className='game-plan';root.hidden=true;
    root.innerHTML='<div class="game-plan-heading"><img src="/assets/ernie/planner.png" alt="" width="72" height="48"><h2>Ernie’s Game Plan</h2></div><p class="game-plan-state" role="status" aria-live="polite"></p><details><summary>See what Ernie did</summary><p class="small">Activity from the completed run. These are tool actions and observed outcomes, not private model reasoning.</p><ol class="game-plan-events"></ol></details>';
    document.querySelector('#planning-entry').after(root);this.root=root;
  },
  show() { if(this.root)this.root.hidden= !(CourtFlow.step==='court' && Ernie.planAllowed && this.active); },
  start() {
    this.active=true;this.root.dataset.state='running';this.root.removeAttribute('data-run-id');
    this.root.querySelector('.game-plan-state').textContent='Ernie is planning with your submitted group data. The activity record will appear when the run finishes.';
    this.root.querySelector('details').hidden=true;this.root.querySelector('ol').replaceChildren();this.show();
  },
  finish(result) {
    this.active=true;this.root.dataset.state=result.planning_state||'failed';this.root.dataset.runId=result.run_id||'';
    const events=result.activity_timeline||[], list=this.root.querySelector('ol');list.replaceChildren();
    const provenance={deterministic:'Built-in rules',model:'Model-selected',policy:'Safety policy',fixture:'Simulated test'};
    for(const event of events){
      if(event.run_id!==result.run_id)continue;
      // The completed-run view shows outcomes, not duplicate historical starts.
      // Started events remain in the contract for a future authenticated stream.
      if(event.status==='started')continue;
      const item=document.createElement('li');item.dataset.stage=event.stage;item.dataset.status=event.status;
      const title=document.createElement('strong');title.textContent=event.title;
      const detail=document.createElement('p');detail.textContent=event.detail;
      const source=document.createElement('small');source.textContent=`${event.status==='started'?'Started':event.status==='failed'?'Could not complete':'Completed'} · ${provenance[event.provenance]||'Unknown source'}`;
      item.append(title,detail,source);list.append(item);
    }
    const final=events.at(-1);
    this.root.querySelector('.game-plan-state').textContent=final?`${final.title}. ${final.detail}`:'No activity record was returned. Review the result before continuing.';
    const details=this.root.querySelector('details');details.hidden=false;details.open=result.planning_state!=='approval_required';this.show();
  },
  fail() {
    this.active=true;this.root.dataset.state='failed';
    this.root.querySelector('.game-plan-state').textContent='The planning request did not finish successfully. Your votes are unchanged. Try again shortly.';
    this.show();
  },
  invalidate() {
    if(!this.active)return;
    this.root.dataset.state='outdated';
    this.root.querySelector('.game-plan-state').textContent='The game changed. This activity belongs to the previous plan. Run Ernie again before approving.';
    this.show();
  },
  clear() {this.active=false;if(this.root){this.root.hidden=true;this.root.querySelector('ol').replaceChildren();}}
};
