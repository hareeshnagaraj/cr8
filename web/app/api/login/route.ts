const API = "http://127.0.0.1:8080";

// Keep authentication in FastAPI. This route only preserves its existing
// POST /login contract now that GET /login belongs to the Next page.
export async function POST(request: Request) {
  const upstream = await fetch(`${API}/login`, {
    method: "POST",
    headers: {
      "content-type": request.headers.get("content-type") ??
        "application/x-www-form-urlencoded",
    },
    body: await request.arrayBuffer(),
    redirect: "manual",
  });
  const headers = new Headers();
  for (const name of ["content-type", "location", "set-cookie"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
