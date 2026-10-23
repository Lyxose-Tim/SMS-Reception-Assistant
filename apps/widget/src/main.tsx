import { readEmbedConfig } from './embed';
import { mountShadow } from './mount';

// Must be read synchronously during the initial execution of this script.
const embed = readEmbedConfig(document.currentScript as HTMLScriptElement | null, document, location);
if (embed) mountShadow(<p>Reception widget</p>);
