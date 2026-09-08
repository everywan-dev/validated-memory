"""Repository-owned DOM enhancement; the canonical page remains the source."""

SCRIPT = r"""
(() => {
  'use strict';
  const units = Array.from(document.querySelectorAll('section[data-unit]'));
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const controls = make('form', '', 'app-controls');
  controls.setAttribute('aria-label', 'Find knowledge units');
  controls.addEventListener('submit', event => event.preventDefault());
  const searchLabel = make('label', 'Search unit text ');
  const search = make('input');
  search.type = 'search';
  searchLabel.append(search);
  controls.append(searchLabel);
  const filters = ['state', 'evidence', 'verdict'].map(key => {
    const label = make('label', key[0].toUpperCase() + key.slice(1) + ' ');
    const select = make('select');
    const all = make('option', 'All');
    all.value = '';
    select.append(all);
    Array.from(new Set(units.map(unit => unit.dataset[key]))).sort().forEach(value => {
      const option = make('option', value);
      option.value = value;
      select.append(option);
    });
    label.append(select);
    controls.append(label);
    return {key, select};
  });
  const reset = make('button', 'Reset filters');
  reset.type = 'button';
  controls.append(reset);
  const status = make('p');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  controls.append(status);
  const overview = document.querySelector('.overview');
  (overview || document.querySelector('h1')).before(controls);
  controls.append(make('p', 'Overview totals cover the full corpus, not the filtered subset.'));

  // Collect only text owned by this unit. Nested history has its own index.
  const records = units.map(unit => {
    const walker = document.createTreeWalker(unit, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement.closest('section[data-unit]') === unit
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const text = [];
    while (walker.nextNode()) text.push(walker.currentNode.textContent);
    return {unit, text: text.join(' ').toLowerCase()};
  });
  const ancestors = unit => {
    const result = [];
    let parent = unit.parentElement.closest('section[data-unit]');
    while (parent) {
      result.push(parent);
      parent = parent.parentElement.closest('section[data-unit]');
    }
    return result;
  };
  const isFiltered = () => search.value.trim() !== '' || filters.some(f => f.select.value);
  const apply = () => {
    const query = search.value.trim().toLowerCase();
    const matching = records.filter(record => record.text.includes(query) &&
      filters.every(f => !f.select.value || record.unit.dataset[f.key] === f.select.value));
    const visible = new Set(matching.map(record => record.unit));
    matching.forEach(({unit}) => ancestors(unit).forEach(parent => {
      visible.add(parent);
      if (isFiltered()) parent.querySelector(':scope > details').open = true;
    }));
    units.forEach(unit => { unit.hidden = !visible.has(unit); });
    status.textContent = matching.length + ' matching unit(s) of ' + units.length +
      (matching.length === 0 ? '. No results. Reset filters to show all units.' :
        '. Historical matches include their containing cards for context.');
  };
  const clear = () => {
    search.value = '';
    filters.forEach(f => { f.select.value = ''; });
    apply();
  };
  search.addEventListener('input', apply);
  filters.forEach(f => f.select.addEventListener('change', apply));
  reset.addEventListener('click', clear);
  apply();

  const revealFragment = () => {
    let id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); }
    catch { return; }
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    const unit = target.closest('section[data-unit]');
    if (unit && [unit, ...ancestors(unit)].some(card => card.hidden)) {
      clear();
      status.textContent += ' Filters reset to reveal the linked unit.';
    }
    let node = target;
    while (node) {
      if (node.tagName === 'DETAILS') node.open = true;
      node = node.parentElement;
    }
    if (unit) unit.querySelector(':scope > details').open = true;
    target.scrollIntoView({block: 'start'});
  };
  window.addEventListener('hashchange', revealFragment);
  document.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (link && (link.getAttribute('href') || '').startsWith('#')) {
      window.setTimeout(revealFragment, 0);
    }
  });
  revealFragment();

  const diagrams = Array.from(document.querySelectorAll('svg')).map(svg => {
    const original = svg.getAttribute('viewBox');
    const base = original.trim().split(/\s+/).map(Number);
    if (base.length !== 4 || !base.every(Number.isFinite) || base[2] <= 0 || base[3] <= 0) return null;
    let box = base.slice();
    const group = make('div', '', 'diagram-controls');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Diagram controls: ' + svg.getAttribute('aria-label'));
    const clamp = (value, lower, upper) => Math.max(lower, Math.min(upper, value));
    const paint = () => {
      box[2] = clamp(box[2], base[2] / 4, base[2]);
      box[3] = clamp(box[3], base[3] / 4, base[3]);
      box[0] = clamp(box[0], base[0], base[0] + base[2] - box[2]);
      box[1] = clamp(box[1], base[1], base[1] + base[3] - box[3]);
      svg.setAttribute('viewBox', box.join(' '));
    };
    const zoom = factor => {
      const width = clamp(box[2] * factor, base[2] / 4, base[2]);
      const height = clamp(box[3] * factor, base[3] / 4, base[3]);
      box[0] += (box[2] - width) / 2;
      box[1] += (box[3] - height) / 2;
      box[2] = width;
      box[3] = height;
    };
    const actions = [
      ['Zoom in', () => zoom(0.8)], ['Zoom out', () => zoom(1.25)],
      ['Pan left', () => { box[0] -= box[2] / 5; }],
      ['Pan right', () => { box[0] += box[2] / 5; }],
      ['Pan up', () => { box[1] -= box[3] / 5; }],
      ['Pan down', () => { box[1] += box[3] / 5; }],
      ['Reset diagram', () => { box = base.slice(); }]
    ];
    actions.forEach(([label, action]) => {
      const button = make('button', label);
      button.type = 'button';
      button.addEventListener('click', () => { action(); paint(); });
      group.append(button);
    });
    svg.before(group);
    return {svg, original};
  }).filter(Boolean);

  let printState = null;
  window.addEventListener('beforeprint', () => {
    if (printState) return;
    printState = {
      units: units.map(unit => [unit, unit.hidden]),
      details: Array.from(document.querySelectorAll('details')).map(node => [node, node.open]),
      diagrams: diagrams.map(({svg}) => [svg, svg.getAttribute('viewBox')])
    };
    units.forEach(unit => { unit.hidden = false; });
    printState.details.forEach(([node]) => { node.open = true; });
    diagrams.forEach(({svg, original}) => svg.setAttribute('viewBox', original));
  });
  window.addEventListener('afterprint', () => {
    if (!printState) return;
    printState.units.forEach(([unit, hidden]) => { unit.hidden = hidden; });
    printState.details.forEach(([node, open]) => { node.open = open; });
    printState.diagrams.forEach(([svg, box]) => svg.setAttribute('viewBox', box));
    printState = null;
  });
})();
"""


def enhance(canonical_page: str) -> str:
    """Insert one fixed script; removing that element recovers the exact input."""
    return canonical_page.replace("</body>", "<script>" + SCRIPT + "</script></body>", 1)
