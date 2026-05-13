(function () {
  "use strict";

  const state = {
    currentStep: 1,
    query: "",
    category: null,
    occasions: [],
    gender: "",
    age: 25,
    budgetMin: null,
    budgetMax: null,
    interests: [],
    relationships: [],
    activeRequestId: 0,
  };

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
  const queryInput = document.getElementById("queryInput");
  const resultsContainer = document.getElementById("resultsContainer");
  const resultsCount = document.getElementById("resultsCount");

  function goToStep(step) {
    if (panels[state.currentStep]) {
      panels[state.currentStep].classList.remove("wizard-panel--active");
    }
    state.currentStep = step;
    if (panels[step]) {
      panels[step].classList.add("wizard-panel--active");
    }
    updateProgress();
    if (step === 4) {
      fireSearch();
    }
  }

  function updateProgress() {
    const percentage = (state.currentStep / 4) * 100;
    if (progressFill) {
      progressFill.style.width = percentage + "%";
    }

    stepIndicators.forEach((element) => {
      const step = parseInt(element.dataset.step, 10);
      element.classList.toggle("wizard-step--active", step === state.currentStep);
      element.classList.toggle("wizard-step--done", step < state.currentStep);
    });
  }

  document.getElementById("next1")?.addEventListener("click", () => goToStep(2));
  document.getElementById("back2")?.addEventListener("click", () => goToStep(1));
  document.getElementById("next2")?.addEventListener("click", () => goToStep(3));
  document.getElementById("back3")?.addEventListener("click", () => goToStep(2));
  document.getElementById("back4")?.addEventListener("click", () => goToStep(3));
  document.getElementById("searchBtn")?.addEventListener("click", () => goToStep(4));

  function initSingleSelect(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (event) => {
      const card = event.target.closest(".wizard-card");
      if (!card) return;

      grid.querySelectorAll(".wizard-card").forEach((node) => {
        node.classList.remove("wizard-card--active");
      });
      card.classList.add("wizard-card--active");
      state[field] = card.dataset.value;
    });
  }

  function initMultiSelect(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (event) => {
      const card = event.target.closest(".wizard-card, .wizard-pill");
      if (!card) return;

      const value = card.dataset.value;
      const values = state[field];
      const index = values.indexOf(value);

      if (index === -1) {
        values.push(value);
        card.classList.add("wizard-card--active", "wizard-pill--active");
      } else {
        values.splice(index, 1);
        card.classList.remove("wizard-card--active", "wizard-pill--active");
      }
    });
  }

  initSingleSelect("categoriesGrid", "category");
  initMultiSelect("occasionsGrid", "occasions");
  initMultiSelect("interestsGrid", "interests");
  initMultiSelect("relationGrid", "relationships");

  const genderContainer = document.querySelector(".wizard-field__options--gender");
  if (genderContainer) {
    genderContainer.addEventListener("click", (event) => {
      const pill = event.target.closest(".wizard-pill");
      if (!pill) return;

      genderContainer.querySelectorAll(".wizard-pill").forEach((node) => {
        node.classList.remove("wizard-pill--active");
      });
      pill.classList.add("wizard-pill--active");
      state.gender = pill.dataset.value;
    });
  }

  if (queryInput) {
    queryInput.addEventListener("input", () => {
      state.query = queryInput.value.trim();
    });
  }

  if (ageSlider && ageValue) {
    ageSlider.addEventListener("input", () => {
      state.age = parseInt(ageSlider.value, 10);
      ageValue.textContent = state.age;
    });
  }

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

  document.querySelectorAll(".wizard-preset").forEach((button) => {
    button.addEventListener("click", () => {
      const min = button.dataset.min;
      const max = button.dataset.max;
      if (budgetMin) {
        budgetMin.value = min;
        state.budgetMin = min;
      }
      if (budgetMax) {
        budgetMax.value = max;
        state.budgetMax = max;
      }

      document.querySelectorAll(".wizard-preset").forEach((node) => {
        node.classList.remove("wizard-preset--active");
      });
      button.classList.add("wizard-preset--active");
    });
  });

  function buildSearchParams() {
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.category) params.set("category", state.category);
    if (state.gender) params.set("gender", state.gender);
    if (state.age) params.set("age", state.age);
    if (state.budgetMin) params.set("budget_min", state.budgetMin);
    if (state.budgetMax) params.set("budget_max", state.budgetMax);

    const allTags = [...state.occasions, ...state.interests, ...state.relationships];
    if (allTags.length) {
      params.set("tags", allTags.join(","));
    }

    return params;
  }

  function renderLoading(message) {
    if (!resultsContainer) return;

    resultsContainer.innerHTML = `
      <div class="results-loading">
        <div class="results-loading__spinner"></div>
        <p>${escHtml(message)}</p>
      </div>
    `;
  }

  function renderFailure(message) {
    if (!resultsContainer) return;

    resultsContainer.innerHTML = `
      <div class="results-empty">
        <div class="results-empty__icon">!</div>
        <h3>Search failed</h3>
        <p>${escHtml(message || "Unable to load results.")}</p>
      </div>
    `;

    if (resultsCount) {
      resultsCount.textContent = "Search failed";
    }
  }

  function fireSearch() {
    if (!resultsContainer) return;

    state.activeRequestId += 1;
    const requestId = state.activeRequestId;
    const params = buildSearchParams();

    renderLoading("Searching gift ideas...");
    if (resultsCount) {
      resultsCount.textContent = "Searching...";
    }

    fetch(window.SEARCH_API_URL + "?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then((data) => {
        if (requestId !== state.activeRequestId) {
          return;
        }
        renderResults(data);
      })
      .catch((error) => {
        if (requestId !== state.activeRequestId) {
          return;
        }
        console.error("Search failed:", error);
        renderFailure("Unable to load search results.");
      });
  }

  function renderResults(data) {
    const results = data.results || [];
    const count = data.count || 0;

    if (resultsCount) {
      resultsCount.textContent = count ? `Found ${count} gift results` : "No gift results found";
    }

    if (!results.length) {
      resultsContainer.innerHTML = `
        <div class="results-empty">
          <div class="results-empty__icon">?</div>
          <h3>No matches</h3>
          <p>Try another query or adjust the filters.</p>
        </div>
      `;
      return;
    }

    const grid = document.createElement("div");
    grid.className = "results-grid";

    results.forEach((gift, index) => {
      const card = document.createElement("article");
      card.className = "result-card";
      card.style.setProperty("--delay", index * 0.06 + "s");
      card.style.animationDelay = index * 0.06 + "s";

      const bestOffer = gift.best_offer || null;
      const tagsHtml = (gift.tags || [])
        .map((tag) => `<span class="result-card__tag">${escHtml(tag)}</span>`)
        .join("");
      const imgHtml = gift.image
        ? `<img src="${escHtml(gift.image)}" alt="${escHtml(gift.title)}" />`
        : "";
      const offerHtml = bestOffer
        ? `
          <div class="result-card__shop">${escHtml(bestOffer.seller_name || bestOffer.shop)}</div>
          <div class="result-card__price-sub">Best offer ${escHtml(bestOffer.price)} UAH</div>
        `
        : `<div class="result-card__price-sub">Offers will appear on the detail page</div>`;

      card.innerHTML = `
        <div class="result-card__media">${imgHtml}</div>
        <div class="result-card__body">
          ${gift.category ? `<div class="result-card__cat">${escHtml(gift.category)}</div>` : ""}
          <h3 class="result-card__title">
            <a href="${escHtml(gift.detail_url)}">${escHtml(gift.title)}</a>
          </h3>
          ${gift.short_description ? `<p class="result-card__desc">${escHtml(gift.short_description)}</p>` : ""}
          ${tagsHtml ? `<div class="result-card__tags">${tagsHtml}</div>` : ""}
          <div class="result-card__price">from ${escHtml(gift.min_price)} UAH</div>
          ${offerHtml}
          <a class="result-card__link" href="${escHtml(gift.detail_url)}">
            View stores
          </a>
        </div>
      `;

      grid.appendChild(card);
    });

    resultsContainer.innerHTML = "";
    resultsContainer.appendChild(grid);
  }

  function escHtml(value) {
    const container = document.createElement("div");
    container.appendChild(document.createTextNode(value == null ? "" : String(value)));
    return container.innerHTML;
  }

  (function initFromURL() {
    const urlParams = new URLSearchParams(window.location.search);
    const category = urlParams.get("category");
    const query = urlParams.get("q");

    if (category) {
      state.category = category;
      document.querySelectorAll("#categoriesGrid .wizard-card").forEach((card) => {
        if (card.dataset.value === category) {
          card.classList.add("wizard-card--active");
        }
      });
    }

    if (query && queryInput) {
      state.query = query;
      queryInput.value = query;
    }
  })();
})();
