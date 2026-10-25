/* Selection and Appearance uses the existing semantic selection/style API.
 * Controls are capabilities, edits are sparse per-property Display intent. */
(function(root){
  'use strict';
  let panel, sceneId, selected=[], editing=[], report=null, serial=0, history=[], pending=Promise.resolve(), previewTimer;
  const labels={color:'Color',opacity:'Opacity',visible:'Visible',radius:'Radius (Å)',material:'Material',representation:'Representation',edge_color:'Edge color',edge_width:'Edge width',edge_opacity:'Edge opacity',flatshading:'Flat shading'};
  function node(tag,text){const el=document.createElement(tag);if(text!==undefined) el.textContent=text;return el;}
  async function request(path,body,method){
    const response=await fetch('/api/v2/'+path,{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({scene_id:sceneId},body))});
    const value=await response.json();if(!response.ok) throw Error(value.error||'Appearance update failed');return value;
  }
  function error(err){if(panel){const status=panel.querySelector('[role=status]');if(status){status.className='mv-appearance-error';status.textContent=err.message;}}}
  function targetsKey(targets){return targets.map(t=>t.kind+':'+t.id).join('|');}
  function serialize(action){pending=pending.then(action).catch(error);return pending;}
  function change(patch,reset){
    const id=sceneId, targets=editing.slice(), old=report, token=serial;
    if(!targets.length || !old) return;
    serialize(async()=>{
      if(sceneId!==id || token!==serial) return;
      const snapshot=old.items.map(row=>({target:row.target,style:row.overrides}));
      await request('style_overrides',Object.assign({targets,target_revision:old.target_revision},reset?{reset:true,field:reset===true?undefined:reset}:{style:patch}),'PATCH');
      if(sceneId!==id) return;
      history.push({sceneId:id,snapshot,target_revision:old.target_revision});if(history.length>30) history.shift();
      await load(targets);
    });
  }
  async function undo(){
    const record=history.pop();if(!record || record.sceneId!==sceneId) return;
    const id=sceneId;
    await request('style_overrides',{targets:record.snapshot.map(r=>r.target),target_revision:record.target_revision,reset:true},'PATCH');
    for(const row of record.snapshot){if(sceneId!==id) return;if(Object.keys(row.style).length) await request('style_overrides',{targets:[row.target],target_revision:record.target_revision,style:row.style},'PATCH');}
    await load(editing);
  }
  async function load(targets){
    const token=++serial, id=sceneId;
    const value=await request('selection/appearance',{targets});
    if(token!==serial || id!==sceneId) return;
    editing=targets;report=value;render();
  }
  function render(){
    if(!panel){const graph=document.getElementById('crystal-graph');if(!graph) return;panel=node('section');panel.id='mv-selection-appearance';panel.className='mv-appearance';panel.setAttribute('aria-label','Selection and Appearance');graph.appendChild(panel);}
    panel.replaceChildren();panel.hidden=!selected.length;if(panel.hidden) return;
    panel.appendChild(node('h3','Selection / Appearance'));
    const identity=node('p',selected.length===1 ? selected[0].kind+' · '+(selected[0].label||selected[0].id) : selected.length+' selected objects');identity.title=selected.map(t=>t.id).join('\n');panel.appendChild(identity);
    const scope=node('select');scope.setAttribute('aria-label','Edit scope');
    const options=[['selection','Selected visible instances']];
    if(selected.length===1){
      const t=selected[0];
      ((t.metadata||{}).parents||[]).forEach(p=>options.push(['parent:'+p.id,'Containing '+p.kind+' · '+(p.label||p.id)]));
      if(t.kind==='facet') options.push(['parent:morphology:bfdh-morphology','Whole morphology']);
      if(t.kind==='atom') options.push(['source','All source copies'],['symmetry','Same source and symmetry operation'],['molecule','Containing molecule instances'],['part','Same disorder part']);
      if(t.kind==='polyhedron') options.push(['spec','All instances of this spec']);
      if(t.kind==='facet') options.push(['facet_family','Same Miller index']);
    }
    options.forEach(([value,text])=>{const o=node('option',text);o.value=value;scope.appendChild(o);});
    const scopeLabel=node('label','Scope');scopeLabel.appendChild(scope);panel.appendChild(scopeLabel);
    const affected=report.items.reduce((sum,row)=>sum+(row.affected_count??1),0);
    const count=node('p','Editing '+editing.length+' target'+(editing.length===1?'':'s')+' · '+affected+' object'+(affected===1?'':'s')+' affected');panel.appendChild(count);
    scope.onchange=()=>{clearTimeout(previewTimer);return serialize(async()=>{
      const id=sceneId, value=scope.value;let targets=selected.slice();
      if(value.startsWith('parent:')){
        const parentId=value.slice(7),parent=(((selected[0].metadata||{}).parents)||[]).find(p=>p.id===parentId);
        targets=[parent||{kind:'morphology',id:parentId}];
      } else if(value!=='selection') {
        const result=await request('selection/scope',{target:selected[0],scope:value,commit:false});targets=result.targets;
      }
      if(id===sceneId){await load(targets);const select=panel.querySelector('select[aria-label="Edit scope"]');if(select) select.value=value;}
    });};
    if(report.discarded_targets?.length) panel.appendChild(node('p',report.discarded_targets.length+' obsolete appearance target(s) were removed.'));
    if(editing.length===1 && targetsKey(editing)!==targetsKey(selected)) {
      const promote=node('button','Select edited target');
      promote.onclick=()=>root.MatterVisSemantic.select({...editing[0],metadata:{...(editing[0].metadata||{}),child:selected[0]}},'replace');panel.appendChild(promote);
    }
    if(selected.length===1 && selected[0].metadata?.child) {
      const child=node('button','Back to picked child');child.onclick=()=>root.MatterVisSemantic.select(selected[0].metadata.child,'replace');panel.appendChild(child);
    }
    const draft={};
    (report.supported_fields||[]).filter(field=>field in labels).forEach(field=>{
      const row=node('label'), text=node('span',labels[field]);row.appendChild(text);
      const values=report.items.map(r=>r.effective[field]);
      const value=values.every(v=>v===values[0])?values[0]:null;
      const local=report.items.filter(r=>Object.hasOwn(r.overrides,field)).length;
      const input=node(field==='material'||field==='representation'?'select':'input');input.setAttribute('aria-label',labels[field]);
      if(input.tagName==='SELECT'){
        const choices=field==='material'?['mesh','flat']:['ball','ball_stick','stick','ortep','wireframe'];
        choices.forEach(v=>{const o=node('option',v);o.value=v;input.appendChild(o);});if(value) input.value=value;
      }else if(field.includes('color')){input.type='color';input.value=/^#[0-9a-f]{6}$/i.test(value)?value:'#808080';}
      else if(field==='visible'||field==='flatshading'){input.type='checkbox';input.checked=value!==false;}
      else {input.type='number';input.step=field.includes('opacity')?'0.05':'0.1';input.min='0';if(field.includes('opacity')) input.max='1';if(value!==undefined&&value!==null) input.value=value;}
      input.title=(value===null?'Mixed values; ':'')+(local?local+' local override(s)':'Inherited');
      input.oninput=()=>{draft[field]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;clearTimeout(previewTimer);previewTimer=setTimeout(()=>{const patch={...draft};if(Object.keys(patch).length) change(patch);},300);};
      row.appendChild(input);
      const reset=node('button','↶');reset.type='button';reset.title='Reset '+labels[field]+' to current inherited value';reset.setAttribute('aria-label','Inherit '+labels[field]);reset.disabled=!local;reset.onclick=()=>change({},field);row.appendChild(reset);
      panel.appendChild(row);panel.appendChild(node('small',labels[field]+': '+(local?'local override':'inherited')+(value===null?' · mixed':'')));
    });
    if(report.property_coloring) panel.appendChild(node('p','A local Color overrides the property map. Inherit Color restores current property colors.'));
    panel.appendChild(node('p','Changes save automatically after a short pause. Apply saves immediately; Undo restores the previous saved edit.'));
    const apply=node('button','Apply');apply.onclick=()=>{clearTimeout(previewTimer);if(Object.keys(draft).length) change(draft);};
    const undoButton=node('button','Undo');undoButton.disabled=!history.some(r=>r.sceneId===sceneId);undoButton.onclick=()=>serialize(undo);
    const resetAll=node('button','Reset all local overrides');resetAll.onclick=()=>change({},true);
    panel.append(apply,document.createTextNode(' '),undoButton,document.createTextNode(' '),resetAll);
    const hint=node('p','Alt-click cycles overlapping targets. Shift adds, Ctrl/Cmd toggles, Esc or empty space clears.');panel.appendChild(hint);
    const status=node('p');status.setAttribute('role','status');panel.appendChild(status);
  }
  root.addEventListener('mattervis-selection',event=>{
    clearTimeout(previewTimer);
    const detail=event.detail;
    if(sceneId!==detail.sceneId) history=[];
    sceneId=detail.sceneId;selected=detail.targets.slice();
    if(!selected.length){serial++;editing=[];if(panel) panel.hidden=true;return;}
    load(selected).catch(error);
  });
  // Scene handoffs do not share draft inputs or undo history. The same
  // scene can also hide this panel while the bridge still holds a pick
  // (a discarded appearance response, or a frame that never re-fired the
  // selection event). Reload that pick. An empty bridge is a user clear.
  setInterval(()=>{
    const bridge=root.MatterVisSemantic;if(!bridge) return;
    const live=bridge.sceneId();
    const bridgeSelected=(bridge.selected()||[]).slice();
    if(live!==sceneId){
      serial++;clearTimeout(previewTimer);sceneId=live;history=[];selected=bridgeSelected;
      if(panel) panel.hidden=true;
      if(selected.length) load(selected).catch(error);
      return;
    }
    if(!bridgeSelected.length) return;
    const hidden=!panel || panel.hidden;
    if(hidden || targetsKey(bridgeSelected)!==targetsKey(selected)){
      selected=bridgeSelected;
      load(selected).catch(error);
    }
  },300);
  root.MatterVisAppearance={refresh:()=>load(editing),targets:()=>editing.slice(),selectionKey:targetsKey};
})(window);
