Demo Lector de archivos (Proyecto de ejemplo)

Pequeña aplicación en FastAPI que demuestra cómo:

- Subir un archivo al endpoint de Files de SAIA (Console API).
- Enviar una consulta al chat de SAIA que referencia el archivo subido.

El objetivo es mantener la demostración simple y reproducible en Heroku.

Contenido
---------

- `app/api/endpoints.py`: endpoints para subir archivos y orquestar la llamada al chat.
- `app/services/ai/saia_console_client.py`: cliente ligero que sube archivos y llama al chat (incluye reintentos frente a la condición 8024).
- `app/main.py`: aplicación FastAPI y configuración de arranque.

Requisitos
----------

- Python 3.10+
- Virtualenv (recomendado)
- Variables de entorno: `GEAI_API_TOKEN`, `ORGANIZATION_ID`, `PROJECT_ID`, `ASSISTANT_ID`

Instalación y ejecución local
----------------------------

1. Crear y activar entorno virtual

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar dependencias

```bash
pip install -r requirements.txt
```

3. Configurar variables de entorno

Crear un archivo `.env` con las variables necesarias o exportarlas en tu shell:

```bash
export GEAI_API_TOKEN="tu_token"
export ORGANIZATION_ID="tu_org"
export PROJECT_ID="tu_proyecto"
export ASSISTANT_ID="tu_assistant"
```

4. Ejecutar la aplicación en desarrollo

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Uso (demo)
-----------

- Abre `http://localhost:8000/`.
- Sube un archivo (PDF/PNG/JPG/CSV/TXT) de hasta ~800 KB.
- La aplicación subirá el archivo a SAIA y enviará un prompt que lo referencia. La respuesta del asistente se muestra en la UI.

Notas técnicas y consideraciones
--------------------------------

- El cliente `SAIAConsoleClient` implementa:
	- subida por archivo y por bytes en memoria,
	- reintentos cortos frente a errores de ingestión (8024),
	- caché en memoria por hash para evitar re-subidas inmediatas.
- Diseño para Heroku:
	- la app evita usar almacenamiento persistente localmente cuando es posible (usa la ruta en memoria). Si tu flujo requiere persistencia, añade Redis o una base de datos externa.
	- limita el tamaño de los uploads para evitar bloqueos por tiempo de respuesta.

Despliegue en Heroku (sencillo)
--------------------------------

1. Crear app y configurar variables (ejemplo):

```bash
heroku create mi-demo-saia
heroku config:set GEAI_API_TOKEN="..." ORGANIZATION_ID="..." PROJECT_ID="..." ASSISTANT_ID="..."
git push heroku HEAD:main
heroku ps:scale web=1
```

2. Abrir la app y probar el flujo de subida y chat.

Limitaciones
------------

- El estado en memoria (si lo hay) no sobrevive reinicios de dynos en Heroku.
- Para producción se recomienda introducir persistencia y manejo de concurrencia más robusto.
