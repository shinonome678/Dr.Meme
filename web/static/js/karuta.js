const gameId = window.location.pathname.split("/").filter(Boolean).pop();
const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
const wsScheme = window.location.protocol === "https:" ? "wss:" : "ws:";

let ws = null;
let appState = null;
let activeRoundNo = 0;
let keywordStartedAt = 0;
let inputEnabled = false;
let actedRoundNo = 0;
let boardSignature = "";
let imageLoadSentFor = "";
let listPage = 1;
let closeTimer = 0;
let audioContext = null;

const statusLine = document.getElementById("statusLine");
const connectionBadge = document.getElementById("connectionBadge");
const playersEl = document.getElementById("players");
const boardEl = document.getElementById("board");
const resultsEl = document.getElementById("results");
const roundLabel = document.getElementById("roundLabel");
const penaltyLabel = document.getElementById("penaltyLabel");
const toastEl = document.getElementById("toast");
const listButton = document.getElementById("listButton");
const listDialog = document.getElementById("listDialog");
const memeRows = document.getElementById("memeRows");
const memeSearch = document.getElementById("memeSearch");
const memeSearchButton = document.getElementById("memeSearchButton");
const prevPage = document.getElementById("prevPage");
const nextPage = document.getElementById("nextPage");
const pageLabel = document.getElementById("pageLabel");
const closeButton = document.getElementById("closeButton");
const confirmDialog = document.getElementById("confirmDialog");
const disbandButton = document.getElementById("disbandButton");
const cancelDisbandButton = document.getElementById("cancelDisbandButton");
const midgameDialog = document.getElementById("midgameDialog");
const midgameText = document.getElementById("midgameText");
const midgameAckButton = document.getElementById("midgameAckButton");

function connect() {
  ws = new WebSocket(`${wsScheme}//${window.location.host}/karuta/ws/${gameId}?token=${encodeURIComponent(token)}`);

  ws.addEventListener("open", () => {
    connectionBadge.textContent = "LIVE";
    connectionBadge.classList.add("live");
  });

  ws.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    await handleMessage(message);
  });

  ws.addEventListener("close", () => {
    connectionBadge.textContent = "OFF";
    connectionBadge.classList.remove("live");
    statusLine.textContent = "再接続中...";
    window.setTimeout(connect, 1200);
  });
}

async function handleMessage(message) {
  if (message.type === "state") {
    appState = message;
    render();
    maybePreloadImages();
    return;
  }
  if (message.type === "countdown") {
    showToast(String(message.value));
    statusLine.textContent = `${message.value}`;
    return;
  }
  if (message.type === "round_started") {
    activeRoundNo = message.round_no;
    actedRoundNo = 0;
    inputEnabled = false;
    statusLine.textContent = `第${message.round_no}戦`;
    renderRound(message);
    await playRound(message);
    return;
  }
  if (message.type === "round_active") {
    if (activeRoundNo === Number(message.round_no || 0) && keywordStartedAt <= 0) {
      keywordStartedAt = performance.now();
    }
    inputEnabled = true;
    return;
  }
  if (message.type === "round_result") {
    inputEnabled = false;
    showToast(`${message.winner_name} さんが獲得`);
    return;
  }
  if (message.type === "mistake") {
    if (appState && message.user_id === appState.self_user_id) {
      inputEnabled = false;
      showToast("せっかちニキ");
    }
    return;
  }
  if (message.type === "midgame_pause") {
    inputEnabled = false;
    midgameText.textContent = `あなたの獲得枚数: ${message.your_cards_won}`;
    showDialog(midgameDialog);
    return;
  }
  if (message.type === "game_finished") {
    inputEnabled = false;
    if (appState) {
      appState.state = message.reason === "disbanded" ? "DISBANDED" : "FINISHED";
      appState.results = message.results || [];
      appState.end_reason = message.reason;
      appState.reading_update_count = message.reading_update_count || 0;
      render();
    }
    showToast(finishReasonText(message.reason));
    return;
  }
  if (message.type === "reading_updated") {
    showToast("読み方を保存候補に入れました");
    if (listDialog.open) {
      fetchMemeList();
    }
  }
}

function send(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  ws.send(JSON.stringify(payload));
}

function render() {
  if (!appState) {
    return;
  }
  statusLine.textContent = stateText(appState);
  renderPlayers();
  renderRound(appState.round || {});
  renderBoard();
  renderResults();
}

function renderPlayers() {
  playersEl.replaceChildren();
  for (const player of appState.players || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = [
      "player",
      player.is_self ? "self" : "",
      player.ready ? "ready" : "",
      player.connected ? "" : "disconnected",
    ].filter(Boolean).join(" ");
    button.disabled = !player.is_self || !["LOBBY", "LOADING", "FINISHED"].includes(appState.state);
    button.addEventListener("click", async () => {
      await unlockAudio();
      await unlockSpeech();
      send({ type: "ready" });
    });

    const img = document.createElement("img");
    img.className = "avatar";
    img.src = player.avatar_url;
    img.alt = "";
    button.appendChild(img);

    const name = document.createElement("span");
    name.className = "player-name";
    name.textContent = player.display_name;
    button.appendChild(name);

    const mark = document.createElement("span");
    mark.className = "ready-mark";
    mark.textContent = player.ready ? "Ready" : "";
    button.appendChild(mark);

    playersEl.appendChild(button);
  }
}

function renderRound(round) {
  const roundNo = Number(round.round_no || 0);
  roundLabel.textContent = `第${roundNo}戦 / ${round.total_rounds || 49}`;
  const self = selfPlayer();
  if (self && self.penalty) {
    penaltyLabel.textContent = "せっかちニキ中";
  } else {
    penaltyLabel.textContent = "";
  }
}

function renderBoard() {
  const cards = appState.cards || [];
  const fragment = document.createDocumentFragment();
  for (const card of cards) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `card${card.remaining ? "" : " gone"}`;
    button.disabled = !card.remaining;
    button.dataset.memeId = String(card.id);
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      clickCard(card.id);
    });

    const img = document.createElement("img");
    img.src = card.image_url;
    img.alt = "札";
    img.draggable = false;
    button.appendChild(img);
    fragment.appendChild(button);
  }
  boardEl.replaceChildren(fragment);
}

function renderResults() {
  const finished = ["FINISHED", "DISBANDED"].includes(appState.state);
  resultsEl.hidden = !finished;
  if (!finished) {
    resultsEl.replaceChildren();
    return;
  }

  const homeButton = document.createElement("button");
  homeButton.type = "button";
  homeButton.className = "primary-button";
  homeButton.textContent = "ホームへ戻る";
  homeButton.disabled = appState.state === "DISBANDED";
  homeButton.addEventListener("click", () => send({ type: "return_home" }));

  const heading = document.createElement("h2");
  heading.textContent = finishReasonText(appState.end_reason);

  const updateText = document.createElement("p");
  updateText.textContent = `読み方更新: ${appState.reading_update_count || 0}件`;

  const rows = document.createElement("div");
  for (const row of appState.results || []) {
    const line = document.createElement("div");
    line.className = "result-row";
    line.append(textCell(`${row.rank}位`));

    const img = document.createElement("img");
    img.src = row.avatar_url;
    img.alt = "";
    line.appendChild(img);

    line.append(textCell(row.display_name));
    line.append(textCell(`${row.cards_won}枚`));
    line.append(textCell(formatMs(row.average_reaction_ms), "result-extra"));
    line.append(textCell(formatMs(row.fastest_reaction_ms), "result-extra"));
    line.append(textCell(`お手付き${row.mistake_count}`));
    rows.appendChild(line);
  }

  resultsEl.replaceChildren(heading, updateText, rows, homeButton);
}

function textCell(text, className = "") {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

function clickCard(memeId) {
  if (!inputEnabled || !appState || activeRoundNo <= 0) {
    return;
  }
  const self = selfPlayer();
  if (self && self.penalty) {
    return;
  }
  if (actedRoundNo === activeRoundNo) {
    return;
  }
  const reactionMs = performance.now() - keywordStartedAt;
  actedRoundNo = activeRoundNo;
  send({
    type: "click_card",
    round_no: activeRoundNo,
    meme_id: Number(memeId),
    reaction_ms: reactionMs,
  });
}

async function playRound(round) {
  if (round.tts_fallback) {
    await sleep(Number(round.wait_ms || 0));
    keywordStartedAt = performance.now();
    send({ type: "keyword_started", round_no: round.round_no });
    inputEnabled = true;
    void speakText(round.reading_text || "");
    return;
  }
  if (!round.audio || !round.audio.intro || !round.audio.keyword) {
    showToast("読み上げ音声を準備しています...");
    return;
  }
  try {
    inputEnabled = false;
    await playToEnd(round.audio.intro);
    statusLine.textContent = "待機中...";
    await sleep(Number(round.wait_ms || 0));
    const keywordAudio = new Audio(round.audio.keyword);
    keywordAudio.preload = "auto";
    await keywordAudio.play();
    keywordStartedAt = performance.now();
    send({ type: "keyword_started", round_no: round.round_no });
    inputEnabled = true;
  } catch (error) {
    console.error(error);
    showToast("音声を再生できませんでした");
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function speakText(text) {
  const value = String(text || "").trim();
  if (!value || !("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    return sleep(350);
  }
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = "ja-JP";
    utterance.rate = 1;
    utterance.pitch = 1;
    utterance.onend = resolve;
    utterance.onerror = resolve;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    window.setTimeout(resolve, Math.max(1200, value.length * 450));
  });
}

function unlockSpeech() {
  if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(".");
    utterance.lang = "ja-JP";
    utterance.volume = 0.01;
    utterance.rate = 1.4;
    utterance.onend = resolve;
    utterance.onerror = resolve;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    window.setTimeout(resolve, 300);
  });
}

function playToEnd(src) {
  return new Promise((resolve, reject) => {
    const audio = new Audio(src);
    audio.preload = "auto";
    audio.addEventListener("ended", resolve, { once: true });
    audio.addEventListener("error", reject, { once: true });
    audio.play().catch(reject);
  });
}

function maybePreloadImages() {
  if (!appState || !appState.cards || appState.cards.length === 0) {
    return;
  }
  const signature = appState.cards.map((card) => card.id).join(",");
  if (signature === imageLoadSentFor || signature === boardSignature) {
    return;
  }
  boardSignature = signature;
  const urls = appState.cards.map((card) => card.image_url);
  Promise.race([preloadImages(urls), sleep(8500)]).then(() => {
    imageLoadSentFor = signature;
    send({ type: "images_loaded" });
  });
}

function preloadImages(urls) {
  return Promise.all(urls.map((url) => new Promise((resolve) => {
    const image = new Image();
    image.onload = resolve;
    image.onerror = resolve;
    image.src = url;
  })));
}

async function unlockAudio() {
  if (!window.AudioContext && !window.webkitAudioContext) {
    return;
  }
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioContext = new AudioContextClass();
  }
  if (audioContext.state !== "running") {
    await audioContext.resume();
  }
}

function selfPlayer() {
  if (!appState) {
    return null;
  }
  return (appState.players || []).find((player) => player.is_self) || null;
}

function stateText(state) {
  if (state.state === "LOBBY") {
    return state.first_five_ready ? "Ready受付中" : "読み上げ音声を準備しています...";
  }
  if (state.state === "LOADING") {
    return "読み上げ音声を準備しています...";
  }
  if (state.state === "COUNTDOWN") {
    return "開始";
  }
  if (state.state === "ROUND_INTRO") {
    return "読み上げ中";
  }
  if (state.state === "ROUND_ACTIVE") {
    return "タッチ";
  }
  if (state.state === "ROUND_RESULT") {
    return "判定";
  }
  if (state.state === "MIDGAME_PAUSE") {
    return "中間確認";
  }
  if (state.state === "FINISHED") {
    return "結果";
  }
  if (state.state === "DISBANDED") {
    return "解散済み";
  }
  return state.state;
}

function finishReasonText(reason) {
  if (reason === "all_mistake") {
    return "全員お手付きのためゲーム終了";
  }
  if (reason === "disbanded") {
    return "ゲームが解散されました";
  }
  if (reason === "voice_error") {
    return "読み上げ音声の生成に失敗しました";
  }
  return "Dr.Memeかるた終了";
}

function formatMs(value) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(Number(value) / 1000).toFixed(3)}秒`;
}

function showToast(text) {
  toastEl.textContent = text;
  toastEl.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toastEl.hidden = true;
  }, 2200);
}

function showDialog(dialog) {
  if (dialog.open) {
    return;
  }
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
  } else {
    dialog.setAttribute("open", "open");
  }
}

async function fetchMemeList() {
  if (!appState) {
    return;
  }
  const query = memeSearch.value.trim();
  const url = `/karuta/api/games/${gameId}/memes?token=${encodeURIComponent(token)}&q=${encodeURIComponent(query)}&page=${listPage}`;
  const response = await fetch(url);
  if (!response.ok) {
    showToast("Memelistを取得できませんでした");
    return;
  }
  const payload = await response.json();
  pageLabel.textContent = `${payload.page} / ${Math.max(1, Math.ceil(payload.total / payload.page_size))}`;
  prevPage.disabled = payload.page <= 1;
  nextPage.disabled = payload.page * payload.page_size >= payload.total;
  renderMemeRows(payload.items || []);
}

function renderMemeRows(items) {
  const fragment = document.createDocumentFragment();
  for (const meme of items) {
    const row = document.createElement("div");
    row.className = "meme-row";

    const img = document.createElement("img");
    img.src = meme.image_url;
    img.alt = "";
    row.appendChild(img);

    const keyword = document.createElement("div");
    keyword.className = "meme-keyword";
    keyword.textContent = `#${meme.id} ${meme.keyword}`;
    row.appendChild(keyword);

    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 100;
    input.value = meme.reading || "";
    input.placeholder = "読み方";
    row.appendChild(input);

    const save = document.createElement("button");
    save.type = "button";
    save.className = "tool-button";
    save.textContent = "保存";
    save.addEventListener("click", () => {
      send({ type: "set_reading", meme_id: Number(meme.id), reading: input.value });
    });
    row.appendChild(save);

    fragment.appendChild(row);
  }
  memeRows.replaceChildren(fragment);
}

listButton.addEventListener("click", () => {
  listPage = 1;
  showDialog(listDialog);
  fetchMemeList();
});

memeSearchButton.addEventListener("click", () => {
  listPage = 1;
  fetchMemeList();
});

memeSearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    listPage = 1;
    fetchMemeList();
  }
});

prevPage.addEventListener("click", () => {
  listPage = Math.max(1, listPage - 1);
  fetchMemeList();
});

nextPage.addEventListener("click", () => {
  listPage += 1;
  fetchMemeList();
});

closeButton.addEventListener("pointerdown", () => {
  window.clearTimeout(closeTimer);
  closeTimer = window.setTimeout(() => showDialog(confirmDialog), 1000);
});

["pointerup", "pointerleave", "pointercancel"].forEach((eventName) => {
  closeButton.addEventListener(eventName, () => window.clearTimeout(closeTimer));
});

disbandButton.addEventListener("click", () => {
  send({ type: "disband" });
  confirmDialog.close();
});

cancelDisbandButton.addEventListener("click", () => {
  confirmDialog.close();
});

midgameAckButton.addEventListener("click", () => {
  send({ type: "midgame_ack" });
  midgameDialog.close();
});

connect();
