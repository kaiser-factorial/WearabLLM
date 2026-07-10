import { createClient } from "npm:@supabase/supabase-js@2";

const jsonHeaders = { "Content-Type": "application/json" };
const allowedCommands = new Set(["GS", "GP", "GC", "RS", "RF", "YP", "BS", "PS", "PP"]);

function response(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
}

function secretKey(): string {
  const keys = Deno.env.get("SUPABASE_SECRET_KEYS");
  if (keys) return JSON.parse(keys).default;
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
}

Deno.serve(async (request) => {
  const expectedToken = Deno.env.get("WEARABLLM_DEVICE_TOKEN") ?? "";
  const receivedToken = request.headers.get("X-WearabLLM-Device-Token") ?? "";
  if (!expectedToken || receivedToken !== expectedToken) {
    return response(401, { error: "unauthorized" });
  }

  const url = Deno.env.get("SUPABASE_URL") ?? "";
  const key = secretKey();
  if (!url || !key) return response(500, { error: "server_not_configured" });
  const supabase = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  if (request.method === "GET") {
    const requestUrl = new URL(request.url);
    const requestedLimit = Number(requestUrl.searchParams.get("limit") ?? "100");
    const afterId = Number(requestUrl.searchParams.get("after_id") ?? "0");
    if (
      !Number.isSafeInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > 200 ||
      !Number.isSafeInteger(afterId) || afterId < 0
    ) {
      return response(400, { error: "invalid_query" });
    }

    let query = supabase
      .from("device_transcripts")
      .select("id,created_at,device_id,interaction_id,command,transcript,reply,capture_source")
      .order("id", { ascending: false })
      .limit(requestedLimit);
    if (afterId > 0) query = query.gt("id", afterId);
    const { data, error } = await query;
    if (error) {
      console.error("device transcript read failed", error.code);
      return response(500, { error: "read_failed" });
    }
    return response(200, { transcripts: data ?? [] });
  }

  if (request.method !== "POST") return response(405, { error: "method_not_allowed" });

  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > 8192) return response(413, { error: "payload_too_large" });

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return response(400, { error: "invalid_json" });
  }

  const deviceId = typeof body.device_id === "string" ? body.device_id.trim() : "";
  const interactionId = Number(body.interaction_id);
  const command = typeof body.command === "string" ? body.command : "";
  const transcript = typeof body.transcript === "string" ? body.transcript.trim() : "";
  const reply = typeof body.reply === "string" ? body.reply.trim() : "";
  const captureSource = typeof body.capture_source === "string" ? body.capture_source : "unknown";
  if (
    !deviceId || deviceId.length > 80 || !Number.isSafeInteger(interactionId) || interactionId < 0 ||
    !allowedCommands.has(command) || !transcript || transcript.length > 2000 ||
    reply.length > 4000 || captureSource.length > 40
  ) {
    return response(400, { error: "invalid_event" });
  }

  const { error } = await supabase.from("device_transcripts").insert({
    device_id: deviceId,
    interaction_id: interactionId,
    command,
    transcript,
    reply,
    capture_source: captureSource,
  });
  if (error) {
    console.error("device transcript insert failed", error.code);
    return response(500, { error: "insert_failed" });
  }
  return response(202, { accepted: true });
});
