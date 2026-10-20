"use strict";

// Each edition is a complete static page. Downloads and the edition archive
// remain usable when JavaScript is unavailable.
const picker = document.querySelector(".edition-picker");
if (picker) {
  picker.hidden = false;
  picker.addEventListener("submit", (event) => {
    event.preventDefault();
    const selected = picker.querySelector("select");
    window.location.assign(selected.value);
  });
}
