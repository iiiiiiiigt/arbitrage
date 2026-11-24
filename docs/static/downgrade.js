// Extend the research table with a repeatable, arbitrary-pet workflow.
const baselineInput=document.getElementById('rb');
baselineInput.value='';baselineInput.placeholder='Choose any pet and variant in the icon picker above';
panel.querySelector('h2').textContent='Find your next downgrade';
panel.querySelector('p').textContent='Focus any pet above to find smaller pets with better estimated net value per frost. Explore successive downgrades below. Resale demand and trade availability are separate signals.';
async function explorePet(name){
 baselineInput.value=name;document.getElementById('rh').value='';document.getElementById('rs').value='';
 await document.getElementById('run-research').onclick();
}
const originalDrawResearch=drawResearch;
drawResearch=function(){
 originalDrawResearch();const table=document.querySelector('#ranking table');if(!table)return;
 table.rows[0].insertCell().textContent='Continue exploring';
 const q=document.getElementById('rs').value.toLowerCase();
 rows.filter(r=>r.name.toLowerCase().includes(q)).forEach((r,i)=>{
 const b=document.createElement('button');b.type='button';b.className='accept-btn';b.textContent='Downgrade this pet next';b.onclick=()=>explorePet(r.name);table.rows[i+1].insertCell().append(b);
 });
};
document.getElementById('rs').oninput=drawResearch;
window.addEventListener('downgrade-focus',e=>explorePet(e.detail));
fetch('/status').then(r=>r.json()).then(d=>{if(!baselineInput.value&&d.target_pet)explorePet(d.target_pet)}).catch(console.error);
let refreshingResearch=false;
setInterval(async()=>{if(document.hidden||refreshingResearch||!baselineInput.value.trim())return;refreshingResearch=true;try{await document.getElementById('run-research').onclick()}finally{refreshingResearch=false}},10000);
const nextPanel=document.getElementById('next-options');
const exploreReceived=document.createElement('button');exploreReceived.type='button';exploreReceived.className='accept-btn';exploreReceived.textContent='Check received holdings for another trade';
exploreReceived.onclick=()=>{document.getElementById('rh').focus();document.getElementById('rh').placeholder='Enter the received pets here, repeating duplicates, then Compare holdings.'};
nextPanel.before(exploreReceived);
