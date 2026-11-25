(function(root){'use strict';
const median=a=>{a=[...a].sort((x,y)=>x-y);const n=a.length;return n?n%2?a[(n-1)/2]:(a[n/2-1]+a[n/2])/2:null};
function calibrate(outcomes,trade,now=Date.now()){
 const valid=outcomes.filter(o=>o.status==='sold'&&Number.isFinite(o.actual_net)&&o.actual_net>=0&&o.trade?.recv_usd>0&&o.completed_at<=now&&now-o.completed_at<90*86400000);
 const similar=valid.filter(o=>!!o.trade.preferred_receive===!!trade.preferred_receive);
 const sample=similar.length>=5?similar:valid;
 if(sample.length<5)return{factor:1,count:sample.length,ready:false,days:null,scope:'Not enough completed sales'};
 return{factor:Math.min(1,median(sample.map(o=>o.actual_net/o.trade.recv_usd))),count:sample.length,ready:true,days:median(sample.filter(o=>Number.isFinite(o.days)&&o.days>=0).map(o=>o.days)),scope:similar.length>=5?'Same preferred-target category':'All completed sales'};
}
function advise(trade,outcomes,now=Date.now()){
 const model=calibrate(outcomes,trade,now),valid=Number.isFinite(trade.recv_usd)&&Number.isFinite(trade.give_usd)&&trade.give_usd>0&&!trade.unknown?.length;
 const gain=valid?trade.recv_usd*model.factor-trade.give_usd:null;
 const market=trade.market_evidence,marketFresh=!!(market?.available&&Number.isFinite(market.summary?.last_updated)&&now/1000-market.summary.last_updated<300);
 const result={model,gain,marketFresh,decision:'Review — insufficient evidence',kind:'review',reason:'Reliable prices and cash-sales evidence are required.'};
 if(trade.historical||(Number.isFinite(trade.observed_at)&&now/1000-trade.observed_at>120))return{...result,reason:'This offer was last observed more than 2 minutes ago. Check that it still exists before considering any trade or follow-up.'};
 if(!valid)return result;
 if(gain<Math.max(1,trade.give_usd*.05))return{...result,decision:'Pass — margin too small',kind:'pass',reason:'Estimated extra value after calibration does not clear $1 and 5%. This is a resale-value comparison, not profit against your purchase cost.'};
 if(model.ready&&model.days>7)return{...result,reason:'Your completed sales in this sample took a median of '+model.days+' days. Review resale speed before committing.'};
 const quoted=marketFresh?market.same_basket:null;
 const pct=100*gain/trade.give_usd;
 if(quoted?.samples>=10&&now/1000-market.summary.observed_since>=300&&pct<quoted.lower_quartile_gain_pct&&quoted.median_gain_pct-pct>=10)return{...result,reason:'Review the price: '+quoted.samples+' observed offers asking for the same give basket quoted a median '+quoted.median_gain_pct.toFixed(1)+'% uplift. This offer is below their lower quartile. These are posted quotes, not completed trades or guaranteed alternatives.'};
 const routeScore=o=>o.extra_net*(1+(marketFresh ? .1*Math.min(1,(o.observed_wanted_offers||0)/20):0));
 const next=(trade.exit_advice?.downgrade_options||[]).filter(o=>o.liquid&&o.extra_net*model.factor>=Math.max(2,trade.recv_usd*.05)).sort((a,b)=>routeScore(b)-routeScore(a))[0];
 if(next&&trade.qualified)return{...result,decision:'Consider taking, then downgrading again',kind:'downgrade',reason:'An observed follow-up fits the received holdings and passes cash-demand checks. Market history gives a small ranking preference to routes ending in pets with more posted requests. Availability is not guaranteed.',next,chainGain:gain+next.extra_net*model.factor};
 const liquid=(trade.exit_advice?.received_demand||[]);if(trade.qualified&&liquid.length&&liquid.every(d=>d.reliable&&d.demand_ok))return{...result,decision:'Consider taking and reselling',kind:'sell',reason:'The estimated margin clears checks and every received variant has reported cash-sales demand.'};
 return result;
}
const api={median,calibrate,advise};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.TradeLearning=api;
})(typeof window!=='undefined'?window:globalThis);
