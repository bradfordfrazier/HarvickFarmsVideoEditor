class VideoTimeline {
  constructor(canvasId, playheadId, videoElement) {
    this.canvas = document.getElementById(canvasId);
    this.playhead = document.getElementById(playheadId);
    this.video = videoElement;
    this.ctx = this.canvas.getContext("2d");

    this.duration = 0.0;
    this.speechSegments = [];
    this.silenceIntervals = [];
    this.waveform = [];

    this.isDragging = false;
    this.initEvents();
  }

  setData(data) {
    this.duration = data.metadata ? data.metadata.duration : 0.0;
    this.speechSegments = data.speech_segments || [];
    this.silenceIntervals = data.silence_intervals || [];
    this.waveform = data.waveform || [];
    this.resize();
    this.render();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.scale(dpr, dpr);
    this.width = rect.width;
    this.height = rect.height;
  }

  initEvents() {
    window.addEventListener("resize", () => {
      this.resize();
      this.render();
    });

    if (this.video) {
      this.video.addEventListener("timeupdate", () => {
        this.updatePlayhead();
      });
    }

    const container = this.canvas.parentElement;
    const seek = (e) => {
      if (this.duration <= 0) return;
      const rect = this.canvas.getBoundingClientRect();
      const clickX = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const targetTime = (clickX / rect.width) * this.duration;
      if (this.video) {
        this.video.currentTime = targetTime;
      }
      this.updatePlayheadPosition(clickX);
    };

    container.addEventListener("mousedown", (e) => {
      this.isDragging = true;
      seek(e);
    });

    window.addEventListener("mousemove", (e) => {
      if (this.isDragging) seek(e);
    });

    window.addEventListener("mouseup", () => {
      this.isDragging = false;
    });
  }

  updatePlayhead() {
    if (!this.video || this.duration <= 0 || !this.playhead) return;
    const pct = this.video.currentTime / this.duration;
    const x = pct * this.width;
    this.updatePlayheadPosition(x);
  }

  updatePlayheadPosition(x) {
    if (this.playhead) {
      this.playhead.style.left = `${x}px`;
    }
  }

  render() {
    if (!this.ctx || this.width <= 0) return;
    const ctx = this.ctx;
    const w = this.width;
    const h = this.height;

    ctx.clearRect(0, 0, w, h);

    if (this.duration <= 0) {
      // Empty placeholder
      ctx.fillStyle = "#1e293b";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Load or analyze a video to view audio timeline & cuts", w / 2, h / 2);
      return;
    }

    // 1. Draw Silence background (Red tint)
    for (const sil of this.silenceIntervals) {
      const startX = (sil.start / this.duration) * w;
      const endX = (sil.end / this.duration) * w;
      const segWidth = Math.max(2, endX - startX);

      ctx.fillStyle = "rgba(239, 68, 68, 0.25)";
      ctx.fillRect(startX, 0, segWidth, h);

      // Top red border
      ctx.fillStyle = "rgba(239, 68, 68, 0.8)";
      ctx.fillRect(startX, 0, segWidth, 3);
    }

    // 2. Draw Kept Speech segments (Green tint & border)
    for (const seg of this.speechSegments) {
      const startX = (seg.start / this.duration) * w;
      const endX = (seg.end / this.duration) * w;
      const segWidth = Math.max(2, endX - startX);

      ctx.fillStyle = "rgba(46, 125, 50, 0.35)";
      ctx.fillRect(startX, 0, segWidth, h);

      // Top green accent line
      ctx.fillStyle = "#4caf50";
      ctx.fillRect(startX, 0, segWidth, 3);
    }

    // 3. Draw Waveform Peaks
    if (this.waveform && this.waveform.length > 0) {
      const numPoints = this.waveform.length;
      const barWidth = w / numPoints;
      const centerY = h / 2 + 10;
      const maxAmp = (h - 25) / 2;

      ctx.fillStyle = "#81c784";
      for (let i = 0; i < numPoints; i++) {
        const amp = this.waveform[i];
        const barHeight = Math.max(1, amp * maxAmp);
        const x = i * barWidth;
        
        ctx.fillRect(x, centerY - barHeight, Math.max(1, barWidth - 1), barHeight * 2);
      }
    }

    // 4. Time ticks at top
    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "left";
    const numTicks = 6;
    for (let i = 0; i <= numTicks; i++) {
      const t = (i / numTicks) * this.duration;
      const x = (i / numTicks) * w;
      const mins = Math.floor(t / 60);
      const secs = Math.floor(t % 60);
      const timeStr = `${mins}:${secs.toString().padStart(2, "0")}`;
      ctx.fillText(timeStr, Math.min(w - 25, Math.max(2, x)), 14);
    }
  }
}
