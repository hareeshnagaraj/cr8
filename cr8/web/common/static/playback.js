(function (global) {
  "use strict";

  if (
    !global.__crateFetchIndicatorInstalled &&
    typeof global.fetch === "function"
  ) {
    var nativeFetch = global.fetch;
    var pendingFetches = 0;
    global.__crateFetchIndicatorInstalled = true;
    global.fetch = function () {
      pendingFetches += 1;
      if (global.document && global.document.body) {
        global.document.body.classList.add("is-fetching");
      }
      var request;
      try {
        request = nativeFetch.apply(global, arguments);
      } catch (error) {
        pendingFetches = Math.max(0, pendingFetches - 1);
        if (!pendingFetches && global.document && global.document.body) {
          global.document.body.classList.remove("is-fetching");
        }
        throw error;
      }
      return Promise.resolve(request).finally(function () {
        pendingFetches = Math.max(0, pendingFetches - 1);
        if (!pendingFetches && global.document && global.document.body) {
          global.document.body.classList.remove("is-fetching");
        }
      });
    };
  }

  function itemId(item) {
    if (typeof item === "string") return item;
    if (item && (item.id || item.bounce_ulid)) return String(item.id || item.bounce_ulid);
    var match = item && String(item.trackUrl || item.track_url || "").match(/\/api\/tracks\/([^/?#]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function normalizeItem(item) {
    var id = itemId(item);
    if (!id) return null;
    var source = typeof item === "string" ? {} : item;
    return {
      id: id,
      trackUrl: source.trackUrl || source.track_url || "/api/tracks/" + encodeURIComponent(id),
      audioUrl: source.audioUrl || source.audio_url || "/m/" + encodeURIComponent(id),
      title: source.title || "",
      reason: source.reason || source.dig_reason || ""
    };
  }

  function normalizeItems(items) {
    var seen = {};
    return Array.from(items || []).reduce(function (result, item) {
      var normalized = normalizeItem(item);
      if (normalized && !seen[normalized.id]) {
        seen[normalized.id] = true;
        result.push(normalized);
      }
      return result;
    }, []);
  }

  function fisherYates(items, random) {
    var output = Array.from(items || []);
    var choose = random || Math.random;
    for (var index = output.length - 1; index > 0; index -= 1) {
      var swap = Math.floor(choose() * (index + 1));
      var value = output[index];
      output[index] = output[swap];
      output[swap] = value;
    }
    return output;
  }

  function storageRead(storage, key) {
    try {
      return storage ? storage.getItem(key) : null;
    } catch (_error) {
      return null;
    }
  }

  function storageWrite(storage, key, value) {
    try {
      if (storage) storage.setItem(key, value);
    } catch (_error) {
      return;
    }
  }

  function Queue(options) {
    var settings = options || {};
    this.storage = settings.storage || global.sessionStorage || null;
    this.storageKey = "cr8.queue." + (settings.storageKey || "default");
    this.preferenceKey = "cr8.shuffle." + (settings.storageKey || "default");
    this.playQueue = [];
    this.sourceQueue = [];
    this.cursor = -1;
    this.repeat = "off";
    this.mode = "queue";
    this.label = "";
    this.shuffleEnabled = storageRead(this.storage, this.preferenceKey) === "true";
    var saved = storageRead(this.storage, this.storageKey);
    if (saved) {
      try {
        var snapshot = JSON.parse(saved);
        this.playQueue = normalizeItems(snapshot.playQueue);
        this.sourceQueue = normalizeItems(snapshot.sourceQueue);
        this.cursor = Math.min(
          Math.max(Number(snapshot.cursor), -1),
          this.playQueue.length - 1
        );
        this.repeat = snapshot.repeat === "all" ? "all" : "off";
        this.mode = snapshot.mode === "dig" ? "dig" : "queue";
        this.label = String(snapshot.label || "");
      } catch (_error) {
        this.playQueue = [];
        this.sourceQueue = [];
        this.cursor = -1;
      }
    }
  }

  Queue.prototype.save = function () {
    storageWrite(this.storage, this.preferenceKey, String(this.shuffleEnabled));
    storageWrite(this.storage, this.storageKey, JSON.stringify(this.snapshot()));
    return this.snapshot();
  };

  Queue.prototype.snapshot = function () {
    return {
      playQueue: this.playQueue.map(function (item) { return Object.assign({}, item); }),
      sourceQueue: this.sourceQueue.map(function (item) { return Object.assign({}, item); }),
      cursor: this.cursor,
      repeat: this.repeat,
      mode: this.mode,
      label: this.label,
      shuffleEnabled: this.shuffleEnabled
    };
  };

  Queue.prototype.current = function () {
    return this.cursor >= 0 ? this.playQueue[this.cursor] || null : null;
  };

  Queue.prototype.replace = function (items, start, options) {
    var settings = options || {};
    this.sourceQueue = normalizeItems(items);
    this.playQueue = settings.shuffle
      ? fisherYates(this.sourceQueue, settings.random)
      : this.sourceQueue.slice();
    this.shuffleEnabled = Boolean(settings.shuffle);
    this.mode = settings.mode === "dig" ? "dig" : "queue";
    this.label = String(settings.label || "");
    var startId = typeof start === "number" ? null : itemId(start);
    if (startId) {
      this.cursor = this.playQueue.findIndex(function (item) { return item.id === startId; });
    } else if (typeof start === "number") {
      this.cursor = Math.min(Math.max(start, 0), this.playQueue.length - 1);
    } else {
      this.cursor = this.playQueue.length ? 0 : -1;
    }
    if (this.cursor < 0 && this.playQueue.length) this.cursor = 0;
    this.save();
    return this.current();
  };

  Queue.prototype.setShuffle = function (enabled) {
    var current = this.current();
    this.shuffleEnabled = Boolean(enabled);
    if (!this.shuffleEnabled) {
      this.playQueue = this.sourceQueue.slice();
      this.cursor = current
        ? this.playQueue.findIndex(function (item) {
          return item.id === current.id;
        })
        : -1;
    }
    this.save();
    return this.shuffleEnabled;
  };

  Queue.prototype.reshuffle = function (random, keepCurrent) {
    var current = this.current();
    var source = this.sourceQueue.length ? this.sourceQueue : this.playQueue;
    if (current && keepCurrent !== false) {
      var rest = source.filter(function (item) { return item.id !== current.id; });
      this.playQueue = [current].concat(fisherYates(rest, random));
      this.cursor = 0;
    } else {
      this.playQueue = fisherYates(source, random);
      this.cursor = this.playQueue.length ? 0 : -1;
    }
    this.shuffleEnabled = true;
    this.save();
    return this.current();
  };

  Queue.prototype.setRepeat = function (repeat) {
    this.repeat = repeat === "all" ? "all" : "off";
    this.save();
    return this.repeat;
  };

  Queue.prototype.advance = function () {
    if (this.cursor + 1 < this.playQueue.length) {
      this.cursor += 1;
    } else if (this.repeat === "all" && this.playQueue.length) {
      this.cursor = 0;
    } else {
      return null;
    }
    this.save();
    return this.current();
  };

  Queue.prototype.retreat = function () {
    if (this.cursor > 0) {
      this.cursor -= 1;
    } else if (this.repeat === "all" && this.playQueue.length) {
      this.cursor = this.playQueue.length - 1;
    } else {
      return null;
    }
    this.save();
    return this.current();
  };

  Queue.prototype.reorder = function (from, to) {
    if (
      from < 0 || to < 0 ||
      from >= this.playQueue.length || to >= this.playQueue.length ||
      from === to
    ) return this.snapshot();
    var current = this.current();
    var moved = this.playQueue.splice(from, 1)[0];
    this.playQueue.splice(to, 0, moved);
    this.sourceQueue = this.playQueue.slice();
    this.cursor = current
      ? this.playQueue.findIndex(function (item) { return item.id === current.id; })
      : -1;
    return this.save();
  };

  Queue.prototype.remove = function (id) {
    var removeId = itemId(id);
    var current = this.current();
    this.playQueue = this.playQueue.filter(function (item) { return item.id !== removeId; });
    this.sourceQueue = this.sourceQueue.filter(function (item) { return item.id !== removeId; });
    this.cursor = current && current.id !== removeId
      ? this.playQueue.findIndex(function (item) { return item.id === current.id; })
      : Math.min(this.cursor, this.playQueue.length - 1);
    return this.save();
  };

  function normalizedPeaks(payload) {
    var data = payload.data || [];
    var peaks = [];
    for (var index = 0; index < data.length; index += 2) {
      peaks.push(Math.max(Math.abs(data[index] || 0), Math.abs(data[index + 1] || 0)) / 128);
    }
    return peaks;
  }

  function isIOS() {
    return /iPad|iPhone|iPod/.test(global.navigator.userAgent) ||
      (global.navigator.platform === "MacIntel" && global.navigator.maxTouchPoints > 1);
  }

  function timeLabel(value) {
    var seconds = Math.max(0, Number(value || 0));
    return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");
  }

  function Player(options) {
    this.options = options;
    this.root = options.root;
    this.audio = options.audio;
    armAudioUnlock(this.audio);
    this.queue = this.root.crateQueue || new Queue({
      storage: options.storage,
      storageKey: this.root.dataset.playerKey || options.storageKey || "default"
    });
    this.root.crateQueue = this.queue;
    this.currentTrack = null;
    this.loadSequence = 0;
    this.wave = null;
    this.audioContext = null;
    this.gainNode = null;
    this.lastProgress = 0;
    this.sortable = null;
    this.root.cratePlayback = this;
    this.bind();
    this.emitQueue();
  }

  Player.prototype.emit = function (name, detail) {
    if (typeof global.CustomEvent !== "function") return;
    this.root.dispatchEvent(new global.CustomEvent(name, {detail: detail}));
  };

  Player.prototype.emitQueue = function () {
    this.syncControls();
    this.renderQueue();
    this.emit("cr8:queuechange", this.queue.snapshot());
  };

  Player.prototype.syncControls = function () {
    var queue = this.queue;
    this.root.querySelectorAll('[data-player-action="shuffle"]').forEach(function (button) {
      button.setAttribute("aria-pressed", String(queue.shuffleEnabled));
    });
    this.root.querySelectorAll('[data-player-action="repeat"]').forEach(function (button) {
      button.setAttribute("aria-pressed", String(queue.repeat === "all"));
      button.textContent = queue.repeat === "all" ? "repeat all" : "repeat off";
    });
    var position = this.root.querySelector("[data-player-position]");
    if (position) {
      if (queue.mode === "dig") {
        var dug = queue.cursor >= 0 ? queue.cursor + 1 : 0;
        position.textContent =
          dug + " dug · " + Math.max(queue.playQueue.length - dug, 0) + " left";
      } else {
        position.textContent = queue.cursor >= 0
          ? (queue.cursor + 1) + " of " + queue.playQueue.length
          : queue.playQueue.length + " queued";
      }
    }
    var mode = this.root.querySelector("[data-player-mode]");
    if (mode) {
      if (queue.mode === "dig") {
        mode.textContent = "digging";
      } else if (queue.shuffleEnabled) {
        mode.textContent = "shuffling" + (
          queue.label ? " · " + queue.label : ""
        );
      } else {
        mode.textContent = "queue";
      }
    }
    this.syncPlaybackState();
  };

  function player_currentTrackId(player) {
    var track = player.currentTrack;
    if (!track) return null;
    return track.bounce_ulid || track.id || track.trackId || null;
  }


  // Browsers only allow programmatic playback inside a real user gesture, and that
  // permission is LOST across an `await`. Every play path here fetches a queue first,
  // so by the time .play() runs the gesture is gone and the browser blocks it silently.
  // Fix: unlock the element on the very first interaction, synchronously, with a
  // silent sample. After that, later programmatic play() calls are permitted.
  var SILENT_WAV =
    "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

  function unlockAudio(audio) {
    if (!audio || audio.dataset.unlocked === "true") return;
    audio.dataset.unlocked = "true";
    var restore = audio.getAttribute("src");
    try {
      audio.src = SILENT_WAV;
      var attempt = audio.play();
      if (attempt && attempt.then) {
        attempt.then(function () {
          audio.pause();
          audio.currentTime = 0;
          if (restore) audio.src = restore; else audio.removeAttribute("src");
        }).catch(function () {
          if (restore) audio.src = restore; else audio.removeAttribute("src");
        });
      }
    } catch (_error) {
      audio.dataset.unlocked = "false";
    }
  }

  function armAudioUnlock(audio) {
    if (!audio || !global.document || !global.document.addEventListener) return;
    var arm = function () { unlockAudio(audio); };
    ["pointerdown", "keydown", "touchstart"].forEach(function (name) {
      global.document.addEventListener(name, arm, {once: true, capture: true});
    });
  }

  Player.prototype.syncPlaybackState = function () {
    var playing = Boolean(
      this.currentTrack && !this.audio.paused && !this.audio.ended
    );
    if (!this.root.dataset.state) {
      this.root.dataset.state = playing ? "playing" : "paused";
    }
    this.root.dataset.playing = String(playing);
    this.root.querySelectorAll(
      '[data-player-action="toggle"], [data-player-toggle="true"]'
    ).forEach(function (button) {
      button.setAttribute("aria-label", playing ? "Pause" : "Play");
      button.dataset.playing = String(playing);
    });
    // Row toggles live outside the player dock, so they need their own sweep:
    // only the row that is actually sounding shows pause.
    var soundingId = playing && player_currentTrackId(this);
    if (global.document.querySelectorAll) {
      global.document.querySelectorAll("[data-player-toggle=\"true\"][data-track-id]")
        .forEach(function (button) {
          var isThisOne = Boolean(
            soundingId && button.dataset.trackId === soundingId
          );
          button.dataset.playing = String(isThisOne);
          button.setAttribute(
            "aria-label",
            (isThisOne ? "Pause " : "Play ") + (button.dataset.trackTitle || "")
          );
        });
    }
    if (!global.document.querySelectorAll) return;
    global.document.querySelectorAll(".play-row[aria-current='true']").forEach(
      function (row) {
        row.removeAttribute("aria-current");
      }
    );
    global.document.querySelectorAll(
      ".song-stack.is-playing, .band-row.is-playing, .collection-track.is-playing"
    ).forEach(function (row) {
      row.classList.remove("is-playing");
    });
    if (!this.currentTrack) return;
    var id = String(this.currentTrack.bounce_ulid || "");
    var songId = String(this.currentTrack.song_ulid || "");
    var currentRow = null;
    global.document.querySelectorAll("[data-track-id]").forEach(function (element) {
      if (
        element.dataset.trackId !== id &&
        (!songId || element.dataset.songUlid !== songId)
      ) return;
      var row = element.closest(".song-stack, .band-row, .collection-track");
      if (row) row.classList.add("is-playing");
      if (!currentRow && element.matches(".play-row")) currentRow = element;
    });
    if (currentRow) currentRow.setAttribute("aria-current", "true");
  };

  Player.prototype.setPlaybackState = function (state) {
    this.root.dataset.state = state;
    this.syncPlaybackState();
  };

  Player.prototype.announceTrack = function () {
    if (!this.currentTrack) return;
    var live = this.root.querySelector("[data-player-live]");
    if (!live) return;
    live.textContent = [
      this.currentTrack.title || "Untitled",
      this.currentTrack.version_label || "mix"
    ].join(" · ");
  };

  Player.prototype.renderQueue = function () {
    var list = this.root.querySelector("[data-queue-view]");
    if (!list) return;
    var snapshot = this.queue.snapshot();
    var windowSize = 20;
    var windowStart = snapshot.cursor < 0
      ? 0
      : Math.min(
        Math.max(snapshot.cursor - 2, 0),
        Math.max(snapshot.playQueue.length - windowSize, 0)
      );
    var windowItems = snapshot.playQueue.slice(
      windowStart,
      windowStart + windowSize
    );
    var count = this.root.querySelector("[data-queue-count]");
    if (count) count.textContent = snapshot.playQueue.length + " queued";
    list.dataset.queueOffset = String(windowStart);
    list.replaceChildren();
    if (!snapshot.playQueue.length) {
      var empty = global.document.createElement("li");
      empty.className = "queue-empty";
      empty.textContent = "queue is empty";
      list.appendChild(empty);
    }
    windowItems.forEach(function (item, windowIndex) {
      var index = windowStart + windowIndex;
      var row = global.document.createElement("li");
      var title = item.title || "untitled bounce";
      row.className = "queue-item";
      row.dataset.queueId = item.id;
      if (index < snapshot.cursor) row.classList.add("is-played");
      if (index === snapshot.cursor) {
        row.classList.add("is-current");
        row.setAttribute("aria-current", "true");
      }

      var grip = global.document.createElement("span");
      grip.className = "queue-grip";
      grip.setAttribute("aria-hidden", "true");
      grip.textContent = "⠿";

      var copy = global.document.createElement("span");
      copy.className = "queue-copy";
      var name = global.document.createElement("span");
      name.className = "queue-title";
      name.textContent = title;
      var state = global.document.createElement("span");
      state.className = "queue-state";
      state.textContent = index === snapshot.cursor
        ? "playing"
        : index === snapshot.cursor + 1
          ? "up next"
          : index < snapshot.cursor
            ? "played"
            : String(index + 1);
      copy.append(name, state);

      var remove = global.document.createElement("button");
      remove.type = "button";
      remove.className = "queue-remove";
      remove.dataset.queueRemove = item.id;
      remove.setAttribute("aria-label", "Remove " + title + " from queue");
      remove.textContent = "×";
      row.append(grip, copy, remove);
      list.appendChild(row);
    });
    if (
      !this.sortable &&
      global.Sortable &&
      typeof global.Sortable.create === "function"
    ) {
      var player = this;
      var reduced = global.matchMedia &&
        global.matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.sortable = global.Sortable.create(list, {
        animation: reduced ? 0 : 150,
        draggable: ".queue-item",
        handle: ".queue-grip",
        onEnd: function (event) {
          if (event.oldIndex == null || event.newIndex == null) return;
          var offset = Number(list.dataset.queueOffset || 0);
          player.queue.reorder(
            offset + event.oldIndex,
            offset + event.newIndex
          );
          player.emitQueue();
        }
      });
    }
  };

  Player.prototype.ensureAudioGraph = function () {
    if (isIOS() || this.audioContext) return;
    var AudioContext = global.AudioContext || global.webkitAudioContext;
    if (!AudioContext) return;
    this.audioContext = new AudioContext();
    this.gainNode = this.audioContext.createGain();
    this.audioContext.createMediaElementSource(this.audio)
      .connect(this.gainNode)
      .connect(this.audioContext.destination);
  };

  Player.prototype.applyReplayGain = function (track) {
    if (
      isIOS() || !this.gainNode || !track.replaygain ||
      track.replaygain.track_gain_db == null
    ) return;
    this.gainNode.gain.value = Math.pow(
      10,
      Number(track.replaygain.track_gain_db) / 20
    );
  };


  // Track switching stalled because nothing was warmed ahead of time: tracks
  // average 6.5 MB and the element only preloaded metadata, so every skip
  // started buffering from zero. Warm the next track's peaks and the head of
  // its audio into the HTTP cache so the swap is instant.
  var warmed = new Set();

  function warmTrack(track) {
    if (!track || !global.fetch) return;
    var audioUrl = track.audio_url;
    if (audioUrl && !warmed.has(audioUrl)) {
      warmed.add(audioUrl);
      global.fetch(audioUrl, {
        credentials: "same-origin",
        headers: {Range: "bytes=0-524287"}
      }).catch(function () {});
    }
    if (track.peaks_url && !warmed.has(track.peaks_url)) {
      warmed.add(track.peaks_url);
      global.fetch(track.peaks_url, {credentials: "same-origin"}).catch(function () {});
    }
    if (warmed.size > 60) warmed.clear();
  }

  Player.prototype.warmNext = function () {
    var snap = this.queue && this.queue.snapshot();
    if (!snap || snap.cursor < 0) return;
    var next = snap.playQueue[snap.cursor + 1];
    if (next) warmTrack(next);
  };

  Player.prototype.configureWave = async function (track, loadSequence) {
    if (!global.WaveSurfer || !this.options.waveform || !track.peaks_url) return;
    var response = await global.fetch(track.peaks_url, {credentials: "same-origin"});
    if (!response.ok) return;
    var peaks = await response.json();
    if (loadSequence !== this.loadSequence) return;
    if (this.wave) this.wave.destroy();
    var settings = {
      container: this.options.waveform,
      media: this.audio,
      peaks: [normalizedPeaks(peaks)],
      duration: Number(track.duration_s || 0),
      height: Number(this.options.waveHeight || 54),
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      waveColor: "rgba(255,255,255,.16)",
      progressColor: "rgba(255,255,255,.92)",
      cursorColor: "rgba(255,255,255,.48)"
    };
    if (this.options.cspNonce) settings.cspNonce = this.options.cspNonce;
    this.wave = global.WaveSurfer.create(settings);
  };

  Player.prototype.setMediaSession = function (track) {
    if (!global.navigator.mediaSession || typeof global.MediaMetadata !== "function") return;
    global.navigator.mediaSession.metadata = new global.MediaMetadata({
      title: track.title,
      artist: track.version_label,
      album: "cr8",
      artwork: track.artwork_url
        ? [{src: track.artwork_url, sizes: "1400x1400", type: "image/jpeg"}]
        : []
    });
  };

  Player.prototype.report = function (state, reason) {
    if (!this.currentTrack || !this.options.progress) return Promise.resolve();
    return Promise.resolve(this.options.progress(
      this.currentTrack,
      state,
      this.audio.currentTime || 0,
      this.audio.duration || 0,
      reason || "checkpoint"
    ));
  };

  Player.prototype.setQueue = function (items, start, options) {
    var current = this.queue.replace(items, start, options);
    this.emitQueue();
    return current;
  };

  Player.prototype.playCurrent = async function (options) {
    var settings = options || {};
    var item = this.queue.current();
    if (!item) return null;
    var loadSequence = ++this.loadSequence;
    if (settings.userGesture) this.ensureAudioGraph();
    var audioUrl = settings.audioUrl || item.audioUrl;
    if (this.audio.getAttribute("src") !== audioUrl) this.audio.src = audioUrl;
    var started = settings.autoplay === false
      ? Promise.resolve(true)
      : this.audio.play().then(function () { return true; }, function () { return false; });
    var response = await global.fetch(item.trackUrl, {credentials: "same-origin"});
    if (loadSequence !== this.loadSequence) return null;
    if (!response.ok) {
      this.emit("cr8:trackerror", {id: item.id, status: response.status});
      return null;
    }
    var track = await response.json();
    var current = this.queue.current();
    if (
      loadSequence !== this.loadSequence ||
      !current ||
      current.id !== item.id
    ) return null;
    track.dig_reason = item.reason || track.dig_reason || "";
    this.currentTrack = track;
    item.title = String(track.title || item.title || "");
    this.queue.save();
    this.emitQueue();
    this.lastProgress = 0;
    this.root.dataset.empty = "false";
    this.applyReplayGain(track);
    this.configureWave(track, loadSequence);
    this.setMediaSession(track);
    if (this.options.render) this.options.render(track, this.queue.snapshot());
    this.syncPlaybackState();
    if (this.options.tags) await this.options.tags(track);
    if (loadSequence !== this.loadSequence) return null;
    this.emit("cr8:trackchange", {track: track, queue: this.queue.snapshot()});
    this.announceTrack();
    if (await started) await this.report("unheard", "start");
    return track;
  };

  Player.prototype.playItems = function (items, start, options) {
    var settings = options || {};
    this.setQueue(items, start, settings);
    return this.playCurrent({
      userGesture: settings.userGesture,
      audioUrl: settings.audioUrl
    });
  };

  Player.prototype.playTrigger = function (trigger) {
    var player = this;
    var seed = collect([trigger]);
    var id = itemId(seed[0]);
    var queued = this.options.queueForTrigger
      ? this.options.queueForTrigger(trigger)
      : seed;
    if (!queued || typeof queued.then !== "function") {
      return this.playItems(queued || seed, id, {
        userGesture: true,
        audioUrl: trigger.dataset.audioUrl
      });
    }
    var started = this.playItems(seed, id, {
      userGesture: true,
      audioUrl: trigger.dataset.audioUrl
    });
    Promise.resolve(queued).then(function (items) {
      var current = player.queue.current();
      var normalized = normalizeItems(items);
      if (!current || current.id !== id || !normalized.length) return;
      player.queue.replace(normalized, id, {
        mode: "queue",
        label: normalized.length + " songs"
      });
      player.emitQueue();
    });
    return started;
  };

  Player.prototype.toggle = function () {
    if (!this.audio.getAttribute("src")) return;
    if (this.audio.paused) this.audio.play();
    else this.audio.pause();
  };

  Player.prototype.next = async function () {
    await this.report("unheard", "checkpoint");
    var item = this.queue.advance();
    if (!item) item = await this.refillDig();
    this.emitQueue();
    return item ? this.playCurrent() : null;
  };

  Player.prototype.previous = async function () {
    if (this.audio.currentTime > 3) {
      this.audio.currentTime = 0;
      return this.queue.current();
    }
    var item = this.queue.retreat();
    this.emitQueue();
    return item ? this.playCurrent() : null;
  };

  Player.prototype.reshuffle = function (random) {
    this.queue.reshuffle(random);
    this.emitQueue();
    return this.queue.current();
  };

  Player.prototype.removeQueueItem = async function (id) {
    var current = this.queue.current();
    var removingCurrent = current && current.id === itemId(id);
    if (removingCurrent) await this.report("unheard", "checkpoint");
    this.queue.remove(id);
    this.emitQueue();
    if (!removingCurrent) return this.queue.current();
    if (this.queue.current()) return this.playCurrent();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    this.loadSequence += 1;
    this.currentTrack = null;
    this.root.dataset.empty = "true";
    return null;
  };

  Player.prototype.refillDig = async function () {
    if (this.queue.mode !== "dig" || !this.options.refill) return null;
    var result = await this.options.refill();
    var items = result && result.tracks ? result.tracks : result;
    return this.queue.replace(items || [], null, {mode: "dig"});
  };

  Player.prototype.bindMediaSession = function () {
    if (!global.navigator.mediaSession) return;
    var player = this;
    var handlers = {
      play: function () { player.audio.play(); },
      pause: function () { player.audio.pause(); },
      previoustrack: function () { player.previous(); },
      nexttrack: function () { player.next(); },
      seekbackward: function () {
        player.audio.currentTime = Math.max(0, player.audio.currentTime - 10);
      },
      seekforward: function () {
        player.audio.currentTime = Math.min(
          player.audio.duration || Infinity,
          player.audio.currentTime + 10
        );
      }
    };
    Object.keys(handlers).forEach(function (name) {
      try {
        global.navigator.mediaSession.setActionHandler(name, handlers[name]);
      } catch (_error) {
        return;
      }
    });
  };

  Player.prototype.bind = function () {
    var player = this;
    this.bindMediaSession();
    this.audio.addEventListener("play", function () {
      player.setPlaybackState("playing");
    });
    this.audio.addEventListener("pause", function () {
      player.setPlaybackState("paused");
    });
    this.audio.addEventListener("waiting", function () {
      player.setPlaybackState("loading");
    });
    this.audio.addEventListener("playing", function () {
      player.warmNext();
      player.setPlaybackState("playing");
    });
    this.audio.addEventListener("error", function () {
      player.setPlaybackState("error");
      var live = player.root.querySelector("[data-player-live]");
      if (live) {
        live.textContent = "Playback error" + (
          player.currentTrack && player.currentTrack.title
            ? " · " + player.currentTrack.title
            : ""
        );
      }
    });
    this.audio.addEventListener("timeupdate", function () {
      if (player.options.renderTime) {
        player.options.renderTime(
          timeLabel(player.audio.currentTime),
          timeLabel(player.audio.duration)
        );
      }
      var every = Number(player.options.progressEvery || 0);
      if (every && player.audio.currentTime - player.lastProgress >= every) {
        player.lastProgress = player.audio.currentTime;
        player.report("unheard", "checkpoint");
      }
    });
    this.audio.addEventListener("ended", async function () {
      await player.report("heard", "ended");
      var item = player.queue.advance();
      if (!item) item = await player.refillDig();
      player.emitQueue();
      if (item) await player.playCurrent();
      else player.emit("cr8:queueended", player.queue.snapshot());
    });
    global.document.addEventListener("click", function (event) {
      var remove = event.target.closest("[data-queue-remove]");
      if (remove && player.root.contains(remove)) {
        event.preventDefault();
        player.removeQueueItem(remove.dataset.queueRemove);
        return;
      }
      var action = event.target.closest("[data-player-action]");
      if (action && player.root.contains(action)) {
        event.preventDefault();
        if (action.dataset.playerAction === "toggle") player.toggle();
        if (action.dataset.playerAction === "previous") player.previous();
        if (action.dataset.playerAction === "next") player.next();
        if (action.dataset.playerAction === "shuffle") {
          if (player.queue.shuffleEnabled) {
            player.queue.setShuffle(false);
            player.emitQueue();
          } else if (player.queue.playQueue.length) {
            player.reshuffle();
          }
        }
        if (action.dataset.playerAction === "reshuffle" && player.queue.playQueue.length) {
          player.reshuffle();
        }
        if (action.dataset.playerAction === "repeat") {
          player.queue.setRepeat(player.queue.repeat === "all" ? "off" : "all");
          player.emitQueue();
        }
        return;
      }
      var trigger = event.target.closest("[data-track-url]");
      if (!trigger) return;
      if (
        trigger.matches("[data-cursor-row]") &&
        !event.target.closest("[data-row-play]")
      ) return;
      event.preventDefault();
      var id = itemId({
        id: trigger.dataset.trackId,
        trackUrl: trigger.dataset.trackUrl
      });
      if (
        trigger.dataset.playerToggle === "true" &&
        player.currentTrack &&
        player.currentTrack.bounce_ulid === id
      ) {
        player.toggle();
        return;
      }
      player.playTrigger(trigger);
    });
  };

  function collect(elements) {
    return normalizeItems(Array.from(elements || []).map(function (element) {
      return {
        id: element.dataset.trackId,
        trackUrl: element.dataset.trackUrl,
        audioUrl: element.dataset.audioUrl,
        title: element.dataset.trackTitle,
        reason: element.dataset.digReason
      };
    }));
  }

  function attach(options) {
    if (!options || !options.root || !options.audio) return null;
    return options.root.cratePlayback || new Player(options);
  }

  global.CratePlayback = {
    Queue: Queue,
    Player: Player,
    attach: attach,
    collect: collect,
    fisherYates: fisherYates
  };
}(typeof window === "undefined" ? globalThis : window));
