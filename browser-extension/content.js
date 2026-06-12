/*
 * Chrome extension content script
 * - Tracks the last <video> element the user interacted with (click/contextmenu)
 * - Exposes it to the background service worker for context-menu downloads
 */

(() => {
  "use strict";

  /** @type {{ pageUrl: string, srcUrl: string, currentTime: number, timestamp: number } | null} */
  let lastVideo = null;

  /** Read video src. */
  function readVideoSrc(video) {
    if (!video) return "";
    return video.currentSrc || video.src || "";
  }

  /** Updates last video. */
  function updateLastVideo(video) {
    const pageUrl = String(window.location.href);
    const srcUrl = readVideoSrc(video);

    lastVideo = {
      pageUrl,
      srcUrl,
      currentTime: Number.isFinite(video?.currentTime) ? video.currentTime : 0,
      timestamp: Date.now(),
    };

    try {
      chrome.storage?.session?.set({ lastVideo });
    } catch {
      // ignore (e.g., storage not available)
    }
  }

  /** Maybe capture video from event. */
  function maybeCaptureVideoFromEvent(event) {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const video = target.closest("video");
    if (!video) return;

    updateLastVideo(video);
  }

  document.addEventListener("pointerdown", maybeCaptureVideoFromEvent, true);
  document.addEventListener("click", maybeCaptureVideoFromEvent, true);
  document.addEventListener("contextmenu", maybeCaptureVideoFromEvent, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message !== "object") return;

    if (message.type === "getLastVideo") {
      const respond = async () => {
        if (lastVideo) return lastVideo;

        try {
          const stored = await chrome.storage?.session?.get("lastVideo");
          return stored?.lastVideo || null;
        } catch {
          return null;
        }
      };

      respond().then((value) => sendResponse(value));
      return true;
    }
  });
})();

