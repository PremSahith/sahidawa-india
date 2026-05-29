import { NextRequest, NextResponse } from "next/server";

const OVERPASS_MIRRORS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
];

export async function POST(req: NextRequest) {
    const { query } = await req.json();

    // Try at most the first 3 mirrors with a 3-second timeout each
    // to ensure we complete within Vercel's 10-second serverless execution limit.
    const serverMirrors = OVERPASS_MIRRORS.slice(0, 3);
    for (const mirror of serverMirrors) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000); // 3 seconds timeout per mirror

        try {
            const response = await fetch(mirror, {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    Accept: "*/*",
                    "User-Agent": "SahiDawaApp/1.0 (https://sahidawa.org; contact@sahidawa.org)",
                },
                body: `data=${encodeURIComponent(query)}`,
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            if (!response.ok) continue;

            const data = await response.json();
            return NextResponse.json(data);
        } catch {
            clearTimeout(timeoutId);
            continue;
        }
    }

    return NextResponse.json({ error: "All Overpass mirrors failed" }, { status: 503 });
}
