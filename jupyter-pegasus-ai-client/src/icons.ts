import { LabIcon } from '@jupyterlab/ui-components';
import pegasusaiSvgStr from '../style/icons/chatbot-icon.svg';
import settingsSvgStr from '../style/icons/settings.svg';

export const pegasusaiIcon = new LabIcon({
  name: 'pegasus-ai-client:pegasusai',
  svgstr: pegasusaiSvgStr
});

export const settingsIcon = new LabIcon({
  name: 'pegasus-ai-client:settings',
  svgstr: settingsSvgStr
});
