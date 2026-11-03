// BedLight AI simulator: renders controller state from ws://<host>:8000/ws.
// Override the controller address with ?controller=host:port
(function () {
  const params = new URLSearchParams(location.search);
  const controller = params.get("controller") || `${location.hostname || "127.0.0.1"}:8000`;
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 2) => (v === null || v === undefined ? "-" : Number(v).toFixed(d));
  let ws = null;

  function connect() {
    ws = new WebSocket(`ws://${controller}/ws`);
    ws.onopen = () => setPill($("conn"), "ok", `controller ${controller}`);
    ws.onclose = () => { setPill($("conn"), "bad", "controller 연결 끊김 - 재시도"); setTimeout(connect, 2000); };
    ws.onerror = () => ws.close();
    ws.onmessage = (m) => { try { render(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  }

  function setPill(el, cls, text) { el.className = `pill ${cls}`; el.textContent = text; }

  async function setLight(on) {
    await fetch(`http://${controller}/api/v1/light`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }),
    });
  }
  $("btnOn").onclick = () => setLight(true);
  $("btnOff").onclick = () => setLight(false);

  function render(s) {
    // light
    const on = s.light.on;
    $("lightState").textContent = on ? "ON" : "OFF";
    $("lightBy").textContent = `(${s.light.changed_by})`;
    $("lightReason").textContent = s.light.reason ? `마지막 변경: ${s.light.reason}` : "";
    $("autoOn").style.display = s.light.auto_control ? "" : "none";
    $("bulb").classList.toggle("on", on);
    $("lightGlow").style.display = on ? "" : "none";
    $("wall").classList.toggle("lit", false);
    $("autoNotice").style.display = s.light.auto_control ? "none" : "";

    // device (prefer a real device, else first)
    const d = s.devices.find((x) => x.source === "device") || s.devices[0];
    if (!d) { $("devId").textContent = "수신 이벤트 없음"; return renderServer(s); }
    $("devId").textContent = d.device_id;
    $("devOnline").innerHTML = d.online ? '<span class="pill ok">online</span>' : `<span class="pill bad">offline</span> ${d.seconds_since_seen ?? "-"} s`;
    $("devSource").innerHTML = `<span class="src-${d.source}">${d.source}</span>`;
    const gate = (d.runtime || {}).gate;
    const gateText = { watching: "WATCHING (Fall 감시)", listening: "LISTENING", idle_light_off: "IDLE (조명 OFF → 감지 중단)", paused: "PAUSED (r)" };
    $("devGate").textContent = gate ? (gateText[gate] || gate) : "-";
    $("devFw").textContent = d.firmware_version || "-";
    const v = d.vision, rt = d.runtime || {};
    $("visLabel").textContent = v.label || "None";
    $("visFall").textContent = fmt(v.fall_confidence);
    $("visStand").textContent = fmt(v.standing_confidence);
    $("visInf").textContent = v.inference_ms == null ? "-" : `${fmt(v.inference_ms, 0)} ms`;
    $("visFps").textContent = rt.fps == null ? "-" : fmt(rt.fps, 2);
    $("personLying").classList.toggle("hidden", v.label !== "Fall");
    $("personStanding").classList.toggle("hidden", v.label !== "Standing");
    $("sceneLabel").textContent = v.label === "Fall" ? "Fall 감지 (누운 상태로 간주)" : v.label === "Standing" ? "Standing 감지" : "감지 없음";

    // listening
    const l = d.listening;
    setPill($("listenBadge"), l.active ? "listen" : "off", l.active ? "LISTENING (마이크 ON)" : "WATCHING");
    const hold = l.hold_ms || 5000;
    $("listenBar").style.width = l.active ? `${Math.min(100, (100 * l.remaining_ms) / hold)}%` : "0";
    $("listenRemain").textContent = l.active ? `${(l.remaining_ms / 1000).toFixed(1)} s` : "-";
    $("listenSession").textContent = l.session_id ? `session ${l.session_id}` : "";
    $("listenExt").textContent = l.active ? l.extensions : "-";
    $("devSent").textContent = d.audio_device.chunks_sent;
    $("devFail").textContent = d.audio_device.chunks_failed;
    $("devUp").textContent = d.audio_device.last_upload_ms == null ? "-" : `${d.audio_device.last_upload_ms} ms`;
    renderServer(s);
  }

  function renderVoice(s) {
    const v = s.voice || {}, m = v.model;
    if (!m) { $("vmModel").textContent = "voice-service 응답 없음"; }
    else if (m.loaded) { $("vmModel").innerHTML = `<span class="pill ok">loaded</span> ${m.model.name || ""} ${m.model.version || ""}`; }
    else { $("vmModel").innerHTML = `<span class="pill off">미배포</span> ${m.reason || ""}`; }
    $("vmKeyword").textContent = m ? `${m.keyword} (threshold ${m.threshold})` : "-";
    $("vmWindows").textContent = m && m.stats ? `${m.stats.windows} / 감지 ${m.stats.detections}` : "-";
    $("vmRecent").innerHTML = (v.recent || []).map((r) =>
      `<li><span class="src-${r.source}">${r.source}</span> ${r.received_at.slice(11, 19)} conf ${fmt(r.confidence)}</li>`).join("") || "<li>없음</li>";
    $("decisions").innerHTML = (s.decisions || []).slice(0, 6).map((d) =>
      `<li class="${d.accepted ? "acc" : "rej"}">${d.accepted ? "소등" : "무시"} — ${d.reason}${d.keyword_to_decision_ms != null ? ` (${d.keyword_to_decision_ms} ms)` : ""}</li>`).join("") || "<li>없음</li>";
  }

  function renderServer(s) {
    renderVoice(s);
    const a = s.server_audio, x = a.latest_session;
    $("srvReach").innerHTML = a.reachable ? '<span class="pill ok">reachable</span>' : a.reachable === false ? '<span class="pill bad">unreachable</span>' : "-";
    $("srvSession").textContent = x ? `${x.session_id} (${x.device_id})` : "-";
    $("srvChunks").textContent = x ? x.received_chunks : "-";
    $("srvDur").textContent = x ? `${(x.received_duration_ms / 1000).toFixed(1)} s` : "-";
    $("srvMissing").textContent = x ? (x.missing_sequences.length ? x.missing_sequences.join(", ") : "없음") : "-";
    $("srvDisc").textContent = x ? x.discontinuities.length : "-";
    $("srvOvf").textContent = x ? x.dma_overflows : "-";
    $("srvComplete").textContent = x ? `${x.final_received ? "final 수신" : "수신 중"} / ${x.complete ? "완전" : "불완전"}` : "-";
    const rows = (s.events || []).slice(0, 15).map((e) =>
      `<tr><td>${e.received_at.slice(11, 19)}</td><td class="src-${e.source}">${e.source}</td><td>${e.event_type}</td><td>${fmt(e.confidence)}</td></tr>`);
    $("events").innerHTML = rows.join("");
  }

  connect();
})();
