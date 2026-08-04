(function () {
  "use strict";

  function csrfHeaders(extra) {
    return Object.assign({"X-CR8-Request": "1"}, extra || {});
  }

  if (window.htmx) window.htmx.config.historyCacheSize = 0;
  function requestUndo() {
    window.htmx.ajax("POST", "/undo", {
      target: "#write-feedback",
      swap: "innerHTML"
    });
  }

  function scheduleUndoToast(root) {
    var toast = (root || document).querySelector("[data-undo-toast]");
    if (!toast || toast.dataset.dismissScheduled === "true") return;
    toast.dataset.dismissScheduled = "true";
    window.setTimeout(function () {
      if (toast.isConnected) toast.remove();
    }, 30000);
  }
  scheduleUndoToast(document);
  document.addEventListener("htmx:afterSwap", function (event) {
    scheduleUndoToast(event.detail.target || document);
  });
  var librarySwapState = null;
  var detailSwapState = null;

  function setOptimisticPressed(control) {
    if (!control || !control.hasAttribute("aria-pressed")) return;
    var previous = control.getAttribute("aria-pressed") === "true";
    control.dataset.optimisticPrevious = String(previous);
    control.setAttribute("aria-pressed", String(!previous));
    if (control.classList.contains("heart") && !previous) pulseOnce(control);
  }

  document.addEventListener("click", function (event) {
    var control = event.target.closest(
      "[data-optimistic][aria-pressed], [data-filter-link][aria-pressed]"
    );
    if (control) setOptimisticPressed(control);
  }, true);

  document.addEventListener("htmx:afterRequest", function (event) {
    var control = event.detail.elt;
    if (
      event.detail.successful ||
      !control ||
      control.dataset.optimisticPrevious == null
    ) return;
    control.setAttribute("aria-pressed", control.dataset.optimisticPrevious);
    delete control.dataset.optimisticPrevious;
  });

  document.addEventListener("click", function (event) {
    var facet = event.target.closest("[data-filter-link][data-multi-url]");
    if (!facet || (!event.metaKey && !event.ctrlKey)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    var url = facet.dataset.multiUrl;
    history.pushState(null, "", url);
    window.htmx.ajax("GET", url, {
      target: "#library-results",
      select: "#library-results",
      swap: "outerHTML"
    });
  }, true);

  document.addEventListener("htmx:beforeRequest", function (event) {
    var target = event.detail.target;
    if (target && target.id === "detail-panel") {
      var detailScroll = target.querySelector(".detail-scroll");
      var versionRail = target.querySelector(".detail-version-rail");
      detailSwapState = {
        detailScroll: detailScroll ? detailScroll.scrollTop : 0,
        versionRail: versionRail ? versionRail.scrollTop : 0
      };
    }
    if (!target || target.id !== "library-results") return;
    var scroll = document.querySelector(".library-scroll");
    var rail = document.getElementById("filter-sheet-body");
    var groupScroll = {};
    document.querySelectorAll("[data-facet-group]").forEach(function (group) {
      var options = group.querySelector(".rail-filter-options");
      groupScroll[group.dataset.facetGroup] = options ? options.scrollTop : 0;
    });
    librarySwapState = {
      libraryScroll: scroll ? scroll.scrollTop : 0,
      railScroll: rail ? rail.scrollTop : 0,
      groupScroll: groupScroll,
      selected: Array.from(
        document.querySelectorAll(".select-song:checked")
      ).map(function (checkbox) { return checkbox.value; })
    };
  });

  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail.target;
    if (!target || target.id !== "library-results") return;
    var page = new DOMParser().parseFromString(
      event.detail.xhr.responseText,
      "text/html"
    );
    var nextRail = page.getElementById("filter-sheet-body");
    var rail = document.getElementById("filter-sheet-body");
    if (!nextRail || !rail) return;
    rail.replaceWith(nextRail);
    window.htmx.process(nextRail);
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target && event.detail.target.id === "detail-panel") {
      var detailState = detailSwapState;
      detailSwapState = null;
      if (detailState) {
        var nextDetailScroll = event.detail.target.querySelector(".detail-scroll");
        var nextVersionRail = event.detail.target.querySelector(".detail-version-rail");
        if (nextDetailScroll) nextDetailScroll.scrollTop = detailState.detailScroll;
        if (nextVersionRail) nextVersionRail.scrollTop = detailState.versionRail;
      }
      return;
    }
    if (!event.detail.target || event.detail.target.id !== "library-results") return;
    var state = librarySwapState;
    librarySwapState = null;
    if (!state) return;
    var scroll = document.querySelector(".library-scroll");
    var rail = document.getElementById("filter-sheet-body");
    if (scroll) scroll.scrollTop = state.libraryScroll;
    if (rail) rail.scrollTop = state.railScroll;
    document.querySelectorAll("[data-facet-group]").forEach(function (group) {
      var options = group.querySelector(".rail-filter-options");
      if (options) {
        options.scrollTop = state.groupScroll[group.dataset.facetGroup] || 0;
      }
    });
    state.selected.forEach(function (value) {
      var checkbox = document.querySelector(
        '.select-song[value="' + CSS.escape(value) + '"]'
      );
      if (!checkbox) return;
      checkbox.checked = true;
      var stack = checkbox.closest(".song-stack");
      if (stack) stack.classList.add("is-selected");
    });
    syncBatchbar();
  });

  document.addEventListener("input", function (event) {
    if (!event.target.matches("[data-facet-search]")) return;
    var query = event.target.value.trim().toLocaleLowerCase();
    var group = event.target.closest("[data-facet-group]");
    if (!group) return;
    group.querySelectorAll(".rail-filter").forEach(function (item) {
      item.hidden = Boolean(
        query && !item.textContent.toLocaleLowerCase().includes(query)
      );
    });
  });

  function pulseOnce(el) {
    el.classList.add("js-just-toggled");
    el.addEventListener("animationend", function handler() {
      el.classList.remove("js-just-toggled");
      el.removeEventListener("animationend", handler);
    });
  }

  document.addEventListener("htmx:configRequest", function (event) {
    event.detail.headers["X-CR8-Request"] = "1";
  });

  document.addEventListener("submit", async function (event) {
    var form = event.target.closest("form[data-cr8-form]");
    if (!form || form.hasAttribute("hx-post") || !dock) return;
    event.preventDefault();
    var endpoint = form.getAttribute("action") || window.location.href;
    var response = await fetch(endpoint, {
      method: "POST",
      headers: csrfHeaders(),
      body: new FormData(form, event.submitter),
      credentials: "same-origin"
    });
    if (response.redirected) {
      window.htmx.ajax("GET", response.url, {
        target: "#cr8-main",
        select: "#cr8-main",
        swap: "outerHTML show:none"
      });
      return;
    }
    var html = await response.text();
    var page = new DOMParser().parseFromString(html, "text/html");
    var main = page.querySelector("#cr8-main");
    if (main) {
      window.htmx.swap("#cr8-main", main, {
        swapStyle: "outerHTML",
        select: "#cr8-main"
      });
    }
  });

  var dock = document.getElementById("crate-player");
  var audio = document.getElementById("crate-audio");
  var ownerPlayer = null;
  var ownerDigEndpoint = "/api/dig";

  async function ownerQueue(trigger) {
    var elements = [trigger];
    if (trigger.matches(".play-row")) {
      var response = await fetch(
        "/api/library-queue" + window.location.search,
        {credentials: "same-origin"}
      );
      if (!response.ok) return window.CratePlayback.collect([trigger]);
      return (await response.json()).tracks || [];
    } else if (trigger.matches(".mini-version")) {
      elements = trigger.closest(".mini-rail").querySelectorAll("[data-track-url]");
    } else if (trigger.matches(".version")) {
      elements = trigger.closest(".rail").querySelectorAll("[data-track-url]");
    }
    return window.CratePlayback.collect(elements);
  }

  function postProgress(track, state, heardSeconds, duration, reason) {
    var form = new FormData();
    form.set("state", state);
    form.set("heard_s", String(state === "heard" ? duration || heardSeconds : heardSeconds));
    form.set("started", String(reason === "start"));
    return fetch("/progress/" + track.bounce_ulid, {
      method: "POST",
      headers: csrfHeaders(),
      body: form,
      credentials: "same-origin"
    });
  }

  if (dock && audio && window.CratePlayback) {
    ownerPlayer = window.CratePlayback.attach({
      root: dock,
      audio: audio,
      waveform: document.getElementById("owner-wave"),
      waveHeight: 54,
      queueForTrigger: ownerQueue,
      progress: postProgress,
      refill: async function () {
        var response = await fetch(
          ownerDigEndpoint,
          {credentials: "same-origin"}
        );
        return response.ok ? response.json() : [];
      },
      render: function (track) {
        dock.style.setProperty("--era", "var(--era-" + track.era_css + ")");
        withFade(document.querySelector(".player-copy"), function () {
          document.getElementById("player-title").textContent = track.title;
          document.getElementById("player-meta").textContent =
            track.version_label + " · " + (track.key_canon || "—") + " · " +
            (track.key_camelot || "—");
          var reason = document.getElementById("player-reason");
          if (reason) {
            reason.textContent = track.dig_reason || "";
            reason.hidden = !track.dig_reason;
          }
          var cover = document.querySelector("[data-player-cover]");
          if (cover) {
            cover.className = "player-cover era-" + track.era_css;
            cover.textContent = track.title;
          }
          var era = document.querySelector("[data-player-era]");
          if (era) era.textContent = track.era;
        });
        updateDetailFromTrack(track);
      },
      renderTime: function (current, duration) {
        document.getElementById("player-time").textContent =
          current + " / " + duration;
      },
      tags: async function (track) {
        var response = await fetch(
          "/fragments/tagbar/" + track.bounce_ulid,
          {credentials: "same-origin"}
        );
        if (!response.ok) return;
        var html = await response.text();
        if (!isPlayingBounce(track.bounce_ulid)) return;
        var tags = document.getElementById("player-tags");
        tags.innerHTML = html;
        window.htmx.process(tags);
      }
    });
  }
  document.querySelectorAll("[data-comment-position]").forEach(function (marker) {
    marker.style.setProperty(
      "--comment-position",
      Number(marker.dataset.commentPosition || 0) + "%"
    );
  });

  function withFade(el, mutate) {
    if (!el) return mutate();
    el.classList.add("js-updating");
    requestAnimationFrame(function () {
      mutate();
      requestAnimationFrame(function () {
        el.classList.remove("js-updating");
      });
    });
  }

  var panelPinned = false;

  function syncPanelPin() {
    document.querySelectorAll("[data-panel-pin]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(panelPinned));
      button.textContent = panelPinned ? "pinned" : "pin";
    });
  }

  function songUlidFromRow(row) {
    var stack = row && row.closest(".song-stack");
    var prefix = "song-stack-";
    if (!stack || !stack.id.startsWith(prefix)) return "";
    return stack.id.slice(prefix.length);
  }

  function requestDetailForRow(row) {
    var songUlid = songUlidFromRow(row);
    if (!songUlid || !window.htmx || !document.getElementById("detail-panel")) {
      return;
    }
    window.htmx.ajax(
      "GET",
      "/fragments/detail/" + encodeURIComponent(songUlid),
      {target: "#detail-panel", swap: "outerHTML"}
    );
  }

  var sharedRowTail = null;

  function setTailTrack(control, row) {
    if (!control) return;
    control.dataset.trackId = row.dataset.trackId;
    control.dataset.trackUrl = "/api/tracks/" +
      encodeURIComponent(row.dataset.trackId);
    control.dataset.audioUrl = row.dataset.audioUrl;
    control.dataset.trackTitle = row.dataset.trackTitle;
  }

  function attachRowTail(row) {
    if (!row) return null;
    var line = row.closest(".song-line");
    var template = document.getElementById("row-tail-template");
    if (!line || !template) return null;
    if (sharedRowTail && sharedRowTail.parentElement === line) {
      return sharedRowTail;
    }
    if (!sharedRowTail) {
      sharedRowTail = template.content.firstElementChild.cloneNode(true);
      window.htmx.process(sharedRowTail);
    }
    line.appendChild(sharedRowTail);
    var title = row.dataset.trackTitle || "song";
    var trackId = row.dataset.trackId || "";
    var songUlid = songUlidFromRow(row);
    sharedRowTail.setAttribute("aria-label", "Actions for " + title);
    sharedRowTail.querySelectorAll(".row-overflow").forEach(function (menu) {
      menu.open = false;
    });
    var play = sharedRowTail.querySelector("[data-row-play]");
    setTailTrack(play, row);
    if (play) play.setAttribute("aria-label", "Play " + title);
    var heart = sharedRowTail.querySelector(".row-heart");
    if (heart) {
      heart.setAttribute("aria-label", "Heart " + title);
      heart.setAttribute("aria-pressed", "false");
      delete heart.dataset.optimisticPrevious;
      heart.dataset.bounceUlid = trackId;
    }
    var tag = sharedRowTail.querySelector("[data-row-tag]");
    if (tag) tag.setAttribute("aria-label", "Tag " + title);
    var queue = sharedRowTail.querySelector("[data-row-queue-add]");
    setTailTrack(queue, row);
    if (queue) queue.setAttribute("aria-label", "Add " + title + " to queue");
    var checkbox = line.querySelector(".select-song");
    var download = sharedRowTail.querySelector(".row-download");
    if (download) {
      download.hidden = !checkbox || !Number(checkbox.dataset.originalSize || 0);
      download.href = "/download/" + encodeURIComponent(trackId) +
        "?format=original";
      download.setAttribute("aria-label", "Download original " + title);
    }
    var rip = sharedRowTail.querySelector("[data-row-rip-stems]");
    if (rip) {
      rip.dataset.bounceUlid = trackId;
      rip.setAttribute("aria-label", "Rip stems for " + title);
    }
    var copyLink = sharedRowTail.querySelector("[data-row-copy-link]");
    if (copyLink) {
      copyLink.dataset.songUlid = songUlid || "";
      copyLink.setAttribute("aria-label", "Copy a link to " + title);
    }
    var detail = sharedRowTail.querySelector(".row-detail");
    if (detail) {
      detail.href = "/songs/" + encodeURIComponent(songUlid);
      detail.setAttribute("aria-label", "Open " + title + " details");
    }
    if (ownerPlayer) ownerPlayer.syncPlaybackState();
    return sharedRowTail;
  }

  document.addEventListener("pointerover", function (event) {
    var line = event.target.closest(".library .song-line");
    if (line) attachRowTail(line.querySelector(".play-row"));
  });

  document.addEventListener("focusin", function (event) {
    var line = event.target.closest(".library .song-line");
    if (line) attachRowTail(line.querySelector(".play-row"));
  });

  function updateDetailFromRow(row, options) {
    if (!row) return;
    var settings = options || {};
    var updatePanel = !panelPinned || settings.forcePanel;
    var selected = document.querySelectorAll(".song-stack.is-cursor");
    if (settings.instant) {
      selected.forEach(function (stack) {
        stack.classList.add("is-instant-selection");
      });
    }
    selected.forEach(function (stack) {
      stack.classList.remove("is-cursor");
    });
    if (updatePanel) {
      document.querySelectorAll(".song-stack.is-panel-subject").forEach(function (item) {
        item.classList.remove("is-panel-subject");
      });
    }
    var stack = row.closest(".song-stack");
    if (settings.instant && stack) stack.classList.add("is-instant-selection");
    document.querySelectorAll(".library .play-row").forEach(function (item) {
      item.tabIndex = -1;
      item.setAttribute("aria-selected", "false");
    });
    row.tabIndex = 0;
    row.setAttribute("aria-selected", "true");
    if (stack) {
      stack.classList.add("is-cursor");
      if (updatePanel) stack.classList.add("is-panel-subject");
    }
    if (!updatePanel) {
      if (settings.instant) {
        requestAnimationFrame(function () {
          document.querySelectorAll(".song-stack.is-instant-selection").forEach(function (item) {
            item.classList.remove("is-instant-selection");
          });
        });
      }
      return;
    }
    if (settings.instant) {
      requestAnimationFrame(function () {
        document.querySelectorAll(".song-stack.is-instant-selection").forEach(function (item) {
          item.classList.remove("is-instant-selection");
        });
      });
    }
    if (settings.requestDetail) requestDetailForRow(row);
  }

  function updateDetailFromTrack(track) {
    revealTrack(track, {
      focus: false,
      flash: false,
      followPlayback: true
    });
  }

  function isPlayingBounce(bounceUlid) {
    return Boolean(
      ownerPlayer &&
      ownerPlayer.currentTrack &&
      ownerPlayer.currentTrack.bounce_ulid === bounceUlid
    );
  }

  function playCursorRow(row) {
    if (!row || !ownerPlayer) return;
    ownerPlayer.playTrigger(row);
  }

  async function revealTrack(track, options) {
    if (!track) return;
    var settings = options || {};
    var bounce = track.bounce_ulid;
    var selector = '.play-row[data-track-id="' + CSS.escape(bounce) + '"]';
    var row = document.querySelector(selector);
    if (!row) {
      var queueResponse = await fetch(
        "/api/library-queue" + window.location.search,
        {credentials: "same-origin"}
      );
      if (settings.followPlayback && !isPlayingBounce(bounce)) return;
      if (!queueResponse.ok) return;
      var tracks = (await queueResponse.json()).tracks || [];
      var index = tracks.findIndex(function (item) {
        return String(item.id || item.bounce_ulid) === String(bounce);
      });
      if (index < 0) return;
      var params = new URLSearchParams(window.location.search);
      params.set("offset", String(Math.floor(index / 48) * 48));
      var pageResponse = await fetch("/?" + params, {
        headers: {"HX-Request": "true"},
        credentials: "same-origin"
      });
      if (settings.followPlayback && !isPlayingBounce(bounce)) return;
      if (!pageResponse.ok) return;
      var library = document.querySelector(".library");
      if (!library) return;
      library.innerHTML = await pageResponse.text();
      window.htmx.process(library);
      row = document.querySelector(selector);
    }
    if (!row) return;
    if (settings.followPlayback && !isPlayingBounce(bounce)) return;
    updateDetailFromRow(row, {
      instant: true,
      requestDetail: true,
      fromPlayback: true
    });
    row.scrollIntoView({block: "center"});
    if (settings.focus !== false) row.focus({preventScroll: true});
    var stack = row.closest(".song-stack");
    if (!stack || settings.flash === false) return;
    stack.classList.add("is-revealed");
    window.setTimeout(function () {
      stack.classList.remove("is-revealed");
    }, 900);
  }

  async function revealPlaying() {
    if (!ownerPlayer || !ownerPlayer.currentTrack) return;
    return revealTrack(ownerPlayer.currentTrack, {focus: true, flash: true});
  }

  function openPlayerExtras() {
    var extras = document.getElementById("player-extras");
    var expand = document.querySelector('[data-player-action="expand"]');
    if (!extras || !expand || extras.dataset.open === "true") return extras;
    extras.hidden = false;
    extras.getBoundingClientRect();
    extras.dataset.open = "true";
    expand.setAttribute("aria-expanded", "true");
    expand.textContent = "queue ▾";
    return extras;
  }

  function stopDigging() {
    if (!ownerPlayer || ownerPlayer.queue.mode !== "dig") return;
    var current = ownerPlayer.queue.current();
    ownerPlayer.queue.replace(
      current ? [current] : [],
      current ? current.id : null,
      {mode: "queue", label: ""}
    );
    ownerPlayer.emitQueue();
  }

  function syncDigMode(snapshot) {
    var state = snapshot || (
      ownerPlayer ? ownerPlayer.queue.snapshot() : null
    );
    if (!state) return;
    var digging = state.mode === "dig";
    var dug = state.cursor >= 0 ? state.cursor + 1 : 0;
    var left = Math.max(state.playQueue.length - dug, 0);
    document.querySelectorAll("[data-dig-mode-status]").forEach(function (status) {
      status.textContent = digging
        ? "digging · " + dug + " played · " + left + " to go"
        : "the cr8 · owner library";
    });
    document.querySelectorAll(
      '[data-dig-action="dig"], [data-dig-action="dig-untagged"]'
    ).forEach(function (button) {
      button.setAttribute("aria-pressed", String(digging));
    });
    document.querySelectorAll("[data-stop-dig]").forEach(function (button) {
      button.hidden = !digging;
    });
  }

  if (dock) {
    dock.addEventListener("cr8:queuechange", function (event) {
      syncDigMode(event.detail);
    });
  }
  syncDigMode();
  syncPanelPin();

  document.addEventListener("click", async function (event) {
    var close = event.target.closest("[data-row-detail-close]");
    if (close) {
      event.preventDefault();
      close.closest(".row-detail-expand").remove();
      return;
    }
    var link = event.target.closest(".row-detail");
    if (!link || !window.matchMedia("(max-width: 1099px)").matches) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    var stack = link.closest(".song-stack");
    var existing = stack && stack.querySelector(".row-detail-expand");
    if (existing) {
      existing.remove();
      return;
    }
    document.querySelectorAll(".row-detail-expand").forEach(function (detail) {
      detail.remove();
    });
    var response = await fetch(
      link.getAttribute("href") + "/row-detail",
      {credentials: "same-origin"}
    );
    if (!response.ok || !stack) return;
    stack.insertAdjacentHTML("beforeend", await response.text());
    window.htmx.process(stack.querySelector(".row-detail-expand"));
  }, true);

  document.addEventListener("click", async function (event) {
    var pin = event.target.closest("[data-panel-pin]");
    if (pin) {
      event.preventDefault();
      panelPinned = !panelPinned;
      syncPanelPin();
      if (!panelPinned) {
        var currentRow = document.querySelector(
          ".song-stack.is-cursor .play-row"
        );
        updateDetailFromRow(currentRow, {
          instant: true,
          requestDetail: true,
          forcePanel: true
        });
      }
      return;
    }
    var stop = event.target.closest("[data-stop-dig]");
    if (stop) {
      event.preventDefault();
      stopDigging();
      return;
    }
    var playerTag = event.target.closest("[data-player-tag]");
    if (playerTag) {
      event.preventDefault();
      var tagExtras = openPlayerExtras();
      window.setTimeout(function () {
        var firstTag = tagExtras && tagExtras.querySelector(
          "[data-tag-toggle], button, input"
        );
        if (firstTag) firstTag.focus();
      }, 0);
      return;
    }
    var reveal = event.target.closest("[data-player-reveal]");
    if (reveal) {
      event.preventDefault();
      revealPlaying();
      return;
    }
    var heart = event.target.closest(".heart");
    if (heart && heart.classList.contains("row-heart")) {
      event.preventDefault();
      var previous = heart.dataset.optimisticPrevious;
      var heartResponse = null;
      try {
        heartResponse = await fetch(
          "/reactions/" + encodeURIComponent(heart.dataset.bounceUlid) + "/heart",
          {
            method: "POST",
            headers: csrfHeaders(),
            credentials: "same-origin"
          }
        );
      } catch (_error) {
        heartResponse = null;
      }
      if ((!heartResponse || !heartResponse.ok) && previous != null) {
        heart.setAttribute("aria-pressed", previous);
      }
      delete heart.dataset.optimisticPrevious;
      return;
    }
    if (
      heart &&
      !heart.hasAttribute("data-optimistic") &&
      heart.getAttribute("aria-pressed") !== "true"
    ) pulseOnce(heart);
    var row = event.target.closest(".play-row");
    if (row) {
      attachRowTail(row);
      updateDetailFromRow(row, {instant: true, requestDetail: true});
      if (document.activeElement !== row) row.focus({preventScroll: true});
    }
    var marker = event.target.closest("[data-comment-time]");
    if (marker && ownerPlayer) {
      event.preventDefault();
      ownerPlayer.playItems(
        [{
          id: marker.dataset.trackId,
          trackUrl: marker.dataset.trackUrl,
          audioUrl: marker.dataset.audioUrl,
          title: marker.dataset.trackTitle
        }],
        marker.dataset.trackId,
        {userGesture: true}
      ).then(function () {
        var seek = function () {
          try {
            audio.currentTime = Number(marker.dataset.commentTime || 0);
          } catch (_error) {
            return;
          }
        };
        if (audio.readyState >= 1) seek();
        else audio.addEventListener("loadedmetadata", seek, {once: true});
      });
      return;
    }
    var expand = event.target.closest('[data-player-action="expand"]');
    if (!expand) return;
    var extras = document.getElementById("player-extras");
    if (!extras) return;
    var opening = extras.dataset.open !== "true";
    if (opening) {
      extras.hidden = false;
      extras.getBoundingClientRect();
    }
    extras.dataset.open = String(opening);
    expand.setAttribute("aria-expanded", String(opening));
    expand.textContent = opening ? "queue ▾" : "queue ▸";
    if (!opening) {
      extras.addEventListener("transitionend", function handler(event) {
        if (event.target !== extras || event.propertyName !== "opacity") return;
        if (extras.dataset.open !== "true") extras.hidden = true;
        extras.removeEventListener("transitionend", handler);
      });
    }
  });

  document.addEventListener("dblclick", function (event) {
    var row = event.target.closest(".play-row");
    if (!row) return;
    event.preventDefault();
    playCursorRow(row);
  });

  function configureFilterSheet() {
    var sheet = document.querySelector(".filter-sheet");
    if (!sheet) return;
    if (window.matchMedia("(max-width: 759px)").matches) sheet.removeAttribute("open");
    else sheet.setAttribute("open", "");
  }
  configureFilterSheet();
  document.addEventListener("htmx:afterSwap", configureFilterSheet);
  document.addEventListener("htmx:afterSwap", function () {
    syncPanelPin();
    syncDigMode();
    syncBatchbar();
    initializeCollectionList(document);
    syncCollectionCount();
  });

  function extendGestureQueue(current, items, dig, label) {
    var active = ownerPlayer.queue.current();
    if (!active || active.id !== current.id) return;
    var rest = (items || []).filter(function (item) {
      return item.id !== current.id;
    });
    if (!dig) rest = window.CratePlayback.fisherYates(rest);
    ownerPlayer.queue.replace(
      [current].concat(rest),
      current.id,
      {mode: dig ? "dig" : "queue", label: label}
    );
    ownerPlayer.queue.shuffleEnabled = !dig;
    ownerPlayer.queue.save();
    ownerPlayer.emitQueue();
  }

  document.addEventListener("click", async function (event) {
    var gesture = event.target.closest("[data-dig-action]");
    if (!gesture || !ownerPlayer) return;
    event.preventDefault();
    var action = gesture.dataset.digAction;
    var dig = action === "dig" || action === "dig-untagged";
    var selector = "[data-" + action + "-seed]";
    var items = window.CratePlayback.collect(
      document.querySelectorAll(selector)
    );
    var current = items[0] || null;
    if (current) {
      ownerPlayer.playItems(
        [current],
        current.id,
        {
          shuffle: !dig,
          mode: dig ? "dig" : "queue",
          userGesture: true
        }
      );
    }
    var filters = new URLSearchParams(
      action === "shuffle-all" ? "" : window.location.search
    );
    filters.delete("offset");
    if (action === "dig-untagged") filters.set("untagged", "true");
    var query = filters.toString();
    var endpoint = (dig ? "/api/dig" : "/api/library-queue") + (
      query ? "?" + query : ""
    );
    if (dig) ownerDigEndpoint = endpoint;
    var response = await fetch(endpoint, {credentials: "same-origin"});
    if (!response.ok) return;
    var tracks = (await response.json()).tracks;
    if (!tracks.length) return;
    if (!current) {
      ownerPlayer.playItems(tracks, null, {
        shuffle: !dig,
        mode: dig ? "dig" : "queue",
        label: action === "dig-untagged" ? "untagged" : tracks.length + " songs",
        userGesture: true
      });
      return;
    }
    extendGestureQueue(
      current,
      tracks,
      dig,
      action === "shuffle-all"
        ? tracks.length + " songs"
        : action === "dig-untagged"
          ? "untagged"
          : tracks.length + " filtered"
    );
  });


  // When you shuffle or dig, the list must BECOME the queue — in queue order,
  // with the sounding track marked and scrolled to. Otherwise the list shows one
  // order while something else plays, and "shuffle" is invisible.
  function reflowListToQueue(player) {
    if (!player || !player.queue) return;
    var snap = player.queue.snapshot();
    var order = (snap.playQueue || []).map(function (item) {
      return item.bounce_ulid || item.id || item.trackId;
    }).filter(Boolean);
    if (order.length < 2) return;

    // Derive the container from the rows themselves — selector guesses were wrong
    // before (rows live in .song-stack under #library-results, not a named wrapper).
    var stacks = new Map();
    var container = null;
    document.querySelectorAll(".song-row[data-track-id]").forEach(function (row) {
      var stack = row.closest(".song-stack") || row;
      if (!container) container = stack.parentElement;
      if (!stacks.has(row.dataset.trackId)) stacks.set(row.dataset.trackId, stack);
    });
    if (!container || stacks.size < 2) return;

    var frag = document.createDocumentFragment();
    var moved = 0;
    order.forEach(function (id) {
      var stack = stacks.get(id);
      if (stack && stack.parentElement === container) {
        frag.appendChild(stack);
        moved += 1;
      }
    });
    if (moved < 2) return;
    container.prepend(frag);

    var currentId = order[snap.cursor >= 0 ? snap.cursor : 0];
    var playing = stacks.get(currentId);
    if (playing && playing.scrollIntoView) {
      playing.scrollIntoView({block: "nearest", behavior: "smooth"});
    }
  }


  // Reflow when the QUEUE changes, not when we asked for it — playItems fetches
  // asynchronously, so reflowing inline sorted by a stale queue while the player
  // started a different track. That mismatch is why shuffle looked broken.
  if (dock) {
    dock.addEventListener("cr8:queuechange", function () {
      reflowListToQueue(ownerPlayer);
      markPlayingRow(ownerPlayer);
    });
  }

  function markPlayingRow(player) {
    if (!player) return;
    var id = null;
    var track = player.currentTrack;
    if (track) id = track.bounce_ulid || track.id || track.trackId;
    document.querySelectorAll(".song-row[aria-current='true']").forEach(function (row) {
      row.removeAttribute("aria-current");
    });
    if (!id) return;
    var row = document.querySelector('.song-row[data-track-id="' + CSS.escape(id) + '"]');
    if (row) row.setAttribute("aria-current", "true");
  }

  async function jumpToTag(chip) {
    var dim = chip.dataset.tagDim;
    var value = chip.dataset.tagValue;
    if (!dim || !value || !ownerPlayer) return;
    var previousPressed = chip.getAttribute("aria-pressed") === "true";
    chip.setAttribute("aria-pressed", String(!previousPressed));
    if (chip.dataset.tagPost) {
      var body = new FormData();
      body.set("dim", dim);
      body.set("value", value);
      body.set("render", "chip");
      body.set("bounce_ulid", chip.dataset.bounceUlid || "");
      var tagged = await fetch(chip.dataset.tagPost, {
        method: "POST",
        headers: csrfHeaders(),
        body: body,
        credentials: "same-origin"
      });
      if (!tagged.ok) {
        chip.setAttribute("aria-pressed", String(previousPressed));
        return;
      }
      var responseHtml = await tagged.text();
      var responsePage = new DOMParser().parseFromString(
        responseHtml, "text/html"
      );
      var replacement = responsePage.querySelector("button[data-tag-filter]");
      var feedback = responsePage.getElementById("write-feedback");
      if (replacement) chip.replaceWith(replacement);
      if (feedback) {
        var feedbackTarget = document.getElementById("write-feedback");
        feedbackTarget.innerHTML = feedback.innerHTML;
        window.htmx.process(feedbackTarget);
        scheduleUndoToast(feedbackTarget);
      }
    }
    var query = new URLSearchParams({dim: dim, value: value});
    var response = await fetch("/api/tag-queue?" + query, {
      credentials: "same-origin"
    });
    if (!response.ok) {
      if (chip.isConnected) {
        chip.setAttribute("aria-pressed", String(previousPressed));
      }
      return;
    }
    var payload = await response.json();
    if (payload.tracks && payload.tracks.length) {
      ownerPlayer.playItems(
        payload.tracks,
        null,
        {shuffle: true, mode: "queue", userGesture: true}
      );
    }
    var url = "/?" + query;
    history.pushState(null, "", url);
    window.htmx.ajax("GET", url, {
      target: "#cr8-main",
      select: "#cr8-main",
      swap: "outerHTML show:none"
    });
  }

  document.addEventListener("click", function (event) {
    var chip = event.target.closest("[data-tag-filter]");
    if (!chip) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    jumpToTag(chip);
  }, true);

  var activitySource = null;

  function connectActivity() {
    var stream = document.querySelector("[data-activity-stream]");
    if (!stream || !window.EventSource) {
      if (activitySource) activitySource.close();
      activitySource = null;
      return;
    }
    if (activitySource) return;
    activitySource = new EventSource("/activity/events");
    activitySource.addEventListener("poke", async function () {
      var response = await fetch("/activity/feed", {credentials: "same-origin"});
      if (!response.ok) return;
      var feed = document.getElementById("activity-feed");
      feed.innerHTML = await response.text();
      window.htmx.process(feed);
    });
  }
  connectActivity();
  document.addEventListener("htmx:afterSwap", connectActivity);
  document.addEventListener("htmx:afterRequest", function (event) {
    var path = event.detail.pathInfo
      ? event.detail.pathInfo.requestPath
      : "";
    if (!path.includes("/tags/toggle") && path !== "/undo") return;
    var rail = document.getElementById("filter-sheet-body");
    if (!rail) return;
    window.htmx.ajax("GET", window.location.href, {
      target: "#filter-sheet-body",
      select: "#filter-sheet-body",
      swap: "outerHTML"
    });
  });

  function syncBatchbar() {
    var actions = document.querySelector("[data-batch-actions]");
    if (!actions) return;
    var checked = Array.from(
      document.querySelectorAll(".select-song:checked")
    );
    actions.hidden = checked.length === 0;
    var label = document.querySelector("[data-selected-count]");
    if (label) label.textContent = checked.length + " selected";
    var download = document.querySelector("[data-download-selection]");
    var note = document.querySelector("[data-download-note]");
    if (!download || !note) return;
    var maxCount = Number(download.dataset.maxCount || 0);
    var maxBytes = Number(download.dataset.maxBytes || 0);
    var bytes = checked.reduce(function (total, checkbox) {
      return total + Number(checkbox.dataset.originalSize || 0);
    }, 0);
    var exceeds = Boolean(
      (maxCount && checked.length > maxCount) ||
      (maxBytes && bytes > maxBytes)
    );
    note.hidden = !exceeds;
    note.textContent = exceeds
      ? "ZIP cap applies · extra originals will be skipped"
      : "";
  }

  syncBatchbar();

  document.addEventListener("change", function (event) {
    if (event.target.matches(".select-song")) {
      var stack = event.target.closest(".song-stack");
      if (stack) {
        stack.classList.toggle("is-selected", event.target.checked);
      }
      syncBatchbar();
    }
  });

  document.addEventListener("keydown", function (event) {
    var editing = event.target.matches("input, textarea, select, [contenteditable]");
    if (
      !editing &&
      (event.metaKey || event.ctrlKey) &&
      event.key.toLowerCase() === "z"
    ) {
      event.preventDefault();
      requestUndo();
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (editing) {
      if (event.key === "Escape") event.target.blur();
      return;
    }
    if (event.key === "Escape" && ownerPlayer && ownerPlayer.queue.mode === "dig") {
      event.preventDefault();
      stopDigging();
      return;
    }
    if (event.key.toLowerCase() === "u") {
      event.preventDefault();
      requestUndo();
      return;
    }
    if (event.key === "/") {
      var search = document.querySelector('input[type="search"]');
      if (search) {
        event.preventDefault();
        search.focus();
      }
      return;
    }
    if (event.key.toLowerCase() === "t") {
      var tagInput = document.querySelector(
        ".detail-panel [data-tag-input], [data-song-tag-panel] [data-tag-input]"
      );
      if (tagInput) {
        event.preventDefault();
        tagInput.focus();
      }
      return;
    }
    if (event.key.toLowerCase() === "d") {
      var downloadRow = document.querySelector(
        ".song-stack.is-cursor .play-row"
      );
      if (downloadRow) {
        event.preventDefault();
        var downloadLink = document.createElement("a");
        downloadLink.href = "/download/" +
          encodeURIComponent(downloadRow.dataset.trackId) +
          "?format=original";
        downloadLink.download = "";
        document.body.appendChild(downloadLink);
        downloadLink.click();
        downloadLink.remove();
      }
      return;
    }
    if (event.key === "L") {
      event.preventDefault();
      revealPlaying();
      return;
    }
    if (event.key === " ") {
      if (ownerPlayer) {
        event.preventDefault();
        var playButton = document.getElementById("player-play");
        var instantRows = [];
        if (ownerPlayer.currentTrack) {
          document.querySelectorAll("[data-track-id]").forEach(function (element) {
            if (element.dataset.trackId !== ownerPlayer.currentTrack.bounce_ulid) return;
            var playingRow = element.closest(".song-stack");
            if (!playingRow || instantRows.includes(playingRow)) return;
            playingRow.classList.add("is-instant-selection");
            instantRows.push(playingRow);
          });
        }
        if (playButton) playButton.classList.add("is-instant-toggle");
        ownerPlayer.toggle();
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            if (playButton) playButton.classList.remove("is-instant-toggle");
            instantRows.forEach(function (row) {
              row.classList.remove("is-instant-selection");
            });
          });
        });
      }
      return;
    }
    if (/^[1-9]$/.test(event.key)) {
      var chips = document.querySelectorAll(
        ".detail-panel [data-tag-toggle], [data-song-tag-panel] [data-tag-toggle]"
      );
      var chip = chips[Number(event.key) - 1];
      if (chip) {
        event.preventDefault();
        chip.click();
      }
      return;
    }
    if (event.key === "Enter") {
      var cursorRow = document.querySelector(
        ".song-stack.is-cursor .play-row"
      );
      if (cursorRow) {
        event.preventDefault();
        playCursorRow(cursorRow);
      }
      return;
    }
    if (!["j", "k"].includes(event.key.toLowerCase())) return;
    var rows = Array.from(document.querySelectorAll(".library .play-row"));
    if (!rows.length) return;
    var current = document.querySelector(".song-stack.is-cursor .play-row");
    var index = Math.max(rows.indexOf(current), 0);
    index += event.key.toLowerCase() === "j" ? 1 : -1;
    var next = rows[Math.min(Math.max(index, 0), rows.length - 1)];
    if (next) {
      event.preventDefault();
      updateDetailFromRow(next, {instant: true, requestDetail: true});
      next.focus({preventScroll: false});
    }
  });

  document.addEventListener("click", function (event) {
    var selectionDownload = event.target.closest("[data-download-selection]");
    if (selectionDownload) {
      event.preventDefault();
      var checked = Array.from(
        document.querySelectorAll(".select-song:checked")
      );
      var maxCount = Number(selectionDownload.dataset.maxCount || 50);
      var maxBytes = Number(selectionDownload.dataset.maxBytes || 0);
      var chosen = [];
      var bytes = 0;
      var skipped = 0;
      checked.forEach(function (checkbox) {
        var size = Number(checkbox.dataset.originalSize || 0);
        if (
          !checkbox.dataset.bounceUlid ||
          !size ||
          chosen.length >= maxCount ||
          (maxBytes && bytes + size > maxBytes)
        ) {
          skipped += 1;
          return;
        }
        chosen.push(checkbox.dataset.bounceUlid);
        bytes += size;
      });
      var note = document.querySelector("[data-download-note]");
      if (!chosen.length) {
        if (note) {
          note.hidden = false;
          note.textContent = checked.length
            ? "No downloadable originals in this selection."
            : "Select at least one song.";
        }
        return;
      }
      if (note) {
        note.hidden = !skipped;
        note.textContent = skipped
          ? "ZIP cap: downloading " + chosen.length + " · skipped " + skipped
          : "";
      }
      var link = document.createElement("a");
      link.href = "/download/selection?ulids=" +
        encodeURIComponent(chosen.join(","));
      link.download = "";
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }
    var copy = event.target.closest("[data-row-copy-link]");
    if (copy) {
      event.preventDefault();
      var ulid = copy.dataset.songUlid;
      if (ulid) {
        var url = window.location.origin + "/songs/" + encodeURIComponent(ulid);
        var done = function () {
          var region = document.getElementById("write-feedback");
          if (!region) return;
          region.innerHTML = "";
          var note = document.createElement("span");
          note.className = "toast";
          note.textContent = "link copied";
          region.appendChild(note);
          window.setTimeout(function () {
            if (note.isConnected) note.remove();
          }, 2600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(done, function () {
            window.prompt("Copy this link", url);
          });
        } else {
          window.prompt("Copy this link", url);
        }
      }
      return;
    }
    var rip = event.target.closest("[data-row-rip-stems]");
    if (rip) {
      event.preventDefault();
      fetch(
        "/stems/" + encodeURIComponent(rip.dataset.bounceUlid),
        {
          method: "POST",
          headers: csrfHeaders(),
          credentials: "same-origin"
        }
      );
      return;
    }
    var tag = event.target.closest("[data-row-tag]");
    if (tag) {
      event.preventDefault();
      var tagRow = tag.closest(".song-line").querySelector(".play-row");
      updateDetailFromRow(tagRow, {instant: true, requestDetail: true});
      var tagInput = document.querySelector(
        "#detail-panel [data-tag-dimension='vibe'] [data-tag-input]"
      );
      if (tagInput) tagInput.focus();
      return;
    }
    var add = event.target.closest("[data-row-queue-add]");
    if (!add || !ownerPlayer) return;
    event.preventDefault();
    var item = window.CratePlayback.collect([add])[0];
    if (!item) return;
    var snapshot = ownerPlayer.queue.snapshot();
    var items = snapshot.playQueue.slice();
    if (!items.some(function (queued) { return queued.id === item.id; })) {
      items.push(item);
    }
    var current = snapshot.cursor >= 0
      ? snapshot.playQueue[snapshot.cursor]
      : null;
    ownerPlayer.queue.replace(items, current ? current.id : null, {
      mode: snapshot.mode
    });
    ownerPlayer.emitQueue();
  });

  function initializeCollectionList(root) {
    if (!window.Sortable) return;
    (root || document).querySelectorAll("[data-collection-list]").forEach(function (list) {
      if (list.dataset.sortableReady === "true") return;
      list.dataset.sortableReady = "true";
      window.Sortable.create(list, {
        animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? 0
          : 150,
        handle: ".queue-grip",
        onEnd: function () {
          var body = new FormData();
          list.querySelectorAll("[data-bounce-ulid]").forEach(function (row) {
            body.append("bounce_ulid", row.dataset.bounceUlid);
          });
          fetch(list.dataset.orderUrl, {
            method: "POST",
            headers: csrfHeaders(),
            body: body,
            credentials: "same-origin"
          });
        }
      });
    });
  }

  function syncCollectionCount() {
    var label = document.querySelector("[data-collection-count]");
    var list = document.querySelector("[data-collection-list]");
    if (!label || !list) return;
    var count = list.querySelectorAll("[data-bounce-ulid]").length;
    label.textContent = "ordered collection · " + count + " songs";
  }

  initializeCollectionList(document);

  document.addEventListener("click", function (event) {
    var playCollection = event.target.closest("[data-collection-play]");
    if (playCollection && ownerPlayer) {
      var items = window.CratePlayback.collect(
        document.querySelectorAll(".collection-play-row")
      );
      ownerPlayer.playItems(items, null, {
        shuffle: playCollection.dataset.shuffle === "true",
        userGesture: true
      });
      return;
    }
    var createFromQueue = event.target.closest("[data-collection-from-queue]");
    if (!createFromQueue || !ownerPlayer) return;
    var nameInput = document.querySelector("[data-queue-collection-name]");
    var name = nameInput ? nameInput.value.trim() : "";
    var queue = ownerPlayer.queue.snapshot().playQueue;
    if (!name || !queue.length) return;
    var maker = createFromQueue.closest(".queue-collection-maker");
    var libraryCount = Number(maker && maker.dataset.libraryCount || 0);
    var queueCount = new Set(queue.map(function (item) {
      return item.id;
    })).size;
    var wholeLibrary = Boolean(libraryCount && queueCount === libraryCount);
    if (
      wholeLibrary &&
      !window.confirm("This queue is the whole library. Create the collection?")
    ) return;
    var body = new FormData();
    body.set("name", name);
    body.set("source", "queue");
    if (wholeLibrary) body.set("confirm_all", "true");
    queue.forEach(function (item) {
      body.append("bounce_ulid", item.id);
    });
    fetch("/collections", {
      method: "POST",
      headers: csrfHeaders(),
      body: body,
      credentials: "same-origin"
    }).then(function (response) {
      if (!response.redirected) return;
      window.htmx.ajax("GET", response.url, {
        target: "#cr8-main",
        select: "#cr8-main",
        swap: "outerHTML show:none"
      });
    });
  });
}());
