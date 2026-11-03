// Video page: start playback, resume where the person left off, and report progress so the
// server can mark the video as watched.
(function () {
  var root = document.getElementById("player");
  var video = document.getElementById("video");
  if (!root || !video) return;

  var overlay = document.getElementById("start-overlay");
  var startButton = document.getElementById("start-button");
  var endPanel = document.getElementById("end-panel");
  var errorBox = document.getElementById("error-box");
  var resumeNote = document.getElementById("resume-note");

  var progressUrl = root.getAttribute("data-progress-url");
  var csrf = root.getAttribute("data-csrf");
  var startAt = parseFloat(root.getAttribute("data-start")) || 0;
  var lastSent = -100;

  function report(ended) {
    var duration = video.duration;
    if (!isFinite(duration) || duration <= 0) return;
    lastSent = video.currentTime;
    fetch(progressUrl, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ position: video.currentTime, duration: duration, ended: !!ended })
    }).catch(function () { /* offline for a moment: the next update will catch up */ });
  }

  function leaveFullscreen() {
    if (document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen();
    } else if (video.webkitDisplayingFullscreen && video.webkitExitFullscreen) {
      video.webkitExitFullscreen();
    }
  }

  video.addEventListener("loadedmetadata", function () {
    if (startAt > 0 && startAt < video.duration - 5) {
      video.currentTime = startAt;
      resumeNote.hidden = false;
    }
    // Starting on its own is a bonus; if the browser refuses, the big Play button is there.
    var attempt = video.play();
    if (attempt && attempt.catch) attempt.catch(function () {});
  });

  startButton.addEventListener("click", function () {
    var attempt = video.play();
    if (attempt && attempt.catch) attempt.catch(function () {});
  });

  video.addEventListener("play", function () { overlay.hidden = true; });

  video.addEventListener("timeupdate", function () {
    if (!video.paused && Math.abs(video.currentTime - lastSent) >= 10) report(false);
  });

  video.addEventListener("pause", function () {
    if (!video.ended) report(false);
  });

  video.addEventListener("ended", function () {
    report(true);
    leaveFullscreen();
    resumeNote.hidden = true;
    endPanel.hidden = false;
    endPanel.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  video.addEventListener("error", function () {
    overlay.hidden = true;
    errorBox.hidden = false;
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden" && !video.paused) report(false);
  });
  window.addEventListener("pagehide", function () {
    if (video.currentTime > 0 && !video.ended) report(false);
  });
})();
