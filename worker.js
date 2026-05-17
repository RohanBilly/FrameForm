export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = url.searchParams.get("url");

    if (!target || !target.startsWith("https://letterboxd.com/")) {
      return new Response("Bad request", { status: 400 });
    }

    const response = await fetch(target, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
    });

    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") || "text/html" },
    });
  },
};
