// UI audit — runs INSIDE the page and returns every craft violation it can prove.
// This exists because the same classes of defect kept shipping and being caught by
// hand: controls covered by other elements, text clipped mid-word, targets too
// small, chips inflated, pages too heavy. All of those are measurable.
//
// Usage: browse js "$(cat scripts/ui_audit.js)"
(function () {
  var out = [];
  var overlays = {};
  var add = function (severity, rule, detail, el) {
    out.push({
      severity: severity,
      rule: rule,
      detail: detail,
      el: el ? (el.tagName.toLowerCase() +
        (el.id ? "#" + el.id : "") +
        (el.className && typeof el.className === "string"
          ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".")
          : "")) : null
    });
  };

  var visible = function (el) {
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" ||
        Number(cs.opacity) <= 0.05) return false;
    if (el.closest("[hidden],[aria-hidden=true],details:not([open])")) return false;

    // An element inside a collapsed menu still reports a box. Only count it if it
    // actually falls inside every clipping ancestor — otherwise a closed dropdown
    // reports each of its items as a covered control, which is how this audit
    // once claimed 125 defects that did not exist.
    var n = el.parentElement;
    while (n && n !== document.body) {
      var ncs = getComputedStyle(n);
      // Read the axes separately: the `overflow` shorthand does not resolve
      // cleanly when only overflow-y is set, which let a virtualiser's
      // off-screen overscan rows count as visible.
      var clips = (ncs.overflowY !== "visible" && ncs.overflowY !== "") ||
        (ncs.overflowX !== "visible" && ncs.overflowX !== "");
      if (clips) {
        var nr = n.getBoundingClientRect();
        if (r.bottom <= nr.top + 1 || r.top >= nr.bottom - 1 ||
            r.right <= nr.left + 1 || r.left >= nr.right - 1) return false;
      }
      n = n.parentElement;
    }
    return true;
  };

  var interactive = Array.prototype.slice.call(
    document.querySelectorAll(
      'button, a[href], input, select, textarea, [role="button"], [tabindex]:not([tabindex="-1"])'
    )
  ).filter(visible);

  // 1. COVERED CONTROLS — the bug where the transport sat on the waveform and
  //    clicks went to the seek surface instead of the button.
  // Test the centre of the part you can actually SEE. An element straddling a
  // scroll boundary is half visible and perfectly clickable, but its geometric
  // centre lands past the clip edge and hits whatever is painted below.
  var visibleBox = function (el) {
    var r = el.getBoundingClientRect();
    var top = r.top, bottom = r.bottom, left = r.left, right = r.right;
    var n = el.parentElement;
    while (n && n !== document.body) {
      var ncs = getComputedStyle(n);
      if ((ncs.overflowY !== "visible" && ncs.overflowY !== "") ||
          (ncs.overflowX !== "visible" && ncs.overflowX !== "")) {
        var nr = n.getBoundingClientRect();
        top = Math.max(top, nr.top);
        bottom = Math.min(bottom, nr.bottom);
        left = Math.max(left, nr.left);
        right = Math.min(right, nr.right);
      }
      n = n.parentElement;
    }
    top = Math.max(top, 0);
    left = Math.max(left, 0);
    bottom = Math.min(bottom, innerHeight);
    right = Math.min(right, innerWidth);
    return {top: top, bottom: bottom, left: left, right: right,
            width: right - left, height: bottom - top};
  };

  interactive.forEach(function (el) {
    var r = visibleBox(el);
    if (r.width < 2 || r.height < 2) return;
    var cx = r.left + r.width / 2;
    var cy = r.top + r.height / 2;
    var top = document.elementFromPoint(cx, cy);
    if (!top) return;
    if (top === el || el.contains(top) || top.contains(el)) return;

    // A fixed/sticky overlay (the player dock, the batch bar) covering content
    // is NOT a covered control — you scroll the content out from under it. It
    // only becomes one if the scroll container lacks the padding to clear it,
    // which rule 1b checks once rather than once per row.
    var over = top, pinned = null;
    while (over && over !== document.body) {
      var pos = getComputedStyle(over).position;
      if (pos === "fixed" || pos === "sticky") { pinned = over; break; }
      over = over.parentElement;
    }
    if (pinned) {
      overlays[pinned.tagName.toLowerCase() + "." +
        String(pinned.className).split(" ")[0]] = pinned;
      return;
    }
    add("high", "covered-control",
      "click at centre lands on <" + top.tagName.toLowerCase() +
      " class=\"" + String(top.className).slice(0, 30) + "\">", el);
  });

  // 1b. Does the page clear its fixed overlays? Inferring this from geometry is
  //     unreliable because lists scroll in inner containers, so scroll each one
  //     to its end and ask whether the last control is genuinely still covered.
  var scrollerOf = function (el) {
    var n = el.parentElement;
    while (n && n !== document.body) {
      var cs = getComputedStyle(n);
      if ((cs.overflowY === "auto" || cs.overflowY === "scroll") &&
          n.scrollHeight > n.clientHeight + 4) return n;
      n = n.parentElement;
    }
    return document.scrollingElement;
  };
  Object.keys(overlays).forEach(function (name) {
    var dock = overlays[name];
    var under = interactive.filter(function (el) {
      return !dock.contains(el) &&
        el.getBoundingClientRect().top > dock.getBoundingClientRect().top;
    });
    if (!under.length) return;
    var last = under[under.length - 1];
    var sc = scrollerOf(last);
    var was = sc.scrollTop;
    sc.scrollTop = sc.scrollHeight;
    var r = last.getBoundingClientRect();
    var hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    var stuck = hit && hit !== last && !last.contains(hit) && dock.contains(hit);
    sc.scrollTop = was;
    if (stuck) {
      add("high", "unreachable-under-overlay",
        "at full scroll the last control is still under fixed " + name +
        " — its scroller needs padding-bottom >= the overlay height");
    }
  });

  // 2. HIT AREAS — 40x40 desktop minimum.
  interactive.forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (el.tagName === "A" && getComputedStyle(el).display === "inline") return;
    // The 40x40 floor is for icon-sized targets. A wide text field is easy to hit
    // at 32px tall and forcing it to 40 is what made the forms look inflated.
    var typed = el.tagName === "TEXTAREA" || el.tagName === "SELECT" ||
      (el.tagName === "INPUT" && !/^(checkbox|radio)$/.test(el.type));
    if (typed && r.width >= 100 && r.height >= 28) return;
    // The 40x40 floor exists for icon-sized square targets. A control that is
    // wide and at least 28px tall is comfortably clickable, and forcing it to 40
    // is what inflated the chips in the first place. WCAG 2.5.8 asks for 24x24.
    if (r.width >= 56 && r.height >= 28) return;
    if (r.height >= 40 && r.width >= 28) return;
    if (r.width < 40 || r.height < 40) {
      add(r.width < 28 || r.height < 28 ? "high" : "medium", "small-target",
        Math.round(r.width) + "x" + Math.round(r.height) + " (min 40x40)", el);
    }
  });

  // 3. CLIPPED TEXT — "release" and "D minor" cut off mid-word.
  Array.prototype.slice.call(document.querySelectorAll("*")).filter(visible)
    .forEach(function (el) {
      if (el.children.length) return;
      if (el.closest(".sr-only,.visually-hidden")) return;
      var text = (el.textContent || "").trim();
      if (!text) return;
      var cs = getComputedStyle(el);
      var ellipsis = cs.textOverflow === "ellipsis";
      if (el.scrollWidth > el.clientWidth + 2 && !ellipsis &&
          cs.overflowX !== "auto" && cs.overflowX !== "scroll") {
        add("high", "clipped-text",
          "\"" + text.slice(0, 24) + "\" overflows by " +
          (el.scrollWidth - el.clientWidth) + "px with no ellipsis", el);
      }
    });

  // 4. OVERSIZED CHIPS — design B chips are ~32px, not 60px.
  Array.prototype.slice.call(
    document.querySelectorAll(".chip, [data-tag-filter], .tag-toggle, .rail-filter")
  ).filter(visible).forEach(function (el) {
    var h = el.getBoundingClientRect().height;
    if (h > 40) add("medium", "inflated-chip", Math.round(h) + "px tall (target 32)", el);
  });

  // 5. RADIUS SPRAWL + nested equal radii (the concentric rule).
  var radii = {};
  Array.prototype.slice.call(document.querySelectorAll("*")).filter(visible)
    .forEach(function (el) {
      var cs = getComputedStyle(el);
      var r = cs.borderRadius;
      if (r && r !== "0px") {
        radii[r] = (radii[r] || 0) + 1;
        var parent = el.parentElement;
        if (parent && getComputedStyle(parent).borderRadius === r &&
            parent.getBoundingClientRect().height > el.getBoundingClientRect().height) {
          add("low", "nested-equal-radius", "same radius as parent (" + r + ")", el);
        }
      }
    });
  var distinct = Object.keys(radii);
  if (distinct.length > 5) {
    add("medium", "radius-sprawl",
      distinct.length + " distinct radii in use: " + distinct.slice(0, 8).join(", "));
  }

  // 6. TYPE SPRAWL — two families only.
  var families = {};
  Array.prototype.slice.call(document.querySelectorAll("*")).filter(visible)
    .forEach(function (el) {
      var f = getComputedStyle(el).fontFamily.split(",")[0].replace(/["']/g, "").trim();
      if (f) families[f] = (families[f] || 0) + 1;
    });
  var famNames = Object.keys(families);
  if (famNames.length > 3) {
    add("medium", "font-sprawl", famNames.length + " families: " + famNames.join(", "));
  }

  // 7. CONTRAST — WCAG AA 4.5:1 on body text.
  // Colours are authored in oklch, so parsing rgb() digits out of a computed
  // value silently produced nonsense ratios. Painting to a 1x1 canvas resolves
  // ANY css colour - oklch, color(), named - to real channels.
  var cv = document.createElement("canvas");
  cv.width = cv.height = 1;
  var ctx = cv.getContext("2d", {willReadFrequently: true});
  var channels = function (c) {
    try {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "#000000";
      ctx.fillStyle = c;
      ctx.fillRect(0, 0, 1, 1);
      var d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3] / 255];
    } catch (e) { return null; }
  };
  var lum = function (c) {
    var ch = channels(c);
    if (!ch) return null;
    var v = ch.slice(0, 3).map(function (x) {
      x = x / 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  var bgOf = function (el) {
    var node = el;
    while (node && node !== document.documentElement) {
      var ch = channels(getComputedStyle(node).backgroundColor);
      if (ch && ch[3] > 0.5) return getComputedStyle(node).backgroundColor;
      node = node.parentElement;
    }
    return "rgb(25,25,25)";
  };
  Array.prototype.slice.call(document.querySelectorAll("*")).filter(visible)
    .forEach(function (el) {
      if (el.children.length) return;
      var text = (el.textContent || "").trim();
      if (text.length < 2) return;
      var cs = getComputedStyle(el);
      var fg = lum(cs.color);
      var bg = lum(bgOf(el));
      if (fg === null || bg === null) return;
      var ratio = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      var size = parseFloat(cs.fontSize);
      var large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
      var floor = large ? 3 : 4.5;
      if (ratio < floor) {
        add(ratio < floor - 1.5 ? "high" : "medium", "low-contrast",
          ratio.toFixed(2) + ":1 at " + Math.round(size) + "px (needs " + floor + ")", el);
      }
    });

  // 8. HORIZONTAL PAGE SCROLL — the body must never scroll sideways.
  if (document.documentElement.scrollWidth > innerWidth + 2) {
    add("high", "page-scrolls-sideways",
      document.documentElement.scrollWidth + "px wide in a " + innerWidth + "px viewport");
  }

  // 9. WEIGHT — the 438 KB library page.
  var nodes = document.querySelectorAll("*").length;
  if (nodes > 3000) add("medium", "heavy-dom", nodes + " elements (target < 3000)");

  var order = {high: 0, medium: 1, low: 2};
  out.sort(function (a, b) { return order[a.severity] - order[b.severity]; });

  var counts = {high: 0, medium: 0, low: 0};
  out.forEach(function (f) { counts[f.severity] += 1; });

  return JSON.stringify({
    url: location.pathname,
    nodes: nodes,
    counts: counts,
    findings: out.slice(0, 40)
  });
})();
