const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  setAttribute(key, value) { this.attrs[key] = value; }
  querySelector(selector) {
    return this.find(el => selector === '[role=status]' && el.attrs.role === 'status');
  }
  find(predicate) { if (predicate(this)) return this; for (const child of this.children) { const match = child.find?.(predicate); if (match) return match; } }
}
const graph = new Element('div'), listeners = {}, requests = [];
let intervalFn = null;
let revision = 7, overrides = {color:'#112233'}, rejectStale = false;
const target = {kind:'geometry', id:'mesh'};
const context = {
  window:{addEventListener:(name, fn)=>listeners[name]=fn},
  document:{createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),getElementById:()=>graph},
  setInterval:(fn)=>{intervalFn=fn; return 1;},setTimeout:()=>{},clearTimeout:()=>{},
  fetch:async (path, options)=>{
    const body = JSON.parse(options.body);
    requests.push({path,body});
    if (path.endsWith('appearance')) return {ok:true,json:async()=>({items:[{target,effective:{color:'#112233'},overrides:{...overrides}}],supported_fields:['color'],target_revision:revision})};
    if (rejectStale && body.target_revision !== revision) return {ok:false,json:async()=>({error:'stale target revision'})};
    overrides = body.reset ? {} : {...body.style};
    return {ok:true,json:async()=>({})};
  },
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const settle = async()=>{for(let i=0;i<30;i++) await Promise.resolve();};
async function edit() {
  const input=graph.find(el=>el.attrs?.['aria-label']==='Color');
  input.value='#abcdef';input.oninput();
  graph.find(el=>el.textContent==='Apply').onclick();await settle();
}
(async()=>{
  listeners['mattervis-selection']({detail:{sceneId:'scene',targets:[target]}});await settle();
  await edit();
  graph.find(el=>el.textContent==='Undo').onclick();await settle();
  const patches=requests.filter(r=>r.path.endsWith('style_overrides'));
  assert.equal(patches.length,3);
  assert(patches.every(r=>r.body.target_revision===7));
  assert.equal(overrides.color,'#112233');
  await edit();revision=8;rejectStale=true;
  const before=requests.length;
  graph.find(el=>el.textContent==='Undo').onclick();await settle();
  assert.equal(requests.length,before+1,'stale reset must stop restoration');
  assert.equal(overrides.color,'#abcdef');
  assert.equal(graph.find(el=>el.attrs?.role==='status').textContent,'stale target revision');
  assert.equal(typeof intervalFn, 'function');
  const panelNode = graph.children[0];
  context.window.MatterVisSemantic = {sceneId:()=>'scene', selected:()=>[]};
  const quiet = requests.length;
  intervalFn();
  await settle();
  assert.equal(requests.length, quiet, 'an empty bridge must not reload the panel');
  panelNode.hidden = true;
  context.window.MatterVisSemantic = {sceneId:()=>'scene', selected:()=>[target]};
  intervalFn();
  await settle();
  assert(requests.length > quiet, 'a hidden panel reloads the bridge selection');
  const inherit = graph.find(el=>el.attrs?.['aria-label']==='Inherit Color');
  assert(inherit, 'Inherit Color is rendered for the reloaded pick');
  assert.equal(inherit.disabled, false);
  console.log('appearance undo revision contracts passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
