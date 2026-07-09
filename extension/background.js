const DEFAULT_ENDPOINT = "http://127.0.0.1:8765/capture";
const PAGE_TEXT_LIMIT = 20000;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "rawmem-save-selection",
    title: "rawmem: save selection",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: "rawmem-save-page",
    title: "rawmem: save page",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "rawmem-save-selection") captureTab(tab, "selection");
  if (info.menuItemId === "rawmem-save-page") captureTab(tab, "page");
});

chrome.commands.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  if (command === "save-selection") captureTab(tab, "selection");
  if (command === "save-page") captureTab(tab, "page");
});

chrome.action.onClicked.addListener((tab) => captureTab(tab, "auto"));

function collectPageData(mode, pageTextLimit) {
  const selection = window.getSelection ? String(window.getSelection()) : "";
  const wantPage = mode === "page" || (mode === "auto" && !selection.trim());
  const pageText = wantPage && document.body ? document.body.innerText.slice(0, pageTextLimit) : "";
  return {
    selection,
    pageText,
    kind: wantPage ? "page" : "selection",
    title: document.title,
    url: location.href,
    host: location.host,
  };
}

async function captureTab(tab, mode) {
  if (!tab || tab.id === undefined) return;
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectPageData,
      args: [mode, PAGE_TEXT_LIMIT],
    });
    const data = result && result.result;
    if (!data) throw new Error("no page data");
    const text = data.kind === "page" ? data.pageText : data.selection;
    if (!text.trim()) throw new Error("nothing to capture");
    const payload = {
      source: "browser",
      event_type: data.kind === "page" ? "web_page" : "web_clip",
      summary: data.title,
      raw_text: text,
      tags: ["browser", "extension", data.kind],
      payload: { url: data.url, title: data.title, host: data.host },
    };
    const { endpoint } = await chrome.storage.sync.get({ endpoint: DEFAULT_ENDPOINT });
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await flashBadge(tab.id, "ok", "#2e7d32");
  } catch (error) {
    console.warn("rawmem capture failed:", error);
    await flashBadge(tab.id, "err", "#c62828");
  }
}

async function flashBadge(tabId, text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ tabId, text });
    setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 2500);
  } catch (_) {
    // Tab may be gone; badge feedback is best-effort.
  }
}
