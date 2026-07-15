import { Token } from '@lumino/coreutils';
import { ISignal } from '@lumino/signaling';

export interface IPegasusAIConfig {
  serverUrl: string;
  userId: string;
  isConfigured: boolean;
  readonly changed: ISignal<IPegasusAIConfig, void>;
  setConfig(url: string, user: string): void;
  clear(): void;
}

export const IPegasusAIConfig = new Token<IPegasusAIConfig>(
  '@gclab/pegasus-ai-client:IPegasusAIConfig'
);
