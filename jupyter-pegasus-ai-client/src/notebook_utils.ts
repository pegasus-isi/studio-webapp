import { NotebookPanel } from '@jupyterlab/notebook';
import { ICodeCellModel } from '@jupyterlab/cells';

/**
 * Assigns problem numbers to code cells in the notebook.
 */
export function numberProblemCells(notebook: NotebookPanel) {
  const model = notebook.content.model;
  if (!model) {
    console.log('Notebook model missing');
    return;
  }

  const cells = model.cells;
  let counter = 1;

  for (const cellModel of cells) {
    if (cellModel.type !== 'code') continue;

    const problem = {
      index: counter,
      label: `question_${counter}`
    };

    cellModel.setMetadata('problem', problem);
    counter++;
  }
}

/**
 * Adds a loading markdown output to a code cell.
 */
export function startLoading(cell: any): { loadingIdx: number } | null {
  if (cell.model.type !== 'code') return null;
  const outputs = (cell.model as ICodeCellModel).outputs;

  const loadingIdx = outputs.length;
  outputs.add({
    output_type: 'display_data',
    data: { 'text/markdown': `⏳ **PegasusAI Bot is thinking...**` },
    metadata: {}
  });

  return { loadingIdx };
}

/**
 * Removes the loading markdown output from a code cell.
 */
export function stopLoading(cell: any, state: { loadingIdx: number } | null) {
  if (!state || cell.model.type !== 'code') return;

  const out = (cell.model as ICodeCellModel).outputs;

  if (state.loadingIdx < out.length) {
    const currentOutputs = [];
    for (let i = 0; i < out.length; i++) {
      currentOutputs.push(out.get(i).toJSON());
    }

    currentOutputs.splice(state.loadingIdx, 1);
    out.fromJSON(currentOutputs);
  }
}

/**
 * Extract displayable text from code cell outputs.
 */
export function extractOutputText(outputs: any[]): string {
  const textParts: string[] = [];
  for (const output of outputs) {
    if (output.output_type === 'stream' && output.text) {
      textParts.push(
        Array.isArray(output.text) ? output.text.join('') : output.text
      );
    } else if (
      (output.output_type === 'execute_result' ||
        output.output_type === 'display_data') &&
      output.data
    ) {
      const plain = output.data['text/plain'];
      if (plain) {
        textParts.push(Array.isArray(plain) ? plain.join('') : plain);
      }
    } else if (output.output_type === 'error') {
      textParts.push(`Error: ${output.ename}: ${output.evalue}`);
    }
  }
  return textParts.join('\n');
}

/**
 * Build a formatted string of all code cells in the notebook except the active cell.
 */
export function formatNotebookContext(notebook: any): string {
  const model = notebook.model;
  if (!model) return '';

  const parts: string[] = [];
  let counter = 1;

  for (const cm of model.cells) {
    if (cm.type !== 'code') continue;

    const source =
      typeof cm.sharedModel.getSource() === 'string'
        ? cm.sharedModel.getSource()
        : '';
    const outputs = (cm as any).outputs?.toJSON?.() ?? [];
    const outputText = extractOutputText(outputs);

    parts.push(
      `=== Cell ${counter} (code) ===\n${source}${outputText ? `\n=== Output ===\n${outputText}` : ''}`
    );
    counter++;
  }

  return parts.join('\n---\n');
}
