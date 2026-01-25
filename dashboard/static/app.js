document.addEventListener("submit", (event) => {
  const form = event.target.closest("form.status-form");
  if (!form) return;
  const card = form.closest(".card");
  if (!card) return;
  const leadId = card.getAttribute("data-lead-id");
  const notes = leadId
    ? card.querySelector(`textarea[data-lead-id="${leadId}"]`)
    : card.querySelector("textarea[name='notes']");
  const hidden = form.querySelector("input[name='notes']");
  if (hidden && notes) {
    hidden.value = notes.value || "";
  }
  card.classList.add("is-saving");
  setTimeout(() => {
    card.remove();
  }, 150);
});
