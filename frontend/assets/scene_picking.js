/* Local semantic picking over the geometry painted by Plotly. One projected
 * index per frame/camera; pointer motion touches a small screen-space grid.
 * No requests, Plotly.react, or mutation of trace colours on this path. */
(function (root) {
  'use strict';
  const POLICY = {cell: 48, hoverWidth: 1.25, selectedWidth: 2.25, bondTolerance: 4, contourSegments: 24, depthEpsilon: 1e-6};
  const cache = new WeakMap();
  const key = t => t.kind + ':' + t.id;
  function mul(m, p) {
    return [0,1,2,3].map(r => m[r]*p[0]+m[r+4]*p[1]+m[r+8]*p[2]+m[r+12]*p[3]);
  }
  function projector(gd) {
    const s = gd && gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
    const params = s && s.glplot && s.glplot.cameraParams;
    if (!params || !params.model || !params.view || !params.projection) return null;
    const rect = s.glplot.canvas.getBoundingClientRect();
    const axes = s.fullSceneLayout;
    function project(p) {
      const data = ['x','y','z'].map((a,i) => axes[a+'axis'].d2l(Number(p[i])) * s.dataScale[i]);
      const view = mul(params.view, mul(params.model, data.concat(1)));
      const clip = mul(params.projection, view);
      if (!(clip[3] > 0) || !clip.every(Number.isFinite)) return null;
      return [rect.left + (clip[0]/clip[3]+1)*rect.width/2,
              rect.top + (1-clip[1]/clip[3])*rect.height/2, clip[2]/clip[3], clip[3]];
    }
    return {project, rect, signature: JSON.stringify([params, rect.x, rect.y, rect.width, rect.height])};
  }
  function barycentric(x, y, a, b, c) {
    const d=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
    if (Math.abs(d)<1e-9) return null;
    const u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/d;
    const v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/d;
    return u>=-1e-6 && v>=-1e-6 && u+v<=1+1e-6 ? [u,v,1-u-v] : null;
  }
  function index(gd) {
    const camera=projector(gd), bridge=root.MatterVisSemantic;
    if (!camera || !bridge) return null;
    const previous=cache.get(gd);
    if (previous && previous.data===gd.data && previous.signature===camera.signature) return previous;
    const primitives=[], grid=new Map(), targets=new Map();
    function add(item, bounds) {
      if (!bounds.every(Number.isFinite)) return;
      const r=camera.rect;
      const loX=Math.max(r.left,bounds[0]), loY=Math.max(r.top,bounds[1]);
      const hiX=Math.min(r.right,bounds[2]), hiY=Math.min(r.bottom,bounds[3]);
      if (loX>hiX || loY>hiY) return;
      const n=primitives.push(item)-1;
      if (item.target) {
        const k=key(item.target);
        if (!targets.has(k)) targets.set(k,[]);
        targets.get(k).push(item);
      }
      for(let x=Math.floor(loX/POLICY.cell);x<=Math.floor(hiX/POLICY.cell);x++)
        for(let y=Math.floor(loY/POLICY.cell);y<=Math.floor(hiY/POLICY.cell);y++) {
          const k=x+','+y;
          if (!grid.has(k)) grid.set(k,[]);
          grid.get(k).push(n);
        }
    }
    (gd._fullData || gd.data || []).forEach(trace => {
      if (!trace || trace.visible===false || trace.visible==='legendonly' || Number(trace.opacity)===0) return;
      const m=trace.meta || {}, role=m.mv_role || m.kind || '';
      const point=(n,extra) => bridge.targetFromPoint(Object.assign({data:trace,customdata:trace.customdata&&trace.customdata[n],pointNumber:n},extra));
      const coord=n=>[trace.x[n],trace.y[n],trace.z[n]];
      if (role==='atom_selection') {
        for(let n=0;n<trace.x.length;n++) {
          const p=camera.project(coord(n)), target=point(n);
          if (!p || !target) continue;
          const radius=Number((m.mv_radii||[])[n]) || 0.2;
          const probes=[0,1,2].map(axis=>{const a=coord(n).map(Number);a[axis]+=radius;return camera.project(a);});
          const r=Math.max(3,...probes.filter(Boolean).map(q=>Math.hypot(q[0]-p[0],q[1]-p[1])));
          const parents=(m.mv_parents||[])[n] || [];
          target.metadata=Object.assign({},target.metadata,{parents});
          const opacity=Number((m.mv_opacities||[])[n] ?? 1);
          if (opacity<=0) continue;
          const depthRadius=Math.hypot(...probes.filter(Boolean).map(q=>q[2]-p[2]));
          add({type:'atom',p,r,depthRadius,target,parents,opacity},[p[0]-r,p[1]-r,p[0]+r,p[1]+r]);
        }
      } else if(role==='bond_selection') {
        (m.mv_segments||[]).forEach((segment,n)=>{
          const a=camera.project(segment[0]),b=camera.project(segment[1]),target=point(n);
          if(!a || !b || !target) return;
          const center=segment[0].map((v,i)=>(v+segment[1][i])/2);
          const c=camera.project(center),radius=(m.mv_bond_radii||[])[n]||0;
          const probes=[0,1,2].map(axis=>{const p=center.slice();p[axis]+=radius;return camera.project(p);});
          const pad=Math.max(POLICY.bondTolerance,...probes.filter(Boolean).map(q=>Math.hypot(q[0]-c[0],q[1]-c[1])));
          add({type:'bond',a,b,pad,target,opacity:1},[Math.min(a[0],b[0])-pad,Math.min(a[1],b[1])-pad,Math.max(a[0],b[0])+pad,Math.max(a[1],b[1])+pad]);
        });
      } else if(trace.type==='mesh3d' && ((m.face_ids && m.face_ids.length) || m.geometry_id || m.instance_id)) {
        const points=Array.from(trace.x,(_,n)=>camera.project(coord(n)));
        for(let n=0;n<(trace.i||[]).length;n++) {
          const a=points[trace.i[n]],b=points[trace.j[n]],c=points[trace.k[n]];
          if(!a || !b || !c) continue;
          const target=point(n,{customdata:null,faceNumber:n});
          if (target && m.facet_millers) target.miller=m.facet_millers[n];
          add({type:'triangle',a,b,c,target,opacity:Number(trace.opacity ?? 1),pickable:m.pickable!==false && m.mv_pickable!==false},
            [Math.min(a[0],b[0],c[0]),Math.min(a[1],b[1],c[1]),Math.max(a[0],b[0],c[0]),Math.max(a[1],b[1],c[1])]);
        }
      }
    });
    const value={data:gd.data,signature:camera.signature,primitives,grid,targets,rect:camera.rect};
    cache.set(gd,value); return value;
  }
  function hits(gd,x,y) {
    const idx=index(gd); if(!idx) return null;
    const candidates=[];
    (idx.grid.get(Math.floor(x/POLICY.cell)+','+Math.floor(y/POLICY.cell)) || []).forEach(n=>{
      const p=idx.primitives[n]; let depth=null;
      if(p.type==='atom') {const d=Math.hypot(x-p.p[0],y-p.p[1]);if(d<=p.r) depth=p.p[2]-p.depthRadius*Math.sqrt(Math.max(0,1-d*d/(p.r*p.r)));}
      else if(p.type==='bond') {
        const dx=p.b[0]-p.a[0],dy=p.b[1]-p.a[1],length=dx*dx+dy*dy;
        const t=length ? Math.max(0,Math.min(1,((x-p.a[0])*dx+(y-p.a[1])*dy)/length)) : 0;
        if(Math.hypot(x-p.a[0]-t*dx,y-p.a[1]-t*dy)<=(p.pad||POLICY.bondTolerance)) depth=p.a[2]+t*(p.b[2]-p.a[2]);
      } else {
        const w=barycentric(x,y,p.a,p.b,p.c);
        if(w) depth=w[0]*p.a[2]+w[1]*p.b[2]+w[2]*p.c[2];
      }
      if(depth!==null) candidates.push({target:p.target,depth,opacity:p.opacity,pickable:p.pickable!==false});
    });
    const priority={atom:6,bond:5,polyhedron:4,facet:3,geometry:2};
    candidates.sort((a,b)=>Math.abs(a.depth-b.depth)>POLICY.depthEpsilon ? a.depth-b.depth :
      (priority[b.target&&b.target.kind]||0)-(priority[a.target&&a.target.kind]||0) || key(a.target||{}).localeCompare(key(b.target||{})));
    const opaque=candidates.find(p=>p.opacity>=0.999);
    const seen=new Set();
    return candidates.filter(p=>p.target && p.pickable && (!opaque || p.depth<=opaque.depth+POLICY.depthEpsilon))
      .filter(p=>{const k=key(p.target);if(seen.has(k)) return false;seen.add(k);return true;}).map(p=>p.target);
  }
  function paint(gd,selected,hover) {
    const idx=index(gd); if(!idx) return;
    let canvas=gd.querySelector('.mv-focus-canvas');
    if(!canvas) {canvas=document.createElement('canvas');canvas.className='mv-focus-canvas';canvas.setAttribute('aria-hidden','true');gd.appendChild(canvas);}
    const rect=gd.getBoundingClientRect(), ratio=root.devicePixelRatio||1;
    canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);
    const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.translate(-rect.left,-rect.top);
    function outline(target,width) {
      let primitives=idx.targets.get(key(target)) || [];
      if(target.kind==='molecule'||target.kind==='part') primitives=idx.primitives.filter(p=>(p.parents||[]).some(t=>key(t)===key(target)));
      if(target.kind==='morphology') primitives=idx.primitives.filter(p=>p.target&&p.target.kind==='facet');
      if(!primitives.length) return;
      const dark = gd._fullLayout && /^#(?:[0-5][0-9a-f]){3}$/i.test(gd._fullLayout.paper_bgcolor || '');
      ctx.strokeStyle=dark?'rgba(221,226,232,.85)':'rgba(65,76,89,.80)';ctx.lineWidth=width;
      ctx.beginPath();
      function visible(x,y) {
        return (hits(gd,x,y)||[]).some(t=>key(t)===key(target) ||
          ((target.kind==='molecule'||target.kind==='part') && ((t.metadata||{}).parents||[]).some(p=>key(p)===key(target))) ||
          (target.kind==='morphology' && t.kind==='facet'));
      }
      const edges=new Map();
      primitives.forEach(p=>{
        if(p.type==='atom') {
          for(let i=0;i<POLICY.contourSegments;i++) {
            const a=i*Math.PI*2/POLICY.contourSegments,b=(i+1)*Math.PI*2/POLICY.contourSegments,m=(a+b)/2;
            if(!visible(p.p[0]+Math.cos(m)*Math.max(0,p.r-1),p.p[1]+Math.sin(m)*Math.max(0,p.r-1))) continue;
            ctx.moveTo(p.p[0]+Math.cos(a)*(p.r+1),p.p[1]+Math.sin(a)*(p.r+1));ctx.lineTo(p.p[0]+Math.cos(b)*(p.r+1),p.p[1]+Math.sin(b)*(p.r+1));
          }
        }
        else if(p.type==='bond') {if(visible((p.a[0]+p.b[0])/2,(p.a[1]+p.b[1])/2)){ctx.moveTo(p.a[0],p.a[1]);ctx.lineTo(p.b[0],p.b[1]);}}
        else [[p.a,p.b],[p.b,p.c],[p.c,p.a]].forEach(([a,b])=>{
          const k=[a,b].map(q=>q.slice(0,2).map(v=>v.toFixed(2)).join(',')).sort().join('|');
          const e=edges.get(k); if(e) e.count++; else edges.set(k,{a,b,count:1});
        });
      });
      // Boundary edges remove triangulation diagonals on broad facets.
      edges.forEach(e=>{if((e.count===1 || target.kind!=='facet') && visible((e.a[0]+e.b[0])/2,(e.a[1]+e.b[1])/2)){ctx.moveTo(e.a[0],e.a[1]);ctx.lineTo(e.b[0],e.b[1]);}});
      ctx.stroke();
    }
    selected.forEach(t=>outline(t,POLICY.selectedWidth));
    if(hover && !selected.some(t=>key(t)===key(hover))) outline(hover,POLICY.hoverWidth);
  }
  root.MatterVisScenePicking={hits,paint,index,projector,policy:POLICY,invalidate:gd=>cache.delete(gd)};
})(window);
