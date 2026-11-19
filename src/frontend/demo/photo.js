// Photo module (Eddy): Stage 2 camera, upload and result display.
//
// Contract with game.js:
//   takePhoto({ gameId, api, audio, container, attemptsLeft }) -> Promise<PhotoResponse>
//
// - container: render the camera and results inside it; game.js clears it afterwards.
// - Live in-app camera only (getUserMedia, rear camera) with a framing guide for the number sign;
//   no gallery upload and no file-picker fallback (spec, Stage 2).
// - Resize to at most 1600 px as JPEG before upload, then send it with
//   api.upload(`/games/${gameId}/verification/photo`, formData) under the field "photo";
//   the server refuses files over 5 MB.
// - Show each result (match / unclear / no_match) with an animation, play response.feedback
//   (a list of {code, audio_url}) with audio.playAll(...), and show response.attempts_remaining.
// - While response.location_result is null the location is still open: let the student retake.
// - Resolve with the last response once location_result isn't null (completed or failed);
//   game.js then goes to response.next, or to the reward screen when next is null.
//   In the demo the student quits from next.
// - Let other API errors throw (the ApiError from api.js); game.js shows them.
//
// The whole frame is uploaded, not only the part inside the guide: OCR reads a sign with a margin
// around it more confidently than one that fills the picture (A10 plan, OCR findings). The guide
// therefore covers the middle of the view only. The result is shown as a symbol, not as text
// (spec, Gameplay Screen Rules: on-screen text is limited to labels, the attempt counter and
// errors), and the response never holds the room number or the OCR text anyway.
// Errors that use no attempt (no network, a refused file, rate limit, server error) keep the
// camera open for a retake; any other error closes it and is thrown. Without the Idempotency-Key
// (not in the backend yet), an upload lost on the network may still have used an attempt.
import { ApiError } from "./api.js";
import { errorText } from "./i18n.js";
import { h } from "./ui.js";

const MAX_EDGE = 1600; // px, long edge (spec, Stage 2)
const JPEG_QUALITY = 0.88;
const RESULT_MS = 1600; // how long a result stays on screen before a retake or the next location
const FIRST_FRAME_MS = 5000; // give up waiting for the camera's first frame after this

// Bilingual camera messages (spec, Localisation): Japanese first, English underneath.
// Drafts by the team, checked with the other Japanese texts before the demo.
const TEXT = {
  denied: {
    ja: "カメラが使えません。ブラウザの設定でこのサイトのカメラを許可してから、「もう一度」を押してください。",
    en: "Camera access is blocked. Allow the camera for this site in your browser settings (Safari: aA › Website Settings › Camera; Chrome: the lock icon › Permissions), then tap Try again.",
  },
  unavailable: {
    ja: "カメラを起動できません。SafariかChromeでこのページを開いてください。",
    en: "The camera can't start. Open this page in Safari or Chrome (over https).",
  },
  noFrame: {
    ja: "カメラの映像が止まっています。少し待ってからもう一度撮ってください。",
    en: "The camera picture has stopped. Wait a moment, then take the photo again.",
  },
};

const RESULTS = {
  match: { symbol: "✓", label: "Match", vibrate: [60] },
  unclear: { symbol: "?", label: "Unclear", vibrate: [60, 80, 60] },
  no_match: { symbol: "✗", label: "No match", vibrate: [200] },
};

// Upload errors that use no attempt (spec, Stage 2): the student can simply take the photo again
function canRetake(error) {
  if (!(error instanceof ApiError)) return true; // fetch failed: no network
  return error.status === 413 || error.status === 415 || error.status === 429 || error.status >= 500;
}

export function takePhoto({ gameId, api, audio, container, attemptsLeft }) {
  return new Promise((resolve, reject) => {
    const view = new Viewfinder(container, attemptsLeft);
    let done = false;

    const finish = (settle, value) => {
      if (done) return;
      done = true;
      view.close();
      if (settle === reject) container.replaceChildren(); // game.js clears it only after a result
      settle(value);
    };

    // game.js replaces the game screen on reset, quit, resume or a new route: the camera must not
    // stay on. The promise then stays pending; game.js has already moved on and ignores it.
    // Pause only adds an overlay, so the camera stays on until resume (to do in game.js).
    view.onDetached = () => {
      done = true;
      view.close();
    };

    const shoot = async () => {
      let blob;
      try {
        blob = await view.capture();
      } catch {
        view.live();
        view.showMessage(TEXT.noFrame);
        return;
      }
      const form = new FormData();
      form.append("photo", blob, "photo.jpg");
      let response;
      try {
        response = await api.upload(`/games/${gameId}/verification/photo`, form);
      } catch (error) {
        if (done) return;
        if (!canRetake(error)) throw error;
        view.live();
        view.showMessage(errorText(error));
        return;
      }
      if (done) return;
      audio.playAll(response.feedback);
      await view.showResult(response.result, response.attempts_remaining);
      if (done) return;
      if (response.location_result) finish(resolve, response);
      else view.live();
    };

    // Anything unexpected ends the photo step with an error instead of leaving the shutter off
    view.onShutter = () => shoot().catch((error) => finish(reject, error));

    view.start();
  });
}

// Resolves once the video has a frame size, or rejects after `ms`
function firstFrame(video, ms) {
  if (video.videoWidth) return Promise.resolve();
  const events = ["loadedmetadata", "loadeddata", "resize", "playing"];
  return new Promise((ok, fail) => {
    const stop = () => {
      clearTimeout(timer);
      events.forEach((name) => video.removeEventListener(name, check));
    };
    const check = () => {
      if (!video.videoWidth) return;
      stop();
      ok();
    };
    const timer = setTimeout(() => {
      stop();
      fail(new Error("No camera frame"));
    }, ms);
    events.forEach((name) => video.addEventListener(name, check));
  });
}

// The camera view: live video, framing guide, shutter, attempt counter and the result overlay.
class Viewfinder {
  constructor(container, attemptsLeft) {
    this.stream = null;
    this.stillUrl = null;
    this.checking = false; // a photo is being sent or its result shown
    this.closed = false;
    this.onShutter = () => {};
    this.onDetached = () => {};

    this.video = h("video", { autoplay: true, muted: true, playsinline: true, "aria-hidden": "true" });
    this.video.muted = true; // the attribute alone doesn't stop iOS asking for audio playback rights
    this.still = h("img", { class: "cam-still", alt: "", hidden: true });
    this.guide = h("div", { class: "cam-guide", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"));
    this.result = h("div", { class: "cam-result", hidden: true, role: "status" });
    this.spinner = h("div", { class: "cam-spinner", hidden: true, role: "status", "aria-label": "Checking the photo" });
    this.attempts = h("span", { class: "pill cam-attempts" });
    this.shutter = h("button", { class: "cam-shutter", type: "button", disabled: true, "aria-label": "Take photo", onclick: () => this.shoot() });
    this.message = h("div", {});
    this.retry = h("button", { class: "btn big", type: "button", hidden: true, onclick: () => this.start() }, "もう一度 · Try again");
    this.setAttempts(attemptsLeft);

    this.camera = h("div", { class: "camera" },
      h("div", { class: "cam-frame" }, this.video, this.still, this.guide, this.spinner, this.result),
      h("div", { class: "cam-bar" }, this.attempts, this.shutter),
    );
    container.replaceChildren(this.camera, this.message, this.retry);

    // iOS stops the camera while the page is in the background: restart it in the live view
    // (during a check, live() restarts it afterwards)
    this.onVisible = () => {
      if (!document.hidden && !this.checking && this.ended()) this.start();
    };
    document.addEventListener("visibilitychange", this.onVisible);
    this.watch = setInterval(() => {
      if (!this.camera.isConnected) this.onDetached();
    }, 500);
  }

  async start() {
    this.stopStream();
    this.clearOverlay();
    this.checking = false;
    this.message.replaceChildren();
    this.retry.hidden = true;
    this.shutter.disabled = true;
    if (!navigator.mediaDevices?.getUserMedia) {
      // also the case on a plain-http page other than localhost
      this.showMessage(TEXT.unavailable);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      if (this.closed) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      this.stream = stream;
      this.watchTracks(stream);
      this.video.srcObject = stream;
      await this.video.play();
      await firstFrame(this.video, FIRST_FRAME_MS);
      if (this.closed) return;
      this.live();
    } catch (error) {
      this.stopStream();
      if (this.closed) return;
      this.showMessage(error?.name === "NotAllowedError" || error?.name === "SecurityError" ? TEXT.denied : TEXT.unavailable);
      this.retry.hidden = false;
    }
  }

  // A muted track (iOS: a call, Control Center) shows a frozen picture: no shutter until it's back.
  // An ended track is restarted, once the page is visible and no photo is being checked.
  watchTracks(stream) {
    for (const track of stream.getVideoTracks()) {
      track.addEventListener("mute", () => {
        if (this.stream === stream) this.shutter.disabled = true;
      });
      track.addEventListener("unmute", () => {
        if (this.stream === stream && !this.checking) this.shutter.disabled = !this.usable();
      });
      track.addEventListener("ended", () => {
        if (this.stream === stream && !this.closed && !this.checking && !document.hidden) this.start();
      });
    }
  }

  usable() {
    return Boolean(this.stream) && this.stream.getVideoTracks().some((t) => t.readyState === "live" && !t.muted);
  }

  ended() {
    return Boolean(this.stream) && this.stream.getVideoTracks().every((t) => t.readyState === "ended");
  }

  clearOverlay() {
    this.still.hidden = true;
    this.still.removeAttribute("src");
    if (this.stillUrl) URL.revokeObjectURL(this.stillUrl);
    this.stillUrl = null;
    this.guide.hidden = false;
    this.spinner.hidden = true;
    this.result.hidden = true;
  }

  // Back to the live view, ready for a (re)take
  live() {
    if (this.ended()) {
      this.start();
      return;
    }
    this.clearOverlay();
    this.checking = false;
    this.shutter.disabled = !this.usable();
  }

  shoot() {
    if (this.shutter.disabled || !this.usable()) return;
    this.shutter.disabled = true;
    this.checking = true;
    this.message.replaceChildren();
    this.onShutter();
  }

  // The current frame, long edge at most 1600 px, as JPEG. A canvas frame carries no EXIF,
  // so the upload isn't flagged not_in_app_capture.
  async capture() {
    const { videoWidth: w, videoHeight: ht } = this.video;
    if (!w || !ht || !this.usable()) throw new Error("No camera frame");
    const scale = Math.min(1, MAX_EDGE / Math.max(w, ht));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(ht * scale);
    canvas.getContext("2d").drawImage(this.video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", JPEG_QUALITY));
    if (!blob) throw new Error("No JPEG");
    // freeze the picture that is being checked, on top of the video (a hidden video can stop on iOS)
    this.stillUrl = URL.createObjectURL(blob);
    this.still.src = this.stillUrl;
    this.still.hidden = false;
    this.guide.hidden = true;
    this.spinner.hidden = false;
    return blob;
  }

  async showResult(result, attemptsLeft) {
    const shown = RESULTS[result] || RESULTS.no_match;
    this.spinner.hidden = true;
    this.result.className = `cam-result ${result}`;
    this.result.setAttribute("aria-label", shown.label);
    this.result.replaceChildren(h("b", { "aria-hidden": "true" }, shown.symbol));
    this.result.hidden = false;
    this.setAttempts(attemptsLeft);
    try {
      navigator.vibrate?.(shown.vibrate);
    } catch {
      // not supported (iOS Safari)
    }
    await new Promise((ok) => setTimeout(ok, RESULT_MS));
  }

  setAttempts(count) {
    this.attempts.textContent = `Attempts left: ${count}`;
  }

  // { ja, en }; an error the page has no Japanese for (errorText) shows the English line only
  showMessage(text) {
    this.message.replaceChildren(h("div", { class: "notice" }, text.ja && h("p", { class: "bi-ja" }, text.ja), h("p", { class: "bi-en" }, text.en)));
  }

  stopStream() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  close() {
    this.closed = true;
    this.stopStream();
    this.clearOverlay();
    clearInterval(this.watch);
    document.removeEventListener("visibilitychange", this.onVisible);
  }
}
