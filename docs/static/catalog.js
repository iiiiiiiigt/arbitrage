(()=>{'use strict';
const $=id=>document.getElementById(id), el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n};
const money=n=>n===null||n===undefined?'Unknown':'$'+Number(n).toFixed(2), num=n=>n===null||n===undefined?'Unknown':String(n);
let data={rows:[]},endpoint='',live=false,limit=60,busy=false;
const pages=location.pathname.endsWith('.html');
const density=r=>r.frost>0&&r.net!==null?r.net/r.frost:null;
const selected=()=>data.rows.find(r=>r.name===$('baseline').value);
function metric(box,label,value,hero=false){const x=el('div');x.append(el('small','',label),el('strong',hero?'hero-number':'',value));box.append(x)}
function render(){
 const base=selected(),bd=base?density(base):null,q=$('search').value.toLowerCase();
 let rows=data.rows.filter(r=>r.name.toLowerCase().includes(q));
 if($('scope').value==='shortlist')rows=rows.filter(r=>r.shortlist);
 if($('scope').value==='smaller')rows=rows.filter(r=>base&&bd!==null&&r.frost>0&&r.frost<base.frost&&density(r)>bd);
 if($('demand').value==='liquid')rows=rows.filter(r=>r.median_daily!==null&&r.median_daily>=2);
 if($('demand').value==='checked')rows=rows.filter(r=>r.median_daily!==null);
 rows.sort((a,b)=>$('sort').value==='name'?a.name.localeCompare(b.name):$('sort').value==='sales'?(b.median_daily??-1)-(a.median_daily??-1):(density(b)??-1)-(density(a)??-1));
 $('comparison').textContent=rows.length+' exact variants. '+(base?'Starting with '+base.name+': '+money(base.net)+' estimated net, '+num(base.frost)+' frost.':'Select a starting pet to see a whole-item downgrade comparison.');
 $('cards').replaceChildren();
 for(const r of rows.slice(0,limit)){
  const c=el('article','catalog-item'),img=el('img');img.src='https://amvgg.com/items/'+encodeURIComponent(r.base)+'.webp';img.alt='';img.loading='lazy';img.onerror=()=>img.remove();c.append(img,el('h3','',r.name),el('small','',r.type+' · '+r.confidence+' price confidence'));
  const m=el('div','metrics');metric(m,'Estimated net / frost',money(density(r)),true);metric(m,'Saved frost value',num(r.frost));metric(m,'Saved gross → net',money(r.gross)+' → '+money(r.net));metric(m,'Last checked lowest listing',money(r.listing_low));metric(m,'Median sales / day',num(r.median_daily));metric(m,'Observed sales / week',num(r.weekly_total));c.append(m);
  if(base&&bd!==null&&r.frost>0&&r.frost<base.frost&&r.net!==null){const qty=Math.floor((base.frost+1e-10)/r.frost),gain=qty*r.net-base.net;c.append(el('p','',qty+' × this variant ≈ '+money(qty*r.net)+' net ('+(gain>=0?'+':'')+money(gain)+' versus your starting pet). '+(base.frost-qty*r.frost).toFixed(4)+' frost remains unallocated. This is a theoretical exchange.'))}
  c.append(el('p','muted',r.median_daily===null?'Sales are unchecked, stale or unavailable. Check before treating this as a resale exit.':'Sales window: '+r.period_start+' to '+r.period_end+'. Checked '+new Date(r.checked_at*1000).toLocaleString()+'.'));
  const check=el('button','outline',live?'Check price & sales':'Live checker offline');check.disabled=!live||busy;check.onclick=()=>queue([r.key]);const focus=el('button','outline','Use as starting pet');focus.onclick=()=>{$('baseline').value=r.name;$('scope').value='smaller';render()};c.append(check,document.createTextNode(' '),focus);$('cards').append(c);
 }
 if(!rows.length)$('cards').append(el('p','muted','No matching variants. Choose a starting pet or broaden your filters.'));
 $('more').hidden=rows.length<=limit;$('check-shortlist').disabled=!live||busy;
}
async function queue(keys){busy=true;render();try{const r=await fetch(endpoint+'/catalog-check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys})});const d=await r.json();if(!r.ok)throw Error(d.error||'Check failed');$('notice').textContent=d.status}catch(e){$('notice').textContent='Could not queue check: '+e.message}finally{busy=false;render()}}
async function load(){try{let r;if(endpoint||!pages){r=await fetch(endpoint+'/catalog-data',{signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error('Live checker unavailable');data=await r.json();live=true}else{r=await fetch('./checker.json');if(!r.ok)throw Error('Catalog unavailable');data=await r.json();live=false}$('connection').textContent=(live?'Live checker · ':'Saved catalog · ')+data.status+' · Prices saved '+new Date(data.price_saved_at*1000).toLocaleDateString();if(!$('pet-list').children.length){const f=document.createDocumentFragment();data.rows.forEach(r=>{const o=el('option');o.value=r.name;f.append(o)});$('pet-list').append(f)}render()}catch(e){live=false;if(pages&&endpoint){endpoint='';await load()}else{$('connection').textContent=e.message;render()}}}
['search','baseline','scope','demand','sort'].forEach(id=>$(id).addEventListener('input',()=>{limit=60;render()}));$('more').onclick=()=>{limit+=60;render()};$('check-shortlist').onclick=()=>queue(data.rows.filter(r=>r.shortlist).map(r=>r.key).slice(0,12));
(async()=>{if(pages){try{const hash=new URLSearchParams(location.hash.slice(1)).get('feed');const config=await fetch('./live-config.json').then(r=>r.json());const candidate=hash||config.feed_url||localStorage.getItem('live_feed_url');if(candidate){const u=new URL(candidate);if(u.protocol==='https:'&&u.hostname.endsWith('.trycloudflare.com'))endpoint=u.origin}}catch{}}await load();setInterval(()=>{if(live&&!document.hidden)load()},15000)})();
})();
