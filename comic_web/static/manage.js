const PAGE_SIZE = 30;
const DESTINATION_STORAGE_KEY = "comic-library.migration.destination";

const state = {
  downloadTab: "single",
  downloadPage: 1,
  batchPreviewPage: 1,
  batchTaskPage: 1,
  incrementalPage: 1,
  booksPage: 1,
  migrationPage: 1,
  selectionPage: 1,
  selectedBookIds: new Set(),
  selectionPageIds: [],
  validatedDestination: "",
  pollTimer: null,
  downloadPollTimer: null,
  batchTaskPollTimer: null,
  incrementalPollTimer: null,
  batchAnalysisId: "",
  batchSelectedKeys: new Set(),
  batchPreviewPageKeys: [],
  incrementalPreviewTaskId: 0,
  incrementalAnalysisId: "",
  incrementalSelectedKeys: new Set(),
  deletionPage: 1,
  deleteChapterPage: 1,
  deleteChapterBookId: "",
  selectedDeletionChapterIds: new Set(),
  deleteChapterPageIds: [],
  deletionDialogBookId: "",
  deletionDialogChapterIds: [],
  deletionPollTimer: null,
  recrawlTaskId: 0,
  recrawlTaskKind: "",
  auth: {role: "guest", csrf_token: ""},
};

const elements = {
  downloadsView: document.querySelector("#downloadsView"),
  batchCreateView: document.querySelector("#batchCreateView"),
  booksView: document.querySelector("#booksView"),
  migrationsView: document.querySelector("#migrationsView"),
  newMigrationView: document.querySelector("#newMigrationView"),
  deleteChaptersView: document.querySelector("#deleteChaptersView"),
  deletionsView: document.querySelector("#deletionsView"),
  usersView: document.querySelector("#usersView"),
  settingsView: document.querySelector("#settingsView"),
  booksNav: document.querySelector("#booksNav"),
  downloadsNav: document.querySelector("#downloadsNav"),
  migrationsNav: document.querySelector("#migrationsNav"),
  usersNav: document.querySelector("#usersNav"),
  settingsNav: document.querySelector("#settingsNav"),
  manageAuthButton: document.querySelector("#manageAuthButton"),

  downloadTabs: document.querySelector("#downloadTabs"),
  singleDownloadTab: document.querySelector("#singleDownloadTab"),
  batchDownloadTab: document.querySelector("#batchDownloadTab"),
  incrementalPullTab: document.querySelector("#incrementalPullTab"),
  singleDownloadsPanel: document.querySelector("#singleDownloadsPanel"),
  batchTasksPanel: document.querySelector("#batchTasksPanel"),
  incrementalPullPanel: document.querySelector("#incrementalPullPanel"),
  openBatchCreate: document.querySelector("#openBatchCreate"),
  downloadSummary: document.querySelector("#downloadSummary"),
  downloadSearch: document.querySelector("#downloadSearch"),
  downloadStatusFilter: document.querySelector("#downloadStatusFilter"),
  downloadRefreshButton: document.querySelector("#downloadRefreshButton"),
  downloadTasksBody: document.querySelector("#downloadTasksBody"),
  downloadTasksMessage: document.querySelector("#downloadTasksMessage"),
  downloadPagination: document.querySelector("#downloadPagination"),
  openDownloadDialog: document.querySelector("#openDownloadDialog"),
  downloadDialog: document.querySelector("#downloadDialog"),
  closeDownloadDialog: document.querySelector("#closeDownloadDialog"),
  cancelDownloadDialog: document.querySelector("#cancelDownloadDialog"),
  downloadComicId: document.querySelector("#downloadComicId"),
  downloadUrl: document.querySelector("#downloadUrl"),
  downloadAccessLevel: document.querySelector("#downloadAccessLevel"),
  downloadDialogMessage: document.querySelector("#downloadDialogMessage"),
  confirmDownloadTask: document.querySelector("#confirmDownloadTask"),
  downloadRootErrorDialog: document.querySelector("#downloadRootErrorDialog"),
  downloadRootErrorMessage: document.querySelector("#downloadRootErrorMessage"),
  downloadRootErrorPath: document.querySelector("#downloadRootErrorPath"),
  closeDownloadRootError: document.querySelector("#closeDownloadRootError"),
  cancelDownloadRootError: document.querySelector("#cancelDownloadRootError"),

  batchParentId: document.querySelector("#batchParentId"),
  batchComicName: document.querySelector("#batchComicName"),
  batchSeriesUrl: document.querySelector("#batchSeriesUrl"),
  batchAccessLevel: document.querySelector("#batchAccessLevel"),
  analyzeBatchButton: document.querySelector("#analyzeBatchButton"),
  batchAnalyzeMessage: document.querySelector("#batchAnalyzeMessage"),
  batchPreviewPanel: document.querySelector("#batchPreviewPanel"),
  batchPreviewSummary: document.querySelector("#batchPreviewSummary"),
  batchPreviewSearch: document.querySelector("#batchPreviewSearch"),
  batchPreviewPageSize: document.querySelector("#batchPreviewPageSize"),
  clearBatchSelectionButton: document.querySelector("#clearBatchSelectionButton"),
  batchSelectPageCheckbox: document.querySelector("#batchSelectPageCheckbox"),
  batchPreviewBody: document.querySelector("#batchPreviewBody"),
  batchPreviewMessage: document.querySelector("#batchPreviewMessage"),
  batchPreviewPagination: document.querySelector("#batchPreviewPagination"),
  batchSelectionSummary: document.querySelector("#batchSelectionSummary"),
  createBatchTasksButton: document.querySelector("#createBatchTasksButton"),
  batchCreateMessage: document.querySelector("#batchCreateMessage"),
  batchTaskSummary: document.querySelector("#batchTaskSummary"),
  batchTaskRefreshButton: document.querySelector("#batchTaskRefreshButton"),
  batchTaskSearch: document.querySelector("#batchTaskSearch"),
  batchTaskStatusFilter: document.querySelector("#batchTaskStatusFilter"),
  batchTaskPageSize: document.querySelector("#batchTaskPageSize"),
  batchTasksBody: document.querySelector("#batchTasksBody"),
  batchTasksMessage: document.querySelector("#batchTasksMessage"),
  batchTaskPagination: document.querySelector("#batchTaskPagination"),

  incrementalSearch: document.querySelector("#incrementalSearch"),
  incrementalComicStatusFilter: document.querySelector("#incrementalComicStatusFilter"),
  incrementalStateFilter: document.querySelector("#incrementalStateFilter"),
  incrementalPageSize: document.querySelector("#incrementalPageSize"),
  incrementalRefreshButton: document.querySelector("#incrementalRefreshButton"),
  incrementalSummary: document.querySelector("#incrementalSummary"),
  incrementalTasksBody: document.querySelector("#incrementalTasksBody"),
  incrementalTasksMessage: document.querySelector("#incrementalTasksMessage"),
  incrementalPagination: document.querySelector("#incrementalPagination"),
  incrementalDialog: document.querySelector("#incrementalDialog"),
  incrementalDialogTitle: document.querySelector("#incrementalDialogTitle"),
  incrementalDialogSummary: document.querySelector("#incrementalDialogSummary"),
  incrementalDialogTableShell: document.querySelector("#incrementalDialogTableShell"),
  incrementalSelectPageCheckbox: document.querySelector("#incrementalSelectPageCheckbox"),
  incrementalPreviewBody: document.querySelector("#incrementalPreviewBody"),
  incrementalDialogMessage: document.querySelector("#incrementalDialogMessage"),
  closeIncrementalDialog: document.querySelector("#closeIncrementalDialog"),
  cancelIncrementalDialog: document.querySelector("#cancelIncrementalDialog"),
  confirmIncrementalPull: document.querySelector("#confirmIncrementalPull"),

  deleteChaptersTitle: document.querySelector("#deleteChaptersTitle"),
  deleteChaptersSummary: document.querySelector("#deleteChaptersSummary"),
  deleteChaptersBack: document.querySelector("#deleteChaptersBack"),
  deleteChapterSearch: document.querySelector("#deleteChapterSearch"),
  deleteChapterStatusFilter: document.querySelector("#deleteChapterStatusFilter"),
  deleteChapterOrder: document.querySelector("#deleteChapterOrder"),
  deleteChapterPageSize: document.querySelector("#deleteChapterPageSize"),
  deleteChapterSelectionSummary: document.querySelector("#deleteChapterSelectionSummary"),
  deleteChapterSelectPage: document.querySelector("#deleteChapterSelectPage"),
  deleteChapterClearSelection: document.querySelector("#deleteChapterClearSelection"),
  createChapterDeletion: document.querySelector("#createChapterDeletion"),
  deleteChaptersBody: document.querySelector("#deleteChaptersBody"),
  deleteChaptersMessage: document.querySelector("#deleteChaptersMessage"),
  deleteChaptersPagination: document.querySelector("#deleteChaptersPagination"),
  deletionSummary: document.querySelector("#deletionSummary"),
  deletionSearch: document.querySelector("#deletionSearch"),
  deletionStatusFilter: document.querySelector("#deletionStatusFilter"),
  deletionTypeFilter: document.querySelector("#deletionTypeFilter"),
  deletionRefreshButton: document.querySelector("#deletionRefreshButton"),
  deletionTasksBody: document.querySelector("#deletionTasksBody"),
  deletionTasksMessage: document.querySelector("#deletionTasksMessage"),
  deletionPagination: document.querySelector("#deletionPagination"),
  deletionDialog: document.querySelector("#deletionDialog"),
  deletionDialogTitle: document.querySelector("#deletionDialogTitle"),
  deletionDialogSummary: document.querySelector("#deletionDialogSummary"),
  deletionVerificationCode: document.querySelector("#deletionVerificationCode"),
  deletionDialogMessage: document.querySelector("#deletionDialogMessage"),
  closeDeletionDialog: document.querySelector("#closeDeletionDialog"),
  cancelDeletionDialog: document.querySelector("#cancelDeletionDialog"),
  confirmDeletionDialog: document.querySelector("#confirmDeletionDialog"),
  recrawlDialog: document.querySelector("#recrawlDialog"),
  recrawlDialogSummary: document.querySelector("#recrawlDialogSummary"),
  recrawlDialogMessage: document.querySelector("#recrawlDialogMessage"),
  closeRecrawlDialog: document.querySelector("#closeRecrawlDialog"),
  cancelRecrawlDialog: document.querySelector("#cancelRecrawlDialog"),
  confirmRecrawlDialog: document.querySelector("#confirmRecrawlDialog"),
  newUsername: document.querySelector("#newUsername"),
  newPassword: document.querySelector("#newPassword"),
  newUserRole: document.querySelector("#newUserRole"),
  createUserButton: document.querySelector("#createUserButton"),
  createUserMessage: document.querySelector("#createUserMessage"),
  usersBody: document.querySelector("#usersBody"),
  usersMessage: document.querySelector("#usersMessage"),
  settingsRefreshButton: document.querySelector("#settingsRefreshButton"),
  settingsBody: document.querySelector("#settingsBody"),
  settingsMessage: document.querySelector("#settingsMessage"),

  managedBooksSummary: document.querySelector("#managedBooksSummary"),
  managedBookSearch: document.querySelector("#managedBookSearch"),
  managedStorageFilter: document.querySelector("#managedStorageFilter"),
  managedStatusFilter: document.querySelector("#managedStatusFilter"),
  managedBooksBody: document.querySelector("#managedBooksBody"),
  managedBooksMessage: document.querySelector("#managedBooksMessage"),
  managedBooksPagination: document.querySelector("#managedBooksPagination"),

  migrationSummary: document.querySelector("#migrationSummary"),
  migrationSearch: document.querySelector("#migrationSearch"),
  migrationStatusFilter: document.querySelector("#migrationStatusFilter"),
  migrationRefreshButton: document.querySelector("#migrationRefreshButton"),
  migrationRecordsBody: document.querySelector("#migrationRecordsBody"),
  migrationRecordsMessage: document.querySelector("#migrationRecordsMessage"),
  migrationPagination: document.querySelector("#migrationPagination"),

  migrationDestination: document.querySelector("#migrationDestination"),
  pickDirectoryButton: document.querySelector("#pickDirectoryButton"),
  validateDirectoryButton: document.querySelector("#validateDirectoryButton"),
  destinationMessage: document.querySelector("#destinationMessage"),
  migrationBookSearch: document.querySelector("#migrationBookSearch"),
  selectPageCheckbox: document.querySelector("#selectPageCheckbox"),
  clearSelectionButton: document.querySelector("#clearSelectionButton"),
  selectionSummary: document.querySelector("#selectionSummary"),
  migrationBooksBody: document.querySelector("#migrationBooksBody"),
  migrationBooksMessage: document.querySelector("#migrationBooksMessage"),
  migrationBooksPagination: document.querySelector("#migrationBooksPagination"),
  migrationCode: document.querySelector("#migrationCode"),
  createMigrationButton: document.querySelector("#createMigrationButton"),
  createMigrationMessage: document.querySelector("#createMigrationMessage"),

  toast: document.querySelector("#toast"),
};

const STATUS_LABELS = {
  available: "可用",
  external_offline: "外盘未连接",
  external_mismatch: "外盘身份不匹配",
  content_missing: "漫画文件缺失",
  local_missing: "本地目录缺失",
  migrating: "迁移中",
  queued: "等待迁移",
  pending: "等待下载",
  paused: "已暂停",
  running: "下载中",
  completed: "已完成",
  completed_with_warning: "完成但有警告",
  skipped_existing: "已存在",
  failed: "失败",
  blocked_external: "外盘未连接",
  interrupted: "已中断",
  waiting_dependency: "等待前置任务",
};

const STAGE_LABELS = {
  queued: "等待队列",
  paused: "已暂停",
  starting: "准备启动",
  opening_page: "打开页面",
  extracting_urls: "提取图片地址",
  downloading: "下载图片",
  validating: "校验下载",
  importing_book: "写入漫画库",
  importing_chapter: "写入章节",
  preflight: "预检",
  copying: "复制文件",
  verifying: "校验文件",
  publishing: "发布目录",
  switching_storage: "切换存储",
  cleaning_source: "清理本地",
  verifying_source: "删除前核对",
  stage_verifying: "暂存校验",
  final_verifying: "最终校验",
  source_quarantining: "隔离本地",
  source_verifying: "删除前核对",
  source_deleting: "清理本地",
  completed: "完成",
  preflight: "删除预检",
  checking_storage: "检查存储",
  quarantining: "隔离资源",
  deleting_data: "删除数据",
  rebuilding_index: "刷新索引",
  cleaning_resource: "清理隔离资源",
};

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    elements.toast.classList.add("hidden");
  }, 3500);
}

function setMessage(element, message = "") {
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(state.auth.csrf_token
        ? {"X-CSRF-Token": state.auth.csrf_token}
        : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.code = payload.code || "";
    error.details = payload.details || {};
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function initializeAuth() {
  const payload = await fetchJSON("/api/auth/session");
  if (payload.role !== "admin") {
    location.href = "/login";
    throw new Error("需要管理员权限");
  }
  state.auth = payload;
  elements.manageAuthButton.textContent = `${payload.username} · 退出`;
  elements.manageAuthButton.onclick = async () => {
    await fetchJSON("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    location.href = "/login";
  };
}

function statusLabel(value) {
  return STATUS_LABELS[value] || value || "未知";
}

function stageLabel(value) {
  return STAGE_LABELS[value] || value || "";
}

function formatTime(value) {
  if (!value) return "—";
  return String(value).replace("T", " ").slice(0, 19);
}

function setView(view) {
  [
    elements.downloadsView,
    elements.batchCreateView,
    elements.booksView,
    elements.deleteChaptersView,
    elements.deletionsView,
    elements.usersView,
    elements.settingsView,
    elements.migrationsView,
    elements.newMigrationView,
  ].forEach((element) => element.classList.add("hidden"));
  if (view) view.classList.remove("hidden");
  elements.booksNav.classList.toggle("active", view === elements.booksView);
  elements.downloadsNav.classList.toggle(
    "active",
    view === elements.downloadsView || view === elements.batchCreateView,
  );
  elements.migrationsNav.classList.toggle(
    "active",
    view === elements.migrationsView,
  );
  elements.usersNav.classList.toggle(
    "active",
    view === elements.usersView,
  );
  elements.settingsNav.classList.toggle(
    "active",
    view === elements.settingsView,
  );
}

function setDownloadTab(tab) {
  state.downloadTab = ["single", "batch", "incremental"].includes(tab)
    ? tab
    : "single";
  const batchActive = state.downloadTab === "batch";
  const incrementalActive = state.downloadTab === "incremental";
  const singleActive = state.downloadTab === "single";
  elements.singleDownloadsPanel.classList.toggle("hidden", !singleActive);
  elements.batchTasksPanel.classList.toggle("hidden", !batchActive);
  elements.incrementalPullPanel.classList.toggle(
    "hidden",
    !incrementalActive,
  );
  elements.singleDownloadTab.classList.toggle("active", singleActive);
  elements.batchDownloadTab.classList.toggle("active", batchActive);
  elements.incrementalPullTab.classList.toggle("active", incrementalActive);
  elements.singleDownloadTab.setAttribute(
    "aria-selected",
    String(singleActive),
  );
  elements.batchDownloadTab.setAttribute(
    "aria-selected",
    String(batchActive),
  );
  elements.incrementalPullTab.setAttribute(
    "aria-selected",
    String(incrementalActive),
  );
  elements.singleDownloadTab.tabIndex = singleActive ? 0 : -1;
  elements.batchDownloadTab.tabIndex = batchActive ? 0 : -1;
  elements.incrementalPullTab.tabIndex = incrementalActive ? 0 : -1;
  elements.openDownloadDialog.classList.toggle("hidden", !singleActive);
  elements.openBatchCreate.classList.toggle("hidden", !batchActive);
  clearTimeout(state.downloadPollTimer);
  clearTimeout(state.batchTaskPollTimer);
  clearTimeout(state.incrementalPollTimer);
  clearTimeout(state.deletionPollTimer);
}

function updateQuery(values) {
  const url = new URL(location.href);
  Object.entries(values).forEach(([key, value]) => {
    if (value) url.searchParams.set(key, String(value));
    else url.searchParams.delete(key);
  });
  history.replaceState({}, "", url);
}

function createPageButton(label, page, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `page-button${options.current ? " current" : ""}`;
  button.textContent = label;
  button.disabled = Boolean(options.disabled);
  if (options.current) button.setAttribute("aria-current", "page");
  if (!button.disabled && !options.current) {
    button.addEventListener("click", () => options.onPage(page));
  }
  return button;
}

function pageSet(current, total) {
  return [...new Set([1, total, current - 1, current, current + 1])]
    .filter((page) => page >= 1 && page <= total)
    .sort((a, b) => a - b);
}

function renderPagination(container, current, total, onPage) {
  container.replaceChildren();
  if (!total) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  container.append(
    createPageButton("首页", 1, {
      disabled: current <= 1,
      onPage,
    }),
    createPageButton("上一页", current - 1, {
      disabled: current <= 1,
      onPage,
    }),
  );
  let previous = 0;
  pageSet(current, total).forEach((page) => {
    if (previous && page > previous + 1) {
      const gap = document.createElement("span");
      gap.className = "page-ellipsis";
      gap.textContent = "…";
      container.append(gap);
    }
    container.append(
      createPageButton(String(page), page, {
        current: page === current,
        onPage,
      }),
    );
    previous = page;
  });
  container.append(
    createPageButton("下一页", current + 1, {
      disabled: current >= total,
      onPage,
    }),
    createPageButton("尾页", total, {
      disabled: current >= total,
      onPage,
    }),
    Object.assign(document.createElement("span"), {
      className: "pagination-info",
      textContent: `第 ${current} / ${total} 页`,
    }),
  );
}

function appendCell(row, content, className = "") {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  if (content instanceof Node) cell.append(content);
  else cell.textContent = content;
  row.append(cell);
  return cell;
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status || "unknown"}`;
  badge.textContent = statusLabel(status);
  return badge;
}

function createBookIdentity(book, showMeta = true) {
  const wrapper = document.createElement("div");
  wrapper.className = "table-book";
  const image = document.createElement("img");
  image.loading = "lazy";
  image.src = book.thumbnail_url || book.cover_url;
  image.alt = "";
  image.addEventListener("error", () => image.classList.add("image-error"));
  const text = document.createElement("div");
  const title = document.createElement("a");
  title.href = `/reader/${encodeURIComponent(book.id)}`;
  title.textContent = book.title;
  title.title = book.title;
  text.append(title);
  if (showMeta) {
    const meta = document.createElement("span");
    meta.textContent = `ID ${book.id} · ${book.folder_name}`;
    text.append(meta);
  }
  wrapper.append(image, text);
  return wrapper;
}

function createSelectableBook(book) {
  const row = document.createElement("tr");
  const checkboxCell = document.createElement("td");
  checkboxCell.className = "checkbox-column";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selectedBookIds.has(book.id);
  checkbox.dataset.selectBookId = book.id;
  checkbox.setAttribute("aria-label", `选择 ${book.title}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selectedBookIds.add(book.id);
    else state.selectedBookIds.delete(book.id);
    updateSelectionSummary();
    syncPageSelectionControl();
  });
  checkboxCell.append(checkbox);
  row.append(checkboxCell);
  appendCell(row, book.id, "mono-cell");
  appendCell(row, createBookIdentity(book, false));
  appendCell(row, `${book.page_count} 页`);
  appendCell(row, createStatusBadge(book.availability));
  return row;
}

function createDownloadStatusCell(task) {
  const wrapper = document.createElement("div");
  wrapper.className = "migration-state-cell";
  wrapper.append(createStatusBadge(task.status));
  const stage = stageLabel(task.stage);
  if (stage && task.status === "running") {
    wrapper.append(
      Object.assign(document.createElement("span"), { textContent: stage }),
    );
  }
  if (task.pull_mode === "recrawl") {
    wrapper.append(
      Object.assign(document.createElement("span"), {
        className: "pull-mode-badge",
        textContent: `重新拉取 #${task.recrawl_count || 1}`,
      }),
    );
  }
  const accessLabels = ["所有用户", "登录用户", "管理员"];
  wrapper.append(
    Object.assign(document.createElement("span"), {
      className: "pull-mode-badge",
      textContent: `可见：${accessLabels[Number(task.access_level || 0)] || "未知"}`,
    }),
  );
  return wrapper;
}

function createDownloadMessageCell(task) {
  const message = document.createElement("span");
  message.className = "migration-message";
  if (task.status === "completed") {
    message.textContent = "success";
    message.classList.add("migration-message-success");
  } else if (task.error_message) {
    message.textContent = task.error_message;
    message.title = task.error_message;
    message.classList.add("migration-message-error");
  } else {
    message.textContent = stageLabel(task.stage) || "—";
  }
  return message;
}

function createDownloadActionCell(task) {
  if (task.status === "completed") {
    const recrawl = document.createElement("button");
    recrawl.type = "button";
    recrawl.className = "inline-action";
    recrawl.textContent = "重新拉取";
    recrawl.addEventListener(
      "click",
      () => openRecrawlDialog("download", task),
    );
    return recrawl;
  }
  if (["failed", "interrupted"].includes(task.status)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inline-action";
    button.textContent = "重试";
    button.addEventListener("click", () => retryDownload(task.id));
    return button;
  }
  return "—";
}

async function loadDownloads(page = 1) {
  state.downloadPage = page;
  elements.downloadTasksBody.replaceChildren();
  setMessage(elements.downloadTasksMessage);
  elements.downloadSummary.textContent = "正在读取下载记录……";
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
  });
  const search = elements.downloadSearch.value.trim();
  const status = elements.downloadStatusFilter.value;
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  updateQuery({ page, q: search, status });
  try {
    const payload = await fetchJSON(`/api/manage/downloads?${params}`);
    state.downloadPage = payload.page || 1;
    elements.downloadSummary.textContent = `共 ${payload.total} 条下载记录。`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((task) => {
      const row = document.createElement("tr");
      appendCell(row, task.id, "mono-cell");
      appendCell(row, task.comic_id, "mono-cell");
      const title = document.createElement("span");
      title.className = "download-title";
      title.textContent = task.title || "—";
      title.title = task.title || "";
      appendCell(row, title);
      const url = document.createElement("span");
      url.className = "path-cell";
      url.textContent = task.download_url;
      url.title = task.download_url;
      appendCell(row, url);
      appendCell(row, createDownloadStatusCell(task));
      appendCell(
        row,
        task.total_images
          ? `${task.processed_images} / ${task.total_images}`
          : "—",
      );
      appendCell(row, createDownloadMessageCell(task));
      appendCell(row, formatTime(task.created_at));
      appendCell(row, formatTime(task.finished_at));
      appendCell(row, createDownloadActionCell(task), "action-cell");
      fragment.append(row);
    });
    elements.downloadTasksBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.downloadTasksMessage, "没有找到下载任务。");
    }
    renderPagination(
      elements.downloadPagination,
      state.downloadPage,
      payload.total_pages || 0,
      loadDownloads,
    );
    scheduleDownloadPoll(payload.items);
  } catch (error) {
    setMessage(elements.downloadTasksMessage, `加载失败：${error.message}`);
  }
}

function scheduleDownloadPoll(items) {
  clearTimeout(state.downloadPollTimer);
  const active = items.some((item) =>
    ["pending", "running", "interrupted"].includes(item.status)
  );
  if (!active || document.hidden) return;
  state.downloadPollTimer = setTimeout(
    () => loadDownloads(state.downloadPage),
    2000,
  );
}

function openDownloadDialog() {
  elements.downloadComicId.value = "";
  elements.downloadUrl.value = "";
  elements.downloadAccessLevel.value = "0";
  setMessage(elements.downloadDialogMessage);
  if (typeof elements.downloadDialog.showModal === "function") {
    elements.downloadDialog.showModal();
  } else {
    elements.downloadDialog.setAttribute("open", "");
  }
  elements.downloadComicId.focus();
}

function closeDownloadDialog() {
  if (elements.downloadDialog.open) elements.downloadDialog.close();
  else elements.downloadDialog.removeAttribute("open");
}

function isDownloadRootError(error) {
  return String(error?.code || "").startsWith("download_root_");
}

function showDownloadRootError(error) {
  const details = error.details || {};
  elements.downloadRootErrorMessage.textContent =
    error.message || "下载根目录不可用，请检查系统设置。";
  elements.downloadRootErrorPath.textContent =
    details.path || details.running_path || "download.root 尚未配置";
  if (typeof elements.downloadRootErrorDialog.showModal === "function") {
    elements.downloadRootErrorDialog.showModal();
  } else {
    elements.downloadRootErrorDialog.setAttribute("open", "");
  }
}

function closeDownloadRootError() {
  if (elements.downloadRootErrorDialog.open) {
    elements.downloadRootErrorDialog.close();
  } else {
    elements.downloadRootErrorDialog.removeAttribute("open");
  }
}

async function ensureDownloadRootAvailable() {
  try {
    await fetchJSON("/api/manage/downloads/root-status");
    return true;
  } catch (error) {
    if (isDownloadRootError(error)) {
      showDownloadRootError(error);
      return false;
    }
    throw error;
  }
}

async function createDownloadTask() {
  const comicId = elements.downloadComicId.value.trim();
  const downloadUrl = elements.downloadUrl.value.trim();
  if (!comicId || !downloadUrl) {
    setMessage(elements.downloadDialogMessage, "请输入漫画 ID 和下载 URL。");
    return;
  }
  try {
    if (!(await ensureDownloadRootAvailable())) return;
  } catch (error) {
    setMessage(
      elements.downloadDialogMessage,
      `目录检测失败：${error.message}`,
    );
    return;
  }
  elements.confirmDownloadTask.disabled = true;
  setMessage(elements.downloadDialogMessage, "正在建立下载任务……");
  try {
    const payload = await fetchJSON("/api/manage/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        comic_id: comicId,
        download_url: downloadUrl,
        access_level: Number(elements.downloadAccessLevel.value),
      }),
    });
    closeDownloadDialog();
    showToast(`下载任务 ${payload.task_id} 已建立`);
    await loadDownloads(1);
  } catch (error) {
    if (isDownloadRootError(error)) {
      showDownloadRootError(error);
    } else {
      setMessage(elements.downloadDialogMessage, `建立失败：${error.message}`);
    }
  } finally {
    elements.confirmDownloadTask.disabled = false;
  }
}

async function retryDownload(taskId) {
  try {
    await fetchJSON(
      `/api/manage/downloads/${encodeURIComponent(taskId)}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    showToast("下载任务已重新排队");
    await loadDownloads(state.downloadPage);
  } catch (error) {
    showToast(`重试失败：${error.message}`);
  }
}

const BATCH_PREVIEW_STATUS_LABELS = {
  available: "新任务",
  already_exists: "已存在",
  active: "已有活动任务",
};

function updateBatchSelectionSummary() {
  elements.batchSelectionSummary.textContent =
    `已选择 ${state.batchSelectedKeys.size} 章。选择可跨分页保留。`;
  elements.createBatchTasksButton.disabled =
    state.batchSelectedKeys.size === 0;
}

function syncBatchPageSelectionControl() {
  const available = elements.batchPreviewBody.querySelectorAll(
    "[data-batch-chapter-key]:not(:disabled)",
  );
  const selected = [...available].filter((checkbox) => checkbox.checked);
  elements.batchSelectPageCheckbox.disabled = available.length === 0;
  elements.batchSelectPageCheckbox.checked =
    available.length > 0 && selected.length === available.length;
  elements.batchSelectPageCheckbox.indeterminate =
    selected.length > 0 && selected.length < available.length;
}

function renderBatchPreview(payload, resetSelection = false) {
  state.batchAnalysisId = payload.analysis_id;
  state.batchPreviewPage = payload.page || 1;
  if (resetSelection) {
    state.batchSelectedKeys = new Set(payload.default_selected_keys || []);
  }
  const counts = payload.status_counts || {};
  elements.batchPreviewSummary.textContent =
    `${payload.comic_name} · ID ${payload.parent_id} · ` +
    `共 ${payload.total} 章，新任务 ${counts.available || 0}，` +
    `已存在 ${counts.already_exists || 0}，` +
    `活动任务 ${counts.active || 0}。`;
  state.batchPreviewPageKeys = payload.items.map((item) => item.chapter_key);
  elements.batchPreviewBody.replaceChildren();
  const fragment = document.createDocumentFragment();
  payload.items.forEach((item) => {
    const row = document.createElement("tr");
    const checkboxCell = document.createElement("td");
    checkboxCell.className = "checkbox-column";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.batchChapterKey = item.chapter_key;
    checkbox.checked =
      item.status === "available" &&
      state.batchSelectedKeys.has(item.chapter_key);
    checkbox.disabled = item.status !== "available";
    checkbox.setAttribute(
      "aria-label",
      `选择 ${item.chapter_title || item.chapter_key}`,
    );
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.batchSelectedKeys.add(item.chapter_key);
      else state.batchSelectedKeys.delete(item.chapter_key);
      updateBatchSelectionSummary();
      syncBatchPageSelectionControl();
    });
    checkboxCell.append(checkbox);
    row.append(checkboxCell);
    appendCell(row, item.sort_order, "mono-cell");
    appendCell(row, item.chapter_key, "mono-cell");
    const title = document.createElement("span");
    title.className = "download-title";
    title.textContent = item.chapter_title || "—";
    title.title = item.chapter_title || "";
    appendCell(row, title);
    appendCell(row, createPathCell(item.download_url));
    const status = createStatusBadge(item.status);
    status.textContent =
      BATCH_PREVIEW_STATUS_LABELS[item.status] || item.status;
    appendCell(row, status);
    fragment.append(row);
  });
  elements.batchPreviewBody.append(fragment);
  elements.batchPreviewPanel.classList.remove("hidden");
  setMessage(
    elements.batchPreviewMessage,
    payload.items.length ? "" : "没有找到章节。",
  );
  renderPagination(
    elements.batchPreviewPagination,
    state.batchPreviewPage,
    payload.total_pages || 0,
    loadBatchPreview,
  );
  updateBatchSelectionSummary();
  syncBatchPageSelectionControl();
}

async function analyzeBatch() {
  const parentId = elements.batchParentId.value.trim();
  const comicName = elements.batchComicName.value.trim();
  const seriesUrl = elements.batchSeriesUrl.value.trim();
  if (!parentId || !comicName || !seriesUrl) {
    setMessage(
      elements.batchAnalyzeMessage,
      "请输入漫画 ID、漫画名称和系列 URL。",
    );
    return;
  }
  try {
    if (!(await ensureDownloadRootAvailable())) return;
  } catch (error) {
    setMessage(
      elements.batchAnalyzeMessage,
      `目录检测失败：${error.message}`,
    );
    return;
  }
  elements.analyzeBatchButton.disabled = true;
  setMessage(elements.batchAnalyzeMessage, "正在读取系列页并解析章节……");
  setMessage(elements.batchCreateMessage);
  try {
    const payload = await fetchJSON(
      "/api/manage/downloads/batch/analyze",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parent_id: parentId,
          comic_name: comicName,
          series_url: seriesUrl,
          access_level: Number(elements.batchAccessLevel.value),
        }),
      },
    );
    elements.batchPreviewSearch.value = "";
    renderBatchPreview(payload, true);
    setMessage(
      elements.batchAnalyzeMessage,
      `预分析完成，共解析到 ${payload.total} 个章节。`,
    );
  } catch (error) {
    if (isDownloadRootError(error)) {
      showDownloadRootError(error);
    } else {
      setMessage(elements.batchAnalyzeMessage, `预分析失败：${error.message}`);
    }
  } finally {
    elements.analyzeBatchButton.disabled = false;
  }
}

async function loadBatchPreview(page = 1) {
  if (!state.batchAnalysisId) return;
  const pageSize = elements.batchPreviewPageSize.value;
  const params = new URLSearchParams({
    page: String(page),
    page_size: pageSize,
  });
  const search = elements.batchPreviewSearch.value.trim();
  if (search) params.set("q", search);
  setMessage(elements.batchPreviewMessage, "正在读取预分析结果……");
  try {
    const payload = await fetchJSON(
      `/api/manage/downloads/batch/analyze/` +
      `${encodeURIComponent(state.batchAnalysisId)}?${params}`,
    );
    renderBatchPreview(payload, false);
  } catch (error) {
    setMessage(elements.batchPreviewMessage, `加载失败：${error.message}`);
  }
}

async function createBatchTasks() {
  if (!state.batchAnalysisId) {
    setMessage(elements.batchCreateMessage, "请先预分析章节。");
    return;
  }
  if (!state.batchSelectedKeys.size) {
    setMessage(elements.batchCreateMessage, "请至少选择一个章节。");
    return;
  }
  try {
    if (!(await ensureDownloadRootAvailable())) return;
  } catch (error) {
    setMessage(
      elements.batchCreateMessage,
      `目录检测失败：${error.message}`,
    );
    return;
  }
  elements.createBatchTasksButton.disabled = true;
  setMessage(elements.batchCreateMessage, "正在校验并生成批量章节任务……");
  try {
    const payload = await fetchJSON("/api/manage/downloads/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis_id: state.batchAnalysisId,
        chapter_keys: [...state.batchSelectedKeys],
      }),
    });
    state.batchAnalysisId = "";
    state.batchSelectedKeys.clear();
    showToast(
      `批次 ${payload.batch_no} 已建立 ${payload.count} 条章节任务`,
    );
    const query = new URLSearchParams({
      q: payload.batch_no,
      page: "1",
      page_size: "30",
    });
    if (payload.index_warning) {
      query.set("warning", payload.index_warning);
    }
    location.href = `/manage/downloads/batch?${query}`;
    return;
  } catch (error) {
    if (isDownloadRootError(error)) {
      showDownloadRootError(error);
    } else {
      setMessage(elements.batchCreateMessage, `创建失败：${error.message}`);
    }
  } finally {
    updateBatchSelectionSummary();
  }
}

function createBatchMessageCell(task) {
  const message = document.createElement("span");
  message.className = "migration-message";
  if (task.status === "completed") {
    message.textContent = "success";
    message.classList.add("migration-message-success");
  } else if (task.status === "skipped_existing") {
    message.textContent = "章节已存在，未重复下载";
    message.classList.add("migration-message-success");
  } else if (task.error_message) {
    message.textContent = task.error_message;
    message.title = task.error_message;
    message.classList.add("migration-message-error");
  } else {
    message.textContent = stageLabel(task.stage) || "—";
  }
  return message;
}

function createBatchActionCell(task) {
  if (task.status === "completed") {
    const recrawl = document.createElement("button");
    recrawl.type = "button";
    recrawl.className = "inline-action";
    recrawl.textContent = "重新拉取";
    recrawl.addEventListener(
      "click",
      () => openRecrawlDialog("batch", task),
    );
    return recrawl;
  }
  if (["failed", "interrupted"].includes(task.status)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inline-action";
    button.textContent = "重试";
    button.addEventListener("click", () => retryBatchTask(task.id));
    return button;
  }
  return "—";
}

async function loadBatchTasks(page = 1) {
  state.batchTaskPage = page;
  elements.batchTasksBody.replaceChildren();
  setMessage(elements.batchTasksMessage);
  elements.batchTaskSummary.textContent = "正在读取批量任务……";
  const params = new URLSearchParams({
    page: String(page),
    page_size: elements.batchTaskPageSize.value,
  });
  const search = elements.batchTaskSearch.value.trim();
  const status = elements.batchTaskStatusFilter.value;
  const pageSize = elements.batchTaskPageSize.value;
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  updateQuery({ page, page_size: pageSize, q: search, status });
  try {
    const payload = await fetchJSON(
      `/api/manage/downloads/batch/tasks?${params}`,
    );
    state.batchTaskPage = payload.page || 1;
    const counts = payload.status_counts || {};
    elements.batchTaskSummary.textContent =
      `共 ${payload.total} 条章节任务 · ` +
      `等待 ${counts.pending || 0} · 下载中 ${counts.running || 0} · ` +
      `完成 ${counts.completed || 0} · 失败 ${counts.failed || 0} · ` +
      `中断 ${counts.interrupted || 0}。`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((task) => {
      const row = document.createElement("tr");
      appendCell(row, task.id, "mono-cell");
      const batch = document.createElement("button");
      batch.type = "button";
      batch.className = "link-button mono-cell";
      batch.textContent = task.batch_no;
      batch.title = "按该批次筛选";
      batch.addEventListener("click", () => {
        elements.batchTaskSearch.value = task.batch_no;
        loadBatchTasks(1);
      });
      appendCell(row, batch);
      appendCell(
        row,
        `${task.parent_id} · ${task.comic_name}`,
        "batch-comic-cell",
      );
      appendCell(
        row,
        `${task.chapter_title} (${task.chapter_key})`,
        "batch-chapter-cell",
      );
      appendCell(row, createPathCell(task.download_url));
      appendCell(row, createDownloadStatusCell(task));
      appendCell(
        row,
        task.total_images
          ? `${task.processed_images} / ${task.total_images}`
          : "—",
      );
      appendCell(row, createBatchMessageCell(task));
      appendCell(row, formatTime(task.created_at));
      appendCell(row, formatTime(task.finished_at));
      appendCell(row, createBatchActionCell(task), "action-cell");
      fragment.append(row);
    });
    elements.batchTasksBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.batchTasksMessage, "没有找到批量章节任务。");
    }
    renderPagination(
      elements.batchTaskPagination,
      state.batchTaskPage,
      payload.total_pages || 0,
      loadBatchTasks,
    );
    scheduleBatchTaskPoll(payload.items);
  } catch (error) {
    setMessage(elements.batchTasksMessage, `加载失败：${error.message}`);
  }
}

function scheduleBatchTaskPoll(items) {
  clearTimeout(state.batchTaskPollTimer);
  const active = items.some((item) =>
    ["pending", "running", "interrupted"].includes(item.status)
  );
  if (!active || document.hidden) return;
  state.batchTaskPollTimer = setTimeout(
    () => loadBatchTasks(state.batchTaskPage),
    2000,
  );
}

async function retryBatchTask(taskId) {
  try {
    await fetchJSON(
      `/api/manage/downloads/batch/tasks/${encodeURIComponent(taskId)}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    showToast("章节任务已重新排队");
    await loadBatchTasks(state.batchTaskPage);
  } catch (error) {
    showToast(`重试失败：${error.message}`);
  }
}

function openRecrawlDialog(kind, task) {
  state.recrawlTaskId = task.id;
  state.recrawlTaskKind = kind;
  const name = kind === "batch"
    ? `${task.comic_name} · ${task.chapter_title}`
    : `${task.comic_id} · ${task.title || task.download_url}`;
  elements.recrawlDialogSummary.textContent =
    `${name} 将重新打开来源页面并下载全部图片。` +
    "成功后整体替换现有资源，失败时旧资源保持不变。";
  setMessage(elements.recrawlDialogMessage);
  if (typeof elements.recrawlDialog.showModal === "function") {
    elements.recrawlDialog.showModal();
  } else {
    elements.recrawlDialog.setAttribute("open", "");
  }
}

function closeRecrawlDialog() {
  if (elements.recrawlDialog.open) elements.recrawlDialog.close();
  else elements.recrawlDialog.removeAttribute("open");
}

async function confirmRecrawl() {
  elements.confirmRecrawlDialog.disabled = true;
  setMessage(elements.recrawlDialogMessage, "正在重新排队……");
  const url = state.recrawlTaskKind === "batch"
    ? `/api/manage/downloads/batch/tasks/${state.recrawlTaskId}/recrawl`
    : `/api/manage/downloads/${state.recrawlTaskId}/recrawl`;
  try {
    await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    closeRecrawlDialog();
    showToast("重新拉取已排队");
    if (state.recrawlTaskKind === "batch") {
      await loadBatchTasks(state.batchTaskPage);
    } else {
      await loadDownloads(state.downloadPage);
    }
  } catch (error) {
    setMessage(elements.recrawlDialogMessage, `操作失败：${error.message}`);
  } finally {
    elements.confirmRecrawlDialog.disabled = false;
  }
}

const INCREMENTAL_STATE_LABELS = {
  idle: "空闲",
  checking: "检查中",
  creating_tasks: "建立任务",
  downloading: "下载中",
  error: "错误",
};

function incrementalStateLabel(value) {
  return INCREMENTAL_STATE_LABELS[value] || value || "未知";
}

function incrementalComicStatusBadge(status) {
  const value = Number(status) === 1 ? 1 : 0;
  const badge = document.createElement("span");
  badge.className = `status-badge ${
    value === 1 ? "status-completed" : "status-running"
  }`;
  badge.textContent = value === 1 ? "已完结" : "连载中";
  return badge;
}

function createIncrementalActionCell(task) {
  const wrapper = document.createElement("div");
  wrapper.className = "incremental-actions";
  const pull = document.createElement("button");
  pull.type = "button";
  pull.className = "inline-action incremental-pull-action";
  pull.textContent = "增量拉取";
  pull.disabled = Number(task.comic_status) === 1;
  if (pull.disabled) {
    pull.title = "该漫画已标记为完结，请先恢复连载状态";
  } else {
    pull.addEventListener("click", () => openIncrementalDialog(task));
  }
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "inline-action";
  toggle.textContent =
    Number(task.comic_status) === 1 ? "恢复连载" : "标记完结";
  toggle.addEventListener("click", () => toggleIncrementalStatus(task));
  wrapper.append(pull, toggle);
  return wrapper;
}

async function loadIncrementalTasks(page = 1) {
  state.incrementalPage = page;
  elements.incrementalTasksBody.replaceChildren();
  setMessage(elements.incrementalTasksMessage);
  elements.incrementalSummary.textContent = "正在读取增量拉取任务……";
  const params = new URLSearchParams({
    page: String(page),
    page_size: elements.incrementalPageSize.value,
  });
  const search = elements.incrementalSearch.value.trim();
  const comicStatus = elements.incrementalComicStatusFilter.value;
  const taskState = elements.incrementalStateFilter.value;
  if (search) params.set("q", search);
  if (comicStatus) params.set("comic_status", comicStatus);
  if (taskState) params.set("task_state", taskState);
  updateQuery({
    page,
    page_size: elements.incrementalPageSize.value,
    q: search,
    comic_status: comicStatus,
    task_state: taskState,
  });
  try {
    const payload = await fetchJSON(
      `/api/manage/downloads/incremental?${params}`,
    );
    state.incrementalPage = payload.page || 1;
    const counts = payload.status_counts || {};
    elements.incrementalSummary.textContent =
      `共 ${payload.total} 条增量任务 · ` +
      `连载中 ${counts["0"] || 0} · 已完结 ${counts["1"] || 0}。`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((task) => {
      const row = document.createElement("tr");
      appendCell(row, task.id, "mono-cell");
      appendCell(row, task.comic_id, "mono-cell");
      appendCell(
        row,
        task.comic_name || "—",
        "incremental-comic-cell",
      );
      appendCell(
        row,
        task.current_chapter_title
          ? `${task.current_chapter_title} (${task.current_chapter_key})`
          : "—",
        "incremental-chapter-cell",
      );
      appendCell(
        row,
        task.latest_known_chapter_title
          ? `${task.latest_known_chapter_title} (${task.latest_known_chapter_key})`
          : "—",
        "incremental-chapter-cell",
      );
      appendCell(row, task.pending_task_count || 0);
      appendCell(row, incrementalComicStatusBadge(task.comic_status));
      const stateBadge = createStatusBadge(task.task_state);
      stateBadge.textContent = incrementalStateLabel(task.task_state);
      appendCell(row, stateBadge);
      appendCell(row, formatTime(task.last_checked_at));
      appendCell(row, formatTime(task.last_pull_at));
      appendCell(row, formatTime(task.last_run_at));
      appendCell(
        row,
        createIncrementalActionCell(task),
        "action-cell",
      );
      fragment.append(row);
    });
    elements.incrementalTasksBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.incrementalTasksMessage, "没有找到增量拉取任务。");
    }
    renderPagination(
      elements.incrementalPagination,
      state.incrementalPage,
      payload.total_pages || 0,
      loadIncrementalTasks,
    );
    scheduleIncrementalPoll(payload.items);
  } catch (error) {
    setMessage(
      elements.incrementalTasksMessage,
      `加载失败：${error.message}`,
    );
  }
}

function scheduleIncrementalPoll(items) {
  clearTimeout(state.incrementalPollTimer);
  const active = items.some((item) =>
    ["checking", "creating_tasks", "downloading"].includes(item.task_state)
  );
  if (!active || document.hidden) return;
  state.incrementalPollTimer = setTimeout(
    () => loadIncrementalTasks(state.incrementalPage),
    2000,
  );
}

function resetIncrementalDialog(task) {
  state.incrementalPreviewTaskId = task.id;
  state.incrementalAnalysisId = "";
  state.incrementalSelectedKeys = new Set();
  elements.incrementalDialogTitle.textContent =
    `增量拉取 · ${task.comic_name || task.comic_id}`;
  elements.incrementalDialogSummary.textContent =
    `当前进度：${task.current_chapter_title || "尚未开始"}`;
  elements.incrementalDialogTableShell.classList.add("hidden");
  elements.incrementalPreviewBody.replaceChildren();
  elements.confirmIncrementalPull.disabled = true;
  setMessage(elements.incrementalDialogMessage);
}

async function openIncrementalDialog(task) {
  resetIncrementalDialog(task);
  if (typeof elements.incrementalDialog.showModal === "function") {
    elements.incrementalDialog.showModal();
  } else {
    elements.incrementalDialog.setAttribute("open", "");
  }
  setMessage(
    elements.incrementalDialogMessage,
    "正在检查来源站的新章节……",
  );
  try {
    const payload = await fetchJSON(
      `/api/manage/downloads/incremental/${encodeURIComponent(task.id)}/preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    if (payload.no_new_chapters) {
      elements.incrementalDialogSummary.textContent =
        `当前进度：${task.current_chapter_title || "尚未开始"} · ` +
        `来源最新：${payload.latest_source_chapter_key || "未知"}`;
      setMessage(
        elements.incrementalDialogMessage,
        "无新的漫画可以拉取。",
      );
      return;
    }
    state.incrementalAnalysisId = payload.analysis_id;
    state.incrementalSelectedKeys = new Set(
      payload.items.map((item) => item.chapter_key),
    );
    elements.incrementalDialogSummary.textContent =
      `当前进度：${task.current_chapter_title || "尚未开始"} · ` +
      `来源最新：${payload.latest_source_chapter_key} · ` +
      `发现 ${payload.new_count} 个新章节`;
    renderIncrementalPreview(payload.items);
    setMessage(
      elements.incrementalDialogMessage,
      "请选择要生成的批量下载章节。",
    );
  } catch (error) {
    setMessage(
      elements.incrementalDialogMessage,
      `检查失败：${error.message}`,
    );
  }
}

function renderIncrementalPreview(items) {
  elements.incrementalDialogTableShell.classList.remove("hidden");
  elements.incrementalPreviewBody.replaceChildren();
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const row = document.createElement("tr");
    const checkboxCell = document.createElement("td");
    checkboxCell.className = "checkbox-column";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.incrementalSelectedKeys.has(item.chapter_key);
    checkbox.dataset.incrementalChapterKey = item.chapter_key;
    checkbox.setAttribute(
      "aria-label",
      `选择 ${item.chapter_title || item.chapter_key}`,
    );
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.incrementalSelectedKeys.add(item.chapter_key);
      } else {
        state.incrementalSelectedKeys.delete(item.chapter_key);
      }
      syncIncrementalDialogSelection();
    });
    checkboxCell.append(checkbox);
    row.append(checkboxCell);
    appendCell(row, item.sort_order, "mono-cell");
    appendCell(row, item.chapter_key, "mono-cell");
    appendCell(row, item.chapter_title || "—");
    appendCell(row, createPathCell(item.download_url));
    fragment.append(row);
  });
  elements.incrementalPreviewBody.append(fragment);
  syncIncrementalDialogSelection();
}

function syncIncrementalDialogSelection() {
  const checkboxes = [
    ...elements.incrementalPreviewBody.querySelectorAll(
      "[data-incremental-chapter-key]",
    ),
  ];
  const selected = checkboxes.filter((checkbox) => checkbox.checked);
  elements.incrementalSelectPageCheckbox.checked =
    checkboxes.length > 0 && selected.length === checkboxes.length;
  elements.incrementalSelectPageCheckbox.indeterminate =
    selected.length > 0 && selected.length < checkboxes.length;
  elements.confirmIncrementalPull.disabled =
    state.incrementalSelectedKeys.size === 0;
}

function closeIncrementalDialog() {
  if (elements.incrementalDialog.open) {
    elements.incrementalDialog.close();
  } else {
    elements.incrementalDialog.removeAttribute("open");
  }
}

async function confirmIncrementalPull() {
  if (!state.incrementalAnalysisId || !state.incrementalSelectedKeys.size) {
    setMessage(
      elements.incrementalDialogMessage,
      "没有可生成的增量章节。",
    );
    return;
  }
  elements.confirmIncrementalPull.disabled = true;
  setMessage(
    elements.incrementalDialogMessage,
    "正在生成批量下载任务……",
  );
  try {
    const payload = await fetchJSON("/api/manage/downloads/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        analysis_id: state.incrementalAnalysisId,
        chapter_keys: [...state.incrementalSelectedKeys],
      }),
    });
    closeIncrementalDialog();
    const query = new URLSearchParams({
      q: payload.batch_no,
      page: "1",
      page_size: "30",
    });
    location.href = `/manage/downloads/batch?${query}`;
  } catch (error) {
    setMessage(
      elements.incrementalDialogMessage,
      `生成失败：${error.message}`,
    );
    elements.confirmIncrementalPull.disabled = false;
  }
}

async function toggleIncrementalStatus(task) {
  const next = Number(task.comic_status) === 1 ? 0 : 1;
  const action = next === 1 ? "标记为已完结" : "恢复为连载中";
  if (!window.confirm(`确定将 ${task.comic_name} ${action}吗？`)) {
    return;
  }
  try {
    await fetchJSON(
      `/api/manage/downloads/incremental/${encodeURIComponent(task.id)}/status`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comic_status: next }),
      },
    );
    showToast("漫画状态已更新");
    await loadIncrementalTasks(state.incrementalPage);
  } catch (error) {
    showToast(`状态更新失败：${error.message}`);
  }
}

function createManagedBookActionCell(book) {
  const wrapper = document.createElement("div");
  wrapper.className = "incremental-actions";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "inline-action delete-action";
  remove.textContent = "删除";
  remove.addEventListener("click", () => previewBookDeletion(book));
  wrapper.append(remove);
  if (book.content_type === "chaptered") {
    remove.textContent = "整部删除";
    const chapters = document.createElement("button");
    chapters.type = "button";
    chapters.className = "inline-action";
    chapters.textContent = "单话删除";
    chapters.addEventListener("click", () => {
      location.href =
        `/manage/books/${encodeURIComponent(book.id)}/delete-chapters`;
    });
    wrapper.append(chapters);
  }
  return wrapper;
}

async function previewBookDeletion(book) {
  state.deletionDialogBookId = book.id;
  state.deletionDialogChapterIds = [];
  setMessage(elements.deletionDialogMessage, "正在生成删除预检……");
  try {
    const preview = await fetchJSON(
      `/api/manage/books/${encodeURIComponent(book.id)}/delete-preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    showDeletionDialog(preview);
  } catch (error) {
    showToast(`删除预检失败：${error.message}`);
  }
}

function showDeletionDialog(preview) {
  elements.deletionDialogTitle.textContent =
    preview.scope === "full_series"
      ? "确认删除整部连载漫画"
      : preview.scope === "chapters"
      ? "确认删除所选章节"
      : "确认删除漫画";
  elements.deletionDialogSummary.textContent =
    `${preview.book_title} · ${preview.chapter_count} 话 · ` +
    `将生成 ${preview.task_count} 条删除任务。` +
    (preview.leaves_empty_series
      ? " 本次会删除全部章节，但保留空连载漫画主体。"
      : " 资源、元数据和关联记录将被永久删除。");
  elements.deletionVerificationCode.value = "";
  setMessage(elements.deletionDialogMessage);
  if (typeof elements.deletionDialog.showModal === "function") {
    elements.deletionDialog.showModal();
  } else {
    elements.deletionDialog.setAttribute("open", "");
  }
  elements.deletionVerificationCode.focus();
}

function closeDeletionDialog() {
  if (elements.deletionDialog.open) elements.deletionDialog.close();
  else elements.deletionDialog.removeAttribute("open");
}

async function confirmDeletion() {
  const code = elements.deletionVerificationCode.value;
  if (!code) {
    setMessage(elements.deletionDialogMessage, "请输入校验码。");
    return;
  }
  elements.confirmDeletionDialog.disabled = true;
  setMessage(elements.deletionDialogMessage, "正在生成删除任务……");
  const chapters = state.deletionDialogChapterIds;
  const url = chapters.length
    ? `/api/series/${encodeURIComponent(state.deletionDialogBookId)}/delete-tasks`
    : `/api/manage/books/${encodeURIComponent(state.deletionDialogBookId)}/delete-tasks`;
  try {
    const payload = await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chapter_ids: chapters,
        verification_code: code,
      }),
    });
    closeDeletionDialog();
    location.href =
      `/manage/deletions?batch_no=${encodeURIComponent(payload.batch_no)}`;
  } catch (error) {
    setMessage(elements.deletionDialogMessage, `删除失败：${error.message}`);
  } finally {
    elements.confirmDeletionDialog.disabled = false;
  }
}

async function loadDeleteChapters(bookId, page = 1) {
  state.deleteChapterBookId = bookId;
  state.deleteChapterPage = page;
  const params = new URLSearchParams({
    page: String(page),
    page_size: elements.deleteChapterPageSize.value,
    order: elements.deleteChapterOrder.value,
  });
  const search = elements.deleteChapterSearch.value.trim();
  const status = elements.deleteChapterStatusFilter.value;
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  try {
    const payload = await fetchJSON(
      `/api/series/${encodeURIComponent(bookId)}/chapters?${params}`,
    );
    state.deleteChapterPageIds = payload.items.map((item) => item.id);
    elements.deleteChaptersTitle.textContent =
      `删除章节 · ${payload.book_title || bookId}`;
    elements.deleteChaptersSummary.textContent =
      `共 ${payload.total} 话。可跨页选择删除。`;
    elements.deleteChaptersBody.replaceChildren();
    const fragment = document.createDocumentFragment();
    payload.items.forEach((chapter) => {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.className = "checkbox-column";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedDeletionChapterIds.has(chapter.id);
      checkbox.dataset.deleteChapterId = chapter.id;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.selectedDeletionChapterIds.add(chapter.id);
        } else {
          state.selectedDeletionChapterIds.delete(chapter.id);
        }
        updateDeleteChapterSelection();
      });
      cell.append(checkbox);
      row.append(cell);
      appendCell(row, chapter.sort_order, "mono-cell");
      appendCell(row, chapter.chapter_key || chapter.id, "mono-cell");
      appendCell(row, chapter.title);
      appendCell(row, chapter.page_count);
      appendCell(
        row,
        chapter.missing ? "文件缺失" : "可用",
      );
      fragment.append(row);
    });
    elements.deleteChaptersBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.deleteChaptersMessage, "没有找到章节。");
    } else {
      setMessage(elements.deleteChaptersMessage);
    }
    renderPagination(
      elements.deleteChaptersPagination,
      page,
      payload.total_pages || 0,
      (next) => loadDeleteChapters(bookId, next),
    );
    updateDeleteChapterSelection();
  } catch (error) {
    setMessage(elements.deleteChaptersMessage, `加载失败：${error.message}`);
  }
}

function updateDeleteChapterSelection() {
  elements.deleteChapterSelectionSummary.textContent =
    `已选择 ${state.selectedDeletionChapterIds.size} 话`;
  elements.createChapterDeletion.disabled =
    state.selectedDeletionChapterIds.size === 0;
}

async function previewSelectedChapterDeletion() {
  state.deletionDialogBookId = state.deleteChapterBookId;
  state.deletionDialogChapterIds = [
    ...state.selectedDeletionChapterIds,
  ];
  try {
    const preview = await fetchJSON(
      `/api/manage/books/${encodeURIComponent(state.deleteChapterBookId)}/delete-preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapter_ids: state.deletionDialogChapterIds }),
      },
    );
    showDeletionDialog(preview);
  } catch (error) {
    showToast(`删除预检失败：${error.message}`);
  }
}

function createDeletionMessageCell(task) {
  const span = document.createElement("span");
  span.className = "migration-message";
  span.textContent = task.message || task.error_message || "—";
  span.title = span.textContent;
  if (task.status === "completed") {
    span.classList.add("migration-message-success");
  } else if (task.error_message) {
    span.classList.add("migration-message-error");
  }
  return span;
}

function createDeletionActionCell(task) {
  if (
    ["failed", "blocked_external", "interrupted", "completed_with_warning"]
      .includes(task.status)
  ) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "inline-action";
    button.textContent = "重试";
    button.addEventListener("click", () => retryDeletion(task.id));
    return button;
  }
  return "—";
}

async function loadDeletions(page = 1) {
  state.deletionPage = page;
  const params = new URLSearchParams({
    page: String(page),
    page_size: "30",
  });
  const search = elements.deletionSearch.value.trim();
  const status = elements.deletionStatusFilter.value;
  const type = elements.deletionTypeFilter.value;
  const batch = new URLSearchParams(location.search).get("batch_no");
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  if (type) params.set("task_type", type);
  if (batch) params.set("batch_no", batch);
  try {
    const payload = await fetchJSON(`/api/manage/deletions?${params}`);
    elements.deletionSummary.textContent =
      `共 ${payload.total} 条删除任务。`;
    elements.deletionTasksBody.replaceChildren();
    const fragment = document.createDocumentFragment();
    payload.items.forEach((task) => {
      const row = document.createElement("tr");
      appendCell(row, task.id, "mono-cell");
      appendCell(row, task.batch_no, "mono-cell");
      appendCell(row, task.task_type);
      appendCell(row, `${task.book_id} · ${task.book_title}`);
      appendCell(
        row,
        task.chapter_title
          ? `${task.chapter_title} (${task.chapter_key})`
          : "—",
      );
      appendCell(row, task.storage_id);
      appendCell(row, createPathCell(task.target_path_snapshot));
      appendCell(row, createStatusBadge(task.status));
      appendCell(
        row,
        task.total_files
          ? `${task.processed_files} / ${task.total_files}`
          : "—",
      );
      appendCell(row, createDeletionMessageCell(task));
      appendCell(row, formatTime(task.created_at));
      appendCell(row, formatTime(task.finished_at));
      appendCell(row, createDeletionActionCell(task), "action-cell");
      fragment.append(row);
    });
    elements.deletionTasksBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.deletionTasksMessage, "没有找到删除任务。");
    } else {
      setMessage(elements.deletionTasksMessage);
    }
    renderPagination(
      elements.deletionPagination,
      payload.page || 1,
      payload.total_pages || 0,
      loadDeletions,
    );
    const active = payload.items.some((item) =>
      ["pending", "running", "waiting_dependency", "interrupted"]
        .includes(item.status)
    );
    clearTimeout(state.deletionPollTimer);
    if (active && !document.hidden) {
      state.deletionPollTimer = setTimeout(
        () => loadDeletions(page),
        2000,
      );
    }
  } catch (error) {
    setMessage(elements.deletionTasksMessage, `加载失败：${error.message}`);
  }
}

async function retryDeletion(taskId) {
  try {
    await fetchJSON(`/api/manage/deletions/${taskId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    showToast("删除任务已重新排队");
    await loadDeletions(state.deletionPage);
  } catch (error) {
    showToast(`重试失败：${error.message}`);
  }
}

async function loadUsers() {
  try {
    const payload = await fetchJSON("/api/manage/users");
    elements.usersBody.replaceChildren();
    const fragment = document.createDocumentFragment();
    payload.items.forEach((user) => {
      const row = document.createElement("tr");
      appendCell(row, user.id, "mono-cell");
      appendCell(row, user.username);
      appendCell(row, user.role);
      appendCell(row, user.active ? "有效" : "已禁用");
      appendCell(row, formatTime(user.last_login_at));
      appendCell(row, formatTime(user.created_at));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "inline-action";
      button.textContent = user.active ? "禁用" : "恢复";
      button.addEventListener("click", async () => {
        try {
          await fetchJSON(`/api/manage/users/${user.id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active: !Boolean(user.active) }),
          });
          await loadUsers();
        } catch (error) {
          setMessage(elements.usersMessage, error.message);
        }
      });
      appendCell(row, button, "action-cell");
      fragment.append(row);
    });
    elements.usersBody.append(fragment);
  } catch (error) {
    setMessage(elements.usersMessage, `加载失败：${error.message}`);
  }
}

async function createUser() {
  setMessage(elements.createUserMessage, "正在创建……");
  try {
    await fetchJSON("/api/manage/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: elements.newUsername.value.trim(),
        password: elements.newPassword.value,
        role: elements.newUserRole.value,
      }),
    });
    elements.newUsername.value = "";
    elements.newPassword.value = "";
    setMessage(elements.createUserMessage, "用户创建成功。");
    await loadUsers();
  } catch (error) {
    setMessage(elements.createUserMessage, `创建失败：${error.message}`);
  }
}

async function loadSettings() {
  elements.settingsBody.replaceChildren();
  setMessage(elements.settingsMessage, "正在读取系统设置……");
  try {
    const payload = await fetchJSON("/api/manage/settings");
    const fragment = document.createDocumentFragment();
    payload.items.forEach((setting) => {
      const row = document.createElement("tr");
      appendCell(row, setting.id, "mono-cell");
      appendCell(row, setting.setting_key, "mono-cell");

      const valueCell = document.createElement("td");
      const input = document.createElement("input");
      input.type = setting.is_secret ? "password" : "text";
      input.autocomplete = setting.is_secret ? "new-password" : "off";
      input.value = setting.is_secret ? "" : setting.value;
      input.placeholder = setting.is_secret
        ? (setting.configured ? "已配置，输入新值可替换" : "尚未配置")
        : "";
      input.disabled = !setting.editable;
      valueCell.append(input);
      row.append(valueCell);

      appendCell(row, setting.value_type, "mono-cell");
      appendCell(row, setting.description);
      appendCell(
        row,
        setting.is_secret
          ? (setting.configured ? "已配置" : "未配置")
          : "已保存",
      );
      appendCell(row, formatTime(setting.updated_at));

      if (setting.editable) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "inline-action";
        button.textContent = "保存";
        button.addEventListener("click", () =>
          saveSetting(setting, input, button)
        );
        appendCell(row, button, "action-cell");
      } else {
        appendCell(row, "只读", "action-cell");
      }
      fragment.append(row);
    });
    elements.settingsBody.append(fragment);
    setMessage(elements.settingsMessage);
  } catch (error) {
    setMessage(elements.settingsMessage, `加载失败：${error.message}`);
  }
}

async function saveSetting(setting, input, button) {
  const value = input.value;
  if (!value.trim()) {
    setMessage(
      elements.settingsMessage,
      setting.is_secret
        ? "请输入新的校验码，不能保存空值。"
        : "配置值不能为空。",
    );
    input.focus();
    return;
  }
  button.disabled = true;
  setMessage(elements.settingsMessage, `正在保存 ${setting.setting_key}……`);
  try {
    const payload = await fetchJSON(
      `/api/manage/settings/${encodeURIComponent(setting.setting_key)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      },
    );
    setMessage(
      elements.settingsMessage,
      payload.requires_restart
        ? "配置已保存，重启网站后生效。"
        : "配置已保存。",
    );
    await loadSettings();
  } catch (error) {
    setMessage(elements.settingsMessage, `保存失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function loadManagedBooks(page = 1) {
  state.booksPage = page;
  elements.managedBooksBody.replaceChildren();
  setMessage(elements.managedBooksMessage);
  elements.managedBooksSummary.textContent = "正在读取漫画记录……";
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
  });
  const search = elements.managedBookSearch.value.trim();
  const storage = elements.managedStorageFilter.value;
  const status = elements.managedStatusFilter.value;
  if (search) params.set("q", search);
  if (storage) params.set("storage", storage);
  if (status) params.set("status", status);
  updateQuery({ page, q: search, storage, status });
  try {
    const payload = await fetchJSON(`/api/manage/books?${params}`);
    state.booksPage = payload.page || 1;
    elements.managedBooksSummary.textContent =
      `共 ${payload.total} 本漫画，外盘未连接时仍保留元数据。`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((book) => {
      const row = document.createElement("tr");
      appendCell(row, book.id, "mono-cell managed-book-id-cell");
      appendCell(
        row,
        createBookIdentity(book, false),
        "managed-book-title-cell",
      );
      appendCell(row, `${book.page_count} 页`);
      const storageCell = document.createElement("div");
      storageCell.className = "storage-cell";
      storageCell.append(
        Object.assign(document.createElement("strong"), {
          textContent: book.storage_name || (
            book.storage_id === "local" ? "本地目录" : "外盘"
          ),
        }),
        Object.assign(document.createElement("span"), {
          textContent: book.storage_path || "",
        }),
      );
      appendCell(row, storageCell);
      const access = document.createElement("select");
      access.className = "inline-select";
      [
        ["0", "所有用户"],
        ["1", "登录用户"],
        ["2", "管理员"],
      ].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        option.selected = Number(book.access_level || 0) === Number(value);
        access.append(option);
      });
      access.addEventListener("change", async () => {
        try {
          await fetchJSON(
            `/api/manage/books/${encodeURIComponent(book.id)}/access-level`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ access_level: Number(access.value) }),
            },
          );
          showToast("访问权限已更新");
        } catch (error) {
          showToast(`权限修改失败：${error.message}`);
          loadManagedBooks(state.booksPage);
        }
      });
      appendCell(row, access);
      appendCell(row, createStatusBadge(book.availability));
      appendCell(row, formatTime(book.updated_at || ""));
      appendCell(row, createManagedBookActionCell(book), "action-cell");
      fragment.append(row);
    });
    elements.managedBooksBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.managedBooksMessage, "没有找到漫画。");
    }
    renderPagination(
      elements.managedBooksPagination,
      state.booksPage,
      payload.total_pages || 0,
      loadManagedBooks,
    );
  } catch (error) {
    setMessage(elements.managedBooksMessage, `加载失败：${error.message}`);
  }
}

function createPathCell(value) {
  const span = document.createElement("span");
  span.className = "path-cell";
  span.textContent = value || "—";
  span.title = value || "";
  return span;
}

function createMigrationStatusCell(record) {
  const wrapper = document.createElement("div");
  wrapper.className = "migration-state-cell";
  wrapper.append(createStatusBadge(record.status));
  const detail = document.createElement("span");
  if (record.status === "migrating" && record.stage) {
    const progress = record.progress || {};
    const files = progress.total_files
      ? ` · ${progress.copied_files || 0}/${progress.total_files}`
      : "";
    detail.textContent = `${stageLabel(record.stage)}${files}`;
  }
  if (detail.textContent) wrapper.append(detail);
  return wrapper;
}

function createMigrationMessageCell(record) {
  const message = document.createElement("span");
  message.className = "migration-message";
  message.textContent = record.message || "—";
  message.title = record.message || "";
  if (record.message === "success") {
    message.classList.add("migration-message-success");
  } else if (record.message) {
    message.classList.add("migration-message-error");
  }
  return message;
}

function createMigrationActionCell(record) {
  if (["failed", "blocked_external", "interrupted"].includes(record.status)) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "inline-action";
    retry.textContent = "重试";
    retry.addEventListener("click", () => retryRecord(record.id));
    return retry;
  }
  return "—";
}

async function loadMigrations(page = 1) {
  state.migrationPage = page;
  elements.migrationRecordsBody.replaceChildren();
  setMessage(elements.migrationRecordsMessage);
  elements.migrationSummary.textContent = "正在读取迁移记录……";
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
  });
  const search = elements.migrationSearch.value.trim();
  const status = elements.migrationStatusFilter.value;
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  updateQuery({ page, q: search, status });
  try {
    const payload = await fetchJSON(`/api/manage/migrations?${params}`);
    state.migrationPage = payload.page || 1;
    elements.migrationSummary.textContent = `共 ${payload.total} 条迁移记录。`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((record) => {
      const row = document.createElement("tr");
      appendCell(row, record.id, "mono-cell migration-id-cell");
      appendCell(row, record.book_id, "mono-cell");
      const title = document.createElement("a");
      title.href = `/reader/${encodeURIComponent(record.book_id)}`;
      title.textContent = record.book_title;
      title.title = record.book_title;
      appendCell(row, title, "record-title-cell");
      const batch = document.createElement("button");
      batch.type = "button";
      batch.className = "link-button mono-cell";
      batch.textContent = record.batch_no;
      batch.title = "按该批次号筛选";
      batch.addEventListener("click", () => {
        elements.migrationSearch.value = record.batch_no;
        loadMigrations(1);
      });
      appendCell(row, batch);
      appendCell(row, createPathCell(record.source_path));
      appendCell(row, createPathCell(record.destination_path));
      appendCell(row, createMigrationStatusCell(record));
      appendCell(row, createMigrationMessageCell(record));
      appendCell(row, formatTime(record.created_at));
      appendCell(row, formatTime(record.finished_at));
      appendCell(row, createMigrationActionCell(record), "action-cell");
      fragment.append(row);
    });
    elements.migrationRecordsBody.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.migrationRecordsMessage, "没有找到迁移记录。");
    }
    renderPagination(
      elements.migrationPagination,
      state.migrationPage,
      payload.total_pages || 0,
      loadMigrations,
    );
    scheduleMigrationPoll(payload.items);
  } catch (error) {
    setMessage(
      elements.migrationRecordsMessage,
      `加载失败：${error.message}`,
    );
  }
}

function scheduleMigrationPoll(items) {
  clearTimeout(state.pollTimer);
  const active = items.some((item) =>
    ["queued", "migrating", "interrupted"].includes(item.status)
  );
  if (!active || document.hidden) return;
  state.pollTimer = setTimeout(() => loadMigrations(state.migrationPage), 2000);
}

async function retryRecord(recordId) {
  try {
    await fetchJSON(
      `/api/manage/migrations/${encodeURIComponent(recordId)}/retry`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    );
    showToast("记录已重新排队");
    await loadMigrations(state.migrationPage);
  } catch (error) {
    showToast(`重试失败：${error.message}`);
  }
}

async function validateDestination(options = {}) {
  const path = elements.migrationDestination.value.trim();
  if (!path) {
    setMessage(elements.destinationMessage, "请输入或选择外盘目录。");
    state.validatedDestination = "";
    return null;
  }
  elements.validateDirectoryButton.disabled = true;
  setMessage(elements.destinationMessage, "正在检测外盘……");
  try {
    const payload = await fetchJSON("/api/manage/storages/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    state.validatedDestination = payload.path;
    if (options.remember !== false) {
      localStorage.setItem(DESTINATION_STORAGE_KEY, payload.path);
    }
    const freeGB = (payload.free_bytes / 1024 ** 3).toFixed(1);
    setMessage(
      elements.destinationMessage,
      `外盘可用，剩余空间 ${freeGB} GB。`,
    );
    return payload;
  } catch (error) {
    state.validatedDestination = "";
    setMessage(elements.destinationMessage, `外盘检测失败：${error.message}`);
    return null;
  } finally {
    elements.validateDirectoryButton.disabled = false;
  }
}

async function pickDirectory() {
  elements.pickDirectoryButton.disabled = true;
  setMessage(elements.destinationMessage, "请在弹出的窗口中选择目录……");
  try {
    const payload = await fetchJSON("/api/manage/storages/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    elements.migrationDestination.value = payload.path;
    state.validatedDestination = payload.path;
    localStorage.setItem(DESTINATION_STORAGE_KEY, payload.path);
    setMessage(elements.destinationMessage, "外盘目录已验证。");
  } catch (error) {
    setMessage(elements.destinationMessage, `选择失败：${error.message}`);
  } finally {
    elements.pickDirectoryButton.disabled = false;
  }
}

async function loadSelectionBooks(page = 1) {
  state.selectionPage = page;
  elements.migrationBooksBody.replaceChildren();
  setMessage(elements.migrationBooksMessage);
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
    storage: "local",
    status: "available",
  });
  const search = elements.migrationBookSearch.value.trim();
  if (search) params.set("q", search);
  try {
    const payload = await fetchJSON(`/api/manage/books?${params}`);
    state.selectionPage = payload.page || 1;
    state.selectionPageIds = payload.items.map((book) => book.id);
    const fragment = document.createDocumentFragment();
    payload.items.forEach((book) => {
      fragment.append(createSelectableBook(book));
    });
    elements.migrationBooksBody.append(fragment);
    syncPageSelectionControl();
    if (!payload.items.length) {
      setMessage(
        elements.migrationBooksMessage,
        "没有可迁移的本地漫画。",
      );
    }
    renderPagination(
      elements.migrationBooksPagination,
      state.selectionPage,
      payload.total_pages || 0,
      loadSelectionBooks,
    );
  } catch (error) {
    setMessage(
      elements.migrationBooksMessage,
      `加载失败：${error.message}`,
    );
  }
}

function updateSelectionSummary() {
  elements.selectionSummary.textContent =
    `已选择 ${state.selectedBookIds.size} 本。选择可跨分页保留。`;
  elements.createMigrationButton.disabled =
    state.selectedBookIds.size === 0;
}

function syncPageSelectionControl() {
  const pageIds = state.selectionPageIds;
  const selected = pageIds.filter((bookId) =>
    state.selectedBookIds.has(bookId)
  ).length;
  elements.selectPageCheckbox.disabled = pageIds.length === 0;
  elements.selectPageCheckbox.checked =
    pageIds.length > 0 && selected === pageIds.length;
  elements.selectPageCheckbox.indeterminate =
    selected > 0 && selected < pageIds.length;
}

async function createMigration() {
  if (!state.selectedBookIds.size) {
    setMessage(elements.createMigrationMessage, "请至少选择一本漫画。");
    return;
  }
  if (!elements.migrationCode.value) {
    setMessage(elements.createMigrationMessage, "请输入验证码。");
    return;
  }
  elements.createMigrationButton.disabled = true;
  setMessage(elements.createMigrationMessage, "正在校验并生成迁移记录……");
  try {
    const destination = await validateDestination();
    if (!destination) {
      setMessage(
        elements.createMigrationMessage,
        "请先修正外盘路径。",
      );
      return;
    }
    const payload = await fetchJSON("/api/manage/migrations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        destination_path: destination.path,
        book_ids: [...state.selectedBookIds],
        verification_code: elements.migrationCode.value,
      }),
    });
    elements.migrationCode.value = "";
    showToast(`已生成 ${payload.count} 条迁移记录`);
    location.href =
      `/manage/migrations?q=${encodeURIComponent(payload.batch_no)}`;
  } catch (error) {
    setMessage(
      elements.createMigrationMessage,
      `创建失败：${error.message}`,
    );
  } finally {
    updateSelectionSummary();
  }
}

let searchTimer;
function debounce(callback) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(callback, 300);
}

elements.downloadSearch.addEventListener("input", () => {
  debounce(() => loadDownloads(1));
});
elements.downloadStatusFilter.addEventListener("change", () => loadDownloads(1));
elements.downloadRefreshButton.addEventListener(
  "click",
  () => loadDownloads(state.downloadPage),
);
elements.openDownloadDialog.addEventListener("click", openDownloadDialog);
elements.downloadTabs.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  const tabs = [
    elements.singleDownloadTab,
    elements.batchDownloadTab,
    elements.incrementalPullTab,
  ];
  const currentIndex = Math.max(0, tabs.indexOf(document.activeElement));
  const direction = event.key === "ArrowRight" ? 1 : -1;
  const nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
  const target = tabs[nextIndex];
  target.focus();
  target.click();
});
elements.closeDownloadDialog.addEventListener("click", closeDownloadDialog);
elements.cancelDownloadDialog.addEventListener("click", closeDownloadDialog);
elements.closeDownloadRootError.addEventListener(
  "click",
  closeDownloadRootError,
);
elements.cancelDownloadRootError.addEventListener(
  "click",
  closeDownloadRootError,
);
elements.confirmDownloadTask.addEventListener("click", createDownloadTask);
elements.downloadUrl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") createDownloadTask();
});
elements.analyzeBatchButton.addEventListener("click", analyzeBatch);
elements.batchSeriesUrl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") analyzeBatch();
});
elements.batchPreviewSearch.addEventListener("input", () => {
  debounce(() => loadBatchPreview(1));
});
elements.batchPreviewPageSize.addEventListener("change", () => {
  loadBatchPreview(1);
});
elements.clearBatchSelectionButton.addEventListener("click", () => {
  state.batchSelectedKeys.clear();
  elements.batchPreviewBody
    .querySelectorAll("[data-batch-chapter-key]")
    .forEach((checkbox) => {
      checkbox.checked = false;
    });
  updateBatchSelectionSummary();
  syncBatchPageSelectionControl();
});
elements.batchSelectPageCheckbox.addEventListener("change", () => {
  const selected = elements.batchSelectPageCheckbox.checked;
  elements.batchPreviewBody
    .querySelectorAll("[data-batch-chapter-key]:not(:disabled)")
    .forEach((checkbox) => {
      checkbox.checked = selected;
      if (selected) {
        state.batchSelectedKeys.add(checkbox.dataset.batchChapterKey);
      } else {
        state.batchSelectedKeys.delete(checkbox.dataset.batchChapterKey);
      }
    });
  updateBatchSelectionSummary();
  syncBatchPageSelectionControl();
});
elements.createBatchTasksButton.addEventListener("click", createBatchTasks);
elements.batchTaskSearch.addEventListener("input", () => {
  debounce(() => loadBatchTasks(1));
});
elements.batchTaskStatusFilter.addEventListener(
  "change",
  () => loadBatchTasks(1),
);
elements.batchTaskPageSize.addEventListener(
  "change",
  () => loadBatchTasks(1),
);
elements.batchTaskRefreshButton.addEventListener(
  "click",
  () => loadBatchTasks(state.batchTaskPage),
);
elements.incrementalSearch.addEventListener("input", () => {
  debounce(() => loadIncrementalTasks(1));
});
elements.incrementalComicStatusFilter.addEventListener(
  "change",
  () => loadIncrementalTasks(1),
);
elements.incrementalStateFilter.addEventListener(
  "change",
  () => loadIncrementalTasks(1),
);
elements.incrementalPageSize.addEventListener(
  "change",
  () => loadIncrementalTasks(1),
);
elements.incrementalRefreshButton.addEventListener(
  "click",
  () => loadIncrementalTasks(state.incrementalPage),
);
elements.closeIncrementalDialog.addEventListener(
  "click",
  closeIncrementalDialog,
);
elements.cancelIncrementalDialog.addEventListener(
  "click",
  closeIncrementalDialog,
);
elements.confirmIncrementalPull.addEventListener(
  "click",
  confirmIncrementalPull,
);
elements.closeRecrawlDialog.addEventListener("click", closeRecrawlDialog);
elements.cancelRecrawlDialog.addEventListener("click", closeRecrawlDialog);
elements.confirmRecrawlDialog.addEventListener("click", confirmRecrawl);
elements.incrementalSelectPageCheckbox.addEventListener("change", () => {
  const selected = elements.incrementalSelectPageCheckbox.checked;
  elements.incrementalPreviewBody
    .querySelectorAll("[data-incremental-chapter-key]")
    .forEach((checkbox) => {
      checkbox.checked = selected;
      if (selected) {
        state.incrementalSelectedKeys.add(
          checkbox.dataset.incrementalChapterKey,
        );
      } else {
        state.incrementalSelectedKeys.delete(
          checkbox.dataset.incrementalChapterKey,
        );
      }
    });
  syncIncrementalDialogSelection();
});
elements.closeDeletionDialog.addEventListener("click", closeDeletionDialog);
elements.cancelDeletionDialog.addEventListener("click", closeDeletionDialog);
elements.confirmDeletionDialog.addEventListener("click", confirmDeletion);
elements.deleteChapterSearch.addEventListener("input", () => {
  debounce(() => loadDeleteChapters(state.deleteChapterBookId, 1));
});
elements.deleteChapterStatusFilter.addEventListener(
  "change",
  () => loadDeleteChapters(state.deleteChapterBookId, 1),
);
elements.deleteChapterOrder.addEventListener(
  "change",
  () => loadDeleteChapters(state.deleteChapterBookId, 1),
);
elements.deleteChapterPageSize.addEventListener(
  "change",
  () => loadDeleteChapters(state.deleteChapterBookId, 1),
);
elements.deleteChapterSelectPage.addEventListener("click", () => {
  state.deleteChapterPageIds.forEach((id) =>
    state.selectedDeletionChapterIds.add(id)
  );
  elements.deleteChaptersBody
    .querySelectorAll("[data-delete-chapter-id]")
    .forEach((checkbox) => {
      checkbox.checked = true;
    });
  updateDeleteChapterSelection();
});
elements.deleteChapterClearSelection.addEventListener("click", () => {
  state.selectedDeletionChapterIds.clear();
  elements.deleteChaptersBody
    .querySelectorAll("[data-delete-chapter-id]")
    .forEach((checkbox) => {
      checkbox.checked = false;
    });
  updateDeleteChapterSelection();
});
elements.createChapterDeletion.addEventListener(
  "click",
  previewSelectedChapterDeletion,
);
elements.deletionSearch.addEventListener("input", () => {
  debounce(() => loadDeletions(1));
});
elements.deletionStatusFilter.addEventListener(
  "change",
  () => loadDeletions(1),
);
elements.deletionTypeFilter.addEventListener(
  "change",
  () => loadDeletions(1),
);
elements.deletionRefreshButton.addEventListener(
  "click",
  () => loadDeletions(state.deletionPage),
);

elements.managedBookSearch.addEventListener("input", () => {
  debounce(() => loadManagedBooks(1));
});
elements.createUserButton.addEventListener("click", createUser);
elements.settingsRefreshButton.addEventListener("click", loadSettings);
elements.managedStorageFilter.addEventListener("change", () => loadManagedBooks(1));
elements.managedStatusFilter.addEventListener("change", () => loadManagedBooks(1));
elements.migrationSearch.addEventListener("input", () => {
  debounce(() => loadMigrations(1));
});
elements.migrationStatusFilter.addEventListener("change", () => loadMigrations(1));
elements.migrationRefreshButton.addEventListener(
  "click",
  () => loadMigrations(state.migrationPage),
);
elements.validateDirectoryButton.addEventListener("click", () => validateDestination());
elements.pickDirectoryButton.addEventListener("click", pickDirectory);
elements.migrationDestination.addEventListener("change", () => {
  state.validatedDestination = "";
  setMessage(elements.destinationMessage, "路径已改变，请重新检测。");
});
elements.migrationBookSearch.addEventListener("input", () => {
  debounce(() => loadSelectionBooks(1));
});
elements.selectPageCheckbox.addEventListener("change", () => {
  const selected = elements.selectPageCheckbox.checked;
  state.selectionPageIds.forEach((bookId) => {
    if (selected) state.selectedBookIds.add(bookId);
    else state.selectedBookIds.delete(bookId);
  });
  elements.migrationBooksBody
    .querySelectorAll("[data-select-book-id]")
    .forEach((checkbox) => {
      checkbox.checked = selected;
    });
  updateSelectionSummary();
  syncPageSelectionControl();
});
elements.clearSelectionButton.addEventListener("click", () => {
  state.selectedBookIds.clear();
  loadSelectionBooks(state.selectionPage);
  updateSelectionSummary();
});
elements.createMigrationButton.addEventListener("click", createMigration);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !elements.downloadsView.classList.contains("hidden")) {
    if (state.downloadTab === "single") {
      loadDownloads(state.downloadPage);
    } else if (state.downloadTab === "batch") {
      loadBatchTasks(state.batchTaskPage);
    } else {
      loadIncrementalTasks(state.incrementalPage);
    }
  }
  if (!document.hidden && !elements.migrationsView.classList.contains("hidden")) {
    loadMigrations(state.migrationPage);
  }
});

function initialize() {
  const path = location.pathname.replace(/\/+$/, "");
  const params = new URLSearchParams(location.search);
  if (path === "/manage/downloads") {
    setView(elements.downloadsView);
    setDownloadTab("single");
    elements.downloadSearch.value = params.get("q") || "";
    elements.downloadStatusFilter.value = params.get("status") || "";
    loadDownloads(Number(params.get("page") || 1));
    return;
  }
  if (path === "/manage/downloads/batch") {
    setView(elements.downloadsView);
    setDownloadTab("batch");
    elements.batchTaskSearch.value = params.get("q") || "";
    elements.batchTaskStatusFilter.value = params.get("status") || "";
    const pageSize = params.get("page_size") || "30";
    if (["30", "50", "100"].includes(pageSize)) {
      elements.batchTaskPageSize.value = pageSize;
    }
    loadBatchTasks(Number(params.get("page") || 1));
    const warning = params.get("warning");
    if (warning) showToast(warning);
    return;
  }
  if (path === "/manage/downloads/batch/new") {
    setView(elements.batchCreateView);
    updateBatchSelectionSummary();
    return;
  }
  if (path === "/manage/downloads/incremental") {
    setView(elements.downloadsView);
    setDownloadTab("incremental");
    elements.incrementalSearch.value = params.get("q") || "";
    elements.incrementalComicStatusFilter.value =
      params.get("comic_status") || "";
    elements.incrementalStateFilter.value =
      params.get("task_state") || "";
    const pageSize = params.get("page_size") || "30";
    if (["30", "50", "100"].includes(pageSize)) {
      elements.incrementalPageSize.value = pageSize;
    }
    loadIncrementalTasks(Number(params.get("page") || 1));
    return;
  }
  if (path === "/manage/books") {
    setView(elements.booksView);
    elements.managedBookSearch.value = params.get("q") || "";
    elements.managedStorageFilter.value = params.get("storage") || "";
    elements.managedStatusFilter.value = params.get("status") || "";
    loadManagedBooks(Number(params.get("page") || 1));
    return;
  }
  const deleteChaptersMatch = path.match(
    /^\/manage\/books\/([^/]+)\/delete-chapters$/,
  );
  if (deleteChaptersMatch) {
    const bookId = decodeURIComponent(deleteChaptersMatch[1]);
    state.selectedDeletionChapterIds.clear();
    setView(elements.deleteChaptersView);
    elements.deleteChaptersBack.href = "/manage/books";
    loadDeleteChapters(bookId, 1);
    return;
  }
  if (path === "/manage/deletions") {
    setView(elements.deletionsView);
    elements.deletionSearch.value = params.get("q") || "";
    elements.deletionStatusFilter.value = params.get("status") || "";
    elements.deletionTypeFilter.value = params.get("task_type") || "";
    loadDeletions(Number(params.get("page") || 1));
    return;
  }
  if (path === "/manage/users") {
    setView(elements.usersView);
    loadUsers();
    return;
  }
  if (path === "/manage/settings") {
    setView(elements.settingsView);
    loadSettings();
    return;
  }
  if (path === "/manage/migrations") {
    setView(elements.migrationsView);
    elements.migrationSearch.value = params.get("q") || "";
    elements.migrationStatusFilter.value = params.get("status") || "";
    loadMigrations(Number(params.get("page") || 1));
    return;
  }
  if (path === "/manage/migrations/new") {
    setView(elements.newMigrationView);
    const remembered = localStorage.getItem(DESTINATION_STORAGE_KEY) || "";
    if (remembered) {
      elements.migrationDestination.value = remembered;
      validateDestination({ remember: false });
    }
    updateSelectionSummary();
    loadSelectionBooks(1);
    return;
  }
  location.replace("/manage/migrations");
}

initializeAuth().then(initialize).catch((error) => {
  if (location.pathname !== "/login") location.href = "/login";
});
