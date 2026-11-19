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
import { h } from "./ui.js";

export async function takePhoto({ container }) {
  container.replaceChildren(h("p", { class: "card muted" }, "The photo module is on its way (Eddy)."));
  throw new Error("Photo module not ready yet");
}
