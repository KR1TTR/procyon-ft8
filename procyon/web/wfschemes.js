// Waterfall colour schemes (Settings → Themes; chosen per device, saved as
// localStorage "procyon.wfScheme"). Shared by the main page and the JS8 chat
// screen, so both waterfalls use the same colours.
'use strict';
// Waterfall colour schemes (Settings → Themes; chosen per device). Each is
// a ramp of [position 0-1, R, G, B] stops from the noise floor up to the
// strongest signals.
var WF_SCHEMES = {
  lightning: [[0, 5, 8, 22], [.22, 14, 30, 82], [.42, 26, 88, 170], [.6, 40, 175, 205],
              [.76, 150, 232, 170], [.9, 255, 226, 122], [1, 255, 255, 255]],
  classic:   [[0, 3, 6, 24], [.25, 6, 14, 48], [.45, 14, 32, 92], [.62, 60, 80, 110],
              [.75, 220, 195, 40], [.9, 255, 236, 90], [1, 255, 252, 215]],
  tubeglow:  [[0, 9, 7, 4], [.25, 26, 18, 7], [.42, 62, 42, 12], [.58, 132, 90, 22],
              [.72, 206, 148, 46], [.86, 250, 204, 104], [1, 255, 242, 204]],
  ember:     [[0, 8, 4, 3], [.25, 26, 9, 4], [.42, 62, 18, 5], [.58, 128, 40, 6],
              [.72, 205, 82, 14], [.86, 255, 150, 48], [1, 255, 226, 170]],
  surf:      [[0, 2, 14, 26], [.25, 4, 30, 52], [.42, 6, 70, 100], [.56, 0, 130, 170],
              [.7, 30, 190, 210], [.84, 120, 235, 230], [.95, 230, 255, 250], [1, 255, 255, 255]],
};

function makeLut(stops) {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    let k = 0;
    while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
    const [a, b] = [stops[k], stops[k + 1]];
    const t = (v - a[0]) / (b[0] - a[0]);
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * t;
  }
  return lut;
}
