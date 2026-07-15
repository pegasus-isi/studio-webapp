import React, { useState } from 'react';
import { IPegasusAIConfig } from '../tokens';

interface IProps {
  config: IPegasusAIConfig;
}

export default function SettingsForm(props: IProps): JSX.Element {
  const { config } = props;
  const [serverUrl, setServerUrl] = useState(config.serverUrl);
  const [userId, setUserId] = useState(config.userId);
  const [hasSaved, setHasSaved] = useState(false);
  const [showSavedMsg, setShowSavedMsg] = useState(false);

  const handleSave = (): void => {
    config.setConfig(serverUrl.trim(), userId.trim());
    setHasSaved(true);
    setShowSavedMsg(true);
    setTimeout(() => setShowSavedMsg(false), 2000);
  };

  const handleClear = (): void => {
    config.clear();
    setServerUrl('');
    setUserId('');
    setHasSaved(false);
    setShowSavedMsg(false);
  };

  return (
    <div style={{ padding: '16px' }}>
      <h3>PegasusAI Configuration</h3>
      <p style={{ fontSize: '12px', color: '#666' }}>
        Configure your server connection and user credentials.
      </p>
      <div style={{ marginBottom: '12px' }}>
        <label
          style={{ display: 'block', marginBottom: '4px', fontWeight: '500' }}
        >
          Endpoint URL
        </label>
        <input
          type="text"
          value={serverUrl}
          onChange={e => setServerUrl(e.target.value)}
          placeholder="http://example.com:8080"
          style={{
            width: '100%',
            padding: '8px',
            border: '1px solid var(--jp-border-color1)'
          }}
        />
      </div>
      <div style={{ marginBottom: '16px' }}>
        <label
          style={{ display: 'block', marginBottom: '4px', fontWeight: '500' }}
        >
          User ID
        </label>
        <input
          type="text"
          value={userId}
          onChange={e => setUserId(e.target.value)}
          placeholder="Enter your user ID"
          style={{
            width: '100%',
            padding: '8px',
            border: '1px solid var(--jp-border-color1)'
          }}
        />
      </div>
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <button
          onClick={handleSave}
          className="jp-Button jp-mod-styled"
          disabled={hasSaved}
          style={{
            opacity: hasSaved ? 0.5 : 1,
            cursor: hasSaved ? 'not-allowed' : 'pointer'
          }}
        >
          Save
        </button>
        <button
          onClick={handleClear}
          className="jp-Button"
          disabled={!hasSaved}
          style={{
            opacity: !hasSaved ? 0.5 : 1,
            cursor: !hasSaved ? 'not-allowed' : 'pointer'
          }}
        >
          Clear
        </button>
      </div>
      {showSavedMsg && (
        <p style={{ color: 'var(--jp-success-color1)' }}>
          Configuration saved!
        </p>
      )}
      <div
        style={{
          marginTop: '12px',
          padding: '8px',
          background: config.isConfigured
            ? 'var(--jp-success-color3)'
            : 'var(--jp-error-color3)',
          borderRadius: '4px',
          color: config.isConfigured
            ? 'var(--jp-success-color0)'
            : 'var(--jp-error-color0)'
        }}
      >
        Status: {config.isConfigured ? 'Configured' : 'Not configured'}
      </div>
    </div>
  );
}
