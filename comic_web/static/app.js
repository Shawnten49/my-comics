const state = {
  page: 1,
  pageSize: 30,
  total: 0,
  totalPages: 0,
  homeLoading: false,
  search: "",
  category: "",
  favoritesView: false,
  favoritesCount: 0,
  series: null,
  chapterPage: 1,
  chapterPageSize: 50,
  chapterTotalPages: 0,
  searchTimer: null,
  reader: null,
  readerLoading: false,
  coverPicker: null,
  blockMode: false,
  restoreMode: false,
  auth: {role: "guest", csrf_token: ""},
};

const elements = {
  homeView: document.querySelector("#homeView"),
  seriesView: document.querySelector("#seriesView"),
  readerView: document.querySelector("#readerView"),
  bookGrid: document.querySelector("#bookGrid"),
  librarySummary: document.querySelector("#librarySummary"),
  homeMessage: document.querySelector("#homeMessage"),
  pagination: document.querySelector("#pagination"),
  paginationButtons: document.querySelector("#paginationButtons"),
  paginationInfo: document.querySelector("#paginationInfo"),
  pageSizeSelect: document.querySelector("#pageSizeSelect"),
  searchBox: document.querySelector("#searchBox"),
  searchInput: document.querySelector("#searchInput"),
  backButton: document.querySelector("#backButton"),
  categoryButton: document.querySelector("#categoryButton"),
  favoritesButton: document.querySelector("#favoritesButton"),
  favoriteCount: document.querySelector("#favoriteCount"),
  favoriteToggleButton: document.querySelector("#favoriteToggleButton"),
  readerProgress: document.querySelector("#readerProgress"),
  manageButton: document.querySelector("#manageButton"),
  authButton: document.querySelector("#authButton"),
  readerTitle: document.querySelector("#readerTitle"),
  readerMeta: document.querySelector("#readerMeta"),
  readerChapterNav: document.querySelector("#readerChapterNav"),
  previousChapterButton: document.querySelector("#previousChapterButton"),
  nextChapterButton: document.querySelector("#nextChapterButton"),
  pageStream: document.querySelector("#pageStream"),
  readerSentinel: document.querySelector("#readerSentinel"),
  readerMessage: document.querySelector("#readerMessage"),
  readerRetryButton: document.querySelector("#readerRetryButton"),
  coverDialog: document.querySelector("#coverDialog"),
  coverDialogTitle: document.querySelector("#coverDialogTitle"),
  closeCoverDialog: document.querySelector("#closeCoverDialog"),
  coverOptionGrid: document.querySelector("#coverOptionGrid"),
  coverDialogMessage: document.querySelector("#coverDialogMessage"),
  coverPrev: document.querySelector("#coverPrev"),
  coverNext: document.querySelector("#coverNext"),
  coverReset: document.querySelector("#coverReset"),
  categoryDialog: document.querySelector("#categoryDialog"),
  closeCategoryDialog: document.querySelector("#closeCategoryDialog"),
  validCategoryList: document.querySelector("#validCategoryList"),
  blockedCategoryList: document.querySelector("#blockedCategoryList"),
  validCategoryCount: document.querySelector("#validCategoryCount"),
  blockedCategoryCount: document.querySelector("#blockedCategoryCount"),
  blockModeButton: document.querySelector("#blockModeButton"),
  restoreModeButton: document.querySelector("#restoreModeButton"),
  categoryDialogMessage: document.querySelector("#categoryDialogMessage"),
  seriesCover: document.querySelector("#seriesCover"),
  seriesTitle: document.querySelector("#seriesTitle"),
  seriesMeta: document.querySelector("#seriesMeta"),
  seriesCategories: document.querySelector("#seriesCategories"),
  chapterSearch: document.querySelector("#chapterSearch"),
  chapterOrder: document.querySelector("#chapterOrder"),
  chapterStatus: document.querySelector("#chapterStatus"),
  chapterPageSize: document.querySelector("#chapterPageSize"),
  chapterList: document.querySelector("#chapterList"),
  seriesMessage: document.querySelector("#seriesMessage"),
  chapterPagination: document.querySelector("#chapterPagination"),
  toast: document.querySelector("#toast"),
};

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
  try {
    const payload = await fetchJSON("/api/auth/session");
    state.auth = payload;
    const admin = payload.role === "admin";
    const loggedIn = ["user", "admin"].includes(payload.role);
    elements.categoryButton.classList.toggle("hidden", !admin);
    elements.manageButton.classList.toggle("hidden", !admin);
    elements.favoritesButton.classList.toggle("hidden", !loggedIn);
    elements.favoriteToggleButton.classList.add("hidden");
    elements.authButton.textContent = loggedIn
      ? `${payload.username} · 退出`
      : "登录";
    elements.authButton.href = loggedIn ? "#" : "/login";
    elements.authButton.onclick = loggedIn
      ? async (event) => {
          event.preventDefault();
          await fetchJSON("/api/auth/logout", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: "{}",
          });
          location.href = "/";
        }
      : null;
  } catch (_) {
    elements.manageButton.classList.add("hidden");
  }
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    elements.toast.classList.add("hidden");
  }, 3000);
}

function setMessage(element, message = "") {
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

function createBookCard(book) {
  const card = document.createElement("article");
  card.className = "book-card";
  card.dataset.bookId = book.id;

  const link = document.createElement("a");
  link.className = "book-link";
  link.href = book.content_type === "chaptered"
    ? `/series/${encodeURIComponent(book.id)}`
    : `/reader/${encodeURIComponent(book.id)}`;

  const frame = document.createElement("div");
  frame.className = "cover-frame";

  const image = document.createElement("img");
  image.loading = "lazy";
  image.decoding = "async";
  image.alt = book.title;
  image.src = book.thumbnail_url || book.cover_url;
  image.addEventListener("error", () => {
    if (image.src && book.cover_url && !image.dataset.fallback) {
      image.dataset.fallback = "1";
      image.src = book.cover_url;
    }
  });

  const count = document.createElement("span");
  count.className = "page-count";
  count.textContent = book.content_type === "chaptered"
    ? `${book.chapter_count || 0} 话`
    : `${book.page_count} 页`;
  frame.append(image, count);
  if (book.favorite) {
    const favoriteBadge = document.createElement("span");
    favoriteBadge.className = "page-count favorite-card-badge";
    favoriteBadge.textContent = "♥";
    frame.append(favoriteBadge);
  }
  if (book.storage_id && book.storage_id !== "local") {
    const storageBadge = document.createElement("span");
    storageBadge.className = "storage-badge";
    storageBadge.textContent =
      book.availability === "external_offline"
        ? "外盘未连接"
        : book.availability === "external_mismatch"
          ? "外盘不匹配"
          : book.availability === "content_missing"
            ? "文件缺失"
            : "外盘";
    if (book.availability !== "available") {
      storageBadge.classList.add("storage-badge-warning");
    }
    frame.append(storageBadge);
  }

  const title = document.createElement("div");
  title.className = "book-title";
  title.textContent = book.title;
  title.title = book.title;

  const subtitle = document.createElement("div");
  subtitle.className = "book-subtitle";
  const storageLabel =
    book.storage_id && book.storage_id !== "local"
      ? ` · ${book.storage_name || "外盘"}`
      : "";
  subtitle.textContent =
    `${book.complete ? "已完成" : "未完成"}${storageLabel}`;

  link.append(frame, title, subtitle);

  card.append(link);
  if (book.content_type !== "chaptered" && state.auth.role === "admin") {
    const editButton = document.createElement("button");
    editButton.className = "cover-edit-button";
    editButton.type = "button";
    editButton.textContent = "更换封面";
    editButton.dataset.coverBookId = book.id;
    card.append(editButton);
  }
  return card;
}

function syncListUrl(page, replace = false) {
  const url = new URL(location.href);
  if (page > 1) url.searchParams.set("page", String(page));
  else url.searchParams.delete("page");
  if (state.pageSize !== 30) {
    url.searchParams.set("page_size", String(state.pageSize));
  } else {
    url.searchParams.delete("page_size");
  }
  if (state.search) url.searchParams.set("q", state.search);
  else url.searchParams.delete("q");
  history[replace ? "replaceState" : "pushState"]({}, "", url);
}

function pageButton(label, page, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `page-button${options.current ? " current" : ""}`;
  button.textContent = label;
  button.disabled = Boolean(options.disabled);
  if (options.ariaLabel) button.setAttribute("aria-label", options.ariaLabel);
  if (options.current) button.setAttribute("aria-current", "page");
  if (!button.disabled && !options.current) {
    button.addEventListener("click", () => goToPage(page));
  }
  return button;
}

function pageNumbers(current, total) {
  const values = new Set([1, total, current - 1, current, current + 1]);
  return [...values]
    .filter((page) => page >= 1 && page <= total)
    .sort((a, b) => a - b);
}

function renderPagination() {
  elements.paginationButtons.replaceChildren();
  const totalPages = state.totalPages;
  if (totalPages <= 0) {
    elements.pagination.classList.add("hidden");
    return;
  }
  elements.pagination.classList.remove("hidden");
  elements.paginationInfo.textContent =
    `第 ${state.page} / ${totalPages} 页 · 共 ${state.total} 本`;
  elements.pageSizeSelect.value = String(state.pageSize);

  const append = (button) => elements.paginationButtons.append(button);
  append(pageButton("首页", 1, {
    disabled: state.page <= 1,
    ariaLabel: "首页",
  }));
  append(pageButton("上一页", state.page - 1, {
    disabled: state.page <= 1,
    ariaLabel: "上一页",
  }));

  let previous = 0;
  pageNumbers(state.page, totalPages).forEach((page) => {
    if (previous && page > previous + 1) {
      const ellipsis = document.createElement("span");
      ellipsis.className = "page-ellipsis";
      ellipsis.textContent = "…";
      append(ellipsis);
    }
    append(pageButton(String(page), page, {
      current: page === state.page,
      ariaLabel: `第 ${page} 页`,
    }));
    previous = page;
  });

  append(pageButton("下一页", state.page + 1, {
    disabled: state.page >= totalPages,
    ariaLabel: "下一页",
  }));
  append(pageButton("尾页", totalPages, {
    disabled: state.page >= totalPages,
    ariaLabel: "尾页",
  }));
}

async function loadBooks(options = {}) {
  if (state.homeLoading) return;
  const page = Math.max(1, Number(options.page || state.page) || 1);
  state.homeLoading = true;
  state.page = page;
  elements.bookGrid.replaceChildren();
  setMessage(elements.homeMessage);
  elements.librarySummary.textContent = "正在读取本地漫画目录……";
  elements.pagination.classList.add("hidden");

  try {
    const params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize),
    });
    if (state.search) params.set("q", state.search);
    if (state.category && !state.favoritesView) {
      params.set("category", state.category);
    }
    const endpoint = state.favoritesView ? "/api/favorites" : "/api/books";
    const payload = await fetchJSON(`${endpoint}?${params}`);
    if (typeof payload.favorite_count === "number") {
      updateFavoriteCount(payload.favorite_count);
    }
    state.page = payload.page || page;
    state.total = payload.total || 0;
    state.totalPages = payload.total_pages || 0;

    const fragment = document.createDocumentFragment();
    payload.items.forEach((book) => fragment.append(createBookCard(book)));
    elements.bookGrid.append(fragment);

    if (state.favoritesView) {
      elements.librarySummary.textContent = state.search
        ? `收藏中找到 ${payload.total} 本与“${state.search}”相关的漫画`
        : `我的收藏 · 共 ${payload.total} 本漫画`;
    } else if (state.category) {
      elements.librarySummary.textContent = state.search
        ? `分类“${state.category}”中找到 ${payload.total} 本与“${state.search}”相关的漫画`
        : `分类：${state.category} · 共 ${payload.total} 本漫画`;
    } else {
      elements.librarySummary.textContent = state.search
        ? `找到 ${payload.total} 本与“${state.search}”相关的漫画`
        : `本地共有 ${payload.total} 本漫画`;
    }

    if (payload.total === 0) {
      setMessage(
        elements.homeMessage,
        state.favoritesView ? "还没有收藏任何漫画。" : "没有找到漫画。",
      );
    }
    renderPagination();
    if (options.updateUrl) {
      syncListUrl(state.page, Boolean(options.replaceUrl));
    }
    if (options.scroll) {
      elements.bookGrid.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    setMessage(elements.homeMessage, `加载失败：${error.message}`);
    elements.pagination.classList.add("hidden");
  } finally {
    state.homeLoading = false;
  }
}

function goToPage(page) {
  const target = Math.max(
    1,
    Math.min(state.totalPages || page, Number(page) || 1),
  );
  loadBooks({
    page: target,
    updateUrl: true,
    scroll: true,
  });
}

function resetHomeAndLoad(updateUrl = true) {
  loadBooks({
    page: 1,
    updateUrl,
    replaceUrl: true,
  });
}

function readerIdFromPath() {
  const match = location.pathname.match(
    /^\/reader\/([^/]+)(?:\/chapter\/\d+)?\/?$/,
  );
  return match ? decodeURIComponent(match[1]) : "";
}

function readerChapterIdFromPath() {
  const match = location.pathname.match(
    /^\/reader\/[^/]+\/chapter\/(\d+)\/?$/,
  );
  return match ? Number(match[1]) : 0;
}

function seriesIdFromPath() {
  const match = location.pathname.match(/^\/series\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : "";
}

function categoryFromPath() {
  const match = location.pathname.match(/^\/category\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : "";
}

function favoritesFromPath() {
  return location.pathname.replace(/\/+$/, "") === "/favorites";
}

function initializeListStateFromUrl() {
  const params = new URLSearchParams(location.search);
  const page = Number(params.get("page") || "1");
  const pageSize = Number(params.get("page_size") || "30");
  state.page = Number.isInteger(page) && page > 0 ? page : 1;
  state.pageSize = [24, 30, 48, 60].includes(pageSize) ? pageSize : 30;
  state.search = (params.get("q") || "").trim();
  elements.searchInput.value = state.search;
}

function updateFavoriteCount(count) {
  state.favoritesCount = Number(count) || 0;
  elements.favoriteCount.textContent = String(state.favoritesCount);
  elements.favoriteCount.classList.toggle(
    "hidden",
    state.favoritesCount <= 0,
  );
}

async function loadFavoriteCount() {
  try {
    const payload = await fetchJSON("/api/favorites/count");
    updateFavoriteCount(payload.count);
  } catch (error) {
    console.warn("收藏数量加载失败", error);
  }
}

function initCategoryView(category) {
  state.category = category;
  state.favoritesView = false;
  elements.favoritesButton.classList.remove("active");
  elements.backButton.classList.remove("hidden");
  elements.backButton.href = "/";
  elements.readerView.classList.add("hidden");
  elements.seriesView.classList.add("hidden");
  elements.homeView.classList.remove("hidden");
  elements.manageButton.classList.toggle(
    "hidden",
    state.auth.role !== "admin",
  );
  elements.readerProgress.classList.add("hidden");
  document.title = `${category} · 本地漫画库`;
  loadBooks({ page: state.page });
}

function initFavoritesView() {
  state.category = "";
  state.favoritesView = true;
  elements.backButton.classList.remove("hidden");
  elements.backButton.href = "/";
  elements.readerView.classList.add("hidden");
  elements.seriesView.classList.add("hidden");
  elements.homeView.classList.remove("hidden");
  elements.manageButton.classList.toggle(
    "hidden",
    state.auth.role !== "admin",
  );
  elements.readerProgress.classList.add("hidden");
  elements.favoritesButton.classList.add("active");
  document.title = "我的收藏 · 本地漫画库";
  loadBooks({ page: state.page });
}

function chapterPaginationButton(label, page, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `page-button${options.current ? " current" : ""}`;
  button.textContent = label;
  button.disabled = Boolean(options.disabled);
  if (!button.disabled && !options.current) {
    button.addEventListener("click", () => loadChapters(page));
  }
  return button;
}

function renderChapterPagination() {
  elements.chapterPagination.replaceChildren();
  if (state.chapterTotalPages <= 0) {
    elements.chapterPagination.classList.add("hidden");
    return;
  }
  elements.chapterPagination.classList.remove("hidden");
  elements.chapterPagination.append(
    chapterPaginationButton("首页", 1, {
      disabled: state.chapterPage <= 1,
    }),
    chapterPaginationButton("上一页", state.chapterPage - 1, {
      disabled: state.chapterPage <= 1,
    }),
  );
  let previous = 0;
  pageNumbers(state.chapterPage, state.chapterTotalPages).forEach((page) => {
    if (previous && page > previous + 1) {
      const gap = document.createElement("span");
      gap.className = "page-ellipsis";
      gap.textContent = "…";
      elements.chapterPagination.append(gap);
    }
    elements.chapterPagination.append(
      chapterPaginationButton(String(page), page, {
        current: page === state.chapterPage,
      }),
    );
    previous = page;
  });
  elements.chapterPagination.append(
    chapterPaginationButton("下一页", state.chapterPage + 1, {
      disabled: state.chapterPage >= state.chapterTotalPages,
    }),
    chapterPaginationButton("尾页", state.chapterTotalPages, {
      disabled: state.chapterPage >= state.chapterTotalPages,
    }),
  );
  const info = document.createElement("span");
  info.className = "pagination-info";
  info.textContent =
    `第 ${state.chapterPage} / ${state.chapterTotalPages} 页`;
  elements.chapterPagination.append(info);
}

async function loadChapters(page = 1) {
  if (!state.series) return;
  state.chapterPage = Math.max(1, Number(page) || 1);
  elements.chapterList.replaceChildren();
  setMessage(elements.seriesMessage);
  const params = new URLSearchParams({
    page: String(state.chapterPage),
    page_size: String(state.chapterPageSize),
    order: elements.chapterOrder.value,
  });
  const search = elements.chapterSearch.value.trim();
  const status = elements.chapterStatus.value;
  if (search) params.set("q", search);
  if (status) params.set("status", status);
  try {
    const payload = await fetchJSON(
      `/api/series/${encodeURIComponent(state.series.id)}/chapters?${params}`,
    );
    state.chapterPage = payload.page || 1;
    state.chapterTotalPages = payload.total_pages || 0;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((chapter) => {
      const item = document.createElement("a");
      item.className = `chapter-item${chapter.missing ? " missing" : ""}`;
      item.href = chapter.read_url;
      const order = document.createElement("span");
      order.className = "chapter-order";
      order.textContent = String(chapter.sort_order);
      const title = document.createElement("span");
      title.className = "chapter-name";
      title.textContent = chapter.title;
      const pages = document.createElement("span");
      pages.className = "chapter-pages";
      pages.textContent = chapter.page_count
        ? `${chapter.page_count} 页`
        : "文件缺失";
      const updated = document.createElement("span");
      updated.className = "chapter-updated";
      updated.textContent = chapter.updated_at || "";
      item.append(order, title, pages, updated);
      fragment.append(item);
    });
    elements.chapterList.append(fragment);
    if (!payload.items.length) {
      setMessage(elements.seriesMessage, "没有找到章节。");
    }
    renderChapterPagination();
  } catch (error) {
    setMessage(elements.seriesMessage, `章节加载失败：${error.message}`);
  }
}

async function initSeries(bookId) {
  elements.homeView.classList.add("hidden");
  elements.readerView.classList.add("hidden");
  elements.seriesView.classList.remove("hidden");
  elements.searchBox.classList.add("hidden");
  elements.categoryButton.classList.add("hidden");
  elements.favoritesButton.classList.add("hidden");
  elements.backButton.classList.remove("hidden");
  elements.backButton.href = "/";
  elements.manageButton.classList.toggle(
    "hidden",
    state.auth.role !== "admin",
  );
  elements.readerProgress.classList.add("hidden");
  document.title = "正在加载 · 本地漫画库";
  try {
    const book = await fetchJSON(
      `/api/series/${encodeURIComponent(bookId)}`,
    );
    state.series = book;
    elements.seriesTitle.textContent = book.title;
    elements.seriesMeta.textContent =
      `${book.chapter_count} 话 · `
      + `${book.chapter_complete_count} 话已完成`;
    elements.seriesCover.src = book.thumbnail_url || book.cover_url;
    elements.seriesCover.alt = book.title;
    elements.seriesCategories.replaceChildren();
    (book.categories || []).slice(0, 20).forEach((category) => {
      const chip = document.createElement("span");
      chip.className = "series-category";
      chip.textContent = category;
      elements.seriesCategories.append(chip);
    });
    document.title = `${book.title} · 本地漫画库`;
    await loadChapters(1);
  } catch (error) {
    setMessage(elements.seriesMessage, `漫画加载失败：${error.message}`);
  }
}

async function toggleFavorite() {
  if (!state.reader) return;
  const bookId = state.reader.id;
  const nextState = !state.reader.favorite;
  elements.favoriteToggleButton.disabled = true;
  try {
    const payload = await fetchJSON(
      `/api/books/${encodeURIComponent(bookId)}/favorite`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorite: nextState }),
      },
    );
    updateFavoriteCount(payload.favorite_count);
    updateFavoriteToggle(payload.favorite);
    showToast(payload.favorite ? "已加入收藏" : "已取消收藏");
  } catch (error) {
    showToast(`收藏操作失败：${error.message}`);
  } finally {
    elements.favoriteToggleButton.disabled = false;
  }
}

function addPagePlaceholder(page) {
  const shell = document.createElement("figure");
  shell.className = "page-shell";
  shell.style.aspectRatio = `${page.width} / ${page.height}`;
  shell.dataset.pageIndex = String(page.index);

  const skeleton = document.createElement("div");
  skeleton.className = "page-skeleton";
  skeleton.textContent = `第 ${page.index} 页`;

  const image = document.createElement("img");
  image.alt = `第 ${page.index} 页`;
  image.loading = "lazy";
  image.decoding = "async";
  image.src = page.url;

  const markLoaded = () => {
    image.classList.add("loaded");
    skeleton.remove();
  };
  image.addEventListener("load", markLoaded, { once: true });
  image.addEventListener("error", () => {
    skeleton.className = "page-error";
    skeleton.textContent = `第 ${page.index} 页加载失败，点击重试`;
    skeleton.addEventListener("click", () => {
      image.src = `${page.url}?retry=${Date.now()}`;
      skeleton.className = "page-skeleton";
      skeleton.textContent = `正在重试第 ${page.index} 页……`;
    }, { once: true });
  }, { once: true });

  shell.append(image, skeleton);
  if (!state.reader?.chapterId && state.auth.role === "admin") {
    const coverButton = document.createElement("button");
    coverButton.className = "page-cover-button";
    coverButton.type = "button";
    coverButton.textContent = "设为封面";
    coverButton.dataset.coverBookId = state.reader?.id || "";
    coverButton.dataset.coverFilename = page.filename;
    shell.append(coverButton);
  }
  if (typeof image.decode === "function") {
    image.decode().then(markLoaded).catch(() => {});
  }
  return shell;
}

async function loadReaderPages(limit) {
  if (!state.reader || state.readerLoading) return;
  if (state.reader.nextOffset >= state.reader.pageCount) {
    setMessage(elements.readerMessage, "已到最后一页。");
    return;
  }
  state.readerLoading = true;
  try {
    const params = new URLSearchParams({
      offset: String(state.reader.nextOffset),
      limit: String(limit),
    });
    const endpoint = state.reader.chapterId
      ? `/api/books/${encodeURIComponent(state.reader.id)}/chapters/`
        + `${state.reader.chapterId}/pages?${params}`
      : `/api/books/${encodeURIComponent(state.reader.id)}/pages?${params}`;
    const payload = await fetchJSON(
      endpoint,
    );
    const fragment = document.createDocumentFragment();
    payload.pages.forEach((page) => fragment.append(addPagePlaceholder(page)));
    elements.pageStream.append(fragment);
    state.reader.nextOffset += payload.pages.length;
    elements.readerProgress.textContent =
      `${Math.min(state.reader.nextOffset, state.reader.pageCount)} / ${state.reader.pageCount}`;
    if (state.reader.nextOffset >= state.reader.pageCount) {
      setMessage(elements.readerMessage, "已到最后一页。");
    } else {
      setMessage(elements.readerMessage);
    }
  } catch (error) {
    setMessage(elements.readerMessage, `加载下一页失败：${error.message}`);
    if (
      error.code === "external_storage_offline"
      || error.code === "external_storage_mismatch"
      || error.code === "book_content_missing"
    ) {
      elements.readerRetryButton.classList.remove("hidden");
    }
  } finally {
    state.readerLoading = false;
  }
}

function updateFavoriteToggle(favorite) {
  if (!state.reader) return;
  state.reader.favorite = Boolean(favorite);
  elements.favoriteToggleButton.classList.remove("hidden");
  elements.favoriteToggleButton.dataset.bookId = state.reader.id;
  elements.favoriteToggleButton.classList.toggle(
    "favorited",
    state.reader.favorite,
  );
  elements.favoriteToggleButton.textContent = state.reader.favorite
    ? "♥ 已收藏"
    : "♡ 添加收藏";
}

async function initReader(bookId, chapterId = 0) {
  elements.homeView.classList.add("hidden");
  elements.seriesView.classList.add("hidden");
  elements.readerView.classList.remove("hidden");
  elements.searchBox.classList.add("hidden");
  elements.categoryButton.classList.add("hidden");
  elements.favoritesButton.classList.add("hidden");
  elements.manageButton.classList.toggle(
    "hidden",
    state.auth.role !== "admin",
  );
  elements.backButton.classList.remove("hidden");
  elements.backButton.href = chapterId
    ? `/series/${encodeURIComponent(bookId)}`
    : "/";
  elements.readerProgress.classList.remove("hidden");
  elements.readerChapterNav.classList.toggle("hidden", !chapterId);
  document.title = "正在加载 · 本地漫画库";
  try {
    let book;
    let chapter = null;
    if (chapterId) {
      chapter = await fetchJSON(
        `/api/books/${encodeURIComponent(bookId)}/chapters/${chapterId}`,
      );
      book = chapter.book;
      elements.readerTitle.textContent = chapter.title;
      elements.readerMeta.textContent =
        `${book.title} · ${chapter.page_count} 页`;
      elements.readerChapterNav.classList.remove("hidden");
      if (chapter.previous_chapter) {
        elements.previousChapterButton.href =
          chapter.previous_chapter.read_url;
        elements.previousChapterButton.classList.remove("hidden");
      } else {
        elements.previousChapterButton.classList.add("hidden");
      }
      if (chapter.next_chapter) {
        elements.nextChapterButton.href = chapter.next_chapter.read_url;
        elements.nextChapterButton.classList.remove("hidden");
      } else {
        elements.nextChapterButton.classList.add("hidden");
      }
      document.title = `${chapter.title} · ${book.title} · 本地漫画库`;
    } else {
      book = await fetchJSON(`/api/books/${encodeURIComponent(bookId)}`);
      elements.readerTitle.textContent = book.title;
      elements.readerMeta.textContent =
        `${book.page_count} 页${book.complete ? " · 已完成" : " · 未完成"}`;
      document.title = `${book.title} · 本地漫画库`;
    }
    state.reader = {
      id: book.id,
      chapterId,
      pageCount: chapter ? chapter.page_count : book.page_count,
      nextOffset: 0,
      favorite: Boolean(book.favorite),
    };
    updateFavoriteCount(book.favorite_count || state.favoritesCount);
    updateFavoriteToggle(book.favorite);
    if (state.reader.pageCount === 0) {
      setMessage(elements.readerMessage, "这本漫画没有可读取的图片。");
      return;
    }
    await loadReaderPages(Math.min(3, state.reader.pageCount));
  } catch (error) {
    const externalError = [
      "external_storage_offline",
      "external_storage_mismatch",
      "book_content_missing",
    ].includes(error.code);
    elements.readerTitle.textContent =
      error.code === "external_storage_offline"
        ? "外盘未连接"
        : error.code === "external_storage_mismatch"
          ? "外盘身份不匹配"
          : "漫画加载失败";
    elements.readerMeta.textContent = error.message;
    setMessage(
      elements.readerMessage,
      externalError
        ? "请连接对应外盘，然后点击“重新检测”。"
        : "请返回首页后重试。",
    );
    if (externalError) {
      elements.readerRetryButton.classList.remove("hidden");
    }
  }
}

function observeReader() {
  new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    if (!state.reader || state.readerLoading) return;
    if (state.reader.nextOffset >= state.reader.pageCount) return;
    loadReaderPages(1);
  }, { rootMargin: "900px 0px" }).observe(elements.readerSentinel);
}

async function loadCoverOptions(offset = 0) {
  if (!state.coverPicker) return;
  setMessage(elements.coverDialogMessage, "正在加载页面缩略图……");
  elements.coverOptionGrid.replaceChildren();
  try {
    const params = new URLSearchParams({
      offset: String(Math.max(0, offset)),
      limit: "12",
    });
    const payload = await fetchJSON(
      `/api/books/${encodeURIComponent(state.coverPicker.id)}/cover-options?${params}`,
    );
    state.coverPicker.offset = payload.offset;
    state.coverPicker.total = payload.total;
    state.coverPicker.book = payload.book;
    elements.coverDialogTitle.textContent = `选择封面 · ${payload.book.title}`;
    const fragment = document.createDocumentFragment();
    payload.items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `cover-option${item.selected ? " selected" : ""}`;
      button.dataset.coverFilename = item.filename;
      const image = document.createElement("img");
      image.loading = "lazy";
      image.src = item.thumbnail_url;
      image.alt = `第 ${item.index} 页`;
      const label = document.createElement("span");
      label.textContent = `第 ${item.index} 页`;
      button.append(image, label);
      fragment.append(button);
    });
    elements.coverOptionGrid.append(fragment);
    setMessage(elements.coverDialogMessage);
    elements.coverPrev.disabled = payload.offset <= 0;
    elements.coverNext.disabled = payload.offset + payload.limit >= payload.total;
  } catch (error) {
    setMessage(elements.coverDialogMessage, `加载失败：${error.message}`);
  }
}

async function openCoverPicker(bookId) {
  state.coverPicker = { id: bookId, offset: 0, total: 0, book: null };
  elements.coverOptionGrid.replaceChildren();
  elements.coverDialogTitle.textContent = "选择封面";
  if (typeof elements.coverDialog.showModal === "function") {
    elements.coverDialog.showModal();
  } else {
    elements.coverDialog.setAttribute("open", "");
  }
  await loadCoverOptions(0);
}

async function applyCover(filename) {
  if (!state.coverPicker || !filename) return;
  setMessage(elements.coverDialogMessage, "正在设置封面……");
  try {
    const payload = await fetchJSON(
      `/api/books/${encodeURIComponent(state.coverPicker.id)}/cover`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
      },
    );
    if (elements.coverDialog.open) elements.coverDialog.close();
    showToast("封面已更新");
    if (readerIdFromPath()) {
      const image = document.querySelector(
        `.page-shell[data-page-index] img[src*="${encodeURIComponent(filename)}"]`,
      );
      if (image) image.scrollIntoView({ block: "center" });
    } else {
      resetHomeAndLoad();
    }
  } catch (error) {
    setMessage(elements.coverDialogMessage, `设置失败：${error.message}`);
  }
}

async function resetCover() {
  if (!state.coverPicker) return;
  setMessage(elements.coverDialogMessage, "正在恢复默认封面……");
  try {
    await fetchJSON(
      `/api/books/${encodeURIComponent(state.coverPicker.id)}/cover`,
      { method: "DELETE" },
    );
    if (elements.coverDialog.open) elements.coverDialog.close();
    showToast("已恢复默认封面");
    if (!readerIdFromPath()) resetHomeAndLoad();
  } catch (error) {
    setMessage(elements.coverDialogMessage, `恢复失败：${error.message}`);
  }
}

async function changeCategoryBlocked(name, blocked) {
  setMessage(
    elements.categoryDialogMessage,
    blocked ? "正在屏蔽分类……" : "正在恢复分类……",
  );
  try {
    await fetchJSON("/api/categories/blocked", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, blocked }),
    });
    if (blocked && state.category &&
        state.category.normalize("NFC") === name.normalize("NFC")) {
      location.href = "/";
      return;
    }
    await loadCategories();
    if (!readerIdFromPath()) resetHomeAndLoad();
  } catch (error) {
    setMessage(
      elements.categoryDialogMessage,
      `操作失败：${error.message}`,
    );
  }
}

function createCategoryChip(category) {
  const chip = document.createElement("div");
  chip.className = "category-chip";
  chip.dataset.categoryName = category.name;

  const main = document.createElement("button");
  main.type = "button";
  main.className = "category-chip-main";
  const name = document.createElement("span");
  name.className = "category-chip-name";
  name.textContent = category.name;
  name.title = category.name;
  const count = document.createElement("span");
  count.className = "category-chip-count";
  count.textContent = category.blocked
    ? String(category.book_count)
    : String(category.visible_count);
  count.title = category.blocked
    ? `该分类已屏蔽，关联 ${category.book_count} 部漫画仍正常显示`
    : `共 ${category.book_count} 部漫画`;
  main.append(name, count);
  main.addEventListener("click", () => {
    if (category.blocked) {
      showToast("该分类已被屏蔽，请先点击“有效”恢复");
      return;
    }
    elements.categoryDialog.close();
    location.href = `/category/${encodeURIComponent(category.name)}`;
  });
  chip.append(main);

  if (!category.blocked && state.blockMode) {
    const blockButton = document.createElement("button");
    blockButton.type = "button";
    blockButton.className = "category-chip-action block-action";
    blockButton.textContent = "屏蔽";
    blockButton.addEventListener("click", () => {
      changeCategoryBlocked(category.name, true);
    });
    chip.append(blockButton);
  }
  if (category.blocked && state.restoreMode) {
    const restoreButton = document.createElement("button");
    restoreButton.type = "button";
    restoreButton.className = "category-chip-action restore-action";
    restoreButton.textContent = "有效";
    restoreButton.addEventListener("click", () => {
      changeCategoryBlocked(category.name, false);
    });
    chip.append(restoreButton);
  }
  return chip;
}

async function loadCategories() {
  setMessage(elements.categoryDialogMessage, "正在读取分类……");
  elements.validCategoryList.replaceChildren();
  elements.blockedCategoryList.replaceChildren();
  elements.blockModeButton.classList.toggle("active", state.blockMode);
  elements.restoreModeButton.classList.toggle("active", state.restoreMode);
  elements.blockModeButton.textContent =
    state.blockMode ? "完成屏蔽" : "屏蔽操作";
  elements.restoreModeButton.textContent =
    state.restoreMode ? "完成有效" : "有效操作";
  try {
    const payload = await fetchJSON("/api/categories");
    const valid = payload.items.filter((item) => !item.blocked);
    const blocked = payload.items.filter((item) => item.blocked);
    const validFragment = document.createDocumentFragment();
    const blockedFragment = document.createDocumentFragment();
    valid.forEach((category) => validFragment.append(createCategoryChip(category)));
    blocked.forEach((category) => blockedFragment.append(createCategoryChip(category)));

    if (!valid.length) {
      const empty = document.createElement("span");
      empty.className = "category-empty";
      empty.textContent = "没有有效分类";
      validFragment.append(empty);
    }
    if (!blocked.length) {
      const empty = document.createElement("span");
      empty.className = "category-empty";
      empty.textContent = "没有屏蔽分类";
      blockedFragment.append(empty);
    }
    elements.validCategoryList.append(validFragment);
    elements.blockedCategoryList.append(blockedFragment);
    elements.validCategoryCount.textContent = String(valid.length);
    elements.blockedCategoryCount.textContent = String(blocked.length);
    setMessage(elements.categoryDialogMessage);
  } catch (error) {
    setMessage(elements.categoryDialogMessage, `加载失败：${error.message}`);
  }
}

function openCategoryDialog() {
  state.blockMode = false;
  state.restoreMode = false;
  if (typeof elements.categoryDialog.showModal === "function") {
    elements.categoryDialog.showModal();
  } else {
    elements.categoryDialog.setAttribute("open", "");
  }
  loadCategories();
}

elements.searchInput.addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.search = elements.searchInput.value.trim();
  state.searchTimer = setTimeout(resetHomeAndLoad, 250);
});

elements.pageSizeSelect.addEventListener("change", () => {
  state.pageSize = Number(elements.pageSizeSelect.value) || 30;
  resetHomeAndLoad(true);
});

elements.categoryButton.addEventListener("click", openCategoryDialog);

elements.favoriteToggleButton.addEventListener("click", toggleFavorite);

elements.readerRetryButton.addEventListener("click", () => location.reload());

elements.closeCategoryDialog.addEventListener("click", () => {
  elements.categoryDialog.close();
});

elements.blockModeButton.addEventListener("click", () => {
  state.blockMode = !state.blockMode;
  loadCategories();
});

elements.restoreModeButton.addEventListener("click", () => {
  state.restoreMode = !state.restoreMode;
  loadCategories();
});

elements.bookGrid.addEventListener("click", (event) => {
  const button = event.target.closest("[data-cover-book-id]");
  if (!button) return;
  event.preventDefault();
  openCoverPicker(button.dataset.coverBookId);
});

elements.pageStream.addEventListener("click", (event) => {
  const button = event.target.closest(".page-cover-button");
  if (!button) return;
  if (!state.coverPicker || state.coverPicker.id !== button.dataset.coverBookId) {
    state.coverPicker = {
      id: button.dataset.coverBookId,
      offset: 0,
      total: state.reader?.pageCount || 0,
      book: null,
    };
  }
  applyCover(button.dataset.coverFilename);
});

elements.coverOptionGrid.addEventListener("click", (event) => {
  const option = event.target.closest("[data-cover-filename]");
  if (!option) return;
  applyCover(option.dataset.coverFilename);
});

elements.closeCoverDialog.addEventListener("click", () => {
  elements.coverDialog.close();
});

elements.coverPrev.addEventListener("click", () => {
  if (!state.coverPicker) return;
  loadCoverOptions(state.coverPicker.offset - 12);
});

elements.coverNext.addEventListener("click", () => {
  if (!state.coverPicker) return;
  loadCoverOptions(state.coverPicker.offset + 12);
});

elements.coverReset.addEventListener("click", resetCover);

elements.chapterSearch.addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadChapters(1), 250);
});

elements.chapterOrder.addEventListener("change", () => loadChapters(1));
elements.chapterStatus.addEventListener("change", () => loadChapters(1));
elements.chapterPageSize.addEventListener("change", () => {
  state.chapterPageSize = Number(elements.chapterPageSize.value) || 50;
  loadChapters(1);
});

window.addEventListener("popstate", () => location.reload());

async function boot() {
  await initializeAuth();
  const readerId = readerIdFromPath();
  const chapterId = readerChapterIdFromPath();
  const seriesId = seriesIdFromPath();
  const categoryName = categoryFromPath();
  const favoritesView = favoritesFromPath();
  initializeListStateFromUrl();
  if (readerId) {
    observeReader();
    initReader(readerId, chapterId);
  } else if (seriesId) {
    initSeries(seriesId);
  } else if (categoryName) {
    initCategoryView(categoryName);
  } else if (favoritesView) {
    loadFavoriteCount();
    initFavoritesView();
  } else {
    loadFavoriteCount();
    loadBooks({ page: state.page });
  }
}

boot();
