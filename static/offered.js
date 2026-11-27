(function(root){
const normalize=s=>String(s).trim().toLowerCase().replace(/[_-]+/g,' ').replace(/\s+/g,' ');
function matches(name,pet,variant){const base=normalize(pet),full=normalize(name);if(full!==base&&!full.startsWith(base+' '))return false;const suffix=full.slice(base.length).trim().split(' ').filter(Boolean);if(suffix.some(x=>!['fly','ride','neon','mega'].includes(x)))return false;return variant==='any'||suffix.sort().join(' ')===normalize(variant).split(' ').filter(Boolean).sort().join(' ')}
function filter(rows,pet,variant='any',minimum=1){if(!String(pet).trim())return [];return rows.filter(t=>(t.receive||[]).filter(n=>matches(n,pet,variant)).length>=Math.max(1,Number(minimum)||1))}
const api={matches,filter};if(typeof module!=='undefined')module.exports=api;root.OfferingSearch=api;
})(typeof window==='undefined'?globalThis:window);
