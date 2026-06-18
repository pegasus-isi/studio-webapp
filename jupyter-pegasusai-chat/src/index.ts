import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
  ILayoutRestorer
} from '@jupyterlab/application';
import { ICommandPalette, IFrame } from '@jupyterlab/apputils';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { ILauncher } from '@jupyterlab/launcher';
import { INotebookTracker } from '@jupyterlab/notebook';
import { LabIcon } from '@jupyterlab/ui-components';

// Monochrome chat glyph (currentColor) — LabIcon runs svgstr through
// decodeURIComponent, which rejects gradient/hex badges; a clean currentColor
// path matches Jupyter's sidebar convention and renders safely.
const pegasusSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16">
<path fill="currentColor" fill-rule="evenodd" clip-rule="evenodd" d="M4 2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2zm8.6 4.2l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1z"/>
</svg>`;

const pegasusIcon = new LabIcon({ name: 'pegasusai:logo', svgstr: pegasusSvg });

const COMMAND = 'pegasusai:open-chat';

/**
 * Derive the studio /chat URL from JupyterLab's base URL. Under KubeSpawner the
 * base is `/user/<name>/jupyter/`; the studio chat is a sibling at
 * `/user/<name>/chat`. So replace the trailing `jupyter/` with `chat`.
 */
function chatUrl(): string {
  const base = PageConfig.getBaseUrl(); // e.g. /user/alice/jupyter/
  if (/jupyter\/?$/.test(base)) {
    return base.replace(/jupyter\/?$/, 'chat');
  }
  // Fallback: sibling of the base path
  return URLExt.join(base, '..', 'chat');
}

/**
 * Docks the studio chat (/chat) as a right-sidebar iframe panel inside
 * JupyterLab. Same-origin, so the gateway session and /api calls work. Posts
 * the focused notebook path into the iframe so the chat is notebook-aware.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyter-pegasusai-chat:plugin',
  autoStart: true,
  optional: [ILauncher, ICommandPalette, ILayoutRestorer, INotebookTracker],
  activate: (
    app: JupyterFrontEnd,
    launcher: ILauncher | null,
    palette: ICommandPalette | null,
    restorer: ILayoutRestorer | null,
    notebooks: INotebookTracker | null
  ) => {
    let widget: IFrame | null = null;

    const iframeWindow = (): Window | null => {
      const el = widget?.node.querySelector('iframe') as HTMLIFrameElement | null;
      return el?.contentWindow ?? null;
    };

    const postActiveNotebook = () => {
      const win = iframeWindow();
      if (!win) {
        return;
      }
      const path = notebooks?.currentWidget?.context?.path ?? null;
      win.postMessage(
        { type: 'pegasusai:active-notebook', path },
        window.location.origin
      );
    };

    const ensureWidget = (): IFrame => {
      if (widget && !widget.isDisposed) {
        return widget;
      }
      const iframe = new IFrame({
        sandbox: [
          'allow-scripts',
          'allow-same-origin',
          'allow-forms',
          'allow-popups',
          'allow-downloads'
        ]
      });
      iframe.url = chatUrl();
      iframe.id = 'pegasusai-chat';
      iframe.title.label = 'PegasusAI Chat';
      iframe.title.icon = pegasusIcon;
      iframe.title.caption = 'PegasusAI workflow assistant';
      iframe.title.closable = false;
      iframe.disposed.connect(() => {
        widget = null;
      });
      widget = iframe;
      app.shell.add(iframe, 'right', { rank: 500 });
      return iframe;
    };

    app.commands.addCommand(COMMAND, {
      label: 'PegasusAI Chat',
      caption: 'Open the PegasusAI workflow assistant',
      icon: pegasusIcon,
      execute: () => {
        const w = ensureWidget();
        app.shell.activateById(w.id);
      }
    });

    if (launcher) {
      launcher.add({ command: COMMAND, category: 'Other', rank: 1 });
    }
    if (palette) {
      palette.addItem({ command: COMMAND, category: 'PegasusAI' });
    }

    notebooks?.currentChanged.connect(() => postActiveNotebook());
    window.addEventListener('message', (e: MessageEvent) => {
      if (e.origin !== window.location.origin) {
        return;
      }
      if (e.data?.type === 'pegasusai:ready') {
        postActiveNotebook();
      }
    });

    void Promise.resolve(restorer ? app.restored : null).then(() => {
      ensureWidget();
    });
  }
};

export default plugin;
