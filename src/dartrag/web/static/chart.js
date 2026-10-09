"use strict";

// 의존성 없는 작은 SVG 차트 (막대, 꺾은선). 색은 CSS 변수 --series-1.. 로 받는다.
const Chart = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const H = 240;
  const M = { top: 16, right: 16, bottom: 28, left: 60 };

  function s(tag, attrs = {}, ...children) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== undefined && v !== null) node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c !== null && c !== undefined) node.append(c instanceof Node ? c : String(c));
    }
    return node;
  }

  function niceStep(span, count) {
    const raw = span / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const norm = raw / mag;
    return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  }

  // 0 을 꼭 포함하는 눈금
  function ticks(values, count = 4) {
    const nums = values.filter((v) => v !== null && v !== undefined);
    let lo = Math.min(0, ...nums);
    let hi = Math.max(0, ...nums);
    if (lo === hi) hi = lo + 1;
    const step = niceStep(hi - lo, count);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    const out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v / step) * step);
    return out;
  }

  function wonShort(v) {
    if (v === null || v === undefined) return "-";
    const a = Math.abs(v);
    const sign = v < 0 ? "-" : "";
    if (a >= 1e12) return `${sign}${(a / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조`;
    if (a >= 1e8) return `${sign}${Math.round(a / 1e8).toLocaleString("ko-KR")}억`;
    if (a === 0) return "0";
    return `${sign}${a.toLocaleString("ko-KR")}원`;
  }

  function pct(v, digits = 1) {
    return v === null || v === undefined ? "-" : `${v.toFixed(digits)}%`;
  }

  // 기준선에서 먼 쪽 모서리만 둥근 막대
  function barPath(x, yBase, yEnd, w) {
    const h = Math.abs(yEnd - yBase);
    const r = Math.min(4, w / 2, h);
    if (yEnd <= yBase) {
      return `M${x},${yBase}V${yEnd + r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd + r}V${yBase}Z`;
    }
    return `M${x},${yBase}V${yEnd - r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd - r}V${yBase}Z`;
  }

  const tip = () => document.getElementById("chart-tip");

  function showTip(evt, title, rows) {
    const t = tip();
    t.replaceChildren();
    const head = document.createElement("strong");
    head.textContent = title;
    t.append(head);
    for (const r of rows) {
      const line = document.createElement("div");
      const key = document.createElement("span");
      key.className = `swatch series-${r.index}`;
      line.append(key, `${r.label} `);
      const val = document.createElement("b");
      val.textContent = r.value;
      line.append(val);
      t.append(line);
    }
    t.hidden = false;
    let x;
    let y;
    if (evt.clientX !== undefined && evt.type.startsWith("pointer")) {
      x = evt.clientX;
      y = evt.clientY;
    } else {
      const box = evt.target.getBoundingClientRect();
      x = box.left + box.width / 2;
      y = box.top + 24;
    }
    const w = t.offsetWidth;
    const left = Math.min(Math.max(8, x + 14), window.innerWidth - w - 8);
    const flip = x + 14 + w > window.innerWidth - 8;
    t.style.left = `${flip ? Math.max(8, x - w - 14) : left}px`;
    t.style.top = `${Math.max(8, y - t.offsetHeight - 12)}px`;
  }

  function hideTip() {
    tip().hidden = true;
  }

  function frame(container, label, yTicks, fmt) {
    const W = Math.max(280, container.clientWidth);
    const root = s("svg", {
      viewBox: `0 0 ${W} ${H}`,
      width: W,
      height: H,
      role: "img",
      "aria-label": label,
      class: "chart",
    });
    const lo = yTicks[0];
    const hi = yTicks[yTicks.length - 1];
    const y = (v) => M.top + ((hi - v) / (hi - lo)) * (H - M.top - M.bottom);
    for (const t of yTicks) {
      root.append(
        s("line", { x1: M.left, x2: W - M.right, y1: y(t), y2: y(t), class: t === 0 ? "axis-zero" : "grid" }),
        s("text", { x: M.left - 8, y: y(t), class: "tick", "text-anchor": "end", "dominant-baseline": "middle" }, fmt(t))
      );
    }
    return { root, W, y };
  }

  function legend(series) {
    const box = document.createElement("div");
    box.className = "legend";
    series.forEach((sr, i) => {
      const item = document.createElement("span");
      const sw = document.createElement("span");
      sw.className = `swatch series-${i + 1}${sr.line ? " line" : ""}`;
      item.append(sw, sr.label);
      box.append(item);
    });
    return box;
  }

  // categories: ["2022", ...], series: [{label, values: [..]}]
  function bars(container, { label, categories, series, fmt = wonShort, fmtTip = fmt }) {
    container.replaceChildren();
    const all = series.flatMap((sr) => sr.values);
    const { root, W, y } = frame(container, label, ticks(all), fmt);
    const band = (W - M.left - M.right) / categories.length;
    const groupW = Math.min(band * 0.72, series.length * 28);
    const gap = 2;
    const barW = (groupW - gap * (series.length - 1)) / series.length;
    const y0 = y(0);
    categories.forEach((cat, ci) => {
      const gx = M.left + ci * band + (band - groupW) / 2;
      const hit = s("rect", {
        x: M.left + ci * band, y: M.top, width: band, height: H - M.top - M.bottom,
        class: "hit", tabindex: 0, "aria-label": `${cat} ${series.map((sr) => `${sr.label} ${fmtTip(sr.values[ci])}`).join(", ")}`,
      });
      root.append(hit);
      series.forEach((sr, si) => {
        const v = sr.values[ci];
        if (v === null || v === undefined || v === 0) return;
        root.append(s("path", { d: barPath(gx + si * (barW + gap), y0, y(v), barW), class: `mark series-${si + 1}` }));
      });
      root.append(s("text", { x: M.left + ci * band + band / 2, y: H - 8, class: "tick", "text-anchor": "middle" }, cat));
      const rows = series.map((sr, si) => ({ index: si + 1, label: sr.label, value: fmtTip(sr.values[ci]) }));
      const on = (e) => { hit.classList.add("active"); showTip(e, cat, rows); };
      const off = () => { hit.classList.remove("active"); hideTip(); };
      hit.addEventListener("pointermove", on);
      hit.addEventListener("pointerleave", off);
      hit.addEventListener("focus", on);
      hit.addEventListener("blur", off);
    });
    // 히트 영역은 막대 위에 올려야 막대 위에서도 툴팁이 뜬다
    root.querySelectorAll(".hit").forEach((h) => root.append(h));
    container.append(legend(series), root);
  }

  function lines(container, { label, categories, series, fmt = pct }) {
    container.replaceChildren();
    const all = series.flatMap((sr) => sr.values);
    const { root, W, y } = frame(container, label, ticks(all), (v) => fmt(v, 0));
    const step = categories.length > 1 ? (W - M.left - M.right - 40) / (categories.length - 1) : 0;
    const x = (i) => M.left + 20 + i * step;
    categories.forEach((cat, i) => {
      root.append(s("text", { x: x(i), y: H - 8, class: "tick", "text-anchor": "middle" }, cat));
    });
    const cross = s("line", { y1: M.top, y2: H - M.bottom, class: "crosshair", visibility: "hidden" });
    root.append(cross);
    series.forEach((sr, si) => {
      let d = "";
      let pen = false;
      sr.values.forEach((v, i) => {
        if (v === null || v === undefined) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
        pen = true;
      });
      root.append(s("path", { d, class: `line series-${si + 1}` }));
      sr.values.forEach((v, i) => {
        if (v !== null && v !== undefined) {
          root.append(s("circle", { cx: x(i), cy: y(v), r: 4, class: `dot series-${si + 1}` }));
        }
      });
      // 마지막 값만 직접 표시
      const last = sr.values.length - 1;
      if (sr.values[last] !== null && sr.values[last] !== undefined) {
        root.append(s("text", { x: x(last), y: y(sr.values[last]) - 10, class: "direct", "text-anchor": "middle" }, fmt(sr.values[last])));
      }
    });
    const overlay = s("rect", {
      x: M.left, y: M.top, width: W - M.left - M.right, height: H - M.top - M.bottom,
      class: "hit", tabindex: 0, "aria-label": label,
    });
    let focusIdx = categories.length - 1;
    const showAt = (e, i) => {
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      showTip(e, categories[i], series.map((sr, si) => ({ index: si + 1, label: sr.label, value: fmt(sr.values[i]) })));
    };
    overlay.addEventListener("pointermove", (e) => {
      const box = root.getBoundingClientRect();
      const px = ((e.clientX - box.left) / box.width) * W;
      const i = step ? Math.round((px - M.left - 20) / step) : 0;
      showAt(e, Math.max(0, Math.min(categories.length - 1, i)));
    });
    const off = () => { cross.setAttribute("visibility", "hidden"); hideTip(); };
    overlay.addEventListener("pointerleave", off);
    overlay.addEventListener("blur", off);
    overlay.addEventListener("focus", (e) => showAt(e, focusIdx));
    overlay.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      focusIdx = Math.max(0, Math.min(categories.length - 1, focusIdx + (e.key === "ArrowRight" ? 1 : -1)));
      showAt(e, focusIdx);
    });
    root.append(overlay);
    container.append(legend(series.map((sr) => ({ ...sr, line: true }))), root);
  }

  return { bars, lines, wonShort, pct };
})();
