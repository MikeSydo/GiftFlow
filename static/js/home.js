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

  // Login / signup toast (placeholder)
  function showToast(msg) {
    // Remove any existing toast
    const old = document.querySelector(".gf-toast");
    if (old) old.remove();

    const toast = document.createElement("div");
    toast.className = "gf-toast";
    toast.style.cssText = `
      position: fixed;
      bottom: 32px;
      left: 50%;
      transform: translateX(-50%) translateY(0);
      background: #2c2420;
      color: #fff;
      padding: 12px 24px;
      border-radius: 999px;
      font-size: 14px;
      font-weight: 500;
      font-family: 'Inter', sans-serif;
      box-shadow: 0 6px 24px rgba(44,36,32,0.25);
      z-index: 9999;
      pointer-events: none;
      animation: toastIn .3s ease forwards;
      white-space: nowrap;
    `;
    toast.textContent = msg;
    document.body.appendChild(toast);

    // Inject keyframes if not yet
    if (!document.getElementById("gf-toast-styles")) {
      const s = document.createElement("style");
      s.id = "gf-toast-styles";
      s.textContent = `
        @keyframes toastIn { from { opacity:0; transform: translateX(-50%) translateY(12px); } to { opacity:1; transform: translateX(-50%) translateY(0); } }
        @keyframes toastOut { from { opacity:1; transform: translateX(-50%) translateY(0); } to { opacity:0; transform: translateX(-50%) translateY(12px); } }
      `;
      document.head.appendChild(s);
    }

    setTimeout(() => {
      toast.style.animation = "toastOut .3s ease forwards";
      setTimeout(() => toast.remove(), 320);
    }, 2200);
  }

  // Attach to all login/signup triggers
  ["btnLogin", "btnSignup", "footerLogin", "footerSignup"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("click", () => {
      const isLogin = id.toLowerCase().includes("login");
      showToast(
        isLogin
          ? "Авторизація поки не доступна — модель User в розробці"
          : "Реєстрація поки не доступна — модель User в розробці"
      );
    });
  });
})();