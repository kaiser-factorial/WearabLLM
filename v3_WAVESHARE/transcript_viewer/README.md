# Local Transcript Viewer

Run from the repository root:

```bash
./v3_WAVESHARE/scripts/run_transcript_viewer.sh
```

The app opens at `http://127.0.0.1:8787` and polls for new transcripts every
1.5 seconds. It reads the Supabase function URL and device token from the
ignored firmware `sdkconfig`. The Python server binds only to localhost and
proxies requests so the token is never sent to browser JavaScript.

Use `--no-open` to skip opening a browser or `--port 9000` to select another
local port.
