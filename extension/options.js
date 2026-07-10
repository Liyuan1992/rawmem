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
    showStatus("saved");
  });
});

document.getElementById("test").addEventListener("click", async () => {
  const endpoint = endpointInput.value.trim() || DEFAULT_ENDPOINT;
  const token = tokenInput.value.trim();
  showStatus("testing...");
  try {
    const checkUrl = new URL(endpoint);
    checkUrl.pathname = "/check";
    checkUrl.search = "";
    checkUrl.hash = "";
    const headers = { Accept: "application/json" };
    if (token) headers["X-Rawmem-Token"] = token;
    const response = await fetch(checkUrl.toString(), { method: "GET", headers });
    if (response.status === 401) throw new Error("token rejected");
    if (!response.ok) throw new Error(`daemon returned HTTP ${response.status}`);
    const data = await response.json();
    if (!data.ok || !data.authorized) throw new Error("unexpected daemon response");
    showStatus("connected; token accepted");
  } catch (error) {
    const message = error instanceof TypeError ? "daemon unreachable" : error.message;
    showStatus(message, true);
  }
});

let clearStatusTimer;

function showStatus(message, isError = false) {
  clearTimeout(clearStatusTimer);
  status.textContent = message;
  status.classList.toggle("error", isError);
  if (message !== "testing...") {
    clearStatusTimer = setTimeout(() => {
      status.textContent = "";
      status.classList.remove("error");
    }, 4000);
  }
}
