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
  if (name === "watch") loadWatch();
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
    out.replaceChildren(
      el("div", { class: "card" }, el("h2", {}, r.title), el("div", { class: "muted" }, link(r.old), " → ", link(r.new))),
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
  } catch (err) {
    out.replaceChildren(errorBox(err));
  }
});

// ---- 시작 ----
loadCompanies();
const initial = location.hash.slice(1);
showTab(["ask", "feed", "watch", "diff"].includes(initial) ? initial : "ask");
