const status = document.querySelector("#status");
const request = (message) => chrome.runtime.sendMessage(message);
document.querySelector("#pair").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const result = await request({op: "pair", server_url: document.querySelector("#server").value.trim(), code: document.querySelector("#code").value.trim()});
    status.textContent = result.error || result.status;
    if (result.ok) document.querySelector("#code").value = "";
  } finally { button.disabled = false; }
});
document.querySelector("#disconnect").addEventListener("click", async () => {
  const result = await request({op: "disconnect"}); status.textContent = result.error || result.status;
});
const result = await request({op: "status"}); status.textContent = result.status;
