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
  async function setOccupant(inBed) {
    await fetch(`http://${controller}/api/v1/occupant`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ in_bed: inBed }),
    });
  }
  let lightOn = true;
  $("btnOn").onclick = () => setLight(true);
  $("btnOff").onclick = () => setLight(false);
  $("wallSwitch").onclick = () => setLight(!lightOn);
  $("btnSleep").onclick = () => setOccupant(true);
  $("btnWake").onclick = () => setOccupant(false);

  // User image (assets/person.png) if present, otherwise the bundled placeholder silhouette.
  (function loadPerson() {
    const img = new Image();
    img.onload = () => $("personImg").setAttribute("href", `assets/person.png?v=${Date.now()}`);
    img.src = `assets/person.png?v=${Date.now()}`;
  })();

  let lastClapKey = null, clapTimer = null;
  function showClap(r) {
    const key = `${r.session_id}-${r.received_at}`;
    if (key === lastClapKey) return;
    const first = lastClapKey === null;
    lastClapKey = key;
    if (first) return;  // do not replay an old detection when the page opens
    $("clapText").textContent = `👏 off 감지 ${fmt(r.confidence)}`;
    $("clapBubble").classList.remove("hidden");
    clearTimeout(clapTimer);
    clapTimer = setTimeout(() => $("clapBubble").classList.add("hidden"), 4000);
  }

  function renderScene(s, d) {
    const on = s.light.on;
    lightOn = on;
    $("bulb").classList.toggle("on", on);
    $("lightGlow").style.opacity = on ? 1 : 0;
    $("dark").style.opacity = on ? 0 : 0.62;
    $("switchToggle").setAttribute("y", on ? 204 : 215);
    $("switchToggle").setAttribute("fill", on ? "#22c55e" : "#64748b");

    const inBed = !!(s.occupant && s.occupant.in_bed);
    $("personImg").classList.toggle("hidden", !inBed);
    $("occState").textContent = inBed ? "침대에 누움" : "침대 밖";

    const rt = (d && d.runtime) || {}, l = d ? d.listening : { active: false };
    const gate = rt.gate || (l.active ? "listening" : d && d.online ? "watching" : "offline");
    const camOn = d && d.online && gate !== "idle_light_off" && gate !== "paused" && rt.runtime_enabled !== false;
    $("fov").setAttribute("class", `fov ${!camOn ? "off" : l.active ? "listening" : ""}`);
    $("micRing").classList.toggle("hidden", !l.active);
    $("camLed").setAttribute("fill", !d || !d.online ? "#64748b" : l.active ? "#a855f7" : camOn ? "#22c55e" : "#f59e0b");
    const v = d ? d.vision : {};
    $("camBadge").textContent = !d || !d.online ? "ESP32 오프라인"
      : !camOn ? (gate === "idle_light_off" ? "카메라 OFF (조명 꺼짐)" : "카메라 일시정지")
      : v.label === "Fall" ? `카메라: Fall ${fmt(v.fall_confidence)}`
      : v.label === "Standing" ? `카메라: Standing ${fmt(v.standing_confidence)}` : "카메라: 감지 없음";
    $("micBadge").textContent = l.active ? `🎤 녹음 중 ${(l.remaining_ms / 1000).toFixed(1)} s` : "";
    $("sceneLabel").textContent = `${inBed ? "가상 사람: 취침 중" : "가상 사람: 침대 밖"} · 조명 ${on ? "ON" : "OFF"}`;
    const recent = ((s.voice || {}).recent || [])[0];
    if (recent) showClap(recent);
  }

  function render(s) {
    // light
    const on = s.light.on;
    $("lightState").textContent = on ? "ON" : "OFF";
    $("lightBy").textContent = `(${s.light.changed_by})`;
    $("lightReason").textContent = s.light.reason ? `마지막 변경: ${s.light.reason}` : "";
    $("autoOn").style.display = s.light.auto_control ? "" : "none";
    $("autoNotice").style.display = s.light.auto_control ? "none" : "";

    // device (prefer a real device, else first)
    const d = s.devices.find((x) => x.source === "device") || s.devices[0];
    renderScene(s, d);
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
