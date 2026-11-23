// Diagnostic viewer: renders saved reader coordinates without refitting them.
import * as THREE from 'three';
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js';
import {STLLoader} from 'three/examples/jsm/loaders/STLLoader.js';
import {SVGRenderer} from 'three/examples/jsm/renderers/SVGRenderer.js';

async function main() {
  const data = await (await fetch('data.json')).json();
  const host = document.getElementById('viewport');
  const shape = await new STLLoader().loadAsync('source.stl');
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#f6f9fc');
  let renderer;
  try { renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true}); }
  catch { renderer = new SVGRenderer(); renderer.setQuality('high'); }
  host.prepend(renderer.domElement);
  const camera = new THREE.OrthographicCamera(-400,400,200,-200,.1,10000);
  camera.up.set(0,1,0);
  const controls = new OrbitControls(camera, renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff,.8));
  const light = new THREE.DirectionalLight(0xffffff,.6);
  light.position.set(400,600,1000); scene.add(light);
  const material = new THREE.MeshPhongMaterial({color:0x82abc8, transparent:true, opacity:.18,
    side:THREE.DoubleSide, depthWrite:false});
  scene.add(new THREE.Mesh(shape,material));
  function line(points,color,width) {
    const geometry = new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p)));
    const mesh = new THREE.Line(geometry,new THREE.LineBasicMaterial({color,linewidth:width,depthTest:false,depthWrite:false}));
    mesh.renderOrder=10;scene.add(mesh);return mesh;
  }
  line(data.points,0xcc3551,3);
  const ends = ['start','end'].map(side=>data.sections.map((r,i)=>r.side===side?i:-1).filter(i=>i>=0));
  ends.forEach((indices,i)=> {
    if(!indices.length)return;
    const a=Math.max(0,indices[0]-(i===1?1:0));
    const b=Math.min(data.points.length-1,indices.at(-1)+(i===0?1:0));
    line(data.points.slice(a,b+1),0xf09415,5);
  });
  data.review.forEach(i=> {
    const dot=new THREE.Mesh(new THREE.SphereGeometry(.7,8,6),new THREE.MeshBasicMaterial({color:0x823ea4,depthTest:false}));
    dot.position.fromArray(data.points[i]);dot.renderOrder=20;scene.add(dot);
  });
  const tags=[];
  function tag(label,point,side) {
    const el=document.createElement('div');el.className='tag';el.textContent=label;host.appendChild(el);
    tags.push({el,point:new THREE.Vector3(...point),side});
  }
  tag('起点 s=0 · 已补到端面',data.points[0],'start');
  tag(`终点 s=${data.length.toFixed(3)} mm`,data.points.at(-1),'end');
  ends.forEach((indices,i)=> {
    if(indices.length) {
      const anchor=i===0?indices.at(-1)+1:indices[0]-1;
      tag(`可靠数据接点 · s=${data.sections[anchor].s.toFixed(3)}`,data.points[anchor],i===0?'start':'end');
    }
  });
  shape.computeBoundingBox();const center=shape.boundingBox.getCenter(new THREE.Vector3());
  let mode='whole',span=500;
  function resize() {
    const w=host.clientWidth,h=host.clientHeight;
    renderer.setSize(w,h,false);camera.left=-span*w/h/2;camera.right=span*w/h/2;
    camera.top=span/2;camera.bottom=-span/2;camera.updateProjectionMatrix();
  }
  function render() {
    tags.forEach((t,i)=> {
      const p=t.point.clone().project(camera);
      t.el.style.display=(Math.abs(p.x)<.95&&Math.abs(p.y)<.94&&(mode==='whole'?i<2:t.side===mode))?'block':'none';
      const left=(p.x+1)/2*host.clientWidth+(i<2?-20:14);
      t.el.style.left=Math.max(10,Math.min(host.clientWidth-t.el.offsetWidth-10,left))+'px';
      t.el.style.top=((-p.y+1)/2*host.clientHeight+(i<2?28:-38))+'px';
    });renderer.render(scene,camera);
  }
  function setView(next) {
    mode=next;const end=mode==='start'?0:1;
    const target=mode==='whole'?center.clone():new THREE.Vector3(...data.points[end===0?0:data.points.length-1]);
    span=mode==='whole'?900/(host.clientWidth/host.clientHeight):mode==='start'?160:110;
    camera.position.copy(target).add(mode==='whole'?new THREE.Vector3(100,240,1000):new THREE.Vector3(100,150,1000));
    camera.zoom=1;controls.target.copy(target);camera.lookAt(target);controls.update();resize();
    ['whole','start','end'].forEach(v=>document.getElementById(v).classList.toggle('active',v===mode));
    document.getElementById('view-label').textContent=mode==='whole'?'整体三维 · 原始比例':`${mode==='start'?'起点':'终点'}端部放大 · 原始比例`;
    const selected=mode==='whole'?[0,data.sections.length-1]:mode==='start'?[0,Math.max(...ends[0])+1]:[Math.min(...ends[1])-1,data.sections.length-1];
    document.getElementById('table').innerHTML=selected.map(i=> {
      const r=data.sections[i];return `<tr><td>${r.s.toFixed(3)}</td><td>${r.p.toFixed(3)}</td><td>${r.t.toFixed(4)}</td><td>${r.status==='extrapolated'?'线性外推':r.status==='needs_review'?'待复核':'测量'}</td><td>${data.points[i].map(v=>v.toFixed(3)).join(' / ')}</td></tr>`;
    }).join('');render();
  }
  ['whole','start','end'].forEach(v=>document.getElementById(v).onclick=()=>setView(v));
  controls.addEventListener('change',render);window.addEventListener('resize',()=>{resize();render();});
  document.getElementById('length').textContent=data.length.toFixed(3)+' mm';
  document.getElementById('count').textContent=data.points.length;
  document.getElementById('end-lengths').textContent=data.trim.map(v=>v.toFixed(3)).join(' / ')+' mm';
  document.getElementById('review').textContent=data.review.length;
  document.getElementById('basis').textContent=data.basis;
  setView('whole');document.getElementById('status').textContent='已加载：原模型 + 读取中心线 + 外推端部';
}
main().catch(e=>document.getElementById('status').textContent='加载失败：'+e.message);
