// Studio modification of the vendored @gclab/pegasus-ai-client
// configPlugin (see ../VENDORED.md for source + ref).
//
// Only change vs upstream: on activation, seed the shared config from
// server-injected PageConfig so the deployment sets the bot server URL and
// the user is taken from the container environment — users don't fill the
// form manually. Falls back to the manual SettingsForm when the options are
// absent (e.g. running the extension outside Studio).
//
// The server injects these via docker/jupyter/jupyter_server_config.py
// (c.LabApp.page_config_data): pegasusBotServerUrl + pegasusBotUserId.
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

    // Studio auto-configuration: server URL is fixed by the deployment and
    // the user comes from the container environment, both injected as
    // PageConfig by the Jupyter server. When present, configure silently so
    // the cell-toolbar button works without any manual setup.
    const injectedUrl = PageConfig.getOption('pegasusBotServerUrl');
    const injectedUser = PageConfig.getOption('pegasusBotUserId');
    if (injectedUrl && injectedUser) {
      config.setConfig(injectedUrl, injectedUser);
    }

    const settingsWidget = ReactWidget.create(<SettingsForm config={config} />);
    settingsWidget.id = 'pegasusai-shared-configs';
    settingsWidget.title.label = 'PegasusAI Settings';
    settingsWidget.title.icon = settingsIcon;

    app.shell.add(settingsWidget, 'left', { rank: 200 });

    return config;
  }
};
