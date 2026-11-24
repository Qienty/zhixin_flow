/* 无状态的小工具：转义与颜色。纯字符串处理，不碰 DOM，也不读主题。

   放在单独模块里，是为了让「什么算安全输出」集中在一个地方：
   所有拼进 innerHTML / style / href 的外部数据都要先过这里。 */

/* 映射表和正则提到模块级：esc 是列表渲染里的热点，
   原来每遇到一个待转义字符就新建一次对象字面量。 */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const ESC_RE = /[&<>"']/g;

export function esc(value) {
  return String(value ?? "").replace(ESC_RE, (c) => ESC_MAP[c]);
}

export function hexToRgb(hex) {
  const raw = String(hex || "#999999").replace("#", "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  const num = parseInt(full, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

/* 情绪色、强度色来自后端，最终会被拼进 style 属性或 CSS 变量。
   只认 #rgb / #rrggbb(aa)，其余一律退回中性灰，避免引号把属性闭合掉。 */
export function safeColor(value) {
  const text = String(value || "").trim();
  return /^#[0-9a-f]{3,8}$/i.test(text) ? text : "#8d948c";
}

/* 外部链接同样只认 https：数据来自后端，不能让它变成 javascript: 伪协议。 */
export function safeHref(value) {
  const text = String(value || "").trim();
  return /^https:\/\/[^\s"'<>]+$/i.test(text) ? esc(text) : "#";
}

/* 情绪色与强度色来自后端，是为浅底调的。深色下整体提亮、略降饱和，
   否则墨绿纸底换成暖炭底之后这些点会糊成一团。 */
export function brighten(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (l >= 0.6) return hex;
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = d === 0 ? 0 : Math.min(0.72, d / (1 - Math.abs(2 * l - 1)));
  return hslToHex(h, s, 0.64);
}

function hslToHex(h, s, l) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const seg = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][Math.floor(h / 60) % 6];
  return "#" + seg.map((v) => Math.round((v + m) * 255).toString(16).padStart(2, "0")).join("");
}
