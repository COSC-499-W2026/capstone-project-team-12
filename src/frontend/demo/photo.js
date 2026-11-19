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
// Not done yet because the backend doesn't support it: the Idempotency-Key header.
import { h } from "./ui.js";

const MAX_EDGE = 1600; // px, long edge (spec, Stage 2)
const JPEG_QUALITY = 0.88;
const MAX_BYTES = 5 * 1024 * 1024; // the server's default limit
const RESULT_MS = 1600; // how long a result stays on screen before a retake or the next location

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
  tooLarge: {
    ja: "写真が大きすぎます。もう一度撮ってください。",
    en: "The photo is too large. Please take it again.",
  },
};

const RESULTS = {
  match: { symbol: "✓", label: "Match", vibrate: [60] },
  unclear: { symbol: "?", label: "Unclear", vibrate: [60, 80, 60] },
  no_match: { symbol: "✗", label: "No match", vibrate: [200] },
};

export function takePhoto({ gameId, api, audio, container, attemptsLeft }) {
  return new Promise((resolve, reject) => {
    const view = new Viewfinder(container, attemptsLeft);
    let done = false;

    const finish = (settle, value) => {
      if (done) return;
      done = true;
      view.close();
      settle(value);
    };

    // game.js replaces the game screen on pause, reset, quit or a new route: the camera must not stay on.
    // The promise then stays pending; game.js has already moved on and ignores it.
    view.onDetached = () => {
      done = true;
      view.close();
    };

    view.onShutter = async () => {
      let blob;
      try {
        blob = await view.capture();
      } catch {
        view.live();
        view.showMessage(TEXT.unavailable);
        return;
      }
      if (blob.size > MAX_BYTES) {
        view.showMessage(TEXT.tooLarge);
        view.live();
        return;
      }
      const form = new FormData();
      form.append("photo", blob, "photo.jpg");
      let response;
      try {
        response = await api.upload(`/games/${gameId}/verification/photo`, form);
      } catch (error) {
        finish(reject, error);
        return;
      }
      if (done) return;
      audio.playAll(response.feedback);
      await view.showResult(response.result, response.attempts_remaining);
      if (done) return;
      if (response.location_result) finish(resolve, response);
      else view.live();
    };

    view.start();
  });
}

// The camera view: live video, framing guide, shutter, attempt counter and the result overlay.
class Viewfinder {
  constructor(container, attemptsLeft) {
    this.stream = null;
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
    this.retry = h("button", { class: "btn big", type: "button", hidden: true, onclick: () => this.start() }, "Try again");
    this.setAttempts(attemptsLeft);

    this.camera = h("div", { class: "camera" },
      h("div", { class: "cam-frame" }, this.video, this.still, this.guide, this.spinner, this.result),
      h("div", { class: "cam-bar" }, this.attempts, this.shutter),
    );
    container.replaceChildren(this.camera, this.message, this.retry);

    // iOS stops the camera while the page is in the background: restart it in the live view
    // (during a check, live() restarts it afterwards)
    this.onVisible = () => {
      if (!document.hidden && this.still.hidden && this.ended()) this.start();
    };
    document.addEventListener("visibilitychange", this.onVisible);
    this.watch = setInterval(() => {
      if (!this.camera.isConnected) this.onDetached();
    }, 500);
  }

  async start() {
    this.stopStream();
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
      this.video.srcObject = stream;
      await this.video.play();
      if (!this.video.videoWidth) await new Promise((ok) => this.video.addEventListener("loadedmetadata", ok, { once: true }));
      this.live();
    } catch (error) {
      if (this.closed) return;
      this.showMessage(error?.name === "NotAllowedError" || error?.name === "SecurityError" ? TEXT.denied : TEXT.unavailable);
      this.retry.hidden = false;
    }
  }

  ended() {
    return Boolean(this.stream) && this.stream.getVideoTracks().every((t) => t.readyState === "ended");
  }

  // Back to the live view, ready for a (re)take
  live() {
    if (this.ended()) {
      this.start();
      return;
    }
    this.still.hidden = true;
    this.still.removeAttribute("src");
    this.guide.hidden = false;
    this.spinner.hidden = true;
    this.result.hidden = true;
    this.shutter.disabled = !this.stream;
  }

  shoot() {
    if (this.shutter.disabled) return;
    this.shutter.disabled = true;
    this.message.replaceChildren();
    this.onShutter();
  }

  // The current frame, long edge at most 1600 px, as JPEG. A canvas frame carries no EXIF,
  // so the upload isn't flagged not_in_app_capture.
  async capture() {
    const { videoWidth: w, videoHeight: ht } = this.video;
    if (!w || !ht) throw new Error("No camera frame");
    const scale = Math.min(1, MAX_EDGE / Math.max(w, ht));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(ht * scale);
    canvas.getContext("2d").drawImage(this.video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", JPEG_QUALITY));
    if (!blob) throw new Error("No JPEG");
    // freeze the picture that is being checked, on top of the video (a hidden video can stop on iOS)
    const url = URL.createObjectURL(blob);
    this.still.onload = () => URL.revokeObjectURL(url);
    this.still.src = url;
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

  showMessage(text) {
    this.message.replaceChildren(h("div", { class: "notice" }, h("p", { class: "bi-ja" }, text.ja), h("p", { class: "bi-en" }, text.en)));
  }

  stopStream() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  close() {
    this.closed = true;
    this.stopStream();
    clearInterval(this.watch);
    document.removeEventListener("visibilitychange", this.onVisible);
  }
}
