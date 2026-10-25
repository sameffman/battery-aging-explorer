// Shared helpers for the explorer pages: data loading, aging-condition metadata,
// filter controls, stable color and line-style assignment, and chart and table builders.
//
// Color and line style are assigned per condition value, never per row position,
// so toggling a filter never recolors the series that remain on screen.

export const DATA_DIR = 'data/processed/';
export const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
export const NARROW_QUERY = '(max-width: 599px)';

/** Last of the regular check-ups; check-up 29 is the manual one at the end of the experiment. */
export const LAST_REGULAR_CU = 28;
export const MANUAL_CU = 29;

/** X-axis choices for the aging charts: stored field name -> axis label. */
export const X_AXES = { efc: 'Equivalent full cycles', days: 'Time (days)' };

// ---------------------------------------------------------------------------
// Page and data helpers
// ---------------------------------------------------------------------------

/** Shorthand for document.getElementById. */
export const $ = (id) => document.getElementById(id);

/** Fetch one processed JSON file, relative to the page. */
export async function loadJson(name) {
  const res = await fetch(DATA_DIR + name);
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  return res.json();
}

const spectraCache = new Map();   // cell id -> spectra list

/** Impedance spectra of one cell, fetched the first time the cell is asked for. */
export async function spectraOf(cid) {
  if (!spectraCache.has(cid)) spectraCache.set(cid, await loadJson(`impedance/${cid}.json`));
  return spectraCache.get(cid);
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

/** Whether the viewport is narrow enough for the compact chart layout. */
export const isNarrow = () => window.matchMedia(NARROW_QUERY).matches;

/** Call `render` again when the OS switches between light and dark mode or the viewport crosses the narrow breakpoint. */
export function watchDisplay(render) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
  window.matchMedia(NARROW_QUERY).addEventListener('change', render);
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

/** Default color, line-style and x-axis selections for an aging type. */
export function typeDefaults(type) {
  const a = AGING[type];
  return {
    color: a.color.values.map((v) => v.id),
    dash: a.dash ? [...a.dash.defaultChecked] : [],
    x: a.defaultX,
  };
}

/** Ids of the cells whose conditions match the filter state (type, temp, color, dash), sorted. */
export function visibleCells(conditions, state) {
  const a = AGING[state.type];
  return Object.keys(conditions).sort().filter((cid) => {
    const c = conditions[cid];
    return c.age_type === state.type
      && c.temp_C === state.temp
      && state.color.includes(a.color.of(c))
      && (!a.dash || state.dash.includes(a.dash.of(c)));
  });
}

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

/** X-axis control (#x-options); the selection is written to `state.x`. */
function buildXControl(state, render) {
  radioGroup($('x-options'), 'x',
    Object.entries(X_AXES).map(([id, label]) => ({ id, label })),
    state.x, (id) => { state.x = id; render(); });
}

/** Color (and optional line-style) filters, which double as the chart legend. */
function buildDimensionControls(state, render) {
  const a = AGING[state.type];
  $('color-title').textContent = a.color.title;
  checkGroup($('color-options'),
    a.color.values.map((v, i) => ({ id: v.id, label: v.label, key: { colorVar: seriesVar(i) } })),
    state.color, (ids) => { state.color = ids; render(); });

  $('dash-group').hidden = !a.dash;
  if (a.dash) {
    $('dash-title').textContent = a.dash.title;
    checkGroup($('dash-options'),
      a.dash.values.map((v) => ({ id: v.id, label: v.label, key: { dash: v.css } })),
      state.dash, (ids) => { state.dash = ids; render(); });
  }
}

/**
 * Build the filter controls both pages share: aging type, temperature, x axis,
 * and the color and line-style filters. Selections are written to `state`
 * (type, temp, x, color, dash) and `render` is called after each change.
 * Changing the aging type resets the other selections to that type's defaults.
 */
export function buildFilterControls(state, render) {
  radioGroup($('type-options'), 'type',
    Object.entries(AGING).map(([id, a]) => ({ id, label: a.label })),
    state.type, (id) => {
      state.type = id;
      Object.assign(state, typeDefaults(id));
      buildXControl(state, render);
      buildDimensionControls(state, render);
      render();
    });

  radioGroup($('temp-options'), 'temp',
    TEMPERATURES.map((t) => ({ id: String(t), label: `${t} °C` })),
    String(state.temp), (id) => { state.temp = Number(id); render(); });

  buildXControl(state, render);
  buildDimensionControls(state, render);
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

/** Plotly layout fields common to every chart. */
export function baseLayout(narrow) {
  const surface = cssVar('--surface');
  return {
    paper_bgcolor: surface,
    plot_bgcolor: surface,
    font: { family: FONT, size: 12, color: cssVar('--ink-2') },
    height: narrow ? 360 : 520,
    hovermode: 'closest',
    hoverdistance: 30,
  };
}

/** Plotly axis fields common to every chart axis. */
export const axisStyle = () => ({
  gridcolor: cssVar('--grid'), linecolor: cssVar('--axis'), tickcolor: cssVar('--axis'), ticks: 'outside', zeroline: false,
});

/**
 * Y axis of a Nyquist plot, −Im(Z), scaled equally with the x axis so that arcs keep their shape.
 * Unless `includeTail` is set, the axis starts at 0, which leaves out the inductive tail (negative −Im(Z) at high frequency).
 */
export const nyquistYAxis = (includeTail) => ({
  title: { text: '−Im(Z) (mΩ)', standoff: 8 },
  scaleanchor: 'x', scaleratio: 1,
  ...(includeTail ? {} : { rangemode: 'tozero', autorangeoptions: { minallowed: 0 }, constraintoward: 'bottom' }),
  ...axisStyle(),
});

/** Plotly hover-label style with the given border color. */
export function hoverLabel(borderColor) {
  return { bgcolor: cssVar('--surface'), bordercolor: borderColor, font: { color: cssVar('--ink'), family: FONT, size: 12 } };
}

/** Line color, Plotly dash, direct-label text and label-group key of one cell's series. */
export function seriesStyle(type, cond) {
  const a = AGING[type];
  const colorIndex = a.color.values.findIndex((v) => v.id === a.color.of(cond));
  const dashValue = a.dash ? a.dash.values.find((v) => v.id === a.dash.of(cond)) : null;
  return {
    color: cssVar(seriesVar(colorIndex)),
    dash: dashValue ? dashValue.plotly : 'solid',
    label: a.color.values[colorIndex].short,
    group: `${a.color.of(cond)}/${dashValue ? dashValue.id : ''}`,
  };
}

/** Per color/line-style group, remember the end point of its longest line, for the direct labels. */
export function trackLongest(groups, style, pts) {
  const last = pts[pts.length - 1];
  const longest = groups.get(style.group);
  if (!longest || last.x > longest.last.x) groups.set(style.group, { last, label: style.label });
}

/** Direct labels at the end of each group's longest line; none on narrow screens or when too many groups would crowd the plot. */
export function directLabels(groups, narrow) {
  if (narrow || groups.size < 1 || groups.size > 4) return [];
  const ink2 = cssVar('--ink-2');
  return [...groups.values()].map((g) => ({
    x: g.last.x, y: g.last.y, text: g.label, showarrow: false,
    xanchor: 'left', xshift: 8, font: { size: 11, color: ink2, family: FONT },
  }));
}

/** Draw a chart in the element with the given id (default #chart), first clearing a load-error notice left there by an earlier failure. */
export function drawChart(traces, layout, id = 'chart') {
  const chart = $(id);
  if (chart.querySelector('.notice')) {
    Plotly.purge(chart);
    chart.replaceChildren();
  }
  return Plotly.react(chart, traces, layout, { displayModeBar: false, responsive: true });
}

/** The 1, 2 or 5 times a power of ten that is closest to `raw`. */
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  return (m <= 1.4 ? 1 : m <= 3.5 ? 2 : m <= 7 ? 5 : 10) * p;
}

/** Give both axes of a Nyquist plot the same tick step (about 70 px apart), so the grid is square like the scale. */
function squareTicks(chart) {
  const { xaxis, yaxis } = chart._fullLayout;
  const pxPerUnit = xaxis._length / (xaxis.range[1] - xaxis.range[0]);
  const dtick = niceStep(70 / pxPerUnit);
  return Plotly.relayout(chart, {
    'xaxis.tickmode': 'linear', 'xaxis.tick0': 0, 'xaxis.dtick': dtick,
    'yaxis.tickmode': 'linear', 'yaxis.tick0': 0, 'yaxis.dtick': dtick,
  });
}

/** Draw a Nyquist plot in #chart: like `drawChart`, with the same tick step on both axes, also after the window is resized. */
export async function drawNyquist(traces, layout) {
  const chart = $('chart');
  await drawChart(traces, layout);
  await squareTicks(chart);
  if (!chart.dataset.squareTicks) {
    chart.dataset.squareTicks = '1';
    let timer;
    window.addEventListener('resize', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (chart.layout?.yaxis?.scaleanchor === 'x') squareTicks(chart); }, 250);
    });
  }
}

// ---------------------------------------------------------------------------
// Tables
// ---------------------------------------------------------------------------

/** Fill `table` with a header row and body rows of text; columns after the first are right-aligned. */
export function fillTable(table, head, rows) {
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  head.forEach((h, i) => {
    const th = document.createElement('th');
    th.textContent = h;
    th.scope = 'col';
    if (i > 0) th.className = 'num';
    hr.append(th);
  });
  thead.append(hr);

  const tbody = document.createElement('tbody');
  for (const values of rows) {
    const tr = document.createElement('tr');
    values.forEach((v, i) => {
      const td = document.createElement('td');
      td.textContent = v;
      if (i > 0) td.className = 'num';
      tr.append(td);
    });
    tbody.append(tr);
  }
  table.replaceChildren(thead, tbody);
}
