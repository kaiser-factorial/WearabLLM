# Sphere Model Tools

This document describes the model-facing tools implemented for Sphere, their
authorization boundaries, and their current limitations.

## Current status and deferred work

The thirteen-tool set is implemented and locally tested. The six supporting
Supabase migrations, including private hybrid vector retrieval and due-time
sensor actions, were applied through 2026-08-12. The private hosted bridge is
live-smoked through the authenticated device API.

The following work is deliberately tabled while tool behavior is reviewed:

- rebuilding/distributing Android with `expo-speech`
- true background delivery through FCM, Web Push, or equivalent wake-up paths
- deciding whether phone/browser audio should use local system voices or one
  authenticated hosted Sphere voice
- building a dedicated memory review, correction, restore, and deletion UI

Waveshare does not require a coordinated flash for the current tool contract.
New expression actions continue to mirror `command` and `reply` at the top
level for compatibility with the currently flashed firmware.

## Tool inventory

| Tool | Type | Reads | Writes or external effects | Explicit current-turn intent required |
|---|---|---|---|---:|
| `web_search` | OpenAI built-in | Public web | OpenAI web-search call and citation metadata | Explicit or clearly time-sensitive request |
| `sphere_status` | Bridge function | Sanitized control-plane state | None | No |
| `memory_search` | Bridge function | Private rich memory | OpenAI query embedding | No |
| `memory_remember` | Bridge function | Active memories for deduplication | Creates or stages a memory | No for safe durable facts |
| `memory_confirm` | Bridge function | One short-lived staged candidate | Saves or discards it | Bound yes/no answer |
| `memory_correct` | Bridge function | One identified memory | Atomically supersedes it | Yes |
| `memory_forget` | Bridge function | One identified memory | Marks it forgotten | Yes |
| `source_list` | Bridge function | Build-time source manifest | None | No |
| `source_read` | Bridge function | One bounded source line range | None | No |
| `send_to_body` | Bridge function | Target/action state | Queues one action per target | Yes |
| `sensor_list` | Bridge function | Authenticated capability manifests | None | No |
| `sensor_read` | Bridge function | Confirmed device sensor results | Queues one fresh physical read and waits up to 20 seconds | Yes |
| `sensor_loop` | Bridge function | Scheduled action state | Queues 2–10 due-time sensor reads at 30–3600 second intervals | Yes |
| `loop_cancel` | Bridge function | One known repeat schedule | Fails unfinished actions in that schedule | Yes |

The memory tools operate only on WearabLLM's private Supabase
`wearabllm_memory_records` table. They do not access the separate local Memory
Hub used by development agents.

## Sensor tools and repeat scheduling

The dedicated `ducati-temp-sensor` body runs the capability-driven v6.4 firmware. It
polls the private hosted bridge over authenticated outbound HTTPS, so the
bridge never opens an inbound home-network port and never depends on a browser
holding a Bluetooth connection.

- `sensor_list` returns the latest bounded manifest registered by authenticated
  firmware: device ID, firmware version, sensor IDs, quantities, labels, and units.
  Firmware source and comments are not treated as a capability declaration.
- `sensor_read` creates one action naming explicit sensor IDs, waits for up to
  20 seconds, and returns values only after the device posts a terminal
  `completed` acknowledgement. A timeout returns `pending`, never a guessed or cached reading.
- `sensor_loop` expands one request into 2–10 independently leased
  actions with explicit due times. The minimum interval is 30 seconds, the
  maximum is one hour, and each due action expires after two minutes.
- `loop_cancel` requires the schedule ID returned at creation and
  marks only unfinished actions as cancelled.

The repeat metadata is operation-neutral (`operation`, arguments, count,
index, due time, and expiry). The initial allowlist contains only `sensor_read`.
Future general loops can reuse it by adding individually reviewed repeatable
operations; arbitrary model turns and non-allowlisted tools are never replayed.

Confirmed loop readings are appended to the active shared conversation as
sensor-authored turns and also appear in the Sensor tab's local history. BLE
remains available for direct manual readings; both transports use the same
measurement and validation code on the ESP32.

The ESP32 stores Wi-Fi credentials, bridge URL/token, and its TLS root CA only
in the Git-ignored `v6.4_sensor_hub/wifi_config.h`. HTTPS fails closed
when a valid CA is not configured.

## How a tool turn works

1. The bridge sends the user turn, Sphere instructions, and available tool
   schemas to the OpenAI Responses API.
2. The model either answers directly, invokes built-in web search, or returns a
   custom function call with schema-validated arguments.
   Explicit remember/correct/forget turns are deterministically narrowed to
   the required memory workflow. The first auditable mutation tool is forced
   for explicit writes and for corrections/deletions with an exact memory ID;
   requests without an ID force search first. Public web search is offered
   only when the current message explicitly requests web research or clearly
   asks for time-sensitive information. A web request in earlier conversation
   history does not authorize a new search.
3. For a custom call, the bridge parses the arguments and executes the named
   operation. Correction, forgetting, body actions, and sensitive-memory
   confirmations re-check the original user transcript. Safe memory creation
   instead passes a deterministic sensitivity screen at the bridge boundary.
4. The bridge returns the function result to the same response chain using
   `previous_response_id`, while resending Sphere's instructions.
5. The loop is bounded to eight custom-tool rounds in the hosted profile
   (configurable and clamped to 1–8). Parallel function calls are disabled. If
   Sphere uses all eight rounds, the completed activity is preserved and the
   bridge returns a normal fallback reply asking for a short follow-up; it does
   not turn the interaction into an HTTP 500.
6. Every tool call also produces a concise persisted activity line for visual
   clients. Sphere still finishes with the normal two-line semantic command
   and reply.

## Cross-turn tool context

Each tool result is carried through its active Responses API turn with
`previous_response_id`. The bridge also stores a bounded private copy of the
model-facing arguments and output beside the assistant turn. On later turns,
that private context is restored to model history, so follow-ups such as
“continue at line 1201” retain the prior source path and contents.

Private tool context is never returned by the conversation/dashboard APIs.
Clients receive only redacted activity summaries. Each stored call is bounded,
and restored tool output is explicitly labeled as data rather than instructions.
The default per-message tool round limit is eight.

## Body-specific rendering

Sphere may use lightweight Markdown for headings, lists, emphasis, links, and
code. Shared conversation storage preserves its newlines. The dashboard renders
that Markdown through a small DOM-based allowlist without injecting model HTML.
Waveshare actions, direct Waveshare replies, and TTS calls receive a deterministic
plain-text projection of the same semantic answer.

OpenAI distinguishes built-in tools such as web search from application-owned
function tools. See [Using tools](https://developers.openai.com/api/docs/guides/tools),
[Function calling](https://developers.openai.com/api/docs/guides/function-calling),
[Web search](https://developers.openai.com/api/docs/guides/tools-web-search),
and [Embeddings](https://developers.openai.com/api/docs/guides/embeddings).

Tool failures are returned to the model as bounded `{ok: false, error: ...}`
results so it can explain the failure. The API response and stored turn contain
a bounded audit summary: tool name, success state, result count, memory ID,
action IDs, and a human-readable activity line. Memory mutations intentionally
include only the beginning of the affected content so the user can see what
changed; secrets are blocked before this point. Full private memory records,
vectors, source-file contents, and raw function results are not copied into
client-visible metadata.

Android and Web render these activity lines inside the assistant turn. They are
stored with the shared conversation, so a tool used from any body has the same
visible audit on every visual body. Waveshare keeps the normal concise spoken
reply and does not read activity metadata aloud.

Model or provider failures are also converted into an ordinary `RF` assistant
turn so the accepted user turn and the failure reply can be persisted together.
If the dashboard cannot reach the bridge at all, it retains the optimistic user
bubble and adds a local retry message instead of immediately replacing both
with the last server snapshot.

## `web_search`

### Purpose

Give Sphere access to current or externally verifiable public information.
Typical uses include current events, schedules, changing product information,
or a claim that benefits from primary web sources.

### How it works

- The bridge adds `{ "type": "web_search" }` only when
  `WEARABLLM_WEB_SEARCH=1` and the current turn has explicit web intent or a
  clearly time-sensitive term such as `latest`, `today`, `weather`, `price`,
  `score`, or `version`.
- OpenAI executes the search as a built-in tool; no WearabLLM search proxy or
  browser automation is involved.
- The model incorporates the results into its answer.
- The bridge requests `web_search_call.action.sources`, collects and
  deduplicates source URL/title pairs, and stores them as assistant-turn
  metadata.
- Android and Web render those source links. They are not automatically read
  aloud by Waveshare or local speech.

### Scope and limitations

- Available only on the OpenAI Responses path. The OpenRouter compatibility
  path currently receives no model tools.
- Enabled in the hosted Docker profile.
- After the bridge offers the tool, the model decides whether a search is
  useful; `tool_choice` is not forced.
- Web search is not inferred from conversation history or offered for an
  ordinary profile/memory statement. This prevents unrelated citations, extra
  cost, and accidental search-query disclosure of household wording.
- There is currently no WearabLLM-specific domain allowlist, user-location
  parameter, or search-result cache.
- It is for public web information, not signed-in/private sites or household
  data.
- Search adds provider latency and usage cost, and source quality still needs
  model judgment.
- Durable citation display requires the conversation-metadata migration.

## `sphere_status`

### Purpose

Let Sphere answer questions such as “is the Waveshare online?”, “what bodies
do you know about?”, or “did the last phone action report completion?” without
granting the model a general admin/config endpoint.

### How it works

- `target_device_ids` may name known bodies or be empty for the full catalog.
- `include_recent_actions` optionally includes the newest sanitized delivery
  acknowledgement per selected body.
- The bridge returns body kind, declared lifecycle status, capabilities,
  heartbeat-derived online state, last-seen timestamp, service availability,
  and model-tool availability.
- Recent actions expose IDs, command/channel metadata, status, attempts,
  timestamps, and a bounded error—never transcript, reply, or expression text.

### Scope and limitations

- It is read-only and does not require a mutation-intent phrase.
- It is a **passive control-plane observation**, not an active network or
  hardware probe. Calling it does not wake, ping, light, or play any body.
- “Online” means the bridge saw an authenticated request within its 20-second
  presence window. It does not prove microphone, speaker, display, or LEDs are
  healthy.
- Action state is whatever the target client acknowledged. Even `played` is
  client-reported, not independent acoustic or optical sensor proof.
- The result deliberately excludes prompts, secrets, tokens, transcripts,
  memory contents, audio captures, filesystem paths, and full action payloads.

## `memory_search`

### Purpose

Retrieve durable household context when it would materially improve a reply,
for example a known preference, person, relationship, routine, instruction, or
household fact.

### Inputs and result

- `query`: natural-language search text
- `limit`: 1–10 model-visible results

Results contain the memory record plus provenance fields such as source,
source device, confidence, importance, confirmation, expiry, and supersession.

### Authorization

Search is read-only and may be used without an explicit “search memory” phrase
when private household context is relevant. It remains scoped to the configured
`WEARABLLM_PRINCIPAL_ID` and is executed server-side with the Supabase service
role.

### Scope and limitations

- Available only when the rich Supabase memory store initializes successfully;
  otherwise every `memory_*` schema is removed from the model request.
- Searches only active, unexpired records.
- Hosted retrieval is hybrid: PostgreSQL full-text rank plus cosine similarity
  over a 512-dimensional `text-embedding-3-small` vector, with smaller
  importance/confidence contributions.
- The bridge generates the query embedding; the service-role-only PostgreSQL
  function applies principal, active-status, and expiry boundaries before
  returning ranked records. Subject and kind filtering remain backend
  capabilities but are deliberately not model-facing: a guessed filter once
  hid successful saves during verification.
- Raw vectors never enter model-visible results or client-visible tool audits.
- The current small corpus uses an exact vector scan rather than an approximate
  index. This is simpler and accurate at current scale but will need latency
  review as the record count grows.
- When no embedding provider is configured, the same RPC falls back to
  lexical-only ranking. The hosted OpenAI profile configures hybrid retrieval.
- An embedding-provider failure is surfaced as a bounded tool error rather
  than silently pretending a semantic search occurred.
- This is separate from the older compact auto-extracted memory path, which
  remains for compatibility.
- There is no memory review UI yet.

## `memory_remember`

### Purpose

Create a provenance-rich durable memory for a stable, user-provided identity,
preference, goal, routine, relationship, instruction, or long-running fact.
The user does not need to say a magic phrase such as “remember that.”

### Inputs and result

The model supplies subject, memory kind, content, tags, importance from 1–5,
and an optional ISO expiry timestamp. The bridge adds:

- principal ID
- source `wearabllm-explicit-tool`
- originating device ID
- confidence `1.0`
- confirmation timestamp

Before insertion, active memories are checked for an exact case-insensitive
content duplicate. A duplicate returns the existing record instead of creating
a second one. New hosted records are embedded synchronously before insertion;
the vector, model name, and embedding timestamp are stored together.

### Authorization and sensitivity policy

The bridge screens both the current user text and proposed memory content:

- ordinary durable facts can be saved immediately;
- a precise postal address, phone number, or email address is staged in bridge
  memory for five minutes and requires a bound yes/no response through
  `memory_confirm`;
- passwords, passcodes, API/access/auth tokens, private keys, financial account
  identifiers, and government identifiers are rejected and never staged.

The model is instructed not to save assistant claims, guesses, transient
details, or whole conversation turns. Correction and forgetting retain their
explicit current-turn guards.

Concrete personal claims such as “my home address is…” or “my phone is…” are
forced through `memory_remember` before Sphere may ask the confirmation
question. A later yes/no answer is likewise forced through `memory_confirm`,
so a reassuring sentence cannot substitute for changing confirmation state.
Explicit mutations follow the same rule: Sphere may report success only after
the corresponding tool result succeeds.

### Scope and limitations

- The model chooses whether an ordinary statement contains a useful durable
  fact; the sensitivity decision is enforced independently by bridge code.
- Content must be 8–1200 characters; subjects 1–120; tags 1–40 characters with
  at most 12 retained.
- Deduplication compares content only; semantically equivalent wording can
  create separate records.
- Pattern screening is intentionally conservative and is not a complete data
  loss prevention system. Unknown secret formats can still evade it, so Sphere
  is also instructed never to store credentials.
- The tool records the source device but does not currently attach the
  conversation turn ID.
- Expiry is optional; Supabase rejects an expiry at or before creation time.
- If configured embedding generation fails, the write fails before insertion
  so a supposedly vector-ready record is not silently created without a vector.
- Sensitive confirmation state is volatile, holds only one candidate, expires
  after five minutes, and is lost on bridge restart. There is still no memory
  review UI.
- A mutation-scoped turn cannot also invoke unrelated status or cross-body
  tools. Ask for those in a separate turn.

## `memory_confirm`

### Purpose and behavior

Resolve the one sensitive candidate staged by `memory_remember`. Sphere asks a
plain yes/no question. A matching affirmative saves the exact staged candidate
with its original device provenance; a matching negative discards it. The
candidate itself is not sent back in client-visible audit metadata.

### Scope and limitations

- It cannot override the hard block on credentials or financial/government
  identifiers.
- It accepts only a current affirmative for `save: true` or a current negative
  for `save: false`.
- There is one pending candidate for the shared household bridge. A newer
  sensitive candidate replaces an older one.
- Confirmation expires after five minutes or a bridge restart; the user must
  repeat the original fact after expiry.

## `memory_correct`

### Purpose

Replace an identified active memory without erasing its history. Sphere would
normally search first, obtain the memory UUID, and then submit the corrected
content.

### How it works

- The bridge requires an explicit correction signal in the current transcript.
- The model supplies the exact `memory_id`, corrected content, tags, and
  optional replacement subject/kind.
- A service-role-only PostgreSQL function locks the current record, creates the
  replacement with `supersedes_id`, and marks the old record `superseded` in one
  transaction.
- Importance and expiry carry forward; the replacement receives confidence
  `1.0`, explicit-tool provenance, and a fresh embedding in the same atomic
  correction operation.

### Scope and limitations

- Only an active, unexpired memory in the configured principal can be corrected.
- The replacement must actually change the content.
- The model must obtain and pass an exact UUID; fuzzy “update whatever memory
  seems related” is not supported.
- The lexical intent guard currently accepts `correct`, `update`, `replace`,
  `actually`, `no longer`, or `instead`. The UUID requirement and model
  instructions are additional safeguards, but this is still not a user-facing
  approval workflow.
- There is no visual diff or rollback UI yet, although the superseded record is
  retained.

## `memory_forget`

### Purpose

Remove a specific memory from active retrieval after a direct forget/delete
request.

### How it works

- Sphere identifies the record, normally through `memory_search`.
- The tool requires its exact UUID.
- The bridge verifies a forget/delete/remove/erase phrase in the current user
  transcript.
- Supabase changes the record status to `forgotten`.

### Scope and limitations

- “Forget” is a soft deletion for auditability, not physical row deletion.
- Forgotten records no longer appear in normal search.
- There is no restore or permanent-delete UI.
- The current guard also expects a later reference such as `memory`, `fact`,
  `preference`, `that`, or `this`; other phrasing can be safely rejected.
- One call addresses one exact memory ID.

## `source_list` and `source_read`

### Purpose

Give Sphere read-only self-knowledge of the code that defines its bridge,
tools, clients, device protocol, firmware, database schema, and documentation.
`source_list` discovers published paths; `source_read` returns a bounded line
range from one exact path.

### How it works

- Deployment builds `source_bundle.json` from an opt-in set of repository
  patterns and uploads it only to the private Hugging Face Space.
- The current bundle covers root/project documentation, Supabase migrations,
  bridge code and tests, protocol docs, hosted/deployment code, dashboard code,
  Android TypeScript, and the firmware `main/` sources.
- `source_list` can list direct children or recursively enumerate up to 200
  entries.
- `source_read` returns at most 200 lines and 30,000 characters, plus path,
  line bounds, total line count, truncation state, and a SHA-256 provenance
  digest.

### Scope and limitations

- This is not arbitrary filesystem access. Absolute paths, traversal, and any
  path absent from the manifest fail closed.
- Build outputs, `.env` files, `sdkconfig`, credentials, secrets, captures,
  private directories, dependency trees, and Git internals are excluded.
- Individual source files are capped at 256 KiB and the bundle at 4 MiB.
- The source snapshot changes only when bridge code is redeployed. It does not
  expose uncommitted files that were not selected by the deployment manifest.
- Tool output can consume model context, so Sphere should list first and read
  only the relevant line range rather than loading files indiscriminately.
- Source contents are available to Sphere in the tool loop but are not copied
  into client-visible tool audit metadata; the activity line shows only path
  and line range.

## `send_to_body`

### Purpose

Ask another explicit Sphere body to render an additional semantic expression.
Examples include “tell the Waveshare dinner is ready” or “show this on my
phone.” It is not used for the ordinary reply on the body already handling the
request.

### Inputs

- `target_device_ids`: one to three explicit targets from
  `wearabllm-esp32`, `wearabllm-android`, and `web-console`
- `text`: 1–4000 characters
- `command`: one of the nine semantic commands (`GS`, `GP`, `GC`, `RS`, `RF`,
  `YP`, `BS`, `PS`, `PP`)
- `channels`: one or more of `visual`, `display`, and `audio`
- `expires_in_seconds`: 15–86,400 seconds

### Authorization and delivery

The original transcript must contain a direct action verb such as send, tell,
say, speak, play, announce, show, display, light, or color. The tool must also
name explicit target IDs; Sphere is instructed never to infer a broadcast.

The bridge creates one durable action per target. Each action gets:

- the same device-neutral expression payload
- a target-specific idempotency key derived from tool-call ID and target
- an expiry timestamp
- an independent lease, retry count, and terminal state

Bodies poll outbound through the authenticated bridge, claim only their own
action, render it, and acknowledge progress. The bridge can reclaim interrupted
nonterminal work after its lease expires. `completed`, `played`, `failed`, and
`expired` are terminal; “queued” or “sent” is not proof of playback.

### Body-specific rendering

- **Waveshare:** semantic command → LEDs, text → TFT, audio → hosted TTS/speaker.
- **Android:** semantic command → colored Sphere surface, text → phone UI,
  audio → local `expo-speech` only when the user opts in.
- **Web:** semantic command → colored Sphere surface, text → browser UI,
  audio → browser speech synthesis only when the user opts in.

### Scope and limitations

- The `audio` channel currently means “ask this body's renderer to speak”; it
  does not carry an audio file or guarantee the same voice across bodies.
- Android and Web poll while their app/page is active. There is no background
  push or wake-up delivery yet.
- Targets are a hard-coded active allowlist; the planned wearable is excluded.
- A target can be offline until the action expires. Completion is reported per
  target, not as one atomic broadcast result.
- The current prototype still shares one device token among trusted bodies.
- Ordinary assistant replies and manually requested “also play on Waveshare”
  interactions remain separate API paths from this model tool.

## Non-tools that support the tools

- The **semantic expression contract** is the data envelope used by
  `send_to_body`; it is not independently callable by the model.
- The **action queue** is the durable transport and acknowledgement layer; it
  is not a model tool.
- `/v1/query`, `/v1/query_text`, and `/v1/tts` are device APIs, not model tools.
- Compact automatic memory extraction still exists outside this tool loop for
  non-Supabase/local compatibility.
- At hosted bridge startup, at most 50 active legacy rich-memory records
  missing vectors are embedded and patched. Failures warn without preventing
  the bridge from serving other requests.

## Implementation map

- Tool schemas, intent guards, and dispatch:
  [`../bridge/sphere_tools.py`](../bridge/sphere_tools.py)
- Responses loop, source collection, and redacted audit metadata:
  [`../bridge/wearabllm_bridge.py`](../bridge/wearabllm_bridge.py)
- Rich memory access:
  [`../bridge/household_memory.py`](../bridge/household_memory.py)
- Read-only build-time self-source access:
  [`../bridge/source_code.py`](../bridge/source_code.py)
- Durable action semantics:
  [`../bridge/action_queue.py`](../bridge/action_queue.py)
- Database migrations: [`../../supabase/migrations/`](../../supabase/migrations/)
