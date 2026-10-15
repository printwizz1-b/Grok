const state = {
  apiKey: "",
  imageDataUrl: "",
  watching: "",
};

const apiKeyInput = document.querySelector("#api-key");
const rememberKey = document.querySelector("#remember-key");
const promptInput = document.querySelector("#prompt");
const imageInput = document.querySelector("#image");
const imagePreview = document.querySelector("#image-preview");
const durationInput = document.querySelector("#duration");
const durationLabel = document.querySelector("#duration-label");
const aspectInput = document.querySelector("#aspect");
const form = document.querySelector("#composer");
const generateButton = document.querySelector("#generate");
const stopButton = document.querySelector("#stop");
const formError = document.querySelector("#form-error");
const stage = document.querySelector("#stage");
const stageMessage = document.querySelector("#stage-message");
const player = document.querySelector("#player");
const progress = document.querySelector("#progress");
const progressBar = document.querySelector("#progress-bar");
const stageMeta = document.querySelector("#stage-meta");
const libraryList = document.querySelector("#library-list");
const apiLine = document.querySelector("#api-line");

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) {
    headers.set("X-Api-Key", state.apiKey);
  }
  let body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(path, { method: options.method || "GET", headers, body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status}).`);
  }
  return data;
}

function setError(message) {
  formError.textContent = message || "";
}

function rememberChanged() {
  state.apiKey = apiKeyInput.value.trim();
  if (rememberKey.checked && state.apiKey) {
    localStorage.setItem("videoDesk.apiKey", state.apiKey);
    localStorage.setItem("videoDesk.remember", "1");
  } else {
    localStorage.removeItem("videoDesk.apiKey");
    localStorage.setItem("videoDesk.remember", rememberKey.checked ? "1" : "0");
  }
}

function updateDuration() {
  const seconds = durationInput.value;
  durationLabel.textContent = `${seconds} second${seconds === "1" ? "" : "s"}`;
}

function updateFrame() {
  const [width, height] = aspectInput.value.split(":");
  stage.style.aspectRatio = `${width} / ${height}`;
}

function showMessage(message) {
  player.hidden = true;
  player.removeAttribute("src");
  player.load();
  stageMessage.hidden = false;
  stageMessage.textContent = message;
}

function showProgress(value) {
  progress.hidden = false;
  const amount = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 8;
  progressBar.style.width = `${amount}%`;
}

function showVideo(data) {
  progress.hidden = true;
  stageMessage.hidden = true;
  player.hidden = false;
  player.src = data.localUrl;
  player.play().catch(() => {});
  const bits = ["Saved in the videos folder"];
  if (data.duration) bits.push(`${data.duration}s`);
  if (typeof data.costUsd === "number") bits.push(`about $${data.costUsd.toFixed(2)} on this key`);
  stageMeta.textContent = bits.join(" · ");
}

async function refreshLibrary() {
  const data = await api("/api/library");
  libraryList.replaceChildren();
  if (!data.videos.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No clips yet.";
    libraryList.append(item);
    return;
  }
  for (const video of data.videos) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = video.fileName;
    button.addEventListener("click", () => {
      setError("");
      showVideo(video);
    });
    item.append(button);
    libraryList.append(item);
  }
}

async function watch(requestId) {
  const started = Date.now();
  while (state.watching === requestId) {
    const data = await api(`/api/videos/${encodeURIComponent(requestId)}`);
    if (data.status === "done") {
      generateButton.disabled = false;
      stopButton.hidden = true;
      if (data.localUrl) {
        showVideo(data);
      } else {
        progress.hidden = true;
        showMessage(data.error || "xAI finished without a video file.");
        setError(data.error || "");
      }
      await refreshLibrary();
      return;
    }
    if (data.status === "failed") {
      generateButton.disabled = false;
      stopButton.hidden = true;
      progress.hidden = true;
      showMessage(data.error || "Video generation failed.");
      setError(data.error || "");
      return;
    }
    showMessage("xAI is making the video. You can leave this page open.");
    showProgress(Number(data.progress));
    if (Date.now() - started > 15 * 60 * 1000) {
      generateButton.disabled = false;
      stopButton.hidden = true;
      setError("This is taking longer than 15 minutes. Leave the xAI request running and try the library again later.");
      return;
    }
    await sleep(4000);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  state.apiKey = apiKeyInput.value.trim();
  rememberChanged();
  if (!state.apiKey) {
    setError("Paste your xAI API key from console.x.ai.");
    return;
  }
  generateButton.disabled = true;
  stopButton.hidden = false;
  showMessage("Sending the request to xAI.");
  showProgress(4);
  try {
    const body = {
      prompt: promptInput.value.trim(),
      model: document.querySelector("#model").value,
      duration: Number(durationInput.value),
      aspectRatio: aspectInput.value,
      resolution: document.querySelector("#resolution").value,
    };
    if (state.imageDataUrl) body.imageDataUrl = state.imageDataUrl;
    const started = await api("/api/generations", { method: "POST", json: body });
    state.watching = started.requestId;
    await watch(started.requestId);
  } catch (error) {
    generateButton.disabled = false;
    stopButton.hidden = true;
    progress.hidden = true;
    showMessage("The video was not started.");
    setError(error.message);
  }
});

stopButton.addEventListener("click", () => {
  state.watching = "";
  generateButton.disabled = false;
  stopButton.hidden = true;
  progress.hidden = true;
  showMessage("Stopped watching. xAI may still finish the clip on their side.");
});

durationInput.addEventListener("input", updateDuration);
aspectInput.addEventListener("change", updateFrame);
apiKeyInput.addEventListener("change", rememberChanged);
rememberKey.addEventListener("change", rememberChanged);

imageInput.addEventListener("change", () => {
  const file = imageInput.files && imageInput.files[0];
  state.imageDataUrl = "";
  imagePreview.hidden = true;
  imagePreview.replaceChildren();
  if (!file) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
    setError("Use a PNG, JPEG, or WebP image.");
    imageInput.value = "";
    return;
  }
  if (file.size > 6 * 1024 * 1024) {
    setError("Use a starting image under 6 MB.");
    imageInput.value = "";
    return;
  }
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    state.imageDataUrl = String(reader.result || "");
    const img = document.createElement("img");
    img.alt = "Starting image";
    img.src = state.imageDataUrl;
    imagePreview.append(img);
    imagePreview.hidden = false;
    setError("");
  });
  reader.readAsDataURL(file);
});

rememberKey.checked = localStorage.getItem("videoDesk.remember") === "1";
if (rememberKey.checked) {
  apiKeyInput.value = localStorage.getItem("videoDesk.apiKey") || "";
  state.apiKey = apiKeyInput.value.trim();
}
updateDuration();
updateFrame();

api("/api/health")
  .then((health) => {
    apiLine.textContent = `Sends video requests to ${health.apiBase}. The key stays in this browser.`;
  })
  .catch(() => {
    apiLine.textContent = "Could not reach the local Video Desk server.";
  });

refreshLibrary().catch((error) => setError(error.message));
