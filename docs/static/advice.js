const originalTradeRender=render;
function adviceBox(t){
 const a=t.exit_advice;if(!a)return null;
 const box=document.createElement('section');box.className='focus-panel';box.style.margin='16px 0 0';
 const title=document.createElement('h2');title.style.fontSize='17px';title.textContent=a.decision;box.append(title);
 function line(text){const p=document.createElement('p');p.className='focus-note';p.textContent=text;box.append(p)}
 line(a.reason);line('Sell received basket: estimated $'+a.sell_now_net.toFixed(2)+' net. Recent offers requesting these pets: '+a.recent_wanted_offers+'.');
 a.received_demand.forEach(p=>line(p.quantity+' × '+p.name+': '+(p.sales_per_day>0?p.sales_per_day+' reported sales/day':'resale demand unknown or zero')));
 if(a.next_step){const n=a.next_step;line('Possible next step: give '+n.give.join(', ')+' → '+n.receive.join(', ')+'. Additional estimated net: $'+n.extra_net.toFixed(2)+'; basket after this step: $'+n.basket_net_after.toFixed(2)+'.');}
 line(a.note);return box;
}
render=function(data){
 originalTradeRender(data);
 data.results.filter(t=>!acceptedIds.has(t.trade_id)).forEach(t=>{const card=document.getElementById('trade-'+t.trade_id);const box=adviceBox(t);if(card&&box)card.append(box)});
 const cards=document.querySelectorAll('#target-section .trade');
 data.target_trades.filter(t=>!acceptedIds.has(t.trade_id)).forEach((t,i)=>{const box=adviceBox(t);if(cards[i]&&box)cards[i].append(box)});
};
