const ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2"];
const EXAMPLES = [
  { label: "Rainy street", text: "A paper boat crossing a rainy street at dusk, camera tracking low and slow" },
  { label: "Clay fox", text: "A clay fox in stop-motion looking up as the first snow starts to fall" },
];

const state = {
  apiKey: "",
  imageDataUrl: "",
  watching: "",
  clock: 0,
  elapsed: 0,
  progress: 0,
  currentUrl: "",
};

const apiKeyInput = document.querySelector("#api-key");
const rememberKey = document.querySelector("#remember-key");
const toggleKey = document.querySelector("#toggle-key");
const keyState = document.querySelector("#key-state");
const promptInput = document.querySelector("#prompt");
const promptCount = document.querySelector("#prompt-count");
const examples = document.querySelector("#examples");
const imageInput = document.querySelector("#image");
const imagePreview = document.querySelector("#image-preview");
const drop = document.querySelector("#drop");
const clearImage = document.querySelector("#clear-image");
const durationInput = document.querySelector("#duration");
const durationLabel = document.querySelector("#duration-label");
const aspects = document.querySelector("#aspects");
const controls = document.querySelector("#controls");
const form = document.querySelector("#composer");
const generateButton = document.querySelector("#generate");
const stopButton = document.querySelector("#stop");
const formError = document.querySelector("#form-error");
const stage = document.querySelector("#stage");
const stageMessage = document.querySelector("#stage-message");
const player = document.querySelector("#player");
const liveStatus = document.querySelector("#live-status");
const progress = document.querySelector("#progress");
const progressBar = document.querySelector("#progress-bar");
const downloadLink = document.querySelector("#download");
const libraryList = document.querySelector("#library-list");
const libraryCount = document.querySelector("#library-count");
const apiLine = document.querySelector("#api-line");

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function selected(name) {
  const input = document.querySelector(`input[name="${name}"]:checked`);
  return input ? input.value : "";
}

function localVideoUrl(url) {
  if (typeof url !== "string" || !url.startsWith("/library/") || url.includes("..")) return "";
  return url;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("X-Api-Key", state.apiKey);
  let body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(path, { method: options.method || "GET", headers, body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function setError(message) {
  formError.textContent = message || "";
}

function setStatus(message) {
  liveStatus.textContent = message || "";
}

function formatWhen(unix) {
  if (!unix) return "";
  return new Date(unix * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatBytes(size) {
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatElapsed(seconds) {
  const mins = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return `${mins}:${rest}`;
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
  keyState.textContent = state.apiKey ? "Key ready" : "No key yet";
  keyState.classList.toggle("is-ready", Boolean(state.apiKey));
}

function updateDuration() {
  const seconds = durationInput.value;
  durationLabel.textContent = `${seconds} second${seconds === "1" ? "" : "s"}`;
  durationInput.setAttribute("aria-valuenow", seconds);
  if (!generateButton.disabled) generateButton.textContent = `Generate ${seconds}-second video`;
}

function updatePromptCount() {
  const length = promptInput.value.length;
  promptCount.textContent = `${length} / 4000`;
  promptCount.classList.toggle("is-full", length > 3800);
}

function showProgress(value) {
  progress.hidden = false;
  const amount = Number.isFinite(value) ? Math.max(4, Math.min(100, value)) : 8;
  state.progress = amount;
  progressBar.style.width = `${amount}%`;
}

function hideProgress() {
  progress.hidden = true;
  progressBar.style.width = "0";
}

function workingCopy() {
  const percent = state.progress ? `${Math.round(state.progress)}%` : "starting";
  return `xAI is making the video · ${percent} · ${formatElapsed(state.elapsed)}. You can leave this page open.`;
}

function startClock() {
  stopClock();
  const started = Date.now();
  state.elapsed = 0;
  state.clock = window.setInterval(() => {
    state.elapsed = Math.floor((Date.now() - started) / 1000);
    if (state.watching) setStatus(workingCopy());
  }, 500);
}

function stopClock() {
  if (state.clock) window.clearInterval(state.clock);
  state.clock = 0;
}

function showVideo(data) {
  const url = localVideoUrl(data.localUrl);
  if (!url) return;
  hideProgress();
  stageMessage.hidden = true;
  player.hidden = false;
  stage.setAttribute("aria-busy", "false");
  if (player.getAttribute("src") !== url) {
    player.src = url;
    player.play().catch(() => {});
  }
  state.currentUrl = url;
  downloadLink.hidden = false;
  downloadLink.href = url;
  const name = data.fileName || "video.mp4";
  downloadLink.setAttribute("download", name);
  const bits = ["Saved in the videos folder"];
  if (data.duration) bits.push(`${data.duration}s`);
  if (data.resolution) bits.push(data.resolution);
  if (typeof data.costUsd === "number") bits.push(`about $${data.costUsd.toFixed(2)} on this key`);
  setStatus(bits.join(" · "));
  markSelected(url);
}

function clearWatch() {
  sessionStorage.removeItem("videoDesk.watch");
  state.watching = "";
  stopClock();
  controls.disabled = false;
  generateButton.disabled = false;
  stopButton.hidden = true;
  updateDuration();
}

async function refreshLibrary() {
  const data = await api("/api/library");
  libraryList.replaceChildren();
  const videos = data.videos || [];
  libraryCount.textContent = videos.length ? `${videos.length} clip${videos.length === 1 ? "" : "s"}` : "";
  if (!videos.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No clips yet. Generated videos show up here.";
    libraryList.append(item);
    return;
  }
  for (const video of videos) {
    libraryList.append(renderClip(video));
  }
  markSelected(state.currentUrl);
}

function renderClip(video) {
  const item = document.createElement("li");
  item.className = "clip";
  item.dataset.url = video.localUrl || "";

  const play = document.createElement("button");
  play.type = "button";
  play.className = "clip-play";
  const title = document.createElement("strong");
  title.textContent = video.prompt || "Saved clip";
  const meta = document.createElement("div");
  meta.className = "clip-meta";
  const details = [video.duration ? `${video.duration}s` : "", video.resolution || "", video.aspectRatio || "", formatWhen(video.modified), formatBytes(video.bytes)];
  meta.textContent = details.filter(Boolean).join(" · ");
  play.append(title, meta);
  play.addEventListener("click", () => {
    setError("");
    showVideo(video);
  });

  const actions = document.createElement("div");
  actions.className = "clip-actions";
  const download = document.createElement("a");
  download.className = "quiet download";
  download.href = localVideoUrl(video.localUrl);
  download.download = video.fileName || "video.mp4";
  download.textContent = "Download";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "quiet slim";
  remove.textContent = "Delete";
  remove.addEventListener("click", async () => {
    if (remove.dataset.confirm !== "1") {
      remove.dataset.confirm = "1";
      remove.textContent = "Delete?";
      return;
    }
    remove.disabled = true;
    try {
      await api(`/api/library/${encodeURIComponent(video.fileName)}`, { method: "DELETE" });
      if (state.currentUrl === video.localUrl) {
        player.hidden = true;
        player.removeAttribute("src");
        player.load();
        stageMessage.hidden = false;
        downloadLink.hidden = true;
        state.currentUrl = "";
        setStatus("Deleted from this computer.");
      }
      await refreshLibrary();
    } catch (error) {
      remove.disabled = false;
      setError(error.message);
    }
  });
  actions.append(download, remove);
  item.append(play, actions);
  return item;
}

function markSelected(url) {
  for (const item of libraryList.querySelectorAll(".clip")) {
    item.classList.toggle("is-selected", item.dataset.url === url);
  }
}

async function watch(requestId) {
  const started = Date.now();
  while (state.watching === requestId) {
    let data;
    try {
      data = await api(`/api/videos/${encodeURIComponent(requestId)}`);
    } catch (error) {
      clearWatch();
      hideProgress();
      setStatus(error.message);
      setError(error.message);
      return;
    }
    if (data.status === "done") {
      clearWatch();
      if (data.localUrl) {
        showVideo(data);
      } else {
        hideProgress();
        stage.setAttribute("aria-busy", "false");
        setStatus(data.error || "xAI finished without a video file.");
        setError(data.error || "");
      }
      await refreshLibrary();
      return;
    }
    if (data.status === "failed") {
      clearWatch();
      hideProgress();
      stage.setAttribute("aria-busy", "false");
      setStatus(data.error || "Video generation failed.");
      setError(data.error || "");
      return;
    }
    const numeric = Number(data.progress);
    showProgress(Number.isFinite(numeric) && data.progress !== null ? numeric : state.progress);
    setStatus(workingCopy());
    if (Date.now() - started > 15 * 60 * 1000) {
      clearWatch();
      setError("This is taking longer than 15 minutes. Check the saved list again in a little while.");
      return;
    }
    await sleep(4000);
  }
}

function useImageFile(file) {
  state.imageDataUrl = "";
  imagePreview.hidden = true;
  imagePreview.replaceChildren();
  clearImage.hidden = true;
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
    imagePreview.replaceChildren(img);
    imagePreview.hidden = false;
    clearImage.hidden = false;
    setError("");
  });
  reader.addEventListener("error", () => setError("That image could not be read."));
  reader.readAsDataURL(file);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  state.apiKey = apiKeyInput.value.trim();
  rememberChanged();
  if (!state.apiKey) {
    setError("Paste your xAI API key from console.x.ai.");
    apiKeyInput.focus();
    return;
  }
  if (!promptInput.value.trim() && !state.imageDataUrl) {
    setError("Write a prompt, or choose a starting image.");
    promptInput.focus();
    return;
  }
  controls.disabled = true;
  generateButton.disabled = true;
  generateButton.textContent = "Sending…";
  stopButton.hidden = false;
  stage.setAttribute("aria-busy", "true");
  setStatus("Sending the request to xAI.");
  showProgress(4);
  startClock();
  try {
    const body = {
      prompt: promptInput.value.trim(),
      model: selected("model"),
      duration: Number(durationInput.value),
      aspectRatio: selected("aspect"),
      resolution: selected("resolution"),
    };
    if (state.imageDataUrl) body.imageDataUrl = state.imageDataUrl;
    const started = await api("/api/generations", { method: "POST", json: body });
    state.watching = started.requestId;
    sessionStorage.setItem("videoDesk.watch", started.requestId);
    generateButton.textContent = "Making video…";
    await watch(started.requestId);
  } catch (error) {
    clearWatch();
    hideProgress();
    stage.setAttribute("aria-busy", "false");
    setStatus("The video was not started.");
    setError(error.message);
  }
});

stopButton.addEventListener("click", () => {
  clearWatch();
  hideProgress();
  stage.setAttribute("aria-busy", "false");
  setStatus("Stopped waiting. xAI may still finish the clip. It will not be saved unless you generate again.");
});

toggleKey.addEventListener("click", () => {
  const hidden = apiKeyInput.type === "password";
  apiKeyInput.type = hidden ? "text" : "password";
  toggleKey.textContent = hidden ? "Hide" : "Show";
});

durationInput.addEventListener("input", updateDuration);
promptInput.addEventListener("input", updatePromptCount);
apiKeyInput.addEventListener("input", rememberChanged);
rememberKey.addEventListener("change", rememberChanged);
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});

imageInput.addEventListener("change", () => useImageFile(imageInput.files && imageInput.files[0]));
clearImage.addEventListener("click", () => {
  imageInput.value = "";
  useImageFile(null);
});
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("is-drag");
});
drop.addEventListener("dragleave", () => drop.classList.remove("is-drag"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("is-drag");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) useImageFile(file);
});

for (const example of EXAMPLES) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "quiet slim";
  button.textContent = example.label;
  button.addEventListener("click", () => {
    promptInput.value = example.text;
    updatePromptCount();
    promptInput.focus();
  });
  examples.append(button);
}

for (const ratio of ASPECTS) {
  const label = document.createElement("label");
  label.className = "pill";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "aspect";
  input.value = ratio;
  if (ratio === "16:9") input.checked = true;
  const text = document.createElement("span");
  text.textContent = ratio;
  label.append(input, text);
  aspects.append(label);
}

rememberKey.checked = localStorage.getItem("videoDesk.remember") === "1";
if (rememberKey.checked) apiKeyInput.value = localStorage.getItem("videoDesk.apiKey") || "";
rememberChanged();
updateDuration();
updatePromptCount();

api("/api/health")
  .then((health) => {
    apiLine.textContent = `Sends video requests to ${health.apiBase}. The key stays in this browser.`;
  })
  .catch(() => {
    apiLine.textContent = "Could not reach the local Video Desk server.";
  });

refreshLibrary()
  .catch((error) => setError(error.message))
  .then(() => {
    const pending = sessionStorage.getItem("videoDesk.watch");
    if (!pending || !state.apiKey) return;
    state.watching = pending;
    controls.disabled = true;
    generateButton.disabled = true;
    generateButton.textContent = "Making video…";
    stopButton.hidden = false;
    stage.setAttribute("aria-busy", "true");
    showProgress(8);
    startClock();
    setStatus("Checking the video you already started.");
    return watch(pending);
  })
  .catch((error) => setError(error.message));
