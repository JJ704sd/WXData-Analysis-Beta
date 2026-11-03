#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Local chat archive viewer; generated from the source directory.
from __future__ import annotations

_BUNDLED_ASSETS = {'index.html': '<!doctype html>\n<html lang="zh-CN">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <meta name="color-scheme" content="light">\n  <title>会话档案</title>\n  <link rel="stylesheet" href="/style.css">\n  <script src="/app.js" defer></script>\n</head>\n<body>\n  <svg class="sprite" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">\n    <symbol id="i-chat" viewBox="0 0 24 24"><path d="M20 11.2c0 4.1-3.6 7.4-8 7.4-1 0-2-.2-2.9-.5L5 20l.8-3.7C4.7 15 4 13.2 4 11.2c0-4.1 3.6-7.4 8-7.4s8 3.3 8 7.4Z"/></symbol>\n    <symbol id="i-people" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3"/><path d="M3.8 19v-1.1c0-2.5 2.3-4.3 5.2-4.3s5.2 1.8 5.2 4.3V19M15.4 5.5a3 3 0 0 1 0 5.7M16.6 13.8c2.1.4 3.6 1.9 3.6 4V19"/></symbol>\n    <symbol id="i-person" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5"/><path d="M5.4 19v-.7c0-3.2 2.9-5.5 6.6-5.5s6.6 2.3 6.6 5.5v.7Z"/></symbol>\n    <symbol id="i-search" viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="6.3"/><path d="m15.5 15.5 4.2 4.2"/></symbol>\n    <symbol id="i-chevron" viewBox="0 0 24 24"><path d="m9 5 7 7-7 7"/></symbol>\n    <symbol id="i-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>\n    <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5Z"/><path d="M3 10h18"/></symbol>\n    <symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 10.5v5M12 7.2h.01"/></symbol>\n    <symbol id="i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></symbol>\n    <symbol id="i-arrow-left" viewBox="0 0 24 24"><path d="m14 5-7 7 7 7M7 12h13"/></symbol>\n    <symbol id="i-image" viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9" r="1.3"/><path d="m4.5 17 5-5 3.3 3 2.3-2.2 4.4 4.2"/></symbol>\n    <symbol id="i-file" viewBox="0 0 24 24"><path d="M6 3.5h8l4 4v13H6zM14 3.5v4h4M9 12h6M9 15.5h6"/></symbol>\n    <symbol id="i-lock" viewBox="0 0 24 24"><rect x="5.5" y="10" width="13" height="10" rx="2"/><path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10M12 14v2.5"/></symbol>\n    <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>\n    <symbol id="i-dots" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></symbol>\n  </svg>\n\n  <div class="app-shell" id="app">\n    <aside class="rail" aria-label="导航">\n      <div class="rail-identity"><div class="brand-mark" aria-label="会话档案"><span></span><span></span><span></span><span></span></div><div class="identity-copy"><strong>会话档案</strong><span>本地浏览</span></div></div>\n      <div class="rail-items">\n        <button class="rail-button active" data-mode="all" title="全部会话" aria-label="全部会话"><svg><use href="#i-chat"/></svg><span class="rail-label">消息</span></button>\n        <div class="rail-section">分组</div>\n        <button class="rail-button" data-mode="direct" title="私聊" aria-label="私聊"><svg><use href="#i-person"/></svg><span class="rail-label">单聊</span></button>\n        <button class="rail-button" data-mode="group" title="群聊" aria-label="群聊"><svg><use href="#i-people"/></svg><span class="rail-label">群聊</span></button>\n      </div>\n      <div class="rail-bottom"><span class="rail-status" title="仅在本机读取"><svg><use href="#i-lock"/></svg></span><span>本地</span></div>\n    </aside>\n\n    <section class="conversation-panel" aria-label="会话列表">\n      <div class="panel-heading">\n        <div><div class="product-kicker">CHAT ARCHIVE</div><h1>会话档案<span class="heading-dot"></span></h1></div>\n        <button id="add-source" class="icon-button add-source" title="接入导出资料" aria-label="接入导出资料"><svg><use href="#i-plus"/></svg></button>\n      </div>\n      <div class="search-wrap"><svg><use href="#i-search"/></svg><input id="conversation-search" type="search" placeholder="搜索会话名称" autocomplete="off" aria-label="搜索会话名称"><kbd>⌕</kbd></div>\n      <button class="global-entry" id="open-global-search" type="button"><svg><use href="#i-search"/></svg><span>全局搜索</span><small>跨会话查找记录</small><svg><use href="#i-chevron"/></svg></button>\n      <div class="filters" role="tablist" aria-label="会话类型">\n        <button class="filter active" data-mode="all" role="tab" aria-selected="true">全部 <span id="count-all"></span></button>\n        <button class="filter" data-mode="group" role="tab" aria-selected="false">群聊 <span id="count-group"></span></button>\n        <button class="filter" data-mode="direct" role="tab" aria-selected="false">私聊 <span id="count-direct"></span></button>\n      </div>\n      <div class="list-caption"><span id="list-caption">全部会话</span><div class="caption-filters"><label class="source-select"><span class="sr-only">筛选平台</span><select id="client-filter"><option value="">全部平台</option><option value="wecom">企微</option><option value="wechat">个微</option><option value="unknown">待确认</option></select><svg><use href="#i-chevron-down"/></svg></label><label class="source-select"><span class="sr-only">筛选资料来源</span><select id="source-filter"><option value="">全部来源</option></select><svg><use href="#i-chevron-down"/></svg></label></div></div>\n      <div id="conversation-list" class="conversation-list" aria-live="polite"><div class="list-loading">正在读取本地资料…</div></div>\n      <div class="panel-footer"><span class="online-dot"></span><span>本地资料 · 只读浏览</span><button id="add-source-footer">接入资料 <span>＋</span></button></div>\n    </section>\n\n    <main class="chat-panel" id="chat-panel">\n      <header class="chat-header">\n        <button class="icon-button mobile-back" id="mobile-back" aria-label="返回会话列表"><svg><use href="#i-arrow-left"/></svg></button>\n        <div class="header-avatar" id="header-avatar"></div>\n        <div class="chat-title"><h2 id="chat-name">选择一个会话</h2><div class="chat-subtitle" id="chat-subtitle">从左侧打开本地导出的聊天记录</div></div>\n        <div class="header-actions"><button class="icon-button" id="toggle-message-search" title="查找聊天记录" aria-label="查找聊天记录" disabled><svg><use href="#i-search"/></svg></button><button class="icon-button details-button" id="toggle-details" title="会话信息" aria-label="会话信息" disabled><svg><use href="#i-info"/></svg></button></div>\n      </header>\n      <div class="chat-scroll" id="chat-scroll"><div class="empty-welcome"><div class="empty-icon"><svg><use href="#i-chat"/></svg></div><h3>查看一段会话</h3><p>选择左侧会话，按原始顺序浏览本地导出的记录。</p></div></div>\n      <div class="archive-bar"><svg><use href="#i-lock"/></svg><div><strong id="archive-mode">本地归档视图</strong><span id="archive-description">这里展示已导出的记录，无法发送消息</span></div><span class="archive-bar-right">READ ONLY</span></div>\n    </main>\n\n    <aside class="details-panel" id="details-panel" aria-label="会话信息">\n      <div class="details-heading"><h2>会话信息</h2><button class="icon-button details-close" id="details-close" aria-label="关闭会话信息"><svg><use href="#i-close"/></svg></button></div>\n      <div id="details-content" class="details-content"><p class="muted">选择会话后查看资料信息。</p></div>\n      <div class="details-footer"><svg><use href="#i-lock"/></svg><span>数据留在本机</span></div>\n    </aside>\n    <aside class="history-panel" id="history-panel" aria-label="查找聊天记录" hidden>\n      <div class="history-heading"><h2>查找聊天记录</h2><button class="icon-button" id="close-history-search" aria-label="关闭聊天记录搜索"><svg><use href="#i-close"/></svg></button></div>\n      <div class="history-tabs" role="tablist" aria-label="聊天记录类型">\n        <button class="history-tab active" type="button" role="tab" aria-selected="true" data-category="all">全部</button>\n        <button class="history-tab" type="button" role="tab" aria-selected="false" data-category="file">文件</button>\n        <button class="history-tab" type="button" role="tab" aria-selected="false" data-category="media">图片与视频</button>\n        <button class="history-tab" type="button" role="tab" aria-selected="false" data-category="link">链接</button>\n        <button class="history-tab" type="button" role="tab" aria-selected="false" data-category="miniprogram">小程序</button>\n      </div>\n      <form class="history-search-form" id="history-search-form"><svg><use href="#i-search"/></svg><input id="history-query" type="search" maxlength="200" placeholder="搜索聊天记录" aria-label="搜索聊天记录" autocomplete="off"><button type="submit">搜索</button></form>\n      <div class="history-filters">\n        <details class="history-filter" id="sender-filter"><summary>发送人<svg><use href="#i-chevron-down"/></svg></summary><div class="filter-popover"><label for="history-sender">按发送人名称筛选</label><input id="history-sender" type="search" list="sender-suggestions" maxlength="200" placeholder="输入名称"><datalist id="sender-suggestions"></datalist><p>输入名称时按显示名称匹配。</p><div><button type="button" data-clear="sender">清除</button><button type="button" data-apply="sender">应用</button></div></div></details>\n        <details class="history-filter" id="date-filter"><summary>日期<svg><use href="#i-chevron-down"/></svg></summary><div class="filter-popover date-popover"><label for="history-from">开始日期</label><input id="history-from" type="date"><label for="history-to">结束日期</label><input id="history-to" type="date"><div><button type="button" data-clear="date">清除</button><button type="button" data-apply="date">应用</button></div></div></details>\n        <button type="button" class="clear-filters" id="clear-history-filters" hidden>清除筛选</button>\n      </div>\n      <div class="history-caption" id="history-caption">当前会话的本地聊天记录</div>\n      <div class="history-results" id="history-results" aria-live="polite"></div>\n    </aside>\n    <aside class="global-panel" id="global-panel" aria-label="全局搜索" hidden>\n      <div class="global-heading"><div><h2>全局搜索</h2><p>查找群聊和私聊中的记录</p></div><button class="icon-button" id="close-global-search" aria-label="关闭全局搜索"><svg><use href="#i-close"/></svg></button></div>\n      <form class="global-search-form" id="global-search-form"><svg><use href="#i-search"/></svg><input id="global-query" type="search" maxlength="200" placeholder="搜索全部聊天记录" aria-label="搜索全部聊天记录" autocomplete="off"><button type="submit">搜索</button></form>\n      <div class="global-filters"><label><span class="sr-only">会话范围</span><select id="global-scope"><option value="all">全部会话</option><option value="group">群聊</option><option value="direct">私聊</option></select></label><label><span class="sr-only">平台</span><select id="global-client"><option value="all">全部平台</option><option value="wecom">企微</option><option value="wechat">个微</option><option value="unknown">待确认</option></select></label><label><span class="sr-only">资料来源</span><select id="global-source"><option value="">全部资料</option></select></label><details class="global-more" id="global-more"><summary>更多筛选<svg><use href="#i-chevron-down"/></svg></summary><div class="global-filter-popover"><label for="global-category">记录类型</label><select id="global-category"><option value="all">全部类型</option><option value="file">文件</option><option value="media">图片与视频</option><option value="link">链接</option><option value="miniprogram">小程序</option></select><label for="global-sender">发送人名称</label><input id="global-sender" type="search" maxlength="200" placeholder="输入发送人名称"><div class="global-date-fields"><label>开始日期<input id="global-from" type="date"></label><label>结束日期<input id="global-to" type="date"></label></div><div class="global-filter-actions"><button id="global-clear-filters" type="button">清除</button><button id="global-apply-filters" type="button">应用</button></div></div></details></div>\n      <div class="global-index" id="global-index"><div class="global-index-top"><span id="global-index-text">准备本地搜索</span><button type="button" id="global-retry" hidden>重试</button></div><progress id="global-index-progress" max="1" value="0"></progress></div>\n      <div class="global-caption" id="global-caption">输入关键词，搜索已准备的会话</div>\n      <div class="global-results" id="global-results" aria-live="polite"><div class="global-empty">输入关键词后，可查找群聊和私聊中的记录。</div></div>\n      <div class="global-pagination" id="global-pagination" hidden><button id="global-previous" type="button">上一页</button><span id="global-page-label"></span><button id="global-next" type="button">下一页</button></div>\n    </aside>\n  </div>\n\n  <dialog id="source-dialog" class="dialog source-dialog">\n    <form id="source-form">\n      <div class="dialog-heading"><div><span class="dialog-kicker">添加本地资料</span><h2>接入会话导出</h2></div><button type="button" class="icon-button dialog-close" aria-label="关闭"><svg><use href="#i-close"/></svg></button></div>\n      <p>粘贴 WeBridge 导出文件夹或 ZIP 的完整路径。可接入单个会话，也可接入包含多个会话的目录。</p>\n      <label for="source-path">文件夹或 ZIP 路径</label><input id="source-path" name="path" placeholder="例如 D:\\导出资料\\聊天记录.zip" autocomplete="off" required>\n      <div class="form-hint"><svg><use href="#i-info"/></svg><span>每个会话需包含 <b>manifest.json</b> 和 <b>messages.jsonl</b>。支持企微、个微的群聊和私聊，资料保持原样。</span></div>\n      <p class="form-error" id="source-error" role="alert" hidden></p>\n      <div class="dialog-actions"><button type="button" class="button-plain dialog-cancel">取消</button><button type="submit" class="button-primary" id="source-submit">接入资料</button></div>\n    </form>\n  </dialog>\n  <dialog id="record-dialog" class="dialog record-dialog"><div class="dialog-heading"><div><span class="dialog-kicker">来源记录</span><h2>原始消息字段</h2></div><button type="button" class="icon-button dialog-close" aria-label="关闭"><svg><use href="#i-close"/></svg></button></div><p>内容来自当前会话的 messages.jsonl。</p><pre id="record-content"></pre></dialog>\n  <div class="toast" id="toast" role="status" aria-live="polite"></div>\n</body>\n</html>\n', 'app.js': '"use strict";\n\nconst $ = (selector) => document.querySelector(selector);\nconst state = {chats: [], mode: "all", source: "", client: "", selected: null, messages: [], older: null,\n  newer: null, highlighted: null, detail: null, expanded: false, loading: false, serial: 0,\n  timelineSerial: 0, controller: null, timelineController: null,\n  search: {category: "all", senderId: "", items: [], older: null, total: null, skipped: 0,\n    parameters: null, controller: null, serial: 0, loading: false}};\nconst globalSearch = {serial:0, jumpSerial:0, panelSerial:0, controller:null, timer:null, params:null,\n  result:null, status:null, autoSignature:"", lastRecover:0, loading:false};\nconst colors = ["#6f9bc7", "#78ad9d", "#d7a16f", "#9a8ec4", "#8babc5", "#c28f98", "#87a98b"];\nlet toastTimer;\n\nfunction node(tag, className = "", value = null) {\n  const element = document.createElement(tag);\n  if (className) element.className = className;\n  if (value !== null) element.textContent = value;\n  return element;\n}\nfunction icon(name) {\n  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");\n  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");\n  use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;\n}\nfunction avatar(name, kind = "direct") {\n  const result = node("div", `avatar ${kind}`);\n  const chars = [...(name || "会话")].filter((char) => /[\\p{L}\\p{N}]/u.test(char));\n  const hash = [...(name || "会话")].reduce((a, c) => ((a * 31 + c.codePointAt(0)) >>> 0), 7);\n  if (kind === "group") {\n    for (let i = 0; i < 4; i++) {\n      const tile = node("span", "", chars[i % Math.max(chars.length, 1)] || "•");\n      tile.style.backgroundColor = colors[(hash + i * 2) % colors.length]; result.append(tile);\n    }\n  } else {\n    result.textContent = chars.slice(0, 1).join("") || "?";\n    result.style.backgroundColor = colors[hash % colors.length];\n  }\n  result.setAttribute("aria-hidden", "true"); return result;\n}\nfunction toast(value) {\n  const item = $("#toast"); item.textContent = value; item.classList.add("visible");\n  clearTimeout(toastTimer); toastTimer = setTimeout(() => item.classList.remove("visible"), 2800);\n}\nasync function api(path, options = {}) {\n  const response = await fetch(path, {...options, cache: "no-store"});\n  const payload = await response.json();\n  if (!response.ok) throw new Error(payload.error || "资料读取失败");\n  return payload;\n}\nfunction sourceLabel(source) {\n  const clean = source.replace(/^资料[：:]?/, ""), batch = clean.split(" / ").at(-1);\n  const stamp = batch.match(/^(\\d{4})(\\d{2})(\\d{2})-(\\d{2})(\\d{2})(\\d{2})/);\n  return stamp ? `${stamp[1]}/${stamp[2]}/${stamp[3]} ${stamp[4]}:${stamp[5]}:${stamp[6]}` : clean;\n}\nfunction clientLabel(client) { return ({wecom:"企微",wechat:"个微",unknown:"来源未标注",conflict:"来源待核对"})[client] || "来源未标注"; }\nfunction matchesClient(chat) { return !state.client || (state.client === "unknown" ? !["wecom", "wechat"].includes(chat.client) : chat.client === state.client); }\nfunction formatNumber(value) { return Number(value || 0).toLocaleString("zh-CN"); }\nfunction formatDate(value) {\n  if (!value) return "时间未知";\n  const match = String(value).match(/^(\\d{4})-(\\d{2})-(\\d{2})/);\n  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : "时间未知";\n}\nfunction formatTime(value) {\n  const match = String(value || "").match(/[T ](\\d{2}:\\d{2})(?::\\d{2})?/);\n  return match ? match[1] : "时间未知";\n}\nfunction dateKey(value) { return String(value || "").match(/^\\d{4}-\\d{2}-\\d{2}/)?.[0] || "unknown"; }\n\nfunction filteredChats() {\n  const query = $("#conversation-search").value.trim().toLocaleLowerCase();\n  return state.chats.filter((chat) => (!state.source || chat.source === state.source)\n    && matchesClient(chat)\n    && (state.mode === "all" || chat.kind === state.mode)\n    && (!query || chat.name.toLocaleLowerCase().includes(query)));\n}\nfunction updateCounts() {\n  const chats = state.chats.filter((chat) => (!state.source || chat.source === state.source) && matchesClient(chat));\n  $("#count-all").textContent = chats.length;\n  $("#count-group").textContent = chats.filter((chat) => chat.kind === "group").length;\n  $("#count-direct").textContent = chats.filter((chat) => chat.kind === "direct").length;\n}\nfunction renderList() {\n  updateCounts();\n  const list = $("#conversation-list"); list.replaceChildren();\n  const chats = filteredChats();\n  $("#list-caption").textContent = `${state.mode === "all" ? "全部会话" : state.mode === "group" ? "群聊" : "私聊"} · ${chats.length}`;\n  if (!chats.length) {\n    const empty = node("div", "list-empty");\n    const noDirect = state.mode === "direct" && !state.chats.some((chat) => chat.kind === "direct" && (!state.source || chat.source === state.source));\n    empty.append(node("div", "", !state.chats.length ? "还没有接入聊天记录" : noDirect ? "当前来源没有私聊导出" : "没有找到匹配的会话"));\n    if (noDirect || !state.chats.length) {\n      const button = node("button", "", noDirect ? "接入私聊导出" : "接入导出资料"); button.type = "button"; button.addEventListener("click", openSource); empty.append(button);\n    }\n    list.append(empty); return;\n  }\n  for (const chat of chats) {\n    const item = node("button", `conversation-item${chat.key === state.selected?.key ? " active" : ""}`);\n    item.type = "button"; item.setAttribute("aria-label", `${chat.name}，${clientLabel(chat.client)}，${chat.source}，${chat.kind === "group" ? "群聊" : "私聊"}`);\n    item.append(avatar(chat.name, chat.kind));\n    const copy = node("div", "conversation-copy");\n    const top = node("div", "conversation-top"); top.append(node("span", "conversation-title", chat.name), node("span", `client-badge ${chat.client}`, clientLabel(chat.client)));\n    const bottom = node("div", "conversation-bottom"); bottom.append(node("span", "conversation-description", chat.available ? `${formatNumber(chat.rows)} 条记录 · ${sourceLabel(chat.source)}` : "导出文件不完整"), node("span", "conversation-kind", chat.kind === "group" ? "群聊" : "私聊"));\n    copy.append(top, bottom); item.append(copy);\n    item.addEventListener("click", () => selectChat(chat)); list.append(item);\n  }\n}\nfunction setMode(mode) {\n  state.mode = mode;\n  document.querySelectorAll("[data-mode]").forEach((button) => {\n    const active = button.dataset.mode === mode; button.classList.toggle("active", active);\n    if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected", String(active));\n  });\n  renderList();\n  const candidates = filteredChats();\n  if (!candidates.some((chat) => chat.key === state.selected?.key)) {\n    if (candidates.length) selectChat(candidates[0], false);\n    else showEmpty(mode === "direct" && !state.chats.some((chat) => chat.kind === "direct") ? "direct" : "filter");\n  }\n}\nfunction openSource() {\n  $("#source-error").hidden = true; $("#source-path").value = "";\n  if (!$("#source-dialog").open) $("#source-dialog").showModal();\n  setTimeout(() => $("#source-path").focus(), 50);\n}\nfunction showEmpty(kind = "initial") {\n  if (state.controller) state.controller.abort();\n  if (state.timelineController) state.timelineController.abort();\n  closeHistorySearch(); state.serial++; state.timelineSerial++; state.selected = null; state.messages = []; state.older = null; state.newer = null; state.detail = null;\n  $("#chat-name").textContent = kind === "direct" ? "私聊" : "选择一个会话";\n  $("#chat-subtitle").textContent = kind === "direct" ? "本地资料中还没有私聊导出" : "从左侧打开本地导出的聊天记录";\n  $("#archive-mode").textContent = "本地归档视图"; $("#archive-description").textContent = "这里展示本地记录，无法发送消息";\n  $("#header-avatar").replaceChildren(); $("#toggle-message-search").disabled = true; $("#toggle-details").disabled = true;\n  const content = node("div", "empty-welcome"); const em = node("div", "empty-icon"); em.append(icon(kind === "direct" ? "person" : "chat"));\n  const isNew = !state.chats.length;\n  content.append(em, node("h3", "", isNew ? "从导出资料开始" : kind === "direct" ? "还没有私聊记录" : "查看一段会话"),\n    node("p", "", isNew ? "接入企微或个微的聊天导出，在本机浏览群聊和私聊。" : kind === "direct" ? "接入私聊导出后，就可以在这里浏览会话。" : "选择左侧会话，按原始顺序浏览本地导出的记录。"));\n  if (kind === "direct" || isNew) { const button = node("button", "button-primary", isNew ? "接入导出资料" : "接入私聊导出"); button.addEventListener("click", openSource); content.append(button); }\n  $("#chat-scroll").replaceChildren(content); $("#details-content").replaceChildren(node("p", "muted", "选择会话后查看资料信息。"));\n  renderList();\n}\nfunction buildSources() {\n  const select = $("#source-filter"); select.replaceChildren(new Option("全部来源", ""));\n  for (const source of [...new Set(state.chats.map((chat) => chat.source))].sort()) select.add(new Option(source, source));\n  if (![...select.options].some((option) => option.value === state.source)) state.source = "";\n  select.value = state.source;\n}\nasync function loadCatalog() {\n  const data = await api("/api/conversations"); state.chats = data.chats;\n  buildSources(); fillGlobalSources(); renderList();\n  if (data.issues.length) toast("部分资料读取失败，请检查本地文件");\n  return data;\n}\nasync function selectChat(chat, mobile = true) {\n  if (!chat.available) { toast("该会话的导出文件不完整"); return; }\n  if (state.controller) state.controller.abort();\n  if (state.timelineController) state.timelineController.abort();\n  closeHistorySearch();\n  state.controller = new AbortController(); const signal = state.controller.signal; const serial = ++state.serial;\n  const timelineSerial = ++state.timelineSerial;\n  state.selected = chat; state.messages = []; state.older = null; state.newer = null; state.highlighted = null; state.detail = null; state.expanded = false; state.loading = false;\n  $("#chat-name").textContent = chat.name;\n  $("#chat-subtitle").textContent = `${clientLabel(chat.client)}  /  ${chat.kind === "group" ? "群聊" : "私聊"}  /  ${chat.source}  /  ${formatNumber(chat.rows)} 条${chat.preview ? "解析记录" : "导出记录"}`;\n  $("#archive-mode").textContent = chat.preview ? "解析预览 · 尚非正式导出" : "本地归档视图";\n  $("#archive-description").textContent = chat.preview ? "复核和导出状态以解析工具为准；关闭工具后页面停止" : "这里展示已导出的记录，无法发送消息";\n  $("#header-avatar").replaceChildren(avatar(chat.name, chat.kind));\n  $("#toggle-message-search").disabled = false; $("#toggle-details").disabled = false;\n  $("#chat-scroll").replaceChildren(node("div", "list-loading", "正在读取聊天记录…"));\n  $("#details-content").replaceChildren(node("p", "muted", "正在读取会话信息…"));\n  if (mobile) $("#app").classList.add("mobile-chat");\n  renderList();\n  const params = new URLSearchParams({chat: chat.key});\n  const messageTask = api(`/api/messages?${params}`, {signal}).then((data) => {\n    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;\n    state.messages = data.messages; state.older = data.older; renderMessages();\n    const scroll = $("#chat-scroll"); scroll.scrollTop = scroll.scrollHeight;\n  }).catch((error) => {\n    if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) $("#chat-scroll").replaceChildren(node("div", "list-loading", `无法读取消息：${error.message}`));\n  });\n  const detailTask = api(`/api/detail?${params}`, {signal}).then((data) => {\n    if (serial !== state.serial) return; state.detail = data; renderDetails();\n  }).catch((error) => {\n    if (error.name !== "AbortError" && serial === state.serial) $("#details-content").replaceChildren(node("p", "muted", `无法读取会话信息：${error.message}`));\n  });\n  await Promise.all([messageTask, detailTask]);\n}\nasync function loadOlder() {\n  if (!state.selected || state.older === null || state.loading) return;\n  state.loading = true; const serial = state.serial, timelineSerial = state.timelineSerial; const scroll = $("#chat-scroll");\n  const height = scroll.scrollHeight, top = scroll.scrollTop;\n  const button = scroll.querySelector(".older-button"); if (button) { button.disabled = true; button.textContent = "读取中…"; }\n  try {\n    const params = new URLSearchParams({chat: state.selected.key, before: String(state.older)});\n    const data = await api(`/api/messages?${params}`, {signal: state.controller.signal});\n    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;\n    state.messages = [...data.messages, ...state.messages]; state.older = data.older; renderMessages();\n    scroll.scrollTop = scroll.scrollHeight - height + top;\n  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) { toast(error.message); renderMessages(); } }\n  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }\n}\nasync function loadNewer() {\n  if (!state.selected || state.newer === null || state.loading) return;\n  state.loading = true; const serial = state.serial, timelineSerial = state.timelineSerial, scroll = $("#chat-scroll");\n  const height = scroll.scrollHeight;\n  try {\n    const params = new URLSearchParams({chat: state.selected.key, after: String(state.newer)});\n    const data = await api(`/api/messages-after?${params}`, {signal: state.controller.signal});\n    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;\n    state.messages = [...state.messages, ...data.messages]; state.newer = data.newer; renderMessages();\n    scroll.scrollTop = Math.max(0, height - scroll.clientHeight - 40);\n  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) toast(error.message); }\n  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }\n}\nasync function replaceTimeline(path, target = null) {\n  if (!state.selected) return;\n  if (state.timelineController) state.timelineController.abort();\n  state.timelineController = new AbortController();\n  const serial = state.serial, timelineSerial = ++state.timelineSerial;\n  state.loading = true;\n  try {\n    const data = await api(path, {signal: state.timelineController.signal});\n    if (serial !== state.serial || timelineSerial !== state.timelineSerial) return;\n    state.messages = data.messages; state.older = data.older; state.newer = data.newer ?? null;\n    state.highlighted = target; renderMessages();\n    const scroll = $("#chat-scroll");\n    if (target !== null) {\n      const found = scroll.querySelector(`[data-start="${target}"]`);\n      if (found) found.scrollIntoView({block:"center"});\n    } else scroll.scrollTop = scroll.scrollHeight;\n  } catch (error) { if (error.name !== "AbortError" && serial === state.serial && timelineSerial === state.timelineSerial) toast(error.message); }\n  finally { if (serial === state.serial && timelineSerial === state.timelineSerial) state.loading = false; }\n}\nfunction loadLatest() {\n  if (!state.selected) return;\n  const params = new URLSearchParams({chat:state.selected.key});\n  replaceTimeline(`/api/messages?${params}`);\n}\nfunction jumpToResult(message) {\n  if (!state.selected || message.kind === "invalid") return;\n  const params = new URLSearchParams({chat:state.selected.key,start:String(message.start),end:String(message.end)});\n  if (window.innerWidth <= 760) closeHistorySearch();\n  replaceTimeline(`/api/message-context?${params}`, message.start);\n}\nfunction renderMessages() {\n  const scroll = $("#chat-scroll"); scroll.replaceChildren();\n  const intro = node("div", "chat-intro", "按记录顺序展示"); scroll.append(intro);\n  if (state.newer !== null) {\n    const wrap = node("div", "latest-wrap"), latest = node("button", "latest-button", "回到最新记录");\n    latest.type = "button"; latest.addEventListener("click", loadLatest); wrap.append(latest); scroll.append(wrap);\n  }\n  if (state.older !== null) {\n    const wrap = node("div", "older-wrap"); const button = node("button", "older-button", "加载更早的记录");\n    button.type = "button"; button.addEventListener("click", loadOlder); wrap.append(button); scroll.append(wrap);\n  }\n  if (!state.messages.length) { scroll.append(node("div", "list-loading", "当前会话没有消息记录。")); return; }\n  let previous = null;\n  for (const message of state.messages) {\n    const day = dateKey(message.time);\n    if (day !== previous) { const sep = node("div", "date-separator"); sep.append(node("span", "", formatDate(message.time))); scroll.append(sep); previous = day; }\n    if (message.kind === "system") {\n      const system = node("div", `system-message${message.start === state.highlighted ? " highlighted-system" : ""}`, message.text || "系统消息");\n      system.dataset.start = String(message.start); scroll.append(system); continue;\n    }\n    const row = node("article", `message-row ${message.direction || "unknown"}${message.start === state.highlighted ? " highlighted" : ""}`);\n    row.dataset.start = String(message.start);\n    const senderButton = node("button", "sender-avatar"); senderButton.type = "button"; senderButton.title = "查找此人的消息";\n    senderButton.setAttribute("aria-label", `查找 ${message.sender || "发言人"} 的消息`); senderButton.append(avatar(message.sender, "direct"));\n    if (message.kind === "invalid" || (!message.senderId && (!message.sender || message.sender === "发言人未知"))) senderButton.disabled = true;\n    else senderButton.addEventListener("click", () => searchBySender(message.sender, message.senderId));\n    row.append(senderButton);\n    const content = node("div", "message-content"), meta = node("div", "message-meta");\n    meta.append(node("span", "sender", message.sender || "发言人未知"), node("time", "", formatTime(message.time)));\n    content.append(meta);\n    const bubble = node("div", `message-bubble${message.kind === "invalid" ? " invalid-message" : ""}`);\n    if (message.text) bubble.append(node("span", "", message.text));\n    const mediaKinds = {image:"图片", file:"文件", emoji:"表情", video:"视频", voice:"语音", record:"聊天记录", unknown:"未识别内容", miniprogram:"小程序"};\n    if (mediaKinds[message.kind]) appendMedia(bubble, message, mediaKinds[message.kind]);\n    if (message.kind === "link" && message.links?.length) {\n      const linkText = node("div", "message-link-text", message.links[0]); bubble.append(linkText);\n    }\n    if (message.kind === "quote") {\n      const quote = node("div", "message-quote");\n      quote.append(node("strong", "", message.quote?.sender || "引用"), node("span", "", `：${message.quote?.text || "引用内容未完整解析"}`)); bubble.append(quote);\n    }\n    if (!bubble.childNodes.length) bubble.append(node("span", "", "此条消息没有可显示的正文"));\n    content.append(bubble);\n    if (message.kind !== "invalid") {\n      const tools = node("div", "message-tools"), inspect = node("button", "", "查看原始记录");\n      inspect.type = "button"; inspect.addEventListener("click", () => showRecord(message)); tools.append(inspect); content.append(tools);\n    }\n    row.append(content); scroll.append(row);\n  }\n  if (state.newer !== null) {\n    const wrap = node("div", "older-wrap"), button = node("button", "older-button", "加载更新的记录");\n    button.type = "button"; button.addEventListener("click", loadNewer); wrap.append(button); scroll.append(wrap);\n  }\n}\nfunction appendMedia(bubble, message, label) {\n  const asset = message.asset || {};\n  const block = node("div", "message-media"), mediaIcon = node("span", "media-icon"); mediaIcon.append(icon(message.kind === "image" ? "image" : "file"));\n  const copy = node("div", "media-copy"); copy.append(node("div", "media-title", asset.filename || label));\n  let status = message.kind === "miniprogram" ? "小程序记录" : asset.path ? (asset.previewOnly ? "本地预览图" : "本地附件") : (asset.reason || "原文件未随导出提供");\n  copy.append(node("div", "media-state", status));\n  let url = "";\n  if (asset.path) {\n    const params = new URLSearchParams({chat: state.selected.key, path: asset.path}); url = `/api/asset?${params}`;\n    const link = node("a", "media-link", message.kind === "image" ? "打开图片" : "打开附件"); link.href = url; link.target = "_blank"; link.rel = "noopener"; copy.append(link);\n  }\n  block.append(mediaIcon, copy); bubble.append(block);\n  if (message.kind === "image" && url) {\n    const image = node("img", "message-image"); image.src = url; image.alt = asset.previewOnly ? "导出的图片预览" : "导出的图片";\n    image.loading = "lazy"; image.addEventListener("error", () => { image.remove(); copy.append(node("div", "media-state", "图片文件无法打开")); }); bubble.append(image);\n  }\n}\nfunction detailRow(label, value) { const row = node("div", "detail-row"); row.append(node("span", "", label), node("strong", "", value)); return row; }\nfunction renderDetails() {\n  const host = $("#details-content"); host.replaceChildren();\n  if (!state.detail || !state.selected) return;\n  const {chat, members, memberWarning, created, truncated} = state.detail;\n  const memberEntries = state.detail.memberEntries || members.map((name) => ({name,id:""}));\n  const suggestions = $("#sender-suggestions"); suggestions.replaceChildren();\n  for (const member of memberEntries) { const option = node("option"); option.value = member.name; suggestions.append(option); }\n  const profile = node("div", "details-profile"); profile.append(avatar(chat.name, chat.kind));\n  const text = node("div"); text.append(node("div", "details-profile-name", chat.name), node("div", "details-profile-sub", chat.kind === "group" ? "群聊档案" : "私聊档案")); profile.append(text); host.append(profile);\n  const basics = node("section", "detail-section"); basics.append(node("div", "section-title", "资料概况"));\n  basics.append(detailRow("平台", clientLabel(chat.client)), detailRow("会话类型", chat.kind === "group" ? "群聊" : "私聊"), detailRow(chat.preview ? "解析记录" : "导出记录", `${formatNumber(chat.rows)} 条`),\n    detailRow(chat.preview ? "解析时间" : "导出时间", created ? formatDate(created) : "未提供"), detailRow("复核状态", chat.reviewStatus || "未提供"));\n  if (chat.origin) basics.append(detailRow("来源标识", chat.origin));\n  const source = node("div", "detail-row"); source.append(node("span", "", "资料来源")); const tag = node("span", "source-tag", chat.source); source.append(tag); basics.append(source);\n  if (truncated) basics.append(node("p", "detail-note", "本次导出达到条数限制，记录可能不完整。")); host.append(basics);\n  if (chat.kind === "group") {\n    const section = node("section", "detail-section"), heading = node("div", "section-title");\n    heading.append(node("span", "", "成员资料"), node("span", "", members.length ? `${formatNumber(members.length)} 人` : "暂无资料")); section.append(heading);\n    if (members.length) {\n      const grid = node("div", "member-grid");\n      for (const member of memberEntries.slice(0, state.expanded ? members.length : 12)) {\n        const item = node("button", "member"); item.type = "button";\n        item.append(avatar(member.name, "direct"), node("span", "member-name", member.name));\n        item.title = `查找 ${member.name} 的消息`; item.setAttribute("aria-label", item.title);\n        item.addEventListener("click", () => searchBySender(member.name, member.id)); grid.append(item);\n      }\n      section.append(grid);\n      if (members.length > 12) { const more = node("button", "show-members", state.expanded ? "收起成员" : `查看全部 ${formatNumber(members.length)} 人`); more.addEventListener("click", () => { state.expanded = !state.expanded; renderDetails(); }); section.append(more); }\n      section.append(node("p", "member-note", memberWarning || "成员信息来自当前资料，可能与当前群成员不同。"));\n    } else section.append(node("p", "member-note", "当前资料没有提供成员名单。"));\n    host.append(section);\n  }\n  const note = node("section", "detail-section"); note.append(node("div", "section-title", "关于这份资料"), node("p", "detail-note", chat.preview ? "当前展示解析工具的本地阅读缓存。复核与正式导出仍需在解析工具中完成；图片和文件以实际可用状态为准。" : "内容仅覆盖已导出的本地记录。图片、文件等以每条消息的实际可用状态为准。")); host.append(note);\n}\n\nfunction setSearchCategory(category) {\n  state.search.category = category;\n  document.querySelectorAll(".history-tab").forEach((button) => {\n    const active = button.dataset.category === category;\n    button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));\n  });\n}\nfunction closeHistorySearch() {\n  if (state.search.controller) state.search.controller.abort();\n  state.search.serial++; state.search.loading = false;\n  $("#history-panel").hidden = true; $("#app").classList.remove("search-open");\n  $("#sender-filter").open = false; $("#date-filter").open = false;\n}\nfunction openHistorySearch(sender = "", senderId = "") {\n  if (!state.selected) return;\n  closeGlobalSearch();\n  $("#details-panel").classList.remove("open");\n  $("#history-panel").hidden = false; $("#app").classList.add("search-open");\n  $("#history-query").value = ""; $("#history-sender").value = sender;\n  $("#history-from").value = ""; $("#history-to").value = "";\n  state.search.senderId = senderId || ""; setSearchCategory("all");\n  runHistorySearch();\n  if (!sender) $("#history-query").focus();\n}\nfunction searchBySender(name, senderId) {\n  openHistorySearch(name || "", senderId || "");\n}\nfunction historyParameters() {\n  const sender = $("#history-sender").value.trim();\n  return {category:state.search.category, q:$("#history-query").value.trim(),\n    sender:state.search.senderId ? "" : sender, senderId:state.search.senderId,\n    from:$("#history-from").value, to:$("#history-to").value};\n}\nfunction syncHistoryFilters() {\n  const senderName = $("#history-sender").value.trim();\n  const sender = Boolean(senderName);\n  const date = Boolean($("#history-from").value || $("#history-to").value);\n  $("#sender-filter").classList.toggle("selected", sender); $("#date-filter").classList.toggle("selected", date);\n  const senderSummary = $("#sender-filter summary"); senderSummary.firstChild.nodeValue = sender ? `发送人：${senderName.length > 6 ? senderName.slice(0, 6) + "…" : senderName}` : "发送人";\n  senderSummary.title = sender ? `发送人：${senderName}` : "发送人";\n  $("#date-filter summary").title = date ? `${$("#history-from").value || "不限"} 至 ${$("#history-to").value || "不限"}` : "日期";\n  $("#clear-history-filters").hidden = !(sender || date);\n}\nasync function runHistorySearch(append = false) {\n  if (!state.selected || state.search.loading && append) return;\n  if (state.search.controller) state.search.controller.abort();\n  const search = state.search, serial = ++search.serial, chatSerial = state.serial;\n  search.controller = new AbortController(); search.loading = true;\n  const parameters = append ? search.parameters : historyParameters();\n  if (!parameters || append && search.older === null) { search.loading = false; return; }\n  if (!append) { search.parameters = parameters; search.items = []; search.older = null; search.total = null; search.skipped = 0; }\n  syncHistoryFilters();\n  $("#history-caption").textContent = append ? "正在加载更早的结果…" : "正在查找当前会话的本地记录…";\n  if (!append) $("#history-results").replaceChildren(node("div", "history-empty", "查找中…"));\n  else { const button = $("#history-results .history-more"); if (button) button.disabled = true; }\n  const params = new URLSearchParams({chat:state.selected.key});\n  for (const [key,value] of Object.entries(parameters)) if (value) params.set(key,value);\n  if (append) { params.set("before", String(search.older)); params.set("pageOnly", "1"); }\n  try {\n    const data = await api(`/api/search?${params}`, {signal:search.controller.signal});\n    if (serial !== search.serial || chatSerial !== state.serial) return;\n    search.items = append ? [...search.items, ...data.items] : data.items;\n    search.older = data.older;\n    if (!append) { search.total = data.total; search.skipped = data.skipped; }\n    renderHistoryResults();\n  } catch (error) {\n    if (error.name !== "AbortError" && serial === search.serial && chatSerial === state.serial) {\n      $("#history-caption").textContent = "查找未完成";\n      if (append) { renderHistoryResults(); toast(error.message); }\n      else $("#history-results").replaceChildren(node("div", "history-empty", error.message));\n    }\n  } finally { if (serial === search.serial && chatSerial === state.serial) search.loading = false; }\n}\nfunction renderHistoryResults() {\n  const search = state.search, results = $("#history-results"); results.replaceChildren();\n  const activeFilters = Boolean(search.parameters?.sender || search.parameters?.senderId || search.parameters?.from || search.parameters?.to);\n  const scope = search.total === null ? "最近的聊天记录" : `找到 ${formatNumber(search.total)} 条记录`;\n  $("#history-caption").textContent = scope + (search.skipped ? `，${formatNumber(search.skipped)} 条无法解析` : "") + (activeFilters ? " · 已筛选" : "");\n  if (!search.items.length) {\n    results.append(node("div", "history-empty", search.total === null ? "当前会话没有聊天记录。" : "没有找到匹配的聊天记录。"));\n    return;\n  }\n  const labels = {image:"图片", video:"视频", file:"文件", link:"链接", miniprogram:"小程序", emoji:"表情", record:"聊天记录", system:"系统消息", unknown:"未识别内容", invalid:"无法读取"};\n  for (const message of search.items) {\n    const item = node("button", "history-result"); item.type = "button";\n    const meta = node("div", "history-result-meta");\n    meta.append(node("span", "history-result-sender", message.kind === "system" ? "系统消息" : message.sender || "发言人未知"), node("span", "history-result-time", `${formatDate(message.time)} ${formatTime(message.time)}`));\n    const preview = message.text || message.asset?.filename || message.quote?.text || message.links?.[0] || `[${labels[message.kind] || "消息"}]`;\n    const body = node("div", "history-result-text"); appendHighlightedText(body, preview, search.parameters?.q || "");\n    item.append(meta, body);\n    const term = (search.parameters?.q || "").toLocaleLowerCase();\n    const quoted = message.quote?.text || "";\n    if (term && !String(preview).toLocaleLowerCase().includes(term) && quoted.toLocaleLowerCase().includes(term)) {\n      const quote = node("div", "history-result-quote"); appendHighlightedText(quote, `引用：${quoted}`, search.parameters.q); item.append(quote);\n    } else if (term && !String(preview).toLocaleLowerCase().includes(term)) {\n      const link = (message.links || []).find((value) => value.toLocaleLowerCase().includes(term));\n      if (link) { const extra = node("div", "history-result-quote"); appendHighlightedText(extra, `链接：${link}`, search.parameters.q); item.append(extra); }\n    }\n    if (labels[message.kind] && !["system","invalid"].includes(message.kind)) item.append(node("span", "history-result-kind", labels[message.kind]));\n    if (message.kind === "invalid") item.disabled = true;\n    else item.addEventListener("click", () => jumpToResult(message));\n    results.append(item);\n  }\n  if (search.older !== null) {\n    const more = node("button", "history-more", "显示更早的结果"); more.type = "button";\n    more.addEventListener("click", () => runHistorySearch(true)); results.append(more);\n  }\n}\nfunction appendHighlightedText(host, text, query) {\n  const value = String(text || ""), needle = String(query || "").toLocaleLowerCase();\n  if (!needle) { host.textContent = value; return; }\n  const lower = value.toLocaleLowerCase(); let index = 0, match;\n  while ((match = lower.indexOf(needle, index)) !== -1) {\n    host.append(document.createTextNode(value.slice(index, match)), node("mark", "", value.slice(match, match + needle.length)));\n    index = match + needle.length;\n  }\n  host.append(document.createTextNode(value.slice(index)));\n}\nasync function showRecord(message) {\n  const dialog = $("#record-dialog"), content = $("#record-content"); content.textContent = "读取中…"; dialog.showModal();\n  try {\n    const params = new URLSearchParams({chat: state.selected.key, start: String(message.start), end: String(message.end)});\n    const result = await api(`/api/record?${params}`); content.textContent = JSON.stringify(result, null, 2);\n  } catch (error) { content.textContent = `读取失败：${error.message}`; }\n}\n\nfunction fillGlobalSources() {\n  const select = $("#global-source"), previous = select.value;\n  select.replaceChildren(new Option("全部资料", ""));\n  for (const source of [...new Set(state.chats.map((chat) => chat.source))].sort()) select.add(new Option(source, source));\n  if ([...select.options].some((option) => option.value === previous)) select.value = previous;\n}\nfunction globalParameters() {\n  return {q:$("#global-query").value.trim(),scope:$("#global-scope").value,client:$("#global-client").value,source:$("#global-source").value,\n    category:$("#global-category").value,sender:$("#global-sender").value.trim(),\n    from:$("#global-from").value,to:$("#global-to").value};\n}\nfunction closeGlobalSearch() {\n  if (globalSearch.controller) globalSearch.controller.abort();\n  globalSearch.serial++; globalSearch.jumpSerial++; globalSearch.panelSerial++; globalSearch.loading = false;\n  clearTimeout(globalSearch.timer); globalSearch.timer = null;\n  $("#global-panel").hidden = true; $("#app").classList.remove("global-open"); $("#global-more").open = false;\n}\nfunction openGlobalSearch() {\n  closeHistorySearch(); $("#details-panel").classList.remove("open");\n  $("#global-panel").hidden = false; $("#app").classList.add("global-open");\n  const panelSerial = ++globalSearch.panelSerial;\n  $("#global-query").focus();\n  api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})\n    .then((status) => { if (panelSerial === globalSearch.panelSerial) showGlobalStatus(status); })\n    .catch((error) => { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); });\n  pollGlobalIndex(panelSerial);\n}\nfunction showGlobalError(message) {\n  $("#global-index-text").textContent = message || "搜索准备失败";\n  $("#global-retry").hidden = false;\n}\nfunction showGlobalStatus(status) {\n  globalSearch.status = status;\n  const total = status.total || 0, ready = status.ready || 0;\n  const progress = $("#global-index-progress"); progress.max = Math.max(total,1); progress.value = Math.min(ready,total);\n  let text;\n  if (status.state === "idle") text = "准备本地搜索";\n  else if (status.state === "building") text = `正在准备 ${formatNumber(ready)} / ${formatNumber(total)} 个会话`;\n  else if (status.failed) text = `已准备 ${formatNumber(ready)} / ${formatNumber(total)} 个会话，${formatNumber(status.failed)} 个失败`;\n  else text = `已准备 ${formatNumber(ready)} 个会话 · ${formatNumber(status.indexedMessages)} 条记录`;\n  if (status.skipped) text += ` · ${formatNumber(status.skipped)} 条无法解析`;\n  if (status.error) text = status.error;\n  $("#global-index-text").textContent = text;\n  $("#global-retry").hidden = !(status.failed || status.error);\n  if (globalSearch.result) renderGlobalPagination();\n}\nasync function pollGlobalIndex(panelSerial) {\n  if (panelSerial !== globalSearch.panelSerial || $("#global-panel").hidden) return;\n  try {\n    const status = await api("/api/global/status");\n    if (panelSerial !== globalSearch.panelSerial || $("#global-panel").hidden) return;\n    showGlobalStatus(status);\n    const coverage = globalSearch.result?.coverage;\n    const signature = `${status.ready}/${status.total}/${status.failed}/${status.skipped}`;\n    const oldSignature = coverage ? `${coverage.ready}/${coverage.total}/${coverage.failed}/${coverage.skipped}` : "";\n    if (!globalSearch.loading && globalSearch.params && coverage && status.state !== "building" && signature !== oldSignature &&\n        globalSearch.autoSignature !== signature && JSON.stringify(globalSearch.params) === JSON.stringify(globalParameters())) {\n      globalSearch.autoSignature = signature; runGlobalSearch(1);\n    }\n    if (status.state === "building" && !status.localWorker && Date.now() - globalSearch.lastRecover > 10000) {\n      globalSearch.lastRecover = Date.now();\n      api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})\n        .then((value) => { if (panelSerial === globalSearch.panelSerial) showGlobalStatus(value); })\n        .catch((error) => { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); });\n    }\n  } catch (error) { if (panelSerial === globalSearch.panelSerial) showGlobalError(error.message); }\n  if (panelSerial === globalSearch.panelSerial && !$("#global-panel").hidden)\n    globalSearch.timer = setTimeout(() => pollGlobalIndex(panelSerial), 1800);\n}\nasync function runGlobalSearch(page = 1) {\n  const parameters = globalParameters();\n  if (!parameters.q) { $("#global-caption").textContent = "请输入要搜索的内容"; $("#global-query").focus(); return; }\n  if (globalSearch.controller) globalSearch.controller.abort();\n  globalSearch.controller = new AbortController(); const serial = ++globalSearch.serial;\n  globalSearch.params = parameters; globalSearch.autoSignature = ""; globalSearch.loading = true;\n  $("#global-caption").textContent = "正在搜索已准备的会话…";\n  $("#global-results").replaceChildren(node("div", "global-empty", "搜索中…")); $("#global-pagination").hidden = true;\n  const params = new URLSearchParams({page:String(page)});\n  for (const [key,value] of Object.entries(parameters)) if (value) params.set(key,value);\n  try {\n    const data = await api(`/api/global/search?${params}`, {signal:globalSearch.controller.signal});\n    if (serial !== globalSearch.serial || $("#global-panel").hidden) return;\n    globalSearch.result = data; renderGlobalResults();\n  } catch (error) {\n    if (error.name !== "AbortError" && serial === globalSearch.serial) {\n      globalSearch.result = null; $("#global-caption").textContent = "搜索未完成";\n      $("#global-results").replaceChildren(node("div", "global-empty", error.message));\n    }\n  } finally { if (serial === globalSearch.serial) globalSearch.loading = false; }\n}\nfunction renderGlobalResults() {\n  const data = globalSearch.result, host = $("#global-results"); if (!data) return;\n  host.replaceChildren();\n  const c = data.coverage, incomplete = c.ready < c.total;\n  let caption = `找到 ${formatNumber(data.total)} 条记录`;\n  if (incomplete) caption += ` · 已准备 ${formatNumber(c.ready)} / ${formatNumber(c.total)} 个会话`;\n  if (c.failed) caption += ` · ${formatNumber(c.failed)} 个会话不可用`;\n  if (c.skipped) caption += ` · ${formatNumber(c.skipped)} 条无法解析`;\n  $("#global-caption").textContent = caption;\n  if (!data.items.length) {\n    host.append(node("div", "global-empty", incomplete ? "已准备的会话中没有匹配记录，索引完成后会更新结果。" : "没有找到匹配的聊天记录。"));\n    renderGlobalPagination(); return;\n  }\n  const labels = {image:"图片",video:"视频",file:"文件",link:"链接",miniprogram:"小程序",emoji:"表情",record:"聊天记录",system:"系统消息",unknown:"未识别内容"};\n  const query = globalSearch.params?.q || "", needle = query.toLocaleLowerCase();\n  for (const item of data.items) {\n    const button = node("button", "global-result"); button.type = "button";\n    const top = node("div", "global-result-top");\n    top.append(node("span", "global-result-chat", item.chatName),node("span", "global-result-source", clientLabel(item.client)));\n    const meta = node("div", "global-result-meta");\n    meta.append(node("span", "", `${item.chatKind === "group" ? "群聊" : "私聊"} · ${item.sender}`),node("span", "", `${formatDate(item.time)} ${formatTime(item.time)}`));\n    const preview = item.text || item.asset?.filename || item.quote?.text || item.links?.[0] || `[${labels[item.kind] || "消息"}]`;\n    const text = node("div", "global-result-text"); appendHighlightedText(text, preview, query); button.append(top,meta,text);\n    button.append(node("div", "global-result-origin", [item.source, item.origin].filter(Boolean).join(" · ")));\n    if (needle && !String(preview).toLocaleLowerCase().includes(needle)) {\n      const candidates = [["引用",item.quote?.text],["文件",item.asset?.filename],["链接",...(item.links || [])],["说明",item.description]];\n      const match = candidates.flatMap(([label,...values]) => values.map((value) => [label,value])).find(([,value]) => String(value || "").toLocaleLowerCase().includes(needle));\n      if (match) { const extra = node("div", "global-result-extra"); appendHighlightedText(extra, `${match[0]}：${match[1]}`, query); button.append(extra); }\n    }\n    button.addEventListener("click", () => jumpGlobalResult(item)); host.append(button);\n  }\n  renderGlobalPagination();\n}\nfunction renderGlobalPagination() {\n  const data = globalSearch.result, box = $("#global-pagination");\n  if (!data || data.total <= data.pageSize) { box.hidden = true; return; }\n  const pages = Math.ceil(data.total / data.pageSize);\n  const building = Boolean(data.coverage.building || data.coverage.pending || globalSearch.status?.state === "building");\n  box.hidden = false; $("#global-previous").disabled = building || data.page <= 1;\n  $("#global-next").disabled = building || data.page >= pages;\n  $("#global-page-label").textContent = building ? "索引完成后可翻页" : `${data.page} / ${pages}`;\n}\nasync function jumpGlobalResult(item) {\n  const jumpSerial = ++globalSearch.jumpSerial, selectionSerial = state.serial;\n  $("#global-caption").textContent = "正在定位聊天记录…";\n  try {\n    const params = new URLSearchParams({record:String(item.recordId),chat:item.chatKey,fingerprint:item.fingerprint});\n    const context = await api(`/api/global/context?${params}`);\n    if (jumpSerial !== globalSearch.jumpSerial || selectionSerial !== state.serial) return;\n    let chat = state.chats.find((value) => value.key === context.chatKey);\n    if (!chat) { await loadCatalog(); chat = state.chats.find((value) => value.key === context.chatKey); }\n    if (!chat) throw new Error("会话不在当前资料中，请重新搜索");\n    state.mode = "all"; state.source = ""; state.client = ""; $("#source-filter").value = ""; $("#client-filter").value = ""; $("#conversation-search").value = "";\n    document.querySelectorAll("[data-mode]").forEach((button) => {\n      const active = button.dataset.mode === "all"; button.classList.toggle("active",active);\n      if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected",String(active));\n    });\n    const expected = state.serial + 1; await selectChat(chat,true);\n    if (jumpSerial !== globalSearch.jumpSerial || state.serial !== expected) return;\n    if (state.timelineController) state.timelineController.abort();\n    state.timelineSerial++; state.loading = false; state.messages = context.messages;\n    state.older = context.older; state.newer = context.newer; state.highlighted = context.target;\n    renderMessages();\n    const match = $("#chat-scroll").querySelector(`[data-start="${context.target}"]`);\n    if (match) match.scrollIntoView({block:"center"});\n    if (window.innerWidth <= 760) closeGlobalSearch();\n    else renderGlobalResults();\n  } catch (error) {\n    if (jumpSerial === globalSearch.jumpSerial) { toast(error.message); if (globalSearch.result) renderGlobalResults(); }\n  }\n}\n\ndocument.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));\n$("#open-global-search").addEventListener("click", () => { if ($("#global-panel").hidden) openGlobalSearch(); else closeGlobalSearch(); });\n$("#close-global-search").addEventListener("click", closeGlobalSearch);\n$("#global-search-form").addEventListener("submit", (event) => { event.preventDefault(); runGlobalSearch(1); });\nfor (const selector of ["#global-scope","#global-client","#global-source"]) $(selector).addEventListener("change", () => { if ($("#global-query").value.trim()) runGlobalSearch(1); });\n$("#global-apply-filters").addEventListener("click", () => { $("#global-more").open = false; if ($("#global-query").value.trim()) runGlobalSearch(1); });\n$("#global-clear-filters").addEventListener("click", () => {\n  $("#global-category").value = "all"; $("#global-sender").value = ""; $("#global-from").value = ""; $("#global-to").value = "";\n  $("#global-more").open = false; if ($("#global-query").value.trim()) runGlobalSearch(1);\n});\n$("#global-retry").addEventListener("click", async () => {\n  $("#global-retry").disabled = true;\n  try { showGlobalStatus(await api("/api/global/prepare", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({retry:true})})); }\n  catch (error) { showGlobalError(error.message); }\n  finally { $("#global-retry").disabled = false; }\n});\n$("#global-previous").addEventListener("click", () => { if (globalSearch.result && globalSearch.result.page > 1) runGlobalSearch(globalSearch.result.page - 1); });\n$("#global-next").addEventListener("click", () => { if (globalSearch.result) runGlobalSearch(globalSearch.result.page + 1); });\n$("#conversation-search").addEventListener("input", renderList);\n$("#source-filter").addEventListener("change", (event) => { state.source = event.target.value; setMode(state.mode); });\n$("#client-filter").addEventListener("change", (event) => { state.client = event.target.value; setMode(state.mode); });\n$("#add-source").addEventListener("click", openSource); $("#add-source-footer").addEventListener("click", openSource);\ndocument.querySelectorAll(".dialog-close,.dialog-cancel").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));\n$("#source-form").addEventListener("submit", async (event) => {\n  event.preventDefault(); const button = $("#source-submit"), error = $("#source-error"); button.disabled = true; button.textContent = "正在接入…"; error.hidden = true;\n  try {\n    const result = await api("/api/sources", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:$("#source-path").value})});\n    await loadCatalog(); $("#source-dialog").close(); toast(result.indexWarning || `已接入 ${result.group} 个群聊、${result.direct} 个私聊${result.warnings ? `，${result.warnings} 项资料需检查` : ""}`);\n    if (result.direct && state.mode === "direct") setMode("direct");\n    else if (!state.selected && filteredChats().length) selectChat(filteredChats()[0], false);\n  } catch (exception) { error.textContent = exception.message; error.hidden = false; }\n  finally { button.disabled = false; button.textContent = "接入资料"; }\n});\n$("#toggle-message-search").addEventListener("click", () => { if ($("#history-panel").hidden) openHistorySearch(); else closeHistorySearch(); });\n$("#close-history-search").addEventListener("click", closeHistorySearch);\n$("#history-search-form").addEventListener("submit", (event) => { event.preventDefault(); runHistorySearch(); });\ndocument.querySelectorAll(".history-tab").forEach((button) => button.addEventListener("click", () => { setSearchCategory(button.dataset.category); runHistorySearch(); }));\n$("#history-sender").addEventListener("input", () => { state.search.senderId = ""; });\ndocument.querySelectorAll("[data-apply]").forEach((button) => button.addEventListener("click", () => { button.closest("details").open = false; runHistorySearch(); }));\ndocument.querySelectorAll("[data-clear]").forEach((button) => button.addEventListener("click", () => {\n  if (button.dataset.clear === "sender") { $("#history-sender").value = ""; state.search.senderId = ""; }\n  else { $("#history-from").value = ""; $("#history-to").value = ""; }\n  button.closest("details").open = false; runHistorySearch();\n}));\n$("#clear-history-filters").addEventListener("click", () => {\n  $("#history-sender").value = ""; $("#history-from").value = ""; $("#history-to").value = ""; state.search.senderId = ""; runHistorySearch();\n});\n$("#toggle-details").addEventListener("click", () => { if (!$("#history-panel").hidden) closeHistorySearch(); closeGlobalSearch(); $("#details-panel").classList.toggle("open"); });\n$("#details-close").addEventListener("click", () => $("#details-panel").classList.remove("open"));\n$("#mobile-back").addEventListener("click", () => { $("#app").classList.remove("mobile-chat"); $("#details-panel").classList.remove("open"); closeHistorySearch(); closeGlobalSearch(); });\n$("#chat-scroll").addEventListener("wheel", (event) => { if (event.deltaY < 0 && event.currentTarget.scrollTop <= 2) loadOlder(); }, {passive:true});\n\nloadCatalog().then(() => {\n  const requested = new URLSearchParams(location.search).get("chat");\n  const first = requested ? state.chats.find((chat) => chat.key === requested) : filteredChats()[0];\n  if (first) selectChat(first, false);\n  else { showEmpty(); if (requested) toast("指定会话不可用，请返回解析工具重新打开"); }\n})\n  .catch((error) => { $("#conversation-list").replaceChildren(node("div", "list-loading", `资料读取失败：${error.message}`)); });\n', 'style.css': ':root{font-family:"Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;color:#263648;background:#e8edf3;font-size:14px;font-synthesis:none;--ink:#263648;--muted:#8996a6;--line:#e7edf2;--blue:#3779d8;--pale:#edf4ff}*{box-sizing:border-box}body{margin:0;min-width:320px}button,input,select{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #7eaeef;outline-offset:2px}button:disabled{opacity:.42;cursor:default}.sprite{position:absolute;width:0;height:0;overflow:hidden}svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none}.sr-only{position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.app-shell{height:100vh;min-height:550px;display:grid;grid-template-columns:74px 326px minmax(400px,1fr) 280px;background:#fff;overflow:hidden}.rail{background:#253b55;color:#a7b7ca;display:flex;flex-direction:column;align-items:center;position:relative;z-index:1}.brand-mark{display:grid;grid-template-columns:repeat(2,9px);gap:3px;margin-top:25px;margin-bottom:39px;transform:rotate(-8deg)}.brand-mark span{width:9px;height:9px;background:#80b2f2;border-radius:3px}.brand-mark span:nth-child(2){background:#e5f1ff}.brand-mark span:nth-child(3){background:#4f8ee4}.brand-mark span:nth-child(4){background:#9dc7fb}.rail-items{display:flex;flex-direction:column;gap:13px;align-items:center}.rail-button{width:46px;height:46px;border:0;border-radius:12px;color:#9eb1c7;background:transparent;display:grid;place-items:center;position:relative}.rail-button svg{width:21px;height:21px}.rail-button:hover,.rail-button.active{color:#fff;background:#365577}.rail-button.active:before{content:"";position:absolute;left:-14px;top:13px;height:20px;width:3px;border-radius:0 3px 3px 0;background:#88b9fc}.rail-bottom{margin-top:auto;padding-bottom:25px;font-size:10px;letter-spacing:.06em;display:flex;align-items:center;flex-direction:column;gap:7px;color:#8da3ba}.rail-status svg{width:16px;height:16px}.conversation-panel{background:#f8fafc;border-right:1px solid #e2e8ee;min-width:0;display:flex;flex-direction:column}.panel-heading{height:103px;padding:26px 22px 14px;display:flex;align-items:center;justify-content:space-between}.product-kicker{font-family:"Segoe UI",sans-serif;font-size:9px;color:#9aa8b9;letter-spacing:.115em;font-weight:700}.panel-heading h1{font-size:23px;letter-spacing:.03em;line-height:1.25;margin:7px 0 0;font-weight:700;color:#26394e}.heading-dot{display:inline-block;width:5px;height:5px;background:#64a4fa;border-radius:50%;margin:0 0 4px 6px}.icon-button{border:0;background:transparent;color:#8290a2;display:inline-grid;place-items:center;width:36px;height:36px;border-radius:9px;padding:0;flex:none}.icon-button:hover:not(:disabled){background:#edf2f8;color:#376cae}.add-source{background:#eaf0f6;color:#6b7e93}.add-source svg{width:18px}.search-wrap{height:39px;margin:0 20px 15px;background:#edf1f5;border:1px solid transparent;border-radius:7px;display:flex;align-items:center;gap:8px;padding:0 10px;color:#8b9aab}.search-wrap:focus-within{background:#fff;border-color:#a8c6ef}.search-wrap svg{width:16px;height:16px}.search-wrap input{border:0;background:transparent;outline:0!important;min-width:0;width:100%;font-size:12px;color:#344961}.search-wrap input::placeholder{color:#a2afbc}.search-wrap kbd{font:14px "Segoe UI",sans-serif;color:#9aaabd}.filters{display:flex;gap:19px;margin:0 21px;border-bottom:1px solid #e8edf2;height:40px}.filter{border:0;background:transparent;color:#8b98a8;font-size:12px;padding:0 0 11px;position:relative;white-space:nowrap}.filter span{font-size:10px;margin-left:2px;color:#a7b3c1}.filter.active{color:#3879cf;font-weight:700}.filter.active span{color:#7aa6de}.filter.active:after{position:absolute;content:"";height:2px;bottom:-1px;left:0;right:0;background:#377cde;border-radius:2px}.list-caption{height:51px;padding:0 20px 0 22px;display:flex;align-items:center;justify-content:space-between;color:#8796a6;font-size:11px;font-weight:700}.source-select{display:flex;align-items:center;position:relative;color:#8999a9;max-width:150px}.source-select select{appearance:none;border:0;background:transparent;color:#8291a3;font-size:11px;outline:none;max-width:150px;text-overflow:ellipsis;padding:6px 18px 6px 8px;cursor:pointer}.source-select svg{position:absolute;right:2px;width:13px;height:13px;pointer-events:none}.conversation-list{overflow:auto;flex:1;padding:0 9px 12px;scrollbar-width:thin;scrollbar-color:#d5dee8 transparent}.conversation-item{width:100%;text-align:left;border:0;background:transparent;padding:10px 10px;display:flex;align-items:center;gap:11px;border-radius:8px;min-height:69px;position:relative;color:inherit}.conversation-item:hover{background:#eef3f8}.conversation-item.active{background:#e4efff}.conversation-item.active:before{content:"";position:absolute;left:0;top:15px;bottom:15px;width:3px;border-radius:3px;background:#3c7ddd}.avatar{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;flex:none;color:white;font-weight:700;font-size:14px;overflow:hidden;letter-spacing:.01em}.avatar.group{grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:1px;padding:3px;background:#dce7f3}.avatar.group span{width:100%;height:100%;display:grid;place-items:center;border-radius:3px;font-size:9px;font-weight:700;color:white}.avatar.direct{background:#7ea3ca}.conversation-copy{min-width:0;flex:1}.conversation-top,.conversation-bottom{display:flex;justify-content:space-between;align-items:center;gap:8px}.conversation-title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;font-weight:700;color:#33475d}.conversation-source{font-size:10px;color:#99a8b8;white-space:nowrap;flex:none}.conversation-bottom{margin-top:7px}.conversation-description{color:#94a2b1;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conversation-kind{font-size:9px;line-height:17px;border-radius:3px;padding:0 4px;background:#edf2f7;color:#8b9aaa;white-space:nowrap}.conversation-item.active .conversation-kind{background:#d4e6ff;color:#6d98cf}.list-loading,.list-empty{padding:24px 15px;color:#96a4b3;font-size:12px;line-height:1.9;text-align:center}.list-empty button{border:0;background:transparent;color:#3779d8;font-size:12px;padding:6px}.panel-footer{height:54px;border-top:1px solid #e9eef3;padding:0 20px;display:flex;align-items:center;gap:7px;font-size:10px;color:#98a5b5}.online-dot{width:6px;height:6px;border-radius:50%;background:#7ac5a5}.panel-footer button{margin-left:auto;background:none;border:0;padding:5px 0;color:#8193a8;font-size:10px}.panel-footer button span{color:#467fd0}.chat-panel{display:flex;flex-direction:column;min-width:0;background:#f3f6f9}.chat-header{height:79px;min-height:79px;background:#fff;border-bottom:1px solid #edf0f4;display:flex;align-items:center;gap:13px;padding:0 29px}.header-avatar:empty{display:none}.header-avatar .avatar{width:38px;height:38px;border-radius:10px}.chat-title{min-width:0;flex:1}.chat-title h2{font-size:16px;line-height:1.3;margin:0 0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;letter-spacing:.01em}.chat-subtitle{color:#9aa7b5;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.header-actions{display:flex;gap:6px}.header-actions .icon-button{width:34px;height:34px}.header-actions svg{width:18px;height:18px}.mobile-back{display:none}.message-search{height:48px;display:flex;align-items:center;gap:9px;padding:0 27px;background:#fff;border-bottom:1px solid #e8eef4;color:#92a0af}.message-search[hidden]{display:none}.message-search>svg{width:16px;height:16px}.message-search input{min-width:80px;flex:1;border:0;outline:0;background:transparent;font-size:12px;color:#31455a}.message-search span{font-size:10px;white-space:nowrap}.message-search .icon-button{width:28px;height:28px}.message-search .icon-button svg{width:15px}.chat-scroll{overflow:auto;flex:1;padding:22px min(7%,58px) 28px;scrollbar-color:#d5dee7 transparent;scrollbar-width:thin}.empty-welcome{height:100%;min-height:280px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;color:#899aab}.empty-welcome h3{font-size:16px;color:#53677b;margin:20px 0 6px}.empty-welcome p{font-size:12px;line-height:1.8;margin:0;max-width:300px}.empty-icon{width:66px;height:66px;border-radius:22px;display:grid;place-items:center;color:#88aed9;background:#e5eff9}.empty-icon svg{width:28px;height:28px}.empty-welcome .button-primary{margin-top:18px}.chat-intro{display:flex;align-items:center;justify-content:center;gap:8px;margin:1px auto 21px;color:#9ca9b6;font-size:10px}.chat-intro:before,.chat-intro:after{content:"";width:35px;height:1px;background:#e0e7ee}.older-wrap{text-align:center;margin:0 0 17px}.older-button{border:1px solid #dee8f3;background:#fff;color:#7892af;border-radius:6px;font-size:11px;padding:7px 13px}.older-button:hover{color:#397aca;border-color:#bcd2ed}.date-separator{display:flex;justify-content:center;margin:22px 0 17px;color:#a1afbd;font-size:10px}.date-separator span{padding:4px 9px;border-radius:4px;background:#e8edf2}.message-row{display:flex;align-items:flex-start;gap:10px;margin:0 0 18px}.message-row.outgoing{flex-direction:row-reverse}.message-row .avatar{width:33px;height:33px;border-radius:8px;font-size:11px}.message-content{max-width:min(80%,600px);min-width:0}.message-meta{display:flex;gap:8px;align-items:center;font-size:10px;color:#97a6b6;margin:0 1px 6px}.outgoing .message-meta{flex-direction:row-reverse}.message-meta .sender{color:#6e8194}.message-bubble{background:#fff;border:1px solid #ebf0f4;border-radius:3px 10px 10px 10px;color:#34495d;line-height:1.75;padding:10px 13px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;box-shadow:0 2px 5px rgba(42,68,98,.025)}.outgoing .message-bubble{background:#dcecff;border-color:#d5e7ff;border-radius:10px 3px 10px 10px}.message-bubble:empty{display:none}.message-quote{font-size:11px;margin-top:7px;padding:7px 9px;background:rgba(116,142,169,.09);border-left:2px solid #bccbd9;border-radius:2px;color:#8090a2;white-space:pre-wrap}.message-quote strong{font-weight:600;color:#6d8095}.message-media{display:flex;align-items:center;gap:10px;min-width:180px;color:#72869b}.media-icon{width:35px;height:40px;border-radius:6px;display:grid;place-items:center;background:#e8f0fa;color:#6093d3}.media-icon svg{width:19px}.media-copy{min-width:0}.media-title{color:#536c84;font-weight:600;overflow-wrap:anywhere}.media-state{font-size:10px;color:#9aa9b6}.media-link{color:#4c84cc;text-decoration:none;font-size:11px}.message-image{display:block;max-width:min(300px,100%);max-height:230px;border-radius:5px;margin-top:3px;object-fit:contain}.message-tools{display:flex;gap:10px;margin:5px 1px 0}.outgoing .message-tools{justify-content:flex-end}.message-tools button{border:0;padding:0;background:none;color:#a3b2c1;font-size:10px}.message-tools button:hover{color:#3779d8}.system-message{text-align:center;color:#a3b0bd;font-size:10px;margin:17px auto;line-height:1.7;max-width:85%;white-space:pre-wrap}.invalid-message{color:#a77863}.archive-bar{height:82px;min-height:82px;background:#fff;border-top:1px solid #e9eef3;display:flex;align-items:center;padding:0 30px;gap:11px}.archive-bar>svg{width:18px;height:18px;color:#a0afbd}.archive-bar strong{font-size:11px;color:#8a9aaa;display:block;margin-bottom:5px;font-weight:600}.archive-bar div span{font-size:10px;color:#b0bdc9}.archive-bar-right{margin-left:auto;color:#c0cbd5;font:9px "Segoe UI",sans-serif;letter-spacing:.12em}.details-panel{display:flex;flex-direction:column;background:#fff;border-left:1px solid #e8edf2;min-width:0;overflow:hidden}.details-heading{height:79px;min-height:79px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #edf1f5;padding:0 21px}.details-heading h2{font-size:13px;margin:0;letter-spacing:.02em}.details-close{display:none}.details-content{padding:24px 20px;overflow:auto;flex:1;scrollbar-width:thin}.details-profile{display:flex;align-items:center;gap:11px;padding-bottom:22px;border-bottom:1px solid #eff2f5}.details-profile .avatar{width:42px;height:42px}.details-profile-name{font-size:13px;color:#31465a;font-weight:700;line-height:1.5;overflow-wrap:anywhere}.details-profile-sub{color:#a1aebb;font-size:10px;margin-top:4px}.detail-section{padding:21px 0;border-bottom:1px solid #f0f3f6}.detail-section:last-child{border-bottom:0}.section-title{display:flex;justify-content:space-between;align-items:center;color:#52677d;font-size:12px;font-weight:700;margin-bottom:17px}.section-title span{font-size:10px;font-weight:400;color:#a1afbd}.detail-row{display:flex;justify-content:space-between;gap:12px;margin:14px 0;color:#95a3b2;font-size:11px}.detail-row strong{font-weight:500;text-align:right;color:#5d7287;overflow-wrap:anywhere}.source-tag{display:inline-block;color:#588ad0;background:#edf5ff;border-radius:4px;padding:3px 7px;font-size:10px}.member-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px 6px}.member{text-align:center;min-width:0}.member .avatar{width:34px;height:34px;border-radius:8px;margin:auto;font-size:10px}.member-name{display:block;color:#8291a1;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:6px}.member-note,.detail-note{font-size:10px;line-height:1.7;color:#9aaab9;margin:12px 0 0}.show-members{border:0;background:none;color:#6d98ce;padding:11px 0 0;font-size:10px}.details-footer{height:54px;min-height:54px;padding:0 20px;border-top:1px solid #edf1f4;display:flex;align-items:center;gap:7px;font-size:10px;color:#a6b4c1}.details-footer svg{width:14px;height:14px}.muted{font-size:12px;color:#a0afbd;line-height:1.8}.dialog{border:0;border-radius:14px;box-shadow:0 20px 90px rgba(35,58,83,.24);padding:26px;width:min(480px,calc(100vw - 32px));color:#34495d}.dialog::backdrop{background:rgba(31,46,64,.38)}.dialog-heading{display:flex;justify-content:space-between;align-items:flex-start}.dialog-kicker{font-size:10px;color:#8da3bb}.dialog h2{font-size:19px;margin:7px 0 0;color:#2d4258}.dialog p{font-size:12px;line-height:1.8;color:#8698a8;margin:18px 0}.dialog label{display:block;color:#596e82;font-size:11px;font-weight:700;margin-bottom:9px}.dialog input{width:100%;height:42px;border:1px solid #dce5ee;border-radius:7px;padding:0 12px;color:#354c63;font-size:12px}.dialog input::placeholder{color:#b1bdc9}.form-hint{display:flex;gap:8px;align-items:flex-start;margin-top:12px;font-size:10px;line-height:1.7;color:#9aaab9}.form-hint svg{width:14px;height:14px;margin-top:1px}.form-hint b{font-weight:500;color:#7d91a6}.dialog p.form-error{color:#bd625c;margin:12px 0 0}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}.button-plain,.button-primary{border-radius:7px;padding:9px 15px;font-size:12px}.button-plain{border:1px solid #e3eaf0;background:white;color:#7e90a1}.button-primary{border:1px solid #397cd7;background:#397cd7;color:white}.button-primary:hover{background:#2e6dc3}.record-dialog{width:min(780px,calc(100vw - 32px));max-height:85vh}.record-dialog pre{background:#f4f7fa;border:1px solid #eaf0f5;padding:16px;border-radius:8px;overflow:auto;max-height:55vh;font:11px/1.7 Consolas,monospace;color:#51677d;white-space:pre-wrap;overflow-wrap:anywhere}.toast{position:fixed;bottom:25px;left:50%;transform:translate(-50%,15px);background:#30465d;color:white;border-radius:7px;padding:10px 16px;font-size:12px;opacity:0;pointer-events:none;transition:opacity .15s,transform .15s;z-index:6}.toast.visible{opacity:1;transform:translate(-50%,0)}\n.app-shell{grid-template-rows:minmax(0,1fr)}.conversation-panel,.chat-panel,.details-panel{min-height:0}\n@media(max-width:1240px){.app-shell{grid-template-columns:68px 300px minmax(360px,1fr)}.details-panel{position:fixed;z-index:4;top:0;right:0;width:280px;height:100vh;box-shadow:-10px 0 28px rgba(33,55,79,.08);transform:translateX(110%);transition:transform .18s;visibility:hidden}.details-panel.open{transform:translateX(0);visibility:visible}.details-close{display:inline-grid}.chat-header{padding:0 24px}.rail-button.active:before{left:-11px}}\n@media(max-width:760px){.app-shell{grid-template-columns:58px minmax(0,1fr);min-height:100dvh;height:100dvh}.brand-mark{margin-top:22px}.rail-button{width:40px;height:40px}.rail-button.active:before{left:-9px}.conversation-panel{grid-column:2;grid-row:1}.panel-heading{height:95px}.chat-panel{grid-column:2;grid-row:1;display:none}.app-shell.mobile-chat .conversation-panel{display:none}.app-shell.mobile-chat .chat-panel{display:flex}.mobile-back{display:inline-grid;margin-left:-10px}.chat-header{gap:7px;padding:0 16px;height:70px;min-height:70px}.chat-title h2{font-size:14px}.chat-scroll{padding-left:18px;padding-right:18px}.archive-bar{height:68px;min-height:68px;padding:0 20px}.archive-bar-right{display:none}.details-panel{width:min(320px,calc(100vw - 58px))}.message-content{max-width:84%}.message-search{padding:0 15px}.message-search span{display:none}}\n@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important}}\n\n/* Conversation navigation and in-chat history search. */\n.app-shell{grid-template-columns:148px 306px minmax(390px,1fr) 280px}\n.rail{background:#e2edf9;color:#75899c;align-items:stretch}\n.rail-identity{display:flex;align-items:center;gap:11px;padding:25px 17px 28px;min-height:87px}\n.rail-identity .brand-mark{margin:0;flex:none}.identity-copy{min-width:0}.identity-copy strong,.identity-copy span{display:block;white-space:nowrap}.identity-copy strong{font-size:12px;color:#304760}.identity-copy span{font-size:10px;color:#8ea0b2;margin-top:4px}\n.rail-items{gap:4px;align-items:stretch}.rail-button{width:auto;height:39px;margin:0 9px;padding:0 11px;border-radius:7px;display:flex;gap:11px;justify-content:flex-start;color:#6f8193;font-size:12px}.rail-button svg{width:17px;height:17px}.rail-button:hover,.rail-button.active{color:#2e76d5;background:#cde1fb}.rail-button.active:before{display:none}.rail-section{font-size:10px;color:#9aabbc;padding:25px 20px 7px}.rail-bottom{align-self:center;color:#90a2b3}.rail-status svg{width:15px}\n.history-panel{display:flex;flex-direction:column;min-width:0;min-height:0;background:#fff;border-left:1px solid #e5ebf1;overflow:hidden}.history-panel[hidden]{display:none}.app-shell.search-open .details-panel{display:none}.app-shell.search-open{grid-template-columns:148px 306px minmax(350px,1fr) 360px}.history-heading{height:78px;min-height:78px;padding:0 19px;display:flex;align-items:center;justify-content:space-between}.history-heading h2{font-size:14px;margin:0;color:#273d52}.history-heading .icon-button svg{width:16px}.history-tabs{display:flex;gap:17px;margin:0 17px;height:36px;min-height:36px;border-bottom:1px solid #edf1f5;overflow-x:auto;scrollbar-width:none}.history-tabs::-webkit-scrollbar{display:none}.history-tab{position:relative;white-space:nowrap;border:0;padding:0 0 11px;background:transparent;color:#788a9d;font-size:11px}.history-tab.active{color:#3278d8;font-weight:700}.history-tab.active:after{content:"";position:absolute;bottom:0;left:0;right:0;height:2px;border-radius:2px;background:#3c80dc}\n.history-search-form{margin:14px 17px 10px;height:36px;min-height:36px;border:1px solid #d6e2ef;border-radius:6px;display:flex;align-items:center;gap:7px;padding:0 9px;color:#8d9eaf}.history-search-form:focus-within{border-color:#67a0ee;box-shadow:0 0 0 2px #edf5ff}.history-search-form svg{width:15px;height:15px}.history-search-form input{border:0;outline:0!important;flex:1;min-width:0;background:transparent;color:#334b62;font-size:11px}.history-search-form input::placeholder{color:#a0afbf}.history-search-form button{border:0;background:none;color:#407dc8;font-size:11px;padding:4px 0 4px 7px}\n.history-filters{position:relative;z-index:3;display:flex;gap:7px;align-items:center;padding:0 17px 11px}.history-filter summary{display:flex;align-items:center;gap:4px;list-style:none;cursor:pointer;background:#f0f3f6;border-radius:5px;padding:6px 8px;color:#788b9e;font-size:10px;white-space:nowrap}.history-filter summary::-webkit-details-marker{display:none}.history-filter summary svg{width:11px;height:11px}.history-filter.selected summary{background:#e6f0ff;color:#3d7cc7}.filter-popover{position:absolute;left:17px;top:34px;width:224px;padding:13px;border:1px solid #e3ebf3;border-radius:8px;background:#fff;box-shadow:0 10px 30px #29425c1f}.date-popover{left:84px}.filter-popover label{display:block;color:#71859a;font-size:10px;margin:2px 0 7px}.filter-popover input{width:100%;height:32px;border:1px solid #dce6ef;border-radius:5px;padding:0 8px;color:#4d647b;font-size:11px;margin-bottom:10px}.filter-popover p{font-size:10px;color:#9aabb9;line-height:1.6;margin:0 0 10px}.filter-popover>div{display:flex;justify-content:flex-end;gap:8px}.filter-popover button{border:0;border-radius:4px;background:#f1f5f9;color:#71889f;font-size:10px;padding:6px 9px}.filter-popover button[data-apply]{background:#3980da;color:white}.clear-filters{border:0;background:none;color:#7799bf;font-size:10px;padding:4px}.clear-filters[hidden]{display:none}\n.history-caption{padding:10px 17px;color:#9baab9;font-size:10px;line-height:1.5;border-top:1px solid #f1f4f7}.history-results{flex:1;min-height:0;overflow:auto;scrollbar-width:thin;scrollbar-color:#d5dee7 transparent}.history-result{display:block;width:100%;text-align:left;padding:13px 18px;border:0;border-bottom:1px solid #f0f3f6;background:#fff;color:#334b60}.history-result:hover{background:#f2f7fd}.history-result:disabled{opacity:.7;cursor:default}.history-result-meta{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:10px;color:#9aaab9}.history-result-sender{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#728aa1}.history-result-time{white-space:nowrap;font-size:9px}.history-result-text{font-size:11px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;margin-top:7px}.history-result-kind{display:inline-block;margin-top:7px;font-size:9px;background:#eef4fa;color:#8aa0b5;padding:3px 5px;border-radius:4px}.history-empty{padding:30px 20px;color:#9aabba;text-align:center;font-size:11px;line-height:1.8}.history-more{display:block;margin:15px auto 24px;border:1px solid #e1eaf3;background:#fff;color:#7795b4;border-radius:6px;padding:7px 12px;font-size:10px}.history-more:hover{color:#3779d8;border-color:#bad4f0}\n.sender-avatar{border:0;background:transparent;padding:0;border-radius:8px}.sender-avatar:disabled{opacity:1}.sender-avatar:not(:disabled):hover{box-shadow:0 0 0 3px #bbd5f3}.message-row.highlighted .message-bubble{outline:2px solid #8ab7f1;box-shadow:0 0 0 5px #c6def766}.highlighted-system{background:#e1ecfa;border-radius:5px;padding:6px}.latest-wrap{text-align:center;margin:-5px 0 12px}.latest-button{border:0;background:none;color:#5b92d4;font-size:10px;padding:5px}.member{border:0;background:transparent;padding:0;border-radius:7px;cursor:pointer}.member:hover .avatar{box-shadow:0 0 0 3px #dceafb}\n.system-message{overflow-wrap:anywhere;word-break:break-word}\n.history-result mark{background:#fff0ba;color:#775315;border-radius:2px;padding:0 1px}.history-result-quote{color:#94a4b2;font-size:10px;line-height:1.5;margin-top:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}\n.message-link-text{color:#7299c8;font-size:10px;margin-top:6px;overflow-wrap:anywhere}\n@media(max-width:1240px){.app-shell,.app-shell.search-open{grid-template-columns:130px 290px minmax(330px,1fr)}.history-panel{position:fixed;z-index:5;top:0;right:0;width:min(360px,calc(100vw - 130px));height:100vh;box-shadow:-10px 0 28px rgba(33,55,79,.1)}.rail-button.active:before{display:none}.rail-identity{padding-left:14px;padding-right:8px}.rail-button{margin:0 8px}}\n@media(max-width:760px){.app-shell,.app-shell.search-open{grid-template-columns:58px minmax(0,1fr)}.rail-identity{justify-content:center;padding:24px 0 28px;min-height:75px}.identity-copy,.rail-label,.rail-section{display:none}.rail-button{width:40px;height:40px;margin:0 auto;justify-content:center;padding:0}.rail-button svg{width:19px;height:19px}.history-panel{width:min(360px,calc(100vw - 58px));height:100dvh}.history-heading{height:70px;min-height:70px}}\n\n/* Global search keeps the current conversation visible while indexing. */\n.global-entry{display:flex;align-items:center;gap:8px;margin:-5px 20px 10px;padding:8px 9px;border:0;border-radius:6px;background:#edf4fc;color:#477dbd;text-align:left;font-size:11px}.global-entry:hover{background:#e1edfc}.global-entry>svg:first-child{width:14px;height:14px}.global-entry>svg:last-child{width:12px;height:12px;margin-left:auto;color:#98b2d0}.global-entry small{font-size:9px;color:#97aabd;margin-left:2px;white-space:nowrap}.app-shell.global-open{grid-template-columns:148px 306px minmax(350px,1fr) 360px}.app-shell.global-open .details-panel{display:none}.global-panel{display:flex;flex-direction:column;min-width:0;min-height:0;background:#fff;border-left:1px solid #e5ebf1;overflow:hidden}.global-panel[hidden]{display:none}.global-heading{height:78px;min-height:78px;padding:0 19px;display:flex;align-items:center;justify-content:space-between}.global-heading h2{font-size:14px;margin:0;color:#273d52}.global-heading p{font-size:10px;color:#a1aebb;margin:6px 0 0}.global-heading svg{width:16px}.global-search-form{margin:0 17px 11px;height:36px;min-height:36px;border:1px solid #d6e2ef;border-radius:6px;display:flex;align-items:center;gap:7px;padding:0 9px;color:#8d9eaf}.global-search-form:focus-within{border-color:#67a0ee;box-shadow:0 0 0 2px #edf5ff}.global-search-form svg{width:15px;height:15px}.global-search-form input{border:0;outline:0!important;flex:1;min-width:0;background:transparent;color:#334b62;font-size:11px}.global-search-form input::placeholder{color:#a0afbf}.global-search-form button{border:0;background:none;color:#407dc8;font-size:11px;padding:4px 0 4px 7px}\n.global-filters{display:flex;gap:6px;align-items:center;padding:0 17px 12px;position:relative;z-index:3}.global-filters>label{min-width:0}.global-filters>label select,.global-more summary{height:28px;max-width:112px;border:0;border-radius:5px;background:#f0f3f6;color:#74899e;font-size:10px;padding:0 6px;cursor:pointer;text-overflow:ellipsis}.global-more summary{display:flex;align-items:center;gap:3px;list-style:none;white-space:nowrap}.global-more summary::-webkit-details-marker{display:none}.global-more summary svg{width:11px;height:11px}.global-filter-popover{position:absolute;right:17px;top:33px;width:250px;padding:13px;border:1px solid #e3ebf3;border-radius:8px;background:#fff;box-shadow:0 10px 30px #29425c1f}.global-filter-popover>label,.global-date-fields label{display:block;color:#71859a;font-size:10px;margin:2px 0 6px}.global-filter-popover>input,.global-filter-popover>select,.global-date-fields input{width:100%;height:32px;border:1px solid #dce6ef;border-radius:5px;padding:0 7px;color:#4d647b;font-size:11px;margin-bottom:10px;background:#fff}.global-date-fields{display:flex;gap:8px}.global-date-fields label{flex:1;min-width:0}.global-date-fields input{margin-top:6px;padding:0 4px}.global-filter-actions{display:flex;justify-content:flex-end;gap:8px}.global-filter-actions button{border:0;border-radius:4px;background:#f1f5f9;color:#71889f;font-size:10px;padding:6px 9px}.global-filter-actions #global-apply-filters{background:#3980da;color:white}\n.global-index{margin:0 17px 10px;padding:9px 10px;background:#f5f8fc;border-radius:6px}.global-index-top{display:flex;align-items:center;justify-content:space-between;gap:6px;color:#879cb0;font-size:10px;line-height:1.5}.global-index-top button{border:0;background:none;color:#397dce;font-size:10px;white-space:nowrap}.global-index-top button[hidden]{display:none}.global-index progress{width:100%;height:4px;border:0;appearance:none;margin-top:6px;display:block}.global-index progress::-webkit-progress-bar{background:#e2eaf2;border-radius:4px}.global-index progress::-webkit-progress-value{background:#78a9e8;border-radius:4px}.global-index progress::-moz-progress-bar{background:#78a9e8;border-radius:4px}.global-caption{padding:9px 17px;color:#9baab9;font-size:10px;line-height:1.5;border-top:1px solid #f1f4f7}.global-results{flex:1;min-height:0;overflow:auto;scrollbar-width:thin;scrollbar-color:#d5dee7 transparent}.global-result{display:block;width:100%;text-align:left;padding:13px 18px;border:0;border-bottom:1px solid #f0f3f6;background:#fff;color:#334b60}.global-result:hover{background:#f2f7fd}.global-result-top,.global-result-meta{display:flex;align-items:center;justify-content:space-between;gap:8px}.global-result-chat{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:#3b5875;font-weight:700}.global-result-source{font-size:9px;color:#8da8c5;white-space:nowrap}.global-result-meta{margin-top:6px;color:#9aaab9;font-size:9px}.global-result-meta span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.global-result-meta span:last-child{white-space:nowrap}.global-result-text{font-size:11px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;margin-top:7px}.global-result-extra{font-size:10px;color:#94a4b2;line-height:1.5;margin-top:6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.global-result mark{background:#fff0ba;color:#775315;border-radius:2px;padding:0 1px}.global-empty{padding:30px 20px;color:#9aabba;text-align:center;font-size:11px;line-height:1.8}.global-pagination{height:48px;min-height:48px;display:flex;align-items:center;justify-content:center;gap:15px;border-top:1px solid #edf1f5;color:#93a5b6;font-size:10px}.global-pagination[hidden]{display:none}.global-pagination button{border:0;border-radius:4px;background:#f0f5fb;color:#6a91ba;padding:6px 9px;font-size:10px}.global-pagination button:disabled{opacity:.45}\n@media(max-width:1240px){.app-shell.global-open{grid-template-columns:130px 290px minmax(330px,1fr)}.global-panel{position:fixed;z-index:5;top:0;right:0;width:min(360px,calc(100vw - 130px));height:100vh;box-shadow:-10px 0 28px rgba(33,55,79,.1)}}\n@media(max-width:760px){.app-shell.global-open{grid-template-columns:58px minmax(0,1fr)}.global-panel{width:min(360px,calc(100vw - 58px));height:100dvh}.global-heading{height:70px;min-height:70px}}\n.caption-filters{display:flex;align-items:center;gap:7px;min-width:0}.caption-filters .source-select{min-width:0}.caption-filters select{max-width:94px}.caption-filters #client-filter{max-width:75px}.client-badge{font-size:9px;white-space:nowrap;color:#4d84c4;background:#e8f2ff;border-radius:4px;padding:3px 5px}.client-badge.wechat{color:#4d927a;background:#e8f5ef}.client-badge.unknown,.client-badge.conflict{color:#9b835d;background:#f9f3e7}.global-filters{flex-wrap:wrap}.global-filters>label select{max-width:95px}.global-result-origin{margin-top:7px;color:#a2b0be;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n'}

"""Local, resumable search index for exported conversations.

The index never changes an export. A conversation becomes searchable only after
its complete source file has been indexed and its fingerprint rechecked.
"""

from datetime import date
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import threading
from zipfile import BadZipFile


MAX_ROW = 32 * 1024 * 1024
SEPARATOR = "\x1f"
SCHEMA_VERSION = 2
PAGE_SIZE = 40


def searchable_fields(row):
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quoted = quote.get("message") if isinstance(quote.get("message"), dict) else {}
    media = row.get("media") if isinstance(row.get("media"), dict) else {}
    asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
    links = row.get("links") if isinstance(row.get("links"), list) else []
    return [str(value or "") for value in (row.get("text"), row.get("description"),
            quoted.get("text"), media.get("filename"), asset.get("filename"), *links)]


def excerpt(value, query, limit):
    value = str(value or "")
    if len(value) <= limit:
        return value
    position = value.casefold().find(query.casefold()) if query else -1
    start = max(0, position - limit // 3) if position >= 0 else 0
    return ("…" if start else "") + value[start:start + limit] + ("…" if start + limit < len(value) else "")


def source_fingerprint(chat):
    source = chat.archive if chat.archive is not None else chat.directory / "messages.jsonl"
    stat = source.stat()
    identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{chat.prefix}|{chat.chat_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def create_search_acceleration(con):
    """FTS5 trigram is optional; ordinary SQLite can still search the records."""
    existed = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'").fetchone())
    try:
        con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                       search_text, content='records', content_rowid='id', tokenize='trigram')""")
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "no such module: fts5" in message or "no such tokenizer: trigram" in message:
            return False
        raise
    con.executescript("""
        CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
          INSERT INTO records_fts(rowid,search_text) VALUES(new.id,new.search_text);
        END;
        CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
          INSERT INTO records_fts(records_fts,rowid,search_text) VALUES('delete',old.id,old.search_text);
        END;
    """)
    if not existed and con.execute("SELECT 1 FROM records LIMIT 1").fetchone():
        con.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
    return True


class GlobalSearchIndex:
    def __init__(self, catalog, directory, opener):
        self.catalog = catalog
        self.directory = Path(directory)
        self.database = self.directory / "global-search.sqlite3"
        self.lock_path = self.directory / "index.lock"
        self.opener = opener
        self.guard = threading.RLock()
        self.thread = None
        self.lock_file = None
        self.fatal_error = ""
        self.stop_event = threading.Event()

    def stop(self):
        """Request cooperative shutdown; incomplete sources resume on the next start."""
        self.stop_event.set()
        worker = self.thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3)

    def exists(self):
        return self.database.is_file()

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.database, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA synchronous=NORMAL")
            with con:
                yield con
        finally:
            con.close()

    def _lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    @staticmethod
    def _unlock(handle):
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _setup(self):
        with self._connection() as con:
            existing = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_meta'").fetchone()
            if existing:
                row = con.execute("SELECT version FROM index_meta LIMIT 1").fetchone()
                if row is None or row["version"] != SCHEMA_VERSION:
                    raise ValueError("本地搜索索引版本不兼容，请使用独立的数据目录")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
            CREATE TABLE IF NOT EXISTS index_meta(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
              chatkey TEXT PRIMARY KEY, chat_id TEXT NOT NULL, name TEXT NOT NULL,
              kind TEXT NOT NULL, source TEXT NOT NULL, client TEXT NOT NULL, origin TEXT NOT NULL, fingerprint TEXT NOT NULL,
              status TEXT NOT NULL, active INTEGER NOT NULL, expected INTEGER NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0, indexed INTEGER NOT NULL DEFAULT 0,
              skipped INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS records(
              id INTEGER PRIMARY KEY AUTOINCREMENT, chatkey TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
              message_id TEXT NOT NULL, time TEXT NOT NULL, day TEXT NOT NULL,
              sender TEXT NOT NULL, sender_fold TEXT NOT NULL, kind TEXT NOT NULL,
              has_links INTEGER NOT NULL, is_mini INTEGER NOT NULL, fields_json TEXT NOT NULL, search_text TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS records_source_position ON records(chatkey,start);
            CREATE INDEX IF NOT EXISTS records_time ON records(day,time DESC,id DESC);
            """)
            row = con.execute("SELECT version FROM index_meta LIMIT 1").fetchone()
            if row is None:
                con.execute("INSERT INTO index_meta VALUES(?)", (SCHEMA_VERSION,))
            create_search_acceleration(con)

    def _sync(self, recover=False, retry_errors=False):
        self.catalog.refresh()
        with self.catalog.lock:
            chats = list(self.catalog.chats.values())
        with self._connection() as con:
            con.execute("UPDATE sources SET active=0")
            for chat in chats:
                try:
                    fingerprint = source_fingerprint(chat) if chat.available else ""
                    problem = "" if fingerprint else "导出消息文件不可用"
                except (OSError, ValueError):
                    fingerprint, problem = "", "导出消息文件不可用"
                old = con.execute("SELECT fingerprint,status FROM sources WHERE chatkey=?", (chat.key,)).fetchone()
                if old is None:
                    status = "error" if problem else "pending"
                    con.execute("""INSERT INTO sources(chatkey,chat_id,name,kind,source,client,origin,fingerprint,status,active,expected,error,updated)
                                   VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                                (chat.key, chat.chat_id, chat.name, chat.kind, chat.source,
                                 getattr(chat, "client", "unknown"), getattr(chat, "origin", ""), fingerprint, status,
                                 chat.rows, problem, chat.updated))
                    continue
                status = old["status"]
                if problem:
                    status = "error"
                elif old["fingerprint"] != fingerprint or status == "removed" or (recover and status == "building") or (retry_errors and status == "error"):
                    status = "pending"
                con.execute("""UPDATE sources SET chat_id=?,name=?,kind=?,source=?,client=?,origin=?,fingerprint=?,status=?,
                               active=1,expected=?,error=CASE WHEN ?<>'' THEN ? WHEN ?='pending' THEN '' ELSE error END,
                               updated=? WHERE chatkey=?""",
                            (chat.chat_id, chat.name, chat.kind, chat.source, getattr(chat, "client", "unknown"),
                             getattr(chat, "origin", ""), fingerprint, status, chat.rows,
                             problem, problem, status, chat.updated, chat.key))
            con.execute("UPDATE sources SET status='removed' WHERE active=0 AND status<>'removed'")

    def prepare(self, retry_errors=False):
        with self.guard:
            if self.stop_event.is_set():
                raise ValueError("查看器已关闭，请重新打开")
            if self.thread and self.thread.is_alive():
                self._sync(retry_errors=retry_errors)
                return self.status()
            handle = self._lock()
            if handle is None:
                # Another process may be indexing while this one has learned of
                # a newly added source. Queue it without disturbing the owner.
                if self.database.is_file():
                    try:
                        self._sync(retry_errors=retry_errors)
                    except sqlite3.Error:
                        pass
                return self.status()
            try:
                self._setup()
                self._sync(recover=True, retry_errors=retry_errors)
                with self._connection() as con:
                    pending = con.execute("SELECT 1 FROM sources WHERE active=1 AND status='pending' LIMIT 1").fetchone()
                if not pending:
                    self._unlock(handle)
                    return self.status()
                self.fatal_error = ""
                self.lock_file = handle
                self.thread = threading.Thread(target=self._work, name="global-search-index", daemon=True)
                self.thread.start()
                return self.status()
            except sqlite3.Error as exc:
                self._unlock(handle)
                raise ValueError("本地搜索索引无法读取，请关闭查看器后检查索引目录。") from exc
            except Exception:
                self._unlock(handle)
                raise

    def _work(self):
        released = False
        try:
            while not self.stop_event.is_set():
                with self.guard:
                    with self._connection() as con:
                        row = con.execute("""SELECT chatkey,fingerprint FROM sources WHERE active=1 AND status='pending'
                                             ORDER BY updated DESC,chatkey LIMIT 1""").fetchone()
                    if not row:
                        handle, self.lock_file = self.lock_file, None
                        self.thread = None
                        self._unlock(handle)
                        released = True
                        return
                self._build(row["chatkey"], row["fingerprint"])
        except Exception:
            self.fatal_error = "搜索索引准备中断，可重试。"
        finally:
            if not released:
                with self.guard:
                    handle, self.lock_file = self.lock_file, None
                    self.thread = None
                    self._unlock(handle)

    def _build(self, chatkey, fingerprint):
        processed = indexed = skipped = 0
        try:
            chat = self.catalog.get(chatkey)
            with self._connection() as con:
                changed = con.execute("""UPDATE sources SET status='building',processed=0,indexed=0,skipped=0,error=''
                                         WHERE chatkey=? AND active=1 AND status='pending' AND fingerprint=?""",
                                      (chatkey, fingerprint)).rowcount
                if not changed:
                    return
                con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
            with self.opener(chat, "messages.jsonl") as (stream, _size):
                batch = []
                while True:
                    if self.stop_event.is_set():
                        return
                    start = stream.tell()
                    raw = stream.readline(MAX_ROW + 2)
                    if not raw:
                        break
                    if len(raw) > MAX_ROW:
                        raise ValueError("单条消息超过可读取大小")
                    if not raw.strip():
                        continue
                    processed += 1
                    try:
                        row = json.loads(raw.decode("utf-8-sig"))
                        if not isinstance(row, dict) or str(row.get("chatId") or "") != chat.chat_id:
                            skipped += 1
                            continue
                    except (UnicodeError, json.JSONDecodeError):
                        skipped += 1
                        continue
                    fields = searchable_fields(row)
                    when = str(row.get("time") or "")
                    day = when[:10]
                    try:
                        if date.fromisoformat(day).isoformat() != day:
                            day = ""
                    except ValueError:
                        day = ""
                    sender = str(row.get("senderName") or "")
                    links = row.get("links") if isinstance(row.get("links"), list) else []
                    batch.append((chatkey, start, start + len(raw.rstrip(b"\r\n")), str(row.get("id") or ""),
                                  when, day, sender, sender.casefold(), str(row.get("kind") or "unknown"),
                                  int(bool(links)), int(bool(row.get("miniProgram"))),
                                  json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                                  SEPARATOR.join(value.casefold() for value in fields)))
                    indexed += 1
                    if len(batch) >= 1200:
                        if not self._write_batch(chatkey, fingerprint, batch, processed, indexed, skipped):
                            self._discard(chatkey)
                            return
                        batch.clear()
                if batch and not self._write_batch(chatkey, fingerprint, batch, processed, indexed, skipped):
                    self._discard(chatkey)
                    return
            if self.stop_event.is_set():
                return
            current = source_fingerprint(chat)
            with self._connection() as con:
                state = con.execute("SELECT fingerprint,status,active FROM sources WHERE chatkey=?", (chatkey,)).fetchone()
                if state and state["active"] and state["status"] == "building" and state["fingerprint"] == fingerprint and current == fingerprint:
                    con.execute("UPDATE sources SET status='ready',processed=?,indexed=?,skipped=?,error='' WHERE chatkey=?",
                                (processed, indexed, skipped, chatkey))
                else:
                    con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
                    if state and state["active"] and state["status"] == "building":
                        con.execute("UPDATE sources SET status='pending',fingerprint=?,processed=0,indexed=0 WHERE chatkey=?", (current, chatkey))
        except (OSError, ValueError, KeyError, BadZipFile, sqlite3.Error):
            with self._connection() as con:
                con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
                con.execute("""UPDATE sources SET status='error',processed=?,indexed=0,skipped=?,error='资料读取失败，请检查导出文件'
                               WHERE chatkey=? AND active=1 AND status IN ('pending','building') AND fingerprint=?""",
                            (processed, skipped, chatkey, fingerprint))

    def _write_batch(self, chatkey, fingerprint, batch, processed, indexed, skipped):
        with self._connection() as con:
            state = con.execute("SELECT fingerprint,status,active FROM sources WHERE chatkey=?", (chatkey,)).fetchone()
            if not state or not state["active"] or state["status"] != "building" or state["fingerprint"] != fingerprint:
                return False
            con.executemany("""INSERT INTO records(chatkey,start,end,message_id,time,day,sender,sender_fold,kind,
                               has_links,is_mini,fields_json,search_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
            con.execute("UPDATE sources SET processed=?,indexed=?,skipped=? WHERE chatkey=?",
                        (processed, indexed, skipped, chatkey))
        return True

    def _discard(self, chatkey):
        with self._connection() as con:
            con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))

    def _coverage(self, con):
        rows = list(con.execute("SELECT status,processed,indexed,skipped,name,error FROM sources WHERE active=1"))
        counts = {key: sum(row["status"] == key for row in rows) for key in ("ready", "building", "pending", "error")}
        current = next((row["name"] for row in rows if row["status"] == "building"), "")
        return {"total": len(rows), "ready": counts["ready"], "building": counts["building"],
                "pending": counts["pending"], "failed": counts["error"],
                "indexedMessages": sum(row["indexed"] for row in rows if row["status"] == "ready"),
                "skipped": sum(row["skipped"] for row in rows if row["status"] == "ready"),
                "current": current, "error": self.fatal_error}

    def status(self):
        if not self.database.is_file():
            return {"state":"idle", "total":len(self.catalog.snapshot()["chats"]), "ready":0,
                    "building":0, "pending":0, "failed":0, "indexedMessages":0, "skipped":0, "current":"", "error":""}
        try:
            with self._connection() as con:
                coverage = self._coverage(con)
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xff
            transient = code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            if "no such table" in str(exc).lower():
                try:
                    handle = self._lock()
                    transient = handle is None
                    self._unlock(handle)
                except OSError:
                    transient = False
            return {"state":"building" if transient else "partial", "total":len(self.catalog.snapshot()["chats"]),
                    "ready":0, "building":0, "pending":0, "failed":0, "indexedMessages":0,
                    "skipped":0, "current":"", "localWorker":False,
                    "error":"" if transient else "本地搜索索引无法读取，请关闭查看器后检查索引目录。"}
        running = bool(self.thread and self.thread.is_alive())
        coverage["state"] = ("building" if running or coverage["building"] or coverage["pending"]
                             else "partial" if coverage["failed"] or coverage["error"] else "ready")
        coverage["localWorker"] = running
        return coverage

    def query(self, parameters):
        query = parameters.get("q", "").strip()
        sender = parameters.get("sender", "").strip()
        source = parameters.get("source", "")
        scope = parameters.get("scope", "all")
        client = parameters.get("client", "all")
        category = parameters.get("category", "all")
        start_date, end_date = parameters.get("from", ""), parameters.get("to", "")
        if not query:
            raise ValueError("请输入要搜索的内容")
        if len(query) > 200 or len(sender) > 200 or SEPARATOR in query or "\x00" in query:
            raise ValueError("搜索内容无效或过长")
        if (scope not in ("all", "group", "direct") or client not in ("all", "wechat", "wecom", "unknown")
                or category not in ("all", "file", "media", "link", "miniprogram")):
            raise ValueError("搜索范围无效")
        for value in (start_date, end_date):
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError("日期格式无效") from None
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        page = int(parameters.get("page", "1"))
        if page < 1 or page > 100000:
            raise ValueError("搜索页码无效")
        self.prepare()
        wanted = query.casefold()
        where, args = ["s.active=1", "s.status='ready'", "instr(d.search_text,?)>0"], [wanted]
        join = ""
        with self._connection() as con:
            has_fts = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'").fetchone())
        if len(wanted) >= 3 and has_fts:
            join = " JOIN records_fts ON records_fts.rowid=d.id"
            where.append("records_fts MATCH ?")
            args.append('"' + wanted.replace('"', '""') + '"')
        if scope != "all": where.append("s.kind=?"); args.append(scope)
        if client == "unknown": where.append("s.client IN ('unknown','conflict')")
        elif client != "all": where.append("s.client=?"); args.append(client)
        if source: where.append("s.source=?"); args.append(source)
        if sender: where.append("instr(d.sender_fold,?)>0"); args.append(sender.casefold())
        if start_date: where.append("d.day>=?"); args.append(start_date)
        if end_date: where.append("d.day<>'' AND d.day<=?"); args.append(end_date)
        if category == "file": where.append("d.kind='file'")
        if category == "media": where.append("d.kind IN ('image','video')")
        if category == "link": where.append("(d.kind='link' OR d.has_links=1)")
        if category == "miniprogram": where.append("(d.kind='miniprogram' OR d.is_mini=1)")
        statement = " FROM records d JOIN sources s ON s.chatkey=d.chatkey" + join + " WHERE " + " AND ".join(where)
        with self._connection() as con:
            con.execute("BEGIN")
            coverage = self._coverage(con)
            total = con.execute("SELECT count(*)" + statement, args).fetchone()[0]
            rows = list(con.execute("""SELECT d.id,d.chatkey,d.start,d.end,d.time,d.sender,d.kind,d.fields_json,
                                  s.name,s.source,s.client,s.origin,s.kind AS chat_kind,s.fingerprint""" + statement +
                                  " ORDER BY (d.day='') ASC,d.time DESC,d.id DESC LIMIT ? OFFSET ?",
                                  [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE]))
            con.commit()
        items = []
        for row in rows:
            fields = json.loads(row["fields_json"])
            links = fields[5:]
            matching_links = [link for link in links if wanted in link.casefold()]
            items.append({"recordId":row["id"], "chatKey":row["chatkey"], "chatName":row["name"],
                          "chatKind":row["chat_kind"], "source":row["source"], "client":row["client"],
                          "origin":row["origin"], "fingerprint":row["fingerprint"],
                          "start":row["start"], "end":row["end"], "time":row["time"],
                          "sender":row["sender"] or "发言人未知", "kind":row["kind"],
                          "text":excerpt(fields[0], query, 500), "description":excerpt(fields[1], query, 240),
                          "quote":{"text":excerpt(fields[2], query, 240)},
                          "asset":{"filename":fields[3] or fields[4]},
                          "links":[excerpt(link, query, 300) for link in (matching_links or links)[:3]]})
        return {"items":items, "total":total, "page":page, "pageSize":PAGE_SIZE, "coverage":coverage}

    def resolve(self, record_id, chatkey, fingerprint):
        with self._connection() as con:
            row = con.execute("""SELECT d.chatkey,d.start,d.end,d.message_id,s.fingerprint FROM records d
                                 JOIN sources s ON s.chatkey=d.chatkey
                                 WHERE d.id=? AND d.chatkey=? AND s.fingerprint=? AND s.active=1 AND s.status='ready'""",
                              (record_id, chatkey, fingerprint)).fetchone()
        if not row:
            raise ValueError("搜索结果已变化，请重新搜索")
        chat = self.catalog.get(chatkey)
        try:
            current = source_fingerprint(chat)
        except OSError:
            raise ValueError("原始导出文件不可用，请重新搜索") from None
        if current != fingerprint:
            self.prepare()
            raise ValueError("原始导出已变化，请等待索引更新后重新搜索")
        return chat, dict(row)


"""A local, read-only browser for WeBridge conversation exports."""


import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import RLock, Timer
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile, BadZipFile
import webbrowser


BASE = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE / "exports"
STATE_DIR = BASE / ".chat-viewer-data"
CONFIG = STATE_DIR / "sources.json"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_ROW = 32 * 1024 * 1024


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def relative_path(value: str) -> str:
    value = str(value).replace("\\", "/")
    parts = PurePosixPath(value).parts
    if not value or value.startswith("/") or not parts or any(p in (".", "..") or ":" in p for p in parts):
        raise ValueError("导出目录中的文件路径无效")
    return "/".join(parts)


@dataclass(frozen=True)
class Conversation:
    key: str
    chat_id: str
    name: str
    kind: str
    source: str
    rows: int
    updated: int
    directory: Path | None = None
    archive: Path | None = None
    prefix: str = ""
    available: bool = True
    client: str = "unknown"
    review_status: str = "未提供"
    origin: str = ""
    preview: bool = False

    def public(self):
        return {"key": self.key, "name": self.name, "kind": self.kind,
                "source": self.source, "rows": self.rows, "updated": self.updated,
                "available": self.available, "client": self.client, "reviewStatus": self.review_status,
                "origin": self.origin, "preview": self.preview}


def export_metadata(manifest, review):
    manifest = manifest if isinstance(manifest, dict) else {}
    review = review if isinstance(review, dict) else {}
    coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    declared = {str(value).strip().lower() for value in
                (manifest.get("client"), coverage.get("client"), review.get("client")) if value}
    declared &= {"wechat", "wecom"}
    family = str(coverage.get("storageFamily") or "").lower()
    if family.startswith("wechat desktop "):
        declared.add("wechat")
    elif family.startswith("wecom "):
        declared.add("wecom")
    client = next(iter(declared)) if len(declared) == 1 else "conflict" if len(declared) > 1 else "unknown"
    human = manifest.get("humanReview") if isinstance(manifest.get("humanReview"), dict) else {}
    if review.get("_readError"):
        status = "复核信息无法读取"
    elif (review.get("status") == "confirmed" and human.get("status") == "confirmed"
            and review.get("reviewId") and review.get("reviewId") == human.get("reviewId")):
        status = "已标记复核"
    elif review.get("status") == "confirmed" or human.get("status") == "confirmed":
        status = "复核信息不完整"
    elif review.get("status") or human.get("status"):
        status = "未完成复核"
    else:
        status = "未提供"
    identities = {str(value) for value in (manifest.get("selfId"), review.get("selfId")) if value}
    fallback = {str(value) for value in (manifest.get("sourceFingerprint"), review.get("sourceFingerprint")) if value}
    if len(identities) > 1 or (not identities and len(fallback) > 1):
        origin = "来源标识冲突"
    else:
        identity = next(iter(identities or fallback), "")
        origin = "来源 " + hashlib.sha256((client + ":" + identity).encode("utf-8")).hexdigest()[:6] if identity else ""
    return client, status, origin


def make_conversation(chat, rows, source, *, directory=None, archive=None, prefix="", available=True,
                      manifest=None, review=None):
    kind = chat.get("conversationKind")
    if kind not in ("group", "direct"):
        return None
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return None
    identity = f"{archive or directory}|{prefix}|{chat_id}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    try:
        updated = int(chat.get("lastMessageTime") or 0)
    except (TypeError, ValueError):
        updated = 0
    client, review_status, origin = export_metadata(manifest, review)
    return Conversation(key, chat_id, str(chat.get("name") or "未命名会话"), kind, source,
                        max(0, int(rows or 0)), updated, directory, archive, prefix, available,
                        client, review_status, origin)


@contextmanager
def open_conversation_file(chat: Conversation, filename: str):
    relative = relative_path(filename)
    if chat.archive is not None:
        with ZipFile(chat.archive) as archive:
            member = chat.prefix.rstrip("/") + "/" + relative if chat.prefix else relative
            info = archive.getinfo(member)
            if info.is_dir():
                raise FileNotFoundError(relative)
            with archive.open(info) as stream:
                yield stream, info.file_size
    elif chat.directory is not None:
        base = chat.directory.resolve(strict=True)
        path = (base / Path(relative)).resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError("文件不在会话导出目录内")
        with path.open("rb") as stream:
            yield stream, path.stat().st_size
    else:
        raise FileNotFoundError("会话没有可读取的导出文件")


def read_conversation_json(chat, filename, limit=20 * 1024 * 1024):
    with open_conversation_file(chat, filename) as (stream, size):
        if size > limit:
            raise ValueError("导出信息文件过大")
        return json.loads(stream.read().decode("utf-8-sig"))


def discover_directory(root: Path, label: str, issues=None):
    """Recognize single exports, batches and ZIPs without following directory links."""
    issues = issues if issues is not None else []
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("请选择导出文件夹")
    conversations = []
    for current, dirs, files in os.walk(root, followlinks=False):
        path = Path(current)
        depth = len(path.relative_to(root).parts)
        dirs[:] = [name for name in dirs if name not in {"assets", "analysis", "batch-analysis", "node_modules", ".chat-viewer-data"}
                   and not (path / name).is_symlink()]
        if depth >= 4:
            dirs[:] = []
        if "manifest.json" in files and "messages.jsonl" in files:
            try:
                manifest = read_json(path / "manifest.json")
                if not isinstance(manifest, dict): raise ValueError("导出元数据格式无效")
                review = {}
                if "review.json" in files:
                    try: review = read_json(path / "review.json")
                    except (OSError, UnicodeError, ValueError):
                        review = {"_readError":True}; issues.append(f"复核信息无法读取：{path.name}")
                source = label if path == root or path.parent == root else f"{label} / {path.parent.name}"
                chat = make_conversation(manifest.get("chat") or {}, manifest.get("exportedRows"), source,
                                         directory=path, available=True, manifest=manifest, review=review)
                if chat: conversations.append(chat)
                else: issues.append(f"会话类型或标识不明确：{path.name}")
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                issues.append(f"导出元数据无法读取：{path.name}（{exc}）")
            dirs[:] = []
            if len(conversations) > 2000:
                raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的目录")
            continue
        for name in sorted(files):
            if name.lower().endswith(".zip"):
                try:
                    conversations.extend(discover_zip(path / name, f"{label} / {Path(name).stem}", issues))
                except (OSError, ValueError, BadZipFile) as exc:
                    issues.append(f"压缩资料无法读取：{name}（{exc}）")
        if len(conversations) > 2000:
            raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的目录")
    return conversations


def discover_zip(archive: Path, label: str, issues=None):
    issues = issues if issues is not None else []
    conversations = []
    with ZipFile(archive) as zipped:
        listed = zipped.namelist()
        names = set(listed)
        duplicates = {name for name, count in Counter(listed).items() if count > 1 and
                      (name.endswith(("/manifest.json", "/messages.jsonl", "/review.json")) or
                       name in ("manifest.json", "messages.jsonl", "review.json"))}
        for name in sorted(n for n in names if n == "manifest.json" or n.endswith("/manifest.json")):
            try:
                prefix = relative_path(name.rsplit("/", 1)[0]) if "/" in name else ""
                messages_name = f"{prefix}/messages.jsonl" if prefix else "messages.jsonl"
                if messages_name not in names: continue
                if name in duplicates or messages_name in duplicates:
                    issues.append(f"压缩包包含同名导出文件：{archive.name}"); continue
                info = zipped.getinfo(name)
                if info.file_size > 10 * 1024 * 1024: raise ValueError("导出元数据过大")
                manifest = json.loads(zipped.read(info).decode("utf-8-sig"))
                if not isinstance(manifest, dict): raise ValueError("导出元数据格式无效")
                review_name = f"{prefix}/review.json" if prefix else "review.json"
                review = {}
                if review_name in names:
                    try:
                        if review_name in duplicates: raise ValueError("复核文件重复")
                        info = zipped.getinfo(review_name)
                        if info.file_size > 10 * 1024 * 1024: raise ValueError("复核元数据过大")
                        review = json.loads(zipped.read(info).decode("utf-8-sig"))
                    except (OSError, UnicodeError, ValueError, KeyError):
                        review = {"_readError":True}; issues.append(f"复核信息无法读取：{archive.name}")
                chat = make_conversation(manifest.get("chat") or {}, manifest.get("exportedRows"), label,
                                         archive=archive, prefix=prefix, manifest=manifest, review=review)
                if chat: conversations.append(chat)
                else: issues.append(f"会话类型或标识不明确：{archive.name}")
            except (ValueError, KeyError, OSError, UnicodeError, TypeError) as exc:
                issues.append(f"压缩包中的导出无法读取：{archive.name}（{exc}）")
                continue
            if len(conversations) > 2000:
                raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的资料")
    return conversations


def discover_path(path: Path, label: str, issues=None):
    path = path.expanduser().resolve(strict=True)
    if path.is_dir(): return discover_directory(path, label, issues)
    if path.is_file() and path.suffix.lower() == ".zip":
        try: return discover_zip(path, label, issues)
        except BadZipFile: raise ValueError("ZIP 压缩包无效或不完整") from None
    raise ValueError("请选择导出文件夹或 ZIP 压缩包")


class Catalog:
    def __init__(self, default_root=DEFAULT_ROOT, config=CONFIG, initial_paths=None):
        self.default_root = Path(default_root)
        self.config = Path(config)
        self.initial_paths = [str(Path(path).expanduser().resolve()) for path in (initial_paths or [])]
        self.lock = RLock()
        self.chats = {}
        self.issues = []
        self.refresh()

    def refresh(self):
        chats, issues = [], []
        try:
            settings = read_json(self.config) if self.config.is_file() else {}
            if not isinstance(settings, dict): raise ValueError("资料设置格式无效")
            extras = settings.get("paths", settings.get("directories", []))
            if not isinstance(extras, list): raise ValueError("资料设置格式无效")
            roots = ([str(self.default_root)] if self.default_root.is_dir() else []) + self.initial_paths + extras
            paths = []
            for raw in roots:
                try:
                    path = Path(raw).expanduser().resolve(strict=True)
                    if path not in paths: paths.append(path)
                except (OSError, TypeError, ValueError) as exc:
                    issues.append(f"已添加的资料无法读取：{exc}")
            repeated = Counter(path.name.casefold() for path in paths)
            for path in paths:
                try:
                    label = f"资料：{path.name}"
                    if repeated[path.name.casefold()] > 1:
                        suffix = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:4]
                        label += f" · {path.parent.name} {suffix}"
                    chats.extend(discover_path(path, label, issues))
                except (OSError, ValueError, BadZipFile) as exc:
                    issues.append(f"已添加的资料无法读取：{exc}")
        except (OSError, ValueError, TypeError):
            issues.append("已添加的资料设置无法读取")
        with self.lock:
            unique = {}
            for chat in chats:
                unique.setdefault(chat.key, chat)
            self.chats = unique
            self.issues = issues

    def add_directory(self, raw_path):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请输入导出文件夹或 ZIP 路径")
        path = Path(raw_path.strip().strip('"')).expanduser().resolve(strict=True)
        warnings = []
        found = discover_path(path, f"资料：{path.name}", warnings)
        if not found:
            raise ValueError("没有找到包含 manifest.json 和 messages.jsonl 的群聊或私聊导出")
        with self.lock:
            added = [chat for chat in found if chat.key not in self.chats]
            if not added:
                raise ValueError("这些会话已经在列表中")
            settings = read_json(self.config) if self.config.is_file() else {}
            old = settings.get("paths", settings.get("directories", []))
            directories = list(dict.fromkeys([str(Path(item).resolve()) for item in old] + [str(path)]))
            self.config.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config.with_suffix(".tmp")
            tmp.write_text(json.dumps({"paths": directories}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.config)
            self.refresh()
        return {"found": len(added), "direct": sum(c.kind == "direct" for c in added),
                "group": sum(c.kind == "group" for c in added), "warnings":len(warnings)}

    def get(self, key):
        with self.lock:
            chat = self.chats.get(key)
        if not chat:
            raise ValueError("找不到该会话，请刷新列表")
        if not chat.available:
            raise ValueError("该会话的导出文件不完整")
        return chat

    def snapshot(self):
        with self.lock:
            chats = list(self.chats.values())
            issues = list(self.issues)
        chats.sort(key=lambda c: (-c.updated, c.name, c.source))
        return {"chats": [chat.public() for chat in chats], "issues": issues}


def read_previous_lines(stream, size, cursor, limit):
    """Return complete records in file order, with byte offsets for paging and source inspection."""
    if cursor is None:
        cursor = size
    if cursor < 0 or cursor > size:
        raise ValueError("消息位置无效，请重新打开会话")
    start, end = cursor, 0
    data = b""
    lines = []
    while len(lines) < limit:
        if end == 0 and start > 0:
            new_start = max(0, start - 512 * 1024)
            stream.seek(new_start)
            part = stream.read(start - new_start)
            data = part + data
            end += len(part)
            start = new_start
        while end > 0 and data[end - 1:end] in (b"\n", b"\r"):
            end -= 1
        if end == 0 and start == 0:
            break
        split = data.rfind(b"\n", 0, end)
        if split < 0 and start > 0:
            if len(data) > MAX_ROW:
                raise ValueError("单条消息超过可读取大小")
            new_start = max(0, start - 512 * 1024)
            stream.seek(new_start)
            part = stream.read(start - new_start)
            data = part + data
            end += len(part)
            start = new_start
            continue
        line_start = split + 1
        raw = data[line_start:end].strip(b"\r")
        if raw:
            lines.append((start + line_start, start + end, raw))
        end = line_start
        if split < 0:
            break
    older = lines[-1][0] if lines else 0
    return list(reversed(lines)), older


def present_message(row, start, end):
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quoted = quote.get("message") if isinstance(quote.get("message"), dict) else {}
    asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
    media = row.get("media") if isinstance(row.get("media"), dict) else {}
    links = [str(link) for link in row.get("links", []) if isinstance(link, str)] if isinstance(row.get("links"), list) else []
    asset_path = asset.get("path") or ""
    try:
        asset_path = relative_path(asset_path) if asset_path and relative_path(asset_path).startswith("assets/") else ""
    except ValueError:
        asset_path = ""
    return {"id": str(row.get("id") or ""), "time": str(row.get("time") or ""),
            "sender": str(row.get("senderName") or "发言人未知"), "senderId": str(row.get("senderId") or ""),
            "direction": row.get("direction") if row.get("direction") in ("incoming", "outgoing") else "unknown",
            "kind": str(row.get("kind") or "unknown"), "text": str(row.get("text") or ""),
            "links": links,
            "quote": {"sender": str(quote.get("senderName") or ""), "text": str(quoted.get("text") or ""),
                      "status": str(quote.get("resolutionStatus") or "")},
            "asset": {"path": asset_path, "status": str(asset.get("status") or ""),
                      "reason": str(asset.get("reason") or ""), "filename": str(media.get("filename") or asset.get("filename") or ""),
                      "previewOnly": bool(asset.get("previewOnly"))},
            "parseStatus": str(row.get("parseStatus") or ""), "start": start, "end": end}


def parse_line(chat, start, end, raw):
    try:
        row = json.loads(raw.decode("utf-8-sig"))
        if str(row.get("chatId") or "") != chat.chat_id:
            raise ValueError("消息的会话标识与导出不一致")
        return present_message(row, start, end)
    except (UnicodeError, json.JSONDecodeError, ValueError, AttributeError):
        return {"kind": "invalid", "text": "此条记录无法读取，请核对原始导出文件。",
                "direction": "unknown", "sender": "", "time": "", "start": start, "end": end}


def messages(chat, cursor=None, limit=40):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        lines, older = read_previous_lines(stream, size, cursor, limit)
    rows = [parse_line(chat, start, end, raw) for start, end, raw in lines]
    return {"messages": rows, "older": older if older > 0 else None}


def forward_lines(stream, size, cursor, limit):
    if cursor < 0 or cursor > size:
        raise ValueError("消息位置无效，请重新打开会话")
    stream.seek(cursor)
    lines = []
    while len(lines) < limit:
        start = stream.tell()
        raw = stream.readline(MAX_ROW + 2)
        if not raw:
            break
        if len(raw) > MAX_ROW:
            raise ValueError("单条消息超过可读取大小")
        clean = raw.rstrip(b"\r\n")
        if clean:
            lines.append((start, start + len(clean), clean))
    next_cursor = stream.tell()
    return lines, next_cursor if next_cursor < size else None


def messages_after(chat, cursor, limit=40):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        lines, newer = forward_lines(stream, size, cursor, limit)
    return {"messages": [parse_line(chat, start, end, raw) for start, end, raw in lines], "newer": newer}


def message_context(chat, start, end, radius=20):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        if start < 0 or end <= start or end > size or end - start > MAX_ROW:
            raise ValueError("消息位置无效，请重新搜索")
        if start:
            stream.seek(start - 1)
            if stream.read(1) != b"\n":
                raise ValueError("消息位置已变化，请重新搜索")
        previous, older = read_previous_lines(stream, size, start, radius)
        current_and_next, newer = forward_lines(stream, size, start, radius + 1)
        if not current_and_next or current_and_next[0][:2] != (start, end):
            raise ValueError("消息位置已变化，请重新搜索")
        rows = [parse_line(chat, a, b, raw) for a, b, raw in previous + current_and_next]
        if not rows or next((r for r in rows if r["start"] == start), {}).get("kind") == "invalid":
            raise ValueError("消息记录无法读取，请重新搜索")
    return {"messages": rows, "older": older if older > 0 else None, "newer": newer, "target": start}


def search_messages(chat, parameters, limit=40):
    query = parameters.get("q", "").strip()
    sender = parameters.get("sender", "").strip()
    sender_id = parameters.get("senderId", "").strip()
    category = parameters.get("category", "all")
    start_date, end_date = parameters.get("from", ""), parameters.get("to", "")
    if len(query) > 200 or len(sender) > 200 or len(sender_id) > 512:
        raise ValueError("搜索内容过长")
    if category not in ("all", "file", "media", "link", "miniprogram"):
        raise ValueError("未知的消息类型")
    for value in (start_date, end_date):
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("日期格式无效") from None
    if start_date and end_date and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    before = int(parameters["before"]) if parameters.get("before") else None
    simple = not (query or sender or sender_id or start_date or end_date) and category == "all"
    if simple:
        page = messages(chat, before, limit)
        return {"items": list(reversed([search_result(row) for row in page["messages"]])),
                "older": page["older"], "total": None, "skipped": 0}
    wanted, sender_wanted = query.casefold(), sender.casefold()

    def matches(row):
        kind = row.get("kind") or "unknown"
        links = row.get("links") if isinstance(row.get("links"), list) else []
        if category == "file" and kind != "file": return False
        if category == "media" and kind not in ("image", "video"): return False
        if category == "link" and kind != "link" and not links: return False
        if category == "miniprogram" and kind != "miniprogram" and not row.get("miniProgram"): return False
        if sender_id and str(row.get("senderId") or "") != sender_id: return False
        if sender_wanted and sender_wanted not in str(row.get("senderName") or "").casefold(): return False
        if start_date or end_date:
            day = str(row.get("time") or "")[:10]
            try:
                if date.fromisoformat(day).isoformat() != day: return False
            except ValueError:
                return False
            if start_date and day < start_date: return False
            if end_date and day > end_date: return False
        return not wanted or any(wanted in value.casefold() for value in searchable_fields(row))

    def decoded(raw):
        try:
            row = json.loads(raw.decode("utf-8-sig"))
            return row if isinstance(row, dict) and str(row.get("chatId") or "") == chat.chat_id else None
        except (UnicodeError, json.JSONDecodeError):
            return None

    hits, count, skipped = deque(maxlen=limit), 0, 0
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        if before is not None and (before < 0 or before > size):
            raise ValueError("搜索位置无效，请重新搜索")
        # The UI already has the exact initial count. For subsequent pages of a
        # regular file, walk backwards until one extra match proves there is a
        # previous page. ZIP streams retain the original forward scan because
        # backwards seeks repeatedly decompress the archive.
        if before is not None and parameters.get("pageOnly") == "1" and chat.archive is None:
            if before:
                stream.seek(before - 1)
                if stream.read(1) != b"\n":
                    raise ValueError("搜索位置无效，请重新搜索")
            cursor, found = before, []
            while cursor > 0 and len(found) <= limit:
                lines, cursor = read_previous_lines(stream, size, cursor, 2048)
                if not lines:
                    break
                for start, end, raw in reversed(lines):
                    row = decoded(raw)
                    if row is None:
                        skipped += 1
                    elif matches(row):
                        found.append(search_result(present_message(row, start, end), query))
                        if len(found) > limit:
                            break
            return {"items": found[:limit], "older": found[limit - 1]["start"] if len(found) > limit else None,
                    "total": None, "skipped": skipped}
        while True:
            pos = stream.tell()
            if before is not None and pos >= before:
                break
            raw = stream.readline(MAX_ROW + 2)
            if not raw:
                break
            if len(raw) > MAX_ROW:
                raise ValueError("单条消息超过可读取大小")
            row = decoded(raw)
            if row is None:
                skipped += 1
                continue
            if not matches(row): continue
            clean = raw.rstrip(b"\r\n")
            hits.append(present_message(row, pos, pos + len(clean)))
            count += 1
    values = list(hits)
    return {"items": list(reversed([search_result(row, query) for row in values])),
            "older": values[0]["start"] if count > limit else None, "total": count, "skipped": skipped}


def search_excerpt(value, query, limit):
    value = str(value or "")
    if len(value) <= limit:
        return value
    position = value.casefold().find(query.casefold()) if query else -1
    start = max(0, position - limit // 3) if position >= 0 else 0
    return ("…" if start else "") + value[start:start + limit] + ("…" if start + limit < len(value) else "")


def search_result(row, query=""):
    value = dict(row)
    value["text"] = search_excerpt(value.get("text"), query, 500)
    value["quote"] = dict(value.get("quote") or {})
    if "text" in value["quote"]:
        value["quote"]["text"] = search_excerpt(value["quote"]["text"], query, 240)
    links = value.get("links") or []
    matching_links = [link for link in links if query and query.casefold() in link.casefold()]
    value["links"] = [search_excerpt(link, query, 300) for link in (matching_links or links)[:3]]
    return value


def detail(chat):
    manifest = read_conversation_json(chat, "manifest.json")
    members = []
    member_warning = ""
    try:
        data = read_conversation_json(chat, "members.json")
        member_entries = [{"name": str(item.get("name") or "未命名"), "id": str(item.get("id") or "")}
                          for item in data.get("members", []) if isinstance(item, dict)]
        members = [item["name"] for item in member_entries]
        if data.get("historicalSpeakersAreMembers"):
            member_warning = "部分名称来自历史发言记录，不能确认当前成员状态。"
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        member_entries = []
    return {"chat": chat.public(), "created": str(manifest.get("createdAt") or ""),
            "members": members, "memberEntries": member_entries, "memberWarning": member_warning,
            "truncated": bool(manifest.get("truncatedByLimit")),
            "mediaSummary": str(manifest.get("mediaSummaryText") or "")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def allowed(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def send_bytes(self, data, kind, status=200, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self.allowed():
            self.send_error(403)
            return
        url = urlparse(self.path)
        params = {key: value[0] for key, value in parse_qs(url.query).items()}
        try:
            if url.path in ASSETS:
                filename, content_type = ASSETS[url.path]
                bundled = globals().get("_BUNDLED_ASSETS")
                data = bundled[filename].encode("utf-8") if bundled is not None else (BASE / filename).read_bytes()
                self.send_bytes(data, content_type)
            elif url.path == "/api/conversations":
                self.send_json(self.server.catalog.snapshot())
            elif url.path == "/api/detail":
                self.send_json(detail(self.server.catalog.get(params.get("chat", ""))))
            elif url.path == "/api/messages":
                chat = self.server.catalog.get(params.get("chat", ""))
                cursor = int(params["before"]) if "before" in params else None
                self.send_json(messages(chat, cursor))
            elif url.path == "/api/messages-after":
                self.send_json(messages_after(self.server.catalog.get(params.get("chat", "")), int(params["after"])))
            elif url.path == "/api/message-context":
                self.send_json(message_context(self.server.catalog.get(params.get("chat", "")), int(params["start"]), int(params["end"])))
            elif url.path == "/api/search":
                self.send_json(search_messages(self.server.catalog.get(params.get("chat", "")), params))
            elif url.path == "/api/global/status":
                self.send_json(self.server.global_index.status())
            elif url.path == "/api/global/search":
                self.send_json(self.server.global_index.query(params))
            elif url.path == "/api/global/context":
                chat, record = self.server.global_index.resolve(int(params["record"]), params["chat"], params["fingerprint"])
                context = message_context(chat, record["start"], record["end"])
                target = next((row for row in context["messages"] if row["start"] == record["start"]), None)
                if not target or target.get("id", "") != record["message_id"] or source_fingerprint(chat) != record["fingerprint"]:
                    raise ValueError("原始记录已变化，请重新搜索")
                self.send_json(context | {"chatKey":chat.key})
            elif url.path == "/api/record":
                chat = self.server.catalog.get(params.get("chat", ""))
                start, end = int(params["start"]), int(params["end"])
                with open_conversation_file(chat, "messages.jsonl") as (stream, size):
                    if start < 0 or end <= start or end > size or end - start > MAX_ROW:
                        raise ValueError("记录位置无效")
                    stream.seek(start)
                    row = json.loads(stream.read(end - start).decode("utf-8-sig"))
                if str(row.get("chatId") or "") != chat.chat_id:
                    raise ValueError("记录不属于当前会话")
                self.send_json(row)
            elif url.path == "/api/asset":
                chat = self.server.catalog.get(params.get("chat", ""))
                path = relative_path(params.get("path", ""))
                if not path.startswith("assets/"):
                    raise ValueError("文件不在导出附件目录中")
                with open_conversation_file(chat, path) as (stream, size):
                    if size > 100 * 1024 * 1024:
                        raise ValueError("附件超过本地预览大小限制")
                    data = stream.read()
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    mime = "application/octet-stream"
                self.send_bytes(data, mime, disposition=None if mime.startswith("image/") else 'attachment; filename="attachment"')
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, KeyError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "资料读取失败"}, 400)

    def do_POST(self):
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if (not self.allowed() or self.headers.get("Origin") != expected
                or self.headers.get("Content-Type") != "application/json"):
            self.send_error(403)
            return
        try:
            if self.path not in ("/api/sources", "/api/global/prepare"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("资料目录无效")
            data = json.loads(self.rfile.read(length))
            if self.path == "/api/global/prepare":
                self.send_json(self.server.global_index.prepare(retry_errors=data.get("retry") is True))
            else:
                result = self.server.catalog.add_directory(data.get("path"))
                if self.server.global_index.exists():
                    try:
                        self.server.global_index.prepare()
                    except (OSError, ValueError, sqlite3.Error):
                        result["indexWarning"] = "资料已接入，全局索引未能更新，请稍后重试"
                self.send_json(result)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "无法接入资料"}, 400)


def main():
    parser = argparse.ArgumentParser(description="本地聊天记录查看器")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--source", action="append", default=[], help="导出文件夹或 ZIP，可重复指定")
    parser.add_argument("--data-dir", default=str(STATE_DIR), help="保存本地设置和搜索索引的目录")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.catalog = Catalog(config=data_dir / "sources.json", initial_paths=args.source)
    server.global_index = GlobalSearchIndex(server.catalog, data_dir / "global-search-index", open_conversation_file)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"会话档案已启动：{url}", flush=True)
    print("关闭此窗口即可停止；原始导出文件不会被修改。", flush=True)
    if not args.no_browser:
        Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
