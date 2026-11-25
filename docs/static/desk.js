let state=null,tab='good',stopped=false,researchRows=[],tenMinuteView=false;
const recentLocal=new Map();let recentMode=null;
let lastSig='',lastUpdate=0,lastChange=0,nextAt=0,pollTimer=null,lastResearchSig='';
let lastAnnounceCount=null,pollBusy=false;
const openDetails=new Set();
function fullyPriced(t){return !t.unknown?.length && !/unpriced|low-confidence/i.test(t.reason||'')}
function screenPrices(data){
 data.alert_trades=data.alert_trades||[...(data.results||[]),...(data.near_misses||[])];
 if(recentMode!==data.mode){recentLocal.clear();recentMode=data.mode}
 const now=Date.now()/1000;
 for(const t of data.recent_trades||data.alert_trades){const previous=recentLocal.get(t.trade_id);const observed=t.observed_at??(Number.isFinite(t.seen_age)?now-t.seen_age:previous?.observed_at??now);recentLocal.set(t.trade_id,{...t,observed_at:observed})}
 for(const [id,t] of recentLocal)if(now-t.observed_at>=600)recentLocal.delete(id);
 if(tenMinuteView){
  const rows=[...recentLocal.values()].filter(t=>now-t.observed_at<600).map(t=>{const age=now-t.observed_at;return {...t,seen_age:age,historical:age>120,...(age>120?{qualified:false,exit_advice:null,reason:'Historical offer: last observed more than 2 minutes ago. Verify availability before acting.'}:{})}});
  data.alert_trades=rows;
  data.results=rows.filter(t=>t.qualified);data.near_misses=rows.filter(t=>!t.qualified);data.target_trades=rows.filter(t=>data.target_pet&&t.give.includes(data.target_pet));
 }
 const historyNote=$('history-note');if(historyNote)historyNote.textContent=tenMinuteView?'Showing offers observed in the last 10 minutes. Older offers appear in Needs review; availability is unconfirmed. History builds while the collector runs.':'';
 const excluded=new Set();
 if(data.mode==='preferred'){
  const dollars=Math.max(0,Number(localStorage.getItem('preferred_min_dollars')??10));
  const percent=Math.max(0,Number(localStorage.getItem('preferred_min_percent')??20));
  const qualifies=t=>t.qualified&&t.preferred_receive&&Number.isFinite(t.profit_usd)&&t.give_usd>0&&t.profit_usd>=dollars&&(100*t.profit_usd/t.give_usd)>=percent;
  for(const field of ['results','near_misses','target_trades','expired'])data[field]=(data[field]||[]).filter(qualifies);
  const profits=data.results.map(t=>t.profit_usd);
  data.profit_summary={threshold:dollars,count:profits.length,total:profits.reduce((a,b)=>a+b,0),best:Math.max(0,...profits)};
 }

 for(const name of ['results','near_misses','target_trades']){
  data[name]=data[name].filter(t=>{if(fullyPriced(t))return true;excluded.add(t.trade_id);return false});
 }
 let note=document.getElementById('price-coverage');
 if(!note){note=node('p','muted');note.id='price-coverage';document.querySelector('.feed-head').after(note)}
 note.textContent=excluded.size?excluded.size+' offers excluded because at least one exact item lacks a supported price.':'';
 return data;
}
const $=id=>document.getElementById(id);
function node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el}
const money=n=>n==null?'Unknown':'$'+Number(n).toFixed(2);

// ── Cadence indicator ────────────────────────────────────────────────
// The feed used to be rebuilt every 2s whether or not anything changed,
// which looked like random refreshing. Now we only touch the DOM when the
// visible content actually differs, and we say when the next check is due.
function ago(ts){
 const s=Math.max(0,Math.round((Date.now()-ts)/1000));
 if(s<60)return s+'s';
 const m=Math.floor(s/60);
 if(m<60)return m+'m '+(s%60)+'s';
 return Math.floor(m/60)+'h '+(m%60)+'m';
}
function updateStamp(){
 const el=$('updated');if(!el)return;
 const parts=[];
 if(!lastUpdate)parts.push('Waiting for first update');
 else parts.push('Checked '+(Date.now()-lastUpdate<1500?'just now':ago(lastUpdate)+' ago'));
 if(lastChange)parts.push('last change '+ago(lastChange)+' ago');
 else parts.push('no changes yet');
 if(stopped)parts.push('auto-refresh off');
 else if(document.hidden)parts.push('paused (tab hidden)');
 else{const wait=Math.ceil((nextAt-Date.now())/1000);parts.push(wait>0?'next in '+wait+'s':'checking…')}
 el.textContent=parts.join(' · ');
}

function petSide(names,label,total,demand={},prices={}){const side=node('div');side.append(node('div','side-label',label+' · '+money(total)+' net'));for(const name of names){const row=node('div','pet-item');const img=node('img');img.src='https://amvgg.com/items/'+encodeURIComponent(name.replace(/(?:\s+(Fly|Ride|Neon|Mega))+$/i,''))+'.webp';img.alt='';img.loading='lazy';img.onerror=()=>{img.style.visibility='hidden'};const text=node('div');text.append(node('span','',name));const value=prices[name];const received=label==='YOU RECEIVE'||label==='GOT';text.append(node('span','micro item-net',Number.isFinite(value)?(received?'+':'')+money(value)+' estimated resale after 30% fee':'Net resale unavailable'));const d=demand[name];const fmt=n=>Number(n).toLocaleString(undefined,{maximumFractionDigits:2});const known=Number.isFinite(d?.median_daily)&&Number.isFinite(d?.weekly_total);const sig=(d?.signal)||d;const caption=node('span','micro',known?'Observed sales: '+fmt(d.median_daily)+'/day median · '+fmt(d.weekly_total)+' in 7 days':'Cash sales: unavailable');caption.title=known?'Seven complete UTC days ('+d.period_start+' to '+d.period_end+'). '+(d.coverage||'Exact-variant daily totals.'):(d?.reason||'StarPets history unavailable');text.append(caption);if(sig?.source==='amvgg_rating')text.append(node('span','micro','Trade interest: '+sig.level+' (AMVGG; not cash sales)'));row.append(img,text);side.append(row)}return side}
function ageLabel(sec){
 if(!Number.isFinite(sec)||sec<90)return null;
 const m=Math.floor(sec/60);
 return m<60?m+'m ago':Math.floor(m/60)+'h ago';
}
// amvgg supports a real per-trade URL; elvebredd does not (verified: no route,
// no query param, its UI scrolls via sessionStorage).
function tradeUrl(id){return String(id).startsWith('elvebredd:')?'https://elvebredd.com/trade-center':'https://amvgg.com/trade/'+encodeURIComponent(id)}
function tradeLabel(id){return String(id).startsWith('elvebredd:')?'Open trade center':'Open trade'}
function shortId(id){return String(id).replace(/^elvebredd:/,'')}
// Elvebredd has no per-listing URL, so the next best thing is a pasteable
// summary: the listing id plus both sides of the offer.
function copyTradeText(t){
 const id=shortId(t.trade_id);
 const txt='Adopt Me trade #'+id+': give '+(t.give||[]).join(', ')+
   ' for '+(t.receive||[]).join(', ')+' (elvebredd listing '+id+')';
 if(navigator.clipboard&&navigator.clipboard.writeText){
  return navigator.clipboard.writeText(txt).then(()=>true,()=>false);
 }
 return Promise.resolve(false);
}

function expiredCard(e){
 const elve=String(e.trade_id).startsWith('elvebredd:');
 const card=node('article','deal expired');
 const head=node('div','deal-head');
 head.append(node('span','pill','Expired'),node('span','',elve?'Elvebredd':'AMVGG'),node('span','author','@'+(e.author||'?')));
 if(elve)head.append(node('span','micro','#'+shortId(e.trade_id)));
 card.append(head);
 const dm={};for(const [n,v] of Object.entries(e.demand||{}))dm[n]={median_daily:v.median_daily,signal:{label:v.label}};
 const ex=node('div','exchange');
 ex.append(petSide(e.give||[],'GAVE',e.give_usd,dm,e.item_net),node('div','exchange-arrow','→'),petSide(e.receive||[],'GOT',e.recv_usd,dm,e.item_net));
 card.append(ex);
 const bottom=node('div','deal-bottom');const nums=node('div','numbers');
 const gain=node('div');gain.append(node('span','micro','ESTIMATED NET CHANGE'),
  node('div','gain'+((e.profit_usd||0)<0?' loss':''),((e.profit_usd||0)>0?'+':'')+money(e.profit_usd)));
 nums.append(gain);
 const frost=node('div','frost');frost.append(node('span','micro','FROST VALUE · GAVE → GOT'),
  node('span','',(e.give_frost??'?')+' → '+(e.recv_frost??'?')));nums.append(frost);
 bottom.append(nums);
 const adv=e.exit_advice||{};
 if(adv.decision)bottom.append(node('p','muted',adv.decision));
 if(e.reason)bottom.append(node('p','muted',String(e.reason)));
 bottom.append(node('p','muted','Left the live window '+ago(Date.now()-(e.expired_at||0)*1000)+' ago. The offer may already be gone.'));
 const actions=node('div','actions');
 if(elve){
  const copy=node('button','outline','Copy details');
  copy.onclick=async()=>{const ok=await copyTradeText(e);copy.textContent=ok?'Copied':'Copy failed';setTimeout(()=>{copy.textContent='Copy details'},2000)};
  actions.append(copy);
 }
 const link=node('a','primary',tradeLabel(e.trade_id));link.href=tradeUrl(e.trade_id);
 if(elve)link.title='Elvebredd has no per-listing URL; opens the trade center.';
 link.target='_blank';link.rel='noopener noreferrer';actions.append(link);
 bottom.append(actions);card.append(bottom);return card;
}
function card(t){
 const incomplete=Boolean(t.unknown?.length)||/unpriced|low-confidence/i.test(t.reason||'');
 const elve=String(t.trade_id).startsWith('elvebredd:');const card=node('article','deal');const head=node('div','deal-head');
 head.append(node('span','pill'+(t.reason?' review':''),incomplete?'Price incomplete':t.reason?'Review carefully':'Potential opportunity'),node('span','',elve?'Elvebredd':'AMVGG'),node('span','author','@'+t.author));
 if(elve)head.append(node('span','micro','#'+shortId(t.trade_id)));card.append(head);
 // an expired window entry can linger; say so rather than implying it is live
 const stale=ageLabel(t.seen_age);
 if(stale)head.append(node('span','pill','last seen '+stale));
 const exchange=node('div','exchange');exchange.append(petSide(t.give,'YOU GIVE',incomplete?null:t.give_usd,t.item_demand,t.item_net),node('div','exchange-arrow','→'),petSide(t.receive,'YOU RECEIVE',incomplete?null:t.recv_usd,t.item_demand,t.item_net));card.append(exchange);
 const bottom=node('div','deal-bottom');const nums=node('div','numbers');const gain=node('div');gain.append(node('span','micro','ESTIMATED NET CHANGE'),node('div','gain'+(incomplete?' unknown':t.profit_usd<0?' loss':''),incomplete?'Profit unknown':(t.profit_usd>0?'+':'')+money(t.profit_usd)));nums.append(gain);
 const frost=node('div','frost');frost.append(node('span','micro','FROST VALUE · GIVE → RECEIVE'),node('span','',t.give_frost+' → '+t.recv_frost+'  ('+((t.recv_frost-t.give_frost)>=0?'+':'')+(t.recv_frost-t.give_frost).toFixed(4)+')'));nums.append(frost);bottom.append(nums);
 if(t.reason){bottom.append(node('p','muted',incomplete?'Some items lack reliable prices. The total profit cannot be determined.':t.reason.replace('no USD profit','Estimated loss')))}
 const a=t.exit_advice;if(a){const advice=node('div','recommendation');advice.append(node('strong','','Market evidence · '+a.decision),node('p','',a.reason));bottom.append(advice);const details=node('details','detail-toggle');details.append(node('summary','','Why this recommendation?'));for(const d of a.received_demand)details.append(node('p','',d.quantity+' × '+d.name+': '+(Number.isFinite(d.median_daily)?d.median_daily+' sales/day (7-day median)':(d.demand_label||'verified sales history unavailable'))));if(a.next_step)details.append(node('p','','Possible next trade: '+a.next_step.give.join(', ')+' → '+a.next_step.receive.join(', ')+' · extra '+money(a.next_step.extra_net)));details.append(node('p','',a.note));
  // keep this panel open across re-renders instead of collapsing it
  details.dataset.tid=t.trade_id;
  if(openDetails.has(t.trade_id))details.open=true;
  details.addEventListener('toggle',()=>{if(details.open)openDetails.add(t.trade_id);else openDetails.delete(t.trade_id)});
  bottom.append(details)}
 const followups=t.exit_advice?.downgrade_options||[];
 if(followups.length){const panel=node('div','recommendation');panel.append(node('strong','','Next downgrades found'),node('p','micro','Alternative offers using only pets you would receive. Extra gains are estimates, not additive.'));
  for(const f of followups){const option=node('div');if(f.preferred)option.append(node('strong','','Preferred downgrade target'));option.append(node('p','',f.give.join(', ')+' → '+f.receive.join(', ')),node('p','', '+'+money(f.extra_net)+' extra ('+f.extra_pct+'%) · holdings after trade: '+money(f.basket_net_after)),node('p','micro',f.liquid?'StarPets sales check passed for resulting holdings':'Cash-sales check incomplete or below threshold'));
   const link=node('a','outline',tradeLabel(f.trade_id));link.href=tradeUrl(f.trade_id);link.target='_blank';link.rel='noopener noreferrer';option.append(link);panel.append(option)}bottom.append(panel)}
 if(window.tradeCoach)bottom.append(window.tradeCoach.panel(t));
 const actions=node('div','actions');const elveTrade=elve;
 if(elveTrade){
  const copy=node('button','outline','Copy details');
  copy.onclick=async()=>{const ok=await copyTradeText(t);copy.textContent=ok?'Copied':'Copy failed';setTimeout(()=>{copy.textContent='Copy details'},2000)};
  actions.append(copy);
 }
 const view=node('a','primary',tradeLabel(t.trade_id));view.href=tradeUrl(t.trade_id);
 if(elveTrade)view.title='Elvebredd has no per-listing URL; opens the trade center where you can search for it.';
 view.target='_blank';view.rel='noopener noreferrer';actions.append(view);
 const accept=node('button','outline',elveTrade?'Open trade center':'Accept trade');accept.onclick=async()=>{if(elveTrade){window.open(view.href,'_blank','noopener');return}if(!confirm('Accept on AMVGG?\nGive: '+t.give.join(', ')+'\nReceive: '+t.receive.join(', ')+(t.reason?'\nReview: '+t.reason:'')))return;accept.disabled=true;accept.textContent='Accepting…';try{const r=await fetch('/accept',{method:'POST',body:new URLSearchParams({trade_id:t.trade_id})});const d=await r.json();if(!r.ok||!d.success)throw Error();accept.textContent='Accepted — verify completion'}catch{accept.textContent='Unconfirmed — check source';accept.disabled=false}};actions.append(accept);bottom.append(actions);card.append(bottom);return card;
}

const BEST_MIN=10;
function bestFloor(){return Number((state.profit_summary||{}).threshold)||BEST_MIN}
function visibleList(){
 if(tab==='expired')return state.expired||[];
 if(tab==='best')return (state.results||[]).filter(t=>(t.profit_usd||0)>=bestFloor())
   .sort((a,b)=>(b.profit_usd||0)-(a.profit_usd||0));
 return tab==='good'?state.results:tab==='review'?state.near_misses:state.target_trades;
}
function sigOf(list){
 return [tab,state.trade_count,state.running,state.rate_limited,state.target_pet,
  state.results.length,state.near_misses.length,state.target_trades.length,
  (state.expired||[]).length,(state.profit_summary||{}).total||0,
  list.map(t=>t.trade_id+'~'+(t.profit_usd||0)+'~'+(t.stars||0)+'~'+(t.reason||'')).join(';')
 ].join('|');
}
function render(force){
 if(!state)return;
 // "Offers scanned" is cumulative and must never fall; the window size is a
 // separate figure because the working set legitimately expires.
 const scanned = state.total_scanned ?? state.trade_count;
 $('scanned').textContent = scanned;
 $('scanned').title = scanned + ' offers ingested this scan; ' +
   (state.in_window ?? state.trade_count) + ' currently live in the window';
 if($('live-window')) $('live-window').textContent = state.in_window ?? state.trade_count;
 const ps=state.profit_summary||{};
 if($('total-profit')){
  const t=Number(ps.total||0);
  $('total-profit').textContent='$'+t.toFixed(2);
  $('total-profit').title=(ps.count||0)+' trade(s) worth at least $'+(ps.threshold??BEST_MIN)+
    ' each (best $'+Number(ps.best||0).toFixed(2)+'). Trades below the floor are not counted.';
 }
 $('good-count').textContent=state.results.length;$('review-count').textContent=state.near_misses.length;
 $('connection').textContent=state.rate_limited?'Rate limited · slowing down':state.running?(state.connection_label || 'Snapshot loaded'):'Scanning stopped';
 if(state.mode!=='inventory')$('connection').textContent+=' · all pets';
 if($('margin-controls'))$('margin-controls').hidden=state.mode!=='preferred';
 if($('mode-toggle'))$('mode-toggle').textContent=state.mode==='preferred'?'Scanning: high-margin downgrade pets':state.mode==='all'?'Scanning: all pets':'Scanning: my pets';
 const list=visibleList();
 const sig=sigOf(list);
 if(!force&&sig===lastSig){updateStamp();return}   // nothing changed: leave the DOM alone
 lastSig=sig;
 lastChange=Date.now();
 // announce only genuine growth, instead of re-announcing the whole feed
 const ann=$('feed-announce');
 if(ann){
  const n=list.length;
  if(lastAnnounceCount!==null&&n>lastAnnounceCount){
   const added=n-lastAnnounceCount;
   ann.textContent=added+' new offer'+(added===1?'':'s')+' in '+tab;
  }
  lastAnnounceCount=n;
 }
 $('feed').replaceChildren();
 if(tab==='expired'){
  const es=state.expired_summary||{};
  $('feed').append(node('p','muted','Expired: '+(es.count||0)+' trade(s) at $'+
   (es.threshold??BEST_MIN)+'+ worth $'+Number(es.total||0).toFixed(2)+' total'+
   (es.best?' (best $'+Number(es.best).toFixed(2)+')':'')+'.'));
 }
 if(!list.length){const empty=node('div','empty');empty.append(node('h2','',tab==='focus'&&!state.target_pet?'Choose a focus pet above':tab==='expired'?'Nothing has expired yet':tab==='best'?'No trades at $'+bestFloor()+'+ yet':'No matching offers yet'),node('p','',tab==='expired'?'Trades that leave the live window and were worth a look land here, with their links intact.':tab==='best'?'Only offers worth at least $'+bestFloor()+' each appear here, sorted by profit.':'Try another tab or keep scanning.'));$('feed').append(empty)}else list.forEach(t=>$('feed').append(tab==='expired'?expiredCard(t):card(t)));
 updateStamp();
}

document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));render(true)});
$('pause').onclick=async()=>{try{const r=await fetch('/stop',{method:'POST'});if(!r.ok)throw Error();stopped=true;clearTimeout(pollTimer);$('pause').disabled=true;$('pause').textContent='Stopped';if(state){state.running=false;render(true)}updateStamp()}catch{$('connection').textContent='Could not stop. Retry.'}};
if($('refresh'))$('refresh').onclick=()=>{pollNow()};
if($('scan-ten'))$('scan-ten').onclick=()=>{tenMinuteView=!tenMinuteView;$('scan-ten').textContent=tenMinuteView?'Back to live trades':'Scan last 10 minutes';$('scan-ten').setAttribute('aria-pressed',String(tenMinuteView));lastSig='';pollNow()};
if($('mode-toggle'))$('mode-toggle').onclick=async()=>{
 const modes=['inventory','all','preferred'];const next=modes[(modes.indexOf(state?.mode)+1)%3];
 try{
  const r=await fetch('/set_mode',{method:'POST',body:new URLSearchParams({mode:next})});
  if(!r.ok)throw Error();
  const d=await r.json();
  if(state){state.mode=d.mode;lastSig='';pollNow()}
 }catch{$('connection').textContent='Could not switch mode'}
};

function schedule(ms){clearTimeout(pollTimer);nextAt=Date.now()+ms;pollTimer=setTimeout(poll,ms);updateStamp()}
async function poll(){
 if(stopped||pollBusy)return;
 if(document.hidden&&!window.dealAlerts?.enabled()){schedule(2000);return}      // don't poll a tab nobody is looking at
 pollBusy=true;
 try{const r=await fetch('/status');if(!r.ok)throw Error();state=screenPrices(await r.json());lastUpdate=Date.now();window.tradeCoach?.observe(state);render();window.dealAlerts?.consider(state)}
 catch{$('connection').textContent='Connection lost · retrying'}
 finally{pollBusy=false}
 if(!stopped)schedule(2000);else updateStamp();
}
function pollNow(){clearTimeout(pollTimer);poll()}
window.addEventListener('live-trades-updated',pollNow);
window.addEventListener('learning-updated',()=>render(true));
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!stopped)pollNow();else updateStamp()});
setInterval(updateStamp,1000);
for(const [id,key,def] of [['margin-dollars','preferred_min_dollars',10],['margin-percent','preferred_min_percent',20]]){
 const input=$(id);if(input){input.value=localStorage.getItem(key)??def;input.onchange=()=>{const value=Number(input.value);if(!Number.isFinite(value)||value<0){input.value=def;return}localStorage.setItem(key,String(value));lastSig='';pollNow()}}
}
poll();

function researchTable(){const table=node('table');const header=node('tr');['Pet variant','Net resale','Potential uplift','Reported sales/day',''].forEach(s=>header.append(node('th','',s)));table.append(header);const q=$('research-filter').value.toLowerCase();researchRows.filter(r=>r.name.toLowerCase().includes(q)).forEach(r=>{const row=node('tr');[r.name,money(r.net),r.uplift_pct+'%',r.sales_per_day||'Unknown'].forEach(v=>row.append(node('td','',String(v))));const cell=node('td');const b=node('button','outline','Explore next');b.onclick=()=>{$('research-base').value=r.name;research()};cell.append(b);row.append(cell);table.append(row)});$('research-list').replaceChildren(table)}
async function research(){
 const base=$('research-base').value.trim();
 if(!base){$('research-note').textContent='Select a focus pet or enter an exact variant first.';return}
 $('research-go').disabled=true;
 try{
  const r=await fetch('/research?'+new URLSearchParams({baseline:base}));
  if(!r.ok)throw Error();
  const d=await r.json();
  researchRows=d.candidates;
  $('research-note').textContent=researchRows.length+' smaller candidates. These are theoretical comparisons; an available trade is required.';
  const sig=researchRows.map(c=>c.name+'~'+c.net).join(';');
  if(sig!==lastResearchSig){lastResearchSig=sig;researchTable()}   // avoid pointless table rebuilds
 }catch{$('research-note').textContent='Research unavailable. Please retry.'}
 finally{$('research-go').disabled=false}
}
$('research-go').onclick=research;$('research-filter').oninput=researchTable;window.addEventListener('downgrade-focus',e=>{$('research-base').value=e.detail;if(e.detail)research()});
