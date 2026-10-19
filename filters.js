// Shared helpers for the explorer pages: data loading, aging-condition metadata,
// stable color and line-style assignment, and small form-control builders.
//
// Color and line style are assigned per condition value, never per row position,
// so toggling a filter never recolors the series that remain on screen.

export const DATA_DIR = 'data/processed/';

/** Fetch one processed JSON file, relative to the page. */
export async function loadJson(name) {
  const res = await fetch(DATA_DIR + name);
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  return res.json();
}

/** Replace a container's content with a readable load-failure message. */
export function showLoadError(container, err) {
  const box = document.createElement('div');
  box.className = 'notice';
  const p1 = document.createElement('p');
  p1.textContent = 'The data files could not be loaded.';
  const p2 = document.createElement('p');
  p2.append(
    'Browsers block pages opened directly from disk from reading local files. ' +
    'From the project folder run ',
    Object.assign(document.createElement('code'), { textContent: 'python3 -m http.server' }),
    ' and open ',
    Object.assign(document.createElement('code'), { textContent: 'http://localhost:8000/' }),
    '.'
  );
  const p3 = document.createElement('p');
  p3.textContent = `Details: ${err.message}`;
  box.append(p1, p2, p3);
  container.replaceChildren(box);
}

/** Current value of a CSS custom property, e.g. cssVar('--surface'). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Call back when the OS switches between light and dark mode. */
export function onThemeChange(callback) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', callback);
}

// ---------------------------------------------------------------------------
// Aging conditions
// ---------------------------------------------------------------------------

/** Voltage windows used by the cyclic-aging conditions (target voltages, in volts). */
export const WINDOWS = [
  { id: '2.50-4.20', label: '2.50–4.20 V', plotly: 'solid', css: '' },
  { id: '3.25-4.20', label: '3.25–4.20 V', plotly: 'dash', css: '6 4' },
  { id: '3.25-4.09', label: '3.25–4.09 V', plotly: 'dot', css: '1.5 3.5' },
];

/** Window identifier of a condition, matching WINDOWS[].id. */
export function windowId(cond) {
  return `${cond.v_min_V.toFixed(2)}-${cond.v_max_V.toFixed(2)}`;
}

/** Charge/discharge rate pairs (in C) of the cyclic-aging conditions. */
const RATE_PAIRS = [[0.33, 0.33], [0.33, 1], [1, 1], [1.67, 1]];

/**
 * Per aging type: which condition field picks the line color (`color`), which
 * optional field picks the line style (`dash`), and the default x-axis.
 * `values` order fixes the palette slot of each value (slot 1 = first value).
 */
export const AGING = {
  calendar: {
    label: 'Calendar',
    defaultX: 'days',
    color: {
      title: 'State of charge',
      values: [10, 50, 90, 100].map((v) => ({ id: String(v), label: `${v}% SoC`, short: `${v}% SoC` })),
      of: (c) => String(c.soc_pct),
    },
    dash: null,
  },
  cyclic: {
    label: 'Cyclic',
    defaultX: 'efc',
    color: {
      title: 'Charge / discharge rate',
      values: RATE_PAIRS.map(([a, b]) => ({
        id: `${a}|${b}`,
        label: `${a} C charge · ${b} C discharge`,
        short: `${a} / ${b} C`,
      })),
      of: (c) => `${c.chg_rate_C}|${c.dischg_rate_C}`,
    },
    dash: {
      title: 'Voltage window',
      values: WINDOWS,
      of: windowId,
      defaultChecked: [WINDOWS[0].id],
    },
  },
  profile: {
    label: 'Driving profile',
    defaultX: 'efc',
    color: {
      title: 'WLTP profile',
      values: [
        { id: '2', label: 'Full cycle, charged to 100%', short: 'Full, to 100%' },
        { id: '3', label: 'Full cycle, charged to 90%', short: 'Full, to 90%' },
        { id: '4', label: 'Extra-high section, charged to 90%', short: 'Extra-high, to 90%' },
      ],
      of: (c) => String(c.profile),
    },
    dash: null,
  },
};

export const TEMPERATURES = [0, 10, 25, 40];

/** CSS variable name of the n-th (0-based) categorical series color. */
export const seriesVar = (index) => `--series-${index + 1}`;

// ---------------------------------------------------------------------------
// Form controls
// ---------------------------------------------------------------------------

/** Build a segmented single-choice control inside `container`. */
export function radioGroup(container, name, options, selected, onChange) {
  container.replaceChildren();
  for (const opt of options) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = name;
    input.value = opt.id;
    input.checked = opt.id === selected;
    input.addEventListener('change', () => onChange(opt.id));
    const text = document.createElement('span');
    text.textContent = opt.label;
    label.append(input, text);
    container.append(label);
  }
}

/** Small line key: a short stroke in a series color and/or dash pattern. */
export function lineKey({ colorVar = '--ink-2', dash = '' }) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '28');
  svg.setAttribute('height', '10');
  svg.setAttribute('aria-hidden', 'true');
  const line = document.createElementNS(ns, 'line');
  line.setAttribute('x1', '1');
  line.setAttribute('x2', '27');
  line.setAttribute('y1', '5');
  line.setAttribute('y2', '5');
  line.setAttribute('stroke-width', '2');
  line.style.stroke = `var(${colorVar})`;
  if (dash) line.style.strokeDasharray = dash;
  svg.append(line);
  return svg;
}

/**
 * Build a multi-choice control inside `container`. Each option may carry a
 * `key` ({colorVar, dash}) drawn beside the label so the control doubles as the legend.
 */
export function checkGroup(container, options, checkedIds, onChange) {
  container.replaceChildren();
  for (const opt of options) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = opt.id;
    input.checked = checkedIds.includes(opt.id);
    input.addEventListener('change', () => {
      onChange([...container.querySelectorAll('input:checked')].map((el) => el.value));
    });
    const text = document.createElement('span');
    text.textContent = opt.label;
    label.append(input);
    if (opt.key) label.append(lineKey(opt.key));
    label.append(text);
    container.append(label);
  }
}
