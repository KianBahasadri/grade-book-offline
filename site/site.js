"use strict";

(() => {
  const root = document.documentElement;
  const book = document.querySelector("#book");
  const stage = document.querySelector(".book-stage");
  const downloads = document.querySelector("#downloads");
  const cover = document.querySelector(".cover-front");
  const opener = document.querySelector(".cover-opener");
  const coverImage = opener.querySelector("img");
  const coverTexture = document.createElement("canvas");
  const coverContext = coverImage.classList.contains("cover-flat")
    ? coverTexture.getContext("2d", { alpha: false }) : null;
  const caption = document.querySelector(".open-label");
  const closeBook = document.querySelector(".close-book");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const support = document.querySelector(".support");
  const supportDropdowns = [...support.querySelectorAll(".support-dropdown")];
  const editions = document.querySelector("#editions");
  const defaultView = { x: 5, y: -22 };
  let rotation = { ...defaultView };
  let zoom = 1;
  let requestedZoom = 1;
  let gesture = null;
  let spinFrame = 0;
  let suppressClick = false;
  let focusTimer;

  function drawCover() {
    if (!coverContext || !coverImage.complete || !coverImage.naturalWidth) return;
    // Prefilter the large original before the 3D projection. Directly shrinking
    // it on the GPU aliases fine type and the barcode at oblique angles.
    const width = opener.clientWidth * zoom;
    const height = opener.clientHeight * zoom;
    if (!width || !height) return;
    const density = Math.min((devicePixelRatio || 1) * 1.5,
      coverImage.naturalWidth / width, coverImage.naturalHeight / height);
    const pixelsWide = Math.min(coverImage.naturalWidth, Math.ceil(width * density));
    const pixelsHigh = Math.min(coverImage.naturalHeight, Math.ceil(height * density));
    if (coverTexture.width === pixelsWide && coverTexture.height === pixelsHigh) return;
    coverTexture.width = pixelsWide;
    coverTexture.height = pixelsHigh;
    coverContext.imageSmoothingEnabled = true;
    coverContext.imageSmoothingQuality = "high";
    coverContext.drawImage(coverImage, 0, 0, pixelsWide, pixelsHigh);
    if (!coverTexture.isConnected) {
      coverTexture.className = "cover-texture";
      coverTexture.setAttribute("aria-hidden", "true");
      opener.append(coverTexture);
      coverImage.hidden = true;
    }
  }
  coverImage.addEventListener("load", drawCover);

  function drawRotation() {
    rotation.y = ((rotation.y + 180) % 360 + 360) % 360 - 180;
    rotation.x = Math.max(-75, Math.min(75, rotation.x));
    book.style.setProperty("--rotation-x", rotation.x + "deg");
    book.style.setProperty("--rotation-y", rotation.y + "deg");
  }
  function stopSpin() {
    cancelAnimationFrame(spinFrame);
    spinFrame = 0;
    book.classList.remove("is-rotating", "is-dragging");
  }
  function resetRotation() {
    stopSpin();
    rotation = { ...defaultView };
    drawRotation();
  }
  function setZoom(value) {
    requestedZoom = Math.max(.55, Math.min(1.8, value));
    fitZoom();
  }
  function fitZoom() {
    const spread = book.classList.contains("is-open") && innerWidth > 860 ? 2 : 1;
    const maximum = Math.max(.55, stage.clientWidth * .94 / (book.offsetWidth * spread));
    zoom = Math.min(maximum, requestedZoom);
    stage.style.setProperty("--book-scale", zoom);
    // Reserve scrolling room for the model; its sticky caption stays above it.
    stage.style.setProperty("--book-height", book.offsetHeight + "px");
    drawCover();
  }
  function setOpen(open, focus = true) {
    clearTimeout(focusTimer);
    stopSpin();
    gesture = null;
    book.classList.toggle("is-open", open);
    downloads.inert = !open;
    downloads.setAttribute("aria-hidden", String(!open));
    cover.inert = open;
    opener.setAttribute("aria-expanded", String(open));
    caption.firstChild.textContent = open ? "Close the book " : "Open the book ";
    caption.classList.toggle("is-open", open);
    resetRotation();
    fitZoom();
    if (focus) {
      if (!open) opener.focus({ preventScroll: true });
      else focusTimer = setTimeout(() => {
        if (!supportDropdowns.some(dropdown => dropdown.open)) downloads.querySelector("a").focus({ preventScroll: true });
      }, reducedMotion.matches ? 0 : 1100);
    }
  }
  opener.addEventListener("click", () => setOpen(true));
  closeBook.addEventListener("click", () => setOpen(false));
  caption.addEventListener("click", () => setOpen(!book.classList.contains("is-open")));
  root.classList.add("is-enhanced");
  for (const element of [caption, closeBook, document.querySelector(".page-utilities"),
                        document.querySelector(".edition-filter-controls")]) element.hidden = false;
  setOpen(location.hash === "#downloads", false);

  stage.addEventListener("wheel", event => {
    if (event.ctrlKey || event.metaKey || event.deltaY === 0) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1;
    const delta = Math.max(-300, Math.min(300, event.deltaY * unit));
    setZoom(zoom * Math.exp(-delta * .0015));
  }, { passive: false });
  stage.addEventListener("keydown", event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (!["+", "=", "-", "0"].includes(event.key)) return;
    event.preventDefault();
    setZoom(event.key === "0" ? 1 : zoom * (event.key === "-" ? 1 / 1.15 : 1.15));
  });
  addEventListener("resize", fitZoom);
  new ResizeObserver(fitZoom).observe(stage);

  // Keep native click/keyboard activation. Capture only once a real drag starts.
  stage.addEventListener("pointerdown", event => {
    if (event.button !== 0 || !event.isPrimary || event.pointerType === "touch"
        || book.classList.contains("is-open")) return;
    stopSpin();
    gesture = { id: event.pointerId, startX: event.clientX, startY: event.clientY,
      lastX: event.clientX, lastY: event.clientY, time: event.timeStamp,
      initial: { ...rotation }, dragging: false, vx: 0, vy: 0 };
  });
  window.addEventListener("pointermove", event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.startX;
    const dy = event.clientY - gesture.startY;
    if (!gesture.dragging && Math.hypot(dx, dy) < 6) return;
    if (!gesture.dragging) {
      gesture.dragging = true;
      stage.setPointerCapture(event.pointerId);
      book.classList.add("is-rotating", "is-dragging");
    }
    event.preventDefault();
    const elapsed = Math.max(8, event.timeStamp - gesture.time);
    gesture.vy = Math.max(-1.2, Math.min(1.2, (event.clientX - gesture.lastX) * .55 / elapsed));
    gesture.vx = Math.max(-.5, Math.min(.5, -(event.clientY - gesture.lastY) * .35 / elapsed));
    rotation = { x: gesture.initial.x - dy * .35, y: gesture.initial.y + dx * .55 };
    gesture.lastX = event.clientX;
    gesture.lastY = event.clientY;
    gesture.time = event.timeStamp;
    drawRotation();
  }, { passive: false });
  function finishDrag(event, cancelled = false) {
    if (!gesture || (event && gesture.id !== event.pointerId)) return;
    const ended = gesture;
    gesture = null;
    if (stage.hasPointerCapture(ended.id)) stage.releasePointerCapture(ended.id);
    book.classList.remove("is-dragging");
    if (!ended.dragging) return;
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 400);
    if (cancelled || reducedMotion.matches || !event || event.timeStamp - ended.time > 100) {
      stopSpin();
      return;
    }
    let previous = performance.now();
    const started = previous;
    function coast(now) {
      const elapsed = Math.min(32, now - previous);
      previous = now;
      ended.vx *= Math.pow(.88, elapsed / 16);
      ended.vy *= Math.pow(.88, elapsed / 16);
      rotation.x += ended.vx * elapsed;
      rotation.y += ended.vy * elapsed;
      drawRotation();
      if (now - started < 700 && Math.abs(ended.vx) + Math.abs(ended.vy) > .015) {
        spinFrame = requestAnimationFrame(coast);
      } else stopSpin();
    }
    spinFrame = requestAnimationFrame(coast);
  }
  window.addEventListener("pointerup", event => finishDrag(event));
  window.addEventListener("pointercancel", event => finishDrag(event, true));
  stage.addEventListener("lostpointercapture", event => finishDrag(event, true));
  window.addEventListener("blur", () => { finishDrag(null, true); stopSpin(); });
  stage.addEventListener("dragstart", event => event.preventDefault());
  stage.addEventListener("click", event => {
    if (suppressClick) {
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressClick = false;
    } else if (!book.classList.contains("is-open") && book.contains(event.target)
        && !event.target.closest("button")) setOpen(true);
  }, true);
  opener.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const directions = { ArrowLeft: [0, -15], ArrowRight: [0, 15], ArrowUp: [-10, 0], ArrowDown: [10, 0] };
    if (event.key === "Home") { event.preventDefault(); resetRotation(); }
    if (directions[event.key]) {
      event.preventDefault();
      stopSpin();
      book.classList.add("is-rotating");
      rotation.x += directions[event.key][0];
      rotation.y += directions[event.key][1];
      drawRotation();
    }
  });
  function showDownloads() {
    setOpen(true);
    downloads.scrollIntoView({ behavior: reducedMotion.matches ? "instant" : "smooth", block: "center" });
  }
  document.querySelectorAll('a[href="#downloads"]').forEach(link =>
    link.addEventListener("click", event => {
      event.preventDefault();
      history.replaceState(null, "", "#downloads");
      showDownloads();
    }));
  addEventListener("hashchange", () => { if (location.hash === "#downloads") showDownloads(); });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const expanded = supportDropdowns.find(dropdown => dropdown.classList.contains("is-expanded"));
    if (expanded) {
      // Handle Escape before the search field consumes it to clear its value.
      event.preventDefault();
      setDropdownOpen(expanded, false);
      expanded.querySelector("summary").focus({ preventScroll: true });
    } else if (book.classList.contains("is-open")) setOpen(false);
  }, true);

  const query = document.querySelector("#edition-query");
  const from = document.querySelector("#edition-from");
  const through = document.querySelector("#edition-to");
  const clearSearch = document.querySelector(".clear-search");
  const clearFilters = document.querySelector(".clear-filters");
  const sortButton = document.querySelector(".sort-editions");
  const count = document.querySelector("#edition-count");
  const list = document.querySelector(".edition-list");
  const normalize = text => text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/g, " ").trim();
  const entries = [...list.children].map(element => ({ element, search: normalize(element.dataset.search),
    date: element.dataset.date, published: element.dataset.published }));
  let newestFirst = true;

  function filterEditions() {
    // Quoted phrases and "build N" remain together; other words may occur anywhere.
    const terms = [...normalize(query.value).matchAll(/"([^"]+)"|(build\s+\d+)|([^\s"]+)/g)]
      .map(match => match[1] || match[2] || match[3]);
    const invalidDates = Boolean(from.value && through.value && from.value > through.value);
    document.querySelector("#date-error").hidden = !invalidDates;
    through.setAttribute("aria-invalid", String(invalidDates));
    clearSearch.hidden = !query.value;
    clearFilters.hidden = !(query.value || from.value || through.value);
    entries.sort((a, b) => (newestFirst ? -1 : 1) * a.published.localeCompare(b.published));
    let visible = 0;
    for (const entry of entries) {
      const matches = !invalidDates && (!from.value || entry.date >= from.value)
        && (!through.value || entry.date <= through.value)
        && terms.every(term => /^(build )?\d+$/.test(term)
          ? new RegExp(`\\b${term}\\b`).test(entry.search) : entry.search.includes(term));
      entry.element.hidden = !matches;
      visible += Number(matches);
      list.append(entry.element);
    }
    count.textContent = `${visible} of ${entries.length} edition${entries.length === 1 ? "" : "s"}`;
    document.querySelector(".no-editions").hidden = visible > 0 || invalidDates;
  }
  for (const input of [query, from, through]) input.addEventListener("input", filterEditions);
  clearSearch.addEventListener("click", () => { query.value = ""; filterEditions(); query.focus(); });
  clearFilters.addEventListener("click", () => {
    query.value = from.value = through.value = "";
    filterEditions();
    query.focus();
  });
  sortButton.addEventListener("click", () => {
    newestFirst = !newestFirst;
    sortButton.textContent = newestFirst ? "Newest first" : "Oldest first";
    filterEditions();
  });
  const dropdownAnimations = new WeakMap();
  function setDropdownOpen(dropdown, expanded, focusSearch = false) {
    if (dropdown.classList.contains("is-expanded") === expanded) return;
    const panel = dropdown.querySelector(".support-panel");
    const startHeight = dropdown.open ? panel.getBoundingClientRect().height : 0;
    const currentStyle = getComputedStyle(panel);
    const startOpacity = dropdown.open ? currentStyle.opacity : 0;
    const startPaddingTop = dropdown.open ? currentStyle.paddingTop : "0px";
    const startPaddingBottom = dropdown.open ? currentStyle.paddingBottom : "0px";
    dropdownAnimations.get(dropdown)?.cancel();
    dropdown.classList.remove("is-animating");
    if (expanded) {
      clearTimeout(focusTimer);
      stopSpin();
      for (const other of supportDropdowns) if (other !== dropdown) setDropdownOpen(other, false);
      dropdown.open = true;
      if (dropdown === editions) filterEditions();
    }
    dropdown.classList.toggle("is-expanded", expanded);
    dropdown.querySelector("summary").setAttribute("aria-expanded", String(expanded));
    panel.inert = !expanded;
    document.body.classList.toggle("support-expanded",
      supportDropdowns.some(item => item.classList.contains("is-expanded")));
    const endHeight = expanded ? panel.getBoundingClientRect().height : 0;
    const finish = () => {
      dropdown.classList.remove("is-animating");
      dropdown.open = expanded;
      dropdownAnimations.delete(dropdown);
      if (expanded && focusSearch && dropdown === editions) query.focus({ preventScroll: true });
    };
    if (reducedMotion.matches) { finish(); return; }
    dropdown.classList.add("is-animating");
    const animation = panel.animate([
      { height: startHeight + "px", opacity: startOpacity, paddingTop: startPaddingTop,
        paddingBottom: startPaddingBottom, transform: expanded ? "translateY(-4px)" : "none" },
      { height: endHeight + "px", opacity: expanded ? 1 : 0, paddingTop: expanded ? "8px" : "0px",
        paddingBottom: expanded ? "16px" : "0px", transform: expanded ? "none" : "translateY(-4px)" },
    ], { duration: 280, easing: "cubic-bezier(.22,.8,.2,1)" });
    dropdownAnimations.set(dropdown, animation);
    animation.onfinish = finish;
  }
  for (const dropdown of supportDropdowns) {
    // Keep native details as the no-JavaScript fallback; animate both directions.
    dropdown.removeAttribute("name");
    dropdown.querySelector(".support-panel").inert = true;
    dropdown.querySelector("summary").setAttribute("aria-expanded", "false");
    dropdown.querySelector("summary").addEventListener("click", event => {
      event.preventDefault();
      setDropdownOpen(dropdown, !dropdown.classList.contains("is-expanded"), event.detail === 0);
    });
  }
  function dismissSupport(event) {
    if (!support.contains(event.target)) {
      for (const dropdown of supportDropdowns) setDropdownOpen(dropdown, false);
    }
  }
  document.addEventListener("pointerdown", dismissSupport);
  document.addEventListener("focusin", dismissSupport);

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
