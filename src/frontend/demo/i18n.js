// Bilingual key prompts: the target language first, English underneath (spec, Localisation).
// Japanese drafts by the team, to be checked by the client.
const TEXT = {
  permissions: {
    ja: "ゲームでは位置情報とカメラを使います。次の画面で許可してください。",
    en: "The game uses your location and camera. Please allow both on the next prompt.",
  },
  gpsUnavailable: {
    ja: "GPSが使えません。しばらくするとGPSなしで続けられます。",
    en: "GPS is unavailable. You can continue without GPS shortly.",
  },
  confirmReset: {
    ja: "ゲームを最初からやり直しますか？",
    en: "Start this game over from the beginning?",
  },
  confirmQuit: {
    ja: "ゲームを終了しますか？",
    en: "Quit this game?",
  },
  paused: {
    ja: "一時停止中",
    en: "Paused",
  },
};

const ERRORS = {
  SESSION_NOT_ACTIVE: { ja: "このゲームは今は進められません。", en: "This game can't continue right now." },
  INVALID_STAGE: { ja: "今はこの操作はできません。", en: "That isn't possible at this step." },
  STATE_CHANGED: { ja: "ゲームが更新されました。もう一度お試しください。", en: "The game changed. Please try again." },
  GPS_OVERRIDE_NOT_ALLOWED: { ja: "まだGPSなしでは続けられません。", en: "You can't continue without GPS yet." },
  NOT_ENOUGH_CONTENT: { ja: "遊べる場所が足りません。", en: "There aren't enough playable locations." },
  RESET_LIMIT_REACHED: { ja: "これ以上やり直せません。", en: "No resets left." },
  NOT_WITHIN_RADIUS: { ja: "まだ目的地に着いていません。", en: "You aren't at the location yet." },
  ATTEMPTS_EXHAUSTED: { ja: "写真の回数が残っていません。", en: "No photo attempts are left." },
  RATE_LIMITED: { ja: "少し待ってからもう一度お試しください。", en: "Please wait a moment and try again." },
};

export function bilingual(key) {
  return TEXT[key];
}

// Game errors are shown in both languages; anything else falls back to the API's own message.
export function errorText(error) {
  return ERRORS[error.code] || { ja: "", en: error.message || "Something went wrong." };
}
