/**
 * Harvick Farms Video Studio - Application Controller
 */

// Global State
let currentFilePath = null;
let currentAnalysis = null;
let activeJobId = null;
let jobPollTimer = null;
let timeline = null;
let availablePresets = {};

// DOM Elements
const previewVideo = document.getElementById("previewVideo");
const playerWrapper = document.getElementById("playerWrapper");
const watermarkOverlay = document.getElementById("watermarkOverlay");
const lowerThirdPreview = document.getElementById("lowerThirdPreview");
const lowerThirdPreviewText = document.getElementById("lowerThirdPreviewText");
const jumpCutPreviewToggle = document.getElementById("jumpCutPreviewToggle");

// Sliders and Inputs
const noiseDbSlider = document.getElementById("noiseDbSlider");
const noiseDbVal = document.getElementById("noiseDbVal");
const minSilenceSlider = document.getElementById("minSilenceSlider");
const minSilenceVal = document.getElementById("minSilenceVal");
const paddingSlider = document.getElementById("paddingSlider");
const paddingVal = document.getElementById("paddingVal");
const clipDurationSelect = document.getElementById("clipDurationSelect");

const framingModeSelect = document.getElementById("framingModeSelect");
const logoToggle = document.getElementById("logoToggle");
const logoPosSelect = document.getElementById("logoPosSelect");
const logoOpacitySlider = document.getElementById("logoOpacitySlider");
const logoOpacityVal = document.getElementById("logoOpacityVal");
const lowerThirdInput = document.getElementById("lowerThirdInput");

const audioNormToggle = document.getElementById("audioNormToggle");
const subtitlesToggle = document.getElementById("subtitlesToggle");
const subStyleRow = document.getElementById("subStyleRow");
const subStyleSelect = document.getElementById("subStyleSelect");

// Scanning Status Banner Elements
const scanStatusBanner = document.getElementById("scanStatusBanner");
const scanStatusTitle = document.getElementById("scanStatusTitle");
const scanStatusTimer = document.getElementById("scanStatusTimer");
const scanStatusDesc = document.getElementById("scanStatusDesc");

let activeScanController = null;
let scanTimerInterval = null;

// Stats & Info
const statOrigDur = document.getElementById("statOrigDur");
const statCutDur = document.getElementById("statCutDur");
const statSavedPct = document.getElementById("statSavedPct");
const statCutsCount = document.getElementById("statCutsCount");
const activeFileIndicator = document.getElementById("activeFileIndicator");

// Buttons & Actions
const btnScan = document.getElementById("btnScan");
const btnRescan = document.getElementById("btnRescan");
const btnRender = document.getElementById("btnRender");
const filePathInput = document.getElementById("filePathInput");
const fileInput = document.getElementById("fileInput");
const dropzone = document.getElementById("dropzone");
const presetButtonsContainer = document.getElementById("presetButtons");

// Progress & Exports
const progressBox = document.getElementById("progressBox");
const progressStep = document.getElementById("progressStep");
const progressPct = document.getElementById("progressPct");
const progressBarFill = document.getElementById("progressBarFill");
const renderActions = document.getElementById("renderActions");
const btnDownload = document.getElementById("btnDownload");
const btnRevealFolder = document.getElementById("btnRevealFolder");
const exportList = document.getElementById("exportList");
const btnRefreshExports = document.getElementById("btnRefreshExports");

// Header Actions
const hwBadge = document.getElementById("hwBadge");
const hwDot = document.getElementById("hwDot");
const systemStatus = document.getElementById("systemStatus");
const btnOpenOutputsHeader = document.getElementById("btnOpenOutputsHeader");
const btnOpenUploadsHeader = document.getElementById("btnOpenUploadsHeader");

// Helper: Format seconds to M:SS or H:MM:SS
function formatDuration(sec) {
  if (!sec || isNaN(sec) || sec <= 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// 1. Initialize Studio
window.addEventListener("DOMContentLoaded", () => {
  // Initialize Timeline canvas controller
  timeline = new VideoTimeline("timelineCanvas", "timelinePlayhead", previewVideo);

  checkSystemHealth();
  loadPresets();
  loadRecentExports();
  setupEventListeners();
  updateWatermarkPreview();
});

// 2. Hardware and Health Check
async function checkSystemHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) throw new Error("Health check failed");
    const data = await res.json();

    if (data.has_nvenc) {
      systemStatus.textContent = "NVIDIA NVENC Hardware Accelerated";
      hwBadge.style.background = "rgba(46, 125, 50, 0.25)";
      hwBadge.style.color = "#81c784";
      hwDot.style.background = "#4caf50";
      hwDot.style.boxShadow = "0 0 10px #4caf50";
    } else {
      systemStatus.textContent = "FFmpeg CPU Encoding (libx264)";
      hwBadge.style.background = "rgba(251, 192, 45, 0.15)";
      hwBadge.style.color = "#fbc02d";
      hwDot.style.background = "#fbc02d";
      hwDot.style.boxShadow = "0 0 8px #fbc02d";
    }
  } catch (err) {
    systemStatus.textContent = "Backend Offline";
    hwBadge.style.background = "rgba(239, 68, 68, 0.2)";
    hwBadge.style.color = "#ef4444";
    hwDot.style.background = "#ef4444";
  }
}

// 3. Load Presets
async function loadPresets() {
  try {
    const res = await fetch("/api/presets");
    if (!res.ok) return;
    const presets = await res.json();
    
    presetButtonsContainer.innerHTML = "";
    presets.forEach((p, idx) => {
      availablePresets[p.id] = p.config;
      const btn = document.createElement("button");
      btn.className = `preset-chip ${idx === 0 ? "active" : ""}`;
      btn.textContent = p.name;
      btn.setAttribute("data-preset", p.id);
      btn.title = p.description || p.name;
      btn.addEventListener("click", () => applyPreset(p.id));
      presetButtonsContainer.appendChild(btn);
    });

    if (presets.length > 0) {
      applyPreset(presets[0].id, false);
    }
  } catch (e) {
    console.warn("Could not load presets:", e);
  }
}

function applyPreset(presetId, triggerRecalc = true) {
  const cfg = availablePresets[presetId];
  if (!cfg) return;

  // Highlight active chip
  document.querySelectorAll(".preset-chip").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-preset") === presetId);
  });

  // Apply Aspect Ratio
  const targetAspect = cfg.target_aspect_ratio || "9:16";
  setAspectRatio(targetAspect);

  // Apply Framing Mode
  if (cfg.framing_mode) {
    framingModeSelect.value = cfg.framing_mode;
  }

  // Set default clip duration scope based on preset
  if (clipDurationSelect) {
    if (presetId === "youtube_shorts_reframe") {
      clipDurationSelect.value = "60";
    } else {
      clipDurationSelect.value = "all";
    }
  }

  // Check if silence parameters changed
  const oldDb = parseFloat(noiseDbSlider.value);
  const oldMinSil = parseFloat(minSilenceSlider.value);
  const oldPad = parseFloat(paddingSlider.value);

  // Apply Silence Settings
  if (cfg.silence_thresh_db !== undefined) {
    noiseDbSlider.value = cfg.silence_thresh_db;
    noiseDbVal.textContent = `${cfg.silence_thresh_db} dB`;
  }
  if (cfg.min_silence_duration !== undefined) {
    minSilenceSlider.value = cfg.min_silence_duration;
    minSilenceVal.textContent = `${cfg.min_silence_duration}s`;
  }
  if (cfg.padding_duration !== undefined) {
    paddingSlider.value = cfg.padding_duration;
    paddingVal.textContent = `${cfg.padding_duration}s`;
  }

  // Apply Branding Settings
  if (cfg.logo_overlay !== undefined) {
    logoToggle.checked = cfg.logo_overlay;
  }
  if (cfg.logo_position) {
    logoPosSelect.value = cfg.logo_position;
  }
  if (cfg.logo_opacity !== undefined) {
    logoOpacitySlider.value = cfg.logo_opacity;
    logoOpacityVal.textContent = `${Math.round(cfg.logo_opacity * 100)}%`;
  }
  updateWatermarkPreview();

  // Audio & Subtitles
  if (cfg.normalize_audio !== undefined) {
    audioNormToggle.checked = cfg.normalize_audio;
  }
  if (cfg.burn_subtitles !== undefined) {
    subtitlesToggle.checked = cfg.burn_subtitles;
    subStyleRow.style.display = cfg.burn_subtitles ? "flex" : "none";
  }
  if (cfg.subtitle_style) {
    subStyleSelect.value = cfg.subtitle_style;
  }

  const silenceChanged = (
    oldDb !== parseFloat(noiseDbSlider.value) ||
    oldMinSil !== parseFloat(minSilenceSlider.value) ||
    oldPad !== parseFloat(paddingSlider.value)
  );

  if (currentFilePath && currentAnalysis) {
    if (triggerRecalc && silenceChanged) {
      scanVideo(currentFilePath);
    } else {
      updateSegmentStats();
    }
  }
}

// Slice speech segments up to the selected clip duration limit
function getActiveSegments() {
  if (!currentAnalysis || !currentAnalysis.speech_segments) return [];
  const all = currentAnalysis.speech_segments;
  const limitStr = clipDurationSelect ? clipDurationSelect.value : "all";
  if (limitStr === "all") return all;

  const maxDur = parseFloat(limitStr);
  let accumulated = 0.0;
  const selected = [];

  for (const seg of all) {
    if (accumulated + seg.duration <= maxDur) {
      selected.push(seg);
      accumulated += seg.duration;
    } else {
      const remainder = maxDur - accumulated;
      if (remainder > 0.25) {
        selected.push({
          start: seg.start,
          end: Math.round((seg.start + remainder) * 1000) / 1000,
          duration: Math.round(remainder * 1000) / 1000
        });
        accumulated += remainder;
      }
      break;
    }
  }
  return selected;
}

function updateSegmentStats() {
  if (!currentAnalysis) return;
  const totalOrig = currentAnalysis.metadata ? currentAnalysis.metadata.duration : 0.0;
  const activeSegments = getActiveSegments();
  const totalActiveDur = activeSegments.reduce((sum, s) => sum + s.duration, 0);
  const segCount = activeSegments.length;

  statOrigDur.textContent = formatDuration(totalOrig);
  statCutDur.textContent = formatDuration(totalActiveDur);

  const limitStr = clipDurationSelect ? clipDurationSelect.value : "all";
  if (limitStr !== "all") {
    statSavedPct.textContent = `Capped to ${limitStr}s`;
  } else {
    statSavedPct.textContent = `${currentAnalysis.time_saved_percent}% (-${currentAnalysis.time_saved_seconds}s)`;
  }
  statCutsCount.textContent = `${segCount} segments`;
}

// 4. Aspect Ratio Switching
function setAspectRatio(ratio) {
  // Update button states
  document.querySelectorAll(".ratio-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-ratio") === ratio);
  });

  // Update Player frame class
  const classMap = {
    "9:16": "ratio-9-16",
    "16:9": "ratio-16-9",
    "1:1": "ratio-1-1",
    "4:5": "ratio-4-5"
  };

  playerWrapper.className = `player-wrapper ${classMap[ratio] || "ratio-9-16"}`;
}

// 5. Watermark Live Preview
function updateWatermarkPreview() {
  const isEnabled = logoToggle.checked;
  const position = logoPosSelect.value;
  const opacity = parseFloat(logoOpacitySlider.value);

  if (!isEnabled) {
    watermarkOverlay.style.display = "none";
    return;
  }

  watermarkOverlay.style.display = "block";
  watermarkOverlay.className = `watermark-overlay-preview ${position}`;
  watermarkOverlay.style.opacity = opacity;
}

// 6. Live Jump-Cut Preview Handler
// When enabled, skips silence blocks seamlessly during preview playback
previewVideo.addEventListener("timeupdate", () => {
  if (!jumpCutPreviewToggle.checked || !currentAnalysis) return;
  const silences = currentAnalysis.silence_intervals || [];
  const currTime = previewVideo.currentTime;

  for (const s of silences) {
    // If playhead lands inside a silence interval, immediately jump to end of silence
    if (currTime >= s.start && currTime < s.end - 0.05) {
      previewVideo.currentTime = s.end;
      break;
    }
  }
});

// 7. Event Listeners Setup
function setupEventListeners() {
  // Ratio buttons
  document.querySelectorAll(".ratio-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      setAspectRatio(btn.getAttribute("data-ratio"));
    });
  });

  // Slider label updates
  noiseDbSlider.addEventListener("input", (e) => {
    noiseDbVal.textContent = `${e.target.value} dB`;
  });
  minSilenceSlider.addEventListener("input", (e) => {
    minSilenceVal.textContent = `${e.target.value}s`;
  });
  paddingSlider.addEventListener("input", (e) => {
    paddingVal.textContent = `${e.target.value}s`;
  });
  logoOpacitySlider.addEventListener("input", (e) => {
    logoOpacityVal.textContent = `${Math.round(e.target.value * 100)}%`;
    updateWatermarkPreview();
  });

  // Watermark controls
  logoToggle.addEventListener("change", updateWatermarkPreview);
  logoPosSelect.addEventListener("change", updateWatermarkPreview);

  // Lower Third Input
  lowerThirdInput.addEventListener("input", (e) => {
    const txt = e.target.value.trim();
    if (txt) {
      lowerThirdPreview.style.display = "block";
      lowerThirdPreviewText.textContent = txt;
    } else {
      lowerThirdPreview.style.display = "none";
    }
  });

  // Subtitles toggle
  subtitlesToggle.addEventListener("change", (e) => {
    subStyleRow.style.display = e.target.checked ? "flex" : "none";
  });

  if (clipDurationSelect) {
    clipDurationSelect.addEventListener("change", updateSegmentStats);
  }

  // Scan & Recalculate
  btnScan.addEventListener("click", () => {
    const path = filePathInput.value.trim();
    if (!path) return alert("Please specify a video file path or upload one.");
    loadAndScanLocalFile(path);
  });

  btnRescan.addEventListener("click", () => {
    if (!currentFilePath) return alert("Please upload or scan a video first.");
    scanVideo(currentFilePath);
  });

  // Dropzone drag-and-drop & file picker
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", handleFileSelect);

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.style.borderColor = "var(--accent-gold)";
    dropzone.style.background = "rgba(251, 192, 45, 0.08)";
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.style.borderColor = "var(--border-color)";
    dropzone.style.background = "var(--bg-card)";
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.style.borderColor = "var(--border-color)";
    dropzone.style.background = "var(--bg-card)";
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });

  // Render button
  btnRender.addEventListener("click", startRender);

  // Folder open buttons
  btnOpenOutputsHeader.addEventListener("click", () => openFolder("output"));
  btnOpenUploadsHeader.addEventListener("click", () => openFolder("upload"));
  btnRevealFolder.addEventListener("click", () => openFolder("output"));
  btnRefreshExports.addEventListener("click", loadRecentExports);
}

// 8. File Upload & Loading
function handleFileSelect(e) {
  if (e.target.files && e.target.files.length > 0) {
    uploadFile(e.target.files[0]);
  }
}

async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);

  btnScan.disabled = true;
  dropzone.querySelector("p").innerHTML = `⏳ Uploading <strong>${file.name}</strong>...`;

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData
    });
    if (!res.ok) throw new Error("Upload failed");
    const data = await res.json();

    currentFilePath = data.file_path;
    filePathInput.value = data.file_path;

    // Load preview video via stream url
    previewVideo.src = data.stream_url || `/api/media-stream?file_path=${encodeURIComponent(data.file_path)}`;
    previewVideo.load();

    dropzone.querySelector("p").innerHTML = `✅ Loaded <strong>${file.name}</strong>`;
    scanVideo(data.file_path);
  } catch (err) {
    alert("Upload failed: " + err.message);
    dropzone.querySelector("p").innerHTML = `Drag & drop video here, or <strong style="color: var(--accent-gold);">Browse Files</strong>`;
  } finally {
    btnScan.disabled = false;
  }
}

function loadAndScanLocalFile(filePath) {
  currentFilePath = filePath;
  previewVideo.src = `/api/media-stream?file_path=${encodeURIComponent(filePath)}`;
  previewVideo.load();
  scanVideo(filePath);
}

// 9. Scan Video for Audio & Silences
async function scanVideo(filePath) {
  if (activeScanController) {
    activeScanController.abort();
  }
  activeScanController = new AbortController();

  btnRescan.disabled = true;
  btnRender.disabled = true;
  activeFileIndicator.textContent = "Analyzing audio & calculating cuts...";

  if (scanStatusBanner) {
    scanStatusBanner.style.display = "flex";
    scanStatusTitle.textContent = "Analyzing Audio & Detecting Silence Cuts...";
    scanStatusDesc.textContent = "Scanning speech pauses across audio track with FFmpeg...";
    const startTime = Date.now();
    if (scanTimerInterval) clearInterval(scanTimerInterval);
    scanTimerInterval = setInterval(() => {
      const elapsedSec = ((Date.now() - startTime) / 1000).toFixed(1);
      scanStatusTimer.textContent = `Elapsed: ${elapsedSec}s`;
    }, 200);
  }

  const body = {
    file_path: filePath,
    noise_db: parseFloat(noiseDbSlider.value),
    min_silence_duration: parseFloat(minSilenceSlider.value),
    padding_duration: parseFloat(paddingSlider.value)
  };

  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: activeScanController.signal
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Scan request failed");
    }

    const data = await res.json();
    currentAnalysis = data;

    const meta = data.metadata || {};
    activeFileIndicator.textContent = `${meta.filename || "Video"} (${meta.width}x${meta.height} @ ${meta.fps}fps)`;

    // Feed Timeline Canvas
    timeline.setData(data);

    // Update Stats according to selected duration scope
    updateSegmentStats();

    btnRender.disabled = false;
  } catch (err) {
    if (err.name === "AbortError") {
      console.log("Previous scan aborted.");
      return;
    }
    alert("Analysis Error: " + err.message);
    activeFileIndicator.textContent = "Failed to analyze video";
  } finally {
    if (scanTimerInterval) clearInterval(scanTimerInterval);
    if (scanStatusBanner) scanStatusBanner.style.display = "none";
    btnRescan.disabled = false;
  }
}

// 10. Start Render & Export Pipeline
async function startRender() {
  if (!currentFilePath || !currentAnalysis) {
    return alert("Please load and scan a video first.");
  }

  const activeSegments = getActiveSegments();
  if (activeSegments.length === 0) {
    return alert("No speech segments found to export.");
  }

  const activeRatioBtn = document.querySelector(".ratio-btn.active");
  const targetAspect = activeRatioBtn ? activeRatioBtn.getAttribute("data-ratio") : "9:16";

  const renderPayload = {
    source_file: currentFilePath,
    segments: activeSegments,
    target_aspect: targetAspect,
    framing_mode: framingModeSelect.value,
    logo_overlay: logoToggle.checked,
    logo_position: logoPosSelect.value,
    logo_opacity: parseFloat(logoOpacitySlider.value),
    logo_scale: 0.22,
    lower_third_text: lowerThirdInput.value.trim() || null,
    burn_subtitles: subtitlesToggle.checked,
    subtitle_style: subStyleSelect.value,
    normalize_audio: audioNormToggle.checked,
    target_lufs: -14.0
  };

  btnRender.disabled = true;
  progressBox.style.display = "flex";
  renderActions.style.display = "none";
  progressBarFill.style.width = "0%";
  progressStep.textContent = "Starting render pipeline...";
  progressPct.textContent = "0%";

  try {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(renderPayload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Render request failed");
    }

    const data = await res.json();
    activeJobId = data.job_id;
    pollJobStatus(activeJobId);
  } catch (err) {
    alert("Export Error: " + err.message);
    progressBox.style.display = "none";
    btnRender.disabled = false;
  }
}

// 11. Poll Job Status
function pollJobStatus(jobId) {
  if (jobPollTimer) clearInterval(jobPollTimer);

  jobPollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();

      const pct = job.progress || 0;
      progressBarFill.style.width = `${pct}%`;
      progressPct.textContent = `${pct}%`;
      progressStep.textContent = job.step || "Processing...";

      if (job.status === "completed") {
        clearInterval(jobPollTimer);
        btnRender.disabled = false;
        progressStep.textContent = "Render Complete!";
        progressBarFill.style.width = "100%";
        progressPct.textContent = "100%";

        btnDownload.href = `/api/jobs/${jobId}/download`;
        btnDownload.setAttribute("download", job.output_filename || "harvick_farms_video.mp4");
        renderActions.style.display = "flex";

        loadRecentExports();
      } else if (job.status === "error") {
        clearInterval(jobPollTimer);
        btnRender.disabled = false;
        progressStep.textContent = "Render Failed: " + (job.error || "Unknown error");
        progressBarFill.style.background = "#ef4444";
      }
    } catch (e) {
      console.warn("Poll status failed:", e);
    }
  }, 600);
}

// 12. Recent Exports Management
async function loadRecentExports() {
  try {
    const res = await fetch("/api/outputs");
    if (!res.ok) return;
    const outputs = await res.json();

    if (!outputs || outputs.length === 0) {
      exportList.innerHTML = `<div style="color: var(--text-dim); font-size: 0.8rem; text-align: center; padding: 12px;">No exports yet. Render a video to see it here!</div>`;
      return;
    }

    exportList.innerHTML = "";
    outputs.forEach(item => {
      const el = document.createElement("div");
      el.className = "export-item";

      const dateStr = new Date(item.modified * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

      el.innerHTML = `
        <div style="display: flex; flex-direction: column; overflow: hidden;">
          <span class="item-title" title="${item.name}">${item.name}</span>
          <span style="font-size: 0.7rem; color: var(--text-dim);">${item.size_mb} MB • ${dateStr}</span>
        </div>
        <div class="item-actions">
          <button class="icon-btn btn-play-export" title="Play exported video">▶️ Play</button>
          <a href="${item.url}" download class="icon-btn" style="text-decoration: none;" title="Download MP4">⬇️</a>
        </div>
      `;

      el.querySelector(".btn-play-export").addEventListener("click", () => {
        previewVideo.src = item.url;
        previewVideo.load();
        previewVideo.play().catch(() => {});
        activeFileIndicator.textContent = `Playing Export: ${item.name}`;
      });

      exportList.appendChild(el);
    });
  } catch (e) {
    console.warn("Error loading exports:", e);
  }
}

// 13. Open Folder in Windows Explorer
async function openFolder(folderType) {
  try {
    await fetch(`/api/open-folder?folder_type=${folderType}`, { method: "POST" });
  } catch (e) {
    console.warn("Failed to open folder:", e);
  }
}
