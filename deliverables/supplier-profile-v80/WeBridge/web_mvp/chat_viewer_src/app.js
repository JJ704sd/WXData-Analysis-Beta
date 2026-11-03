"use strict";

const $ = (selector) => document.querySelector(selector);
const state = {chats: [], mode: "all", source: "", client: "", selected: null, messages: [], older: null,
  newer: null, highlighted: null, detail: null, expanded: false, loading: false, serial: 0,
  timelineSerial: 0, controller: null, timelineController: null,
  search: {category: "all", senderId: "", items: [], older: null, total: null, skipped: 0,
    parameters: null, controller: null, serial: 0, loading: false}};
const globalSearch = {serial:0, jumpSerial:0, panelSerial:0, controller:null, timer:null, params:null,
  result:null, status:null, autoSignature:"", lastRecover:0, loading:false};
const colors = ["#6f9bc7", "#78ad9d", "#d7a16f", "#9a8ec4", "#8babc5", "#c28f98", "#87a98b"];
let toastTimer;

function node(tag, className = "", value = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== null) element.textContent = value;
  return element;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;
}
function avatar(name, kind = "direct") {
  const result = node("div", `avatar ${kind}`);
  const chars = [...(name || "会话")].filter((char) => /[\p{L}\p{N}]/u.test(char));
  const hash = [...(name || "会话")].reduce((a, c) => ((a * 31 + c.codePointAt(0)) >>> 0), 7);
  if (kind === "group") {
    for (let i = 0; i < 4; i++) {
      const tile = node("span", "", chars[i % Math.max(chars.length, 1)] || "•");
      tile.style.backgroundColor = colors[(hash + i * 2) % colors.length]; result.append(tile);
    }
  } else {
    result.textContent = chars.slice(0, 1).join("") || "?";
    result.style.backgroundColor = colors[hash % colors.length];
  }
  result.setAttribute("aria-hidden", "true"); return result;
}
function toast(value) {
  const item = $("#toast"); item.textContent = value; item.classList.add("visible");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => item.classList.remove("visible"), 2800);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, cache: "no-store"});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "资料读取失败");
  return payload;
}
function sourceLabel(source) {
  const clean = source.replace(/^资料[：:]?/, ""), batch = clean.split(" / ").at(-1);
  const stamp = batch.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  return stamp ? `${stamp[1]}/${stamp[2]}/${stamp[3]} ${stamp[4]}:${stamp[5]}:${stamp[6]}` : clean;
}
function clientLabel(client) { return ({wecom:"企微",wechat:"个微",unknown:"来源未标注",conflict:"来源待核对"})[client] || "来源未标注"; }
function matchesClient(chat) { return !state.client || (state.client === "unknown" ? !["wecom", "wechat"].includes(chat.client) : chat.client === state.client); }
function formatNumber(value) { return Number(value || 0).toLocaleString("zh-CN"); }
function formatDate(value) {
  if (!value) return "时间未知";
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : "时间未知";
}
function formatTime(value) {
  const match = String(value || "").match(/[T ](\d{2}:\d{2})(?::\d{2})?/);
  return match ? match[1] : "时间未知";
}
function dateKey(value) { return String(value || "").match(/^\d{4}-\d{2}-\d{2}/)?.[0] || "unknown"; }

function filteredChats() {
  const query = $("#conversation-search").value.trim().toLocaleLowerCase();
  return state.chats.filter((chat) => (!state.source || chat.source === state.source)
    && matchesClient(chat)
    && (state.mode === "all" || chat.kind === state.mode)
    && (!query || chat.name.toLocaleLowerCase().includes(query)));
}
function updateCounts() {
  const chats = state.chats.filter((chat) => (!state.source || chat.source === state.source) && matchesClient(chat));
  $("#count-all").textContent = chats.length;
  $("#count-group").textContent = chats.filter((chat) => chat.kind === "group").length;
  $("#count-direct").textContent = chats.filter((chat) => chat.kind === "direct").length;
}
function renderList() {
  updateCounts();
  const list = $("#conversation-list"); list.replaceChildren();
  const chats = filteredChats();
  $("#list-caption").textContent = `${state.mode === "all" ? "全部会话" : state.mode === "group" ? "群聊" : "私聊"} · ${chats.length}`;
  if (!chats.length) {
    const empty = node("div", "list-empty");
    const noDirect = state.mode === "direct" && !state.chats.some((chat) => chat.kind === "direct" && (!state.source || chat.source === state.source));
    empty.append(node("div", "", !state.chats.length ? "还没有接入聊天记录" : noDirect ? "当前来源没有私聊导出" : "没有找到匹配的会话"));
    if (noDirect || !state.chats.length) {
      const button = node("button", "", noDirect ? "接入私聊导出" : "接入导出资料"); button.type = "button"; button.addEventListener("click", openSource); empty.append(button);
    }
    list.append(empty); return;
  }
  for (const chat of chats) {
    const item = node("button", `conversation-item${chat.key === state.selected?.key ? " active" : ""}`);
    item.type = "button"; item.setAttribute("aria-label", `${chat.name}，${clientLabel(chat.client)}，${chat.source}，${chat.kind === "group" ? "群聊" : "私聊"}`);
    item.append(avatar(chat.name, chat.kind));
    const copy = node("div", "conversation-copy");
    const top = node("div", "conversation-top"); top.append(node("span", "conversation-title", chat.name), node("span", `client-badge ${chat.client}`, clientLabel(chat.client)));
    const bottom = node("div", "conversation-bottom"); bottom.append(node("span", "conversation-description", chat.available ? `${formatNumber(chat.rows)} 条记录 · ${sourceLabel(chat.source)}` : "导出文件不完整"), node("span", "conversation-kind", chat.kind === "group" ? "群聊" : "私聊"));
    copy.append(top, bottom); item.append(copy);
    item.addEventListener("click", () => selectChat(chat)); list.append(item);
  }
}
function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll("[data-mode]").forEach((button) => {
    const active = button.dataset.mode === mode; button.classList.toggle("active", active);
    if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected", String(active));
  });
  renderList();
  const candidates = filteredChats();
  if (!candidates.some((chat) => chat.key === state.selected?.key)) {
    if (candidates.length) selectChat(candidates[0], false);
    else showEmpty(mode === "direct" && !state.chats.some((chat) => chat.kind === "direct") ? "direct" : "filter");
  }
}
function openSource() {
  $("#source-error").hidden = true; $("#source-path").value = "";
  if (!$("#source-dialog").open) $("#source-dialog").showModal();
  setTimeout(() => $("#source-path").focus(), 50);
}
function showEmpty(kind = "initial") {
  if (state.controller) state.controller.abort();
  if (state.timelineController) state.timelineController.abort();
  closeHistorySearch(); state.serial++; state.timelineSerial++; state.selected = null; state.messages = []; state.older = null; state.newer = null; state.detail = null;
  $("#chat-name").textContent = kind === "direct" ? "私聊" : "选择一个会话";
  $("#chat-subtitle").textContent = kind === "direct" ? "本地资料中还没有私聊导出" : "从左侧打开本地导出的聊天记录";
  $("#archive-mode").textContent = "本地归档视图"; $("#archive-description").textContent = "这里展示本地记录，无法发送消息";
  $("#header-avatar").replaceChildren(); $("#toggle-message-search").disabled = true; $("#toggle-details").disabled = true;
  const content = node("div", "empty-welcome"); const em = node("div", "empty-icon"); em.append(icon(kind === "direct" ? "person" : "chat"));
  const isNew = !state.chats.length;
  content.append(em, node("h3", "", isNew ? "从导出资料开始" : kind === "direct" ? "还没有私聊记录" : "查看一段会话"),
    node("p", "", isNew ? "接入企微或个微的聊天导出，在本机浏览群聊和私聊。" : kind === "direct" ? "接入私聊导出后，就可以在这里浏览会话。" : "选择左侧会话，按原始顺序浏览本地导出的记录。"));
  if (kind === "direct" || isNew) { const button = node("button", "button-primary", isNew ? "接入导出资料" : "接入私聊导出"); button.addEventListener("click", openSource); content.append(button); }
  $("#chat-scroll").replaceChildren(content); $("#details-content").replaceChildren(node("p", "muted", "选择会话后查看资料信息。"));
  renderList();
}
function buildSources() {
  const select = $("#source-filter"); select.replaceChildren(new Option("全部来源", ""));
  for (const source of [...new Set(state.chats.map((chat) => chat.source))].sort()) select.add(new Option(source, source));
  if (![...select.options].some((option) => option.value === state.source)) state.source = "";
  select.value = state.source;
}
async function loadCatalog() {
  const data = await api("/api/conversations"); state.chats = data.chats;
  buildSources(); fillGlobalSources(); renderList();
  if (data.issues.length) toast("部分资料读取失败，请检查本地文件");
  return data;
}
async function selectChat(chat, mobile = true) {
  if (!chat.available) { toast("该会话的导出文件不完整"); return; }
  if (state.controller) state.controller.abort();
  if (state.timelineController) state.timelineController.abort();
  closeHistorySearch();
  state.controller = new AbortController(); const signal = state.controller.signal; const serial = ++state.serial;
  const timelineSerial = ++state.timelineSerial;
  state.selected = chat; state.messages = []; state.older = null; state.newer = null; state.highlighted = null; state.detail = null; state.expanded = false; state.loading = false;
  $("#chat-name").textContent = chat.name;
  $("#chat-subtitle").textContent = `${clientLabel(chat.client)}  /  ${chat.kind === "group" ? "群聊" : "私聊"}  /  ${chat.source}  /  ${formatNumber(chat.rows)} 条${chat.preview ? "解析记录" : "导出记录"}`;
  $("#archive-mode").textContent = chat.preview ? "解析预览 · 尚非正式导出" : "本地归档视图";
  $("#archive-description").textContent = chat.preview ? "复核和导出状态以解析工具为准；关闭工具后页面停止" : "这里展示已导出的记录，无法发送消息";
  $("#header-avatar").replaceChildren(avatar(chat.name, chat.kind));
  $("#toggle-message-search").disabled = false; $("#toggle-details").disabled = false;
  $("#chat-scroll").replaceChildren(node("div", "list-loading", "正在读取聊天记录…"));
  $("#details-content").replaceChildren(node("p", "muted", "正在读取会话信息…"));
  if (mobile) $("#app").classList.add("mobile-chat");
  renderList();
  const params = new URLSearchParams({chat: chat.key});
  const messageTask = api(`/api/messages?${params}`, {signal}).then((data) => {
    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;
    state.messages = data.messages; state.older = data.older; renderMessages();
    const scroll = $("#chat-scroll"); scroll.scrollTop = scroll.scrollHeight;
  }).catch((error) => {
    if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) $("#chat-scroll").replaceChildren(node("div", "list-loading", `无法读取消息：${error.message}`));
  });
  const detailTask = api(`/api/detail?${params}`, {signal}).then((data) => {
    if (serial !== state.serial) return; state.detail = data; renderDetails();
  }).catch((error) => {
    if (error.name !== "AbortError" && serial === state.serial) $("#details-content").replaceChildren(node("p", "muted", `无法读取会话信息：${error.message}`));
  });
  await Promise.all([messageTask, detailTask]);
}
async function loadOlder() {
  if (!state.selected || state.older === null || state.loading) return;
  state.loading = true; const serial = state.serial, timelineSerial = state.timelineSerial; const scroll = $("#chat-scroll");
  const height = scroll.scrollHeight, top = scroll.scrollTop;
  const button = scroll.querySelector(".older-button"); if (button) { button.disabled = true; button.textContent = "读取中…"; }
  try {
    const params = new URLSearchParams({chat: state.selected.key, before: String(state.older)});
    const data = await api(`/api/messages?${params}`, {signal: state.controller.signal});
    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;
    state.messages = [...data.messages, ...state.messages]; state.older = data.older; renderMessages();
    scroll.scrollTop = scroll.scrollHeight - height + top;
  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) { toast(error.message); renderMessages(); } }
  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }
}
async function loadNewer() {
  if (!state.selected || state.newer === null || state.loading) return;
  state.loading = true; const serial = state.serial, timelineSerial = state.timelineSerial, scroll = $("#chat-scroll");
  const height = scroll.scrollHeight;
  try {
    const params = new URLSearchParams({chat: state.selected.key, after: String(state.newer)});
    const data = await api(`/api/messages-after?${params}`, {signal: state.controller.signal});
    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;
    state.messages = [...state.messages, ...data.messages]; state.newer = data.newer; renderMessages();
    scroll.scrollTop = Math.max(0, height - scroll.clientHeight - 40);
  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) toast(error.message); }
  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }
}
async function replaceTimeline(path, target = null) {
  if (!state.selected) return;
  if (state.timelineController) state.timelineController.abort();
  state.timelineController = new AbortController();
  const serial = state.serial, timelineSerial = ++state.timelineSerial;
  state.loading = true;
  try {
    const data = await api(path, {signal: state.timelineController.signal});
    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;
    state.messages = data.messages; state.older = data.older; state.newer = data.newer ?? null;
    state.highlighted = target; renderMessages();
    const scroll = $("#chat-scroll");
    if (target !== null) {
      const found = scroll.querySelector(`[data-start="${target}"]`);
      if (found) found.scrollIntoView({block:"center"});
    } else scroll.scrollTop = scroll.scrollHeight;
  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) toast(error.message); }
  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }
}
function loadLatest() {
  if (!state.selected) return;
  const params = new URLSearchParams({chat:state.selected.key});
  replaceTimeline(`/api/messages?${params}`);
}
function jumpToResult(message) {
  if (!state.selected || message.kind === "invalid") return;
  const params = new URLSearchParams({chat:state.selected.key,start:String(message.start),end:String(message.end)});
  if (window.innerWidth <= 760) closeHistorySearch();
  replaceTimeline(`/api/message-context?${params}`, message.start);
}
function renderMessages() {
  const scroll = $("#chat-scroll"); scroll.replaceChildren();
  const intro = node("div", "chat-intro", "按记录顺序展示"); scroll.append(intro);
  if (state.newer !== null) {
    const wrap = node("div", "latest-wrap"), latest = node("button", "latest-button", "回到最新记录");
    latest.type = "button"; latest.addEventListener("click", loadLatest); wrap.append(latest); scroll.append(wrap);
  }
  if (state.older !== null) {
    const wrap = node("div", "older-wrap"); const button = node("button", "older-button", "加载更早的记录");
    button.type = "button"; button.addEventListener("click", loadOlder); wrap.append(button); scroll.append(wrap);
  }
  if (!state.messages.length) { scroll.append(node("div", "list-loading", "当前会话没有消息记录。")); return; }
  let previous = null;
  for (const message of state.messages) {
    const day = dateKey(message.time);
    if (day !== previous) { const sep = node("div", "date-separator"); sep.append(node("span", "", formatDate(message.time))); scroll.append(sep); previous = day; }
    if (message.kind === "system") {
      const system = node("div", `system-message${message.start === state.highlighted ? " highlighted-system" : ""}`, message.text || "系统消息");
      system.dataset.start = String(message.start); scroll.append(system); continue;
    }
    const row = node("article", `message-row ${message.direction || "unknown"}${message.start === state.highlighted ? " highlighted" : ""}`);
    row.dataset.start = String(message.start);
    const senderButton = node("button", "sender-avatar"); senderButton.type = "button"; senderButton.title = "查找此人的消息";
    senderButton.setAttribute("aria-label", `查找 ${message.sender || "发言人"} 的消息`); senderButton.append(avatar(message.sender, "direct"));
    if (message.kind === "invalid" || (!message.senderId && (!message.sender || message.sender === "发言人未知"))) senderButton.disabled = true;
    else senderButton.addEventListener("click", () => searchBySender(message.sender, message.senderId));
    row.append(senderButton);
    const content = node("div", "message-content"), meta = node("div", "message-meta");
    meta.append(node("span", "sender", message.sender || "发言人未知"), node("time", "", formatTime(message.time)));
    content.append(meta);
    const bubble = node("div", `message-bubble${message.kind === "invalid" ? " invalid-message" : ""}`);
    if (message.text) bubble.append(node("span", "", message.text));
    const mediaKinds = {image:"图片", file:"文件", emoji:"表情", video:"视频", voice:"语音", record:"聊天记录", unknown:"未识别内容", miniprogram:"小程序"};
    if (mediaKinds[message.kind]) appendMedia(bubble, message, mediaKinds[message.kind]);
    if (message.kind === "link" && message.links?.length) {
      const linkText = node("div", "message-link-text", message.links[0]); bubble.append(linkText);
    }
    if (message.kind === "quote") {
      const quote = node("div", "message-quote");
      quote.append(node("strong", "", message.quote?.sender || "引用"), node("span", "", `：${message.quote?.text || "引用内容未完整解析"}`)); bubble.append(quote);
    }
    if (!bubble.childNodes.length) bubble.append(node("span", "", "此条消息没有可显示的正文"));
    content.append(bubble);
    if (message.kind !== "invalid") {
      const tools = node("div", "message-tools"), inspect = node("button", "", "查看原始记录");
      inspect.type = "button"; inspect.addEventListener("click", () => showRecord(message)); tools.append(inspect); content.append(tools);
    }
    row.append(content); scroll.append(row);
  }
  if (state.newer !== null) {
    const wrap = node("div", "older-wrap"), button = node("button", "older-button", "加载更新的记录");
    button.type = "button"; button.addEventListener("click", loadNewer); wrap.append(button); scroll.append(wrap);
  }
}
function appendMedia(bubble, message, label) {
  const asset = message.asset || {};
  const block = node("div", "message-media"), mediaIcon = node("span", "media-icon"); mediaIcon.append(icon(message.kind === "image" ? "image" : "file"));
  const copy = node("div", "media-copy"); copy.append(node("div", "media-title", asset.filename || label));
  let status = message.kind === "miniprogram" ? "小程序记录" : asset.path ? (asset.previewOnly ? "本地预览图" : "本地附件") : (asset.reason || "原文件未随导出提供");
  copy.append(node("div", "media-state", status));
  let url = "";
  if (asset.path) {
    const params = new URLSearchParams({chat: state.selected.key, path: asset.path}); url = `/api/asset?${params}`;
    const link = node("a", "media-link", message.kind === "image" ? "打开图片" : "打开附件"); link.href = url; link.target = "_blank"; link.rel = "noopener"; copy.append(link);
  }
  block.append(mediaIcon, copy); bubble.append(block);
  if (message.kind === "image" && url) {
    const image = node("img", "message-image"); image.src = url; image.alt = asset.previewOnly ? "导出的图片预览" : "导出的图片";
    image.loading = "lazy"; image.addEventListener("error", () => { image.remove(); copy.append(node("div", "media-state", "图片文件无法打开")); }); bubble.append(image);
  }
}
function detailRow(label, value) { const row = node("div", "detail-row"); row.append(node("span", "", label), node("strong", "", value)); return row; }
function renderDetails() {
  const host = $("#details-content"); host.replaceChildren();
  if (!state.detail || !state.selected) return;
  const {chat, members, memberWarning, created, truncated} = state.detail;
  const memberEntries = state.detail.memberEntries || members.map((name) => ({name,id:""}));
  const suggestions = $("#sender-suggestions"); suggestions.replaceChildren();
  for (const member of memberEntries) { const option = node("option"); option.value = member.name; suggestions.append(option); }
  const profile = node("div", "details-profile"); profile.append(avatar(chat.name, chat.kind));
  const text = node("div"); text.append(node("div", "details-profile-name", chat.name), node("div", "details-profile-sub", chat.kind === "group" ? "群聊档案" : "私聊档案")); profile.append(text); host.append(profile);
  const basics = node("section", "detail-section"); basics.append(node("div", "section-title", "资料概况"));
  basics.append(detailRow("平台", clientLabel(chat.client)), detailRow("会话类型", chat.kind === "group" ? "群聊" : "私聊"), detailRow(chat.preview ? "解析记录" : "导出记录", `${formatNumber(chat.rows)} 条`),
    detailRow(chat.preview ? "解析时间" : "导出时间", created ? formatDate(created) : "未提供"), detailRow("复核状态", chat.reviewStatus || "未提供"));
  if (chat.origin) basics.append(detailRow("来源标识", chat.origin));
  const source = node("div", "detail-row"); source.append(node("span", "", "资料来源")); const tag = node("span", "source-tag", chat.source); source.append(tag); basics.append(source);
  if (truncated) basics.append(node("p", "detail-note", "本次导出达到条数限制，记录可能不完整。")); host.append(basics);
  if (chat.kind === "group") {
    const section = node("section", "detail-section"), heading = node("div", "section-title");
    heading.append(node("span", "", "成员资料"), node("span", "", members.length ? `${formatNumber(members.length)} 人` : "暂无资料")); section.append(heading);
    if (members.length) {
      const grid = node("div", "member-grid");
      for (const member of memberEntries.slice(0, state.expanded ? members.length : 12)) {
        const item = node("button", "member"); item.type = "button";
        item.append(avatar(member.name, "direct"), node("span", "member-name", member.name));
        item.title = `查找 ${member.name} 的消息`; item.setAttribute("aria-label", item.title);
        item.addEventListener("click", () => searchBySender(member.name, member.id)); grid.append(item);
      }
      section.append(grid);
      if (members.length > 12) { const more = node("button", "show-members", state.expanded ? "收起成员" : `查看全部 ${formatNumber(members.length)} 人`); more.addEventListener("click", () => { state.expanded = !state.expanded; renderDetails(); }); section.append(more); }
      section.append(node("p", "member-note", memberWarning || "成员信息来自当前资料，可能与当前群成员不同。"));
    } else section.append(node("p", "member-note", "当前资料没有提供成员名单。"));
    host.append(section);
  }
  const note = node("section", "detail-section"); note.append(node("div", "section-title", "关于这份资料"), node("p", "detail-note", chat.preview ? "当前展示解析工具的本地阅读缓存。复核与正式导出仍需在解析工具中完成；图片和文件以实际可用状态为准。" : "内容仅覆盖已导出的本地记录。图片、文件等以每条消息的实际可用状态为准。")); host.append(note);
}

function setSearchCategory(category) {
  state.search.category = category;
  document.querySelectorAll(".history-tab").forEach((button) => {
    const active = button.dataset.category === category;
    button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
  });
}
function closeHistorySearch() {
  if (state.search.controller) state.search.controller.abort();
  state.search.serial++; state.search.loading = false;
  $("#history-panel").hidden = true; $("#app").classList.remove("search-open");
  $("#sender-filter").open = false; $("#date-filter").open = false;
}
function openHistorySearch(sender = "", senderId = "") {
  if (!state.selected) return;
  closeGlobalSearch();
  $("#details-panel").classList.remove("open");
  $("#history-panel").hidden = false; $("#app").classList.add("search-open");
  $("#history-query").value = ""; $("#history-sender").value = sender;
  $("#history-from").value = ""; $("#history-to").value = "";
  state.search.senderId = senderId || ""; setSearchCategory("all");
  runHistorySearch();
  if (!sender) $("#history-query").focus();
}
function searchBySender(name, senderId) {
  openHistorySearch(name || "", senderId || "");
}
function historyParameters() {
  const sender = $("#history-sender").value.trim();
  return {category:state.search.category, q:$("#history-query").value.trim(),
    sender:state.search.senderId ? "" : sender, senderId:state.search.senderId,
    from:$("#history-from").value, to:$("#history-to").value};
}
function syncHistoryFilters() {
  const senderName = $("#history-sender").value.trim();
  const sender = Boolean(senderName);
  const date = Boolean($("#history-from").value || $("#history-to").value);
  $("#sender-filter").classList.toggle("selected", sender); $("#date-filter").classList.toggle("selected", date);
  const senderSummary = $("#sender-filter summary"); senderSummary.firstChild.nodeValue = sender ? `发送人：${senderName.length > 6 ? senderName.slice(0, 6) + "…" : senderName}` : "发送人";
  senderSummary.title = sender ? `发送人：${senderName}` : "发送人";
  $("#date-filter summary").title = date ? `${$("#history-from").value || "不限"} 至 ${$("#history-to").value || "不限"}` : "日期";
  $("#clear-history-filters").hidden = !(sender || date);
}
async function runHistorySearch(append = false) {
  if (!state.selected || state.search.loading && append) return;
  if (state.search.controller) state.search.controller.abort();
  const search = state.search, serial = ++search.serial, chatSerial = state.serial;
  search.controller = new AbortController(); search.loading = true;
  const parameters = append ? search.parameters : historyParameters();
  if (!parameters || append && search.older === null) { search.loading = false; return; }
  if (!append) { search.parameters = parameters; search.items = []; search.older = null; search.total = null; search.skipped = 0; }
  syncHistoryFilters();
  $("#history-caption").textContent = append ? "正在加载更早的结果…" : "正在查找当前会话的本地记录…";
  if (!append) $("#history-results").replaceChildren(node("div", "history-empty", "查找中…"));
  else { const button = $("#history-results .history-more"); if (button) button.disabled = true; }
  const params = new URLSearchParams({chat:state.selected.key});
  for (const [key,value] of Object.entries(parameters)) if (value) params.set(key,value);
  if (append) { params.set("before", String(search.older)); params.set("pageOnly", "1"); }
  try {
    const data = await api(`/api/search?${params}`, {signal:search.controller.signal});
    if (serial !== search.serial || chatSerial !== state.serial) return;
    search.items = append ? [...search.items, ...data.items] : data.items;
    search.older = data.older;
    if (!append) { search.total = data.total; search.skipped = data.skipped; }
    renderHistoryResults();
  } catch (error) {
    if (error.name !== "AbortError" && serial === search.serial && chatSerial === state.serial) {
      $("#history-caption").textContent = "查找未完成";
      if (append) { renderHistoryResults(); toast(error.message); }
      else $("#history-results").replaceChildren(node("div", "history-empty", error.message));
    }
  } finally { if (serial === search.serial && chatSerial === state.serial) search.loading = false; }
}
function renderHistoryResults() {
  const search = state.search, results = $("#history-results"); results.replaceChildren();
  const activeFilters = Boolean(search.parameters?.sender || search.parameters?.senderId || search.parameters?.from || search.parameters?.to);
  const scope = search.total === null ? "最近的聊天记录" : `找到 ${formatNumber(search.total)} 条记录`;
  $("#history-caption").textContent = scope + (search.skipped ? `，${formatNumber(search.skipped)} 条无法解析` : "") + (activeFilters ? " · 已筛选" : "");
  if (!search.items.length) {
    results.append(node("div", "history-empty", search.total === null ? "当前会话没有聊天记录。" : "没有找到匹配的聊天记录。"));
    return;
  }
  const labels = {image:"图片", video:"视频", file:"文件", link:"链接", miniprogram:"小程序", emoji:"表情", record:"聊天记录", system:"系统消息", unknown:"未识别内容", invalid:"无法读取"};
  for (const message of search.items) {
    const item = node("button", "history-result"); item.type = "button";
    const meta = node("div", "history-result-meta");
    meta.append(node("span", "history-result-sender", message.kind === "system" ? "系统消息" : message.sender || "发言人未知"), node("span", "history-result-time", `${formatDate(message.time)} ${formatTime(message.time)}`));
    const preview = message.text || message.asset?.filename || message.quote?.text || message.links?.[0] || `[${labels[message.kind] || "消息"}]`;
    const body = node("div", "history-result-text"); appendHighlightedText(body, preview, search.parameters?.q || "");
    item.append(meta, body);
    const term = (search.parameters?.q || "").toLocaleLowerCase();
    const quoted = message.quote?.text || "";
    if (term && !String(preview).toLocaleLowerCase().includes(term) && quoted.toLocaleLowerCase().includes(term)) {
      const quote = node("div", "history-result-quote"); appendHighlightedText(quote, `引用：${quoted}`, search.parameters.q); item.append(quote);
    } else if (term && !String(preview).toLocaleLowerCase().includes(term)) {
      const link = (message.links || []).find((value) => value.toLocaleLowerCase().includes(term));
      if (link) { const extra = node("div", "history-result-quote"); appendHighlightedText(extra, `链接：${link}`, search.parameters.q); item.append(extra); }
    }
    if (labels[message.kind] && !["system","invalid"].includes(message.kind)) item.append(node("span", "history-result-kind", labels[message.kind]));
    if (message.kind === "invalid") item.disabled = true;
    else item.addEventListener("click", () => jumpToResult(message));
    results.append(item);
  }
  if (search.older !== null) {
    const more = node("button", "history-more", "显示更早的结果"); more.type = "button";
    more.addEventListener("click", () => runHistorySearch(true)); results.append(more);
  }
}
function appendHighlightedText(host, text, query) {
  const value = String(text || ""), needle = String(query || "").toLocaleLowerCase();
  if (!needle) { host.textContent = value; return; }
  const lower = value.toLocaleLowerCase(); let index = 0, match;
  while ((match = lower.indexOf(needle, index)) !== -1) {
    host.append(document.createTextNode(value.slice(index, match)), node("mark", "", value.slice(match, match + needle.length)));
    index = match + needle.length;
  }
  host.append(document.createTextNode(value.slice(index)));
}
async function showRecord(message) {
  const dialog = $("#record-dialog"), content = $("#record-content"); content.textContent = "读取中…"; dialog.showModal();
  try {
    const params = new URLSearchParams({chat: state.selected.key, start: String(message.start), end: String(message.end)});
    const result = await api(`/api/record?${params}`); content.textContent = JSON.stringify(result, null, 2);
  } catch (error) { content.textContent = `读取失败：${error.message}`; }
}

function fillGlobalSources() {
  const select = $("#global-source"), previous = select.value;
  select.replaceChildren(new Option("全部资料", ""));
  for (const source of [...new Set(state.chats.map((chat) => chat.source))].sort()) select.add(new Option(source, source));
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}
function globalParameters() {
  return {q:$("#global-query").value.trim(),scope:$("#global-scope").value,client:$("#global-client").value,source:$("#global-source").value,
    category:$("#global-category").value,sender:$("#global-sender").value.trim(),
    from:$("#global-from").value,to:$("#global-to").value};
}
function closeGlobalSearch() {
  if (globalSearch.controller) globalSearch.controller.abort();
  globalSearch.serial++; globalSearch.jumpSerial++; globalSearch.panelSerial++; globalSearch.loading = false;
  clearTimeout(globalSearch.timer); globalSearch.timer = null;
  $("#global-panel").hidden = true; $("#app").classList.remove("global-open"); $("#global-more").open = false;
}
function openGlobalSearch() {
  closeHistorySearch(); $("#details-panel").classList.remove("open");
  $("#global-panel").hidden = false; $("#app").classList.add("global-open");
  const panelSerial = ++globalSearch.panelSerial;
  $("#global-query").focus();
  api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})
    .then((status) => { if (panelSerial === globalSearch.panelSerial) showGlobalStatus(status); })
    .catch((error) => { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); });
  pollGlobalIndex(panelSerial);
}
function showGlobalError(message) {
  $("#global-index-text").textContent = message || "搜索准备失败";
  $("#global-retry").hidden = false;
}
function showGlobalStatus(status) {
  globalSearch.status = status;
  const total = status.total || 0, ready = status.ready || 0;
  const progress = $("#global-index-progress"); progress.max = Math.max(total,1); progress.value = Math.min(ready,total);
  let text;
  if (status.state === "idle") text = "准备本地搜索";
  else if (status.state === "building") text = `正在准备 ${formatNumber(ready)} / ${formatNumber(total)} 个会话`;
  else if (status.failed) text = `已准备 ${formatNumber(ready)} / ${formatNumber(total)} 个会话，${formatNumber(status.failed)} 个失败`;
  else text = `已准备 ${formatNumber(ready)} 个会话 · ${formatNumber(status.indexedMessages)} 条记录`;
  if (status.skipped) text += ` · ${formatNumber(status.skipped)} 条无法解析`;
  if (status.error) text = status.error;
  $("#global-index-text").textContent = text;
  $("#global-retry").hidden = !(status.failed || status.error);
  if (globalSearch.result) renderGlobalPagination();
}
async function pollGlobalIndex(panelSerial) {
  if (panelSerial !== globalSearch.panelSerial || $("#global-panel").hidden) return;
  try {
    const status = await api("/api/global/status");
    if (panelSerial !== globalSearch.panelSerial || $("#global-panel").hidden) return;
    showGlobalStatus(status);
    const coverage = globalSearch.result?.coverage;
    const signature = `${status.ready}/${status.total}/${status.failed}/${status.skipped}`;
    const oldSignature = coverage ? `${coverage.ready}/${coverage.total}/${coverage.failed}/${coverage.skipped}` : "";
    if (!globalSearch.loading && globalSearch.params && coverage && status.state !== "building" && signature !== oldSignature &&
        globalSearch.autoSignature !== signature && JSON.stringify(globalSearch.params) === JSON.stringify(globalParameters())) {
      globalSearch.autoSignature = signature; runGlobalSearch(1);
    }
    if (status.state === "building" && !status.localWorker && Date.now() - globalSearch.lastRecover > 10000) {
      globalSearch.lastRecover = Date.now();
      api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})
        .then((value) => { if (panelSerial === globalSearch.panelSerial) showGlobalStatus(value); })
        .catch((error) => { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); });
    }
  } catch (error) { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); }
  if (panelSerial === globalSearch.panelSerial && !$("#global-panel").hidden)
    globalSearch.timer = setTimeout(() => pollGlobalIndex(panelSerial), 1800);
}
async function runGlobalSearch(page = 1) {
  const parameters = globalParameters();
  if (!parameters.q) { $("#global-caption").textContent = "请输入要搜索的内容"; $("#global-query").focus(); return; }
  if (globalSearch.controller) globalSearch.controller.abort();
  globalSearch.controller = new AbortController(); const serial = ++globalSearch.serial;
  globalSearch.params = parameters; globalSearch.autoSignature = ""; globalSearch.loading = true;
  $("#global-caption").textContent = "正在搜索已准备的会话…";
  $("#global-results").replaceChildren(node("div", "global-empty", "搜索中…")); $("#global-pagination").hidden = true;
  const params = new URLSearchParams({page:String(page)});
  for (const [key,value] of Object.entries(parameters)) if (value) params.set(key,value);
  try {
    const data = await api(`/api/global/search?${params}`, {signal:globalSearch.controller.signal});
    if (serial !== globalSearch.serial || $("#global-panel").hidden) return;
    globalSearch.result = data; renderGlobalResults();
  } catch (error) {
    if (error.name !== "AbortError" && serial === globalSearch.serial) {
      globalSearch.result = null; $("#global-caption").textContent = "搜索未完成";
      $("#global-results").replaceChildren(node("div", "global-empty", error.message));
    }
  } finally { if (serial === globalSearch.serial) globalSearch.loading = false; }
}
function renderGlobalResults() {
  const data = globalSearch.result, host = $("#global-results"); if (!data) return;
  host.replaceChildren();
  const c = data.coverage, incomplete = c.ready < c.total;
  let caption = `找到 ${formatNumber(data.total)} 条记录`;
  if (incomplete) caption += ` · 已准备 ${formatNumber(c.ready)} / ${formatNumber(c.total)} 个会话`;
  if (c.failed) caption += ` · ${formatNumber(c.failed)} 个会话不可用`;
  if (c.skipped) caption += ` · ${formatNumber(c.skipped)} 条无法解析`;
  $("#global-caption").textContent = caption;
  if (!data.items.length) {
    host.append(node("div", "global-empty", incomplete ? "已准备的会话中没有匹配记录，索引完成后会更新结果。" : "没有找到匹配的聊天记录。"));
    renderGlobalPagination(); return;
  }
  const labels = {image:"图片",video:"视频",file:"文件",link:"链接",miniprogram:"小程序",emoji:"表情",record:"聊天记录",system:"系统消息",unknown:"未识别内容"};
  const query = globalSearch.params?.q || "", needle = query.toLocaleLowerCase();
  for (const item of data.items) {
    const button = node("button", "global-result"); button.type = "button";
    const top = node("div", "global-result-top");
    top.append(node("span", "global-result-chat", item.chatName),node("span", "global-result-source", clientLabel(item.client)));
    const meta = node("div", "global-result-meta");
    meta.append(node("span", "", `${item.chatKind === "group" ? "群聊" : "私聊"} · ${item.sender}`),node("span", "", `${formatDate(item.time)} ${formatTime(item.time)}`));
    const preview = item.text || item.asset?.filename || item.quote?.text || item.links?.[0] || `[${labels[item.kind] || "消息"}]`;
    const text = node("div", "global-result-text"); appendHighlightedText(text, preview, query); button.append(top,meta,text);
    button.append(node("div", "global-result-origin", [item.source, item.origin].filter(Boolean).join(" · ")));
    if (needle && !String(preview).toLocaleLowerCase().includes(needle)) {
      const candidates = [["引用",item.quote?.text],["文件",item.asset?.filename],["链接",...(item.links || [])],["说明",item.description]];
      const match = candidates.flatMap(([label,...values]) => values.map((value) => [label,value])).find(([,value]) => String(value || "").toLocaleLowerCase().includes(needle));
      if (match) { const extra = node("div", "global-result-extra"); appendHighlightedText(extra, `${match[0]}：${match[1]}`, query); button.append(extra); }
    }
    button.addEventListener("click", () => jumpGlobalResult(item)); host.append(button);
  }
  renderGlobalPagination();
}
function renderGlobalPagination() {
  const data = globalSearch.result, box = $("#global-pagination");
  if (!data || data.total <= data.pageSize) { box.hidden = true; return; }
  const pages = Math.ceil(data.total / data.pageSize);
  const building = Boolean(data.coverage.building || data.coverage.pending || globalSearch.status?.state === "building");
  box.hidden = false; $("#global-previous").disabled = building || data.page <= 1;
  $("#global-next").disabled = building || data.page >= pages;
  $("#global-page-label").textContent = building ? "索引完成后可翻页" : `${data.page} / ${pages}`;
}
async function jumpGlobalResult(item) {
  const jumpSerial = ++globalSearch.jumpSerial, selectionSerial = state.serial;
  $("#global-caption").textContent = "正在定位聊天记录…";
  try {
    const params = new URLSearchParams({record:String(item.recordId),chat:item.chatKey,fingerprint:item.fingerprint});
    const context = await api(`/api/global/context?${params}`);
    if (jumpSerial !== globalSearch.jumpSerial || selectionSerial !== state.serial) return;
    let chat = state.chats.find((value) => value.key === context.chatKey);
    if (!chat) { await loadCatalog(); chat = state.chats.find((value) => value.key === context.chatKey); }
    if (!chat) throw new Error("会话不在当前资料中，请重新搜索");
    state.mode = "all"; state.source = ""; state.client = ""; $("#source-filter").value = ""; $("#client-filter").value = ""; $("#conversation-search").value = "";
    document.querySelectorAll("[data-mode]").forEach((button) => {
      const active = button.dataset.mode === "all"; button.classList.toggle("active",active);
      if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected",String(active));
    });
    const expected = state.serial + 1; await selectChat(chat,true);
    if (jumpSerial !== globalSearch.jumpSerial || state.serial !== expected) return;
    if (state.timelineController) state.timelineController.abort();
    state.timelineSerial++; state.loading = false; state.messages = context.messages;
    state.older = context.older; state.newer = context.newer; state.highlighted = context.target;
    renderMessages();
    const match = $("#chat-scroll").querySelector(`[data-start="${context.target}"]`);
    if (match) match.scrollIntoView({block:"center"});
    if (window.innerWidth <= 760) closeGlobalSearch();
    else renderGlobalResults();
  } catch (error) {
    if (jumpSerial === globalSearch.jumpSerial) { toast(error.message); if (globalSearch.result) renderGlobalResults(); }
  }
}

document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
$("#open-global-search").addEventListener("click", () => { if ($("#global-panel").hidden) openGlobalSearch(); else closeGlobalSearch(); });
$("#close-global-search").addEventListener("click", closeGlobalSearch);
$("#global-search-form").addEventListener("submit", (event) => { event.preventDefault(); runGlobalSearch(1); });
for (const selector of ["#global-scope","#global-client","#global-source"]) $(selector).addEventListener("change", () => { if ($("#global-query").value.trim()) runGlobalSearch(1); });
$("#global-apply-filters").addEventListener("click", () => { $("#global-more").open = false; if ($("#global-query").value.trim()) runGlobalSearch(1); });
$("#global-clear-filters").addEventListener("click", () => {
  $("#global-category").value = "all"; $("#global-sender").value = ""; $("#global-from").value = ""; $("#global-to").value = "";
  $("#global-more").open = false; if ($("#global-query").value.trim()) runGlobalSearch(1);
});
$("#global-retry").addEventListener("click", async () => {
  $("#global-retry").disabled = true;
  try { showGlobalStatus(await api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({retry:true})})); }
  catch (error) { showGlobalError(error.message); }
  finally { $("#global-retry").disabled = false; }
});
$("#global-previous").addEventListener("click", () => { if (globalSearch.result && globalSearch.result.page > 1) runGlobalSearch(globalSearch.result.page - 1); });
$("#global-next").addEventListener("click", () => { if (globalSearch.result) runGlobalSearch(globalSearch.result.page + 1); });
$("#conversation-search").addEventListener("input", renderList);
$("#source-filter").addEventListener("change", (event) => { state.source = event.target.value; setMode(state.mode); });
$("#client-filter").addEventListener("change", (event) => { state.client = event.target.value; setMode(state.mode); });
$("#add-source").addEventListener("click", openSource); $("#add-source-footer").addEventListener("click", openSource);
document.querySelectorAll(".dialog-close,.dialog-cancel").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
$("#source-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = $("#source-submit"), error = $("#source-error"); button.disabled = true; button.textContent = "正在接入…"; error.hidden = true;
  try {
    const result = await api("/api/sources", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:$("#source-path").value})});
    await loadCatalog(); $("#source-dialog").close(); toast(result.indexWarning || `已接入 ${result.group} 个群聊、${result.direct} 个私聊${result.warnings ? `，${result.warnings} 项资料需检查` : ""}`);
    if (result.direct && state.mode === "direct") setMode("direct");
    else if (!state.selected && filteredChats().length) selectChat(filteredChats()[0], false);
  } catch (exception) { error.textContent = exception.message; error.hidden = false; }
  finally { button.disabled = false; button.textContent = "接入资料"; }
});
$("#toggle-message-search").addEventListener("click", () => { if ($("#history-panel").hidden) openHistorySearch(); else closeHistorySearch(); });
$("#close-history-search").addEventListener("click", closeHistorySearch);
$("#history-search-form").addEventListener("submit", (event) => { event.preventDefault(); runHistorySearch(); });
document.querySelectorAll(".history-tab").forEach((button) => button.addEventListener("click", () => { setSearchCategory(button.dataset.category); runHistorySearch(); }));
$("#history-sender").addEventListener("input", () => { state.search.senderId = ""; });
document.querySelectorAll("[data-apply]").forEach((button) => button.addEventListener("click", () => { button.closest("details").open = false; runHistorySearch(); }));
document.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.clear === "sender") { $("#history-sender").value = ""; state.search.senderId = ""; }
  else { $("#history-from").value = ""; $("#history-to").value = ""; }
  button.closest("details").open = false; runHistorySearch();
}));
$("#clear-history-filters").addEventListener("click", () => {
  $("#history-sender").value = ""; $("#history-from").value = ""; $("#history-to").value = ""; state.search.senderId = ""; runHistorySearch();
});
$("#toggle-details").addEventListener("click", () => { if (!$("#history-panel").hidden) closeHistorySearch(); closeGlobalSearch(); $("#details-panel").classList.toggle("open"); });
$("#details-close").addEventListener("click", () => $("#details-panel").classList.remove("open"));
$("#mobile-back").addEventListener("click", () => { $("#app").classList.remove("mobile-chat"); $("#details-panel").classList.remove("open"); closeHistorySearch(); closeGlobalSearch(); });
$("#chat-scroll").addEventListener("wheel", (event) => { if (event.deltaY < 0 && event.currentTarget.scrollTop <= 2) loadOlder(); }, {passive:true});

loadCatalog().then(() => {
  const requested = new URLSearchParams(location.search).get("chat");
  const first = requested ? state.chats.find((chat) => chat.key === requested) : filteredChats()[0];
  if (first) selectChat(first, false);
  else { showEmpty(); if (requested) toast("指定会话不可用，请返回解析工具重新打开"); }
})
  .catch((error) => { $("#conversation-list").replaceChildren(node("div", "list-loading", `资料读取失败：${error.message}`)); });
