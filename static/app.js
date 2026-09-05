(function () {
  const autosaveForm = document.querySelector("[data-autosave]");
  if (!autosaveForm) return;

  const status = autosaveForm.querySelector(".autosave-state");
  const csrf = document.querySelector("meta[name='csrf-token']")?.getAttribute("content") || "";
  let timer = null;
  let lastBody = "";

  function setStatus(text) {
    if (status) status.textContent = text;
  }

  function collectBody() {
    const data = new FormData(autosaveForm);
    if (!data.get("csrf_token")) data.set("csrf_token", csrf);
    return new URLSearchParams(data).toString();
  }

  async function saveNow() {
    const body = collectBody();
    if (body === lastBody) return;
    lastBody = body;
    setStatus("Saving...");
    try {
      const response = await fetch(autosaveForm.action, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRF-Token": csrf
        },
        body
      });
      const payload = await response.json();
      if (!payload.ok) {
        setStatus(payload.error || "Could not save");
        return;
      }
      setStatus("Saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    } catch (error) {
      setStatus("Offline. Changes will save when retried.");
    }
  }

  function schedule() {
    setStatus("Unsaved changes");
    clearTimeout(timer);
    timer = setTimeout(saveNow, 550);
  }

  autosaveForm.addEventListener("input", schedule);
  autosaveForm.addEventListener("change", schedule);
  autosaveForm.addEventListener("submit", function (event) {
    event.preventDefault();
    saveNow();
  });
})();

