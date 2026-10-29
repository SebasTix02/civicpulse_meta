// On-phone photo check before sending (robustness test 2026-10-29: dark and blurred photos are
// where SAM and the classifier fail most). Only a warning: the citizen can send anyway.
// Thresholds from 150 anonymized pothole pair photos at 256 px: normal photos have mean
// luminance >= 112 and Laplacian variance >= 829 (5th percentile); a 35% exposure has mean <= 56,
// a Gaussian blur of sigma 4 has variance <= 100, and overexposure gives mean >= 203 or leaves
// >= 25% of pixels burnt (normal: mean <= 159, 1% burnt, 95th percentile).

export type PhotoIssue = "dark" | "bright" | "blurry";

const SIDE = 256;
const DARK_MEAN = 60;
const BRIGHT_MEAN = 200;
const BURNT_SHARE = 0.2;
const BLUR_VARIANCE = 150;

export async function photoIssue(file: File): Promise<PhotoIssue | null> {
  const bitmap = await createImageBitmap(file);
  const scale = SIDE / Math.max(bitmap.width, bitmap.height);
  const w = Math.max(3, Math.round(bitmap.width * scale));
  const h = Math.max(3, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0, w, h);
  const px = ctx.getImageData(0, 0, w, h).data;
  const y = new Float32Array(w * h);
  let sum = 0;
  let burnt = 0;
  for (let i = 0; i < w * h; i++) {
    y[i] = 0.299 * px[4 * i] + 0.587 * px[4 * i + 1] + 0.114 * px[4 * i + 2];
    sum += y[i];
    if (y[i] > 250) burnt++;
  }
  if (sum / (w * h) < DARK_MEAN) return "dark";
  if (sum / (w * h) > BRIGHT_MEAN || burnt / (w * h) > BURNT_SHARE) return "bright";
  // Variance of the 4-neighbour Laplacian: low when there are no sharp edges.
  let n = 0;
  let mean = 0;
  let m2 = 0;
  for (let r = 1; r < h - 1; r++) {
    for (let c = 1; c < w - 1; c++) {
      const i = r * w + c;
      const v = y[i - w] + y[i + w] + y[i - 1] + y[i + 1] - 4 * y[i];
      n++;
      const d = v - mean;
      mean += d / n;
      m2 += d * (v - mean);
    }
  }
  return m2 / n < BLUR_VARIANCE ? "blurry" : null;
}

// Longest side 1600 px, re-encoded through a canvas: this also drops EXIF (GPS, device).
export async function resizeToJpeg(file: File): Promise<{ blob: Blob; width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, width, height);
  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode_failed"))), "image/jpeg", 0.85),
  );
  return { blob, width, height };
}
