from __future__ import annotations

from html import escape


DISPLAY_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>特检智能体</title>
  <style>
    :root {
      color-scheme: dark;
      --accent: #23c47b;
      --ink: #17222e;
      --muted: #536577;
      --panel: #dcedf9;
      --panel-2: #e8f4fd;
      --line: #b9d3eb;
      font-family: Arial, "Microsoft YaHei", "PingFang SC", sans-serif;
    }
    * { box-sizing: border-box; }
    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
      background: #e8f4fd;
      color: var(--ink);
      overflow: hidden;
    }
    body {
      display: grid;
      place-items: center;
    }
    .screen {
      position: relative;
      width: 480px;
      height: 320px;
      background: #e8f4fd;
      overflow: hidden;
    }
    .agent-card {
      position: absolute;
      inset: 0;
      width: 480px;
      height: 320px;
      padding: 0;
      border-radius: 0;
      background: #e8f4fd;
      overflow: hidden;
    }
    .panel {
      position: relative;
      width: 100%;
      height: 100%;
      border-radius: 0;
      background: var(--panel);
      border: 0;
      padding: 82px 18px 28px 28px;
      display: grid;
      grid-template-columns: 1fr 120px;
      gap: 22px;
    }
    .panel::before {
      content: "●  特检智能体";
      position: absolute;
      left: 0;
      right: 0;
      top: 0;
      height: 58px;
      padding: 17px 28px;
      background: #daedfa;
      border-bottom: 2px solid rgba(185, 211, 235, 0.72);
      color: var(--accent);
      font-size: 19px;
      font-weight: 700;
    }
    .panel.select {
      display: block;
      padding: 76px 28px 28px;
    }
    .title {
      font-size: 36px;
      line-height: 1.12;
      font-weight: 700;
      letter-spacing: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .select .title {
      font-size: 27px;
      margin-bottom: 8px;
    }
    .subtitle {
      margin-top: 16px;
      font-size: 20px;
      line-height: 1.25;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .field {
      height: 44px;
      margin-bottom: 18px;
      border-radius: 8px;
      background: #fff;
      border: 1px solid #c2d9ed;
      padding: 9px 14px;
      font-size: 19px;
      color: #6b7d8d;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      max-width: 250px;
      height: 36px;
      margin-top: 28px;
      padding: 0 16px;
      border-radius: 8px;
      background: var(--accent);
      color: #fff;
      font-size: 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .robot-tile {
      height: 100%;
      border-radius: 0;
      background: var(--panel-2);
      border: 0;
      border-left: 2px solid var(--line);
      display: grid;
      place-items: center;
    }
    .robot {
      position: relative;
      width: 78px;
      height: 72px;
    }
    .robot::before {
      content: "";
      position: absolute;
      left: 0;
      top: 18px;
      width: 78px;
      height: 51px;
      border: 6px solid var(--accent);
      border-radius: 15px;
    }
    .robot::after {
      content: "";
      position: absolute;
      left: 18px;
      top: 36px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 32px 0 0 var(--accent);
    }
    .antenna {
      position: absolute;
      left: 9px;
      top: 0;
      width: 60px;
      height: 30px;
      border-top: 6px solid var(--accent);
      border-left: 6px solid var(--accent);
      border-right: 6px solid var(--accent);
      border-radius: 14px 14px 0 0;
    }
    .list {
      display: grid;
      gap: 4px;
    }
    .row {
      height: 34px;
      border-radius: 8px;
      background: #f4f9fd;
      border: 1px solid #d5e4f0;
      color: #233242;
      padding: 5px 14px;
      font-size: 18px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .row.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .hint {
      position: absolute;
      left: 28px;
      bottom: 10px;
      max-width: 280px;
      height: 30px;
      padding: 5px 12px;
      border-radius: 8px;
      background: #e7f0f8;
      color: #536577;
      font-size: 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .meta {
      display: none;
    }
    .popup {
      position: absolute;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.52);
    }
    .popup.show { display: flex; }
    .popup-box {
      width: 265px;
      height: 84px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.98);
      border: 2px solid var(--accent);
      padding: 17px 22px;
      overflow: hidden;
    }
    .popup-title {
      font-size: 19px;
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .popup-message {
      margin-top: 8px;
      font-size: 14px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  </style>
</head>
<body>
  <div class="screen">
    <div class="agent-card">
      <div id="panel" class="panel">
        <div id="left"></div>
        <div class="robot-tile"><div class="robot"><div class="antenna"></div></div></div>
      </div>
    </div>
    <div id="meta" class="meta"></div>
    <div id="popup" class="popup">
      <div class="popup-box">
        <div id="popupTitle" class="popup-title"></div>
        <div id="popupMessage" class="popup-message"></div>
      </div>
    </div>
  </div>
  <script>
    const COPY = {
      wait_scan: ["等待患者报到", "请扫描患者申请单", "等待报到"],
      select_item: ["患者ID扫码", "选择检查项目", "选择项目"],
      inputting: ["正在检查中", "正在自动录入", "自动录入"],
      upload_done: ["录入完成", "可以继续患者报到", "已完成"],
      not_found: ["未找到患者申请单", "请核对条码后重试", "未找到"],
      querying: ["正在查询", "请稍候", "查询申请单"],
      wait_report: ["正在检查中", "正在自动录入", "检查中"],
      exam_mismatch: ["项目不符", "患者检查项目与设备不符", "未执行"],
      printer_error: ["本地需要打印机", "请检查打印链路", "异常"],
      connected: ["智能体已连接", "正在进入患者报到", "已连接"],
    };
    const ACCENTS = {
      ok: "#23c47b",
      blue: "#468fff",
      amber: "#ffb74d",
      red: "#f55c5c",
    };
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }
    function labelFor(screen) {
      if (screen === "query_not_found") screen = "not_found";
      if (screen === "api_querying") screen = "querying";
      if (screen === "report_waiting") screen = "wait_report";
      if (screen === "gadget_error") screen = "printer_error";
      return COPY[screen] || COPY.wait_scan;
    }
    function accentFor(screen) {
      if (screen === "not_found" || screen === "query_not_found" || screen === "exam_mismatch" || screen === "printer_error" || screen === "gadget_error") return ACCENTS.red;
      if (screen === "select_item" || screen === "querying" || screen === "api_querying" || screen === "wait_report" || screen === "report_waiting") return ACCENTS.blue;
      if (screen === "inputting") return ACCENTS.amber;
      return ACCENTS.ok;
    }
    function itemTitle(item) {
      if (!item || typeof item !== "object") return "未命名项目";
      return item.exam_item || item.exam_item_name || item.title || item.name || "未命名项目";
    }
    function renderSelect(display) {
      const items = Array.isArray(display.items) ? display.items : [];
      const selected = Number(display.selected_index || 0);
      const visible = [];
      for (let offset = 0; offset < Math.min(4, items.length); offset += 1) {
        visible.push(items[(selected + offset) % items.length]);
      }
      const rows = visible.length
        ? visible.map((item, index) => `<div class="row${index === 0 ? " active" : ""}">${index === 0 ? "&gt; " : ""}${esc(itemTitle(item))}</div>`).join("")
        : `<div class="row">未查询到可选择项目</div>`;
      return `
        <div class="title">选择检查项目</div>
        <div class="list">${rows}</div>
        <div class="hint">上/下选择　确认键确认</div>
      `;
    }
    function renderNormal(screen, display) {
      const copy = labelFor(screen);
      const title = display.title && /[\\u4e00-\\u9fff]/.test(display.title) ? display.title : copy[0];
      const message = display.message && /[\\u4e00-\\u9fff]/.test(display.message) ? display.message : copy[1];
      const field = screen === "wait_scan" ? `<div class="field">请扫描患者申请单</div>` : "";
      const mainTitle = `<div class="title">${esc(title)}</div>`;
      const subtitle = screen === "wait_scan" ? "" : `<div class="subtitle">${esc(message)}</div>`;
      return `
        ${field}
        ${mainTitle}
        ${subtitle}
        <div class="tag">${esc(copy[2])}</div>
      `;
    }
    function renderPopup(display) {
      const popup = display && typeof display.popup === "object" ? display.popup : null;
      const node = document.getElementById("popup");
      if (!popup) {
        node.classList.remove("show");
        return;
      }
      document.getElementById("popupTitle").textContent = popup.title || "文件已接收";
      document.getElementById("popupMessage").textContent = popup.message || "正在转换并打印";
      node.classList.add("show");
    }
    function metaText(data) {
      const parts = [];
      if (data.last_scan) parts.push("最近扫码 " + data.last_scan);
      if (Number.isFinite(Number(data.queued_events))) parts.push("队列 " + data.queued_events);
      if (Number.isFinite(Number(data.print_jobs))) parts.push("打印 " + data.print_jobs);
      if (Number.isFinite(Number(data.msc_files))) parts.push("U盘 " + data.msc_files);
      return parts.join("    ");
    }
    async function refresh() {
      const panel = document.getElementById("panel");
      const left = document.getElementById("left");
      try {
        const res = await fetch("/display/state", { cache: "no-store" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const display = data.display && typeof data.display === "object" ? data.display : {};
        const screen = display.screen || "wait_scan";
        document.documentElement.style.setProperty("--accent", accentFor(screen));
        panel.className = screen === "select_item" ? "panel select" : "panel";
        left.innerHTML = screen === "select_item" ? renderSelect(display) : renderNormal(screen, display);
        document.getElementById("meta").textContent = metaText(data);
        renderPopup(display);
      } catch (err) {
        document.documentElement.style.setProperty("--accent", ACCENTS.red);
        panel.className = "panel";
        left.innerHTML = renderNormal("printer_error", { title: "连接异常", message: "本地服务未响应" });
        document.getElementById("meta").textContent = "";
        renderPopup({});
      }
    }
    refresh();
    setInterval(refresh, 800);
  </script>
</body>
</html>
"""


def html_response() -> str:
    return DISPLAY_HTML


def safe_text(value: object) -> str:
    return escape(str(value), quote=False)
