/**
 * Mela AI - <mela-chat> Web Component (Phase 6B)
 *
 * Standalone bundle external apps load with a single <script> tag:
 *
 *   <script src="https://armely-ai-api.azurewebsites.net/embed.bundle.js"></script>
 *   <mela-chat token="…" height="600px" theme="auto"></mela-chat>
 *
 * This file builds to ``embed.bundle.js`` via the script in
 * ``package.json`` (``npm run build:embed``).  See README for usage.
 *
 * Communication with the iframe happens ONLY through:
 *   - The iframe ``src`` URL (one-time, on attribute change)
 *   - postMessage frames:
 *       - host → iframe: { type: "mela-send", payload: { text } }
 *       - iframe → host: { type: "mela-response", payload: {...} }
 *
 * Standard web component isolation — never reaches into iframe DOM.
 */

interface MelaResponsePayload {
  content: string;
  conversationId: string | null;
}

const DEFAULT_EMBED_BASE = 'https://armely-ai-api.azurewebsites.net';
const ATTR_TOKEN = 'token';
const ATTR_HEIGHT = 'height';
const ATTR_WIDTH = 'width';
const ATTR_THEME = 'theme';
const ATTR_BASE_URL = 'base-url';

class MelaChatElement extends HTMLElement {
  private _iframe: HTMLIFrameElement | null = null;
  private _onMessageBound = this._onMessage.bind(this);

  static get observedAttributes(): string[] {
    return [ATTR_TOKEN, ATTR_HEIGHT, ATTR_WIDTH, ATTR_THEME, ATTR_BASE_URL];
  }

  connectedCallback(): void {
    if (!this._iframe) {
      this._iframe = document.createElement('iframe');
      this._iframe.style.border = 'none';
      this._iframe.allow = 'clipboard-read; clipboard-write';
      this._iframe.title = 'Mela Chat';
      this._applyDimensions();
      this.appendChild(this._iframe);
    }
    window.addEventListener('message', this._onMessageBound);
    this._refreshSrc();
  }

  disconnectedCallback(): void {
    window.removeEventListener('message', this._onMessageBound);
  }

  attributeChangedCallback(name: string): void {
    if (name === ATTR_HEIGHT || name === ATTR_WIDTH) {
      this._applyDimensions();
    } else {
      this._refreshSrc();
    }
  }

  /**
   * Public API — host pages call element.sendMessage("hello").
   * Forwards through postMessage; never touches iframe DOM.
   */
  sendMessage(text: string): void {
    if (!this._iframe || !this._iframe.contentWindow) return;
    this._iframe.contentWindow.postMessage(
      { type: 'mela-send', payload: { text } },
      '*',
    );
  }

  // ── Internals ──────────────────────────────────────────────────────

  private _applyDimensions(): void {
    if (!this._iframe) return;
    this._iframe.style.height =
      this.getAttribute(ATTR_HEIGHT) || '600px';
    this._iframe.style.width =
      this.getAttribute(ATTR_WIDTH) || '100%';
  }

  private _refreshSrc(): void {
    if (!this._iframe) return;
    const token = this.getAttribute(ATTR_TOKEN);
    if (!token) return;
    const base = (
      this.getAttribute(ATTR_BASE_URL) || DEFAULT_EMBED_BASE
    ).replace(/\/$/, '');
    const theme = this.getAttribute(ATTR_THEME);
    const params = new URLSearchParams({ token });
    if (theme) params.set('theme', theme);
    const next = `${base}/embed?${params.toString()}`;
    if (this._iframe.src !== next) {
      this._iframe.src = next;
    }
  }

  private _onMessage(ev: MessageEvent): void {
    if (!this._iframe || ev.source !== this._iframe.contentWindow) return;
    const data = ev.data as
      | { type?: string; payload?: MelaResponsePayload }
      | undefined;
    if (!data || data.type !== 'mela-response') return;
    this.dispatchEvent(
      new CustomEvent<MelaResponsePayload>('mela-response', {
        detail: data.payload as MelaResponsePayload,
        bubbles: true,
        composed: true,
      }),
    );
  }
}

if (typeof window !== 'undefined' && !customElements.get('mela-chat')) {
  customElements.define('mela-chat', MelaChatElement);
}

export {};
