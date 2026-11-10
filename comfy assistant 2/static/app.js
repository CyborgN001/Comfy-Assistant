"use strict";

// =====================================================
// COMFYUI ASSISTANT V3.6 
// =====================================================

const $ = (id) => document.getElementById(id);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let galleryItems = [];
const activeJobs = new Set();
let selectedUpload = null;
let initializationStarted = false;
let statusInterval = null;
let comfyConnected = false;
let serverModelsLoaded = false;
let currentLightboxItem = null;
let upscalePreviewObjectUrl = null;

// =====================================================
// KONFIGURATION
// =====================================================

const APP_CONFIG = {
    version: "3.6",
    maxImages: 20,
    maxUploadSize: 50 * 1024 * 1024,
    jobTimeout: 900000,
    pollInterval: 2000,
    statusInterval: 15000,
    defaultWidth: 832,
    defaultHeight: 1216,
    defaultSteps: 10,
    defaultCFG: 1,
    minDimension: 64,
    maxDimension: 4096,
    maxSeed: 18446744073709551615n
};

// =====================================================
// TOAST & ERROR HANDLING
// =====================================================

function toast(message, type = "") {
    const container = $("toast-container");
    if (!container) return;
    const element = document.createElement("div");
    element.className = `toast ${type}`;
    element.textContent = String(message ?? "");
    container.appendChild(element);
    setTimeout(() => element.remove(), 4500);
}

function handleError(error, context = "") {
    console.error(context || "Anwendungsfehler", error);
    const message = error?.message || "Ein unbekannter Fehler ist aufgetreten.";
    toast(message, "error");
}

// =====================================================
// API REQUEST (vereinheitlicht)
// =====================================================

async function apiRequest(url, options = {}) {
    const headers = options.headers || {};
    const isFormData = options.body instanceof FormData;

    if (!isFormData && options.body !== undefined && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers,
            cache: "no-store"
        });

        const contentType = response.headers.get("content-type") || "";
        let data = {};

        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            const body = await response.text();
            data = { error: body || `Unerwartete Serverantwort (${response.status}).` };
        }

        if (!response.ok || (data.success === false)) {
            throw new Error(data.error || data.message || `Serverfehler: ${response.status}`);
        }
        return data;
    } catch (error) {
        throw error;
    }
}

// =====================================================
// HELPER FUNCTIONS
// =====================================================

function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = String(value ?? "");
}

function setVisible(id, visible) {
    const el = $(id);
    if (el) el.classList.toggle("hidden", !visible);
}

function setButtonLoading(button, loading, loadingText = "Bitte warten...") {
    if (!button) return;
    if (loading) {
        button.dataset.originalText = button.textContent;
        button.disabled = true;
        button.textContent = loadingText;
    } else {
        button.disabled = false;
        if (button.dataset.originalText) {
            button.textContent = button.dataset.originalText;
            delete button.dataset.originalText;
        }
    }
}

function getValue(id, fallback = "") {
    return $(id)?.value ?? fallback;
}

function getNumber(id, fallback = 0) {
    const val = Number(getValue(id, fallback));
    return Number.isFinite(val) ? val : fallback;
}

// =====================================================
// SEED & MODEL MANAGEMENT (repariert)
// =====================================================

function initializeSeed() {
    const randomSeed = $("random-seed");
    const seedInput = $("seed");

    if (!randomSeed || !seedInput) return;

    randomSeed.addEventListener("change", () => {
        seedInput.disabled = randomSeed.checked;
        if (randomSeed.checked) {
            generateRandomSeed();
        }
    });

    generateRandomSeed();
}

function generateRandomSeed() {
    const seedInput = $("seed");

    if (seedInput) {
        const randomSeed = BigInt(
            "0x" + Array(16).fill(0)
                .map(() => Math.floor(Math.random() * 16).toString(16))
                .join("")
        );

        seedInput.value = randomSeed.toString();
    }
}

async function loadModels() {
    const selects = {
        "unet-model": "unet",
        "clip-model": "clip",
        "vae-model": "vae"
    };

    try {
        const data = await apiRequest("/api/models");

        Object.entries(selects).forEach(([id, key]) => {
            const select = $(id);
            if (!select) return;

            const models = data.models?.[key] || [];
            select.innerHTML = "";

            models.forEach(model => {
                const option = document.createElement("option");
                option.value = model;
                option.textContent = model;
                select.appendChild(option);
            });

            if (models.length === 0) {
                const option = document.createElement("option");
                option.value = "";
                option.textContent = "Keine Modelle gefunden";
                select.appendChild(option);
            }

            const defaultModel = data.defaults?.[key];
            if (defaultModel && models.includes(defaultModel)) {
                select.value = defaultModel;
            }
        });
    } catch (error) {
        Object.keys(selects).forEach(id => {
            const select = $(id);
            if (select) select.innerHTML = '<option value="">ComfyUI nicht verfügbar</option>';
        });
        console.warn("ComfyUI-Modelle konnten nicht geladen werden:", error);
    }
}


async function loadUpscaleModels() {
    const select = $("upscale-model");
    const status = $("upscale-model-status");
    if (!select) return;

    select.innerHTML = "";
    const classicOption = document.createElement("option");
    classicOption.value = "";
    classicOption.textContent = "Kein KI-Modell – klassisches Resize";
    select.appendChild(classicOption);

    try {
        const data = await apiRequest("/api/upscale-models");
        const models = Array.isArray(data.models) ? data.models : [];

        models.forEach(model => {
            const option = document.createElement("option");
            option.value = model;
            option.textContent = model;
            select.appendChild(option);
        });

        if (status) {
            status.textContent = models.length
                ? `${models.length} KI-Upscale-Modell(e) in ComfyUI gefunden.`
                : "Keine KI-Upscale-Modelle gefunden. Klassisches Resize bleibt verfügbar.";
        }
    } catch (error) {
        if (status) {
            status.textContent = "KI-Modellliste derzeit nicht verfügbar. Klassisches Resize bleibt verfügbar.";
        }
        console.warn("Upscale-Modelle konnten nicht geladen werden:", error);
    }

    updateUpscaleModeDescription();
}

function updateUpscaleModeDescription() {
    const model = $("upscale-model")?.value || "";
    const note = $("upscale-mode-note");
    const scaleSelect = $("upscale-scale");
    const methodSelect = $("upscale-method");

    if (scaleSelect) scaleSelect.disabled = Boolean(model);
    if (methodSelect) methodSelect.disabled = Boolean(model);

    if (!note) return;

    note.textContent = model
        ? `KI-Upscaling aktiv: ${model}. Skalierungsfaktor und Interpolationsmethode sind deaktiviert, weil der gewählte Modelltyp seine eigene Vergrößerung verwendet.`
        : "Klassischer ComfyUI-Upscale: Skalierungsfaktor und Interpolationsmethode werden direkt angewendet.";
}


// =====================================================
// Z-IMAGE GENERATION
// =====================================================



async function startZImageGeneration() {
    const button = $("generate-button");

    const positivePrompt = $("positive-prompt")?.value?.trim() || "";

    if (!positivePrompt) {
        toast("Bitte zuerst einen positiven Prompt eingeben.", "error");
        $("positive-prompt")?.focus();
        return;
    }

    setButtonLoading(button, true, "Generiere...");

    try {
        const data = {
            positive_prompt: positivePrompt,
            negative_prompt: getValue("negative-prompt"),
            width: getNumber("image-width", 832),
            height: getNumber("image-height", 1216),
            steps: getNumber("steps", 10),
            cfg: getNumber("cfg", 1),
            random_seed: $("random-seed")?.checked ?? true,
            seed: $("seed")?.value || "0",
            filename_prefix: getValue("filename-prefix", "ComfyUI"),
            image_count: getNumber("image-count", 1)
        };

        data.unet_model = $("unet-model")?.value || "";
        data.clip_model = $("clip-model")?.value || "";
        data.vae_model = $("vae-model")?.value || "";

        console.log("Z-Image Anfrage:", data);

        const response = await apiRequest("/api/generate", {
            method: "POST",
            body: JSON.stringify(data)
        });

        toast("Generierung gestartet", "success");

        updateProgress("zimage", null, "Bild wird vorbereitet...");

        if (!response.jobs || response.jobs.length === 0) {
            throw new Error("Keine Jobs vom Server erhalten.");
        }

        for (const job of response.jobs) {
            registerJob(job.prompt_id);

            try {
                const images = await waitForJob(
                    job.prompt_id,
                    "zimage"
                );

                console.log("Generierte Bilder:", images);

                if (images && images.length > 0) {
                    await refreshGallery();

                    toast(
                        images.length + " Bild(er) erfolgreich generiert.",
                        "success"
                    );
                } else {
                    toast("Keine Bilder erhalten.", "error");
                }

            } finally {
                unregisterJob(job.prompt_id);
            }
        }

    } catch (error) {
        handleError(error, "Z-Image Generierung");

    } finally {
        setButtonLoading(button, false);
    }
}

// =====================================================
// UPSCALER
// =====================================================

function validateUpscaleFile(file) {
    if (!file) throw new Error("Keine Datei ausgewählt.");
    if (file.size === 0) throw new Error("Die Datei ist leer.");
    if (file.size > APP_CONFIG.maxUploadSize) throw new Error("Die Datei darf maximal 50 MB groß sein.");
    if (!["image/png", "image/jpeg", "image/webp", "image/bmp"].includes(file.type)) {
        throw new Error("Erlaubt sind PNG, JPG, WEBP und BMP.");
    }
}

function getUpscaleSettings() {
    return {
        scale: getValue("upscale-scale", "2"),
        method: getValue("upscale-method", "lanczos"),
        upscale_model: $( "upscale-model" )?.value || "",
        filename_prefix: getValue("upscale-filename", "Upscaled")
    };
}

async function upscaleImage() {
    const button = $("upscale-button");
    const fileInput = $("upscale-image");

    if (activeJobs.size > 0) {
        toast("Bitte warte, bis der aktuelle Auftrag abgeschlossen ist.", "error");
        return;
    }

    let promptId = null;
    setButtonLoading(button, true);

    try {
        const file = fileInput.files[0];
        validateUpscaleFile(file);

        const settings = getUpscaleSettings();

        updateProgress("upscale", null, "Bild wird vorbereitet...");

        const formData = new FormData();
        formData.append("image", file);
        formData.append("scale", settings.scale);
        formData.append("method", settings.method);
        if (settings.upscale_model) formData.append("upscale_model", settings.upscale_model);
        formData.append("filename_prefix", settings.filename_prefix);

        const response = await apiRequest("/api/upscale", { method: "POST", body: formData });
        promptId = response.prompt_id;

        registerJob(promptId);
        toast("Upscaling wurde gestartet.", "success");

        const images = await waitForJob(promptId, "upscale");
        showUpscaleResult(images[0]);
        await refreshGallery();

        updateProgress("upscale", 100, "Upscaling erfolgreich abgeschlossen.");
        toast("Bild erfolgreich hochskaliert.", "success");

    } catch (error) {
        handleError(error, "Upscaler");
        updateProgress("upscale", null, "Fehler: " + error.message, { state: "error" });
    } finally {
        setButtonLoading(button, false);
        if (promptId) unregisterJob(promptId);
    }
}

// =====================================================
// UPSCALER-VORSCHAU (erhalten)
// =====================================================

function initializeUpscalePreview() {
    const input = $("upscale-image");
    const previewPanel = $("upload-preview");
    const previewImage = $("original-preview");
    const filename = $("original-filename");
    const dimensions = $("original-dimensions");
    const removeButton = $("remove-upload");

    if (!input || !previewPanel || !previewImage) return;

    input.addEventListener("change", () => {
        const file = input.files?.[0];
        if (!file) return;

        try {
            validateUpscaleFile(file);

            if (upscalePreviewObjectUrl) {
                URL.revokeObjectURL(upscalePreviewObjectUrl);
            }

            upscalePreviewObjectUrl = URL.createObjectURL(file);
            previewImage.src = upscalePreviewObjectUrl;
            setVisible("upload-preview", true);

            if (filename) filename.textContent = file.name;
            if (dimensions) dimensions.textContent = "Bildabmessungen werden gelesen...";

            previewImage.onload = () => {
                if (dimensions) {
                    dimensions.textContent =
                        `${previewImage.naturalWidth} × ${previewImage.naturalHeight}px · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
                }
            };

            previewImage.onerror = () => {
                toast("Die ausgewählte Bilddatei kann nicht angezeigt werden.", "error");
                input.value = "";
                setVisible("upload-preview", false);
            };

            setVisible("upscale-result-preview", false);
        } catch (error) {
            input.value = "";
            toast(error.message, "error");
        }
    });

    removeButton?.addEventListener("click", () => {
        input.value = "";

        if (upscalePreviewObjectUrl) {
            URL.revokeObjectURL(upscalePreviewObjectUrl);
            upscalePreviewObjectUrl = null;
        }

        previewImage.removeAttribute("src");
        if (filename) filename.textContent = "";
        if (dimensions) dimensions.textContent = "";

        setVisible("upload-preview", false);
        setVisible("upscale-result-preview", false);
    });
}


// =====================================================
// PROGRESS UPDATE
// =====================================================

function formatElapsed(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    const remainder = total % 60;
    return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function updateProgress(type, percent, message, options = {}) {
    const isZImage = type === "zimage";

    const ids = isZImage
        ? {
            panel: "generate-progress",
            status: "generate-status",
            percent: "generate-percent",
            bar: "generate-progress-bar",
            message: "generate-message",
            elapsed: "generate-elapsed"
        }
        : {
            panel: "upscale-progress",
            status: "upscale-status",
            percent: "upscale-percent",
            bar: "upscale-progress-bar",
            message: "upscale-message",
            elapsed: "upscale-elapsed"
        };

    setVisible(ids.panel, true);

    const hasProgress = percent !== null && percent !== undefined && Number.isFinite(Number(percent));
    const progress = hasProgress
        ? Math.max(0, Math.min(100, Number(percent)))
        : null;

    const bar = $(ids.bar);
    const status = $(ids.status);

    if (progress !== null) {
        setText(ids.percent, `${Math.round(progress)}%`);
        if (bar) {
            bar.classList.remove("progress-indeterminate");
            bar.style.width = `${progress}%`;
        }
    } else if (options.state === "error") {
        setText(ids.percent, "—");
        if (bar) {
            bar.classList.remove("progress-indeterminate");
            bar.style.width = "0%";
        }
    } else {
        setText(ids.percent, "Live");
        if (bar) {
            bar.classList.add("progress-indeterminate");
            bar.style.width = "34%";
        }
    }

    if (progress !== null && progress >= 100) {
        if (status) status.textContent = "Abgeschlossen";
    } else if (options.state === "error") {
        if (status) status.textContent = "Fehler";
    } else if (status) {
        status.textContent = options.statusText || "Verarbeitung läuft...";
    }

    setText(ids.message, message || "ComfyUI verarbeitet den Auftrag.");

    if (options.elapsed !== undefined && options.elapsed !== null) {
        setText(ids.elapsed, formatElapsed(options.elapsed));
    }
}


function registerJob(promptId) {
    if (!promptId) {
        throw new Error("Keine gültige Job-ID erhalten.");
    }

    activeJobs.add(promptId);
}

function unregisterJob(promptId) {
    if (promptId) {
        activeJobs.delete(promptId);
    }
}

async function waitForJob(promptId, type = "zimage") {
    if (!promptId) {
        throw new Error("Keine gültige Job-ID.");
    }

    const startTime = Date.now();

    while (Date.now() - startTime < APP_CONFIG.jobTimeout) {
        const data = await apiRequest(
            `/api/progress/${encodeURIComponent(promptId)}`
        );

        if (data.state === "error" || data.state === "timeout") {
            updateProgress(type, null, data.message || "ComfyUI-Auftrag fehlgeschlagen.", {
                state: "error",
                elapsed: data.elapsed
            });
            throw new Error(data.message || "ComfyUI-Fehler.");
        }

        if (data.completed === true) {
            updateProgress(type, 100, "ComfyUI hat die Verarbeitung abgeschlossen.", {
                elapsed: data.elapsed
            });

            const result = await apiRequest(
                `/api/result/${encodeURIComponent(promptId)}`
            );

            return result.images || [];
        }

        const hasRealProgress =
            data.progress !== null &&
            data.progress !== undefined &&
            Number.isFinite(Number(data.progress));

        const statusMessage = data.progress_message || data.message || "ComfyUI verarbeitet den Auftrag.";

        updateProgress(
            type,
            hasRealProgress ? Number(data.progress) : null,
            statusMessage,
            {
                elapsed: data.elapsed,
                statusText: hasRealProgress ? "Verarbeitung läuft..." : "ComfyUI arbeitet..."
            }
        );

        await new Promise(resolve =>
            setTimeout(resolve, APP_CONFIG.pollInterval)
        );
    }

    throw new Error("Zeitüberschreitung bei ComfyUI.");
}


 // =====================================================
// GALERIE ANZEIGEN
// =====================================================

function getGalleryDownloadUrl(item) {
    if (item.download_url) return item.download_url;
    const url = item.url || "";
    return url + (url.includes("?") ? "&" : "?") + "download=1";
}

function refreshGallery() {
    return apiRequest("/api/gallery")
        .then(data => {
            galleryItems = Array.isArray(data.images) ? data.images : [];
            renderGallery();
            return galleryItems;
        });
}

function renderGallery() {
    const grid = $("gallery-grid");
    if (!grid) return;

    grid.innerHTML = "";

    const count = galleryItems.length;
    setText("gallery-count", `${count} ${count === 1 ? "Bild" : "Bilder"}`);

    const clearButton = $("clear-gallery");
    if (clearButton) clearButton.disabled = count === 0;

    if (count === 0) {
        const empty = document.createElement("div");
        empty.className = "empty-state";

        const symbol = document.createElement("span");
        symbol.textContent = "▦";

        const heading = document.createElement("h3");
        heading.textContent = "Noch keine Bilder";

        const description = document.createElement("p");
        description.textContent = "Deine Ergebnisse erscheinen hier.";

        empty.append(symbol, heading, description);
        grid.appendChild(empty);
        return;
    }

    galleryItems.forEach(item => {
        const filename = item.filename || item.name || "Bild";
        const imageUrl = item.url;

        const card = document.createElement("article");
        card.className = "gallery-card";

        const image = document.createElement("img");
        image.src = imageUrl || "";
        image.alt = filename;
        image.loading = "lazy";
        image.decoding = "async";
        image.tabIndex = 0;
        image.setAttribute("role", "button");
        image.setAttribute("aria-label", `${filename} in Originalgröße öffnen`);

        const open = () => openLightbox(item);
        image.addEventListener("click", open);
        image.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                open();
            }
        });

        image.addEventListener("error", () => {
            image.alt = `${filename} konnte nicht geladen werden`;
            image.classList.add("gallery-image-error");
        });

        const details = document.createElement("div");
        details.className = "gallery-details";

        const name = document.createElement("strong");
        name.textContent = filename;

        const type = document.createElement("p");
        const kind = String(item.kind || item.type || "").toLowerCase();
        type.textContent = kind.includes("upscale")
            ? "Upscaler"
            : "Z-Image";

        const actions = document.createElement("div");
        actions.className = "gallery-actions";

        const download = document.createElement("a");
        download.href = getGalleryDownloadUrl(item);
        download.download = filename;
        download.className = "download-button";
        download.textContent = "↓ Bild speichern";

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "gallery-delete-button";
        remove.textContent = "Entfernen";
        remove.setAttribute("aria-label", `${filename} aus der Galerie entfernen`);
        remove.addEventListener("click", () => deleteGalleryItem(item.id));

        actions.append(download, remove);
        details.append(name, type, actions);
        card.append(image, details);
        grid.appendChild(card);
    });
}

function openLightbox(item) {
    const lightbox = $("image-lightbox");
    const image = $("lightbox-image");
    const download = $("lightbox-download");

    if (!lightbox || !image || !item?.url) {
        toast("Dieses Bild kann nicht geöffnet werden.", "error");
        return;
    }

    currentLightboxItem = item;
    image.onload = () => {
        const dimensions = `${image.naturalWidth} × ${image.naturalHeight}px`;
        setText("lightbox-filename", `${item.filename || item.name || "Bild"} · ${dimensions}`);
    };
    image.onerror = () => {
        setText("lightbox-filename", "Bild konnte nicht geladen werden.");
    };

    image.src = item.url;

    if (download) {
        download.href = getGalleryDownloadUrl(item);
        download.download = item.filename || item.name || "ComfyUI-Bild";
    }

    if (!image.complete) {
        setText("lightbox-filename", item.filename || item.name || "Originalgröße");
    }

    lightbox.classList.remove("hidden");
    document.body.classList.add("lightbox-open");
    $("lightbox-close")?.focus();
}

function closeLightbox() {
    const lightbox = $("image-lightbox");
    const image = $("lightbox-image");

    lightbox?.classList.add("hidden");
    document.body.classList.remove("lightbox-open");

    if (image) {
        image.removeAttribute("src");
        image.onload = null;
        image.onerror = null;
    }

    currentLightboxItem = null;
}

async function deleteGalleryItem(itemId) {
    if (!itemId) {
        toast("Dieser Galerie-Eintrag besitzt keine gültige ID.", "error");
        return;
    }

    try {
        await apiRequest(`/api/gallery/${encodeURIComponent(itemId)}`, {
            method: "DELETE"
        });

        galleryItems = galleryItems.filter(item => item.id !== itemId);
        renderGallery();
        toast("Bild aus der Galerie entfernt.", "success");
    } catch (error) {
        handleError(error, "Galerie-Eintrag löschen");
    }
}

async function clearGallery() {
    if (!galleryItems.length) {
        renderGallery();
        return;
    }

    const confirmed = window.confirm(
        `Möchtest du wirklich alle ${galleryItems.length} Bilder aus der Galerie entfernen?`
    );

    if (!confirmed) return;

    const button = $("clear-gallery");
    setButtonLoading(button, true, "Leere Galerie...");

    try {
        await apiRequest("/api/gallery", { method: "DELETE" });
        galleryItems = [];
        renderGallery();
        closeLightbox();
        toast("Die Galerie wurde vollständig geleert.", "success");
    } catch (error) {
        handleError(error, "Galerie leeren");
    } finally {
        setButtonLoading(button, false);
        if (button) button.disabled = galleryItems.length === 0;
    }
}

function showUpscaleResult(item) {
    if (!item?.url) {
        throw new Error("Das Upscaling-Ergebnis enthält keine Bildadresse.");
    }

    const before = $("upscale-before-image");
    const after = $("upscale-after-image");
    const slider = $("upscale-compare-range");
    const handle = $("upscale-compare-handle");
    const download = $("upscale-result-download");

    if (!before || !after) return;

    if (upscalePreviewObjectUrl) {
        before.src = upscalePreviewObjectUrl;
    } else {
        before.removeAttribute("src");
    }

    after.onload = () => {
        const originalDimensions = before.complete && before.naturalWidth
            ? `${before.naturalWidth} × ${before.naturalHeight}`
            : "Original";
        const resultDimensions = `${after.naturalWidth} × ${after.naturalHeight}`;
        setText("upscale-result-dimensions", `${originalDimensions} → ${resultDimensions} px`);
    };
    after.onerror = () => {
        setText("upscale-result-dimensions", "Das Ergebnisbild konnte nicht geladen werden.");
    };

    after.src = item.url;
    setText("upscale-result-name", item.filename || "Upscaling-Ergebnis");

    if (download) {
        download.href = getGalleryDownloadUrl(item);
        download.download = item.filename || "Upscaled.png";
    }

    const updateCompare = () => {
        const value = Number(slider?.value ?? 50);
        after.style.clipPath = `inset(0 ${100 - value}% 0 0)`;
        if (handle) handle.style.left = `${value}%`;
    };

    if (slider && !slider.dataset.bound) {
        slider.addEventListener("input", updateCompare);
        slider.dataset.bound = "true";
    }

    if (slider) slider.value = "50";
    updateCompare();
    setVisible("upscale-result-preview", true);
}


// =====================================================
// STATUS & MONITORING
// =====================================================

function updateConnectionDisplay(connected, server = "") {
    comfyConnected = connected;
    const statusClass = comfyConnected ? "connected" : "disconnected";
    $$(".connection-dot").forEach(el => el.className = statusClass);
    $$(".status-dot").forEach(el => el.className = statusClass);
    setText("connection-label", comfyConnected ? "ComfyUI verbunden" : "Nicht verbunden");
    setText("top-status", comfyConnected ? "ComfyUI Online" : "ComfyUI Offline");
}

async function refreshStatus() {
    try {
        const data = await apiRequest("/api/status");
        updateConnectionDisplay(data.connected, data.server);

        if (data.connected && !serverModelsLoaded) {
            await loadModels();
            await loadUpscaleModels();
            serverModelsLoaded = true;
        } else if (!data.connected) {
            serverModelsLoaded = false;
        }

        return data.connected;
    } catch (error) {
        updateConnectionDisplay(false);
        serverModelsLoaded = false;
        return false;
    }
}

function startStatusMonitoring() {
    if (statusInterval !== null) clearInterval(statusInterval);
    statusInterval = setInterval(async () => {
        await refreshStatus();
    }, APP_CONFIG.statusInterval);
}

// =====================================================
// NAVIGATION & PAGES
// =====================================================

function openPage(page) {
    $$(".page").forEach(p => p.classList.remove("active"));
    $$(".nav-item").forEach(n => n.classList.remove("active"));
    $(`page-${page}`).classList.add("active");
    $(`nav-item-${page}`)?.classList.add("active");

    setText("page-title", {
        dashboard: "Dashboard",
        zimage: "Z-Image",
        upscaler: "AI Upscaler",
        gallery: "Galerie",
        settings: "Einstellungen"
    }[page] || page);

    switch (page) {
        case "dashboard":
        case "zimage":
        case "upscaler":
        case "gallery":
        case "settings":
            // Hier weitere spezielle Updates möglich
            break;
    }
}

// =====================================================
// INITIALIZATION (einmalig)
// =====================================================

async function initialize() {
    if (initializationStarted) return;
    initializationStarted = true;

    // Events
    $$("[data-page]").forEach(el => {
        el.addEventListener("click", () => openPage(el.dataset.page));
    });
    $$("[data-open]").forEach(el => {
        el.addEventListener("click", () => openPage(el.dataset.open));
    });

    // Buttons
    $("generate-button")?.addEventListener("click", startZImageGeneration);
    $("upscale-button")?.addEventListener("click", upscaleImage);
    $("refresh-status")?.addEventListener("click", refreshStatus);
    $("clear-gallery")?.addEventListener("click", clearGallery);
    $("lightbox-close")?.addEventListener("click", closeLightbox);

    $("lightbox-background")?.addEventListener("click", event => {
        if (event.target === $("lightbox-background")) closeLightbox();
    });

    $("image-lightbox")?.addEventListener("click", event => {
        if (event.target === $("image-lightbox")) closeLightbox();
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !$("image-lightbox")?.classList.contains("hidden")) {
            closeLightbox();
        }
    });

    $("upscale-model")?.addEventListener("change", updateUpscaleModeDescription);

    // Seed & Upload-Vorschau initialisieren
    initializeSeed();
    initializeUpscalePreview();

    // Gespeicherte Galerie aus dem Backend laden
    try {
        await refreshGallery();
    } catch (error) {
        console.warn("Gespeicherte Galerie konnte nicht geladen werden:", error);
    }

    // Verbindung prüfen und Modelllisten nur bei erreichbarem ComfyUI laden
    await refreshStatus();
    startStatusMonitoring();

    // Standardseite
    openPage("dashboard");

    toast("ComfyUI Assistant V3.6 gestartet", "success");
}

// =====================================================
// PROGRAMMSTART
// =====================================================

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
} else {
    initialize();
}
