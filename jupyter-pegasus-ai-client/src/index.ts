import { JupyterFrontEndPlugin } from '@jupyterlab/application';

import { configPlugin } from './configPlugin';
import { toolbarPlugin } from './toolbarPlugin';

/**
 * Export the plugins as an array.
 * JupyterLab will automatically register all plugins in this array.
 */
const plugins: JupyterFrontEndPlugin<any>[] = [configPlugin, toolbarPlugin];

export default plugins;
