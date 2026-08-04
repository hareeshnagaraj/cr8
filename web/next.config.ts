import {realpathSync} from "node:fs";
import path from "node:path";

import type {NextConfig} from "next";

// Everything the browser needs stays same-origin so the FastAPI session cookie
// just works: no second auth path, no CORS, and Range requests on /m pass
// straight through to the app that already serves them.
const API = "http://127.0.0.1:8080";

function contains(root: string, target: string) {
  const relative = path.relative(root, target);
  return (
    relative === "" ||
    (relative !== ".." &&
      !relative.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relative))
  );
}

function linkedDependencyRoot() {
  const project = __dirname;
  const dependencies = realpathSync(path.join(project, "node_modules"));
  let root = project;
  while (!contains(root, dependencies)) {
    const parent = path.dirname(root);
    if (parent === root) {
      throw new Error("linked dependencies do not share a safe filesystem root");
    }
    root = parent;
  }
  return root;
}

const config: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Worktrees link their installed dependencies from the primary checkout.
  // Turbopack refuses a linked directory outside its root, so widen only as
  // far as the nearest parent shared by this project and that real directory.
  turbopack: {root: linkedDependencyRoot()},
  // Next otherwise silently truncates proxied request bodies at 10 MB. A larger
  // multipart upload reaches FastAPI without its closing boundary and 400s.
  experimental: {proxyClientMaxBodySize: 512 * 1024 * 1024},
  async rewrites() {
    return [
      {
        source: "/api/since-you-were-here",
        destination: `${API}/api/since-you-were-here`,
      },
      {source: "/api/:path*", destination: `${API}/api/:path*`},
      {source: "/s/:path*", destination: `${API}/s/:path*`},
      {source: "/m/:path*", destination: `${API}/m/:path*`},
      {source: "/peaks/:path*", destination: `${API}/peaks/:path*`},
      {source: "/art/:path*", destination: `${API}/art/:path*`},
      {source: "/art-preview/:path*", destination: `${API}/art-preview/:path*`},
      {source: "/art-strip/:path*", destination: `${API}/art-strip/:path*`},
      {source: "/download/:path*", destination: `${API}/download/:path*`},
      // Listening has to be reported for "unheard", the dig queue, and
      // homework to mean anything. This was missing for the whole port, so
      // nothing the app played was ever recorded as played.
      {source: "/progress/:path*", destination: `${API}/progress/:path*`},
      // Same omission as /progress above, found the same way and twice over.
      // Every POST the triage screen made returned 404 from Next, so not one
      // verdict in the whole port was ever recorded - and because the page
      // advances to the next track before awaiting the response, it looked
      // like it worked. Same for the one-click stem separation button.
      //
      // Anything under web/ that fetches a path not beginning /api must have
      // an entry here; tests/web/test_proxy_coverage.py now asserts that, so
      // a third instance of this fails the suite rather than the product.
      {source: "/triage/:path*", destination: `${API}/triage/:path*`},
      {source: "/stems/:path*", destination: `${API}/stems/:path*`},
      {source: "/login", destination: `${API}/login`},
      {source: "/logout", destination: `${API}/logout`},
      {source: "/setup", destination: `${API}/setup`},
      // The login page is still rendered by FastAPI, so its stylesheet and
      // scripts must be reachable too. Without this the page loads with no CSS
      // and the fields are invisible - which automated fills never notice,
      // because they address elements by selector.
      {source: "/static/:path*", destination: `${API}/static/:path*`},
    ];
  },
};

export default config;
