import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const LIMIT = 40 * 1024;
const dist = new URL('../dist/', import.meta.url);
const files = readdirSync(dist);
if (files.length !== 1 || files[0] !== 'widget.js') {
  console.error(`Expected exactly dist/widget.js, found: ${files.join(', ') || '(nothing)'}`);
  process.exit(1);
}
const code = readFileSync(new URL('widget.js', dist));
const text = code.toString('utf8');
if (/\bprocess\.env\b/.test(text)) {
  console.error('dist/widget.js references process.env (it would throw in browsers)');
  process.exit(1);
}
if (/\bimport\s*\(|\brequire\s*\(|^\s*import\s/m.test(text)) {
  console.error('dist/widget.js contains module imports; everything must be bundled');
  process.exit(1);
}
const gz = gzipSync(code).length;
console.log(`widget.js: ${code.length} B raw, ${gz} B gzip (limit ${LIMIT} B)`);
if (gz > LIMIT) {
  console.error('widget.js exceeds the gzip size budget');
  process.exit(1);
}
