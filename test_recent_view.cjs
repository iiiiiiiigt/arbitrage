const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/desk.js','utf8').split("const $=id=>document.getElementById(id);")[0];
const nodes={};const ctx={Date,Map,Set,Number,Math,localStorage:{getItem:()=>null},document:{getElementById:id=>nodes[id]??={}},$:id=>nodes[id]??={}};vm.createContext(ctx);vm.runInContext(src,ctx);
const now=Date.now()/1000,trade=(id,age)=>({trade_id:id,observed_at:now-age,qualified:true,give:['A'],receive:['B'],give_usd:10,recv_usd:15,profit_usd:5});
ctx.data={mode:'all',results:[trade('live',1)],near_misses:[],target_trades:[],recent_trades:[trade('live',1),trade('older',300),trade('expired',650)]};
vm.runInContext('tenMinuteView=true;screenPrices(data)',ctx);
assert.equal(ctx.data.results.length,1);assert.equal(ctx.data.near_misses.length,1);assert.equal(ctx.data.near_misses[0].historical,true);assert.equal(ctx.data.alert_trades.length,2);assert.equal(ctx.data.near_misses[0].exit_advice,null);
console.log('10-minute view retains older offers, expires old rows, and includes review offers in the all-trades alert pool.');
