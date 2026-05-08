(function () {
  "use strict";

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
      const card = event.target.closest(".wizard-card");
      if (!card) return;

      const value = card.dataset.value;
      const values = state[field];
      const index = values.indexOf(value);

      if (index === -1) {
        values.push(value);
        card.classList.add("wizard-card--active");
      } else {
        values.splice(index, 1);
        card.classList.remove("wizard-card--active");
      }
    });
  }

  function initPillGroup(gridId, field) {
    const grid = document.getElementById(gridId);
    if (!grid) return;

    grid.addEventListener("click", (event) => {
      const pill = event.target.closest(".wizard-pill");
      if (!pill || !pill.dataset.field) return;

      const value = pill.dataset.value;
      const values = state[field];
      const index = values.indexOf(value);

      if (index === -1) {
        values.push(value);
        pill.classList.add("wizard-pill--active");
      } else {
        values.splice(index, 1);
        pill.classList.remove("wizard-pill--active");
      }
    });
  }

  initSingleSelect("categoriesGrid", "category");
  initMultiSelect("occasionsGrid", "occasions");
  initPillGroup("interestsGrid", "interests");
  initPillGroup("relationGrid", "relationships");

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

  function fireSearch() {
    if (!resultsContainer) return;

    resultsContainer.innerHTML = `
      <div class="results-loading" id="resultsLoading">
        <div class="results-loading__spinner"></div>
        <p>Шукаємо подарунки...</p>
      </div>
    `;
    if (resultsCount) {
      resultsCount.textContent = "Шукаємо...";
    }

    const params = new URLSearchParams();
    if (state.category) params.set("category", state.category);
    if (state.gender) params.set("gender", state.gender);
    if (state.age) params.set("age", state.age);
    if (state.budgetMin) params.set("budget_min", state.budgetMin);
    if (state.budgetMax) params.set("budget_max", state.budgetMax);

    const allTags = [...state.occasions, ...state.interests, ...state.relationships];
    if (allTags.length) {
      params.set("tags", allTags.join(","));
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
      .then((data) => renderResults(data))
      .catch((error) => {
        console.error("Search failed:", error);
        resultsContainer.innerHTML = `
          <div class="results-empty">
            <div class="results-empty__icon">⚠️</div>
            <h3>Помилка завантаження</h3>
            <p>Спробуйте ще раз або змініть параметри пошуку.</p>
          </div>
        `;
      });
  }

  function renderResults(data) {
    const { results, count } = data;

    if (resultsCount) {
      resultsCount.textContent = count
        ? `Знайдено ${count} подарунок${count === 1 ? "" : count < 5 ? "а" : "ів"}`
        : "Подарунків не знайдено";
    }

    if (!results || !results.length) {
      resultsContainer.innerHTML = `
        <div class="results-empty">
          <div class="results-empty__icon">🎁</div>
          <h3>Нічого не знайдено</h3>
          <p>Спробуйте змінити параметри або обрати іншу категорію.</p>
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
      const tagsHtml = gift.tags
        .map((tag) => `<span class="result-card__tag">${escHtml(tag)}</span>`)
        .join("");
      const imgHtml = gift.image
        ? `<img src="${escHtml(gift.image)}" alt="${escHtml(gift.title)}" />`
        : "";
      const offerHtml = bestOffer
        ? `
          <div class="result-card__shop">${escHtml(bestOffer.seller_name || bestOffer.shop)}</div>
          <a class="result-card__link" href="${escHtml(bestOffer.product_url)}" target="_blank" rel="noopener noreferrer">
            Купити за ${escHtml(bestOffer.price)} ₴
          </a>
        `
        : "";

      card.innerHTML = `
        <div class="result-card__media">${imgHtml}</div>
        <div class="result-card__body">
          ${gift.category ? `<div class="result-card__cat">${escHtml(gift.category)}</div>` : ""}
          <h3 class="result-card__title">${escHtml(gift.title)}</h3>
          ${gift.short_description ? `<p class="result-card__desc">${escHtml(gift.short_description)}</p>` : ""}
          ${tagsHtml ? `<div class="result-card__tags">${tagsHtml}</div>` : ""}
          <div class="result-card__price">від ${escHtml(gift.min_price)} ₴</div>
          ${offerHtml}
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
    if (!category) return;

    state.category = category;
    document.querySelectorAll("#categoriesGrid .wizard-card").forEach((card) => {
      if (card.dataset.value === category) {
        card.classList.add("wizard-card--active");
      }
    });
  })();
})();
