(function () {
  "use strict";

  // Header: scroll shadow
  const header = document.querySelector(".site-header");
  if (header) {
    let ticking = false;
    window.addEventListener("scroll", () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          header.classList.toggle("site-header--scrolled", window.scrollY > 20);
          ticking = false;
        });
        ticking = true;
      }
    });
  }

  // Catalog dropdown
  const catalogToggle = document.getElementById("catalogToggle");
  const catalogDropdown = document.getElementById("catalogDropdown");
  const catalogTabs = document.getElementById("catalogTabs");
  const catalogPanels = document.getElementById("catalogPanels");

  if (catalogToggle && catalogDropdown) {
    let dropdownOpen = false;

    function openDropdown() {
      dropdownOpen = true;
      catalogDropdown.classList.add("catalog-dropdown--open");
      catalogToggle.classList.add("btn-catalog--open");
    }
    function closeDropdown() {
      dropdownOpen = false;
      catalogDropdown.classList.remove("catalog-dropdown--open");
      catalogToggle.classList.remove("btn-catalog--open");
    }

    catalogToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdownOpen ? closeDropdown() : openDropdown();
    });

    // Close on outside click
    document.addEventListener("click", (e) => {
      if (dropdownOpen && !catalogDropdown.contains(e.target)) {
        closeDropdown();
      }
    });

    // Close on Escape
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && dropdownOpen) closeDropdown();
    });

    // Tab switching inside dropdown
    if (catalogTabs) {
      catalogTabs.addEventListener("click", (e) => {
        const tab = e.target.closest(".catalog-tab");
        if (!tab) return;

        const idx = tab.dataset.tab;
        if (!idx) return;
        e.preventDefault();

        // tabs
        catalogTabs.querySelectorAll(".catalog-tab").forEach((t) => {
          t.classList.toggle("catalog-tab--active", t.dataset.tab === idx);
        });

        // panels
        if (catalogPanels) {
          catalogPanels.querySelectorAll(".catalog-panel").forEach((p) => {
            p.classList.toggle("catalog-panel--active", p.dataset.panel === idx);
          });
        }
      });
    }
  }

})();
