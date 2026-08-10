const state = {
  users: [],
  clients: [],
  selectedClientId: null,
  selectedClient: null,
  currentUser: null,
  editingQuoteId: null,
  quoteContext: "client",
  prospects: [],
  zones: [],
  prospectorMode: "prospects",
  selectedProspectId: null,
  selectedProspect: null,
  editingProspectQuoteId: null,
  doneFollowups: [],
};

const money = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });
const qs = (selector) => document.querySelector(selector);
const qsa = (selector) => [...document.querySelectorAll(selector)];
const API_BASE = window.location.pathname.includes("/comandas/crm") ? "/comandas/crm/api" : (window.location.pathname.startsWith("/crm") ? "/crm/api" : "/api");
const EXPORT_BASE = window.location.pathname.includes("/comandas/crm") ? "/comandas/crm/exports" : (window.location.pathname.startsWith("/crm") ? "/crm/exports" : "/exports");

async function api(path, options = {}) {
  const target = path.startsWith("/api/") ? `${API_BASE}${path.slice(4)}` : path;
  const res = await fetch(target, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Error de servidor");
  return data;
}

function exportUrl(url) {
  return url.startsWith("/exports/") ? `${EXPORT_BASE}${url.slice(8)}` : url;
}

function setStatus(message) {
  qs("#status").textContent = message;
}

function activeUserId() {
  return state.currentUser?.id || qs("#activeUser").value;
}

async function loadUsers() {
  state.users = await api("/api/users");
  if (state.currentUser) {
    qs("#activeUser").innerHTML = `<option value="${state.currentUser.id}">${escapeHtml(state.currentUser.name)} (${escapeHtml(state.currentUser.role)})</option>`;
    return;
  }
  qs("#activeUser").innerHTML = state.users
    .map((u) => `<option value="${u.id}">${escapeHtml(u.name)} (${escapeHtml(u.role)})</option>`)
    .join("");
}

async function loadClients() {
  const q = encodeURIComponent(qs("#clientSearch").value.trim());
  state.clients = await api(`/api/clients?q=${q}`);
  qs("#clientCount").textContent = state.clients.length;
  qs("#clients").innerHTML = state.clients.map(clientRow).join("");
  refreshDoneFollowupClientOptions();
  qsa(".client-row").forEach((button) => {
    button.addEventListener("click", () => selectClient(button.dataset.id));
  });
}

async function loadDoneFollowups() {
  const q = encodeURIComponent(qs("#doneFollowupSearch")?.value.trim() || "");
  const userId = encodeURIComponent(activeUserId() || "");
  state.doneFollowups = await api(`/api/followups-done?user_id=${userId}&q=${q}`);
  renderDoneFollowups();
}

function refreshDoneFollowupClientOptions() {
  const select = qs("#doneFollowupClient");
  if (!select) return;
  const current = select.value;
  select.innerHTML = `<option value="">Selecciona cliente</option>` + state.clients.map((client) => {
    const label = `${client.code || "Sin codigo"} - ${client.name}`;
    return `<option value="${escapeAttr(client.id)}">${escapeHtml(label)}</option>`;
  }).join("");
  if (state.clients.some((client) => client.id === current)) select.value = current;
}

function renderDoneFollowups() {
  const target = qs("#doneFollowups");
  if (!target) return;
  target.innerHTML = state.doneFollowups.length
    ? state.doneFollowups.map(doneFollowupRecord).join("")
    : emptyLine("Sin seguimientos registrados para este usuario");
  qsa("[data-done-client]").forEach((button) => {
    button.addEventListener("click", () => openMatchedClient(button.dataset.doneClient));
  });
}

function doneFollowupRecord(item) {
  const next = item.next_action ? `<p class="linked-quote">Siguiente accion: ${escapeHtml(item.next_action)} ${escapeHtml(formatDate(item.next_action_at))}</p>` : "";
  const quote = item.quote_folio ? `<p class="linked-quote">Cotizacion: ${escapeHtml(item.quote_title || item.quote_folio)}</p>` : "";
  return `
    <article class="record done-followup-card">
      <header>
        <strong>${escapeHtml(item.client_name || "Cliente sin nombre")}</strong>
        <span>${formatDate(item.contact_at)}</span>
      </header>
      <p>${escapeHtml(item.client_code || "")} ${item.client_phone ? `- ${escapeHtml(item.client_phone)}` : ""}</p>
      <p><strong>${escapeHtml(item.channel || "")}</strong> - ${escapeHtml(item.outcome || "")}</p>
      ${next}
      ${quote}
      <p>${escapeHtml(item.notes || "")}</p>
      <button class="small-action" data-done-client="${escapeAttr(item.client_id)}" type="button">Abrir cliente</button>
    </article>
  `;
}

async function loadProspects() {
  const q = encodeURIComponent(qs("#prospectorFilter")?.value.trim() || "");
  const selectedStatus = qs("#prospectorStatus")?.value || "todos";
  const status = encodeURIComponent(selectedStatus);
  const zone = encodeURIComponent(qs("#prospectorZoneFilter")?.value || "todas");
  state.prospects = await api(`/api/prospector/prospects?q=${q}&status=${status}&zone=${zone}`);
  renderProspects();
  if (state.selectedProspectId && !state.prospects.some((prospect) => prospect.id === state.selectedProspectId)) {
    state.selectedProspectId = null;
    state.selectedProspect = null;
    qs("#prospectDetail")?.classList.add("hidden");
  }
  updateProspectorResultsVisibility();
}

async function loadZones() {
  const currentSearchZone = qs("#prospectorZone")?.value || "";
  const currentFilterZone = qs("#prospectorZoneFilter")?.value || "todas";
  const currentScanZones = new Set([...qs("#prospectorZonesMulti")?.selectedOptions || []].map((option) => option.value));
  state.zones = await api("/api/prospector/zones");
  const options = state.zones.map((zone) => `<option value="${escapeAttr(zone.name)}">${escapeHtml(zone.name)}</option>`).join("");
  qs("#prospectorZone").innerHTML = `<option value="">Sin zona especifica</option>` + options;
  qs("#prospectorZoneFilter").innerHTML = `<option value="todas">Todas las zonas</option>` + options;
  qs("#prospectorZonesMulti").innerHTML = options;
  if (state.zones.some((zone) => zone.name === currentSearchZone)) qs("#prospectorZone").value = currentSearchZone;
  if (currentFilterZone === "todas" || state.zones.some((zone) => zone.name === currentFilterZone)) qs("#prospectorZoneFilter").value = currentFilterZone;
  qsa("#prospectorZonesMulti option").forEach((option) => option.selected = currentScanZones.has(option.value));
}

function renderProspects() {
  const target = qs("#prospectorResults");
  if (!target) return;
  target.innerHTML = state.prospects.length
    ? state.prospects.map(prospectCard).join("")
    : emptyLine("Sin prospectos guardados");
  qsa("[data-convert-prospect]").forEach((button) => {
    button.addEventListener("click", () => startProspectFollowup(button.dataset.convertProspect));
  });
  qsa("[data-open-prospect]").forEach((button) => {
    button.addEventListener("click", () => openProspectDetail(button.dataset.openProspect));
  });
  qsa("[data-open-client]").forEach((button) => {
    button.addEventListener("click", () => openMatchedClient(button.dataset.openClient));
  });
  qsa("[data-link-prospect]").forEach((button) => {
    button.addEventListener("click", () => linkProspectToClient(button.dataset.linkProspect));
  });
  updateProspectorResultsVisibility();
}

function updateProspectorResultsVisibility() {
  const results = qs("#prospectorResults");
  if (!results) return;
  const hideList = state.prospectorMode === "followup" && Boolean(state.selectedProspectId);
  results.classList.toggle("hidden", hideList);
}

function prospectCard(prospect) {
  const hasClient = Boolean(prospect.client_id);
  const converted = prospect.status === "convertido";
  const existing = prospect.status === "cliente_existente";
  const following = prospect.status === "en_seguimiento";
  const website = prospect.website
    ? `<a class="small-link" href="${escapeAttr(prospect.website)}" target="_blank" rel="noreferrer">Sitio web</a>`
    : "";
  const phone = prospect.phone
    ? `<a class="small-link" href="tel:${escapeAttr(prospect.phone)}">${escapeHtml(prospect.phone)}</a>`
    : `<span class="muted">Sin telefono</span>`;
  const rating = prospect.rating ? `${formatNumber(prospect.rating)} / ${formatNumber(prospect.total_reviews || 0)} resenas` : "Sin rating";
  const zone = prospect.zone_name ? `<span>${escapeHtml(prospect.zone_name)}</span>` : "";
  const clientLine = prospect.client_name
    ? `<p class="client-match">Cliente existente: ${escapeHtml(prospect.client_name)}</p>`
    : "";
  const linkClientForm = following && !hasClient
    ? `
      <div class="prospect-link-client">
        <input id="client-code-${escapeAttr(prospect.id)}" placeholder="Numero de cliente">
        <button class="small-action" data-link-prospect="${escapeAttr(prospect.id)}" type="button">Asociar</button>
      </div>
    `
    : "";
  return `
    <article class="record prospect-card">
      <header>
        <strong>${escapeHtml(prospect.name)}</strong>
        <span class="status-badge ${escapeAttr(prospect.status || "nuevo")}">${escapeHtml(prospectStatusLabel(prospect.status))}</span>
      </header>
      <p>${escapeHtml(prospect.category || "Sin categoria")} - ${escapeHtml(rating)}</p>
      <p>${escapeHtml(prospect.address || "Sin direccion")}</p>
      ${clientLine}
      <div class="prospect-meta">
        ${phone}
        ${website}
        ${zone}
        <span>${escapeHtml(prospect.source_query || "")}</span>
      </div>
      ${linkClientForm}
      <div class="prospect-actions">
        ${hasClient
          ? `<button class="small-action" data-open-client="${escapeAttr(prospect.client_id)}">${existing ? "Ver cliente existente" : "Ver cliente"}</button>`
          : following
            ? `<button class="small-action" data-open-prospect="${escapeAttr(prospect.id)}">Abrir seguimiento</button>`
            : `<button class="small-action" data-convert-prospect="${escapeAttr(prospect.id)}" ${converted ? "disabled" : ""}>Iniciar seguimiento</button>`}
      </div>
    </article>
  `;
}

function prospectStatusLabel(status) {
  const labels = {
    nuevo: "Nuevo",
    en_seguimiento: "En seguimiento",
    convertido: "Convertido",
    cliente_existente: "Cliente existente",
    descartado: "Descartado",
  };
  return labels[status || "nuevo"] || status || "Nuevo";
}

function clientRow(client) {
  const active = client.id === state.selectedClientId ? " active" : "";
  return `
    <button class="client-row${active}" data-id="${escapeAttr(client.id)}">
      <strong>${escapeHtml(client.name)}</strong>
      <span>${escapeHtml(client.code || "Sin codigo")} · ${escapeHtml(client.assigned_user || "Sin vendedor")}</span>
    </button>
  `;
}

async function selectClient(id) {
  state.selectedClientId = id;
  state.selectedClient = await api(`/api/client?id=${encodeURIComponent(id)}&light=1`);
  renderClientCard();
  loadClientDetails(id);
  loadClients();
}

async function loadClientDetails(id) {
  const data = await api(`/api/client?id=${encodeURIComponent(id)}`);
  if (state.selectedClientId !== id) return;
  state.selectedClient = data;
  renderClientCard();
}

function renderClientCard() {
  const data = state.selectedClient;
  const client = data.client;
  qs("#emptyState").classList.add("hidden");
  qs("#clientCard").classList.remove("hidden");
  qs("#cardName").textContent = client.name;
  qs("#cardCode").textContent = `${client.code || "Sin codigo"} · ${client.assigned_user || "Sin vendedor asignado"}`;
  qs("#taxAddress").textContent = client.tax_address || "Pendiente";
  qs("#consigneeAddress").textContent = client.consignee_address || "Pendiente";
  qs("#deliveryMethod").textContent = client.delivery_method || "Pendiente por capturar";
  qs("#contactInfo").innerHTML = contactInfoHtml(client);
  refreshFollowupQuoteOptions();

  qs("#productFilter").value = "";
  qs("#invoiceFilter").value = "";
  if (data.details_pending) {
    qs("#productResults").innerHTML = emptyLine("Cargando productos...");
    qs("#invoiceResults").innerHTML = emptyLine("Cargando facturas...");
  } else {
    renderProducts();
    renderInvoices();
  }
  qs("#quotes").innerHTML = data.quotes.length ? data.quotes.map(quoteRecord).join("") : emptyLine("Sin cotizaciones guardadas");
  qs("#followups").innerHTML = data.followups.length ? data.followups.map(followupRecord).join("") : emptyLine("Sin seguimientos registrados");
  qs("#activity").innerHTML = data.activity.length ? data.activity.map(activityRecord).join("") : emptyLine("Sin actividad");

  bindProductInvoiceEvents();
  qsa("[data-quote]").forEach((button) => button.addEventListener("click", () => previewQuote(button.dataset.quote)));
  qsa("[data-edit-quote]").forEach((button) => button.addEventListener("click", () => editQuote(button.dataset.editQuote)));
  qsa("[data-export-pdf]").forEach((button) => button.addEventListener("click", () => exportQuote(button.dataset.exportPdf, "pdf")));
  qsa("[data-export-xlsx]").forEach((button) => button.addEventListener("click", () => exportQuote(button.dataset.exportXlsx, "xlsx")));
  qsa("[data-delete-quote]").forEach((button) => button.addEventListener("click", () => deleteQuote(button.dataset.deleteQuote)));
}

function renderProducts() {
  const term = normalizeSearch(qs("#productFilter")?.value || "");
  const products = (state.selectedClient?.products || []).filter((product) => {
    if (!term) return true;
    return normalizeSearch([
      product.cip,
      product.description,
      product.total_amount,
      product.last_purchase,
      ...(product.purchases || []).flatMap((purchase) => [purchase.invoice, purchase.date, purchase.amount]),
    ].join(" ")).includes(term);
  });
  qs("#productResults").innerHTML = products.length ? products.map(productRecord).join("") : emptyLine("Sin productos que coincidan");
  bindProductInvoiceEvents();
}

function renderInvoices() {
  const term = normalizeSearch(qs("#invoiceFilter")?.value || "");
  const invoices = (state.selectedClient?.invoices || []).filter((invoice) => {
    if (!term) return true;
    return normalizeSearch([invoice.folio, invoice.issued_at, invoice.total, invoice.status].join(" ")).includes(term);
  });
  qs("#invoiceResults").innerHTML = invoices.length ? invoices.map(invoiceRecord).join("") : emptyLine("Sin facturas que coincidan");
  bindProductInvoiceEvents();
}

function bindProductInvoiceEvents() {
  qsa("[data-product]").forEach((button) => button.addEventListener("click", () => toggleProduct(button.dataset.product)));
  qsa("[data-invoice]").forEach((button) => button.addEventListener("click", () => previewInvoice(button.dataset.invoice)));
}

function normalizeSearch(value) {
  return String(value || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function contactInfoHtml(client) {
  const phones = String(client.phone || "")
    .split("/")
    .map((item) => item.trim())
    .filter(Boolean);
  const lines = [];
  if (phones.length) lines.push(`<div>${phones.map(escapeHtml).join("<br>")}</div>`);
  if (client.contact_name) lines.push(`<div class="contact-sub">${escapeHtml(client.contact_name)}</div>`);
  if (client.email) lines.push(`<div class="contact-sub">${escapeHtml(client.email)}</div>`);
  return lines.length ? lines.join("") : "Pendiente";
}

function invoiceRecord(invoice) {
  return `
    <article class="record invoice-card">
      <header><strong>${escapeHtml(invoice.folio)}</strong><span>${formatDate(invoice.issued_at)}</span></header>
      <p>${money.format(invoice.total)} · ${escapeHtml(invoice.status)}</p>
      <button class="small-action" data-invoice="${invoice.id}">Vista previa</button>
    </article>
  `;
}

function productRecord(product, index) {
  const target = `product-detail-${index}`;
  return `
    <article class="record product-card">
      <button class="product-summary" data-product="${escapeAttr(target)}" type="button">
        <span>
          <strong>${escapeHtml(product.cip || "Sin CIP")}</strong>
          <em>${escapeHtml(product.description || "Sin descripcion")}</em>
        </span>
        <span>
          <strong>${money.format(product.total_amount || 0)}</strong>
          <em>${formatDate(product.last_purchase)}</em>
        </span>
      </button>
      <div id="${escapeAttr(target)}" class="product-detail hidden">
        ${product.purchases.map(productPurchaseRow).join("")}
      </div>
    </article>
  `;
}

function productPurchaseRow(purchase) {
  return `
    <button class="purchase-row" data-invoice="${escapeAttr(purchase.invoice_id)}" type="button">
      <span>${formatDate(purchase.date)}</span>
      <span>${formatNumber(purchase.pieces)} pzas</span>
      <span>${money.format(purchase.amount || 0)}</span>
      <strong>${escapeHtml(purchase.invoice)}</strong>
    </button>
  `;
}

function toggleProduct(id) {
  const detail = qs(`#${CSS.escape(id)}`);
  if (detail) detail.classList.toggle("hidden");
}

function formatNumber(value) {
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 2 }).format(Number(value || 0));
}

function formatDate(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) return `${match[3]}/${match[2]}/${match[1]}`;
  const date = new Date(text);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleDateString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" });
  }
  return text.split(" ")[0];
}

function quoteRecord(quote) {
  const title = quote.quote_title || quote.folio;
  return `
    <article class="record quote-card">
      <header><strong>${escapeHtml(title)}</strong><span>${formatDate(quote.created_at)}</span></header>
      <p>Total ${money.format(quote.total)} - costo de envio autorizado ${money.format(quote.authorized_shipping)}</p>
      ${userMeta(quote.user_name)}
      <div class="quote-actions">
        <button class="small-action" data-quote="${quote.id}">Ver</button>
        <button class="small-action" data-edit-quote="${quote.id}">Editar</button>
        <button class="small-action" data-export-pdf="${quote.id}">PDF</button>
        <button class="small-action" data-export-xlsx="${quote.id}">Excel</button>
        <button class="small-action danger-action" data-delete-quote="${quote.id}">Borrar</button>
      </div>
    </article>
  `;
}
function followupRecord(item) {
  const quote = item.quote_folio ? `<p class="linked-quote">Cotizacion relacionada: ${escapeHtml(item.quote_title || item.quote_folio)}</p>` : "";
  return `
    <article class="record">
      <header><strong>${escapeHtml(item.channel)}</strong><span>${formatDate(item.contact_at)}</span></header>
      <p>${escapeHtml(item.outcome)}</p>
      ${userMeta(item.user_name)}
      ${quote}
      <p>${escapeHtml(item.notes || "")}</p>
    </article>
  `;
}

function activityRecord(item) {
  return `
    <article class="record">
      <header><strong>${escapeHtml(item.title)}</strong><span>${formatDate(item.created_at)}</span></header>
      <p>${escapeHtml(item.type)}</p>
      ${userMeta(item.user_name)}
    </article>
  `;
}

function emptyLine(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function userMeta(name) {
  return name ? `<p class="record-meta">Usuario: ${escapeHtml(name)}</p>` : "";
}

async function previewInvoice(id) {
  if (id.startsWith("extfact:") || id.startsWith("extfactid:")) {
    try {
      const result = await api(`/api/invoice/export?id=${encodeURIComponent(id)}`);
      qs("#previewContent").innerHTML = `
        <iframe class="pdf-preview" src="${escapeAttr(exportUrl(result.url))}"></iframe>
      `;
      qs("#previewDialog").showModal();
      return;
    } catch (error) {
      setStatus(error.message);
    }
  }
  const invoice = await api(`/api/invoice?id=${encodeURIComponent(id)}`);
  qs("#previewContent").innerHTML = `
    <h2>Factura ${escapeHtml(invoice.folio)}</h2>
    <div class="info-grid">
      <div><span>Fecha</span><strong>${escapeHtml(invoice.issued_at)}</strong></div>
      <div><span>Estatus</span><strong>${escapeHtml(invoice.status)}</strong></div>
      <div><span>Subtotal</span><strong>${money.format(invoice.subtotal)}</strong></div>
      <div><span>Descuento</span><strong>${money.format(invoice.discount)}</strong></div>
      <div><span>IVA</span><strong>${money.format(invoice.tax)}</strong></div>
      <div><span>Total</span><strong>${money.format(invoice.total)}</strong></div>
    </div>
    <p>${escapeHtml(invoice.detail || "Sin detalle capturado")}</p>
  `;
  qs("#previewDialog").showModal();
}

async function previewQuote(id) {
  const result = await api(`/api/quote/export?id=${encodeURIComponent(id)}&format=pdf`);
  const url = `${exportUrl(result.url)}?t=${Date.now()}`;
  qs("#previewContent").innerHTML = `
    <iframe class="pdf-preview" src="${escapeAttr(url)}"></iframe>
  `;
  qs("#previewDialog").showModal();
}

async function previewProspectQuote(id) {
  const result = await api(`/api/prospector/quote/export?id=${encodeURIComponent(id)}&format=pdf`);
  const url = `${exportUrl(result.url)}?t=${Date.now()}`;
  qs("#previewContent").innerHTML = `
    <iframe class="pdf-preview" src="${escapeAttr(url)}"></iframe>
  `;
  qs("#previewDialog").showModal();
}

async function exportQuote(id, format) {
  const result = await api(`/api/quote/export?id=${encodeURIComponent(id)}&format=${format}`);
  window.open(`${exportUrl(result.url)}?t=${Date.now()}`, "_blank");
  setStatus(format === "pdf" ? "PDF generado" : "Excel generado");
}

async function exportProspectQuote(id, format) {
  const result = await api(`/api/prospector/quote/export?id=${encodeURIComponent(id)}&format=${format}`);
  window.open(`${exportUrl(result.url)}?t=${Date.now()}`, "_blank");
  setStatus(format === "pdf" ? "PDF generado" : "Excel generado");
}

async function deleteQuote(id) {
  if (!confirm("¿Eliminar esta cotizacion?")) return;
  try {
    await api(`/api/quotes?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    await selectClient(state.selectedClientId);
    switchTab("quotes");
    setStatus("Cotizacion eliminada");
  } catch (error) {
    setStatus(error.message);
  }
}

async function deleteProspectQuote(id) {
  if (!confirm("¿Eliminar esta cotizacion?")) return;
  try {
    await api(`/api/prospector/quotes?id=${encodeURIComponent(id)}`, { method: "DELETE" });
    await openProspectDetail(state.selectedProspectId);
    switchProspectTab("prospect-quotes");
    setStatus("Cotizacion eliminada");
  } catch (error) {
    setStatus(error.message);
  }
}

async function editQuote(id) {
  try {
    const quote = await api(`/api/quote?id=${encodeURIComponent(id)}`);
    state.editingQuoteId = id;
    state.editingProspectQuoteId = null;
    state.quoteContext = "client";
    state.selectedClientId = quote.client_id;
    const form = qs("#quoteForm");
    form.quote_recipient.value = quote.quote_recipient || "";
    form.notes.value = quote.notes || "";
    qs("#quoteItems").innerHTML = "";
    quote.items.forEach((item) => addQuoteItem({
      cip: item.cip,
      description: item.description,
      quantity: item.quantity,
      unit_price: item.unit_price,
      discount_rate: item.discount_rate,
      tax_rate: item.tax_rate,
    }));
    if (!quote.items.length) addQuoteItem();
    qs("#quoteSubmitBtn").textContent = "Guardar cambios";
    switchView("quote");
    setStatus(`Editando cotizacion ${quote.folio}`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function editProspectQuote(id) {
  try {
    const quote = await api(`/api/prospector/quote?id=${encodeURIComponent(id)}`);
    state.editingQuoteId = null;
    state.editingProspectQuoteId = id;
    state.quoteContext = "prospect";
    state.selectedProspectId = quote.prospect_id;
    const form = qs("#quoteForm");
    form.quote_recipient.value = quote.prospect_name || quote.client_name || "";
    form.notes.value = quote.notes || "";
    qs("#quoteItems").innerHTML = "";
    quote.items.forEach((item) => addQuoteItem({
      cip: item.cip,
      description: item.description,
      quantity: item.quantity,
      unit_price: item.unit_price,
      discount_rate: item.discount_rate,
      tax_rate: item.tax_rate,
    }));
    if (!quote.items.length) addQuoteItem();
    qs("#quoteSubmitBtn").textContent = "Guardar cambios";
    switchView("quote");
    setStatus(`Editando cotizacion ${quote.folio}`);
  } catch (error) {
    setStatus(error.message);
  }
}

function startProspectQuote() {
  if (!state.selectedProspectId || !state.selectedProspect?.prospect) return setStatus("Abre un prospecto en seguimiento");
  resetQuoteForm();
  state.quoteContext = "prospect";
  state.editingProspectQuoteId = null;
  const prospect = state.selectedProspect.prospect;
  qs("#quoteForm").quote_recipient.value = prospect.name || "";
  switchView("quote");
  setStatus(`Cotizando a prospecto: ${prospect.name}`);
}

function resetQuoteForm() {
  state.editingQuoteId = null;
  state.editingProspectQuoteId = null;
  state.quoteContext = "client";
  const form = qs("#quoteForm");
  form.reset();
  qs("#quoteItems").innerHTML = "";
  addQuoteItem();
  qs("#quoteSubmitBtn").textContent = "Guardar cotizacion";
}

function bindProspectQuoteActions() {
  qsa("#prospectDetail [data-prospect-quote]").forEach((button) => button.addEventListener("click", () => previewProspectQuote(button.dataset.prospectQuote)));
  qsa("#prospectDetail [data-edit-prospect-quote]").forEach((button) => button.addEventListener("click", () => editProspectQuote(button.dataset.editProspectQuote)));
  qsa("#prospectDetail [data-export-prospect-pdf]").forEach((button) => button.addEventListener("click", () => exportProspectQuote(button.dataset.exportProspectPdf, "pdf")));
  qsa("#prospectDetail [data-export-prospect-xlsx]").forEach((button) => button.addEventListener("click", () => exportProspectQuote(button.dataset.exportProspectXlsx, "xlsx")));
  qsa("#prospectDetail [data-delete-prospect-quote]").forEach((button) => button.addEventListener("click", () => deleteProspectQuote(button.dataset.deleteProspectQuote)));
}

function switchView(view) {
  qsa(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  qsa(".view").forEach((panel) => panel.classList.toggle("active", panel.id === `view-${view}`));
  if (view === "prospector") {
    loadZones().then(loadProspects).catch((error) => setStatus(error.message));
  }
  if (view === "followupsDone") {
    loadClients().then(loadDoneFollowups).catch((error) => setStatus(error.message));
  }
  if (view === "settings") loadBankAccounts();
  if (view === "followup") refreshFollowupQuoteOptions();
}

function switchProspectorMode(mode) {
  state.prospectorMode = mode;
  qsa(".prospector-mode").forEach((button) => {
    button.classList.toggle("active", button.dataset.prospectorMode === mode);
  });
  if (mode === "followup") {
    qs("#prospectorStatus").value = "en_seguimiento";
  } else {
    qs("#prospectorStatus").value = "todos";
    state.selectedProspectId = null;
    state.selectedProspect = null;
    qs("#prospectDetail")?.classList.add("hidden");
  }
  updateProspectorResultsVisibility();
  loadProspects();
}

function switchTab(tab) {
  qsa(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  qsa(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === tab));
}

function openClientDialog(client = {}) {
  const form = qs("#clientForm");
  form.id.value = client.id || "";
  form.code.value = client.code || "";
  form.name.value = client.name || "";
  form.tax_address.value = client.tax_address || "";
  form.consignee_address.value = client.consignee_address || "";
  form.delivery_method.value = client.delivery_method || "";
  form.phone.value = client.phone || "";
  form.email.value = client.email || "";
  qs("#clientDialog").showModal();
}

async function saveClient(event) {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.assigned_user_id = activeUserId();
    const result = await api("/api/clients", { method: "POST", body: JSON.stringify(data) });
    qs("#clientDialog").close();
    await loadClients();
    await selectClient(result.id || data.id);
    setStatus("Cliente guardado");
  } catch (error) {
    setStatus(error.message);
  }
}

async function saveDoneFollowup(event) {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.user_id = activeUserId();
    if (!data.quote_id) delete data.quote_id;
    await api("/api/followups", { method: "POST", body: JSON.stringify(data) });
    form.reset();
    refreshDoneFollowupClientOptions();
    await loadDoneFollowups();
    if (state.selectedClientId === data.client_id) await selectClient(data.client_id);
    setStatus("Seguimiento guardado");
  } catch (error) {
    setStatus(error.message);
  }
}

async function searchProspects(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.user_id = activeUserId();
  try {
    setStatus("Buscando prospectos en Google Places...");
    const result = await api("/api/prospector/search", { method: "POST", body: JSON.stringify(data) });
    state.prospects = result.prospects || [];
    renderProspects();
    setStatus(`${result.saved} prospectos guardados`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function scanProspectZones() {
  const zones = [...qs("#prospectorZonesMulti").selectedOptions].map((option) => option.value);
  const query = qs("#prospectorQuery").value.trim();
  const limit = qs("#prospectorLimit").value || 20;
  try {
    setStatus("Escaneando zonas...");
    const result = await api("/api/prospector/scan-zones", {
      method: "POST",
      body: JSON.stringify({
        query,
        limit,
        zones,
        rescan: qs("#prospectorRescan").checked,
        user_id: activeUserId(),
      }),
    });
    state.prospects = result.prospects || [];
    renderProspects();
    const skipped = result.skipped?.length ? `, ${result.skipped.length} zonas omitidas` : "";
    setStatus(`${result.saved} prospectos guardados${skipped}`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function startProspectFollowup(prospectId) {
  try {
    await api("/api/prospector/start-followup", {
      method: "POST",
      body: JSON.stringify({ prospect_id: prospectId, user_id: activeUserId() }),
    });
    state.prospectorMode = "followup";
    qsa(".prospector-mode").forEach((button) => {
      button.classList.toggle("active", button.dataset.prospectorMode === "followup");
    });
    qs("#prospectorStatus").value = "en_seguimiento";
    await loadProspects();
    await openProspectDetail(prospectId);
    setStatus("Prospecto agregado a seguimiento");
  } catch (error) {
    setStatus(error.message);
  }
}

async function openProspectDetail(prospectId) {
  try {
    state.selectedProspectId = prospectId;
    state.selectedProspect = await api(`/api/prospector/prospect?id=${encodeURIComponent(prospectId)}`);
    renderProspectDetail();
    qs("#prospectDetail").classList.remove("hidden");
    updateProspectorResultsVisibility();
    qs("#prospectDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    setStatus("Seguimiento de prospecto abierto");
  } catch (error) {
    setStatus(error.message);
  }
}

function renderProspectDetail() {
  const data = state.selectedProspect;
  if (!data) return;
  const prospect = data.prospect;
  qs("#prospectDetailName").textContent = prospect.name;
  qs("#prospectDetailMeta").textContent = [
    prospect.category || "Sin categoria",
    prospect.phone || "Sin telefono",
    prospect.address || "Sin direccion",
    prospect.zone_name || "",
  ].filter(Boolean).join(" - ");
  qs("#prospectDetailActions").innerHTML = prospect.client_id
    ? `<button class="small-action" data-open-client="${escapeAttr(prospect.client_id)}" type="button">Ver cliente ${escapeHtml(prospect.client_code || "")}</button>`
    : `
      <div class="prospect-link-client">
        <input id="detail-client-code" placeholder="Numero de cliente">
        <button class="small-action" id="detailLinkClientBtn" type="button">Asociar cliente</button>
      </div>
    `;
  qs("#prospectQuotes").innerHTML = data.quotes.length ? data.quotes.map(prospectQuoteRecord).join("") : emptyLine("Sin cotizaciones de prospecto");
  qs("#prospectFollowups").innerHTML = data.followups.length ? data.followups.map(prospectFollowupRecord).join("") : emptyLine("Sin seguimientos registrados");
  qs("#prospectPhones").innerHTML = data.phones.length ? data.phones.map(prospectPhoneRecord).join("") : emptyLine("Sin telefonos adicionales");
  qs("#prospectActivity").innerHTML = data.activity.length ? data.activity.map(activityRecord).join("") : emptyLine("Sin actividad");
  qs("#newProspectQuoteBtn")?.addEventListener("click", startProspectQuote);
  qs("#detailLinkClientBtn")?.addEventListener("click", () => linkProspectToClient(prospect.id, "#detail-client-code"));
  qsa("#prospectDetail [data-open-client]").forEach((button) => {
    button.addEventListener("click", () => openMatchedClient(button.dataset.openClient));
  });
  bindProspectQuoteActions();
}

function prospectQuoteRecord(quote) {
  const title = quote.quote_title || quote.title || quote.folio;
  return `
    <article class="record quote-card">
      <header><strong>${escapeHtml(title)}</strong><span>${formatDate(quote.created_at)}</span></header>
      <p>Total ${money.format(quote.total || quote.amount || 0)} - costo de envio autorizado ${money.format(quote.authorized_shipping || 0)}</p>
      ${userMeta(quote.user_name)}
      <div class="quote-actions">
        <button class="small-action" data-prospect-quote="${escapeAttr(quote.id)}">Ver</button>
        <button class="small-action" data-edit-prospect-quote="${escapeAttr(quote.id)}">Editar</button>
        <button class="small-action" data-export-prospect-pdf="${escapeAttr(quote.id)}">PDF</button>
        <button class="small-action" data-export-prospect-xlsx="${escapeAttr(quote.id)}">Excel</button>
        <button class="small-action danger-action" data-delete-prospect-quote="${escapeAttr(quote.id)}">Borrar</button>
      </div>
    </article>
  `;
}

function prospectFollowupRecord(item) {
  return `
    <article class="record">
      <header><strong>${escapeHtml(item.channel)}</strong><span>${formatDate(item.contact_at)}</span></header>
      <p>${escapeHtml(item.outcome)}</p>
      ${item.next_action ? `<p class="linked-quote">Siguiente accion: ${escapeHtml(item.next_action)} ${escapeHtml(formatDate(item.next_action_at))}</p>` : ""}
      ${userMeta(item.user_name)}
      <p>${escapeHtml(item.notes || "")}</p>
    </article>
  `;
}

function prospectPhoneRecord(item) {
  return `
    <article class="record">
      <header><strong>${escapeHtml(item.phone)}</strong><span>${formatDate(item.updated_at)}</span></header>
      <p>${escapeHtml(item.label || "Telefono")}</p>
      <p>${escapeHtml(item.notes || "")}</p>
    </article>
  `;
}

async function saveProspectModule(event, endpoint, successMessage) {
  event.preventDefault();
  if (!state.selectedProspectId) return setStatus("Abre un prospecto en seguimiento");
  try {
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.prospect_id = state.selectedProspectId;
    data.user_id = activeUserId();
    await api(endpoint, { method: "POST", body: JSON.stringify(data) });
    form.reset();
    await openProspectDetail(state.selectedProspectId);
    setStatus(successMessage);
  } catch (error) {
    setStatus(error.message);
  }
}

function switchProspectTab(tab) {
  qsa(".prospect-tab").forEach((button) => button.classList.toggle("active", button.dataset.prospectTab === tab));
  qsa(".prospect-tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === tab));
}

async function linkProspectToClient(prospectId, inputSelector = "") {
  const input = inputSelector ? qs(inputSelector) : qs(`#client-code-${CSS.escape(prospectId)}`);
  const clientCode = input?.value.trim();
  if (!clientCode) return setStatus("Captura el numero de cliente para asociar");
  try {
    const result = await api("/api/prospector/link-client", {
      method: "POST",
      body: JSON.stringify({ prospect_id: prospectId, client_code: clientCode, user_id: activeUserId() }),
    });
    await loadClients();
    await loadProspects();
    if (state.selectedProspectId === prospectId) await openProspectDetail(prospectId);
    await selectClient(result.client_id);
    switchView("clients");
    setStatus("Prospecto asociado al cliente");
  } catch (error) {
    setStatus(error.message);
  }
}

async function openMatchedClient(clientId) {
  try {
    await selectClient(clientId);
    switchView("clients");
    setStatus("Cliente abierto");
  } catch (error) {
    setStatus(error.message);
  }
}

async function checkProspectClients() {
  try {
    setStatus("Verificando clientes existentes...");
    const payload = {
      q: qs("#prospectorFilter")?.value.trim() || "",
      status: qs("#prospectorStatus")?.value || "todos",
      zone: qs("#prospectorZoneFilter")?.value || "todas",
      include_external: true,
    };
    const result = await api("/api/prospector/check-clients", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadProspects();
    setStatus(`${result.matched} coincidencias detectadas de ${result.checked} prospectos revisados`);
  } catch (error) {
    setStatus(error.message);
  }
}

function addQuoteItem(item = {}) {
  const wrapper = document.createElement("div");
  wrapper.className = "quote-item";
  wrapper.innerHTML = `
    <label class="field"><span>CIP</span><input name="cip" list="productSuggestions" value="${escapeAttr(item.cip || "")}"></label>
    <label class="field"><span>Descripcion</span><input name="description" required value="${escapeAttr(item.description || "")}"></label>
    <label class="field"><span>Cantidad</span><input name="quantity" type="number" step="0.01" value="${item.quantity || 1}"></label>
    <label class="field"><span>Precio</span><input name="unit_price" type="number" step="0.01" value="${item.unit_price || 0}"></label>
    <label class="field"><span>Desc %</span><input name="discount_rate" type="number" step="0.01" value="${item.discount_rate || 0}"></label>
    <label class="field"><span>IVA %</span><input name="tax_rate" type="number" step="0.01" value="${item.tax_rate ?? 16}"></label>
    <button type="button" title="Quitar">X</button>
  `;
  wrapper.querySelector("button").addEventListener("click", () => {
    wrapper.remove();
    updateQuoteTotals();
  });
  wrapper.querySelectorAll("input").forEach((input) => input.addEventListener("input", updateQuoteTotals));
  wrapper.querySelector('[name="cip"]').addEventListener("change", () => fillProductPrice(wrapper));
  qs("#quoteItems").appendChild(wrapper);
  updateQuoteTotals();
}

async function fillProductPrice(row) {
  const cipInput = row.querySelector('[name="cip"]');
  const cip = normalizeCipInput(cipInput.value);
  if (!cip) return;
  if (state.quoteContext === "prospect") return fillProspectProduct(row, cip);
  if (!state.selectedClientId) return setStatus("Selecciona un cliente antes de consultar precio");
  try {
    const price = await api(`/api/price?client_id=${encodeURIComponent(state.selectedClientId)}&cip=${encodeURIComponent(cip)}`);
    cipInput.value = price.cip || cip;
    row.querySelector('[name="description"]').value = price.description || "";
    row.querySelector('[name="unit_price"]').value = price.unit_price || 0;
    row.querySelector('[name="discount_rate"]').value = price.discount_rate || 0;
    row.querySelector('[name="tax_rate"]').value = price.tax_rate ?? 16;
    updateQuoteTotals();
    setStatus(`Precio ${price.list || ""} aplicado`);
  } catch (error) {
    setStatus(error.message);
  }
}

async function fillProspectProduct(row, cip) {
  try {
    const product = await api(`/api/product-info?cip=${encodeURIComponent(cip)}`);
    row.querySelector('[name="cip"]').value = product.cip || cip;
    row.querySelector('[name="description"]').value = product.description || "";
    if (Number(product.unit_price || 0) > 0) row.querySelector('[name="unit_price"]').value = product.unit_price;
    row.querySelector('[name="discount_rate"]').value = product.discount_rate || 0;
    row.querySelector('[name="tax_rate"]').value = product.tax_rate ?? 16;
    updateQuoteTotals();
    setStatus(Number(product.unit_price || 0) > 0 ? "Producto de lista general aplicado" : "Descripcion de producto aplicada; captura el precio");
  } catch (error) {
    setStatus(error.message);
  }
}

function getQuoteItems() {
  return qsa(".quote-item").map((row) => {
    const item = {};
    row.querySelectorAll("input").forEach((input) => item[input.name] = input.value);
    item.cip = normalizeCipInput(item.cip);
    return item;
  }).filter((item) => item.description);
}

function normalizeCipInput(value) {
  return String(value || "").split(" - ")[0].trim();
}

function updateQuoteTotals() {
  const totals = getQuoteItems().reduce((acc, item) => {
    const quantity = Number(item.quantity || 0);
    const unit = Number(item.unit_price || 0);
    const discountRate = Number(item.discount_rate || 0) / 100;
    const taxRate = Number(item.tax_rate || 0) / 100;
    const gross = quantity * unit;
    const discount = gross * discountRate;
    const tax = (gross - discount) * taxRate;
    acc.subtotal += gross;
    acc.discount += discount;
    acc.tax += tax;
    return acc;
  }, { subtotal: 0, discount: 0, tax: 0 });
  totals.total = totals.subtotal - totals.discount + totals.tax;
  qs("#quoteSubtotal").textContent = money.format(totals.subtotal);
  qs("#quoteDiscount").textContent = money.format(totals.discount);
  qs("#quoteTax").textContent = money.format(totals.tax);
  qs("#quoteTotal").textContent = money.format(totals.total);
  qs("#quoteShipping").textContent = money.format(totals.total * 0.08);
}

async function saveQuote(event) {
  event.preventDefault();
  if (state.quoteContext === "prospect") return saveProspectQuote(event);
  if (!state.selectedClientId) return setStatus("Selecciona un cliente antes de cotizar");
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.client_id = state.selectedClientId;
  data.user_id = activeUserId();
  data.items = getQuoteItems();
  if (state.selectedClient?.client?.code === "100000" && !(data.quote_recipient || "").trim()) {
    return setStatus("Captura empresa/persona cotizada para cliente 100000");
  }
  if (!data.items.length) return setStatus("Agrega al menos una partida");
  if (state.editingQuoteId) data.id = state.editingQuoteId;
  const method = state.editingQuoteId ? "PUT" : "POST";
  const result = await api("/api/quotes", { method, body: JSON.stringify(data) });
  resetQuoteForm();
  await selectClient(state.selectedClientId);
  switchView("clients");
  setStatus(`Cotizacion ${result.folio} guardada`);
}

async function saveProspectQuote(event) {
  if (!state.selectedProspectId) return setStatus("Abre un prospecto antes de cotizar");
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.prospect_id = state.selectedProspectId;
  data.user_id = activeUserId();
  data.items = getQuoteItems();
  if (!data.items.length) return setStatus("Agrega al menos una partida");
  if (state.editingProspectQuoteId) data.id = state.editingProspectQuoteId;
  const method = state.editingProspectQuoteId ? "PUT" : "POST";
  const result = await api("/api/prospector/quotes", { method, body: JSON.stringify(data) });
  const prospectId = state.selectedProspectId;
  resetQuoteForm();
  switchView("prospector");
  await openProspectDetail(prospectId);
  switchProspectTab("prospect-quotes");
  setStatus(`Cotizacion ${result.folio} guardada`);
}

async function saveFollowup(event) {
  event.preventDefault();
  if (!state.selectedClientId) return setStatus("Selecciona un cliente antes de registrar seguimiento");
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.client_id = state.selectedClientId;
  data.user_id = activeUserId();
  if (!data.quote_id) delete data.quote_id;
  await api("/api/followups", { method: "POST", body: JSON.stringify(data) });
  form.reset();
  await selectClient(state.selectedClientId);
  switchView("clients");
  switchTab("followups");
  await loadDoneFollowups().catch(() => {});
  setStatus("Seguimiento guardado");
}

function refreshFollowupQuoteOptions() {
  const select = qs("#followupQuote");
  if (!select) return;
  const quotes = state.selectedClient?.quotes || [];
  const current = select.value;
  select.innerHTML = `<option value="">Sin cotizacion relacionada</option>` + quotes.map((quote) => {
    const title = quote.quote_title || quote.folio;
    return `<option value="${escapeAttr(quote.id)}">${escapeHtml(title)} - ${money.format(quote.total || 0)}</option>`;
  }).join("");
  if (quotes.some((quote) => quote.id === current)) select.value = current;
}

async function importClients() {
  const csv = qs("#csvImport").value;
  const result = await api("/api/import/clients", {
    method: "POST",
    body: JSON.stringify({ csv, assigned_user_id: activeUserId() }),
  });
  qs("#csvImport").value = "";
  await loadClients();
  setStatus(`${result.imported} clientes importados`);
}

async function loadBankAccounts() {
  try {
    const accounts = await api("/api/settings/bank-accounts");
    qs("#bankAccounts").innerHTML = accounts.length
      ? accounts.map((account) => `
        <article class="record">
          <header><strong>${escapeHtml(account.company)}</strong><span>${account.enabled ? "Activa" : "Inactiva"}</span></header>
          <p>${escapeHtml(account.beneficiary || "")}</p>
          <p>${escapeHtml(account.bank || "")} · ${escapeHtml(account.account || "")} · ${escapeHtml(account.clabe || "")}</p>
        </article>
      `).join("")
      : emptyLine("Sin cuentas bancarias capturadas");
  } catch (error) {
    setStatus(error.message);
  }
}

async function saveBankAccount(event) {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.enabled = data.enabled === "1";
    await api("/api/settings/bank-accounts", { method: "POST", body: JSON.stringify(data) });
    await loadBankAccounts();
    setStatus("Cuenta bancaria guardada");
  } catch (error) {
    setStatus(error.message);
  }
}

async function loadProductSuggestions() {
  try {
    const products = await api("/api/products?q=");
    qs("#productSuggestions").innerHTML = products
      .map((p) => `<option value="${escapeAttr(`${p.cip} - ${p.descripcion || ""}`)}"></option>`)
      .join("");
  } catch {
    qs("#productSuggestions").innerHTML = "";
  }
}

async function login(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const user = await api("/api/auth/login", { method: "POST", body: JSON.stringify(data) });
    state.currentUser = user;
    qs("#loginScreen").classList.add("hidden");
    await loadUsers();
    await loadClients();
    await loadZones();
    await loadProductSuggestions();
    setStatus(`Sesion iniciada: ${user.name}`);
  } catch (error) {
    qs("#loginMessage").textContent = error.message;
  }
}

function logout() {
  state.currentUser = null;
  state.selectedClientId = null;
  state.selectedClient = null;
  state.clients = [];
  qs("#clients").innerHTML = "";
  qs("#clientCount").textContent = "0";
  qs("#clientSearch").value = "";
  qs("#activeUser").innerHTML = "";
  qs("#clientCard").classList.add("hidden");
  qs("#emptyState").classList.remove("hidden");
  qs("#loginForm").reset();
  qs("#loginMessage").textContent = "";
  qs("#loginScreen").classList.remove("hidden");
  switchView("clients");
  setStatus("Sesion cerrada");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;");
}

function bindEvents() {
  qs("#loginForm").addEventListener("submit", login);
  qs("#logoutBtn").addEventListener("click", logout);
  qs("#clientSearch").addEventListener("input", debounce(loadClients, 250));
  qs("#productFilter").addEventListener("input", renderProducts);
  qs("#invoiceFilter").addEventListener("input", renderInvoices);
  qs("#newClientBtn").addEventListener("click", () => openClientDialog());
  qs("#editClientBtn").addEventListener("click", () => openClientDialog(state.selectedClient.client));
  qs("#cancelClientBtn").addEventListener("click", () => qs("#clientDialog").close());
  qs("#clientForm").addEventListener("submit", saveClient);
  qs("#closePreviewBtn").addEventListener("click", () => qs("#previewDialog").close());
  qsa(".nav-button").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  qsa(".tab").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  qs("#addItemBtn").addEventListener("click", () => addQuoteItem());
  qs("#quoteForm").addEventListener("submit", saveQuote);
  qs("#followupForm").addEventListener("submit", saveFollowup);
  qs("#doneFollowupForm").addEventListener("submit", saveDoneFollowup);
  qs("#doneFollowupSearch").addEventListener("input", debounce(loadDoneFollowups, 250));
  qs("#refreshDoneFollowupsBtn").addEventListener("click", loadDoneFollowups);
  qs("#importClientsBtn").addEventListener("click", importClients);
  qs("#bankAccountForm").addEventListener("submit", saveBankAccount);
  qs("#prospectorForm").addEventListener("submit", searchProspects);
  qs("#prospectorFilter").addEventListener("input", debounce(loadProspects, 250));
  qs("#prospectorStatus").addEventListener("change", loadProspects);
  qs("#prospectorZoneFilter").addEventListener("change", loadProspects);
  qs("#refreshProspectsBtn").addEventListener("click", loadProspects);
  qs("#checkProspectClientsBtn").addEventListener("click", checkProspectClients);
  qs("#scanZonesBtn").addEventListener("click", scanProspectZones);
  qsa(".prospector-mode").forEach((button) => {
    button.addEventListener("click", () => switchProspectorMode(button.dataset.prospectorMode));
  });
  qsa(".prospect-tab").forEach((button) => {
    button.addEventListener("click", () => switchProspectTab(button.dataset.prospectTab));
  });
  qs("#prospectFollowupForm").addEventListener("submit", (event) => saveProspectModule(event, "/api/prospector/followups", "Seguimiento de prospecto guardado"));
  qs("#prospectPhoneForm").addEventListener("submit", (event) => saveProspectModule(event, "/api/prospector/phones", "Telefono de prospecto guardado"));
}

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function boot() {
  bindEvents();
  addQuoteItem();
  setStatus("Inicia sesion para consultar ventas");
}

boot().catch((error) => setStatus(error.message));
