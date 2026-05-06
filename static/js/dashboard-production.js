(function () {
  const storage = {
    get(key, fallback) {
      try {
        return localStorage.getItem(key) || fallback;
      } catch (error) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, value);
      } catch (error) {
        return undefined;
      }
    }
  };

  const normalize = (value) => (value || '').toString().trim().toLowerCase();

  function syncPlotlyTheme(theme) {
    if (!window.Plotly) return;
    const isDark = theme === 'dark';
    const textColor = isDark ? '#e5edf7' : '#111827';
    const mutedColor = isDark ? '#97a6ba' : '#697586';
    const gridColor = isDark ? '#1f2a3d' : '#eef2f7';
    const lineColor = isDark ? '#263449' : '#dbe4f0';
    const hoverBg = isDark ? '#111827' : '#ffffff';

    document.querySelectorAll('.js-plotly-plot').forEach((chart) => {
      Plotly.relayout(chart, {
        'font.color': textColor,
        'title.font.color': textColor,
        'xaxis.color': mutedColor,
        'xaxis.linecolor': lineColor,
        'xaxis.gridcolor': gridColor,
        'xaxis.title.font.color': mutedColor,
        'yaxis.color': mutedColor,
        'yaxis.linecolor': lineColor,
        'yaxis.gridcolor': gridColor,
        'yaxis.title.font.color': mutedColor,
        'legend.font.color': mutedColor,
        'hoverlabel.bgcolor': hoverBg,
        'hoverlabel.bordercolor': lineColor,
        'hoverlabel.font.color': textColor,
        'paper_bgcolor': 'rgba(0,0,0,0)',
        'plot_bgcolor': 'rgba(0,0,0,0)'
      });
    });
  }

  window.MarketPulseSyncCharts = function () {
    syncPlotlyTheme(document.body.dataset.theme || 'light');
  };

  function applyTheme(theme) {
    document.body.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle] i').forEach((icon) => {
      icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    });
    storage.set('marketpulse-theme', theme);
    setTimeout(() => syncPlotlyTheme(theme), 40);
  }

  function setupThemeToggle() {
    const saved = storage.get('marketpulse-theme', '');
    const preferred = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    applyTheme(saved || preferred);
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.addEventListener('click', () => {
        applyTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');
      });
    });
  }

  function setupSidebarToggle() {
    const shell = document.querySelector('.app-shell');
    const toggle = document.querySelector('[data-sidebar-toggle]');
    if (!shell) return;
    shell.classList.remove('sidebar-collapsed');
    storage.set('marketpulse-sidebar', 'expanded');
    if (toggle) toggle.hidden = true;
  }

  function setupCommandPalette() {
    const modalEl = document.getElementById('commandPalette');
    const search = document.getElementById('commandSearch');
    const list = document.getElementById('commandList');
    if (!modalEl || !search || !list || !window.bootstrap) return;

    const modal = new bootstrap.Modal(modalEl);
    const links = Array.from(document.querySelectorAll('.side-nav .nav-link')).map((link) => ({
      label: link.textContent.trim(),
      icon: link.querySelector('i') ? link.querySelector('i').className : 'fas fa-circle',
      href: link.href
    }));
    const actions = [
      { label: 'Export current report', icon: 'fas fa-download', href: document.querySelector('a[href*="export.csv"]')?.href },
      { label: 'Print dashboard', icon: 'fas fa-print', run: () => window.print() },
      { label: 'Toggle dark mode', icon: 'fas fa-moon', run: () => document.querySelector('[data-theme-toggle]')?.click() }
    ].filter((item) => item.href || item.run);
    const items = [...links, ...actions];

    function render() {
      const query = normalize(search.value);
      const filtered = items.filter((item) => normalize(item.label).includes(query));
      list.innerHTML = filtered.length ? '' : '<div class="command-empty">No matching action</div>';
      filtered.forEach((item) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'command-item';
        button.innerHTML = `<i class="${item.icon}"></i><span>${item.label}</span>`;
        button.addEventListener('click', () => {
          modal.hide();
          if (item.href) window.location.href = item.href;
          if (item.run) item.run();
        });
        list.appendChild(button);
      });
    }

    document.querySelectorAll('[data-command-open]').forEach((button) => {
      button.addEventListener('click', () => {
        render();
        modal.show();
        setTimeout(() => search.focus(), 160);
      });
    });

    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        render();
        modal.show();
        setTimeout(() => search.focus(), 160);
      }
    });
    search.addEventListener('input', render);
    modalEl.addEventListener('shown.bs.modal', render);
  }

  function getCellValue(row, index) {
    return row.children[index] ? row.children[index].innerText.trim() : '';
  }

  function parseSortValue(value) {
    const numeric = Number(value.replace(/[$,%\s,]/g, ''));
    return Number.isNaN(numeric) ? normalize(value) : numeric;
  }

  function tableToCsv(table) {
    const rows = Array.from(table.querySelectorAll('tr')).filter((row) => row.style.display !== 'none');
    return rows.map((row) => Array.from(row.children).map((cell) => {
      const text = cell.innerText.replace(/\s+/g, ' ').trim().replace(/"/g, '""');
      return `"${text}"`;
    }).join(',')).join('\n');
  }

  function downloadCsv(filename, contents) {
    const blob = new Blob([contents], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function printElement(element) {
    const target = element.closest('.card') || element.closest('.chart-container') || element.closest('.table-responsive') || element;
    document.body.classList.add('print-scoped');
    target.classList.add('print-target');

    const cleanup = () => {
      document.body.classList.remove('print-scoped');
      target.classList.remove('print-target');
      window.removeEventListener('afterprint', cleanup);
    };

    window.addEventListener('afterprint', cleanup);
    window.print();
    setTimeout(cleanup, 1200);
  }

  function enhanceTables() {
    document.querySelectorAll('.table').forEach((table, index) => {
      if (table.dataset.enhanced === 'true') return;
      table.dataset.enhanced = 'true';
      table.classList.add('production-table');

      const tbody = table.querySelector('tbody');
      const headers = Array.from(table.querySelectorAll('thead th'));
      const rows = tbody ? Array.from(tbody.querySelectorAll('tr')) : [];

      if (!rows.length && tbody) {
        const colSpan = Math.max(headers.length, 1);
        tbody.innerHTML = `<tr><td colspan="${colSpan}" class="table-empty">No records available for this view.</td></tr>`;
        return;
      }

      headers.forEach((header, columnIndex) => {
        header.tabIndex = 0;
        header.classList.add('sortable-column');
        header.addEventListener('click', () => sortTable(table, columnIndex));
        header.addEventListener('keydown', (event) => {
          if (event.key === 'Enter') sortTable(table, columnIndex);
        });
      });

      if (rows.length < 6) return;

      const toolbar = document.createElement('div');
      toolbar.className = 'table-toolbar';
      const searchEnabled = table.dataset.tableSearch !== 'false';
      toolbar.innerHTML = `
        ${searchEnabled ? `
        <label class="table-search">
          <i class="fas fa-magnifying-glass"></i>
          <input type="search" placeholder="Search table">
        </label>
        ` : '<div></div>'}
        <div class="table-actions">
          <button class="btn btn-outline-primary btn-sm" type="button" data-table-export><i class="fas fa-file-csv me-1"></i>CSV</button>
          <button class="btn btn-outline-primary btn-sm" type="button" data-table-print><i class="fas fa-print me-1"></i>Print</button>
        </div>
      `;
      const host = table.closest('.table-responsive') || table;
      host.parentNode.insertBefore(toolbar, host);

      const input = toolbar.querySelector('input');
      if (input) {
        input.addEventListener('input', () => {
          const query = normalize(input.value);
          rows.forEach((row) => {
            row.style.display = normalize(row.innerText).includes(query) ? '' : 'none';
          });
        });
      }

      toolbar.querySelector('[data-table-export]').addEventListener('click', () => {
        downloadCsv(`marketpulse-table-${index + 1}.csv`, tableToCsv(table));
      });
      toolbar.querySelector('[data-table-print]').addEventListener('click', () => printElement(toolbar));
    });
  }

  function sortTable(table, columnIndex) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr')).filter((row) => !row.querySelector('.table-empty'));
    const current = table.dataset.sortColumn === String(columnIndex) ? table.dataset.sortDirection : 'desc';
    const direction = current === 'asc' ? 'desc' : 'asc';

    rows.sort((a, b) => {
      const valueA = parseSortValue(getCellValue(a, columnIndex));
      const valueB = parseSortValue(getCellValue(b, columnIndex));
      if (valueA > valueB) return direction === 'asc' ? 1 : -1;
      if (valueA < valueB) return direction === 'asc' ? -1 : 1;
      return 0;
    });

    table.dataset.sortColumn = String(columnIndex);
    table.dataset.sortDirection = direction;
    rows.forEach((row) => tbody.appendChild(row));
  }

  function setupPageState() {
    document.body.classList.add('dashboard-ready');
    document.querySelectorAll('.chart-container').forEach((chart) => {
      chart.setAttribute('aria-live', 'polite');
    });
  }

  function setupResponsiveCharts() {
    if (!window.ResizeObserver) return;

    const resizeChart = (chart) => {
      if (!window.Plotly || !chart || !chart.classList.contains('js-plotly-plot')) return;
      window.requestAnimationFrame(() => Plotly.Plots.resize(chart));
    };

    const observer = new ResizeObserver((entries) => {
      entries.forEach((entry) => {
        entry.target.querySelectorAll('.js-plotly-plot').forEach(resizeChart);
      });
    });

    document.querySelectorAll('.chart-container').forEach((container) => observer.observe(container));
    window.addEventListener('resize', () => {
      document.querySelectorAll('.js-plotly-plot').forEach(resizeChart);
    }, { passive: true });
  }

  function setupPasswordToggles() {
    document.querySelectorAll('[data-password-toggle]').forEach((button) => {
      const field = button.closest('.password-field');
      const input = field ? field.querySelector('input') : null;
      const icon = button.querySelector('i');
      if (!input) return;

      button.addEventListener('click', () => {
        const shouldShow = input.type === 'password';
        input.type = shouldShow ? 'text' : 'password';
        button.setAttribute('aria-label', shouldShow ? 'Hide password' : 'Show password');
        button.setAttribute('title', shouldShow ? 'Hide password' : 'Show password');
        if (icon) {
          icon.className = shouldShow ? 'fas fa-eye-slash' : 'fas fa-eye';
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupThemeToggle();
    setupSidebarToggle();
    setupCommandPalette();
    enhanceTables();
    setupPageState();
    setupResponsiveCharts();
    setupPasswordToggles();
  });
})();
