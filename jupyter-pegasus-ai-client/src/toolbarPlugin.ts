import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { INotebookTracker, NotebookPanel } from '@jupyterlab/notebook';
import { markdownIcon } from '@jupyterlab/ui-components';
import { ICodeCellModel } from '@jupyterlab/cells';
import { IRenderMimeRegistry } from '@jupyterlab/rendermime';
import { IRenderMime } from '@jupyterlab/rendermime-interfaces';

import { IPegasusAIConfig } from './tokens';
import { pegasusaiIcon } from './icons';
import { sendSearchRequest, ISearchPayload } from './api';
import {
  numberProblemCells,
  startLoading,
  stopLoading,
  formatNotebookContext
} from './notebook_utils';
import { LikertWidget, LIKERT_MIME } from './feedback_widget';

import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { IToolbarWidgetRegistry } from '@jupyterlab/apputils';

const CommandIds = {
  /**
   * Command to render a markdown cell.
   */
  renderMarkdownCell: 'toolbar-button:render-markdown-cell',
  /**
   * Command to run a code cell.
   */
  runCodeCell: 'toolbar-button:run-code-cell'
};

let buttonCooldown = false;

/**
 * Create the front-end plugin defining the cell-toolbar extension
 */
export const toolbarPlugin: JupyterFrontEndPlugin<void> = {
  id: '@gclab/pegasus-ai-client:toolbar-plugin',
  description:
    'A cell-toolbar extension for Jupyter Lab to request help from the PegasusAI Bot.',
  autoStart: true,
  requires: [INotebookTracker, IRenderMimeRegistry, IPegasusAIConfig],
  optional: [ISettingRegistry, IToolbarWidgetRegistry],
  activate: (
    app: JupyterFrontEnd,
    tracker: INotebookTracker,
    rendermime: IRenderMimeRegistry,
    config: IPegasusAIConfig,
    settingsRegistry?: ISettingRegistry,
    toolbarRegistry?: IToolbarWidgetRegistry
  ) => {
    const { commands } = app;

    // Register custom MIME renderer for Likert scale
    rendermime.addFactory(
      {
        safe: true,
        mimeTypes: [LIKERT_MIME],
        createRenderer: (options: IRenderMime.IRendererOptions) => {
          return new LikertWidget(config.serverUrl);
        }
      },
      0
    );

    // Number cells when notebook is opened
    tracker.widgetAdded.connect((_, panel) => {
      const notebook = panel as NotebookPanel;
      notebook.revealed.then(() => {
        numberProblemCells(notebook);
      });
    });

    // Command: Run Code Cell with PegasusAI
    commands.addCommand(CommandIds.runCodeCell, {
      icon: pegasusaiIcon,
      caption: 'Run with PegasusAI Bot',
      execute: async () => {
        if (buttonCooldown) return;
        buttonCooldown = true;
        commands.notifyCommandChanged(CommandIds.runCodeCell);

        await commands.execute('notebook:run-cell');

        const current = tracker.currentWidget;
        if (!current) return;

        if (!config.isConfigured) {
          const cell = current.content.activeCell;
          if (!cell) {
            buttonCooldown = false;
            return;
          }
          const out = (cell.model as ICodeCellModel).outputs;
          out.add({
            output_type: 'display_data',
            data: {
              'text/markdown':
                '⚠️ **PegasusAI not configured.** Open the PegasusAI Settings panel and configure your credentials.'
            },
            metadata: {}
          });
          buttonCooldown = false;
          commands.notifyCommandChanged(CommandIds.runCodeCell);
          return;
        }

        const notebook = current.content;
        const cell = notebook.activeCell;
        if (!cell) return;

        const rawSource = cell.model.sharedModel.getSource();
        const source = typeof rawSource === 'string' ? rawSource : '';
        const cellID = cell.model.id;
        const outputs =
          cell.model.type === 'code'
            ? (cell.model as any).outputs.toJSON()
            : [];
        const problem = cell.model.getMetadata('problem') as
          | { index: number; label: string }
          | undefined;

        const requestId =
          crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
        const t0 = performance.now();

        const notebookContext = formatNotebookContext(notebook);

        const cellData = {
          user: config.userId,
          request_id: requestId,
          cell_type: cell.model.type as ISearchPayload['cell_type'],
          cell_id: cellID,
          source,
          outputs,
          problem_index: problem?.index,
          problem_label: problem?.label,
          notebook_context: notebookContext
        };

        const loadingState = startLoading(cell);

        try {
          const data = await sendSearchRequest(config.serverUrl, cellData);
          const latencyMs = Math.round(performance.now() - t0);

          stopLoading(cell, loadingState);
          if (buttonCooldown) {
            buttonCooldown = false;
            commands.notifyCommandChanged(CommandIds.runCodeCell);
          }

          const responseText = data.response ?? 'No model response';
          const out = (cell.model as ICodeCellModel).outputs;

          // Display AI Response
          out.add({
            output_type: 'display_data',
            data: {
              'text/markdown': `### PegasusAI Bot Response\n\n${responseText}`
            },
            metadata: {}
          });

          // Display Likert Feedback Widget
          (out as any).add({
            output_type: 'display_data',
            data: { [LIKERT_MIME]: {} },
            metadata: {
              server_url: config.serverUrl,
              request_id: requestId,
              cell_id: cellID,
              problem_label: problem?.label,
              latency_ms: latencyMs
            }
          });
        } catch (err: any) {
          stopLoading(cell, loadingState);
          const out = (cell.model as ICodeCellModel).outputs;

          let displayMessage = `**PegasusAI Bot is currently offline and cannot process requests.**`;
          if (err.status === 403) {
            displayMessage = err.message;
          }

          out.add({
            output_type: 'display_data',
            data: { 'text/markdown': displayMessage },
            metadata: {}
          });

          console.error('Error sending content to server: ', err);

          if (buttonCooldown) {
            buttonCooldown = false;
            commands.notifyCommandChanged(CommandIds.runCodeCell);
          }
        }
      },
      isEnabled: () =>
        !buttonCooldown && tracker.activeCell?.model.type === 'code',
      isVisible: () => tracker.activeCell?.model.type === 'code'
    });

    // Command: Render Markdown Cell
    commands.addCommand(CommandIds.renderMarkdownCell, {
      icon: markdownIcon,
      caption: 'Render a markdown cell',
      execute: () => {
        commands.execute('notebook:run-cell');
      },
      isVisible: () => tracker.activeCell?.model.type === 'markdown'
    });

    const registerToolbarItems = async () => {
      try {
        const cellToolbarPluginId = '@jupyterlab/cell-toolbar-extension:plugin';
        const settings = await settingsRegistry!.load(cellToolbarPluginId);
        const toolbar: any[] =
          (settings.get('toolbar').composite as any[]) ?? [];
        const ourItems = [
          {
            name: 'run-code-cell',
            command: 'toolbar-button:run-code-cell',
            rank: 0
          },
          {
            name: 'render-markdown-cell',
            command: 'toolbar-button:render-markdown-cell',
            rank: 0
          }
        ];
        let changed = false;
        for (const item of ourItems) {
          if (!toolbar.some(t => t.name === item.name)) {
            toolbar.push(item);
            changed = true;
          }
        }
        if (changed) {
          await settings.set('toolbar', toolbar);
        }
      } catch (err) {
        console.warn('Failed to register cell toolbar items:', err);
      }
    };
    if (settingsRegistry) {
      app.restored.then(registerToolbarItems);
    }
  }
};
