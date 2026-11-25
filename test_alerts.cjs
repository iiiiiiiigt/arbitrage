const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=readFileSync('static/alerts.js','utf8');
async function run(mode,trade){
 const elements={},store=new Map(),sent=[];
 store.set('trade_desk_alerts_v1',JSON.stringify({url:'https://discord.com/api/webhooks/123/test_token',enabled:true,percent:20,mode}));
 const context={document:{getElementById:id=>elements[id]??=( {})},localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},URL,Date,Number,Map,Object,String,JSON,Math,AbortSignal,navigator:{},window:{addEventListener(){},tradeCoach:{recommend:()=>({kind:'review',model:{ready:true},decision:'Review demand',reason:'Sales history is missing'})}},fetch:async(url,opt)=>{sent.push(JSON.parse(opt.body));return{ok:true,status:200,headers:{get:()=>null}}}};
 vm.runInNewContext(source,context);
 const data={running:true,results:[],near_misses:[],alert_trades:[trade,trade],connection_label:'Live · connected'};
 context.window.dealAlerts.consider(data);await new Promise(r=>setImmediate(r));
 context.window.dealAlerts.consider(data);await new Promise(r=>setImmediate(r));
 return {sent,elements};
}
(async()=>{
 const t={trade_id:'123',give:['Parrot'],receive:['Cow'],give_usd:40,recv_usd:30,qualified:false,preferred_receive:false};
 let r=await run('all',t);assert.equal(r.sent.length,1);assert.match(JSON.stringify(r.sent),/Sales history is missing/);assert.match(r.sent[0].embeds[0].title,/-\$10.00/);assert.equal(r.elements['alert-percent'].disabled,true);
 r=await run('all',{...t,historical:true});assert.equal(r.sent.length,1);assert.match(r.sent[0].content,/Historical offer/);
 r=await run('qualified',t);assert.equal(r.sent.length,0);
 r=await run('all',{...t,unknown:['Cow']});assert.equal(r.sent.length,1);assert.match(r.sent[0].embeds[0].title,/Unknown/);assert.doesNotMatch(JSON.stringify(r.sent),/NaN/);
 console.log('All-trade loss, unknown-price, default filtering and duplicate checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
