// Execute the generated viewer page's script against a stub DOM/canvas.
// Catches syntax errors, undefined references and wrong data indexing without a browser.
import fs from 'node:fs';

const file = process.argv[2];
const html = fs.readFileSync(file, 'utf8');
const match = html.match(/<script>([\s\S]*)<\/script>/);
if (!match) { console.error('未找到内联脚本'); process.exit(2); }
const code = match[1];

const calls = {};
function ctx2d() {
  return new Proxy({}, {
    get(target, prop) {
      if (prop === 'canvas') return {};
      if (prop in target) return target[prop];
      return (...args) => {
        calls[prop] = (calls[prop] || 0) + 1;
        for (const a of args) {
          if (typeof a === 'number' && !Number.isFinite(a)) {
            throw new Error(`Canvas 参数出现非有限数：${String(prop)}(${args.join(',')})`);
          }
        }
        return undefined;
      };
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      value: '0',
      textContent: '',
      innerHTML: '',
      hidden: false,
      width: 0,
      height: 0,
      classList: { add() {}, remove() {} },
      style: {},
      getContext: () => ctx2d(),
      getBoundingClientRect: () => ({ width: 1000, height: 430, left: 0, top: 0 }),
      addEventListener() {},
      setPointerCapture() {},
      releasePointerCapture() {},
      querySelector: () => null,
    });
  }
  return elements.get(id);
}

const listeners = {};
globalThis.window = {
  devicePixelRatio: 2,
  addEventListener: (name, fn) => { (listeners[name] ||= []).push(fn); },
};
globalThis.document = {
  getElementById: element,
  querySelectorAll: () => [],
};
globalThis.requestAnimationFrame = (fn) => fn();

try {
  new Function(code)();
} catch (err) {
  console.error('页面脚本执行失败：', err && err.stack ? err.stack : err);
  process.exit(1);
}

const essential = ['beginPath', 'moveTo', 'lineTo', 'stroke', 'fill', 'arc', 'strokeRect'];
const missing = essential.filter((name) => !calls[name]);
const summary = Object.entries(calls).sort((a, b) => b[1] - a[1]).slice(0, 8)
  .map(([k, v]) => `${k}=${v}`).join(' ');
console.log(`画布调用：${summary}`);
console.log(`读数字段长度：${element('readout').innerHTML.length}`);
if (missing.length) {
  console.error('缺少关键绘制调用：', missing.join(', '));
  process.exit(1);
}
if (element('readout').innerHTML.length < 100) {
  console.error('读数面板为空');
  process.exit(1);
}
console.log('OK');
