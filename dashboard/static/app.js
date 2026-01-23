document.addEventListener("click", (event) => {
  const button = event.target.closest("button.copy");
  if (!button) return;
  const targetId = button.getAttribute("data-target");
  if (!targetId) return;
  const textarea = document.getElementById(targetId);
  if (!textarea) return;
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  document.execCommand("copy");
  button.textContent = "Copied!";
  setTimeout(() => {
    button.textContent = "Copy draft";
  }, 1200);
});
