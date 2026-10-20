"use strict";

(() => {
  const root = document.documentElement;
  const book = document.querySelector("#book");
  const downloads = document.querySelector("#downloads");
  const cover = document.querySelector(".cover-front");
  const opener = document.querySelector(".cover-opener");
  const caption = document.querySelector(".open-label");
  const closeBook = document.querySelector(".close-book");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let focusTimer;
  let sectionIndex = 0;

  function setOpen(open, focus = true) {
    clearTimeout(focusTimer);
    book.classList.toggle("is-open", open);
    downloads.inert = !open;
    downloads.setAttribute("aria-hidden", String(!open));
    cover.inert = open;
    opener.setAttribute("aria-expanded", String(open));
    caption.firstChild.textContent = open ? "Close the book " : "Open the book ";
    caption.classList.toggle("is-open", open);
    if (focus) {
      if (!open) opener.focus({ preventScroll: true });
      else focusTimer = setTimeout(() => downloads.querySelector("a").focus({ preventScroll: true }),
        reducedMotion.matches ? 0 : 1100);
    }
  }
  opener.addEventListener("click", () => setOpen(true));
  closeBook.addEventListener("click", () => setOpen(false));
  caption.addEventListener("click", () => setOpen(!book.classList.contains("is-open")));
  root.classList.add("is-enhanced");
  for (const element of [caption, closeBook, document.querySelector(".page-utilities")]) element.hidden = false;
  setOpen(location.hash === "#downloads", false);

  const destinations = [
    { id: "downloads", label: "01 Downloads", key: "Alt+1" },
    { id: "editions", label: "02 Other editions", key: "Alt+2" },
    { id: "about", label: "03 About & help", key: "Alt+3" },
  ];
  function jump(index) {
    sectionIndex = index;
    const destination = document.getElementById(destinations[index].id);
    if (index === 0) setOpen(true);
    else {
      destination.open = true;
      destination.querySelector("summary").focus({ preventScroll: true });
    }
    history.replaceState(null, "", "#" + destination.id);
    destination.scrollIntoView({ behavior: reducedMotion.matches ? "instant" : "smooth", block: "center" });
  }
  document.querySelectorAll('a[href="#downloads"]').forEach(link =>
    link.addEventListener("click", event => { event.preventDefault(); jump(0); }));
  addEventListener("hashchange", () => {
    const index = destinations.findIndex(item => "#" + item.id === location.hash);
    if (index >= 0) jump(index);
  });
  addEventListener("scroll", () => {
    for (let index = 0; index < destinations.length; index++) {
      if (document.getElementById(destinations[index].id).getBoundingClientRect().top <= 140) sectionIndex = index;
    }
  }, { passive: true });

  const themeButton = document.querySelector(".theme-toggle");
  function setTheme(theme) {
    root.dataset.theme = theme;
    const label = `Switch to ${theme === "dark" ? "light" : "dark"} theme`;
    themeButton.setAttribute("aria-label", label);
    themeButton.dataset.tooltip = label;
    themeButton.querySelector("use").setAttribute("href", theme === "dark" ? "#icon-sun" : "#icon-moon");
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#080b0d" : "#ecefec";
    try { localStorage.setItem("grade-book-theme", theme); } catch { /* Storage may be disabled. */ }
  }
  let savedTheme;
  try { savedTheme = localStorage.getItem("grade-book-theme"); } catch { /* Use light paper by default. */ }
  setTheme(savedTheme === "dark" ? "dark" : "light");
  const toggleTheme = () => setTheme(root.dataset.theme === "dark" ? "light" : "dark");
  themeButton.addEventListener("click", toggleTheme);

  const palette = document.querySelector("#spotlight");
  const query = document.querySelector("#command-query");
  const results = document.querySelector("#command-results");
  const launcher = document.querySelector(".search-launcher");
  const shortcuts = document.querySelector("#shortcuts");
  const isApple = /Mac|iPhone|iPad/.test(navigator.platform);
  let mode = "commands";
  let filtered = [];
  let active = 0;
  const invokers = new WeakMap();
  function openDialog(dialog, invoker = document.activeElement) {
    invokers.set(dialog, invoker);
    dialog.showModal();
  }
  document.querySelectorAll("dialog").forEach(dialog => {
    dialog.addEventListener("click", event => {
      // The dialog's padding is content too; only an actual backdrop click closes.
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right
          || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    dialog.addEventListener("close", () => invokers.get(dialog)?.focus({ preventScroll: true }));
    dialog.querySelectorAll("[data-close-dialog]").forEach(button =>
      button.addEventListener("click", () => dialog.close()));
  });
  function keycaps(shortcut) {
    const group = document.createElement("span");
    group.className = "key-group";
    group.setAttribute("aria-hidden", "true");
    shortcut.split("+").forEach(key => {
      const cap = document.createElement("kbd");
      cap.textContent = key === "Alt" && isApple ? "⌥" : key;
      group.append(cap);
    });
    return group;
  }
  const commands = [
    { label: "Jump to…", key: "Alt+j", run: () => showPalette("destinations") },
    { label: "Download PDF", key: "Alt+Shift+p", run: () => document.querySelector('[data-format="pdf"]').click() },
    { label: "Toggle theme", key: "Alt+Shift+t", run: toggleTheme },
    { label: "Keyboard shortcuts", key: "Alt+/", run: () => openDialog(shortcuts, launcher) },
  ];
  const allShortcuts = [
    ["Search and commands", "Ctrl+K / Command+K / Ctrl+Space"],
    ...commands.map(command => [command.label, command.key]),
    ...destinations.map(destination => [destination.label, destination.key]),
    ["Previous section", "Alt+ArrowUp"], ["Next section", "Alt+ArrowDown"],
    ["Open or close book", "Enter / Space"], ["Close book or dialog", "Escape"],
  ];
  for (const [label, keys] of allShortcuts) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    keys.split(" / ").forEach((shortcut, index) => {
      if (index) description.append(" / ");
      description.append(keycaps(shortcut));
    });
    row.append(term, description);
    shortcuts.querySelector("dl").append(row);
  }
  function activate(index) {
    const item = filtered[index];
    if (!item) return;
    if (item.label === "Jump to…") { item.run(); return; }
    palette.close();
    // Wait for native close/focus restoration before opening another task.
    setTimeout(item.run, 0);
  }
  function select(index) {
    active = Math.max(0, Math.min(index, filtered.length - 1));
    results.querySelectorAll('[role="option"]').forEach((row, i) =>
      row.setAttribute("aria-selected", String(i === active)));
    if (filtered.length) {
      query.setAttribute("aria-activedescendant", "command-" + active);
      results.children[active].scrollIntoView({ block: "nearest" });
    } else query.removeAttribute("aria-activedescendant");
  }
  function renderCommands() {
    const items = mode === "commands" ? commands : destinations.map((item, index) =>
      ({ ...item, run: () => jump(index) }));
    filtered = items.filter(item => item.label.toLowerCase().includes(query.value.trim().toLowerCase()));
    results.replaceChildren();
    filtered.forEach((item, index) => {
      const row = document.createElement("div");
      row.id = "command-" + index;
      row.className = "command-option";
      row.setAttribute("role", "option");
      row.setAttribute("aria-keyshortcuts", item.key);
      const label = document.createElement("span");
      label.textContent = item.label;
      row.append(label, keycaps(item.key));
      row.addEventListener("pointermove", () => select(index));
      row.addEventListener("mousedown", event => event.preventDefault());
      row.addEventListener("click", () => activate(index));
      results.append(row);
    });
    const empty = palette.querySelector(".empty-results");
    empty.hidden = filtered.length > 0;
    empty.textContent = mode === "commands" ? "No matching commands" : "No matching components";
    select(0);
  }
  function showPalette(nextMode = "commands") {
    mode = nextMode;
    query.value = "";
    query.placeholder = mode === "commands" ? "Search commands" : "Jump to a component";
    document.querySelector("#search-label").textContent = query.placeholder;
    results.setAttribute("aria-label", mode === "commands" ? "Commands" : "Components");
    if (!palette.open) openDialog(palette, launcher);
    query.focus();
    renderCommands();
  }
  launcher.addEventListener("click", () => showPalette());
  query.addEventListener("input", renderCommands);
  query.addEventListener("keydown", event => {
    if (["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) event.preventDefault();
    if (event.key === "ArrowDown") select(active + 1);
    if (event.key === "ArrowUp") select(active - 1);
    if (event.key === "Enter") activate(active);
  });
  document.addEventListener("keydown", event => {
    const editing = event.target.matches("input, textarea, select, [contenteditable='true']");
    if ((editing && event.target !== query) || shortcuts.open) return;
    if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")
        || (event.ctrlKey && event.code === "Space")) {
      event.preventDefault(); showPalette(); return;
    }
    if (event.key === "Escape" && !palette.open && book.classList.contains("is-open")) {
      setOpen(false); return;
    }
    if (!event.altKey) return;
    let action;
    const key = event.key.toLowerCase();
    if (key === "j") action = () => showPalette("destinations");
    if (event.shiftKey && key === "p") action = commands[1].run;
    if (event.shiftKey && key === "t") action = toggleTheme;
    if (key === "/") action = commands[3].run;
    if (/^[1-3]$/.test(key)) action = () => jump(Number(key) - 1);
    if (key === "arrowup") action = () => jump(Math.max(0, sectionIndex - 1));
    if (key === "arrowdown") action = () => jump(Math.min(2, sectionIndex + 1));
    if (action) {
      event.preventDefault();
      if (palette.open && key !== "j") { palette.close(); setTimeout(action, 0); }
      else action();
    }
  });

  const tooltip = document.querySelector("#tooltip");
  let tooltipTimer, tooltipTarget;
  function hideTooltip() {
    clearTimeout(tooltipTimer);
    tooltip.hidden = true;
    tooltipTarget?.removeAttribute("aria-describedby");
    tooltipTarget = null;
  }
  function positionTooltip() {
    if (!tooltipTarget) return;
    const rect = tooltipTarget.getBoundingClientRect();
    const bounds = tooltip.getBoundingClientRect();
    tooltip.style.left = Math.max(12, Math.min(innerWidth - bounds.width - 12,
      rect.left + rect.width / 2 - bounds.width / 2)) + "px";
    tooltip.style.top = (rect.top >= bounds.height + 20
      ? rect.top - bounds.height - 8 : rect.bottom + 8) + "px";
  }
  document.querySelectorAll("[data-tooltip]").forEach(button => {
    const show = () => {
      hideTooltip();
      tooltipTimer = setTimeout(() => {
        tooltipTarget = button;
        tooltip.textContent = button.dataset.tooltip;
        // Tooltips inside a modal must be in that modal's top layer.
        (button.closest("dialog") || document.body).append(tooltip);
        tooltip.hidden = false;
        button.setAttribute("aria-describedby", "tooltip");
        positionTooltip();
      }, 150);
    };
    button.addEventListener("pointerenter", show);
    button.addEventListener("focus", show);
    button.addEventListener("pointerleave", hideTooltip);
    button.addEventListener("blur", hideTooltip);
    button.addEventListener("click", hideTooltip);
  });
  addEventListener("resize", positionTooltip);
  addEventListener("scroll", positionTooltip, { passive: true });
  visualViewport?.addEventListener("resize", positionTooltip);
  document.addEventListener("keydown", event => { if (event.key === "Escape") hideTooltip(); });
})();
