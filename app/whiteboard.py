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
        <button id="clear">Limpiar</button>
        <label>Color: <input id="color" type="color" value="#000000"></label>
        <label>Tamaño: <input id="size" type="range" min="1" max="20" value="2"></label>
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
      const canvas = document.getElementById('board');
      const ctx = canvas.getContext('2d');
      const colorEl = document.getElementById('color');
      const sizeEl = document.getElementById('size');
      const messagesEl = document.getElementById('messages');
      const nameEl = document.getElementById('name');
      const textEl = document.getElementById('text');
      document.getElementById('clear').addEventListener('click', () => {
        ctx.clearRect(0,0,canvas.width, canvas.height);
        ws.send(JSON.stringify({type:'clear'}));
      });

      document.getElementById('send').addEventListener('click', sendChat);
      textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });

      function sendChat() {
        const t = textEl.value && textEl.value.trim();
        if (!t) return;
        const payload = { type: 'chat', name: nameEl.value || 'Anon', text: t };
        ws.send(JSON.stringify(payload));
        textEl.value = '';
      }

      let drawing = false;
      let last = null;

      canvas.addEventListener('pointerdown', (e) => { drawing = true; last = {x: e.offsetX, y: e.offsetY}; });
      canvas.addEventListener('pointerup', (e) => { drawing = false; last = null; });
      canvas.addEventListener('pointermove', (e) => {
        if (!drawing) return;
        const cur = {x: e.offsetX, y: e.offsetY};
        const payload = {type:'stroke', from:last, to:cur, color: colorEl.value, size: parseInt(sizeEl.value,10)};
        ws.send(JSON.stringify(payload));
        drawLine(payload);
        last = cur;
      });

      ws.addEventListener('message', (ev) => {
        try {
          const m = JSON.parse(ev.data);
              if (m.type === 'stroke') drawLine(m);
              else if (m.type === 'clear') ctx.clearRect(0,0,canvas.width, canvas.height);
              else if (m.type === 'chat') appendMessage(m.name, m.text);
          else if (m.type === 'init') {
            // render strokes and chat history
            if (Array.isArray(m.strokes)) {
              for (const s of m.strokes) drawLine(s);
            }
            if (Array.isArray(m.chat)) {
              for (const c of m.chat) appendMessage(c.name, c.text);
            }
          }
        } catch(e) { console.warn('bad msg', e); }
      });

      function drawLine(m) {
        ctx.strokeStyle = m.color || '#000';
        ctx.lineWidth = m.size || 2;
        ctx.beginPath();
        ctx.moveTo(m.from.x, m.from.y);
        ctx.lineTo(m.to.x, m.to.y);
        ctx.stroke();
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
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                continue

            mtype = msg.get('type')
            if mtype == 'stroke':
                # persist stroke and broadcast
                strokes.append(msg)
                for c in list(clients):
                    try:
                        if c is not ws:
                            await c.send_text(json.dumps(msg))
                    except Exception:
                        try:
                            clients.remove(c)
                        except Exception:
                            pass
            elif mtype == 'clear':
                strokes.clear()
                for c in list(clients):
                    try:
                        await c.send_text(json.dumps(msg))
                    except Exception:
                        try:
                            clients.remove(c)
                        except Exception:
                            pass
            elif mtype == 'chat':
                entry = {'type': 'chat', 'name': msg.get('name') or 'Anon', 'text': msg.get('text')}
                chat_history.append(entry)
                # bound history size to avoid memory growth
                if len(chat_history) > 500:
                    chat_history.pop(0)
                # broadcast chat
                for c in list(clients):
                    try:
                        await c.send_text(json.dumps(entry))
                    except Exception:
                        try:
                            clients.remove(c)
                        except Exception:
                            pass
                pass
    except WebSocketDisconnect:
        try:
            clients.remove(ws)
        except Exception:
            pass


def register_whiteboard(app):
    app.include_router(router)
