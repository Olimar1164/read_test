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
  html,body { height:100%; margin:0; overflow:hidden; }
  body { font-family: Arial, sans-serif; display:flex; height:100%; overflow:hidden; }
  /* layout: left = board area, right = chat */
  /* allow the left pane to shrink when the right pane is present */
  #left { flex:1 1 auto; padding:8px; box-sizing:border-box; position: relative; min-width:0; }
    #board { background:#fff; border:1px solid #ccc; display:block; width:100%; height:auto; }
  #toolbar { padding:6px 0; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  /* Icon button styles for eraser and undo - uniform square */
  .icon-btn { display:inline-flex; align-items:center; justify-content:center; min-width:64px; height:36px; padding:6px 10px; margin:0; border:1px solid #bbb; background:#fff; border-radius:6px; cursor:pointer; box-sizing:border-box; font-family: inherit; font-size: 13px; }
  .icon-btn.active { background:#e6f7ff; border-color:#66b3ff; }
  /* preview cursor inside the board */
  /* boardWrap fills available left area while keeping a 4:3 aspect ratio */
  #boardWrap { position: relative; width:100%; aspect-ratio: 4/3; margin:8px 0; overflow:hidden; }
  #cursorPreview { position: absolute; pointer-events: none; border-radius: 50%; box-sizing: border-box; border: 2px solid rgba(0,0,0,0.6); background: rgba(255,255,255,0.0); transform: translate(-50%, -50%); display:none; }
      /* make the chat column fixed and not allowed to grow from long messages */
      #right { flex: 0 0 300px; width:300px; min-width:220px; max-width:360px; box-sizing:border-box; border-left:1px solid #eee; display:flex; flex-direction:column; }
      /* responsive adjustments */
      @media (max-width: 1400px) {
        #right { width:280px; }
      }
      @media (max-width: 1200px) {
        #right { width:260px; }
      }
      @media (max-width: 900px) {
        /* stack vertically on small screens: board first, chat below */
        body { flex-direction: column; }
        #right { width:100%; border-left:none; border-top:1px solid #eee; }
        #left { max-width:100%; }
        #boardWrap { width:100%; margin:6px 0; }
      }
      #messages { flex:1; overflow:auto; padding:8px; background:#fafafa; box-sizing:border-box; word-break: break-word; white-space: pre-wrap; }
      .msg { margin:6px 0; word-break: break-word; white-space: pre-wrap; }
      #composer { display:flex; padding:8px; border-top:1px solid #eee; box-sizing:border-box; }
      #composer input[type=text] { flex:1; padding:6px; margin-right:8px; min-width:0; box-sizing:border-box; }
      #composer input[name=name] { width:120px; flex: 0 0 120px; margin-right:8px; }
      #composer button { flex: 0 0 auto; }
      /* ensure meta text stays inline and doesn't push layout */
      #meta { font-size:12px; color:#333; margin-left:8px; white-space:nowrap; }
    </style>
  </head>
  <body>
    <div id="left">
  <div id="toolbar">
    <!-- Tools: only one active at a time -->
    <button id="undo" title="Deshacer (Ctrl+Z)" aria-label="Deshacer" class="icon-btn">Deshacer</button>
    <button id="toolPen" class="icon-btn active" title="Lapiz">Lapiz</button>
    <button id="toolEraser" class="icon-btn" title="Goma">Goma</button>
    <button id="toolBucket" class="icon-btn" title="Bote">Bote</button>
    <button id="toolHand" class="icon-btn" title="Mano">Mano</button>

    <label>Color: <input id="color" type="color" value="#000000"></label>
    <label>Tamaño: <input id="size" type="range" min="1" max="40" value="4"></label>

  <span id="meta">Resolución pizarra: 1200x900</span>
  </div>
      <div id="boardWrap" style="position:relative; border:1px solid #ddd; background:#fff;">
        <canvas id="board" width="1200" height="900" style="transform-origin: 0 0; width:100%; height:100%; display:block;"></canvas>
        <div id="cursorPreview"></div>
      </div>
  <!-- zoom controls moved to bottom-left of the canvas area -->
  <div id="zoomContainer" style="position:absolute; bottom:8px; left:8px; display:flex; align-items:center; gap:6px; background: rgba(255,255,255,0.9); padding:6px; border-radius:6px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);">
        <span>Zoom:</span>
        <button id="zoomOut" class="icon-btn" title="Alejar">-</button>
        <button id="zoomIn" class="icon-btn" title="Acercar">+</button>
        <span id="zoomPct" style="margin-left:6px; font-size:12px;">100%</span>
      </div>
    </div>
    <div id="right">
      <div id="presence" style="padding:8px; border-bottom:1px solid #eee; font-size:13px; overflow:hidden;">En línea: <span id="presenceCount">0</span></div>
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
  // zoom state: visual zoom per-client; logical drawing resolution remains canvas.width x canvas.height
  let zoom = 1.0;
  // pan offsets (pixels) — initialize before applyZoom to avoid ReferenceError
  let panX = 0, panY = 0;
      const ZOOM_STEP = 1.2; // multiplicative step
      const ZOOM_MIN = 0.5;
      const ZOOM_MAX = 3.0;
  const zoomPctEl = document.getElementById('zoomPct');
      const zoomInBtn = document.getElementById('zoomIn');
      const zoomOutBtn = document.getElementById('zoomOut');
  // boardWrap is used by coordinate/zoom helpers; declare early to avoid TDZ
  const boardWrap = document.getElementById('boardWrap');

      function applyZoom(centerLogicalX, centerLogicalY) {
        // apply CSS translate + scale to account for pan and zoom
        // clamp pan so canvas remains at least partially visible
        const rect = boardWrap.getBoundingClientRect();
        const maxPanX = rect.width; const maxPanY = rect.height;
        // compute visual size of canvas under current zoom
        const visW = canvas.width * zoom / (canvas.width / rect.width);
        const visH = canvas.height * zoom / (canvas.height / rect.height);
        // basic clamps (allow some movement but avoid moving fully out)
        panX = Math.min(Math.max(panX, rect.width - visW - 40), 40);
        panY = Math.min(Math.max(panY, rect.height - visH - 40), 40);
        canvas.style.transform = 'translate(' + panX + 'px, ' + panY + 'px) scale(' + zoom + ')';
        zoomPctEl.textContent = Math.round(zoom * 100) + '%';
        // optionally center view on a logical coordinate: compute pan so that logical point stays visually centered
        if (typeof centerLogicalX === 'number' && typeof centerLogicalY === 'number') {
          // center the logical point in the visible board area
          const visCx = rect.width / 2;
          const visCy = rect.height / 2;
          // compute where logical point maps to without pan
          const px = centerLogicalX * zoom;
          const py = centerLogicalY * zoom;
          // update pan so that px,py land at vis center
          panX = visCx - px;
          panY = visCy - py;
          // re-clamp after center calculation
          panX = Math.min(Math.max(panX, rect.width - (canvas.width*zoom*rect.width/canvas.width) - 40), 40);
          panY = Math.min(Math.max(panY, rect.height - (canvas.height*zoom*rect.height/canvas.height) - 40), 40);
          canvas.style.transform = 'translate(' + panX + 'px, ' + panY + 'px) scale(' + zoom + ')';
        }
      }

      zoomInBtn.addEventListener('click', () => {
        zoom = Math.min(ZOOM_MAX, zoom * ZOOM_STEP);
        if (zoom < 1) zoom = 1;
  // center on logical center so zoom doesn't shift focus unexpectedly
  applyZoom(canvas.width/2, canvas.height/2);
      });
      // zoom out resets to 100% (no zoom below 100%)
      zoomOutBtn.addEventListener('click', () => {
  zoom = 1.0;
  panX = 0; panY = 0;
  applyZoom();
      });
  // initialize at 100%
      zoom = 1.0;
      applyZoom();
      const colorEl = document.getElementById('color');
      const sizeEl = document.getElementById('size');
      const messagesEl = document.getElementById('messages');
      const nameEl = document.getElementById('name');
      const textEl = document.getElementById('text');
      // tool buttons
      const penBtn = document.getElementById('toolPen');
      const eraserBtn = document.getElementById('toolEraser');
      const bucketBtnEl = document.getElementById('toolBucket');
      const handBtn = document.getElementById('toolHand');
      const cursorPreview = document.getElementById('cursorPreview');

  // pan state for hand tool
  // panX/panY already declared above; ensure initialized
  panX = panX || 0; panY = panY || 0; // in pixels
      let isPanning = false;
      let panStart = null;
      let startPanX = 0, startPanY = 0;

      // active tool: 'pen'|'eraser'|'bucket'|'hand'
      let currentTool = 'pen';

      function setActiveButton(btn) {
        [penBtn, eraserBtn, bucketBtnEl, handBtn].forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
      }

      function selectTool(tool) {
        currentTool = tool;
        if (tool === 'pen') setActiveButton(penBtn);
        else if (tool === 'eraser') setActiveButton(eraserBtn);
        else if (tool === 'bucket') setActiveButton(bucketBtnEl);
        else if (tool === 'hand') setActiveButton(handBtn);
        // update cursor preview immediately
        updateCursorPreview(null);
      }

      penBtn.addEventListener('click', () => selectTool('pen'));
      eraserBtn.addEventListener('click', () => selectTool('eraser'));
      bucketBtnEl.addEventListener('click', () => selectTool('bucket'));
      handBtn.addEventListener('click', () => selectTool('hand'));
  // legacy sync for eraser checkbox removed (we use explicit tool buttons now)

      // helper: clear board and paint white background (canvas defaults to transparent)
      function clearBoard() {
        ctx.clearRect(0,0,canvas.width, canvas.height);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0,0,canvas.width, canvas.height);
      }

      // initialize canvas with white background so flood-fill works reliably
      clearBoard();

      // expose limpiar() for console use (no visible clear button in toolbar)
      window.limpiar = function() {
        clearBoard();
        try { ws.send(JSON.stringify({ type: 'clear', clientId: clientId })); } catch (e) { /* ignore if ws not ready */ }
      };

  // undo (button + Ctrl+Z)
      document.getElementById('undo').addEventListener('click', () => {
        ws.send(JSON.stringify({type:'undo', clientId: clientId}));
      });
  // clicking a tool selects it (bucket doesn't use legacy flags)
  bucketBtnEl.addEventListener('click', () => selectTool('bucket'));
      window.addEventListener('keydown', (e) => {
        const z = e.key === 'z' || e.key === 'Z';
        if ((e.ctrlKey || e.metaKey) && z) {
          e.preventDefault();
          ws.send(JSON.stringify({type:'undo', clientId: clientId}));
        }
      });

      // when user reloads/leaves the page (F5, close tab) try to finish any active stroke
      window.addEventListener('beforeunload', (ev) => {
        try {
          if (drawing && currentStrokeId) {
            ws.send(JSON.stringify({ type: 'stroke_end', clientId: clientId, strokeId: currentStrokeId }));
          }
        } catch (e) {}
        try { ws.close(); } catch (e) {}
        // allow default unload (no preventDefault)
      });

  document.getElementById('send').addEventListener('click', sendChat);
      textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });

  // persistent audio context
  let audioCtx = null;
      function ensureAudioCtx() {
        if (!audioCtx) {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (!Ctx) return null;
          audioCtx = new Ctx();
        }
        return audioCtx;
      }

      function sendChat() {
        const t = textEl.value && textEl.value.trim();
        if (!t) return;
        const payload = { type: 'chat', clientId: clientId, name: nameEl.value || 'Anon', text: t };
        ws.send(JSON.stringify(payload));
        // play local SNES-style coin sound when sending; intensity depends on message length
        try { playCoinSend(Math.min(1.5, Math.max(0.2, t.length / 18))); } catch (e) {}
        textEl.value = '';
      }

  let drawing = false;
  let last = null;
  let currentStrokeId = null;

      // helper: convert a pointer event to logical canvas coords (and return visual coords)
      function toLogical(e) {
        const rect = boardWrap.getBoundingClientRect();
        const xVis = e.clientX - rect.left;
        const yVis = e.clientY - rect.top;
        // account for CSS scaling of canvas (canvas.width may be larger than rect.width)
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        // map visual coords into canvas pixel coordinates, then undo pan/zoom
        const canvasX = xVis * scaleX;
        const canvasY = yVis * scaleY;
        // panX/Y are in CSS pixels; convert to canvas pixels before subtracting
        const panCanvasX = panX * scaleX;
        const panCanvasY = panY * scaleY;
        return { x: (canvasX - panCanvasX) / zoom, y: (canvasY - panCanvasY) / zoom, xVis, yVis };
      }

      canvas.addEventListener('pointerdown', (e) => {
        // if hand tool -> start panning
        if (currentTool === 'hand') {
          isPanning = true;
          panStart = { x: e.clientX, y: e.clientY };
          startPanX = panX; startPanY = panY;
          try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
          return;
        }
  // if bucket tool active, perform fill at this point
  if (currentTool === 'bucket') {
          const coords = toLogical(e);
          const logicalX = Math.round(coords.x);
          const logicalY = Math.round(coords.y);
          const pt = { x: logicalX, y: logicalY };
          const fillColor = colorEl.value;
          try { floodFill(canvas, pt.x|0, pt.y|0, fillColor); } catch (err) { console.warn('fill err', err); }
          ws.send(JSON.stringify({ type: 'fill', clientId: clientId, x: pt.x|0, y: pt.y|0, color: fillColor }));
          return;
        }
        drawing = true;
  const startCoords = toLogical(e);
  last = { x: startCoords.x, y: startCoords.y };
        currentStrokeId = clientId + '_' + Date.now();
        try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
        const start = {
          type: 'stroke_start',
          clientId: clientId,
          strokeId: currentStrokeId,
          color: colorEl.value,
          size: parseInt(sizeEl.value,10),
          tool: currentTool === 'eraser' ? 'eraser' : 'pen',
          from: { x: Math.round(last.x), y: Math.round(last.y) }
        };
        ws.send(JSON.stringify(start));
      });

      function endStroke(e) {
        if (isPanning) {
          isPanning = false;
          try { canvas.releasePointerCapture(e.pointerId); } catch (err) {}
          return;
        }
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
        // update cursor preview
        updateCursorPreview(e);
        if (isPanning) {
          // compute delta in client coords
          const dx = e.clientX - panStart.x;
          const dy = e.clientY - panStart.y;
          panX = startPanX + dx;
          panY = startPanY + dy;
          // clamp pan to keep canvas visible inside boardWrap
          try {
            const rect = boardWrap.getBoundingClientRect();
            const visW = rect.width; const visH = rect.height;
            panX = Math.min(Math.max(panX, visW - (canvas.width * zoom / (canvas.width / rect.width)) - 40), 40);
            panY = Math.min(Math.max(panY, visH - (canvas.height * zoom / (canvas.height / rect.height)) - 40), 40);
          } catch (err) {}
          // apply transform to canvas via CSS translate
          canvas.style.transform = 'translate(' + panX + 'px, ' + panY + 'px) scale(' + zoom + ')';
          return;
        }
        if (!drawing) return;
        // map pointer (viewport) coords to logical coords taking current pan and scale into account
        const cur = toLogical(e);
        const payload = {
          type: 'stroke_point',
          clientId: clientId,
          strokeId: currentStrokeId,
          from: { x: Math.round(last.x), y: Math.round(last.y) },
          to: { x: Math.round(cur.x), y: Math.round(cur.y) }
        };
        ws.send(JSON.stringify(payload));
  drawLine({ from: { x: last.x, y: last.y }, to: { x: cur.x, y: cur.y }, color: colorEl.value, size: parseInt(sizeEl.value,10), tool: currentTool === 'eraser' ? 'eraser' : 'pen' });
  last = { x: cur.x, y: cur.y };
      });

      // update cursor preview (position & size) — e can be null to hide/update size
      function updateCursorPreview(e) {
        // hide preview for hand tool (panning)
        if (currentTool === 'hand') { cursorPreview.style.display = 'none'; return; }
        const sz = parseInt(sizeEl.value, 10) * zoom;
        cursorPreview.style.width = sz + 'px';
        cursorPreview.style.height = sz + 'px';
        cursorPreview.style.borderColor = currentTool === 'eraser' ? 'rgba(200,20,20,0.9)' : 'rgba(0,0,0,0.6)';
        if (!e) { cursorPreview.style.display = 'none'; return; }
        // compute position inside boardWrap and account for canvas transform
  const coords = toLogical(e);
  // xVis/yVis are the visual coordinates where the logical point is drawn
  // if boardWrap rect is tiny for any reason, fallback to center
  const r = boardWrap.getBoundingClientRect();
  const left = (typeof coords.xVis === 'number') ? coords.xVis : (r.width/2 || 100);
  const top = (typeof coords.yVis === 'number') ? coords.yVis : (r.height/2 || 100);
  cursorPreview.style.left = left + 'px';
  cursorPreview.style.top = top + 'px';
        cursorPreview.style.display = 'block';
      }

      ws.addEventListener('open', () => {
        // announce ourselves so server can map ws -> clientId
    ws.send(JSON.stringify({ type: 'join', clientId: clientId, name: nameEl.value || 'Anon' }));
      });

      ws.addEventListener('message', (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.type === 'fill') {
            try { floodFill(canvas, m.x|0, m.y|0, m.color); } catch (err) { console.warn('remote fill err', err); }
            return;
          }
          if (m.type === 'presence') {
            try {
              const pEl = document.getElementById('presenceCount');
              if (pEl) pEl.textContent = String(m.count || 0);
            } catch (e) {}
            return;
          }
          if (m.type === 'stroke_start') {
            // treat as first point (from)
            // server will broadcast points as stroke_point as they arrive
            // nothing to do for start beyond ensuring a slot exists when redrawing
          } else if (m.type === 'stroke_point') {
            drawLine({ from: m.from, to: m.to, color: m.color, size: m.size, tool: m.tool });
          } else if (m.type === 'stroke_end') {
            // nothing required on end
          } else if (m.type === 'clear') {
            // clear and paint white background; replay actions if provided
            clearBoard();
            if (Array.isArray(m.actions)) {
              for (const a of m.actions) {
                if (a.type === 'stroke' && Array.isArray(a.obj.points)) {
                  for (let i = 1; i < a.obj.points.length; i++) {
                    drawLine({ from: a.obj.points[i-1], to: a.obj.points[i], color: a.obj.color, size: a.obj.size, tool: a.obj.tool });
                  }
                } else if (a.type === 'fill' && a.obj) {
                  try { floodFill(canvas, a.obj.x|0, a.obj.y|0, a.obj.color); } catch (err) { console.warn('replay fill err', err); }
                }
              }
            } else {
              // fallback to strokes/fills arrays
              if (Array.isArray(m.strokes)) {
                for (const s of m.strokes) {
                  if (Array.isArray(s.points)) {
                    for (let i = 1; i < s.points.length; i++) {
                      drawLine({ from: s.points[i-1], to: s.points[i], color: s.color, size: s.size, tool: s.tool });
                    }
                  }
                }
              }
              if (Array.isArray(m.fills)) {
                for (const f of m.fills) {
                  try { floodFill(canvas, f.x|0, f.y|0, f.color); } catch (err) { console.warn('replay fill err', err); }
                }
              }
            }
          } else if (m.type === 'undo') {
            // server provides updated actions/strokes/fills; prefer actions for exact chronological replay
            clearBoard();
            if (Array.isArray(m.actions)) {
              for (const a of m.actions) {
                if (a.type === 'stroke' && Array.isArray(a.obj.points)) {
                  for (let i = 1; i < a.obj.points.length; i++) {
                    drawLine({ from: a.obj.points[i-1], to: a.obj.points[i], color: a.obj.color, size: a.obj.size, tool: a.obj.tool });
                  }
                } else if (a.type === 'fill' && a.obj) {
                  try { floodFill(canvas, a.obj.x|0, a.obj.y|0, a.obj.color); } catch (err) { console.warn('replay fill err', err); }
                }
              }
            } else {
              if (Array.isArray(m.strokes)) {
                for (const s of m.strokes) {
                  if (Array.isArray(s.points)) {
                    for (let i = 1; i < s.points.length; i++) {
                      drawLine({ from: s.points[i-1], to: s.points[i], color: s.color, size: s.size, tool: s.tool });
                    }
                  }
                }
              }
              if (Array.isArray(m.fills)) {
                for (const f of m.fills) {
                  try { floodFill(canvas, f.x|0, f.y|0, f.color); } catch (err) { console.warn('replay fill err', err); }
                }
              }
            }
          }
          else if (m.type === 'chat') appendMessage(m.name, m.text, m.clientId);
          else if (m.type === 'init') {
            // render strokes and chat history
            // ensure background is white first
            clearBoard();
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
            // replay any fills recorded on server
            if (Array.isArray(m.fills)) {
              for (const f of m.fills) {
                try { floodFill(canvas, f.x|0, f.y|0, f.color); } catch (err) { console.warn('init fill err', err); }
              }
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

      // improved flood fill (scanline) with color tolerance to handle anti-aliased edges
      function floodFill(canvasEl, startX, startY, fillColor, tolerance = 50) {
        // robust scanline flood-fill that compares against the original pixels
        const ctxLocal = canvasEl.getContext('2d');
        const w = canvasEl.width;
        const h = canvasEl.height;
        if (startX < 0 || startY < 0 || startX >= w || startY >= h) return;
        const img = ctxLocal.getImageData(0, 0, w, h);
        const data = img.data;
        // copy original pixels for reference comparisons (so writing doesn't affect checks)
        const orig = new Uint8ClampedArray(data);

        // treat fully transparent pixels as white (so eraser cuts become white areas for fill)
        function getColorAt(off) {
          const a = orig[off+3];
          if (a === 0) return [255,255,255,255];
          return [orig[off], orig[off+1], orig[off+2], a];
        }

        function colorToRgba(hex) {
          const v = hex.replace('#','');
          const r = parseInt(v.substring(0,2),16);
          const g = parseInt(v.substring(2,4),16);
          const b = parseInt(v.substring(4,6),16);
          return [r,g,b,255];
        }

        function colorDistSquared(a, b) {
          const dr = a[0] - b[0];
          const dg = a[1] - b[1];
          const db = a[2] - b[2];
          return dr*dr + dg*dg + db*db;
        }

        // compute target color as the average of a 3x3 neighborhood around start to be tolerant of anti-aliasing
    const samples = [];
        for (let yy = Math.max(0, startY-1); yy <= Math.min(h-1, startY+1); yy++) {
          for (let xx = Math.max(0, startX-1); xx <= Math.min(w-1, startX+1); xx++) {
      const off = (yy * w + xx) * 4;
      samples.push(getColorAt(off));
          }
        }
        const targetColor = samples.reduce((acc, s) => { acc[0]+=s[0]; acc[1]+=s[1]; acc[2]+=s[2]; return acc; }, [0,0,0]);
        targetColor[0] = Math.round(targetColor[0] / samples.length);
        targetColor[1] = Math.round(targetColor[1] / samples.length);
        targetColor[2] = Math.round(targetColor[2] / samples.length);
        targetColor[3] = 255;

        const replaceColor = colorToRgba(fillColor);
        const tolSq = tolerance * tolerance;
        if (colorDistSquared(targetColor, replaceColor) <= tolSq) return;

        // visited map to avoid reprocessing
        const visited = new Uint8Array(w * h);
        const stack = [[startX, startY]];
        while (stack.length) {
          const [x, y] = stack.pop();
          let nx = x;
          // move left while matching target in orig
          while (nx >= 0) {
            const off = (y * w + nx) * 4;
            const col = getColorAt(off);
            if (colorDistSquared(col, targetColor) <= tolSq) nx--; else break;
          }
          nx++;
          let spanUp = false, spanDown = false;
          while (nx < w) {
            const off = (y * w + nx) * 4;
            const origCol = getColorAt(off);
            if (colorDistSquared(origCol, targetColor) > tolSq) break;
            const idx = y * w + nx;
            if (!visited[idx]) {
              visited[idx] = 1;
              // set color into data (this writes the replacement)
              data[off] = replaceColor[0]; data[off+1] = replaceColor[1]; data[off+2] = replaceColor[2]; data[off+3] = replaceColor[3];
              // queue above
              if (!spanUp && y > 0) {
                const offUp = ((y-1) * w + nx) * 4;
                const colUp = getColorAt(offUp);
                if (colorDistSquared(colUp, targetColor) <= tolSq) { stack.push([nx, y-1]); spanUp = true; }
              } else if (spanUp && y > 0) {
                const offUp = ((y-1) * w + nx) * 4;
                const colUp = getColorAt(offUp);
                if (colorDistSquared(colUp, targetColor) > tolSq) spanUp = false;
              }
              // queue below
              if (!spanDown && y < h-1) {
                const offDown = ((y+1) * w + nx) * 4;
                const colDown = getColorAt(offDown);
                if (colorDistSquared(colDown, targetColor) <= tolSq) { stack.push([nx, y+1]); spanDown = true; }
              } else if (spanDown && y < h-1) {
                const offDown = ((y+1) * w + nx) * 4;
                const colDown = getColorAt(offDown);
                if (colorDistSquared(colDown, targetColor) > tolSq) spanDown = false;
              }
            }
            nx++;
          }
        }
        ctxLocal.putImageData(img, 0, 0);
      }

    function appendMessage(name, text, fromClientId) {
        const d = document.createElement('div');
        d.className = 'msg';
        d.textContent = name + ': ' + text;
        messagesEl.appendChild(d);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        // play coin sound when message comes from other clients (softer)
        try {
      if (fromClientId && fromClientId !== clientId) playCoinReceive(Math.min(1.0, Math.max(0.1, text.length / 22)));
        } catch (e) {}
      }

      // persistent, scheduled coin sounds that accept intensity (0..1.5)
      function playCoinReceive(intensity = 1.0) {
        const ctx = ensureAudioCtx();
        if (!ctx) return;
        const doPlay = () => {
          const now = ctx.currentTime;
          const dur = 0.18;
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          const filt = ctx.createBiquadFilter();
          osc.type = 'triangle';
          filt.type = 'bandpass';
          filt.frequency.value = 1000;
          filt.Q.value = 1.2;
          osc.frequency.setValueAtTime(920, now);
          osc.frequency.exponentialRampToValueAtTime(520, now + dur);
          gain.gain.setValueAtTime(0.0001, now);
          gain.gain.exponentialRampToValueAtTime(0.28 * intensity, now + 0.01);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);
          osc.connect(filt);
          filt.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now);
          osc.stop(now + dur + 0.02);
        };
        // resume if suspended (some browsers require user interaction first)
        if (ctx.state === 'suspended') {
          ctx.resume().then(doPlay).catch(() => doPlay());
        } else {
          doPlay();
        }
      }

      function playCoinSend(intensity = 1.0) {
        const ctx = ensureAudioCtx();
        if (!ctx) return;
        const doPlay = () => {
          const now = ctx.currentTime;
          const notes = [1568.0, 2093.0, 2637.0];
          const step = 0.05;
          const total = notes.length * step;
          const oscA = ctx.createOscillator();
          const oscB = ctx.createOscillator();
          const gain = ctx.createGain();
          const filt = ctx.createBiquadFilter();
          oscA.type = 'square';
          oscB.type = 'square';
          filt.type = 'bandpass'; filt.frequency.value = 1400; filt.Q.value = 1.6;
          gain.gain.setValueAtTime(0.0001, now);
          gain.gain.linearRampToValueAtTime(0.95 * intensity, now + 0.005);
          gain.gain.exponentialRampToValueAtTime(0.001, now + total + 0.05);
          oscA.connect(filt);
          oscB.connect(filt);
          filt.connect(gain);
          gain.connect(ctx.destination);
          oscA.start(now);
          oscB.start(now);
          for (let i = 0; i < notes.length; i++) {
            const t = now + i * step;
            oscA.frequency.setValueAtTime(notes[i], t);
            oscB.frequency.setValueAtTime(notes[i] * 1.005, t);
          }
          const stopTime = now + total + 0.02;
          oscA.stop(stopTime);
          oscB.stop(stopTime);
        };
        if (ctx.state === 'suspended') {
          ctx.resume().then(doPlay).catch(() => doPlay());
        } else {
          doPlay();
        }
      }
    </script>
  </body>
</html>
"""


# In-memory state: strokes and chat_history kept simple for Heroku single-dyno.
clients = set()
strokes = []
chat_history = []
fills = []
actions = []


@router.get("/whiteboard", response_class=HTMLResponse)
async def whiteboard_index():
    return HTMLResponse(INDEX_HTML)


@router.websocket("/whiteboard/ws")
async def websocket_endpoint(ws: WebSocket):
  await ws.accept()
  # send current state immediately
  try:
    init = {"type": "init", "strokes": strokes, "chat": chat_history, "fills": fills, "actions": actions, "presence": {"count": len(clients) + 1}}
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
        # broadcast presence (new connection was already added to clients set earlier)
        try:
          broadcast = {"type": "presence", "count": len(clients)}
          for c in list(clients):
            await c.send_text(json.dumps(broadcast))
        except Exception:
          pass

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
        # record action in timeline so undo can be chronological and precise
        actions.append({"type": "stroke", "obj": stroke})
        # let others know a stroke started
        broadcast = {"type": "stroke_start", **stroke}
        for c in list(clients):
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
        fills.clear()
        actions.clear()
        broadcast = {"type": "clear", "actions": actions, "strokes": strokes, "fills": fills}
        for c in list(clients):
          try:
            await c.send_text(json.dumps(broadcast))
          except Exception:
            clients.discard(c)

      elif mtype == "fill":
        # record fill and broadcast so others replicate; also append to actions timeline
        fill = {"x": int(msg.get("x")), "y": int(msg.get("y")), "color": msg.get("color"), "clientId": msg.get("clientId")}
        fills.append(fill)
        actions.append({"type": "fill", "obj": fill})
        out = {"type": "fill", **fill}
        for c in list(clients):
          try:
            await c.send_text(json.dumps(out))
          except Exception:
            clients.discard(c)

      elif mtype == "undo":
        # undo only last action by this clientId
        target_cid = msg.get("clientId")
        if target_cid:
          # find last action owned by target_cid in actions timeline
          aidx = None
          for i in range(len(actions)-1, -1, -1):
            obj = actions[i].get('obj')
            if obj and obj.get('clientId') == target_cid:
              aidx = i
              break
          if aidx is not None:
            actions.pop(aidx)
            # rebuild strokes and fills from actions to preserve chronological order
            strokes.clear()
            fills.clear()
            for a in actions:
              if a.get('type') == 'stroke':
                strokes.append(a.get('obj'))
              elif a.get('type') == 'fill':
                fills.append(a.get('obj'))
        else:
          # fallback: pop last action globally
          if actions:
            actions.pop()
            strokes.clear()
            fills.clear()
            for a in actions:
              if a.get('type') == 'stroke':
                strokes.append(a.get('obj'))
              elif a.get('type') == 'fill':
                fills.append(a.get('obj'))
        broadcast = {"type": "undo", "actions": actions, "strokes": strokes, "fills": fills}
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
      # broadcast updated presence
      try:
        broadcast = {"type": "presence", "count": len(clients)}
        for c in list(clients):
          try:
            await c.send_text(json.dumps(broadcast))
          except Exception:
            clients.discard(c)
      except Exception:
        pass
  except Exception:
    # ensure client is removed on unexpected errors
    clients.discard(ws)


def register_whiteboard(app):
    app.include_router(router)
