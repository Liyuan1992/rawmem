const DEFAULT_ENDPOINT = "http://127.0.0.1:8765/capture";

const input = document.getElementById("endpoint");
const status = document.getElementById("status");

chrome.storage.sync.get({ endpoint: DEFAULT_ENDPOINT }, ({ endpoint }) => {
  input.value = endpoint;
});

document.getElementById("save").addEventListener("click", () => {
  const endpoint = input.value.trim() || DEFAULT_ENDPOINT;
  chrome.storage.sync.set({ endpoint }, () => {
    status.textContent = "saved";
    setTimeout(() => (status.textContent = ""), 1500);
  });
});
