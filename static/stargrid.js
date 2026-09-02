/**
 * StarGrid — A lightweight animated background for TrendAnalyzer.
 * Pure Canvas 2D. No external dependencies. No WebGL.
 *
 * Renders:
 *  - A faint dot-grid that drifts slowly
 *  - Sparse "shooting" horizontal data lines (like a scrolling ticker feed)
 *  - A subtle radial vignette from the canvas edges
 */

export function initStarGrid(canvas, opts = {}) {
  const {
    dotColor       = 'rgba(56, 189, 248, 0.12)',
    lineColor      = 'rgba(56, 189, 248, 0.18)',
    bgColor        = '#050b14',
    dotSpacing     = 44,
    dotRadius      = 1.1,
    numLines       = 6,
    speed          = 0.4,
  } = opts;

  const ctx = canvas.getContext('2d');
  let W, H, animId;
  let t = 0;

  // Lines state
  const lines = Array.from({ length: numLines }, (_, i) => createLine(i, numLines));

  function createLine(index, total) {
    return {
      y: (canvas.height / total) * index + Math.random() * 80 - 40,
      width: 60 + Math.random() * 180,
      x: Math.random() * (canvas.width + 400) - 200,
      speed: 0.35 + Math.random() * 0.55,
      alpha: 0.06 + Math.random() * 0.12,
      thickness: 1 + Math.random() * 1.2,
    };
  }

  function resize() {
    W = canvas.width  = canvas.offsetWidth  || window.innerWidth;
    H = canvas.height = canvas.offsetHeight || window.innerHeight;
    // Reinit line positions after resize
    lines.forEach((l, i) => {
      l.y = (H / numLines) * i + Math.random() * 80 - 40;
      if (l.x > W + 300) l.x = -l.width;
    });
  }

  function drawBackground() {
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, W, H);
  }

  function drawDotGrid(time) {
    const driftX = Math.sin(time * 0.00008) * 6;
    const driftY = Math.cos(time * 0.00006) * 6;

    ctx.fillStyle = dotColor;
    const startX = ((driftX % dotSpacing) + dotSpacing) % dotSpacing;
    const startY = ((driftY % dotSpacing) + dotSpacing) % dotSpacing;

    for (let x = startX; x < W + dotSpacing; x += dotSpacing) {
      for (let y = startY; y < H + dotSpacing; y += dotSpacing) {
        // Slight pulse per dot based on time + position
        const pulse = 0.7 + 0.3 * Math.sin(time * 0.0003 + x * 0.05 + y * 0.03);
        ctx.globalAlpha = pulse;
        ctx.beginPath();
        ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }

  function drawLines(dt) {
    lines.forEach((line, i) => {
      // Advance
      line.x += line.speed * dt * speed;
      if (line.x > W + line.width + 100) {
        // Reset off-screen left
        line.x = -line.width - 50;
        line.y = Math.random() * H;
        line.width = 60 + Math.random() * 180;
        line.alpha = 0.06 + Math.random() * 0.12;
        line.speed = 0.35 + Math.random() * 0.55;
        line.thickness = 1 + Math.random() * 1.2;
      }

      // Draw a glowing horizontal line segment
      const grad = ctx.createLinearGradient(line.x, 0, line.x + line.width, 0);
      grad.addColorStop(0,   'transparent');
      grad.addColorStop(0.3, lineColor.replace(/[\d.]+\)$/, `${line.alpha})`));
      grad.addColorStop(0.7, lineColor.replace(/[\d.]+\)$/, `${line.alpha * 1.6})`));
      grad.addColorStop(1,   'transparent');

      ctx.beginPath();
      ctx.strokeStyle = grad;
      ctx.lineWidth   = line.thickness;
      ctx.globalAlpha = 1;
      ctx.moveTo(line.x, line.y);
      ctx.lineTo(line.x + line.width, line.y);
      ctx.stroke();
    });
  }

  function drawVignette() {
    const grad = ctx.createRadialGradient(W / 2, H / 2, H * 0.3, W / 2, H / 2, H * 0.9);
    grad.addColorStop(0,   'transparent');
    grad.addColorStop(1,   'rgba(5, 11, 20, 0.55)');
    ctx.fillStyle = grad;
    ctx.globalAlpha = 1;
    ctx.fillRect(0, 0, W, H);
  }

  let last = 0;
  function frame(now) {
    const dt = Math.min(now - last, 64); // cap at ~15fps minimum
    last = now;
    t = now;

    drawBackground();
    drawDotGrid(t);
    drawLines(dt);
    drawVignette();

    animId = requestAnimationFrame(frame);
  }

  function start() {
    resize();
    window.addEventListener('resize', resize);
    animId = requestAnimationFrame(frame);
  }

  function destroy() {
    cancelAnimationFrame(animId);
    window.removeEventListener('resize', resize);
  }

  // Respect prefers-reduced-motion
  const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (mediaQuery.matches) {
    // Static version — just draw once
    resize();
    drawBackground();
    drawDotGrid(0);
    drawVignette();
  } else {
    start();
  }

  return { destroy };
}
