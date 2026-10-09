"use strict";

// ---- 공통 ----
const $ = (sel) => document.querySelector(sel);
const IMP = { 3: "중요", 2: "주목", 1: "일반" };
let companies = [];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c !== null && c !== undefined) node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (res.status === 401 && !path.startsWith("/api/auth/")) showLogin();
  if (!res.ok) {
    const detail = body && body.detail;
    const msg = typeof detail === "string" ? detail : "요청을 처리하지 못했습니다";
    throw new Error(`${msg} (${res.status})`);
  }
  return body;
}

function errorBox(err) {
  const box = $("#tpl-error").content.firstElementChild.cloneNode();
  box.textContent = err.message;
  return box;
}

// "삼성전자", "005930", "삼성전자 (005930)" 모두 종목코드로
function toStock(text) {
  const t = (text || "").trim();
  if (!t) return null;
  const m = t.match(/\d{6}/);
  if (m) return m[0];
  const found = companies.find((c) => c.corp_name === t);
  return found ? found.stock_code : undefined;
}

async function loadCompanies() {
  try {
    companies = await api("/api/companies");
    $("#company-list").replaceChildren(
      ...companies.map((c) => el("option", { value: `${c.corp_name} (${c.stock_code})` }))
    );
  } catch {
    companies = [];
  }
}

// ---- 탭 ----
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === name));
  });
  document.querySelectorAll(".panel").forEach((p) => {
    p.hidden = p.id !== `tab-${name}`;
  });
  if (name === "feed") loadFeed();
  if (name === "watch") {
    loadWatch();
    loadAlerts();
  }
  history.replaceState(null, "", `#${name}`);
}
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab))
);

// ---- 질문하기 ----
function renderAnswer(text, onCite) {
  // [1] 같은 출처 번호를 눌러서 해당 출처를 펼칠 수 있게
  const out = el("div", { class: "answer" });
  let last = 0;
  for (const m of text.matchAll(/\[(\d{1,2})\]/g)) {
    out.append(text.slice(last, m.index));
    const n = Number(m[1]);
    out.append(el("button", { class: "cite", type: "button", onclick: () => onCite(n) }, n));
    last = m.index + m[0].length;
  }
  out.append(text.slice(last));
  return out;
}

function sourceCard(s) {
  return el(
    "details",
    { class: "source", id: `src-${s.number}` },
    el(
      "summary",
      {},
      el("strong", {}, `[${s.number}] `),
      `${s.corp_name || ""} · ${s.report_nm || ""} · ${s.section}`,
      s.cited ? "" : el("span", { class: "muted" }, " (인용 안 됨)")
    ),
    s.unit ? el("div", { class: "muted" }, `단위: ${s.unit}`) : null,
    el("pre", {}, s.body),
    s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener" }, "DART 원문 보기") : null
  );
}

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = $("#ask-result");
  const button = e.submitter || $("#ask-form button");
  const stock = toStock($("#ask-stock").value);
  if (stock === undefined) {
    result.replaceChildren(errorBox(new Error("회사를 찾지 못했습니다. 목록에서 고르거나 종목코드를 입력하세요")));
    return;
  }
  const payload = {
    question: $("#ask-q").value.trim(),
    stocks: stock ? [stock] : [],
    year_from: Number($("#ask-from").value) || null,
    year_to: Number($("#ask-to").value) || null,
  };
  button.disabled = true;
  result.replaceChildren(el("div", { class: "card spinner" }, "공시를 찾아 답을 만드는 중… (로컬 모델은 수십 초 걸릴 수 있어요)"));
  try {
    const r = await api("/api/ask", { method: "POST", body: JSON.stringify(payload) });
    const sources = el("div", { class: "card sources" }, el("h3", {}, "출처"), r.sources.map(sourceCard));
    const openSource = (n) => {
      const d = sources.querySelector(`#src-${n}`);
      if (!d) return;
      d.open = true;
      d.scrollIntoView({ behavior: "smooth", block: "nearest" });
      d.classList.add("flash");
      setTimeout(() => d.classList.remove("flash"), 1200);
    };
    result.replaceChildren(
      el(
        "div",
        { class: "card" },
        renderAnswer(r.answer, openSource),
        r.warnings.length
          ? el("div", { class: "warn" }, el("strong", {}, "확인 필요: "), r.warnings.join(" / "))
          : null,
        el("p", { class: "muted" }, r.disclaimer)
      ),
      r.sources.length ? sources : ""
    );
  } catch (err) {
    result.replaceChildren(errorBox(err));
  } finally {
    button.disabled = false;
  }
});

// ---- 공시 피드 ----
async function loadFeed() {
  const list = $("#feed-list");
  const params = new URLSearchParams({
    days: $("#feed-days").value,
    min_importance: $("#feed-imp").value,
    watched_only: $("#feed-watched").checked,
  });
  list.replaceChildren(el("li", { class: "spinner" }, "불러오는 중…"));
  try {
    const rows = await api(`/api/feed?${params}`);
    list.replaceChildren(
      ...(rows.length
        ? rows.map((d) =>
            el(
              "li",
              {},
              el("span", { class: `badge imp-${d.importance}` }, IMP[d.importance]),
              el(
                "div",
                { class: "grow" },
                el("strong", {}, d.corp_name),
                ` · ${d.event_label}${d.correction ? " (정정)" : ""}`,
                el("div", { class: "muted" }, `${d.rcept_dt} · `, el("a", { href: d.url, target: "_blank", rel: "noopener" }, d.report_nm))
              )
            )
          )
        : [el("li", { class: "muted" }, "해당 조건의 공시가 없습니다. dartrag feed poll 로 공시를 받아오세요.")])
    );
  } catch (err) {
    list.replaceChildren(el("li", {}, errorBox(err)));
  }
}
["#feed-days", "#feed-imp", "#feed-watched"].forEach((s) => $(s).addEventListener("change", loadFeed));
$("#feed-refresh").addEventListener("click", loadFeed);

// ---- 관심 종목 ----
async function loadWatch() {
  const list = $("#watch-list");
  try {
    const rows = await api("/api/watchlist");
    list.replaceChildren(
      ...(rows.length
        ? rows.map((w) =>
            el(
              "li",
              {},
              el("div", { class: "grow" }, el("strong", {}, w.corp_name), ` ${w.stock_code || ""}`,
                el("span", { class: "muted" }, ` · ${IMP[w.min_importance]} 이상 알림`)),
              el("button", {
                type: "button",
                onclick: async () => {
                  try {
                    await api(`/api/watchlist/${w.stock_code}`, { method: "DELETE" });
                    loadWatch();
                  } catch (err) {
                    list.prepend(el("li", {}, errorBox(err)));
                  }
                },
              }, "삭제")
            )
          )
        : [el("li", { class: "muted" }, "관심 종목이 없습니다.")])
    );
  } catch (err) {
    list.replaceChildren(el("li", {}, errorBox(err)));
  }
}

$("#watch-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const stock = toStock($("#watch-stock").value);
  if (!stock) {
    $("#watch-list").prepend(el("li", {}, errorBox(new Error("회사를 찾지 못했습니다"))));
    return;
  }
  try {
    await api("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ stock, min_importance: Number($("#watch-imp").value) }),
    });
    $("#watch-stock").value = "";
    loadWatch();
  } catch (err) {
    $("#watch-list").prepend(el("li", {}, errorBox(err)));
  }
});

// ---- 변경점 ----
$("#diff-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#diff-result");
  const stock = toStock($("#diff-stock").value);
  if (!stock) {
    out.replaceChildren(errorBox(new Error("회사를 찾지 못했습니다")));
    return;
  }
  out.replaceChildren(el("div", { class: "card spinner" }, "비교하는 중…"));
  try {
    const r = await api(`/api/diff?stock=${stock}`);
    const real = r.sections.filter((s) => s.status !== "changed" || s.added.length || s.removed.length || s.modified.length);
    const minor = r.sections.filter((s) => !real.includes(s));
    const link = (f) => el("a", { href: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${f.rcept_no}`, target: "_blank", rel: "noopener" }, f.report_nm);
    const summary = el("div", { class: "card diff-summary" });
    out.replaceChildren(
      el("div", { class: "card" }, el("h2", {}, r.title), el("div", { class: "muted" }, link(r.old), " → ", link(r.new))),
      summary,
      ...real.map((s) =>
        el(
          "div",
          { class: "card diff-section" },
          el("h3", {}, s.importance >= 2 ? "⚠️ " : "", s.key),
          el("div", { class: "muted" },
            `${{ added: "새 섹션", removed: "삭제된 섹션", changed: "변경" }[s.status]} · 추가 ${s.added.length} · 삭제 ${s.removed.length} · 수정 ${s.modified.length} · 숫자만 변경 ${s.numbers_only}`),
          el(
            "ul",
            {},
            s.added.slice(0, 20).map((t) => el("li", { class: "add" }, "➕ ", t)),
            s.removed.slice(0, 20).map((t) => el("li", { class: "del" }, "➖ ", t)),
            s.modified.slice(0, 20).map((m) =>
              el("li", {}, "✏️ ", el("span", { class: "del" }, m.before), el("br"), el("span", { class: "add" }, m.after))
            )
          )
        )
      ),
      minor.length
        ? el("div", { class: "card muted" }, "숫자만 갱신된 섹션: ", minor.map((s) => `${s.key} (${s.numbers_only})`).join(", "))
        : ""
    );
    loadDiffSummary(stock, summary);
  } catch (err) {
    out.replaceChildren(errorBox(err));
  }
});


// ---- 알림 채널 (로그인을 켠 경우) ----
const CHANNEL = { email: "이메일", telegram: "텔레그램" };

async function loadAlerts() {
  const box = $("#alert-settings");
  try {
    const r = await api("/api/alerts");
    if (!r.per_user) {
      box.replaceChildren(
        el("div", { class: "card muted" },
          "알림은 .env 의 ALERT_WEBHOOK_URL, ALERT_TELEGRAM_CHAT_ID, ALERT_EMAIL_TO 로 받습니다. ",
          el("code", {}, "dartrag alert test"), " 로 시험해 보세요.")
      );
      return;
    }
    const byKind = Object.fromEntries(r.channels.map((c) => [c.kind, c]));
    const rows = ["email", "telegram"].map((kind) => {
      const ch = byKind[kind];
      const state = !r.available[kind]
        ? "서버에 설정되지 않음"
        : !ch
          ? "연결 안 됨"
          : !ch.verified
            ? ch.pending ? "인증 대기 중" : "인증 만료"
            : ch.enabled ? "켜짐" : "꺼짐";
      const actions = [];
      if (r.available[kind] && (!ch || !ch.verified)) {
        actions.push(el("button", { type: "button", onclick: () => connect(kind) }, kind === "email" ? "인증 메일 받기" : "텔레그램 연결"));
      }
      if (ch && ch.verified) {
        actions.push(el("button", { type: "button", onclick: () => toggle(kind, !ch.enabled) }, ch.enabled ? "끄기" : "켜기"));
        actions.push(el("button", { type: "button", onclick: () => remove(kind) }, "삭제"));
      }
      return el("li", {}, el("div", { class: "grow" }, el("strong", {}, CHANNEL[kind]), el("span", { class: "muted" }, ` · ${state}`)), ...actions);
    });
    const note = el("div", { id: "alert-note", role: "status" });
    box.replaceChildren(el("div", { class: "card" }, el("h3", {}, "알림 받을 곳"), el("ul", { class: "list" }, rows), note));
  } catch (err) {
    box.replaceChildren(errorBox(err));
  }

  async function connect(kind) {
    try {
      if (kind === "email") {
        const r = await api("/api/alerts/email", { method: "POST" });
        await loadAlerts();
        $("#alert-note").textContent = `${r.sent_to} 로 인증 메일을 보냈습니다. 메일의 링크를 열면 알림이 시작됩니다.`;
      } else {
        const r = await api("/api/alerts/telegram", { method: "POST" });
        await loadAlerts();
        $("#alert-note").replaceChildren(
          "아래 링크를 열고 텔레그램에서 '시작'을 누르세요 (30분 안에): ",
          el("a", { href: r.link, target: "_blank", rel: "noopener" }, "봇 열기")
        );
      }
    } catch (err) {
      box.prepend(errorBox(err));
    }
  }
  async function toggle(kind, enabled) {
    try {
      await api(`/api/alerts/${kind}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      loadAlerts();
    } catch (err) {
      box.prepend(errorBox(err));
    }
  }
  async function remove(kind) {
    try {
      await api(`/api/alerts/${kind}`, { method: "DELETE" });
      loadAlerts();
    } catch (err) {
      box.prepend(errorBox(err));
    }
  }
}

// ---- 변경점 요약 ----
async function loadDiffSummary(stock, box) {
  box.replaceChildren(el("div", { class: "muted spinner" }, "요약을 만드는 중… 처음에는 1~2분 걸릴 수 있습니다."));
  try {
    const r = await api(`/api/diff/summary?stock=${stock}`);
    const parts = [el("h3", {}, "한눈에 보기")];
    for (const [key, points] of Object.entries(r.points)) {
      parts.push(el("h4", {}, r.titles[key]));
      parts.push(el("ul", {}, points.map((p) => el("li", {}, p.text,
        p.unverified.length ? el("span", { class: "flag" }, " (숫자 확인 필요)") : "",
        el("span", { class: "muted" }, ` ${p.refs.map((n) => `[${n}]`).join("")}`)))));
    }
    const won = (v) => (v === null ? "-" : `${(v / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억원`);
    const metrics = r.metrics.filter((m) => m.before !== null && m.after !== null);
    if (metrics.length) {
      parts.push(el("h4", {}, "숫자 변화 (재무 데이터로 계산)"));
      parts.push(el("ul", {}, metrics.map((m) => el("li", {}, `${m.label}: ${won(m.before)} → ${won(m.after)}`,
        m.growth !== null ? ` (${m.growth > 0 ? "+" : ""}${m.growth}%)` : ""))));
    }
    if (!Object.keys(r.points).length && !metrics.length) {
      parts.push(el("p", { class: "muted" }, r.model ? "요약할 만한 변경을 찾지 못했습니다." : "답변 모델이 꺼져 있어 요약을 만들지 않았습니다."));
    }
    if (r.evidence.length) {
      parts.push(el("details", {}, el("summary", {}, `근거 항목 ${r.evidence.length}개`),
        el("ol", {}, r.evidence.map((e) => el("li", {}, el("span", { class: "muted" }, `${e.section} · ${e.kind} `), e.text)))));
    }
    parts.push(el("p", { class: "muted" }, r.model ? `요약 모델 ${r.model} · ` : "", r.disclaimer));
    box.replaceChildren(...parts);
  } catch (err) {
    box.replaceChildren(errorBox(err));
  }
}

// ---- 회사 대시보드 ----
const KPI = [
  { key: "revenue", label: "매출액", kind: "won" },
  { key: "operating_income", label: "영업이익", kind: "won" },
  { key: "net_income", label: "당기순이익", kind: "won" },
  { key: "operating_margin", label: "영업이익률", kind: "ratio" },
  { key: "debt_ratio", label: "부채비율", kind: "ratio" },
];

function arrow(v, unit) {
  if (v === null || v === undefined) return "전년 비교 불가";
  const sign = v > 0 ? "▲" : v < 0 ? "▼" : "–";
  return `전년 대비 ${sign} ${Math.abs(v).toFixed(1)}${unit}`;
}

function kpiTiles(series) {
  const cur = series[series.length - 1];
  const prev = series.length > 1 && series[series.length - 2].year === cur.year - 1 ? series[series.length - 2] : null;
  return el(
    "div",
    { class: "kpis" },
    KPI.map((k) => {
      let value;
      let delta;
      if (k.kind === "won") {
        value = Chart.wonShort(cur.values[k.key]);
        delta = arrow(cur.growth[k.key], "%");
      } else {
        const v = cur.ratios[k.key];
        const p = prev ? prev.ratios[k.key] : null;
        value = Chart.pct(v);
        delta = arrow(v !== null && p !== null && p !== undefined ? v - p : null, "%p");
      }
      return el("div", { class: "kpi" }, el("div", { class: "label" }, `${k.label} (${cur.year})`), el("div", { class: "value" }, value), el("div", { class: "delta" }, delta));
    })
  );
}

function dataTable(series) {
  const rows = [
    ["매출액", (p) => Chart.wonShort(p.values.revenue)],
    ["영업이익", (p) => Chart.wonShort(p.values.operating_income)],
    ["당기순이익", (p) => Chart.wonShort(p.values.net_income)],
    ["매출 증감률", (p) => Chart.pct(p.growth.revenue)],
    ["영업이익률", (p) => Chart.pct(p.ratios.operating_margin)],
    ["순이익률", (p) => Chart.pct(p.ratios.net_margin)],
    ["부채비율", (p) => Chart.pct(p.ratios.debt_ratio)],
    ["재무제표", (p) => p.fs_div || "-"],
  ];
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      { class: "data-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "항목"), series.map((p) => el("th", {}, String(p.year))))),
      el("tbody", {}, rows.map(([label, f]) => el("tr", {}, el("th", { scope: "row" }, label), series.map((p) => el("td", {}, f(p))))))
    )
  );
}

let companyCharts = null;
function drawCharts() {
  if (!companyCharts) return;
  const { series, money, margin } = companyCharts;
  const categories = series.map((p) => String(p.year));
  Chart.bars(money, {
    label: "연도별 매출액, 영업이익, 당기순이익",
    categories,
    series: [
      { label: "매출액", values: series.map((p) => p.values.revenue) },
      { label: "영업이익", values: series.map((p) => p.values.operating_income) },
      { label: "당기순이익", values: series.map((p) => p.values.net_income) },
    ],
  });
  Chart.lines(margin, {
    label: "연도별 영업이익률과 순이익률",
    categories,
    series: [
      { label: "영업이익률", values: series.map((p) => p.ratios.operating_margin) },
      { label: "순이익률", values: series.map((p) => p.ratios.net_margin) },
    ],
  });
}
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawCharts, 150);
});

function disclosureItem(d) {
  return el(
    "li",
    {},
    el("span", { class: `badge imp-${d.importance}` }, IMP[d.importance]),
    el(
      "div",
      { class: "grow" },
      el("strong", {}, d.event_label),
      d.correction ? " (정정)" : "",
      el("div", { class: "muted" }, `${d.rcept_dt} · `, el("a", { href: d.url, target: "_blank", rel: "noopener" }, d.report_nm))
    )
  );
}

async function diffSummary(stock) {
  const box = el("div", { class: "card" }, el("h3", {}, "최근 사업보고서 변경점"), el("div", { class: "muted" }, "비교하는 중…"));
  api(`/api/diff?stock=${stock}`)
    .then((r) => {
      const changed = r.sections.filter((s) => s.status !== "changed" || s.added.length || s.removed.length || s.modified.length);
      const important = changed.filter((s) => s.importance >= 2);
      box.replaceChildren(
        el("h3", {}, "최근 사업보고서 변경점"),
        el("div", { class: "muted" }, `${r.old.report_nm} → ${r.new.report_nm}`),
        el("p", {}, `내용이 바뀐 섹션 ${changed.length}곳`, important.length ? `, 그중 눈여겨볼 곳 ${important.length}곳` : ""),
        important.length ? el("ul", {}, important.slice(0, 5).map((s) => el("li", {}, "⚠️ ", s.key))) : null,
        el("button", { type: "button", onclick: () => { $("#diff-stock").value = stock; showTab("diff"); $("#diff-form").requestSubmit(); } }, "변경점 자세히 보기")
      );
    })
    .catch((err) => {
      box.replaceChildren(el("h3", {}, "최근 사업보고서 변경점"), el("div", { class: "muted" }, err.message));
    });
  return box;
}

async function loadCompany(stock) {
  const out = $("#company-result");
  out.replaceChildren(el("div", { class: "card spinner" }, "불러오는 중…"));
  companyCharts = null;
  try {
    const r = await api(`/api/company/${stock}`);
    history.replaceState(null, "", `#company/${stock}`);
    const watchBtn = el("button", { type: "button" }, r.watched ? "관심 종목 해제" : "관심 종목에 추가");
    let watched = r.watched;
    watchBtn.addEventListener("click", async () => {
      watchBtn.disabled = true;
      try {
        if (watched) await api(`/api/watchlist/${stock}`, { method: "DELETE" });
        else await api("/api/watchlist", { method: "POST", body: JSON.stringify({ stock, min_importance: 2 }) });
        watched = !watched;
        watchBtn.textContent = watched ? "관심 종목 해제" : "관심 종목에 추가";
      } catch (err) {
        out.prepend(errorBox(err));
      } finally {
        watchBtn.disabled = false;
      }
    });
    const head = el(
      "div",
      { class: "card company-head" },
      el("h2", {}, r.corp_name, el("span", { class: "muted" }, ` ${r.stock_code}`)),
      el("div", { class: "row" },
        el("a", { class: "button", href: `/api/company/${stock}/report.pdf`, download: "" }, "PDF 리포트"),
        watchBtn)
    );
    const parts = [head];
    if (r.series.length) {
      const latest = r.series[r.series.length - 1];
      const money = el("div", { class: "chart-box" });
      const margin = el("div", { class: "chart-box" });
      parts.push(
        kpiTiles(r.series),
        r.issues && r.issues.length
          ? el("div", { class: "warn" },
              el("strong", {}, "데이터 확인 필요 "),
              el("ul", {}, r.issues.slice(0, 5).map((i) => el("li", {}, `${i.bsns_year} ${i.fs_div === "CFS" ? "연결" : "별도"} · ${i.detail}`))),
              el("span", { class: "muted" }, "수집한 재무 수치가 검증 규칙에 걸렸습니다. 원문 공시와 비교해 보세요."))
          : "",
        el("div", { class: "card chart-card" }, el("h3", {}, "매출과 이익 (원)"), money,
          el("details", {}, el("summary", {}, "표로 보기"), dataTable(r.series)),
          el("p", { class: "muted" }, `사업보고서 기준, ${latest.fs_div || "연결"} 재무제표 우선 · `,
            latest.rcept_no ? el("a", { href: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${latest.rcept_no}`, target: "_blank", rel: "noopener" }, `${latest.year} 사업보고서 원문`) : "")),
        el("div", { class: "card chart-card" }, el("h3", {}, "이익률 (%)"), margin)
      );
      companyCharts = { series: r.series, money, margin };
    } else {
      parts.push(el("div", { class: "card muted" }, "재무 데이터가 없습니다. dartrag collect 로 사업보고서 재무제표를 받아오세요."));
    }
    parts.push(
      el(
        "div",
        { class: "card" },
        el("h3", {}, "최근 90일 공시"),
        r.disclosures.length
          ? el("ul", { class: "list" }, r.disclosures.map(disclosureItem))
          : el("div", { class: "muted" }, "최근 공시가 없습니다. dartrag feed poll 로 공시를 받아오세요.")
      ),
      await diffSummary(stock),
      el("p", { class: "muted" }, r.disclaimer)
    );
    out.replaceChildren(...parts);
    drawCharts();
  } catch (err) {
    out.replaceChildren(errorBox(err));
  }
}

$("#company-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const stock = toStock($("#company-stock").value);
  if (!stock) {
    $("#company-result").replaceChildren(errorBox(new Error("회사를 찾지 못했습니다. 목록에서 고르거나 종목코드를 입력하세요")));
    return;
  }
  loadCompany(stock);
});

// ---- 로그인 ----
let authInfo = { auth_required: false, allow_signup: false, user: null };
let signupMode = false;

function setAuthMode(signup) {
  signupMode = signup && authInfo.allow_signup;
  $("#auth-title").textContent = signupMode ? "가입하기" : "로그인";
  $("#auth-submit").textContent = signupMode ? "가입하고 시작하기" : "로그인";
  $("#auth-password").setAttribute("autocomplete", signupMode ? "new-password" : "current-password");
  $("#auth-hint").hidden = !signupMode;
  $("#auth-switch-text").textContent = signupMode ? "이미 계정이 있으신가요?" : "계정이 없으신가요?";
  $("#auth-switch").textContent = signupMode ? "로그인" : "가입하기";
  $("#auth-switch-line").hidden = !authInfo.allow_signup;
  $("#auth-error").replaceChildren();
}

function showLogin() {
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = true; });
  $(".tabs").hidden = true;
  $("#account").hidden = true;
  $("#auth-view").hidden = false;
  setAuthMode(signupMode);
  $("#auth-email").focus();
}

$("#auth-switch").addEventListener("click", () => setAuthMode(!signupMode));

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("#auth-submit");
  button.disabled = true;
  try {
    const r = await api(signupMode ? "/api/auth/signup" : "/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("#auth-email").value, password: $("#auth-password").value }),
    });
    $("#auth-password").value = "";
    authInfo.user = r.user;
    startApp();
  } catch (err) {
    $("#auth-error").replaceChildren(errorBox(err));
  } finally {
    button.disabled = false;
  }
});

$("#logout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } finally {
    authInfo.user = null;
    signupMode = false;
    showLogin();
  }
});

function startApp() {
  $("#auth-view").hidden = true;
  $(".tabs").hidden = false;
  $("#account").hidden = !authInfo.user;
  $("#account-email").textContent = authInfo.user ? authInfo.user.email : "";
  loadCompanies();
  const [initial, initialStock] = location.hash.slice(1).split("/");
  const tab = initial === "alerts" ? "watch" : initial;
  showTab(["ask", "company", "feed", "watch", "diff"].includes(tab) ? tab : "ask");
  if (initial === "company" && /^\d{6}$/.test(initialStock || "")) {
    $("#company-stock").value = initialStock;
    loadCompany(initialStock);
  }
}

// ---- 시작 ----
(async () => {
  try {
    authInfo = await api("/api/auth/me");
  } catch {
    // 서버가 옛 버전이거나 잠시 안 될 때는 로그인 없이 시작
  }
  if (authInfo.auth_required && !authInfo.user) showLogin();
  else startApp();
})();
