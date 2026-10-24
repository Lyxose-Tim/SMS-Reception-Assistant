import { readEmbedConfig } from './embed';
import { startWidget, whenBodyReady } from './widget';

// document.currentScript is only set during this synchronous first execution.
const embed = readEmbedConfig(document.currentScript as HTMLScriptElement | null, document, location);
// Only the mounting is deferred (the snippet may be in <head>); the script lookup above is not.
if (embed) whenBodyReady(document, () => startWidget(embed));
