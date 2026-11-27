const assert=require('node:assert/strict'),{filter}=require('./static/offered.js');
const rows=[{trade_id:'one',give:['Parrot'],receive:['Fallow Deer Fly Ride'],qualified:false,profit_usd:-10},{trade_id:'two',give:['Unknown Pet'],receive:['Fallow Deer','Fallow Deer'],unknown:['Unknown Pet']},{trade_id:'other',give:['Fallow Deer'],receive:['Reindeer']}];
assert.equal(filter(rows,'Fallow Deer').length,2);
assert.equal(filter(rows,'Fallow Deer','any',2)[0].trade_id,'two');
assert.equal(filter(rows,'Fallow Deer','Ride Fly')[0].trade_id,'one');
assert.equal(filter(rows,'Fallow Deer','')[0].trade_id,'two');
assert.equal(filter(rows,'Deer').length,0);
assert.equal(filter(rows,'').length,0);
console.log('Offering search: receive side, exact names, variants, repeated copies, losses and unknown prices passed.');
