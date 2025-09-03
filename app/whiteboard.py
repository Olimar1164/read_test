from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import json

router = APIRouter()

INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Pizarra interactiva</title>
    <style>
      html,body { height:100%; margin:0; }
      body { font-family: Arial, sans-serif; display:flex; height:100%; }
  #left { width:820px; padding:8px; box-sizing:border-box; }
      #board { background:#fff; border:1px solid #ccc; display:block; }
      #toolbar { padding:6px 0; }
  /* Icon button styles for eraser and undo - uniform square */
  .icon-btn { display:inline-flex; align-items:center; justify-content:center; width:92px; height:36px; padding:4px; margin-right:6px; border:1px solid #bbb; background:#fff; border-radius:6px; cursor:pointer; box-sizing:border-box; font-family: inherit; font-size: 13px; }
  .icon-btn.active { background:#e6f7ff; border-color:#66b3ff; }
      #right { flex:1; border-left:1px solid #eee; display:flex; flex-direction:column; }
      #messages { flex:1; overflow:auto; padding:8px; background:#fafafa; }
      .msg { margin:6px 0; }
      #composer { display:flex; padding:8px; border-top:1px solid #eee; }
      #composer input[type=text] { flex:1; padding:6px; margin-right:8px; }
      #meta { font-size:12px; color:#333; margin-left:8px; }
    </style>
  </head>
  <body>
    <div id="left">
      <div id="toolbar">
        <!-- Visible buttons: Deshacer and Goma (text only, identical size) -->
        <button id="undo" title="Deshacer (Ctrl+Z)" aria-label="Deshacer" class="icon-btn">Deshacer</button>

        <!-- Eraser toggle: hidden checkbox + visible label button (text) -->
        <input id="eraser" type="checkbox" style="display:none" />
        <label for="eraser" id="eraserBtn" class="icon-btn" title="Goma" role="button" aria-pressed="false">Goma</label>

        <label>Color: <input id="color" type="color" value="#000000"></label>
        <label>Tamaño: <input id="size" type="range" min="1" max="40" value="4"></label>
        <span id="meta">Resolución pizarra: 800x600</span>
      </div>
      <canvas id="board" width="800" height="600"></canvas>
    </div>
    <div id="right">
      <div id="messages"></div>
      <div id="composer">
        <input id="name" type="text" placeholder="Tu nombre" value="Anon" style="width:120px; margin-right:8px;" />
        <input id="text" type="text" placeholder="Escribe un mensaje" />
        <button id="send">Enviar</button>
      </div>
    </div>
    <script>
  const proto = (location.protocol === 'https:' ? 'wss://' : 'ws://');
  const ws = new WebSocket(proto + location.host + '/whiteboard/ws');
  const clientId = (crypto && crypto.randomUUID) ? crypto.randomUUID() : ('c_' + Math.random().toString(36).slice(2,9));
      const canvas = document.getElementById('board');
      const ctx = canvas.getContext('2d');
      const colorEl = document.getElementById('color');
      const sizeEl = document.getElementById('size');
      const messagesEl = document.getElementById('messages');
      const nameEl = document.getElementById('name');
      const textEl = document.getElementById('text');
      const eraserBtn = document.getElementById('eraserBtn');
      // sync visual pressed state from checkbox
      function syncEraserVisual() {
        const cb = document.getElementById('eraser');
        if (cb.checked) {
          eraserBtn.classList.add('active');
          eraserBtn.setAttribute('aria-pressed', 'true');
        } else {
          eraserBtn.classList.remove('active');
          eraserBtn.setAttribute('aria-pressed', 'false');
        }
      }
      // toggle checkbox when label clicked (label is for the hidden checkbox so browser toggles it automatically)
      document.getElementById('eraser').addEventListener('change', syncEraserVisual);
      // initialize visual
      syncEraserVisual();

      // expose limpiar() for console use (no visible clear button in toolbar)
      window.limpiar = function() {
        ctx.clearRect(0,0,canvas.width, canvas.height);
        try { ws.send(JSON.stringify({ type: 'clear', clientId: clientId })); } catch (e) { /* ignore if ws not ready */ }
      };

      // undo (button + Ctrl+Z)
      document.getElementById('undo').addEventListener('click', () => {
        ws.send(JSON.stringify({type:'undo', clientId: clientId}));
      });
      window.addEventListener('keydown', (e) => {
        const z = e.key === 'z' || e.key === 'Z';
        if ((e.ctrlKey || e.metaKey) && z) {
          e.preventDefault();
          ws.send(JSON.stringify({type:'undo', clientId: clientId}));
        }
      });

      document.getElementById('send').addEventListener('click', sendChat);
      textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });

      function sendChat() {
        const t = textEl.value && textEl.value.trim();
        if (!t) return;
        const payload = { type: 'chat', clientId: clientId, name: nameEl.value || 'Anon', text: t };
        ws.send(JSON.stringify(payload));
        textEl.value = '';
      }

  let drawing = false;
  let last = null;
  let currentStrokeId = null;

      canvas.addEventListener('pointerdown', (e) => {
        drawing = true;
        last = {x: e.offsetX, y: e.offsetY};
        currentStrokeId = clientId + '_' + Date.now();
        // capture pointer so we always receive pointerup even if leaving canvas
        try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
        const start = {
          type: 'stroke_start',
          clientId: clientId,
          strokeId: currentStrokeId,
          color: colorEl.value,
          size: parseInt(sizeEl.value,10),
          tool: document.getElementById('eraser').checked ? 'eraser' : 'pen',
          from: last
        };
        ws.send(JSON.stringify(start));
      });

      function endStroke(e) {
        if (!drawing) return;
        drawing = false;
        const endMsg = { type: 'stroke_end', clientId: clientId, strokeId: currentStrokeId };
        try { canvas.releasePointerCapture(e.pointerId); } catch (err) {}
        currentStrokeId = null;
        last = null;
        ws.send(JSON.stringify(endMsg));
      }

      canvas.addEventListener('pointerup', endStroke);
      canvas.addEventListener('pointercancel', endStroke);
      // if pointer leaves viewport still stop drawing when pointer is released
      canvas.addEventListener('pointerout', (e) => {
        // do not stop if moving inside canvas between children; only stop on pointerup/cancel
      });

      canvas.addEventListener('pointermove', (e) => {
        if (!drawing) return;
        const cur = {x: e.offsetX, y: e.offsetY};
        const payload = {
          type: 'stroke_point',
          clientId: clientId,
          strokeId: currentStrokeId,
          from: last,
          to: cur
        };
        ws.send(JSON.stringify(payload));
        drawLine({ from: last, to: cur, color: colorEl.value, size: parseInt(sizeEl.value,10), tool: document.getElementById('eraser').checked ? 'eraser' : 'pen' });
        last = cur;
      });

      ws.addEventListener('open', () => {
        // announce ourselves so server can map ws -> clientId
        ws.send(JSON.stringify({ type: 'join', clientId: clientId, name: nameEl.value || 'Anon' }));
      });

      ws.addEventListener('message', (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === 'stroke_start') {
            // treat as first point (from)
            // server will broadcast points as stroke_point as they arrive
            // nothing to do for start beyond ensuring a slot exists when redrawing
          } else if (m.type === 'stroke_point') {
            drawLine({ from: m.from, to: m.to, color: m.color, size: m.size, tool: m.tool });
          } else if (m.type === 'stroke_end') {
            // nothing required on end
          } else if (m.type === 'clear') {
            ctx.clearRect(0,0,canvas.width, canvas.height);
            // redraw from provided strokes if present
            if (Array.isArray(m.strokes)) {
              for (const s of m.strokes) {
                // draw stroke as sequence of points
                if (Array.isArray(s.points)) {
                  for (let i = 1; i < s.points.length; i++) {
                    drawLine({ from: s.points[i-1], to: s.points[i], color: s.color, size: s.size, tool: s.tool });
                  }
                }
              }
            }
          } else if (m.type === 'undo') {
            // server provides updated strokes array
            ctx.clearRect(0,0,canvas.width, canvas.height);
            if (Array.isArray(m.strokes)) {
              for (const s of m.strokes) {
                if (Array.isArray(s.points)) {
                  for (let i = 1; i < s.points.length; i++) {
                    drawLine({ from: s.points[i-1], to: s.points[i], color: s.color, size: s.size, tool: s.tool });
                  }
                }
              }
            }
          }
          else if (m.type === 'chat') appendMessage(m.name, m.text);
          else if (m.type === 'init') {
            // render strokes and chat history
            if (Array.isArray(m.strokes)) {
              for (const s of m.strokes) {
                if (Array.isArray(s.points)) {
                  for (let i = 1; i < s.points.length; i++) {
                    drawLine({ from: s.points[i-1], to: s.points[i], color: s.color, size: s.size, tool: s.tool });
                  }
                }
              }
            }
            if (Array.isArray(m.chat)) {
              for (const c of m.chat) appendMessage(c.name, c.text);
            }
          }
        } catch(e) { console.warn('bad msg', e); }
      });

      function drawLine(m) {
        // support eraser via globalCompositeOperation
        const isEraser = m.tool === 'eraser';
        ctx.save();
        if (isEraser) {
          ctx.globalCompositeOperation = 'destination-out';
          ctx.strokeStyle = 'rgba(0,0,0,1)';
        } else {
          ctx.globalCompositeOperation = 'source-over';
          ctx.strokeStyle = m.color || '#000';
        }
        ctx.lineWidth = m.size || 2;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(m.from.x, m.from.y);
        ctx.lineTo(m.to.x, m.to.y);
        ctx.stroke();
        ctx.restore();
      }

      function appendMessage(name, text) {
        const d = document.createElement('div');
        d.className = 'msg';
        d.textContent = name + ': ' + text;
        messagesEl.appendChild(d);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    </script>
  </body>
</html>
"""


# In-memory state: strokes and chat_history kept simple for Heroku single-dyno.
clients = set()
strokes = []
chat_history = []


@router.get("/whiteboard", response_class=HTMLResponse)
async def whiteboard_index():
    return HTMLResponse(INDEX_HTML)


@router.websocket("/whiteboard/ws")
async def websocket_endpoint(ws: WebSocket):
  await ws.accept()
  # send current state immediately
  try:
    init = {"type": "init", "strokes": strokes, "chat": chat_history}
    await ws.send_text(json.dumps(init))
  except Exception:
    pass

  clients.add(ws)

  # mapping from websocket -> clientId (for graceful disconnects)
  ws_client_map = {}

  try:
    while True:
      data = await ws.receive_text()
      try:
        msg = json.loads(data)
      except Exception:
        continue

      mtype = msg.get("type")

      if mtype == "join":
        cid = msg.get("clientId")
        if cid:
          ws_client_map[ws] = cid
        # no broadcast needed

      elif mtype == "stroke_start":
        # create a new stroke object
        stroke = {
          "id": msg.get("strokeId"),
          "clientId": msg.get("clientId"),
          "color": msg.get("color"),
          "size": msg.get("size"),
          "tool": msg.get("tool"),
          "points": [msg.get("from")] if msg.get("from") else []
        }
        strokes.append(stroke)
        # let others know a stroke started
        broadcast = {"type": "stroke_start", **stroke}
        for c in list(clients):
          if c is ws:
            continue
          try:
            await c.send_text(json.dumps(broadcast))
          except Exception:
            clients.discard(c)

      elif mtype == "stroke_point":
        sid = msg.get("strokeId")
        cid = msg.get("clientId")
        # find stroke by id
        found = None
        for s in reversed(strokes):
          if s.get("id") == sid and s.get("clientId") == cid:
            found = s
            break
        if found is None:
          # ignore if we don't know this stroke
          continue
        pt = msg.get("to")
        if pt:
          found.setdefault("points", []).append(pt)
          # broadcast point to others
          out = {"type": "stroke_point", "strokeId": sid, "clientId": cid, "from": msg.get("from"), "to": pt, "color": found.get("color"), "size": found.get("size"), "tool": found.get("tool")}
          for c in list(clients):
            if c is ws:
              continue
            try:
              await c.send_text(json.dumps(out))
            except Exception:
              clients.discard(c)

      elif mtype == "stroke_end":
        # nothing special server-side; stroke already stored
        pass

      elif mtype == "clear":
        # clear all strokes and broadcast the updated strokes array
        strokes.clear()
        broadcast = {"type": "clear", "strokes": strokes}
        for c in list(clients):
          try:
            await c.send_text(json.dumps(broadcast))
          except Exception:
            clients.discard(c)

      elif mtype == "undo":
        # undo only last stroke by this clientId
        target_cid = msg.get("clientId")
        if target_cid:
          # find last stroke owned by target_cid
          idx = None
          for i in range(len(strokes)-1, -1, -1):
            if strokes[i].get("clientId") == target_cid:
              idx = i
              break
          if idx is not None:
            strokes.pop(idx)
        else:
          # fallback: pop last stroke globally
          if strokes:
            strokes.pop()
        broadcast = {"type": "undo", "strokes": strokes}
        for c in list(clients):
          try:
            await c.send_text(json.dumps(broadcast))
          except Exception:
            clients.discard(c)

      elif mtype == "chat":
        entry = {"type": "chat", "clientId": msg.get("clientId"), "name": msg.get("name") or "Anon", "text": msg.get("text")}
        chat_history.append(entry)
        # bound history size to avoid memory growth
        if len(chat_history) > 500:
          chat_history.pop(0)
        # broadcast chat
        for c in list(clients):
          try:
            await c.send_text(json.dumps(entry))
          except Exception:
            clients.discard(c)

  except WebSocketDisconnect:
    # remove ws cleanly
    clients.discard(ws)
  except Exception:
    # ensure client is removed on unexpected errors
    clients.discard(ws)


def register_whiteboard(app):
    app.include_router(router)
