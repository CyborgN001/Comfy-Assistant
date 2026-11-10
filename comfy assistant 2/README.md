# ComfyUI Assistant V3.6

A local Flask frontend for ComfyUI with Z-Image generation, persistent gallery management, full-size image viewing, and classic or model-based upscaling.

## Start on Windows

1. Start ComfyUI and confirm that its API is reachable at `http://127.0.0.1:8188`.
2. Double-click `start.bat`.
3. The launcher installs the packages listed in `requirements.txt` and starts the assistant.
4. Open `http://127.0.0.1:5000` if the browser does not open automatically.

The ComfyUI server URL can be changed with the `COMFYUI_URL` environment variable before starting the app, for example:

```bat
set COMFYUI_URL=http://127.0.0.1:8188
py app.py
```

## Gallery repairs

- Saved entries are loaded from `gallery.json` whenever the frontend starts.
- Clicking a thumbnail opens the full-size lightbox. Press `Esc`, click the close control, or click the dark background to close it.
- Each image can be removed separately.
- **Galerie leeren** clears all gallery entries and persists the empty gallery on the server.
- The bulk-delete action asks for confirmation before deleting.
- Gallery additions are de-duplicated by ComfyUI filename, subfolder, output type, and job type.

Clearing the gallery removes the application's saved gallery entries; it does not delete the original image files from ComfyUI's output directory.

## Upscaling

Two modes are supported:

### Classic resize

Leave the AI-model selector on **Kein KI-Modell – klassisches Resize**. The workflow uses ComfyUI's `ImageScaleBy` node and the selected interpolation method and factor (2x, 3x, 4x, or 8x).

### AI model upscaling

Place an upscaling model supported by ComfyUI in `ComfyUI/models/upscale_models` (or a configured additional model path), restart/refresh ComfyUI as needed, then reopen the assistant or refresh the connection. The model list is retrieved from ComfyUI's `UpscaleModelLoader` node. When a model is selected, the workflow uses:

`LoadImage -> UpscaleModelLoader -> ImageUpscaleWithModel -> SaveImage`

Model-based upscaling determines its own output scale; the classic scale and interpolation controls are disabled while this mode is active.

## Progress display

The application connects to ComfyUI's `/ws?clientId=...` WebSocket before submitting a job and filters progress events for that job. During nodes that publish step progress, the UI displays the received step percentage. For nodes that do not publish a numeric percentage (which can include some image-upscaling nodes), the bar switches to an animated indeterminate state and displays elapsed time instead of showing a fabricated percentage. ComfyUI's history endpoint remains the source of truth for job completion and errors.

## Configuration and files

- `app.py` — Flask API and ComfyUI integration
- `templates/index.html` — frontend structure
- `static/app.js` — frontend behavior
- `static/style.css` — layout and styling
- `workflows/z_image_api.json` — Z-Image API workflow template
- `workflows/upscaler_api.json` — classic upscale API workflow template
- `workflows/upscaler.json` — original ComfyUI canvas workflow export retained for reference
- `gallery.json` — persisted gallery data
- `requirements.txt` — Python dependencies

## API endpoints added or corrected

- `GET /api/gallery` — return saved gallery items
- `DELETE /api/gallery` — clear the entire gallery
- `DELETE /api/gallery/<item_id>` — delete one gallery item
- `GET /api/upscale-models` — return installed ComfyUI upscale models
- `GET /api/progress/<prompt_id>` — include live progress and elapsed time

## Notes

The Flask and ComfyUI services must both be running for generation, image serving, and upscaling. The editor sandbox used to patch this package did not have Flask installed and did not have access to the user's running ComfyUI instance; the source files were syntax-checked and core gallery/upscaler backend logic was exercised with mocked API responses. A final live end-to-end test should be run on the machine that hosts ComfyUI.
