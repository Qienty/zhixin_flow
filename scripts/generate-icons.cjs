/* Developer utility: npm-installed sharp or SHARP_MODULE pointing to a bundled copy.
 *
 * 生成两类图标：
 *   icon-*.png            「any」：满幅设计，浏览器标签页、通知、iOS 主屏用；
 *   icon-maskable-*.png   「maskable」：设计缩到 76% 并居中在品牌底色上，
 *                          Android 主屏会按自己的形状（圆/方/水滴）裁剪它，
 *                          满幅版本被裁会切掉心形和流线的边缘，所以必须留安全区。
 * 改 icon.svg 之后跑一次本脚本，两个变体会一起更新。
 */
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require(process.env.SHARP_MODULE || 'sharp');

const BRAND = '#3E5B4B';
// Android maskable 安全区：内容应落在中心 80% 直径圆内，取 76% 更稳。
const MASKABLE_SCALE = 0.76;

(async () => {
  const frontend = path.resolve(__dirname, '../frontend');
  const source = await fs.readFile(path.join(frontend, 'icon.svg'));

  for (const [file, size] of [['icon-192.png', 192], ['icon-512.png', 512], ['apple-touch-icon-180.png', 180]]) {
    // Opaque corners allow operating systems to apply their own icon masks.
    await sharp(source, { density: 576 }).resize(size, size).flatten({ background: BRAND }).png().toFile(path.join(frontend, file));
    console.log(`Generated ${file} (${size} x ${size})`);
  }

  for (const size of [192, 512]) {
    const inner = Math.round(size * MASKABLE_SCALE);
    const art = await sharp(source, { density: 576 }).resize(inner, inner).flatten({ background: BRAND }).png().toBuffer();
    const offset = Math.round((size - inner) / 2);
    await sharp({ create: { width: size, height: size, channels: 4, background: BRAND } })
      .composite([{ input: art, top: offset, left: offset }])
      .png()
      .toFile(path.join(frontend, `icon-maskable-${size}.png`));
    console.log(`Generated icon-maskable-${size}.png (${size} x ${size}, art at ${Math.round(MASKABLE_SCALE * 100)}%)`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
