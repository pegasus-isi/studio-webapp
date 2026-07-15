export interface ISearchPayload {
  request_id: string;
  user?: string | null;
  cell_id?: string | null;
  cell_type?: 'code' | 'markdown' | 'raw' | null;
  source?: string | null;
  outputs?: any[] | null;
  problem_index?: number | null;
  problem_label?: string | null;
  notebook_context?: string | null;
}

export interface IBaseResponse {
  status: string;
  status_code: number;
  response: string;
}

export interface ISearchResponse extends IBaseResponse {
  provider: string;
  model: string;
}

export interface IFeedbackPayload {
  request_id: string;
  cell_id: string;
  value?: number | null;
  label?: string | null; // e.g., 'Yes' | 'Somewhat' | 'No'
  problem_label?: string | null;
  latency_ms?: number | null;
  ts_ms?: number | null;
}

/**
 * Sends code cell data to the PegasusAI server.
 * @param serverUrl The URL to the PegasusAI server.
 * @param payload The payload to send to the PegasusAI server.
 * @returns A future promise for the server response.
 */
export async function sendSearchRequest(
  serverUrl: string,
  payload: ISearchPayload
): Promise<ISearchResponse> {
  const response = await fetch(`${serverUrl}/search`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Access-Controll-Allow-Origin': '*'
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    let serverErrorMessage = 'Unknown server error';
    try {
      const errorData = await response.json();
      serverErrorMessage = errorData.detail || JSON.stringify(errorData);
    } catch (_e) {
      // Fallback to the default error message
    }
    throw { status: response.status, message: serverErrorMessage };
  }
  return response.json();
}

/**
 * Send feedback data to the PegasusAI server.
 * @param serverUrl The URL to the PegasusAI server.
 * @param payload The payload to send to the PegasusAI server.
 */
export async function sendFeedbackRequest(
  serverUrl: string,
  payload: IFeedbackPayload
): Promise<void> {
  const response = await fetch(`${serverUrl}/feedback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*'
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
}
