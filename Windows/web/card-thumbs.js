// Lazy, concurrency-limited static full-card thumbnail generator & cache
// shared by "资料库 → 全部球员" (library-ui.js) and "我的球员" (personal-ui.js).
import {NativeCardRenderer} from './native-card.js';

const CARD_W = 268;
const CARD_H = 384;
const MAX_CONCURRENT = 6;
const MAX_CACHE = 400;
const MAX_IMG_CACHE = 240;

const memoryCache = new Map(); // key -> url (persisted /card-thumbs/... or data:image/...)
const inFlight = new Map();    // key -> Promise<string|null>
const sharedImages = new Map(); // asset_id -> Promise<HTMLImageElement|null>
const sharedFonts = new Map();  // asset_id -> Promise<string|null>
const queue = [];
let activeCount = 0;

function loadSharedImage(id) {
  if (!id) return Promise.resolve(null);
  if (sharedImages.has(id)) {
    const p = sharedImages.get(id);
    sharedImages.delete(id);
    sharedImages.set(id, p);
    return p;
  }
  const promise = new Promise(resolve => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = '/assets/' + id;
  });
  sharedImages.set(id, promise);
  while (sharedImages.size > MAX_IMG_CACHE) {
    const oldest = sharedImages.keys().next().value;
    sharedImages.delete(oldest);
  }
  return promise;
}

function loadSharedFont(record) {
  if (!record?.asset_id) return Promise.resolve(null);
  const id = record.asset_id;
  if (sharedFonts.has(id)) return sharedFonts.get(id);
  const family = 'Native' + id.slice(0, 16);
  let face;
  try { face = new FontFace(family, 'url(/assets/' + id + ')'); } catch { return Promise.resolve(null); }
  const promise = face.load().then(() => {
    document.fonts.add(face);
    return family;
  }, () => null);
  sharedFonts.set(id, promise);
  return promise;
}

function cacheGet(key) {
  if (!key || !memoryCache.has(key)) return null;
  const val = memoryCache.get(key);
  // refresh LRU order
  memoryCache.delete(key);
  memoryCache.set(key, val);
  return val;
}

function cacheSet(key, url) {
  if (!key || !url) return;
  if (memoryCache.has(key)) memoryCache.delete(key);
  memoryCache.set(key, url);
  while (memoryCache.size > MAX_CACHE) {
    const oldest = memoryCache.keys().next().value;
    memoryCache.delete(oldest);
  }
}

export function invalidateThumbPrefix(prefix) {
  if (!prefix) return;
  for (const key of [...memoryCache.keys()]) {
    if (key.startsWith(prefix)) memoryCache.delete(key);
  }
}

export function getCachedThumb(key, serverUrl) {
  if (serverUrl) {
    cacheSet(key, serverUrl);
    return serverUrl;
  }
  return cacheGet(key);
}

function loadImg(id) {
  return loadSharedImage(id);
}

function canvasHasPixels(canvas) {
  try {
    const ctx = canvas.getContext('2d', {willReadFrequently: true});
    const w = canvas.width, h = canvas.height;
    if (!w || !h) return false;
    // Sample a 5x5 grid across the card to confirm non-empty render
    for (let gy = 1; gy <= 5; gy++) {
      for (let gx = 1; gx <= 5; gx++) {
        const px = ctx.getImageData(Math.floor(w * gx / 6), Math.floor(h * gy / 6), 1, 1).data;
        if (px[3] > 10) return true;
      }
    }
    return false;
  } catch {
    return true;
  }
}

function encodeCanvas(canvas) {
  try {
    const webp = canvas.toDataURL('image/webp', 0.86);
    if (webp && webp.startsWith('data:image/webp;base64,')) return webp;
  } catch {}
  try {
    return canvas.toDataURL('image/png');
  } catch {
    return null;
  }
}

async function renderProjectToDataUrl(project, simulation, catalog, {requireCardLayers = false} = {}) {
  const card = project?.native_card;
  const app = project?.appearance || {};
  const hasNative = Boolean(
    card && (card.layers?.length || (app.card_background_override && app.background_image))
  );

  const canvas = document.createElement('canvas');
  canvas.width = CARD_W;
  canvas.height = CARD_H;

  if (hasNative) {
    // Require a real base/background/frame layer (f01/f02/f04), not just a lone
    // transparent sparkle overlay (f10/f03/f05), before treating it as a complete card.
    const baseSlots = new Set(['f01', 'f02', 'f04']);
    const hasBaseLayer = (card.layers || []).some(l => (baseSlots.has(l.slot) || l.native_slot==='f07') && l.asset_id);
    const hasBgOverride = Boolean(app.card_background_override && app.background_image);
    if (requireCardLayers && !hasBaseLayer && !hasBgOverride) return null;

    const previewProject = {
      ...project,
      appearance: {...app},
      native_card: {...card, preview_only: true},
    };
    const renderer = new NativeCardRenderer(canvas, () => {}, catalog || {});
    renderer.motion = false;
    // Reuse session-wide decoded images & fonts so pages render in milliseconds
    const origAsset = renderer.asset.bind(renderer);
    renderer.font = record => loadSharedFont(record);
    renderer.asset = layer => layer?.kind === 'video' ? origAsset(layer) : loadSharedImage(layer?.asset_id);
    try {
      await renderer.update(previewProject, simulation || {ovr: ''});
      // Ensure at least one visual layer loaded
      const loadedNonPortrait = renderer.resources.some(l => (l.slot !== 'portrait' || l.native_slot==='f07') && l.element);
      const loadedAny = renderer.resources.some(l => l.element);
      if (!loadedAny || (requireCardLayers && !loadedNonPortrait)) return null;
      if (!canvasHasPixels(canvas)) return null;
      return encodeCanvas(canvas);
    } finally {
      // Release only video elements (shared images & fonts stay warm across cards)
      renderer.stopPlayback();
      for (const [, entry] of renderer.media) entry.release();
      renderer.media.clear();
      renderer.fontFaces.clear();
      renderer.key = null;
    }
  }

  // Non-native custom card (full image or background + portrait + foreground)
  const art = project?.native_art || {};
  const fullId = art.full?.asset_id || null;
  const bgId = art.background?.asset_id || app.background_image || null;
  const fgId = art.foreground?.asset_id || null;
  const portraitId = app.portrait || null;
  // Bare portrait without any card frame/background falls back to portrait view
  if (!fullId && !bgId && !fgId) return null;

  const [portrait, background, foreground, full] = await Promise.all([
    loadImg(portraitId),
    loadImg(bgId),
    loadImg(fgId),
    loadImg(fullId),
  ]);
  if (!full && !background && !foreground) return null;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, CARD_W, CARD_H);
  ctx.save();
  ctx.scale(CARD_W / 600, CARD_H / 860);
  const fit = (image, x = 0, y = 0, w = 600, h = 860, zoom = 1, dx = 0, dy = 0) => {
    const scale = Math.min(w / image.width, h / image.height) * (zoom || 1);
    ctx.drawImage(
      image,
      x + (w - image.width * scale) / 2 + (dx || 0),
      y + (h - image.height * scale) / 2 + (dy || 0),
      image.width * scale,
      image.height * scale,
    );
  };
  if (full && (!app.hide_card_ui || (!background && !portrait))) {
    fit(full);
  } else {
    if (background) fit(background);
    if (portrait && !app.hide_portrait) {
      fit(portrait, 0, background ? 70 : 0, 600, background ? 670 : 860, app.photo_zoom, app.photo_x, app.photo_y);
    }
    if (foreground) fit(foreground);
  }
  if (app.native_overlay_text && !app.hide_card_ui) {
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'left';
    ctx.font = '700 72px Arial';
    ctx.fillText(String(simulation?.ovr ?? ''), 35, 100);
    ctx.font = '24px Arial';
    ctx.fillText(String(project?.identity?.position || ''), 40, 132);
    ctx.font = '600 34px "Microsoft YaHei",Arial';
    ctx.fillText(String(project?.display_name || project?.identity?.name || ''), 35, 790, 530);
  }
  ctx.restore();

  if (!canvasHasPixels(canvas)) return null;
  return encodeCanvas(canvas);
}

function pumpQueue() {
  while (activeCount < MAX_CONCURRENT && queue.length) {
    const job = queue.shift();
    if (job.isCancelled()) continue;
    activeCount++;
    job.run().finally(() => {
      activeCount--;
      pumpQueue();
    });
  }
}

function enqueue(job) {
  queue.push(job);
  pumpQueue();
}

export class CardThumbController {
  constructor(scope, {api, catalog}) {
    this.scope = scope;
    this.api = api;
    this.catalog = catalog;
    this.generation = 0;
    this.observer = null;
    this.entries = new Map(); // element -> {key, loader, alt, onFallback}
  }

  cancel() {
    this.generation++;
    this.observer?.disconnect();
    this.observer = null;
    this.entries.clear();
  }

  applyImage(el, url, alt) {
    if (!el || !el.isConnected) return;
    el.classList.add('has-full-card');
    el.dataset.cardThumbState = 'ready';
    let img = el.querySelector('img.full-card-img');
    if (!img) {
      // Remove fallback portrait image or placeholder text while keeping hidden OVR slot untouched
      for (const child of [...el.children]) {
        if (child.tagName === 'IMG' || child.tagName === 'SPAN') child.remove();
      }
      img = document.createElement('img');
      img.className = 'full-card-img';
      img.decoding = 'async';
      el.prepend(img);
    }
    img.alt = alt || '球员完整卡面';
    if (img.src !== url) img.src = url;
  }

  observe(el, {key, serverUrl, alt, loadData, onFallback, eager = false}) {
    if (!el || !key) return;
    const cached = getCachedThumb(key, serverUrl);
    if (cached) {
      this.applyImage(el, cached, alt);
      return;
    }
    const gen = this.generation;
    const info = {key, alt, loadData, onFallback, gen};
    if (eager) {
      this.schedule(el, info);
      return;
    }
    this.entries.set(el, info);
    if (!this.observer) {
      this.observer = new IntersectionObserver(
        entries => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const target = entry.target;
            const item = this.entries.get(target);
            this.observer?.unobserve(target);
            this.entries.delete(target);
            if (!item || item.gen !== this.generation || !target.isConnected) continue;
            this.schedule(target, item);
          }
        },
        {rootMargin: '420px 0px', threshold: 0.01},
      );
    }
    this.observer.observe(el);
  }

  schedule(el, info) {
    const {key, alt, loadData, onFallback, gen} = info;
    const cached = cacheGet(key);
    if (cached) {
      if (gen === this.generation && el.isConnected) this.applyImage(el, cached, alt);
      return;
    }
    if (inFlight.has(key)) {
      inFlight.get(key).then(url => {
        if (gen !== this.generation || !el.isConnected) return;
        if (url) this.applyImage(el, url, alt);
        else onFallback?.(el);
      });
      return;
    }

    enqueue({
      isCancelled: () => gen !== this.generation || !el.isConnected,
      run: async () => {
        if (gen !== this.generation || !el.isConnected) return;
        let promise = inFlight.get(key);
        if (!promise) {
          promise = (async () => {
            try {
              const spec = await loadData();
              if (!spec) return null;
              if (spec.cachedUrl) {
                cacheSet(key, spec.cachedUrl);
                return spec.cachedUrl;
              }
              const dataUrl = await renderProjectToDataUrl(
                spec.project,
                spec.simulation,
                this.catalog,
                {requireCardLayers: Boolean(spec.requireCardLayers)},
              );
              if (!dataUrl) return null;
              cacheSet(key, dataUrl);
              // Persist to disk cache asynchronously so future visits load directly
              this.api('card-thumbs/save', {key, data: dataUrl})
                .then(res => { if (res?.url) cacheSet(key, res.url); })
                .catch(() => {});
              return dataUrl;
            } catch {
              return null;
            }
          })();
          inFlight.set(key, promise);
          promise.finally(() => inFlight.delete(key));
        }
        const url = await promise;
        if (gen !== this.generation || !el.isConnected) return;
        if (url) this.applyImage(el, url, alt);
        else onFallback?.(el);
      },
    });
  }
}

export function buildLibraryPreviewSpec(row, card) {
  if (!card || !card.layers?.length) return null;
  const pv = card.player_preview || {};
  const portraitLayer = card.layers.find(l => l.slot === 'portrait');
  const project = {
    display_name: pv.name || row.name || '',
    card_type: pv.card_type || row.card_type || card.card_type || 'Standard',
    booster_ids: pv.booster_ids || [],
    custom_boosters: [],
    identity: {
      name: pv.name || row.name || '',
      position: pv.position || row.position || '',
      team_id: pv.team_id ?? card.info?.team_id ?? 0,
      nation_id: pv.nation_id ?? card.info?.nation_id ?? 0,
    },
    appearance: {
      portrait: portraitLayer?.asset_id || null,
      portrait_fit: 'native',
      hide_portrait: false,
      hide_card_ui: false,
      native_overlay_text: true,
      photo_zoom: 1,
      photo_x: 0,
      photo_y: 0,
    },
    native_card: card,
  };
  const ovr = pv.overall ?? row.overall ?? '';
  return {project, simulation: {ovr}, requireCardLayers: true};
}
