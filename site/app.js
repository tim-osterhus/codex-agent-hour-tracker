(() => {
  const copyButton = document.querySelector("[data-copy-command]");
  const copyStatus = document.querySelector("#copy-status");

  if (!copyButton || !copyStatus) {
    return;
  }

  const originalLabel = copyButton.textContent;

  copyButton.addEventListener("click", async () => {
    const command = copyButton.dataset.copyCommand || "";

    try {
      await navigator.clipboard.writeText(command);
      copyButton.textContent = "Copied";
      copyStatus.textContent = "Copied";
    } catch (error) {
      copyButton.textContent = "Copy failed";
      copyStatus.textContent = "Copy failed";
    }

    setTimeout(() => {
      copyButton.textContent = originalLabel;
      copyStatus.textContent = "";
    }, 2000);
  });
})();
