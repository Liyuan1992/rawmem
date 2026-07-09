const DEFAULT_ENDPOINT = "http://127.0.0.1:8765/capture";

const endpointInput = document.getElementById("endpoint");
const tokenInput = document.getElementById("token");
const status = document.getElementById("status");

chrome.storage.sync.get({ endpoint: DEFAULT_ENDPOINT, token: "" }, ({ endpoint, token }) => {
  endpointInput.value = endpoint;
  tokenInput.value = token;
});

document.getElementById("save").addEventListener("click", () => {
  const endpoint = endpointInput.value.trim() || DEFAULT_ENDPOINT;
  const token = tokenInput.value.trim();
  chrome.storage.sync.set({ endpoint, token }, () => {
    status.textContent = "saved";
    setTimeout(() => (status.textContent = ""), 1500);
  });
});
