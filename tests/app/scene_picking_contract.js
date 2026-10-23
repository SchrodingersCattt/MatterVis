const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const root={};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{window:root,Map,Set,WeakMap,Array,Math,Number,JSON});
root.MatterVisSemantic={targetFromPoint:p=>p.customdata || (p.data.meta.geometry_id?{kind:'geometry',id:p.data.meta.face_ids?.[p.faceNumber]||p.data.meta.geometry_id}:null)};
const camera={model:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],view:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],projection:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]};
const rect={left:0,top:0,right:200,bottom:200,x:0,y:0,width:200,height:200};
const axis={d2l:n=>n};
function graph(data){return {data,_fullData:data,_fullLayout:{scene:{_scene:{glplot:{cameraParams:camera,canvas:{getBoundingClientRect:()=>rect}},dataScale:[1,1,1],fullSceneLayout:{xaxis:axis,yaxis:axis,zaxis:axis}}}}};}
function triangle(id,z,opacity=1,more={}){return {type:'mesh3d',x:[-.8,.8,-.8],y:[-.8,-.8,.8],z:[z,z,z],i:[0],j:[1],k:[2],opacity,meta:{geometry_id:id,face_ids:[id]},...more};}
const api=root.MatterVisScenePicking;
assert.equal(api.hits(graph([{...triangle('edge',-.5),meta:{kind:'polyhedron',spec_id:'shared'}}]),60,140).length,0);
const front=triangle('front',-.5),back=triangle('back',.5);
assert.equal(api.hits(graph([back,front]),60,140)[0].id,'front');
assert.equal(api.hits(graph([front,back]),60,140)[0].id,'front');
assert.deepEqual(Array.from(api.hits(graph([{...front,opacity:.4},back]),60,140),t=>t.id),['front','back']);
assert.equal(api.hits(graph([{...front,visible:false},back]),60,140)[0].id,'back');
assert.equal(api.hits(graph([{...front,opacity:0},back]),60,140)[0].id,'back');
assert.equal(api.hits(graph([{...front,meta:{...front.meta,pickable:false}},back]),60,140).length,0);
const bond={type:'scatter3d',x:[0],y:[0],z:[0],customdata:[{kind:'bond',id:'bond:one'}],meta:{mv_role:'bond_selection',mv_segments:[[[-.8,0,0],[.8,0,0]]]}};
assert.equal(api.hits(graph([bond]),35,100)[0].id,'bond:one'); // away from midpoint
assert.equal(api.hits(graph([bond]),35,108).length,0);
const atom={type:'scatter3d',x:[0],y:[0],z:[-.7],customdata:[{kind:'atom',id:'atom:source:0|image:1,0,0'}],meta:{mv_role:'atom_selection',mv_radii:[.1],mv_parents:[[{kind:'part',id:'part:a'}]]}};
assert.equal(api.hits(graph([bond,atom]),100,100)[0].kind,'atom');
assert.equal(api.hits(graph([atom]),100,100)[0].metadata.parents[0].id,'part:a');
const gd=graph([front]);assert.equal(api.index(gd),api.index(gd));gd.data=[back];gd._fullData=gd.data;assert.equal(api.hits(gd,60,140)[0].id,'back');
assert.equal(api.hits(graph([front]),190,10).length,0);
console.log('semantic local picking contracts passed');
