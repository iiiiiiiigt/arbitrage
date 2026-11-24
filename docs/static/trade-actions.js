const renderWithAdvice=render;
render=function(data){
 renderWithAdvice(data);
 for(const [section,trades] of [['results-section',data.results],['target-section',data.target_trades],['near-miss-section',data.near_misses]]){
  const cards=document.getElementById(section).querySelectorAll(':scope > .trade');
  trades.filter(t=>!acceptedIds.has(t.trade_id)).forEach((t,i)=>{
   const card=cards[i];if(!card)return;
   const metrics=document.createElement('p');metrics.className='focus-note';
   const delta=t.recv_frost-t.give_frost;
   metrics.textContent='Frost comparison: give '+t.give_frost+' → receive '+t.recv_frost+' • '+(delta>=0?'+':'')+delta.toFixed(4)+' frost • '+(t.give_frost>0?(t.recv_frost/t.give_frost).toFixed(3)+'×':'ratio unavailable');card.append(metrics);
   // DOM handlers avoid embedding server-provided names in inline JavaScript.
   card.querySelectorAll('.accept-btn').forEach(b=>b.remove());
   const external=String(t.trade_id).startsWith('elvebredd:');
   const view=document.createElement('a');view.className='accept-btn';view.target='_blank';view.rel='noopener noreferrer';
   view.href=external?'https://elvebredd.com/trade-center':'https://amvgg.com/trade/'+encodeURIComponent(t.trade_id);
   view.textContent=external?'Open trade center':'View trade';
   const accept=document.createElement('button');accept.type='button';accept.className='accept-btn';accept.style.marginRight='10px';
   accept.textContent=external?'Accept on Elvebredd':'Accept trade';
   accept.onclick=async()=>{
    if(external){window.open(view.href,'_blank','noopener');return}
    if(!confirm('Accept this AMVGG trade?\nGive: '+t.give.join(', ')+'\nReceive: '+t.receive.join(', ')+(t.reason?'\nScanner warning: '+t.reason:'')))return;
    accept.disabled=true;accept.textContent='Accepting…';
    try{const r=await fetch('/accept',{method:'POST',body:new URLSearchParams({trade_id:t.trade_id})});const d=await r.json();if(!r.ok||!d.success)throw Error();
     accept.textContent='Accepted — verify completion';
     // Acceptance is not evidence the in-game exchange has completed.
    }catch{accept.disabled=false;accept.textContent='Could not confirm — view trade before retrying'}
   };card.append(accept,view);
  });
 }
};
