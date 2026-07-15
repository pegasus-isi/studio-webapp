// Studio modification of the vendored @gclab/pegasus-ai-client
// configPlugin (see ../VENDORED.md for source + ref).
//
// Only change vs upstream: on activation, auto-configure so users don't fill
// the settings form. Values are resolved WITHOUT relying on the JupyterLab
// process environment:
//   * server URL -> the same-origin studio-api proxy (/api/pegasus-bot),
//     derived from this page's own JupyterLab base URL.
//   * user       -> studio-api /api/whoami, which resolves the user from the
//     gateway auth header (works even when JUPYTERHUB_USER isn't in the
//     JupyterLab env — the reason PageConfig injection could come back empty).
// Server-injected PageConfig (pegasusBotServerUrl / pegasusBotUserId) is
// honored as an override when present; otherwise we fall back to the manual
// SettingsForm.
import React from 'react';
import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ReactWidget } from '@jupyterlab/apputils';
import { PageConfig } from '@jupyterlab/coreutils';
import { Signal } from '@lumino/signaling';

import { IPegasusAIConfig } from './tokens';
import SettingsForm from './components/SettingsForm';
import { settingsIcon } from './icons';

// Config for credentials
class PegasusAIConfigImpl implements IPegasusAIConfig {
  private _serverUrl = '';
  private _userId = '';
  private _isConfigured = false;
  private _changed = new Signal<this, void>(this);

  get serverUrl(): string {
    return this._serverUrl;
  }
  get userId(): string {
    return this._userId;
  }
  get isConfigured(): boolean {
    return this._isConfigured;
  }
  get changed(): Signal<this, void> {
    return this._changed;
  }

  setConfig(url: string, user: string): void {
    this._serverUrl = url;
    this._userId = user;
    this._isConfigured = !!(url && user);
    this._changed.emit(undefined);
  }

  clear(): void {
    this._serverUrl = '';
    this._userId = '';
    this._isConfigured = false;
    this._changed.emit(undefined);
  }
}

export const configPlugin: JupyterFrontEndPlugin<IPegasusAIConfig> = {
  id: '@gclab/pegasus-ai-client:config-plugin',
  description: 'PegasusAI shared configuration',
  autoStart: true,
  provides: IPegasusAIConfig,
  activate: (app: JupyterFrontEnd): IPegasusAIConfig => {
    const config = new PegasusAIConfigImpl();

    // Seed asynchronously (whoami is a fetch). The toolbar reads the config at
    // click time and listens to config.changed, so returning the config now
    // and configuring shortly after is fine.
    void autoConfigure(config);

    const settingsWidget = ReactWidget.create(<SettingsForm config={config} />);
    settingsWidget.id = 'pegasusai-shared-configs';
    settingsWidget.title.label = 'PegasusAI Settings';
    settingsWidget.title.icon = settingsIcon;

    app.shell.add(settingsWidget, 'left', { rank: 200 });

    return config;
  }
};

// studio-api base derived from JupyterLab's own base URL:
//   <prefix>/jupyter/  ->  <prefix>/api
// so the calls are same-origin and prefix-aware under JupyterHub.
function studioApiBase(): string {
  const base = PageConfig.getBaseUrl(); // ends with '/jupyter/'
  return base.replace(/jupyter\/?$/, 'api');
}

async function autoConfigure(config: PegasusAIConfigImpl): Promise<void> {
  const apiBase = studioApiBase();

  // Server URL: PageConfig override, else the same-origin proxy path.
  const url =
    PageConfig.getOption('pegasusBotServerUrl') || `${apiBase}/pegasus-bot`;

  // User: PageConfig override, else resolve from studio-api whoami (which
  // reads the gateway auth header, not just the process env).
  let user = PageConfig.getOption('pegasusBotUserId');
  if (!user) {
    try {
      const res = await fetch(`${apiBase}/whoami`, {
        headers: { Accept: 'application/json' }
      });
      if (res.ok) {
        user = ((await res.json()).user as string | null) || '';
      }
    } catch {
      // leave unconfigured; the manual SettingsForm remains available
    }
  }

  if (url && user) {
    config.setConfig(url, user);
  }
}
