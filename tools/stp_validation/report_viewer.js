/* Offline software projection of the real CAD mesh; no WebGL dependency. */
function createTubeViewer(container, traces) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'width:100%;height:100%;touch-action:none;cursor:grab';
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  const mesh = traces[0];
  const vertices = mesh.x.map((x, i) => [x, mesh.y[i], mesh.z[i]]);
  const lines = traces.slice(1, 3).map(t => t.x.map((x, i) => [x, t.y[i], t.z[i]]));
  const bounds = [0, 1, 2].map(k => {
    let lo = Infinity, hi = -Infinity;
    for (const p of vertices) { lo = Math.min(lo, p[k]); hi = Math.max(hi, p[k]); }
    return [lo, hi];
  });
  const center = bounds.map(([a, b]) => (a + b) / 2);
  const span = Math.max(...bounds.map(([a, b]) => b - a));
  let yaw = .10, pitch = .55, zoom = 1, selected = [], drag;
  function draw() {
    const width = container.clientWidth, height = container.clientHeight;
    canvas.width = width * devicePixelRatio; canvas.height = height * devicePixelRatio;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const scale = Math.min(width * .9, height * 1.7) / span * zoom;
    function project(p) {
      const x = p[0] - center[0], y = p[1] - center[1], z = p[2] - center[2];
      const a = x * Math.cos(yaw) + z * Math.sin(yaw);
      const b = -x * Math.sin(yaw) + z * Math.cos(yaw);
      return [width / 2 + a * scale, height / 2 - (y * Math.cos(pitch) - b * Math.sin(pitch)) * scale,
        y * Math.sin(pitch) + b * Math.cos(pitch)];
    }
    const pv = vertices.map(project);
    const order = mesh.i.map((_, i) => i).sort((a, b) =>
      pv[mesh.i[a]][2] + pv[mesh.j[a]][2] + pv[mesh.k[a]][2] - pv[mesh.i[b]][2] - pv[mesh.j[b]][2] - pv[mesh.k[b]][2]);
    ctx.fillStyle = 'rgba(84,145,186,.10)';
    for (const i of order) {
      const a = pv[mesh.i[i]], b = pv[mesh.j[i]], c = pv[mesh.k[i]];
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.lineTo(c[0], c[1]); ctx.closePath(); ctx.fill();
    }
    function line(points, color, width, dash = []) {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.beginPath();
      points.map(project).forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
    }
    line(lines[1], '#e9a23b', 2, [6, 4]); line(lines[0], '#e11d48', 3);
    if (selected.length) line(selected.concat([selected[0]]), '#00a58b', 2);
    ctx.setLineDash([]); ctx.fillStyle = '#617087'; ctx.font = '13px Microsoft YaHei';
    ctx.fillText('真实三维网格 · 拖动旋转 / 滚轮缩放', 12, 24);
    ctx.fillStyle = '#e11d48'; ctx.fillText('红：修复后中心线', 12, height - 18);
    ctx.fillStyle = '#c78519'; ctx.fillText('橙：旧版中心线', 165, height - 18);
  }
  canvas.onpointerdown = e => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); };
  canvas.onpointermove = e => {
    if (!drag) return;
    yaw += (e.clientX - drag[0]) * .007; pitch += (e.clientY - drag[1]) * .007;
    drag = [e.clientX, e.clientY]; draw();
  };
  canvas.onpointerup = () => { drag = null; };
  canvas.onwheel = e => { e.preventDefault(); zoom = Math.max(.3, Math.min(6, zoom * Math.exp(-e.deltaY * .001))); draw(); };
  new ResizeObserver(draw).observe(container);
  draw();
  return { section(points) { selected = points; draw(); } };
}
