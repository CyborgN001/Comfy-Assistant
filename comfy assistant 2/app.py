
import os
import json
import time
import copy
import secrets
import logging
import threading
import uuid

from pathlib import Path
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response
)

from werkzeug.utils import secure_filename


# =========================================================
# COMFYUI ASSISTANT V3.6
# Z-IMAGE, UPSCALING, GALERIE UND FORTSCHRITT
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

WORKFLOW_DIR = BASE_DIR / "workflows"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"

Z_IMAGE_FILE = WORKFLOW_DIR / "z_image_api.json"
UPSCALER_FILE = WORKFLOW_DIR / "upscaler_api.json"
GALLERY_FILE = BASE_DIR / "gallery.json"


# =========================================================
# KONFIGURATION
# =========================================================

SERVER_URL = os.environ.get(
    "COMFYUI_URL",
    "http://127.0.0.1:8188"
).rstrip("/")

MAX_WAIT = 900
REQUEST_TIMEOUT = 60

MAX_UPLOAD_SIZE = 50 * 1024 * 1024
MAX_BATCH_IMAGES = 20

MAX_SCALE_FACTOR = 8.0

ALLOWED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp"
}


# =========================================================
# STANDARDMODELLE
# =========================================================

DEFAULT_UNET = "unstableRevolution_V3Fp16.safetensors"

DEFAULT_CLIP = "qwen_3_4b.safetensors"

DEFAULT_VAE = "ultrafluxVAEImproved_v10.safetensors"


CLIENT_ID = "comfyui-assistant-3.6"


# =========================================================
# FLASK
# =========================================================

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR)
)

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

app.json.sort_keys = False


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("ComfyUI Assistant")


# =========================================================
# JOB-MANAGEMENT
# =========================================================

JOBS = {}

JOBS_LOCK = threading.Lock()


# =========================================================
# VERZEICHNISSE ERSTELLEN
# =========================================================

for directory in (
    WORKFLOW_DIR,
    TEMPLATE_DIR,
    STATIC_DIR,
    UPLOAD_DIR
):
    directory.mkdir(
        parents=True,
        exist_ok=True
    )

# =========================================================
# JOB-MANAGEMENT FUNKTIONEN
# =========================================================

def save_job(prompt_id, kind, seed=None, prompt="", client_id=None):

    with JOBS_LOCK:

        JOBS[prompt_id] = {
            "kind": kind,
            "seed": seed,
            "started": time.time(),
            "finished": False,
            "error": None,
            "prompt": prompt,
            "requested_images": 1,
            "client_id": client_id,
            "progress": None,
            "progress_value": 0,
            "progress_max": 0,
            "progress_node": None,
            "progress_message": "Auftrag an ComfyUI übergeben."
        }


def get_local_job(prompt_id):

    with JOBS_LOCK:

        job = JOBS.get(prompt_id)

        return copy.deepcopy(job) if job else None


def update_job(prompt_id, **values):

    with JOBS_LOCK:

        if prompt_id in JOBS:

            JOBS[prompt_id].update(values)


# =========================================================
# DATEINAMEN BEREINIGEN
# =========================================================

def clean_filename(value, default="ComfyUI"):

    value = secure_filename(
        str(value or "")
    )

    value = value[:100]

    return value or default


# =========================================================
# DATEIFORMAT PRÜFEN
# =========================================================

def allowed_file(filename):

    if not filename:
        return False

    extension = Path(filename).suffix.lower()

    return extension in ALLOWED_EXTENSIONS


# =========================================================
# JSON DATEIEN LESEN
# =========================================================

def read_json_file(path):

    if not path.exists():

        raise FileNotFoundError(
            f"Datei nicht gefunden: {path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# =========================================================
# JSON DATEIEN SCHREIBEN
# =========================================================

def write_json_file(path, data):

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    temporary.replace(path)


# =========================================================
# COMFYUI SERVER
# =========================================================

def get_comfy_url():

    return SERVER_URL.rstrip("/")


# =========================================================
# JSON FEHLERANTWORT
# =========================================================

def json_error(message, status=400):

    return jsonify({
        "success": False,
        "error": str(message)
    }), status


# =========================================================
# COMFYUI REQUEST
# =========================================================

def comfy_request(method, endpoint, **kwargs):

    url = get_comfy_url() + endpoint

    timeout = kwargs.pop(
        "timeout",
        REQUEST_TIMEOUT
    )

    try:

        response = requests.request(
            method,
            url,
            timeout=timeout,
            **kwargs
        )

        response.raise_for_status()

        return response

    except requests.Timeout:

        raise RuntimeError(
            "Zeitüberschreitung bei ComfyUI."
        )

    except requests.ConnectionError:

        raise RuntimeError(
            f"ComfyUI ist nicht erreichbar: {get_comfy_url()}"
        )

    except requests.HTTPError as error:

        details = (
            error.response.text[:2000]
            if error.response is not None
            else ""
        )

        raise RuntimeError(
            f"ComfyUI HTTP-Fehler: {error}. {details}"
        )


# =========================================================
# COMFYUI JSON REQUEST
# =========================================================

def comfy_json(method, endpoint, **kwargs):

    response = comfy_request(
        method,
        endpoint,
        **kwargs
    )

    try:

        return response.json()

    except ValueError:

        raise RuntimeError(
            "ComfyUI hat keine gültige JSON-Antwort geliefert."
        )


# =========================================================
# VERBINDUNG PRÜFEN
# =========================================================

def check_connection():

    try:

        requests.get(
            get_comfy_url() + "/system_stats",
            timeout=5
        ).raise_for_status()

        return True

    except requests.RequestException:

        return False

# =========================================================
# WORKFLOW VALIDIEREN
# =========================================================

def validate_workflow(workflow, name="Workflow"):

    if not isinstance(workflow, dict):

        raise ValueError(
            f"{name}: Ungültiges Format."
        )

    if not workflow:

        raise ValueError(
            f"{name}: Workflow ist leer."
        )

    if "nodes" in workflow or "links" in workflow:

        raise ValueError(
            f"{name}: Es wird ein API-Workflow benötigt, "
            "kein normaler ComfyUI-Workflow-Export."
        )

    errors = []

    for node_id, node in workflow.items():

        if not isinstance(node, dict):

            errors.append(
                f"Node {node_id} ungültig."
            )

            continue

        if not node.get("class_type"):

            errors.append(
                f"Node {node_id} besitzt keinen class_type."
            )

        if not isinstance(node.get("inputs"), dict):

            errors.append(
                f"Node {node_id} besitzt ungültige inputs."
            )

    if errors:

        raise ValueError(
            f"{name}: " + " ".join(errors)
        )

    return True


# =========================================================
# WORKFLOW LADEN
# =========================================================

def load_workflow(path):

    workflow = read_json_file(path)

    validate_workflow(
        workflow,
        path.name
    )

    return copy.deepcopy(workflow)


# =========================================================
# Z-IMAGE WORKFLOW PRÜFEN
# =========================================================

def validate_z_image_workflow():

    workflow = load_workflow(
        Z_IMAGE_FILE
    )

    required_nodes = {
        "9": "SaveImage",
        "62": "CLIPLoader",
        "63": "VAELoader",
        "66": "UNETLoader",
        "67": "CLIPTextEncode",
        "68": "EmptySD3LatentImage",
        "69": "ModelSamplingAuraFlow",
        "70": "KSampler",
        "71": "CLIPTextEncode"
    }

    for node_id, expected_type in required_nodes.items():

        node = workflow.get(node_id)

        if not node:

            raise ValueError(
                f"Z-Image Node {node_id} fehlt."
            )

        actual_type = node.get("class_type")

        if actual_type != expected_type:

            raise ValueError(
                f"Z-Image Node {node_id}: "
                f"Erwartet {expected_type}, "
                f"gefunden {actual_type}."
            )

    return workflow


# =========================================================
# UPSCALER WORKFLOW PRÜFEN
# =========================================================

def validate_upscaler_workflow():

    workflow = load_workflow(UPSCALER_FILE)

    required_nodes = {
        "2": "LoadImage",
        "3": "ImageScaleBy",
        "5": "SaveImage"
    }

    for node_id, expected_type in required_nodes.items():

        node = workflow.get(node_id)

        if not node or node.get("class_type") != expected_type:

            raise ValueError(
                f"Upscaler Node {node_id} fehlt oder besitzt den falschen Typ."
            )

    return workflow


# =========================================================
# MODELLOPTIONEN AUS COMFYUI AUSLESEN
# =========================================================

def extract_node_options(node_info, input_name):

    try:

        inputs = node_info.get(
            "input",
            {}
        )

        for section in (
            "required",
            "optional"
        ):

            definition = inputs.get(
                section,
                {}
            ).get(input_name)

            if (
                isinstance(definition, list)
                and definition
                and isinstance(definition[0], list)
            ):

                return definition[0]

    except (
        AttributeError,
        TypeError,
        IndexError
    ):

        pass

    return []


# =========================================================
# VERFÜGBARE MODELLE ABRUFEN
# =========================================================

def get_comfy_object_info():

    return comfy_json(
        "GET",
        "/object_info",
        timeout=60
    )


def get_available_models():

    object_info = get_comfy_object_info()

    return {

        "unet": extract_node_options(
            object_info.get("UNETLoader", {}),
            "unet_name"
        ),

        "clip": extract_node_options(
            object_info.get("CLIPLoader", {}),
            "clip_name"
        ),

        "vae": extract_node_options(
            object_info.get("VAELoader", {}),
            "vae_name"
        )

    }


def get_available_upscale_models():

    object_info = get_comfy_object_info()

    return extract_node_options(
        object_info.get("UpscaleModelLoader", {}),
        "model_name"
    )


# =========================================================
# MODELLAUSWAHL PRÜFEN
# =========================================================

def validate_model_selection(
    selected,
    available,
    label
):

    if selected is None or selected == "":

        return

    if selected not in available:

        raise ValueError(
            f"Das ausgewählte {label} ist nicht verfügbar: "
            f"{selected}"
        )

# =========================================================
# Z-IMAGE WORKFLOW VORBEREITEN
# =========================================================

def prepare_z_image(data):

    workflow = copy.deepcopy(
        validate_z_image_workflow()
    )

    positive = str(
        data.get("positive_prompt", "")
    ).strip()

    negative = str(
        data.get("negative_prompt", "")
    )

    if not positive:

        raise ValueError(
            "Bitte einen positiven Prompt eingeben."
        )

    # -----------------------------------------------------
    # BILDGRÖSSE
    # -----------------------------------------------------

    width = int(
        data.get("width", 832)
    )

    height = int(
        data.get("height", 1216)
    )

    if not 64 <= width <= 4096:

        raise ValueError(
            "Ungültige Bildbreite."
        )

    if not 64 <= height <= 4096:

        raise ValueError(
            "Ungültige Bildhöhe."
        )

    if width % 8 or height % 8:

        raise ValueError(
            "Breite und Höhe müssen durch 8 teilbar sein."
        )

    # -----------------------------------------------------
    # SAMPLING
    # -----------------------------------------------------

    steps = int(
        data.get("steps", 10)
    )

    cfg = float(
        data.get("cfg", 1)
    )

    if not 1 <= steps <= 150:

        raise ValueError(
            "Ungültige Step-Anzahl."
        )

    if not 0 <= cfg <= 30:

        raise ValueError(
            "Ungültiger CFG-Wert."
        )

    # -----------------------------------------------------
    # SEED
    # -----------------------------------------------------

    random_seed = data.get(
        "random_seed",
        True
    )

    if isinstance(random_seed, str):

        random_seed = random_seed.lower() in (
            "true",
            "1",
            "yes"
        )

    if random_seed:

        seed = secrets.randbelow(
            2**64
        )

    else:

        seed = int(
            data.get("seed", 0)
        )

        if not 0 <= seed <= 2**64 - 1:

            raise ValueError(
                "Ungültiger Seed."
            )

    # -----------------------------------------------------
    # DATEINAME
    # -----------------------------------------------------

    filename_prefix = clean_filename(
        data.get("filename_prefix"),
        "ZImage"
    )

    # -----------------------------------------------------
    # MODELLE AUS COMFYUI LADEN
    # -----------------------------------------------------

    models = get_available_models()

    selected_unet = data.get("unet_model")
    selected_clip = data.get("clip_model")
    selected_vae = data.get("vae_model")

    validate_model_selection(
        selected_unet,
        models["unet"],
        "UNET-Modell"
    )

    validate_model_selection(
        selected_clip,
        models["clip"],
        "CLIP-Modell"
    )

    validate_model_selection(
        selected_vae,
        models["vae"],
        "VAE-Modell"
    )

    # -----------------------------------------------------
    # PROMPTS
    # -----------------------------------------------------

    workflow["67"]["inputs"]["text"] = positive

    workflow["71"]["inputs"]["text"] = negative

    # -----------------------------------------------------
    # BILDGRÖSSE
    # -----------------------------------------------------

    workflow["68"]["inputs"]["width"] = width

    workflow["68"]["inputs"]["height"] = height

    # -----------------------------------------------------
    # SAMPLING
    # -----------------------------------------------------

    workflow["70"]["inputs"]["steps"] = steps

    workflow["70"]["inputs"]["cfg"] = cfg

    workflow["70"]["inputs"]["seed"] = seed

    # -----------------------------------------------------
    # DATEINAME
    # -----------------------------------------------------

    workflow["9"]["inputs"]["filename_prefix"] = (
        filename_prefix
    )

    # -----------------------------------------------------
    # MODELLAUSWAHL
    # -----------------------------------------------------

    if selected_unet:

        workflow["66"]["inputs"]["unet_name"] = (
            selected_unet
        )

    if selected_clip:

        workflow["62"]["inputs"]["clip_name"] = (
            selected_clip
        )

    if selected_vae:

        workflow["63"]["inputs"]["vae_name"] = (
            selected_vae
        )

    return workflow, seed


# =========================================================
# WORKFLOW AN COMFYUI ÜBERGEBEN
# =========================================================

def start_progress_monitor(prompt_id, client_id, websocket_connection=None):

    def monitor():

        ws = websocket_connection
        started = time.time()

        try:

            import websocket

            if ws is None:

                base = get_comfy_url()
                ws_base = base.replace("https://", "wss://", 1).replace(
                    "http://", "ws://", 1
                )
                ws_url = (
                    ws_base
                    + "/ws?"
                    + urlencode({"clientId": client_id})
                )

                ws = websocket.create_connection(
                    ws_url,
                    timeout=5,
                    enable_multithread=True
                )

            ws.settimeout(2)

            while time.time() - started < MAX_WAIT:

                job = get_local_job(prompt_id)

                if not job or job.get("finished") or job.get("error"):
                    break

                try:
                    raw_message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue

                if not raw_message:
                    continue

                try:
                    message = json.loads(raw_message)
                except (TypeError, ValueError):
                    continue

                event_type = message.get("type")
                event_data = message.get("data") or {}

                if not isinstance(event_data, dict):
                    continue

                event_prompt_id = event_data.get("prompt_id")
                if event_prompt_id and event_prompt_id != prompt_id:
                    continue

                if event_type == "progress":

                    value = event_data.get("value")
                    maximum = event_data.get("max")

                    try:
                        value = float(value)
                        maximum = float(maximum)
                    except (TypeError, ValueError):
                        continue

                    if maximum <= 0:
                        continue

                    percent = max(0.0, min(99.5, value / maximum * 100.0))
                    node_id = event_data.get("node")

                    update_job(
                        prompt_id,
                        progress=round(percent, 1),
                        progress_value=value,
                        progress_max=maximum,
                        progress_node=str(node_id) if node_id is not None else None,
                        progress_message=f"ComfyUI-Schritt {value:g} von {maximum:g}"
                    )

                elif event_type == "execution_start":

                    update_job(
                        prompt_id,
                        progress_message="ComfyUI hat die Verarbeitung gestartet."
                    )

                elif event_type == "executing":

                    node_id = event_data.get("node")

                    if node_id is None:
                        update_job(
                            prompt_id,
                            progress_message="Verarbeitung abgeschlossen; Ergebnis wird gespeichert."
                        )
                    else:
                        update_job(
                            prompt_id,
                            progress_node=str(node_id),
                            progress_message=f"ComfyUI verarbeitet Node {node_id}."
                        )

                elif event_type in ("execution_error", "execution_interrupted"):

                    update_job(
                        prompt_id,
                        error=str(event_data),
                        progress_message="ComfyUI hat den Auftrag abgebrochen oder einen Ausführungsfehler gemeldet."
                    )
                    break

        except Exception as error:

            logger.info(
                "Live-Fortschritt für %s nicht verfügbar: %s",
                prompt_id,
                error
            )

        finally:

            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    thread = threading.Thread(
        target=monitor,
        name=f"comfy-progress-{prompt_id[:8]}",
        daemon=True
    )
    thread.start()


def queue_workflow(
    workflow,
    kind,
    seed=None,
    prompt=""
):

    client_id = f"{CLIENT_ID}-{uuid.uuid4().hex}"

    websocket_connection = None
    try:
        import websocket

        ws_base = get_comfy_url().replace(
            "https://", "wss://", 1
        ).replace(
            "http://", "ws://", 1
        )
        ws_url = (
            ws_base
            + "/ws?"
            + urlencode({"clientId": client_id})
        )

        # Connect before queueing so the first execution/progress events
        # cannot be lost between POST /prompt and websocket handshake.
        websocket_connection = websocket.create_connection(
            ws_url,
            timeout=3,
            enable_multithread=True
        )
        websocket_connection.settimeout(2)

    except Exception as error:
        logger.info(
            "WebSocket-Vorverbindung nicht verfügbar; Statusabfrage bleibt aktiv: %s",
            error
        )

    payload = {
        "prompt": workflow,
        "client_id": client_id
    }

    try:
        result = comfy_json(
            "POST",
            "/prompt",
            json=payload,
            timeout=60
        )
    except Exception:
        if websocket_connection is not None:
            try:
                websocket_connection.close()
            except Exception:
                pass
        raise

    prompt_id = result.get("prompt_id")

    if not prompt_id:

        raise RuntimeError(
            "ComfyUI hat keine Prompt-ID zurückgegeben: "
            + str(result)
        )

    save_job(
        prompt_id,
        kind,
        seed,
        prompt,
        client_id=client_id
    )

    start_progress_monitor(prompt_id, client_id, websocket_connection)

    return prompt_id


# =========================================================
# COMFYUI HISTORY ABRUFEN
# =========================================================

def get_history(prompt_id):

    return comfy_json(
        "GET",
        f"/history/{prompt_id}",
        timeout=30
    )


# =========================================================
# JOB-STATUS ERMITTELN
# =========================================================

def get_job_status(prompt_id):

    history = get_history(prompt_id)
    job = get_local_job(prompt_id)

    if job and job.get("error"):

        return {
            "state": "error",
            "completed": False,
            "message": str(job["error"])
        }

    if prompt_id not in history:

        if job:

            elapsed = time.time() - job["started"]

            if elapsed > MAX_WAIT:

                return {
                    "state": "timeout",
                    "completed": False,
                    "message": "Zeitüberschreitung."
                }

        return {
            "state": "running",
            "completed": False,
            "message": "Auftrag wird verarbeitet."
        }

    job_history = history[prompt_id]

    status = job_history.get(
        "status",
        {}
    )

    for message in status.get("messages", []):

        if (
            isinstance(message, list)
            and message
            and message[0] == "execution_error"
        ):

            error_info = (
                message[1]
                if len(message) > 1
                else {}
            )

            update_job(
                prompt_id,
                error=str(error_info)
            )

            return {
                "state": "error",
                "completed": False,
                "message": str(error_info)
            }

    if status.get("completed", False):

        update_job(
            prompt_id,
            finished=True
        )

        return {
            "state": "completed",
            "completed": True,
            "message": "Auftrag erfolgreich abgeschlossen."
        }

    return {
        "state": "running",
        "completed": False,
        "message": "ComfyUI arbeitet."
    }

# =========================================================
# AUSGABEBILDER AUS COMFYUI AUSLESEN
# =========================================================

def get_output_images(prompt_id):

    history = get_history(prompt_id)

    if prompt_id not in history:

        return []

    outputs = history[prompt_id].get(
        "outputs",
        {}
    )

    images = []

    for node_id, node_output in outputs.items():

        for image_data in node_output.get(
            "images",
            []
        ):

            images.append({

                "filename": image_data["filename"],

                "subfolder": image_data.get(
                    "subfolder",
                    ""
                ),

                "type": image_data.get(
                    "type",
                    "output"
                ),

                "node": node_id

            })

    return images


# =========================================================
# BILDER AN COMFYUI ÜBERTRAGEN
# =========================================================

def upload_to_comfy(file_storage):

    filename = secure_filename(
        file_storage.filename or ""
    )

    if not allowed_file(filename):

        raise ValueError(
            "Dieses Bildformat wird nicht unterstützt."
        )

    file_storage.stream.seek(0)

    files = {

        "image": (
            filename,
            file_storage.stream,
            file_storage.mimetype or "application/octet-stream"
        )

    }

    response = comfy_request(
        "POST",
        "/upload/image",
        files=files,
        data={
            "overwrite": "true"
        },
        timeout=120
    )

    result = response.json()

    image_name = result.get("name")

    if not image_name:

        raise RuntimeError(
            "ComfyUI konnte das Bild nicht übernehmen."
        )

    subfolder = result.get(
        "subfolder",
        ""
    )

    if subfolder:

        image_name = (
            subfolder.rstrip("/")
            + "/"
            + image_name
        )

    return image_name


# =========================================================
# BILDER MIT URL VERSEHEN
# =========================================================

def add_image_urls(images):

    result = []

    for image_data in images:

        item = dict(image_data)

        item["url"] = (
            "/api/image?"
            + urlencode({
                "filename": item["filename"],
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output")
            })
        )

        item["download_url"] = (
            item["url"] + "&download=1"
        )

        result.append(item)

    return result


# =========================================================
# GALERIE LADEN
# =========================================================

def load_gallery():

    if not GALLERY_FILE.exists():

        return []

    try:

        data = read_json_file(
            GALLERY_FILE
        )

        if isinstance(data, list):

            return data

        return []

    except Exception:

        logger.exception(
            "Galerie konnte nicht geladen werden."
        )

        return []


# =========================================================
# GALERIE SPEICHERN
# =========================================================

def save_gallery(items):

    write_json_file(
        GALLERY_FILE,
        items
    )


# =========================================================
# GALERIE-EINTRAG HINZUFÜGEN
# =========================================================

def add_to_gallery(images, kind="z_image"):

    gallery = load_gallery()

    existing_keys = {
        (
            item.get("filename"),
            item.get("subfolder", ""),
            item.get("type", "output"),
            item.get("kind", "z_image")
        )
        for item in gallery
    }

    for image_data in images:

        key = (
            image_data.get("filename"),
            image_data.get("subfolder", ""),
            image_data.get("type", "output"),
            kind
        )

        if not key[0] or key in existing_keys:
            continue

        image_urls = add_image_urls([image_data])[0]

        entry = {
            "id": secrets.token_hex(12),
            "filename": image_data["filename"],
            "subfolder": image_data.get("subfolder", ""),
            "type": image_data.get("type", "output"),
            "kind": kind,
            "created": time.time(),
            "url": image_data.get("url") or image_urls["url"],
            "download_url": image_data.get("download_url") or image_urls["download_url"]
        }

        gallery.insert(0, entry)
        existing_keys.add(key)

    gallery = gallery[:500]
    save_gallery(gallery)

    return gallery


# =========================================================
# GALERIE-EINTRAG LÖSCHEN
# =========================================================

def delete_gallery_item(item_id):

    gallery = load_gallery()

    updated = [
        item
        for item in gallery
        if item.get("id") != item_id
    ]

    if len(updated) == len(gallery):

        return False

    save_gallery(updated)

    return True

# =========================================================
# UPSCALER WORKFLOW VORBEREITEN
# =========================================================

def prepare_upscaler(data, uploaded_image):

    workflow = copy.deepcopy(
        validate_upscaler_workflow()
    )

    if not uploaded_image:

        raise ValueError(
            "Bitte zuerst ein Bild auswählen."
        )

    try:
        factor = float(data.get("scale", 2))
    except (TypeError, ValueError):
        raise ValueError("Ungültiger Skalierungsfaktor.")

    if factor not in (2.0, 3.0, 4.0, 8.0):

        raise ValueError(
            "Erlaubt sind 2x, 3x, 4x und 8x."
        )

    method = str(
        data.get("method", "lanczos")
    ).lower()

    allowed_methods = {
        "lanczos",
        "nearest-exact",
        "bilinear",
        "bicubic",
        "area"
    }

    if method not in allowed_methods:

        raise ValueError(
            "Ungültige Upscaling-Methode."
        )

    filename_prefix = clean_filename(
        data.get("filename_prefix"),
        "Upscaled"
    )

    selected_model = str(
        data.get("upscale_model", "")
    ).strip()

    if selected_model:

        available_models = get_available_upscale_models()

        validate_model_selection(
            selected_model,
            available_models,
            "Upscale-Modell"
        )

    image_name = upload_to_comfy(
        uploaded_image
    )

    workflow["2"]["inputs"]["image"] = image_name
    workflow["5"]["inputs"]["filename_prefix"] = filename_prefix

    if selected_model:

        workflow.pop("3", None)

        workflow["4"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {
                "model_name": selected_model
            }
        }

        workflow["6"] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {
                "upscale_model": ["4", 0],
                "image": ["2", 0]
            }
        }

        workflow["5"]["inputs"]["images"] = ["6", 0]

    else:

        workflow["3"]["inputs"]["upscale_method"] = method
        workflow["3"]["inputs"]["scale_by"] = factor

    return workflow


# =========================================================
# BROWSER: STARTSEITE
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# API: STATUS
# =========================================================

@app.route(
    "/api/status",
    methods=["GET"]
)
def api_status():

    connected = check_connection()

    return jsonify({

        "success": True,

        "connected": connected,

        "server": get_comfy_url(),

        "application": "ComfyUI Assistant V3.6"

    })


# =========================================================
# API: MODELLE
# =========================================================

@app.route(
    "/api/models",
    methods=["GET"]
)
def api_models():

    try:

        models = get_available_models()

        return jsonify({

            "success": True,

            "models": models,

            "defaults": {

                "unet": DEFAULT_UNET,

                "clip": DEFAULT_CLIP,

                "vae": DEFAULT_VAE

            }

        })

    except Exception as error:

        logger.exception(
            "Modellabfrage fehlgeschlagen."
        )

        return json_error(
            str(error),
            500
        )


# =========================================================
# API: UPSCALER-METHODEN
# =========================================================

@app.route(
    "/api/upscale-models",
    methods=["GET"]
)
def api_upscale_models():

    try:

        models = get_available_upscale_models()

        return jsonify({

            "success": True,

            "models": models,

            "methods": [
                "lanczos",
                "nearest-exact",
                "bilinear",
                "bicubic",
                "area"
            ],

            "types": {
                "classic": "ImageScaleBy",
                "ai": "UpscaleModelLoader + ImageUpscaleWithModel"
            }

        })

    except Exception as error:

        logger.exception(
            "Upscale-Modelle konnten nicht geladen werden."
        )

        return json_error(
            str(error),
            503
        )


# =========================================================
# API: WORKFLOWS PRÜFEN
# =========================================================

@app.route(
    "/api/workflows",
    methods=["GET"]
)
def api_workflows():

    result = {

        "z_image": False,

        "upscaler": False,

        "errors": []

    }

    try:

        validate_z_image_workflow()

        result["z_image"] = True

    except Exception as error:

        result["errors"].append(
            "Z-Image: " + str(error)
        )

    try:

        validate_upscaler_workflow()

        result["upscaler"] = True

    except Exception as error:

        result["errors"].append(
            "Upscaler: " + str(error)
        )

    return jsonify(result)

# =========================================================
# API: Z-IMAGE MEHRFACHGENERIERUNG
# =========================================================

@app.route(
    "/api/generate",
    methods=["POST"]
)
def api_generate():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        if not isinstance(data, dict):

            raise ValueError(
                "Ungültige Anfrage."
            )

        # -------------------------------------------------
        # BILDERANZAHL
        # -------------------------------------------------

        count = int(
            data.get("image_count", 1)
        )

        if not 1 <= count <= MAX_BATCH_IMAGES:

            raise ValueError(
                f"Erlaubt sind 1 bis {MAX_BATCH_IMAGES} Bilder."
            )

        random_seed = data.get(
            "random_seed",
            True
        )

        if isinstance(random_seed, str):

            random_seed = random_seed.lower() in (
                "true",
                "1",
                "yes"
            )

        base_seed = int(
            data.get("seed", 0)
        )

        if not random_seed:

            if not 0 <= base_seed <= 2**64 - 1:

                raise ValueError(
                    "Ungültiger Seed."
                )

            if base_seed + count - 1 > 2**64 - 1:

                raise ValueError(
                    "Seed-Bereich überschritten."
                )

        # -------------------------------------------------
        # WORKFLOWS VORBEREITEN
        # -------------------------------------------------

        prepared_workflows = []

        for index in range(count):

            image_data = dict(data)

            if random_seed:

                image_data["random_seed"] = True

            else:

                image_data["random_seed"] = False

                image_data["seed"] = (
                    base_seed + index
                )

            workflow, seed = prepare_z_image(
                image_data
            )

            prepared_workflows.append({

                "workflow": workflow,

                "seed": seed,

                "number": index + 1

            })

        # -------------------------------------------------
        # AUFTRÄGE AN COMFYUI SENDEN
        # -------------------------------------------------

        jobs = []

        for item in prepared_workflows:

            prompt_id = queue_workflow(

                item["workflow"],

                kind="z_image",

                seed=item["seed"],

                prompt=str(
                    data.get("positive_prompt", "")
                )

            )

            jobs.append({

                "prompt_id": prompt_id,

                "seed": item["seed"],

                "number": item["number"]

            })

        logger.info(
            "%s Z-Image-Auftrag/-Aufträge gestartet.",
            len(jobs)
        )

        return jsonify({

            "success": True,

            "count": len(jobs),

            "jobs": jobs,

            "prompt_ids": [
                job["prompt_id"]
                for job in jobs
            ],

            "seeds": [
                job["seed"]
                for job in jobs
            ],

            "message": (
                f"{len(jobs)} Bildauftrag/-aufträge "
                "wurden an ComfyUI übergeben."
            )

        })

    except Exception as error:

        logger.exception(
            "Z-Image-Generierung fehlgeschlagen."
        )

        return json_error(
            str(error),
            400
        )


# =========================================================
# API: UPSCALER
# =========================================================

@app.route(
    "/api/upscale",
    methods=["POST"]
)
def api_upscale():

    try:

        uploaded_image = request.files.get(
            "image"
        )

        if not uploaded_image:

            raise ValueError(
                "Kein Bild hochgeladen."
            )

        if not allowed_file(
            uploaded_image.filename
        ):

            raise ValueError(
                "Nicht unterstütztes Bildformat."
            )

        data = request.form.to_dict()

        workflow = prepare_upscaler(
            data,
            uploaded_image
        )

        prompt_id = queue_workflow(

            workflow,

            kind="upscaler"

        )

        logger.info(
            "Upscaling gestartet: %s",
            prompt_id
        )

        return jsonify({

            "success": True,

            "prompt_id": prompt_id,

            "message": "Upscaling-Auftrag gestartet."

        })

    except Exception as error:

        logger.exception(
            "Upscaler fehlgeschlagen."
        )

        return json_error(
            str(error),
            400
        )


# =========================================================
# API: FORTSCHRITT
# =========================================================

@app.route(
    "/api/progress/<prompt_id>",
    methods=["GET"]
)
def api_progress(prompt_id):

    try:

        result = get_job_status(
            prompt_id
        )

        job = get_local_job(
            prompt_id
        )

        if job:

            result["kind"] = job["kind"]

            result["elapsed"] = round(
                time.time() - job["started"],
                1
            )

            result["seed"] = job["seed"]
            result["progress"] = job.get("progress")
            result["progress_value"] = job.get("progress_value")
            result["progress_max"] = job.get("progress_max")
            result["progress_node"] = job.get("progress_node")
            result["progress_message"] = job.get("progress_message")

        return jsonify(result)

    except Exception as error:

        logger.exception(
            "Fortschrittsabfrage fehlgeschlagen."
        )

        return jsonify({

            "state": "error",

            "completed": False,

            "message": str(error)

        }), 500


# =========================================================
# API: ERGEBNISSE
# =========================================================

@app.route(
    "/api/result/<prompt_id>",
    methods=["GET"]
)
def api_result(prompt_id):

    try:

        status = get_job_status(
            prompt_id
        )

        if not status["completed"]:

            return jsonify({

                "success": False,

                "state": status["state"],

                "message": status["message"]

            }), 409

        images = get_output_images(
            prompt_id
        )

        if not images:

            raise RuntimeError(
                "Keine Bilder im ComfyUI-Ergebnis gefunden."
            )

        images = add_image_urls(
            images
        )

        job = get_local_job(
            prompt_id
        )

        kind = (
            job["kind"]
            if job
            else "z_image"
        )

        # Galerie aktualisieren

        add_to_gallery(
            images,
            kind
        )

        return jsonify({

            "success": True,

            "images": images,

            "count": len(images),

            "prompt_id": prompt_id

        })

    except Exception as error:

        logger.exception(
            "Ergebnis konnte nicht geladen werden."
        )

        return jsonify({

            "success": False,

            "error": str(error)

        }), 500

# =========================================================
# API: BILDER ANZEIGEN UND DOWNLOAD
# =========================================================

@app.route(
    "/api/image",
    methods=["GET"]
)
def api_image():

    filename = request.args.get(
        "filename",
        ""
    )

    subfolder = request.args.get(
        "subfolder",
        ""
    )

    image_type = request.args.get(
        "type",
        "output"
    )

    download = (
        request.args.get("download") == "1"
    )

    # -----------------------------------------------------
    # EINGABEN PRÜFEN
    # -----------------------------------------------------

    if (
        not filename
        or Path(filename).name != filename
        or filename in (".", "..")
        or ".." in Path(subfolder).parts
        or "\\" in subfolder
        or image_type not in (
            "output",
            "temp",
            "input"
        )
    ):

        return jsonify({

            "success": False,

            "error": "Ungültige Bildadresse."

        }), 400

    try:

        response = comfy_request(

            "GET",

            "/view",

            params={

                "filename": filename,

                "subfolder": subfolder,

                "type": image_type

            },

            timeout=60

        )

        result = Response(

            response.content,

            mimetype=response.headers.get(
                "Content-Type",
                "image/png"
            )

        )

        if download:

            safe_name = clean_filename(
                filename,
                "ComfyUI-Bild"
            )

            result.headers[
                "Content-Disposition"
            ] = (
                f'attachment; filename="{safe_name}"'
            )

        return result

    except Exception as error:

        logger.exception(
            "Bild konnte nicht geladen werden."
        )

        return jsonify({

            "success": False,

            "error": str(error)

        }), 500


# =========================================================
# API: GALERIE ABRUFEN
# =========================================================

@app.route(
    "/api/gallery",
    methods=["GET"]
)
def api_gallery():

    try:

        gallery = load_gallery()

        return jsonify({

            "success": True,

            "images": gallery,

            "count": len(gallery)

        })

    except Exception as error:

        logger.exception(
            "Galerie konnte nicht geladen werden."
        )

        return json_error(
            str(error),
            500
        )


# =========================================================
# API: GESAMTE GALERIE LEEREN
# =========================================================

@app.route(
    "/api/gallery",
    methods=["DELETE"]
)
def api_gallery_clear():

    try:

        previous_count = len(load_gallery())
        save_gallery([])

        return jsonify({
            "success": True,
            "deleted": previous_count,
            "count": 0,
            "message": "Galerie wurde geleert."
        })

    except Exception as error:

        logger.exception(
            "Galerie konnte nicht geleert werden."
        )

        return json_error(
            str(error),
            500
        )


# =========================================================
# API: GALERIE-EINTRAG LÖSCHEN
# =========================================================

@app.route(
    "/api/gallery/<item_id>",
    methods=["DELETE"]
)
def api_gallery_delete(item_id):

    try:

        deleted = delete_gallery_item(
            item_id
        )

        if not deleted:

            return json_error(
                "Galerie-Eintrag nicht gefunden.",
                404
            )

        return jsonify({

            "success": True,

            "message": "Galerie-Eintrag gelöscht."

        })

    except Exception as error:

        logger.exception(
            "Galerie-Eintrag konnte nicht gelöscht werden."
        )

        return json_error(
            str(error),
            500
        )


# =========================================================
# API: BILD UPLOAD
# =========================================================

@app.route(
    "/api/upload",
    methods=["POST"]
)
def api_upload():

    try:

        uploaded_image = request.files.get(
            "image"
        )

        if not uploaded_image:

            raise ValueError(
                "Kein Bild ausgewählt."
            )

        if not allowed_file(
            uploaded_image.filename
        ):

            raise ValueError(
                "Nicht unterstütztes Bildformat."
            )

        image_name = upload_to_comfy(
            uploaded_image
        )

        return jsonify({

            "success": True,

            "filename": image_name,

            "message": "Bild erfolgreich hochgeladen."

        })

    except Exception as error:

        logger.exception(
            "Bild-Upload fehlgeschlagen."
        )

        return json_error(
            str(error),
            400
        )


# =========================================================
# FEHLERBEHANDLUNG: DATEI ZU GROSS
# =========================================================

@app.errorhandler(413)
def file_too_large(error):

    return jsonify({

        "success": False,

        "error": "Die Datei ist größer als 50 MB."

    }), 413


# =========================================================
# FEHLERBEHANDLUNG: API NICHT GEFUNDEN
# =========================================================

@app.errorhandler(404)
def not_found(error):

    if request.path.startswith("/api/"):

        return jsonify({

            "success": False,

            "error": "API-Endpunkt nicht gefunden."

        }), 404

    return "Seite nicht gefunden.", 404


# =========================================================
# FEHLERBEHANDLUNG: SERVERFEHLER
# =========================================================

@app.errorhandler(500)
def internal_server_error(error):

    logger.exception(
        "Interner Serverfehler."
    )

    return jsonify({

        "success": False,

        "error": "Interner Serverfehler."

    }), 500


# =========================================================
# PROGRAMMSTART
# =========================================================

if __name__ == "__main__":

    print()

    print("=" * 58)

    print("             COMFYUI ASSISTANT V3.6")

    print("=" * 58)

    print()

    print("[INFO] Prüfe Projektdateien ...")

    # -----------------------------------------------------
    # COMFYUI VERBINDUNG
    # -----------------------------------------------------

    if check_connection():

        print("[OK] ComfyUI ist erreichbar.")

    else:

        print("[WARNUNG] ComfyUI ist nicht erreichbar.")

        print("[INFO] Bitte ComfyUI starten.")

    print()

    print("Browser:  http://127.0.0.1:5000")

    print("ComfyUI: ", get_comfy_url())

    print()

    # -----------------------------------------------------
    # FUNKTIONSÜBERSICHT
    # -----------------------------------------------------

    print("Z-Image:                 Aktiv")

    print("Mehrfachgenerierung:     Aktiv")

    print("Maximale Bildanzahl:    ", MAX_BATCH_IMAGES)

    print("UNET-Auswahl:            Aktiv")

    print("CLIP-Auswahl:            Aktiv")

    print("VAE-Auswahl:             Aktiv")

    print("Zufälliger Seed:         Aktiv")

    print("Manueller Seed:          Aktiv")

    print("Upscaler:                ImageScaleBy / KI-Upscale-Modell")

    print("Upscaling:               2x / 3x / 4x / 8x")

    print("Galerie:                 Aktiv")

    print("Bild-Download:           Aktiv")

    print("Dynamische Modellabfrage: Z-Image + Upscale-Modelle")

    print()

    print("=" * 58)

    print("       COMFYUI ASSISTANT WIRD GESTARTET")

    print("=" * 58)

    print()

    app.run(

        host="127.0.0.1",

        port=5000,

        debug=False,

        threaded=True,

        use_reloader=False

    )
