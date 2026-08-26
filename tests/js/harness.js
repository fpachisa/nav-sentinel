// Runs the desk's streaming client against a minimal fake DOM and a scripted NDJSON response.
//
// The client logic is the one layer nothing else covers: the Python tests check what is served,
// `node --check` checks that it parses, and a headless browser proves it attaches. None of them
// exercises what the handler *does* with the lines that arrive -- which is the demo, and which is
// forty lines of code that has already been broken once.
//
// The fake DOM is deliberately narrow: it answers exactly the queries the script makes. The step
// rows are returned for the four known stages rather than parsed out of the injected HTML, so this
// tests the handler's logic and not a hand-rolled HTML parser. That is a real limit and it is why
// the browser check exists alongside it.
const script = process.argv[2];
const fs = require('fs');

const marks = [];          // [stage, state, note]
const appended = [];       // innerHTML of each appended section
const state = { railHtml: null, status: null, finishedClass: false, disabled: false, label: null };

function step(stage) {
  const dataset = {};
  return {
    dataset: new Proxy(dataset, {
      set(t, k, v) { t[k] = v; if (k === 'state') marks.push([stage, v, t.note || null]); return true; },
      get(t, k) { return k === 'stage' ? stage : t[k]; },
    }),
    querySelector(sel) {
      if (sel !== '.pnote') throw new Error('unexpected selector ' + sel);
      return { set textContent(v) { dataset.note = v; }, get textContent() { return dataset.note; } };
    },
  };
}

const STAGES = ['triage', 'routing', 'investigation', 'proposal'];
const steps = STAGES.map(step);

const button = { set disabled(v) { state.disabled = v; }, set textContent(v) { state.label = v; } };
const form = {
  dataset: { progress: '<progress-rail/>', stream: '/app/case/C1/work/stream' },
  action: '/app/case/C1/work',
  _handler: null,
  addEventListener(kind, fn) { if (kind === 'submit') this._handler = fn; },
  querySelector(sel) { if (sel === 'button') return button; throw new Error(sel); },
};

const rail = {
  set innerHTML(v) { state.railHtml = v; },
  get innerHTML() { return state.railHtml; },
  querySelectorAll(sel) { if (sel !== '.pstep') throw new Error(sel); return steps; },
  querySelector(sel) { if (sel !== '.pstep') throw new Error(sel); return steps[0]; },
};

const host = { appendChild(node) { appended.push(node.innerHTML); } };

global.window = global;
global.document = {
  getElementById(id) {
    return {
      'work-form': form,
      'case-rail': rail,
      'case-sections': host,
      'work-progress': { classList: { add() { state.finishedClass = true; } } },
      'work-status': { set textContent(v) { state.status = v; } },
    }[id] || null;
  },
  createElement() { return { className: '', innerHTML: '' }; },
};

const LINES = [
  { stage: 'triage', state: 'running' },
  { stage: 'triage', state: 'done', detail: 'nav.fx_rate', html: '<div>TRIAGE</div>' },
  { stage: 'routing', state: 'running' },
  { stage: 'routing', state: 'done', detail: 'fx-rates-investigator@1.3.0', html: '' },
  { stage: 'investigation', state: 'running' },
  { stage: 'investigation', state: 'done', html: '<div>CAUSE</div><div>EVIDENCE</div>' },
  { stage: 'proposal', state: 'running' },
  { stage: 'proposal', state: 'done', html: '<div>PROPOSAL</div>' },
  { state: 'finished', rail: '<div>APPROVAL RAIL</div>' },
];

// Delivered in two chunks with a line split across the boundary, because that is what a real
// stream does and a reader that assumes whole lines per chunk works until it does not.
const whole = LINES.map((l) => JSON.stringify(l)).join('\n') + '\n';
const cut = Math.floor(whole.length * 0.37);
const chunks = [whole.slice(0, cut), whole.slice(cut)].map((s) => new TextEncoder().encode(s));

global.fetch = () => Promise.resolve({
  body: {
    getReader() {
      let i = 0;
      return { read: () => Promise.resolve(i < chunks.length ? { done: false, value: chunks[i++] } : { done: true }) };
    },
  },
});

eval(fs.readFileSync(script, 'utf8'));

let navigated = false;
form._handler({ preventDefault() { navigated = false; } });

setTimeout(() => {
  console.log(JSON.stringify({
    enhanced: form.dataset.enhanced,
    disabled: state.disabled,
    label: state.label,
    railSwapped: state.railHtml,
    marks,
    appended,
    status: state.status,
    finishedClass: state.finishedClass,
    navigated,
  }, null, 1));
}, 50);
