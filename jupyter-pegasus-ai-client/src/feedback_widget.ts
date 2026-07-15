import { Widget } from '@lumino/widgets';
import { IRenderMime } from '@jupyterlab/rendermime-interfaces';
import { sendFeedbackRequest } from './api';

export const LIKERT_MIME = 'application/vnd.pegasusai.likert-clarity' as const;
const LIKERT_QUESTION =
  'Did PegasusAI bot help you accomplish what you wanted?';
const LIKERT_SCALE = ['Yes', 'Somewhat', 'No'] as const;

export class LikertWidget extends Widget implements IRenderMime.IRenderer {
  private submitted = false;
  private selectedValue: number | null = null;
  private selectedLabel: string | null = null;
  private fallbackServerUrl: string;

  constructor(fallbackServerUrl: string) {
    super();
    this.fallbackServerUrl = fallbackServerUrl;
  }

  async renderModel(model: IRenderMime.IMimeModel): Promise<void> {
    this.node.textContent = '';

    const meta = (model.metadata ?? {}) as Record<string, any>;
    // TODO compare fallbackServerUrl to config.serverUrl
    const serverUrl = meta.server_url ?? this.fallbackServerUrl;
    const requestId = meta.request_id ?? null;
    const cellId = meta.cell_id ?? null;
    const problemLabel = meta.problem_label ?? null;
    const latencyMs =
      typeof meta.latency_ms === 'number' ? meta.latency_ms : null;

    // Reset UI state each render
    this.submitted = false;
    this.selectedValue = null;
    this.selectedLabel = null;

    const container = document.createElement('div');
    container.style.marginTop = '10px';
    container.style.padding = '10px';
    container.style.border = '1px solid var(--jp-border-color2)';
    container.style.borderRadius = '10px';

    const heading = document.createElement('div');
    heading.textContent = 'PegasusAI Feedback';
    heading.style.fontWeight = '600';
    heading.style.marginBottom = '8px';
    heading.style.display = 'flex';

    const question = document.createElement('div');
    question.textContent = LIKERT_QUESTION;
    question.style.fontWeight = '600';
    question.style.marginBottom = '8px';

    const group = document.createElement('div');
    group.setAttribute('role', 'radiogroup');
    group.style.marginBottom = '10px';

    const radioName = `likert-${Math.random().toString(16).slice(2)}`;
    const radios: HTMLInputElement[] = [];
    const status = document.createElement('span');
    status.style.fontSize = '12px';

    const btn = document.createElement('button');
    btn.textContent = 'Submit';
    btn.className = 'jp-Button jp-mod-styled';
    btn.disabled = true;

    const close_btn = document.createElement('button');
    close_btn.textContent = 'X';
    close_btn.className = 'jp-Button';
    close_btn.style.top = '20px';
    close_btn.style.right = '20px';

    LIKERT_SCALE.forEach((label, idx) => {
      const row = document.createElement('label');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.gap = '8px';
      row.style.margin = '6px 0';

      const input = document.createElement('input');
      input.type = 'radio';
      input.name = radioName;
      input.value = String(idx + 1);

      input.addEventListener('change', () => {
        if (this.submitted) return;
        this.selectedValue = idx + 1;
        this.selectedLabel = label;
        btn.disabled = false;
        status.textContent = '';
      });

      radios.push(input);
      const span = document.createElement('span');
      span.textContent = label;

      row.appendChild(input);
      row.appendChild(span);
      group.appendChild(row);
    });

    close_btn.onclick = async () => {
      this.node.textContent = '';
    };

    btn.onclick = async () => {
      if (
        this.submitted ||
        this.selectedValue === null ||
        this.selectedLabel === null
      )
        return;

      // Lock the button to prevent multiple submits
      this.submitted = true;
      btn.disabled = true;
      btn.textContent = 'Sending…';
      radios.forEach(r => (r.disabled = true));
      status.textContent = '';

      try {
        await sendFeedbackRequest(serverUrl, {
          request_id: requestId,
          cell_id: cellId,
          problem_label: problemLabel,
          latency_ms: latencyMs,
          ts_ms: Date.now(),
          value: this.selectedValue,
          label: this.selectedLabel
        });

        btn.textContent = 'Submitted ✓';
        status.textContent = 'Thanks!';
      } catch (e) {
        btn.textContent = 'Submitted (offline)';
        status.textContent = 'Could not reach server.';
        console.warn('Likert feedback POST failed:', e);
      }
    };

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.gap = '10px';
    row.style.alignItems = 'center';
    row.style.marginTop = '12px';
    row.appendChild(btn);
    row.appendChild(status);

    heading.appendChild(close_btn);
    container.appendChild(heading);
    container.appendChild(question);
    container.appendChild(group);
    container.appendChild(row);

    this.node.appendChild(container);
  }
}
