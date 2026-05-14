(function () {
  "use strict";

  const offerLinks = document.querySelectorAll("[data-offer-click]");
  if (!offerLinks.length) return;

  function csrfToken() {
    if (window.GF_CSRF_TOKEN) return window.GF_CSRF_TOKEN;

    const cookie = document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : "";
  }

  function openTarget(targetUrl, openedWindow) {
    if (openedWindow) {
      openedWindow.opener = null;
      openedWindow.location.href = targetUrl;
      return;
    }

    window.location.href = targetUrl;
  }

  async function resolveTrackedUrl(link) {
    const fallbackUrl = link.dataset.originalUrl || link.href;
    const clickUrl = link.dataset.clickUrl;
    if (!clickUrl) return fallbackUrl;

    try {
      const response = await fetch(clickUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          referrer: document.referrer || window.location.href,
        }),
      });

      if (!response.ok) return fallbackUrl;

      const payload = await response.json();
      return payload.affiliate_url || fallbackUrl;
    } catch (error) {
      return fallbackUrl;
    }
  }

  offerLinks.forEach((link) => {
    link.addEventListener("click", async (event) => {
      event.preventDefault();

      const fallbackUrl = link.dataset.originalUrl || link.href;
      const openedWindow = window.open("about:blank", "_blank");
      const targetUrl = await resolveTrackedUrl(link);
      openTarget(targetUrl || fallbackUrl, openedWindow);
    });
  });
})();
