(function () {
  "use strict";

  // State
  const state = {
    currentStep: 1,
    category: null,
    occasions: [],
    gender: "",
    age: 25,
    budgetMin: null,
    budgetMax: null,
    interests: [],
    relationships: [],
  };

  // DOM REFS
  const panels = {
    1: document.getElementById("step1"),
    2: document.getElementById("step2"),
    3: document.getElementById("step3"),
    4: document.getElementById("step4"),
  };

  const progressFill = document.getElementById("progressFill");
  const stepIndicators = document.querySelectorAll(".wizard-step");

  const ageSlider = document.getElementById("ageSlider");
  const ageValue = document.getElementById("ageValue");
  const budgetMin = document.getElementById("budgetMin");
  const budgetMax = document.getElementById("budgetMax");

  const resultsContainer = document.getElementById("resultsContainer");
  const resultsCount = document.getElementById("resultsCount");

  // Navigation
  function goToStep(n) {
    if (panels[state.currentStep]) panels[state.currentStep].classList.remove("wizard-panel--active");
    state.currentStep = n;
    if (panels[n]) panels[n].classList.add("wizard-panel--active");
    updateProgress();

    // If entering step 4 → fire search
    if (n === 4) fireSearch();
  }

  function updateProgress() {
    const pct = (state.currentStep / 4) * 100;
    if (progressFill) progressFill.style.width = pct + "%";

    stepIndicators.forEach((el) => {
      const s = parseInt(el.dataset.step, 10);
      el.classList.toggle("wizard-step--active", s === state.currentStep);
      el.classList.toggle("wizard-step--done", s < state.currentStep);
    });
  }

  // Wire next / back buttons
  document.getElementById("next1")?.addEventListener("click", () => goToStep(2));
  document.getElementById("back2")?.addEventListener("click", () => goToStep(1));
  document.getElementById("next2")?.addEventListener("click", () => goToStep(3));
  document.getElementById("back3")?.addEventListener("click", () => goToStep(2));
  document.getElementById("back4")?.addEventListener("click", () => goToStep(3));
  document.getElementById("searchBtn")?.addEventListener("click", () => goToStep(4));

  // Single-select cards (category)
  function initSingleSelect(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (e) => {
      const card = e.target.closest(".wizard-card");
      if (!card) return;

      grid.querySelectorAll(".wizard-card").forEach((c) => c.classList.remove("wizard-card--active"));
      card.classList.add("wizard-card--active");
      state[field] = card.dataset.value;
    });
  }
  initSingleSelect("categoriesGrid", "category");

  // Multi-select cards (occasions)
  function initMultiSelect(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (e) => {
      const card = e.target.closest(".wizard-card");
      if (!card) return;

      const val = card.dataset.value;
      const arr = state[field];
      const idx = arr.indexOf(val);

      if (idx === -1) {
        arr.push(val);
        card.classList.add("wizard-card--active");
      } else {
        arr.splice(idx, 1);
        card.classList.remove("wizard-card--active");
      }
    });
  }
  initMultiSelect("occasionsGrid", "occasions");

  // Multi-select pills (interests, relationships)
  function initPillGroup(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (e) => {
      const pill = e.target.closest(".wizard-pill");
      if (!pill || !pill.dataset.field) return;

      const val = pill.dataset.value;
      const arr = state[field];
      const idx = arr.indexOf(val);

      if (idx === -1) {
        arr.push(val);
        pill.classList.add("wizard-pill--active");
      } else {
        arr.splice(idx, 1);
        pill.classList.remove("wizard-pill--active");
      }
    });
  }
  initPillGroup("interestsGrid", "interests");
  initPillGroup("relationGrid", "relationships");

  // Gender pills (single)
  const genderContainer = document.querySelector(".wizard-field__options--gender");
  if (genderContainer) {
    genderContainer.addEventListener("click", (e) => {
      const pill = e.target.closest(".wizard-pill");
      if (!pill) return;

      genderContainer.querySelectorAll(".wizard-pill").forEach((p) => p.classList.remove("wizard-pill--active"));
      pill.classList.add("wizard-pill--active");
      state.gender = pill.dataset.value;
    });
  }

  // Age slider
  if (ageSlider && ageValue) {
    ageSlider.addEventListener("input", () => {
      state.age = parseInt(ageSlider.value, 10);
      ageValue.textContent = state.age;
    });
  }

  // Budget inputs
  if (budgetMin) {
    budgetMin.addEventListener("input", () => {
      state.budgetMin = budgetMin.value || null;
    });
  }
  if (budgetMax) {
    budgetMax.addEventListener("input", () => {
      state.budgetMax = budgetMax.value || null;
    });
  }

  // Budget presets
  document.querySelectorAll(".wizard-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const min = btn.dataset.min;
      const max = btn.dataset.max;
      if (budgetMin) { budgetMin.value = min; state.budgetMin = min; }
      if (budgetMax) { budgetMax.value = max; state.budgetMax = max; }

      // highlight active preset
      document.querySelectorAll(".wizard-preset").forEach((b) => b.classList.remove("wizard-preset--active"));
      btn.classList.add("wizard-preset--active");
    });
  });

  // Fire search (AJAX)
  function fireSearch() {
    if (!resultsContainer) return;

    // Show loading
    resultsContainer.innerHTML = `
      <div class="results-loading" id="resultsLoading">
        <div class="results-loading__spinner"></div>
        <p>Шукаємо подарунки...</p>
      </div>
    `;
    if (resultsCount) resultsCount.textContent = "Шукаємо...";

    // Build params
    const params = new URLSearchParams();
    if (state.category) params.set("category", state.category);
    if (state.gender) params.set("gender", state.gender);
    if (state.age) params.set("age", state.age);
    if (state.budgetMin) params.set("budget_min", state.budgetMin);
    if (state.budgetMax) params.set("budget_max", state.budgetMax);

    // Merge all tag arrays
    const allTags = [...state.occasions, ...state.interests, ...state.relationships];
    if (allTags.length) params.set("tags", allTags.join(","));

    const url = window.SEARCH_API_URL + "?" + params.toString();

    fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => renderResults(data))
      .catch((err) => {
        console.error("Search failed:", err);
        resultsContainer.innerHTML = `
          <div class="results-empty">
            <div class="results-empty__icon">⚠️</div>
            <h3>Помилка завантаження</h3>
            <p>Будь ласка, попробуйте знову або зміните параметри.</p>
          </div>
        `;
      });
  }

  // Render results
  function renderResults(data) {
    const { results, count } = data;

    if (resultsCount) {
      resultsCount.textContent = count
        ? `Знайдено ${count} подарунок${count === 1 ? "" : count < 5 ? "а" : "ів"}`
        : "Жоден подарунок не знайдений";
    }

    if (!results || !results.length) {
      resultsContainer.innerHTML = `
        <div class="results-empty">
          <div class="results-empty__icon">🎁</div>
          <h3>Нічого не знайдено</h3>
          <p>Попробуйте зlooser параметри або оберіть іншу категорію.</p>
        </div>
      `;
      return;
    }

    const grid = document.createElement("div");
    grid.className = "results-grid";

    results.forEach((gift, i) => {
      const card = document.createElement("article");
      card.className = "result-card";
      card.style.setProperty("--delay", i * 0.06 + "s");
      card.style.animationDelay = i * 0.06 + "s";

      const tagsHtml = gift.tags
        .map((t) => `<span class="result-card__tag">${escHtml(t)}</span>`)
        .join("");

      const imgHtml = gift.image
        ? `<img src="${escHtml(gift.image)}" alt="${escHtml(gift.title)}" />`
        : "";

      card.innerHTML = `
        <div class="result-card__media">${imgHtml}</div>
        <div class="result-card__body">
          ${gift.category ? `<div class="result-card__cat">${escHtml(gift.category)}</div>` : ""}
          <h3 class="result-card__title">${escHtml(gift.title)}</h3>
          ${gift.short_description ? `<p class="result-card__desc">${escHtml(gift.short_description)}</p>` : ""}
          ${tagsHtml ? `<div class="result-card__tags">${tagsHtml}</div>` : ""}
          <div class="result-card__price">від ${gift.min_price} ₴</div>
        </div>
      `;

      grid.appendChild(card);
    });

    resultsContainer.innerHTML = "";
    resultsContainer.appendChild(grid);
  }

  // Utility
  function escHtml(str) {
    const d = document.createElement("div");
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  // Init: check if URL has ?category= (from home page "see all" link)
  (function initFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    const cat = urlParams.get("category");
    if (cat) {
      state.category = cat;
      const cards = document.querySelectorAll("#categoriesGrid .wizard-card");
      cards.forEach((c) => {
        if (c.dataset.value === cat) c.classList.add("wizard-card--active");
      });
    }
  })();

})();