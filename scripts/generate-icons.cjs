/* Developer utility: npm-installed sharp or SHARP_MODULE pointing to a bundled copy. */
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require(process.env.SHARP_MODULE || 'sharp');

(async () => {
  const frontend = path.resolve(__dirname, '../frontend');
  const source = await fs.readFile(path.join(frontend, 'icon.svg'));
  for (const [file, size] of [['icon-192.png', 192], ['icon-512.png', 512], ['apple-touch-icon-180.png', 180]]) {
    // Opaque corners allow operating systems to apply their own icon masks.
    await sharp(source, { density: 576 }).resize(size, size).flatten({ background: '#3E5B4B' }).png().toFile(path.join(frontend, file));
    console.log(`Generated ${file} (${size} x ${size})`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
