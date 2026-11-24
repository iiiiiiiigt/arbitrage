// Same UI, browser-local preferences and static public market snapshots.
const nativeFetch=window.fetch.bind(window);
let snapshot=null,active=true;
let liveBase=localStorage.getItem('live_feed_url')||'', liveState='Snapshot mode',liveBusy=false;
const linkFeed=new URLSearchParams(location.hash.slice(1)).get('feed');
function validFeed(value){try{let u=new URL(value);const embedded=new URLSearchParams(u.hash.slice(1)).get('feed');if(embedded)u=new URL(embedded);return u.protocol==='https:'&&!u.username&&!u.password&&!u.search&&!u.hash?u.origin:''}catch{return ''}}
if(linkFeed&&validFeed(linkFeed)){liveBase=validFeed(linkFeed);localStorage.setItem('live_feed_url',liveBase);localStorage.setItem('feed_mode','live');history.replaceState(null,'',location.pathname+location.search)}
if(liveBase)liveState='Connecting to live feed…';
async function refreshLive(){
 if(!liveBase||liveBusy||document.hidden)return;liveBusy=true;
 try{const r=await nativeFetch(liveBase+'/feed',{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error();const d=await r.json();if(!Array.isArray(d.trades)||!Number.isFinite(d.generated_at)||!d.sources)throw Error();
  if(Date.now()/1000-d.generated_at>30)throw Error();
  snapshot={...snapshot,...d};liveState='Live · '+(d.sources.collector||'Connected');
 }catch{liveState='Live feed offline · showing last received data'}finally{liveBusy=false}
}
const key=s=>s.toLowerCase().split(/[\s_-]+/).sort().join('_');
const inventoryNames=()=>JSON.parse(localStorage.getItem('adoptme_inventory')||'[]');
function affordable(give,have){const counts=new Map();have.forEach(n=>counts.set(key(n),(counts.get(key(n))||0)+1));return give.every(n=>{const k=key(n),v=counts.get(k)||0;counts.set(k,v-1);return v>0})}
async function loadSnapshot(){const r=await nativeFetch('./snapshot.json',{cache:'no-cache'});if(!r.ok)throw Error('Snapshot unavailable');snapshot=await r.json();return snapshot}
const ready=(async()=>{
 if(!liveBase&&localStorage.getItem('feed_mode')!=='snapshot'){
  try{const r=await nativeFetch('./live-config.json',{cache:'no-cache'});if(r.ok){const c=await r.json();const u=validFeed(c.feed_url);if(u){liveBase=u;liveState='Connecting to live feed…';localStorage.setItem('live_feed_url',u)}}}catch{}
 }
 return loadSnapshot();
})();
ready.then(refreshLive).catch(console.error);
window.fetch=async function(input,options={}){
 const url=new URL(String(input),location.href),path=url.pathname;
 if(!['/pet-options','/status','/research','/set_target','/stop','/update_inventory','/accept'].includes(path))return nativeFetch(input,options);
 const s=await ready;const data=snapshot||s;const form=new URLSearchParams(options.body||'');let result={ok:true};
 if(path==='/pet-options')result=data.pets;
 if(path==='/set_target')localStorage.setItem('pages_target',form.get('target_pet')||'');
 if(path==='/stop')active=false;
 if(path==='/update_inventory')localStorage.setItem('adoptme_inventory',form.get('inventory')||'[]');
 if(path==='/accept')result={success:false,error:'Open the source website to accept'};
 if(path==='/status'){
  const inv=inventoryNames(),target=localStorage.getItem('pages_target')||'',owned=data.trades.filter(t=>affordable(t.give,inv));
  result={running:active,connection_label:liveBase?liveState:'Snapshot loaded',inventory:inv,target_pet:target,total_fetches:1,total_new:data.trades.length,last_new:0,last_batch:data.trades.length,trade_count:data.trades.length,
   results:owned.filter(t=>t.qualified),near_misses:owned.filter(t=>!t.qualified),target_trades:data.trades.filter(t=>t.qualified&&t.give.some(p=>key(p)===key(target))),stats:{},elapsed:Math.max(0,Date.now()/1000-data.generated_at),rate_limited:false};
 }
 if(path==='/research'){
  const base=url.searchParams.get('baseline')||'',ref=data.catalog.find(p=>key(p.name)===key(base)),hold=url.searchParams.getAll('holding');const have=hold.length?hold:[base];
  const infos=have.map(n=>data.catalog.find(p=>key(p.name)===key(n)));const density=ref?ref.net/ref.frost:null;
  const rows=ref?data.catalog.filter(p=>p.frost<ref.frost&&p.net/p.frost>density).map(p=>({...p,net_per_frost:Math.round(p.net/p.frost*100)/100,uplift_pct:Math.round((p.net/p.frost/density-1)*1000)/10})).sort((a,b)=>b.net_per_frost-a.net_per_frost):[];
  const next=data.trades.filter(t=>t.qualified&&affordable(t.give,have));
  result={baseline:base,baseline_priced:!!ref,candidates:rows,sell_now_net:infos.every(Boolean)?Math.round(infos.reduce((sum,p)=>sum+p.net,0)*100)/100:null,next_trades:next.slice(0,10),recommendation:next.length?'Snapshot contains possible next trades; verify availability.':'No qualifying next trade in this snapshot.',source_status:data.sources.Elvebredd,note:'Saved catalog, 30% commission assumption. Snapshot offers may have expired. Resale demand does not guarantee trades or sales.'};
 }
 return new Response(JSON.stringify(result),{status:200,headers:{'Content-Type':'application/json'}});
};
document.addEventListener('DOMContentLoaded',()=>{
 const settings=document.createElement('details');settings.className='toolbox';
 const summary=document.createElement('summary');summary.textContent='Live connection';settings.append(summary);
 const help=document.createElement('p');help.className='muted';help.textContent='Run the read-only collector on your PC and paste its HTTPS tunnel address. Inventory and focus stay independent in this browser. Closing the collector stops live updates.';settings.append(help);
 const input=document.createElement('input');input.type='url';input.placeholder='Paste a live connection link';input.value=liveBase;input.setAttribute('aria-label','Live feed HTTPS address');settings.append(input);
 ready.then(()=>{if(!input.value)input.value=liveBase}).catch(console.error);
 const connect=document.createElement('button');connect.className='primary';connect.textContent='Connect live';connect.onclick=async()=>{await ready;const u=validFeed(input.value.trim()||liveBase);if(!u){help.textContent='Paste the live dashboard link shared with you, then tap Connect live.';return}liveBase=u;input.value=u;localStorage.setItem('live_feed_url',u);localStorage.setItem('feed_mode','live');await refreshLive();updateStatus()};settings.append(connect);
 const disconnect=document.createElement('button');disconnect.className='outline';disconnect.textContent='Use snapshots';disconnect.onclick=async()=>{await ready;liveBase='';localStorage.removeItem('live_feed_url');localStorage.setItem('feed_mode','snapshot');await loadSnapshot();updateStatus()};settings.append(disconnect);
 document.getElementById('pages-status').after(settings);
 function navigationUrl(path){return path+(liveBase?'#feed='+encodeURIComponent(liveBase):'')}
 function syncNavigation(){
  document.querySelectorAll('a[href]').forEach(a=>{const u=new URL(a.href,location.href);if(u.origin===location.origin&&u.pathname.startsWith('/arbitrage/')&&/\/(index|dashboard)\.html$/.test(u.pathname)){u.hash=liveBase?'feed='+encodeURIComponent(liveBase):'';a.href=u.href}});
 }
 syncNavigation();
 const form=document.getElementById('search-form');if(form)form.addEventListener('submit',e=>{e.preventDefault();location.href=navigationUrl('./dashboard.html')});
 // Static hosting never accepts authenticated trades or collects cookies.
 document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||!/^Accept/.test(b.textContent.trim()))return;const card=b.closest('.trade, .deal');const link=card?.querySelector('a[target="_blank"]');if(link){e.preventDefault();e.stopImmediatePropagation();window.open(link.href,'_blank','noopener')}},true);
 ready.then(()=>updateStatus()).catch(()=>{document.getElementById('pages-status').textContent='Snapshot failed to load. Reload to retry.'});
 function updateStatus(){syncNavigation();const el=document.getElementById('pages-status');el.textContent=(liveBase?liveState:'Snapshot mode · scheduled every 5 min')+' · data updated '+new Date(snapshot.generated_at*1000).toLocaleTimeString();el.title='Live mode checks your collector every 2 seconds. Marketplace limits and network latency still apply.'}
 setInterval(()=>{if(liveBase)refreshLive().then(updateStatus)},2000);
 setInterval(()=>{if(!liveBase&&!document.hidden)loadSnapshot().then(updateStatus).catch(console.error)},10000);
});
